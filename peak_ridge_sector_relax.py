"""
赛道上限放宽: 5只里最多3只同赛道, 其余2只各不同
"""
import sys,io,os,math
from collections import defaultdict, Counter
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates

INIT=10_000_000;RF=0.025;TD=252;MAX_POS=5
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
K=1.5;LB=14;TRAIL=0.30;REBAL=21
MAX_SAME_SECTOR=3  # 同一赛道最多3只

FUND_DIR='data/fundamentals_70stocks'
import csv
csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm={}
with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()

all_s=load_prices(stock_filter=None)
stocks={c:i for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
cd=get_common_dates(stocks)
print('[DATA] %d stocks, %d days' % (len(stocks),len(cd)))

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
print('[FACTOR] %d vals' % sum(len(v) for v in factor.values()))

def select_top5(candidates, held_sectors_count):
    """
    放宽规则: 同一赛道最多 MAX_SAME_SECTOR 只
    held_sectors_count: dict {sector -> count} of already-held positions
    """
    selected=[];sector_counts=dict(held_sectors_count)
    for code,score in candidates:
        if len(selected)>=MAX_POS: break
        sec=sm.get(code,'')
        cnt=sector_counts.get(sec,0)
        if cnt>=MAX_SAME_SECTOR: continue  # 该赛道已满
        # If this sector already has >=1, limit other sectors to 1 each?
        # Rule: max 3 same sector, others must be different
        # Check: if we add this, will any sector exceed 3? No, already checked.
        # Check: after adding, total unique non-majority sectors ≤ 2?
        # Actually just enforce: max_same <= 3, and non-same-sectors must each be 1
        # Simpler: count existing + new by sector, reject if > MAX_SAME_SECTOR
        # For non-majority: must be unique (max 1 each)
        other_ok=True
        new_counts=dict(sector_counts)
        new_counts[sec]=new_counts.get(sec,0)+1
        # Count how many sectors have 2+
        majority_secs=[s for s,c in new_counts.items() if c>=2]
        if len(majority_secs)>1:
            other_ok=False  # Only 1 sector can have majority
        if not other_ok: continue

        selected.append((code,score))
        sector_counts[sec]=sector_counts.get(sec,0)+1
    return selected,sector_counts

def backtest(trail, rule):
    cash=INIT;slot=INIT/MAX_POS;pos={};eq=[];trades=[]
    # Track focus stocks
    focus_hits={c:0 for c in ['300308','300394','300502']}
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}

    for di,dt in enumerate(cd):
        for code,p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px=stocks[code]['close'][idx[code][dt]]
            if px>p['peak']:p['peak']=px
            if px<=p['peak']*(1-trail):
                sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'exit':'trail','sector':sm.get(code,'')})
                del pos[code]

        if di%REBAL==0:
            cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
            cand.sort(key=lambda x:x[1],reverse=True)

            if rule=='strict':
                # Original: each sector unique
                top=set()
                sel_secs=set()
                for c,s in cand:
                    sec=sm.get(c,'')
                    if sec and sec in sel_secs: continue
                    if len(top)>=MAX_POS: break
                    top.add(c);sel_secs.add(sec)
            elif rule=='relaxed':
                # Relaxed: max 3 same sector
                selected,sc=select_top5(cand,{})
                top=set(c for c,_ in selected)
            else:  # 'none'
                top=set(c for c,_ in cand[:MAX_POS])

            # Sell non-top
            for code in list(pos.keys()):
                if code not in top:
                    px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                    cash+=pos[code]['shares']*sp
                    trades.append({'code':code,'name':stocks[code]['name'],
                        'bd':pos[code]['bd'],'sd':dt,
                        'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                        'exit':'rebal','sector':sm.get(code,'')})
                    del pos[code]

            # Count current sector counts
            held_secs=Counter(sm.get(c,'') for c in pos.keys())
            # Buy new
            remaining_slots=MAX_POS-len(pos)
            for code,sc in cand:
                if len(pos)>=MAX_POS: break
                if code in pos: continue  # Already held
                sec=sm.get(code,'')
                cnt=held_secs.get(sec,0)
                if cnt>=MAX_SAME_SECTOR: continue
                # For relaxed: check non-majority constraint
                if rule=='relaxed':
                    test_counts=dict(held_secs)
                    test_counts[sec]=test_counts.get(sec,0)+1
                    majority=[s for s,c in test_counts.items() if c>=2]
                    if len(majority)>1: continue
                if cash<slot*0.99: break
                raw=stocks[code]['close'][idx[code][dt]];bp=raw*(1+SLIP+B_FEE);sh=slot/bp;cash-=slot
                pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}
                held_secs[sec]=held_secs.get(sec,0)+1

        cash*=(1+RF/TD)
        pv=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date':dt,'equity':cash+pv,'pos':len(pos)})
        # Track focus hits
        for fc in focus_hits:
            if fc in pos: focus_hits[fc]+=1

    ld=cd[-1]
    for code,p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px=stocks[code]['close'][idx[code][ld]];sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'name':stocks[code]['name'],
                'bd':p['bd'],'sd':ld,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                'exit':'final','sector':sm.get(code,'')})
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
        e=t['exit'];exits[e]=exits.get(e,{'cnt':0,'ret':0.0})
        exits[e]['cnt']+=1;exits[e]['ret']+=t['ret']
    for e in exits: exits[e]['avg']=exits[e]['ret']/exits[e]['cnt']*100 if exits[e]['cnt'] else 0

    # Sector concentration
    sector_positions=Counter(t.get('sector','?') for t in trades if t['exit']=='rebal')
    # Focus stock hit days
    focus_hit_days={c:focus_hits[c] for c in focus_hits}

    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,
        'nt':len(trades),'wr':w/len(trades) if trades else 0,
        'exits':exits,'trades':trades,'eq':eq,
        'sector_positions':sector_positions,'focus_hit_days':focus_hit_days}

