"""
熔断+强制重入 vs 原版熔断 vs 无熔断
"""
import sys,io,os,math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates

INIT=10_000_000;RF=0.025;TD=252;MAX_POS=5
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
K=1.5;LB=14;TRAIL=0.30;REBAL=21;MIN_F=0.8

import csv
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

idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}

def do_rebalance(di,dt,cash,pos):
    cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
    cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
    cand=[(c,s) for c,s in cand if s>=MIN_F]
    cand.sort(key=lambda x:x[1],reverse=True)
    selected=[];sel_secs=set()
    for c,s in cand:
        sec=sm.get(c,'')
        if sec and sec in sel_secs: continue
        if len(selected)>=MAX_POS: break
        selected.append((c,s));sel_secs.add(sec)
    top_codes=set(c for c,_ in selected)
    trades=[]
    for code in list(pos.keys()):
        if code not in top_codes:
            px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
            cash+=pos[code]['shares']*sp
            trades.append({'code':code,'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,'exit':'rebal'})
            del pos[code]
    pv2=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
    nav=cash+pv2;target=nav/len(selected) if selected else 0
    for code,score in selected:
        if code in pos: continue
        if cash<target*0.99: break
        bv=min(target,cash)
        if bv<=0: break
        raw=stocks[code]['close'][idx[code][dt]];bp=raw*(1+SLIP+B_FEE)
        if bp>0 and bv>bp*0.01:
            sh=bv/bp;cash-=bv
            pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}
    return cash,pos,trades

def backtest(crash_threshold, reentry_delay):
    cash=INIT;pos={};trades=[];eq=[]
    guard_countdown=0;guards_hit=0;prev_nav=INIT
    for di,dt in enumerate(cd):
        pv=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        nav=cash+pv;daily_ret=(nav-prev_nav)/prev_nav if prev_nav>0 else 0

        # Crash guard trigger
        if crash_threshold and daily_ret<crash_threshold and guard_countdown==0:
            prev_nav_before=nav
            for code,p in list(pos.items()):
                px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                cash+=p['shares']*sp
                trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'crash'})
            pos.clear();guards_hit+=1
            guard_countdown=reentry_delay
            nav=cash

        if guard_countdown>0:
            guard_countdown-=1
            if guard_countdown==0:
                # Force re-entry immediately
                cash,pos,nt=do_rebalance(di,dt,cash,pos)
                trades.extend(nt)

        if guard_countdown==0:
            for code,p in list(pos.items()):
                if code not in idx or dt not in idx[code]: continue
                px=stocks[code]['close'][idx[code][dt]]
                if px>p['peak']:p['peak']=px
                if px<=p['peak']*(1-TRAIL):
                    sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                    trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'trail'})
                    del pos[code]
            if di%REBAL==0:
                cash,pos,nt=do_rebalance(di,dt,cash,pos)
                trades.extend(nt)

        cash*=(1+RF/TD)
        pv2=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append(cash+pv2);prev_nav=cash+pv2

    ld=cd[-1]
    for code,p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px=stocks[code]['close'][idx[code][ld]];sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'final'})
    pos.clear()

    v=eq;tr=(v[-1]-v[0])/v[0];rs=[(v[i]-v[i-1])/v[i-1] for i in range(1,len(v)) if v[i-1]>0]
    y=len(rs)/TD;cagr=(v[-1]/v[0])**(1/y)-1 if y>0 else 0
    mu=sum(rs)/len(rs) if rs else 0
    sd=(sum((r-mu)**2 for r in rs)/len(rs))**0.5 if rs else 0
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk=v[0];mdd=0.0
    for x in v:
        if x>pk:pk=x
        d2=(pk-x)/pk
        if d2>mdd:mdd=d2
    cm=cagr/mdd if mdd>0 else float('inf')
    w=sum(1 for t in trades if t['ret']>0);nl=sum(1 for t in trades if t['ret']<0)
    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,'nt':len(trades),
        'wr':w/len(trades) if trades else 0,'n_loss':nl,
        'loss_rate':nl/len(trades)*100 if trades else 0,'guards':guards_hit,'eq':eq}

print('='*85)
print('  熔断+强制重入 vs 原版熔断 vs 无熔断')
print('  基准: K=1.5 LB=14 Trail=30% min_f=0.8')
print('='*85)

configs=[
    ('#0 Baseline (no guard)',None,0),
    ('#1 Fast: -5%+5d reentry',-0.05,5),
    ('#2 Fast: -5%+3d reentry',-0.05,3),
    ('#3 Fast: -5%+7d reentry',-0.05,7),
    ('#4 Medium: -5%+10d reentry',-0.05,10),
    ('#5 Old: -5%+wait21d',-0.05,21),
    ('#6 Fast: -4%+5d reentry',-0.04,5),
    ('#7 Fast: -3%+5d reentry',-0.03,5),
]

print('  %-35s %7s %8s %6s %6s %6s %5s %5s %5s %5s' % (
    'Config','Sharpe','Ret','CAGR','MDD','Calmar','Trd','Win','Loss','Guard'))
print('  '+'-'*85)
results={}
for label,ct,rd in configs:
    r=backtest(ct,rd)
    results[label]=r
    tag=' *' if r['sh']==max(x['sh'] for x in results.values()) else ''
    tag2=' @' if r['mdd']==min(x['mdd'] for x in results.values()) else ''
    print('  %-35s %7.3f %7.1f%% %5.2f%% %5.1f%% %6.3f %4d %4.0f%% %4.0f%% %5d%s%s' % (
        label[:35],r['sh'],r['tr']*100,r['cagr']*100,r['mdd']*100,r['calmar'],
        r['nt'],r['wr']*100,r['loss_rate'],r['guards'],tag,tag2))

base=results['#0 Baseline (no guard)']
print('\n  vs Baseline:')
print('  %-35s %8s %8s %8s %8s %8s' % ('Config','dSharpe','dRet%','dMDD%','dLoss%','Guard'))
print('  '+'-'*70)
for label,r in results.items():
    if 'Baseline' in label: continue
    ds=r['sh']-base['sh'];dr=(r['tr']-base['tr'])*100
    dd=(r['mdd']-base['mdd'])*100;dl=r['loss_rate']-base['loss_rate']
    impr=(ds>0 or dd<0 or dl<0);tag=' *' if impr else ''
    print('  %-35s %+7.3f %+7.1f %+7.1f %+7.1f%% %5d%s' % (label[:35],ds,dr,dd,dl,r['guards'],tag))

print('\nDone!')
