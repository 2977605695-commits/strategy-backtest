"""NoChase 细扫 8%-14%"""
import sys,io,os,math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates
import csv

INIT=10_000_000;RF=0.025;TD=252;K=1.5;LB=14;TRAIL=0.30;REBAL=21;MIN_F=0.8
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005

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
    ma_vol=calc_ma(vols,20);vals={}
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
def get_si(code,dt):
    if code not in stocks: return None
    im={d:i for i,d in enumerate(stocks[code]['dates'])}
    return im.get(dt)

def bt(no_chase):
    cash=INIT;pos={};trades=[];eq=[]
    for di,dt in enumerate(cd):
        for code,p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px=stocks[code]['close'][idx[code][dt]]
            if px>p['peak']:p['peak']=px
            if px<=p['peak']*(1-TRAIL):
                sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'trail'})
                del pos[code]
        if di%REBAL==0:
            cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c] and s>=MIN_F]
            if no_chase:
                filtered=[]
                for c,s in cand:
                    si=get_si(c,dt)
                    if si is not None and si>=5:
                        px_now=stocks[c]['close'][si];px_5d=stocks[c]['close'][si-5]
                        rally=(px_now-px_5d)/px_5d if px_5d>0 else 0
                        if rally>no_chase: continue
                    filtered.append((c,s))
                cand=filtered
            cand.sort(key=lambda x:x[1],reverse=True)
            selected=[];sel_secs=set()
            for c,s in cand:
                sec=sm.get(c,'')
                if sec and sec in sel_secs: continue
                if len(selected)>=5: break
                selected.append((c,s));sel_secs.add(sec)
            n_select=len(selected);top=set(c for c,_ in selected)
            for code in list(pos.keys()):
                if code not in top:
                    px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                    cash+=pos[code]['shares']*sp
                    trades.append({'code':code,'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,'exit':'rebal'})
                    del pos[code]
            pv2=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
            nav=cash+pv2;target=nav/n_select if n_select>0 else 0
            for code,score in selected:
                if code in pos: continue
                if cash<target*0.99: break
                bv=min(target,cash)
                if bv<=0: break
                raw=stocks[code]['close'][idx[code][dt]];bp=raw*(1+SLIP+B_FEE)
                if bp>0 and bv>bp*0.01:
                    sh=bv/bp;cash-=bv
                    pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}
        cash*=(1+RF/TD)
        pv3=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append(cash+pv3)
    ld=cd[-1]
    for code,p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px=stocks[code]['close'][idx[code][ld]];sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'final'})
    pos.clear()
    v=eq;tr=(v[-1]-v[0])/v[0];rs=[(v[i]-v[i-1])/v[i-1] for i in range(1,len(v)) if v[i-1]>0]
    if not rs: rs=[0]
    y=len(rs)/TD;cagr=(v[-1]/v[0])**(1/y)-1 if y>0 and v[0]>0 else 0
    mu=sum(rs)/len(rs) if rs else 0
    sd=(sum((r-mu)**2 for r in rs)/len(rs))**0.5 if rs else 0
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk=v[0];maxdd=0.0
    for x in v:
        if x>pk: pk=x
        ddx=(pk-x)/pk
        if ddx>maxdd: maxdd=ddx
    cm=cagr/maxdd if maxdd>0 else float('inf')
    w=sum(1 for t in trades if t['ret']>0);nl=sum(1 for t in trades if t['ret']<0)
    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':maxdd,'calmar':cm,'nt':len(trades),
        'wr':w/len(trades) if trades else 0,'n_loss':nl,
        'loss_rate':nl/len(trades)*100 if trades else 0}

print('='*80)
print('  NoChase 细扫: 8%~14%')
print('  基准: K=1.5 LB=14 Trail=30% min_f=0.8 Strict-Sector')
print('='*80)
results={}
for nc in [0,0.08,0.09,0.10,0.11,0.12,0.13,0.14]:
    r=bt(nc if nc>0 else None)
    results[nc]=r
    print('  NoChase>%d%% S=%.3f R=%7.1f%% DD=%5.1f%% CM=%.3f Trd=%d Win=%d%%' % (
        nc,r['sh'],r['tr']*100,r['mdd']*100,r['calmar'],r['nt'],int(r['wr']*100)))

base=results[0]
print('\n  vs Baseline (NoChase>0):')
for nc in sorted(results):
    if nc==0: continue
    r=results[nc];ds=r['sh']-base['sh'];dd=(r['mdd']-base['mdd'])*100;dr=(r['tr']-base['tr'])*100
    impr=(ds>0 or dd<0);tag=' *' if impr else ''
    print('  >%d%% dS=%+.3f dR=%+.0f%% dDD=%+.1fpp%s' % (nc,ds,dr,dd,tag))

best_nc=max((nc for nc in results if nc>0),key=lambda nc:results[nc]['sh'])
br=results[best_nc]
print('\n  BEST: NoChase>%d%% S=%.3f R=%.1f%% DD=%.1f%%' % (best_nc,br['sh'],br['tr']*100,br['mdd']*100))
print('Done!')