# ======================
# TEST
# ======================
print('\n'+'='*85)
print('  赛道限制对比: 严格 vs 放宽 vs 无限制')
print('  基准: K=1.5 LB=14d Trail=30% rebal=21d Top5')
print('='*85)

results={}
for rule,label in [('strict','①严格(每赛道1只)'),('relaxed','②放宽(同赛道≤3只)'),('none','③无限制')]:
    r=backtest(TRAIL,rule)
    results[label]=r
    print('  %-30s S=%.3f R=%7.1f%% DD=%5.1f%% CM=%.3f Trd=%d Win=%.0f%%' % (
        label,r['sh'],r['tr']*100,r['mdd']*100,r['calmar'],r['nt'],r['wr']*100))

# ======================
# Detail comparison
# ======================
base=results['①严格(每赛道1只)']
print('\n'+'='*85)
print('  vs 严格去重')
print('='*85)
print('  %-30s %8s %8s %8s %8s %8s %8s' % ('Config','dSharpe','dRet%','dMDD%','dCalmar','dTrd','dWin'))
print('  %s' % ('-'*75))
for label,r in results.items():
    if '严格' in label: continue
    ds=r['sh']-base['sh'];dr=(r['tr']-base['tr'])*100
    dd=(r['mdd']-base['mdd'])*100;dc=r['calmar']-base['calmar']
    dtrd=r['nt']-base['nt'];dwr=(r['wr']-base['wr'])*100
    print('  %-30s %+7.3f %+7.1f %+7.1f %+7.3f %+6d %+7.1f%%' % (label[:30],ds,dr,dd,dc,dtrd,dwr))

# ======================
# Super stock participation
# ======================
print('\n'+'='*85)
print('  超级牛股参与天数')
print('='*85)
for label,r in results.items():
    fh=r['focus_hit_days']
    parts=' | '.join('%s:%dd' % (stocks[c]['name'][:3],fh[c]) for c in fh)
    print('  %-30s %s' % (label[:30],parts))

# ======================
# Sector concentration detail
# ======================
print('\n'+'='*85)
print('  赛道集中度 (rebalance退出时的赛道)')
print('='*85)
for label,r in results.items():
    sp=r.get('sector_positions',{})
    top3=sp.most_common(3)
    total=sum(sp.values())
    top3_pct=sum(v for _,v in top3)/total*100 if total>0 else 0
    print('  %-30s Top3赛道占比=%.0f%%: %s' % (label[:30],top3_pct,
        ', '.join('%s(%d)' % (s,c) for s,c in top3)))

# ======================
# Best config annual returns
# ======================
print('\n'+'='*85)
print('  年度收益对比')
print('='*85)
# Quick annual for each
for label,r in results.items():
    yr=defaultdict(lambda:{'s':None,'e':None})
    for d in r['eq']:
        yk=d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s']=d['equity']
        yr[yk]['e']=d['equity']
    ann={y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}
    parts=' '.join('%s:%+5.1f%%' % (y,ann.get(y,0)) for y in sorted(ann.keys()))
    print('  %-30s %s' % (label[:30],parts))

print('\nDone!')
