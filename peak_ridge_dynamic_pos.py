"""
动态仓位: 因子信号强弱 → 仓位动态调整
方案:
  A. 因子值Z-Score加权 (强信号多配, 弱信号少配)
  B. 因子阈值分档 (>=2.0满仓5只, 1.0-2.0持仓3只, <1.0持仓1只)
  C. 信号强度×波动率倒数混合
"""
import sys,io,os,math
from collections import defaultdict, Counter
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates
import csv

INIT=10_000_000;RF=0.025;TD=252
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
K=1.5;LB=14;TRAIL=0.30;REBAL=21
MAX_SAME_SECTOR=3

FUND_DIR='data/fundamentals_70stocks'
csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm={}
with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()

all_s=load_prices(stock_filter=None)
stocks={c:i for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
cd=get_common_dates(stocks)

factor={}
for code,info in stocks.items():
    vols=info['volume'];dates=info['dates'];n=len(vols)
    ma_vol=calc_ma(vols,20)
    vals={}
    for i in range(n):
        if i<LB or math.isnan(ma_vol[i]): continue
        w=vols[i-19:i+1];mu=sum(w)/20;var=sum((v-mu)**2 for v in w)/20;std=var**0.5
        thr=ma_vol[i]+K*std
        ps=0.0;rs=0.0
        for j in range(max(0,i-LB+1),i+1):
            erupt=vols[j]>=thr
            if erupt:
                prev=(j>0 and vols[j-1]>=thr)
                if prev: rs+=vols[j]
                else: ps+=vols[j]
        vals[dates[i]]=ps/rs if rs>0 else float('nan')
    factor[code]=vals

print('[DATA] %d stocks %d days' % (len(stocks),len(cd)))
print('[FACTOR] %d vals' % sum(len(v) for v in factor.values()))

def select_top_dynamic(candidates, held_sectors_count, scheme):
    """Dynamic selection & weighting.
    scheme: 'equal', 'zscore_weight', 'tier'
    Returns: [(code, weight, score), ...] where weights sum to 1.0
    """
    if not candidates: return []

    # Build sector-constrained list
    selected=[]
    sec_counts=dict(held_sectors_count)
    for code,score in candidates:
        if len(selected)>=5: break
        sec=sm.get(code,'')
        cnt=sec_counts.get(sec,0)
        if cnt>=MAX_SAME_SECTOR: continue
        test_counts=dict(sec_counts)
        test_counts[sec]=test_counts.get(sec,0)+1
        majority=[s for s,c in test_counts.items() if c>=2]
        if len(majority)>1: continue
        selected.append((code,score))
        sec_counts[sec]=sec_counts.get(sec,0)+1

    if not selected: return []

    if scheme=='equal':
        w=1.0/len(selected)
        return [(c,s,w) for c,s in selected]

    elif scheme=='zscore_weight':
        # Cross-sectional Z-score → squashed to positive weights
        scores=[s for _,s in selected]
        mu=sum(scores)/len(scores);sd=(sum((v-mu)**2 for v in scores)/len(scores))**0.5 if len(scores)>1 else 1
        if sd<0.01: sd=0.01
        zs=[(s-mu)/sd for s in scores]
        # Softmax-like: exp(z) with temperature
        temp=0.5  # lower = more concentrated
        weights=[math.exp(z/temp) for z in zs]
        total=sum(weights)
        weights=[w/total for w in weights]
        return [(c,s,w) for (c,s),w in zip(selected,weights)]

    elif scheme=='tier':
        # Tier-based: top1 Z-Score determines how many positions to hold
        # All tiers use zscore_weight within selected stocks
        scores=[s for _,s in selected]
        mu=sum(scores)/len(scores);sd=(sum((v-mu)**2 for v in scores)/len(scores))**0.5 if len(scores)>1 else 1
        if sd<0.01: sd=0.01
        top1_z=(scores[0]-mu)/sd

        if top1_z>1.5:     max_pos=5  # Strong signal: full allocation
        elif top1_z>0.5:   max_pos=4  # Moderate
        elif top1_z>-0.5:  max_pos=3  # Neutral
        elif top1_z>-1.5:  max_pos=2  # Weak
        else:              max_pos=1  # Very weak: minimal exposure

        # Take top max_pos, weight equally
        sub=selected[:max_pos]
        w=1.0/len(sub)
        return [(c,s,w) for c,s in sub]

    elif scheme=='factor_threshold':
        # Absolute factor value decides allocation
        fvals=[s for _,s in selected]
        top1_f=fvals[0]

        if top1_f>=2.0:    max_pos=5
        elif top1_f>=1.5:  max_pos=4
        elif top1_f>=1.0:  max_pos=3
        elif top1_f>=0.5:  max_pos=2
        else:              max_pos=1

        sub=selected[:max_pos]
        w=1.0/len(sub)
        return [(c,s,w) for c,s in sub]

    return []

def backtest(trail, scheme, dynamic_trail=False):
    """dynami_trail: trail% adjusts based on signal strength"""
    cash=INIT;pos={};eq=[];trades=[]
    cash_days=0;total_rebals=0
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}

    for di,dt in enumerate(cd):
        # Check exits with possibly dynamic trail
        for code,p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px=stocks[code]['close'][idx[code][dt]]
            if px>p['peak']:p['peak']=px

            # Dynamic trail: stronger signal -> wider trail
            actual_trail=trail
            if dynamic_trail:
                score=p.get('score',1.0)
                # High score stocks get wider trail (let winners run longer)
                if score>=2.5:   actual_trail=0.35
                elif score>=2.0: actual_trail=0.30
                elif score>=1.5: actual_trail=0.25
                else:            actual_trail=0.20

            if px<=p['peak']*(1-actual_trail):
                sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'exit':'trail','trail_used':actual_trail,'score':p.get('score',0)})
                del pos[code]

        if di%REBAL==0:
            total_rebals+=1
            cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
            cand.sort(key=lambda x:x[1],reverse=True)

            # Select with dynamic weighting
            hsc=Counter(sm.get(c,'') for c in pos.keys())
            dyn_sel=select_top_dynamic(cand,hsc,scheme)

            if not dyn_sel:
                # No valid selections → go to cash
                for code in list(pos.keys()):
                    if code in idx and dt in idx[code]:
                        px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                        cash+=pos[code]['shares']*sp
                        trades.append({'code':code,'name':stocks[code]['name'],
                            'bd':pos[code]['bd'],'sd':dt,
                            'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                            'exit':'rebal','score':pos[code].get('score',0)})
                        del pos[code]
                cash_days+=REBAL
                cash*=(1+RF/TD)
                eq.append({'date':dt,'equity':cash,'pos':0})
                continue

            top_codes=set(c for c,_,_ in dyn_sel)
            # Weight map
            weight_map={c:w for c,_,w in dyn_sel}

            # Sell non-top
            for code in list(pos.keys()):
                if code not in top_codes:
                    px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                    cash+=pos[code]['shares']*sp
                    trades.append({'code':code,'name':stocks[code]['name'],
                        'bd':pos[code]['bd'],'sd':dt,
                        'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                        'exit':'rebal','score':pos[code].get('score',0)})
                    del pos[code]

            # Adjust existing positions to target weights
            total_equity=cash+sum(p['shares']*stocks[c]['close'][idx[c][dt]]
                                  for c,p in pos.items() if c in idx and dt in idx[c])

            for code,score,target_w in dyn_sel:
                target_val=total_equity*target_w
                if code in pos:
                    # Adjust: sell excess or buy more
                    curr_val=pos[code]['shares']*stocks[code]['close'][idx[code][dt]]
                    diff=target_val-curr_val
                    if diff>0 and cash>0:
                        # Need to buy more
                        buy_val=min(diff,cash*0.9)
                        raw=stocks[code]['close'][idx[code][dt]]
                        bp=raw*(1+SLIP+B_FEE);sh=buy_val/bp
                        cash-=buy_val
                        avg_bp=(pos[code]['bp']*pos[code]['shares']+bp*sh)/(pos[code]['shares']+sh)
                        pos[code]['shares']+=sh;pos[code]['bp']=avg_bp
                    elif diff<0:
                        # Need to sell some
                        sell_val=min(-diff,curr_val*0.5)  # Don't sell more than half
                        raw=stocks[code]['close'][idx[code][dt]]
                        sp=raw*(1-SLIP-S_FEE-STAX);sh=sell_val/sp
                        cash+=sell_val
                        pos[code]['shares']-=sh
                        if pos[code]['shares']<=0:
                            trades.append({'code':code,'name':stocks[code]['name'],
                                'bd':pos[code]['bd'],'sd':dt,
                                'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                                'exit':'rebal_partial','score':score})
                            del pos[code]
                else:
                    # New position
                    raw=stocks[code]['close'][idx[code][dt]];bp=raw*(1+SLIP+B_FEE)
                    buy_val=min(target_val,cash*0.9)
                    if buy_val>0:
                        sh=buy_val/bp;cash-=buy_val
                        pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di,'score':score}

        cash*=(1+RF/TD)
        pv=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date':dt,'equity':cash+pv,'pos':len(pos)})

    # Final
    ld=cd[-1]
    for code,p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px=stocks[code]['close'][idx[code][ld]];sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'name':stocks[code]['name'],
                'bd':p['bd'],'sd':ld,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                'exit':'final'})
    pos.clear()

    v=[d['equity'] for d in eq]
    tr=(v[-1]-v[0])/v[0];rs=[(v[i]-v[i-1])/v[i-1] for i in range(1,len(v)) if v[i-1]>0]
    y=len(rs)/TD;cagr=(v[-1]/v[0])**(1/y)-1 if y>0 else 0
    mu=sum(rs)/len(rs) if rs else 0
    sd=(sum((r-mu)**2 for r in rs)/len(rs))**0.5 if rs else 0
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk=v[0];mdd=0.0
    for x in v:
        if x>pk:pk=x
        dd=(pk-x)/pk
        if dd>mdd:mdd=dd
    cm=cagr/mdd if mdd>0 else float('inf')
    w=sum(1 for t in trades if t['ret']>0)

    exits={}
    for t in trades:
        e=t['exit'];exits[e]=exits.get(e,{'cnt':0,'ret':0.0,'wins':0})
        exits[e]['cnt']+=1;exits[e]['ret']+=t['ret']
        if t['ret']>0: exits[e]['wins']+=1
    for e in exits:
        d=exits[e]['cnt']
        exits[e]['avg']=exits[e]['ret']/d*100 if d>0 else 0
        exits[e]['wr']=exits[e]['wins']/d*100 if d>0 else 0

    # Avg positions held
    avg_pos=sum(d['pos'] for d in eq)/len(eq) if eq else 0

    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,
        'nt':len(trades),'wr':w/len(trades) if trades else 0,
        'exits':exits,'avg_pos':avg_pos,'cash_days':cash_days,
        'eq':eq}

# ======================
# TEST CONFIGS
# ======================
print('\n'+'='*95)
print('  动态仓位方案对比')
print('  baseline=等权5只/赛道放宽')
print('='*95)

configs=[
    ('#0 Baseline (equal weight)', 0.30, 'equal', False),
    ('#1 Z-Score weighted (temp=0.5)', 0.30, 'zscore_weight', False),
    ('#2 Tier (Z-score → position count)', 0.30, 'tier', False),
    ('#3 Factor threshold (abs value)', 0.30, 'factor_threshold', False),
    ('#4 Z-Score + Dynamic Trail', 0.30, 'zscore_weight', True),
    ('#5 Factor threshold + Dynamic Trail', 0.30, 'factor_threshold', True),
]

results={}
for label,trail,scheme,dtrail in configs:
    r=backtest(trail,scheme,dtrail)
    results[label]=r
    print('  %-45s S=%6.3f R=%7.1f%% DD=%5.1f%% CM=%6.3f Trd=%4d Win=%3.0f%% AvgPos=%.1f Cash=%dd' % (
        label[:45], r['sh'], r['tr']*100, r['mdd']*100, r['calmar'],
        r['nt'], r['wr']*100, r['avg_pos'], r['cash_days']))

# ======================
# vs Baseline
# ======================
base=results['#0 Baseline (equal weight)']
print('\n'+'='*95)
print('  vs Baseline')
print('='*95)
print('  %-45s %8s %8s %8s %8s %8s' % ('Config','dSharpe','dRet%','dMDD%','dCalmar','dAvgPos'))
print('  %s' % ('-'*80))
for label,r in results.items():
    if 'Baseline' in label: continue
    ds=r['sh']-base['sh'];dr=(r['tr']-base['tr'])*100
    dd=(r['mdd']-base['mdd'])*100;dc=r['calmar']-base['calmar']
    dp=r['avg_pos']-base['avg_pos']
    improved=(ds>0 or dd<0)
    tag=' *' if improved else ''
    print('  %-45s %+7.3f %+7.1f %+7.1f %+7.3f %+6.1f%s' % (label[:45],ds,dr,dd,dc,dp,tag))

# ======================
# Annual returns for best
# ======================
print('\n'+'='*95)
print('  年度收益对比')
print('='*95)
for label,r in results.items():
    yr=defaultdict(lambda:{'s':None,'e':None})
    for d in r['eq']:
        yk=d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s']=d['equity']
        yr[yk]['e']=d['equity']
    ann={y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}
    parts=' '.join('%s:%+5.1f%%' % (y,ann.get(y,0)) for y in sorted(ann.keys()))
    print('  %-45s %s' % (label[:45],parts))

# ======================
# Best exit detail
# ======================
best_label=max(results,key=lambda x:results[x]['sh'])
best=results[best_label]
print('\n'+'='*95)
print('  BEST: %s' % best_label)
print('  S=%.3f R=%.1f%% DD=%.1f%% CM=%.3f' % (best['sh'],best['tr']*100,best['mdd']*100,best['calmar']))
print('  Exits:')
for e,d in sorted(best['exits'].items()):
    print('    %-20s %3d trades  avg=%+.1f%%  wr=%.0f%%' % (e,d['cnt'],d['avg'],d['wr']))

print('\nDone!')
