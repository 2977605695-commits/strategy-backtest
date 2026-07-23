"""
峰岭因子改进方案: ①盈利不换 ②动量加分 ③组合
"""
import sys,io,os,math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates
import csv

INIT=10_000_000;RF=0.025;TD=252;MAX_POS=5
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
K=1.5;LB=14;REBAL=21

FUND_DIR='data/fundamentals_70stocks'
csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm={}
with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()

all_s=load_prices(stock_filter=None)
stocks={c:i for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
cd=get_common_dates(stocks)

# Factor
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

def backtest(trail, lock_profit, momentum_boost, momentum_weight=0.3):
    """
    lock_profit: float 持仓浮盈>此值则不参与重排踢出
    momentum_boost: float 给因子值加 momentum_weight * Z(21日涨幅)
    """
    cash=INIT;slot=INIT/MAX_POS;pos={};eq=[];trades=[]
    locked_saves=0
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}

    for di,dt in enumerate(cd):
        # Trail exits
        for code,p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px=stocks[code]['close'][idx[code][dt]]
            if px>p['peak']:p['peak']=px
            if px<=p['peak']*(1-trail):
                sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                ret=(sp-p['bp'])/p['bp'] if p['bp']>0 else 0
                trades.append({'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,'ret':ret,'exit':'trail',
                    'float_pct':(px-p['bp'])/p['bp']*100 if p['bp']>0 else 0})
                del pos[code]

        if di%REBAL==0:
            # Compute momentum boost factor
            mom_z={}
            if momentum_boost:
                raw_mom={}
                for code in stocks:
                    if code in idx and dt in idx[code]:
                        si=idx[code][dt]
                        if si>=21:
                            px_now=stocks[code]['close'][si]
                            px_21=stocks[code]['close'][si-21]
                            raw_mom[code]=(px_now-px_21)/px_21 if px_21>0 else 0
                # Compute cross-sectional Z of momentum
                if raw_mom:
                    vals=list(raw_mom.values())
                    mu=sum(vals)/len(vals);var=sum((v-mu)**2 for v in vals)/len(vals);sd=var**0.5 if var>0 else 1
                    mom_z={c:(v-mu)/sd for c,v in raw_mom.items()}

            # Build score = factor + momentum boost
            raw_score={}
            for code in stocks:
                fv=factor.get(code,{}).get(dt,float('nan'))
                if math.isnan(fv): continue
                score=fv
                if momentum_boost and code in mom_z:
                    score+=momentum_weight*mom_z[code]
                raw_score[code]=score

            cand=[(c,raw_score[c]) for c in raw_score]
            cand.sort(key=lambda x:x[1],reverse=True)
            top=set(c for c,_ in cand[:MAX_POS])

            # Rebalance: sell non-top, but protect locked-in-profit positions
            for code in list(pos.keys()):
                if code not in top:
                    # Check if locked (protected from rebalance)
                    px=stocks[code]['close'][idx[code][dt]]
                    float_pct=(px-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0
                    if lock_profit and float_pct>lock_profit:
                        locked_saves+=1
                        continue  # Protect this position!
                    sp=px*(1-SLIP-S_FEE-STAX);cash+=pos[code]['shares']*sp
                    trades.append({'code':code,'name':stocks[code]['name'],
                        'bd':pos[code]['bd'],'sd':dt,
                        'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                        'exit':'rebal','float_pct':float_pct*100})
                    del pos[code]

            # Buy new
            hc=set(pos.keys());hs={sm.get(c,'') for c in hc}
            for code,sc in cand:
                if len(pos)>=MAX_POS:break
                if code in hc:continue
                s=sm.get(code,'')
                if s and s in hs:continue
                if cash<slot*0.99:break
                raw=stocks[code]['close'][idx[code][dt]];bp=raw*(1+SLIP+B_FEE);sh=slot/bp;cash-=slot
                pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}
                hc.add(code);hs.add(s)

        cash*=(1+RF/TD)
        pv=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date':dt,'equity':cash+pv,'pos':len(pos)})

    # Final
    ld=cd[-1]
    for code,p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px=stocks[code]['close'][idx[code][ld]];sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'name':stocks[code]['name'],
                'bd':p['bd'],'sd':ld,
                'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'final'})
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
    # Exit breakdown
    exits={}
    for t in trades:
        e=t['exit'];exits[e]=exits.get(e,{'cnt':0,'ret':0.0})
        exits[e]['cnt']+=1;exits[e]['ret']+=t['ret']
    for e in exits: exits[e]['avg']=exits[e]['ret']/exits[e]['cnt']*100 if exits[e]['cnt'] else 0

    # Focus-stock participation
    focus_trades={c:[] for c in ['300308','300394','300502']}
    for t in trades:
        if t['code'] in focus_trades:
            focus_trades[t['code']].append(t)

    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,
        'nt':len(trades),'wr':w/len(trades) if trades else 0,
        'exits':exits,'locked_saves':locked_saves,
        'focus_trades':focus_trades,'trades':trades,'eq':eq}

# ======================
# TEST ALL
# ======================
print('\n'+'='*85)
print('  改进方案测试: ①盈利不换  ②动量加分  ③ ①+②组合')
print('  基准: Trail=30% K=1.5 LB=14d rebal=21d')
print('='*85)

configs=[
    ('#0 Baseline', 0.30, None, False, 0),
    ('#0.5 Trail=25% (less strict)', 0.25, None, False, 0),
    # ① 盈利不换
    ('#1 Lock >20% profit', 0.30, 0.20, False, 0),
    ('#2 Lock >30% profit', 0.30, 0.30, False, 0),
    ('#3 Lock >50% profit', 0.30, 0.50, False, 0),
    # ② 动量加分
    ('#4 Mom+w=0.2', 0.30, None, True, 0.2),
    ('#5 Mom+w=0.3', 0.30, None, True, 0.3),
    ('#6 Mom+w=0.5', 0.30, None, True, 0.5),
    # ③ 组合
    ('#7 Lock>20% + Mom0.3', 0.30, 0.20, True, 0.3),
    ('#8 Lock>30% + Mom0.3', 0.30, 0.30, True, 0.3),
    ('#9 Lock>20% + Mom0.3 Trail25%', 0.25, 0.20, True, 0.3),
]

results={}
for label,trail,p_lock,boost,mw in configs:
    r=backtest(trail,p_lock,boost,mw)
    results[label]=r
    print('  %-35s S=%6.3f R=%7.1f%% DD=%5.1f%% CM=%6.3f Trd=%4d Win=%3.0f%% Locks=%d' % (
        label[:35], r['sh'], r['tr']*100, r['mdd']*100, r['calmar'], r['nt'], r['wr']*100, r['locked_saves']))

# ======================
# vs baseline
# ======================
base=results['#0 Baseline']
print('\n'+'='*85)
print('  vs Baseline')
print('='*85)
print('  %-35s %8s %8s %8s %8s %8s' % ('Config','dSharpe','dRet%','dMDD%','dTrd','Locks'))
print('  %s' % ('-'*65))
for label,r in results.items():
    if label=='#0 Baseline': continue
    ds=r['sh']-base['sh'];dr=(r['tr']-base['tr'])*100
    dd=(r['mdd']-base['mdd'])*100;dtrd=r['nt']-base['nt']
    print('  %-35s %+7.3f %+7.1f %+7.1f %+6d %6d' % (label[:35],ds,dr,dd,dtrd,r['locked_saves']))

# ======================
# Focus stock participation
# ======================
print('\n'+'='*85)
print('  超级牛股参与度: 中际旭创 / 天孚通信 / 新易盛')
print('='*85)
for label,r in results.items():
    focus=r['focus_trades']
    parts=[]
    for c in ['300308','300394','300502']:
        ft=focus[c]
        if ft:
            n=len(ft);avg=sum(t['ret'] for t in ft)/n*100
            parts.append('%s:%d笔/avg%.0f%%' % (stocks[c]['name'][:3],n,avg))
        else:
            parts.append('%s:0' % stocks[c]['name'][:3])
    print('  %-35s %s' % (label[:35], ' | '.join(parts)))

# ======================
# Best config exit detail
# ======================
best_label=max(results,key=lambda x:results[x]['sh'])
best=results[best_label]
print('\n'+'='*85)
print('  BEST: %s' % best_label)
print('  S=%.3f R=%.1f%% DD=%.1f%% CM=%.2f Trd=%d Win=%.0f%%' % (
    best['sh'],best['tr']*100,best['mdd']*100,best['calmar'],best['nt'],best['wr']*100))
print('  Exits:')
for e,d in sorted(best['exits'].items()):
    print('    %-15s %3d trades  avg=%.1f%%' % (e,d['cnt'],d['avg']))

print('\nDone!')
