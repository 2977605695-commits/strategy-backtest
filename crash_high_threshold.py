"""
高阈值熔断: -7%/-8%/-9%/-10% + 10d/14d/21d 强制重入
"""
import sys,io,os,math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates
import csv

INIT=10_000_000;RF=0.025;TD=252;MAX_POS=5
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
K=1.5;LB=14;TRAIL=0.30;REBAL=21;MIN_F=0.8

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
        thr=ma_vol[i]+K*std;ps=0.0;rs=0.0
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
    cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c] and s>=MIN_F]
    cand.sort(key=lambda x:x[1],reverse=True)
    selected=[];sel_secs=set()
    for c,s in cand:
        sec=sm.get(c,'')
        if sec and sec in sel_secs: continue
        if len(selected)>=MAX_POS: break
        selected.append((c,s));sel_secs.add(sec)
    top=set(c for c,_ in selected)
    trades=[]
    for code in list(pos.keys()):
        if code not in top:
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

def backtest(thr,delay):
    cash=INIT;pos={};trades=[];eq=[]
    cd_rem=0;hits=[];prev_nav=INIT
    for di,dt in enumerate(cd):
        pv=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        nav=cash+pv;dr=(nav-prev_nav)/prev_nav if prev_nav>0 else 0

        if thr and dr<thr and cd_rem==0:
            for code,p in list(pos.items()):
                px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                cash+=p['shares']*sp
                trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'crash'})
            pos.clear();hits.append({'di':di,'dt':dt,'dr':dr*100,'nav':nav})
            cd_rem=delay;nav=cash

        if cd_rem>0:
            cd_rem-=1
            if cd_rem==0:
                cash,pos,nt=do_rebalance(di,dt,cash,pos)
                trades.extend(nt)

        if cd_rem==0:
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
        pv3=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append(cash+pv3);prev_nav=cash+pv3

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
        'loss_rate':nl/len(trades)*100 if trades else 0,'hits':len(hits),
        'hit_list':hits,'eq':eq}

print('='*95)
print('  高阈值熔断: -7%/-8%/-9%/-10% + 10d/14d/21d 强制重入')
print('  基准: K=1.5 LB=14 Trail=30% min_f=0.8')
print('='*95)

configs=[
    ('#0 Baseline',None,0),
    ('-7%+10d',-0.07,10),('-7%+14d',-0.07,14),('-7%+21d',-0.07,21),
    ('-8%+10d',-0.08,10),('-8%+14d',-0.08,14),('-8%+21d',-0.08,21),
    ('-9%+10d',-0.09,10),('-9%+14d',-0.09,14),('-9%+21d',-0.09,21),
    ('-10%+14d',-0.10,14),('-10%+21d',-0.10,21),
]

print('  %-15s %7s %8s %6s %6s %6s %5s %5s %5s %5s' % (
    'Config','Sharpe','Ret','CAGR','MDD','Calmar','Trd','Win','Loss','Hits'))
print('  '+'-'*80)
results={}
for label,ct,rd in configs:
    r=backtest(ct,rd)
    results[label]=r
    tag=' *' if r['sh']==max(x['sh'] for x in results.values()) else ''
    tag2=' @' if r['mdd']==min(x['mdd'] for x in results.values()) else ''
    print('  %-15s %7.3f %7.1f%% %5.2f%% %5.1f%% %6.3f %4d %4.0f%% %4.0f%% %5d%s%s' % (
        label[:15],r['sh'],r['tr']*100,r['cagr']*100,r['mdd']*100,r['calmar'],
        r['nt'],r['wr']*100,r['loss_rate'],r['hits'],tag,tag2))

base=results['#0 Baseline']
print('\n  vs Baseline:')
print('  %-15s %8s %8s %8s %8s %8s' % ('Config','dSharpe','dRet%','dMDD%','dLoss%','Hits'))
print('  '+'-'*60)
for label,r in results.items():
    if 'Baseline' in label: continue
    ds=r['sh']-base['sh'];dr=(r['tr']-base['tr'])*100
    dd=(r['mdd']-base['mdd'])*100;dl=r['loss_rate']-base['loss_rate']
    impr=(ds>0 or dd<0 or dl<0);tag=' *' if impr else ''
    print('  %-15s %+7.3f %+7.1f %+7.1f %+7.1f%% %5d%s' % (label[:15],ds,dr,dd,dl,r['hits'],tag))

# Hit analysis for best high-threshold
if results:
    best=max((k for k in results if 'Baseline' not in k),key=lambda k:results[k]['sh'])
    br=results[best]
    print('\n  BEST: %s -> S=%.3f R=%.1f%% DD=%.1f%% Hits=%d' % (best,br['sh'],br['tr']*100,br['mdd']*100,br['hits']))
    if br['hit_list']:
        print('  Hit dates:')
        for h in br['hit_list']:
            print('    %s: -%.1f%% (nav=%.0f万)' % (h['dt'],-h['dr'],h['nav']/1e4))

print('\nDone!')
