"""OOS过拟合验证: 44只训练池 vs 50只全新股票"""
import sys,io,os,math,json
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import calc_ma
import csv

INIT=10_000_000;RF=0.025;TD=252
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
K=1.5;LB=14;TRAIL=0.22;REBAL=21;MIN_F=0.8;NO_CHASE=0.10

def load_stocks(dir_path):
    stocks={}
    for fname in sorted(os.listdir(dir_path)):
        if not fname.endswith('.json') or fname.startswith('_'): continue
        with open(os.path.join(dir_path,fname),encoding='utf-8') as f:
            d=json.load(f)
        dates=[b['date'] for b in d['bars']]
        closes=[b['close'] for b in d['bars']]
        opens=[b['open'] for b in d['bars']]
        highs=[b['high'] for b in d['bars']]
        lows=[b['low'] for b in d['bars']]
        vols=[b['volume'] for b in d['bars']]
        stocks[d['code']]={
            'name':d['name'],'dates':dates,'close':closes,
            'open':opens,'high':highs,'low':lows,'volume':vols}
    return stocks

def get_common_dates(stocks):
    sets=[set(s['dates']) for s in stocks.values()]
    return sorted(sets[0].intersection(*sets[1:]))

def calc_factor(stocks):
    fac={}
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
        fac[code]=vals
    return fac

def backtest(stocks,factor,dates,has_sectors=False):
    sm={}
    if has_sectors:
        FUND_DIR='data/fundamentals_70stocks'
        csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
        with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
            for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()

    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    cash=INIT;pos={};trades=[];eq=[]

    for di,dt in enumerate(dates):
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
            # NoChase filter
            filtered=[]
            for c,s in cand:
                si=idx[c].get(dt)
                if si is not None and si>=5:
                    px_now=stocks[c]['close'][si];px_5d=stocks[c]['close'][si-5]
                    if px_5d>0 and (px_now-px_5d)/px_5d>NO_CHASE: continue
                filtered.append((c,s))
            cand=filtered
            cand.sort(key=lambda x:x[1],reverse=True)

            selected=[];sel_secs=set()
            for c,s in cand:
                sec=sm.get(c,'')
                if has_sectors and sec and sec in sel_secs: continue
                if len(selected)>=5: break
                selected.append((c,s))
                if has_sectors: sel_secs.add(sec)

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

    ld=dates[-1]
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
        if x>pk:pk=x
        cur_dd=(pk-x)/pk
        if cur_dd>maxdd:maxdd=cur_dd
    cm=cagr/maxdd if maxdd>0 else float('inf')
    w=sum(1 for t in trades if t['ret']>0);nl=sum(1 for t in trades if t['ret']<0)
    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':maxdd,'calmar':cm,'nt':len(trades),
        'wr':w/len(trades) if trades else 0,'n_loss':nl,
        'loss_rate':nl/len(trades)*100 if trades else 0,'eq':eq}

# Load both pools
base_dir='data'
oos_dir='data_oos'

print('[LOAD] Training pool (44 stocks)...')
stocks_train=load_stocks(base_dir)
stocks_train={c:i for c,i in stocks_train.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
cd_train=get_common_dates(stocks_train)
print('  %d stocks, %d days (%s - %s)' % (len(stocks_train),len(cd_train),cd_train[0],cd_train[-1]))

print('[LOAD] OOS pool (50 new stocks)...')
stocks_oos=load_stocks(oos_dir)
cd_oos=get_common_dates(stocks_oos)
print('  %d stocks, %d days (%s - %s)' % (len(stocks_oos),len(cd_oos),cd_oos[0],cd_oos[-1]))

# Use common date range: 2024-01-21 onwards (MA20 + LB14 + NoChase5d)
START='20240121'
cd_train_s=[d for d in cd_train if d>=START]
cd_oos_s=[d for d in cd_oos if d>=START]
# Intersection of both
common_dates=sorted(set(cd_train_s) & set(cd_oos_s))
print('\n  Common backtest dates from %s: %d days (%.1fyr)' % (START,len(common_dates),len(common_dates)/252))

# Factor calc
print('\n[FACTOR] Computing...')
f_train=calc_factor(stocks_train)
f_oos=calc_factor(stocks_oos)
print('  Train: %d vals | OOS: %d vals' % (
    sum(len(v) for v in f_train.values()),
    sum(len(v) for v in f_oos.values())))

# Run backtests
print('\n[BACKTEST] Running...')
r_train=backtest(stocks_train,f_train,common_dates,has_sectors=True)
r_oos_sector=backtest(stocks_oos,f_oos,common_dates,has_sectors=False)
r_oos_nosector=backtest(stocks_oos,f_oos,common_dates,has_sectors=False)  # same as above

print('\n'+'='*95)
print('  OOS Validation Results')
print('  Same params: K=%.1f LB=%dd Trail=%d%% NC=%d%% min_f=%.1f Rebal=%dd' % (
    K,LB,TRAIL*100,NO_CHASE*100,MIN_F,REBAL))
print('='*95)

print('\n  %-25s %7s %8s %6s %6s %6s %5s %5s %6s' % (
    'Pool','Sharpe','Ret','CAGR','MDD','Calmar','Trd','Win','Loss'))
print('  '+'-'*75)
for label,r in [
    ('44-stocks (train)',r_train),
    ('50-OOS (no sectors)',r_oos_nosector),
]:
    print('  %-25s %7.3f %7.1f%% %5.2f%% %5.1f%% %6.3f %4d %4.0f%% %5.0f%%' % (
        label[:25],r['sh'],r['tr']*100,r['cagr']*100,r['mdd']*100,r['calmar'],
        r['nt'],r['wr']*100,r['loss_rate']))

# KEY: degradation ratio
print('\n  --- Degradation Analysis ---')
sh_ratio=r_oos_nosector['sh']/r_train['sh'] if r_train['sh']>0 else 0
mdd_ratio=r_oos_nosector['mdd']/r_train['mdd'] if r_train['mdd']>0 else 0
print('  Sharpe ratio (OOS/Train): %.2f (target > 0.7)' % sh_ratio)
print('  MDD ratio (OOS/Train): %.2f (target < 1.5)' % mdd_ratio)
if sh_ratio>0.7 and mdd_ratio<1.5:
    print('  VERDICT: Not overfit - strategy generalizes to out-of-sample stocks')
elif sh_ratio>0.5:
    print('  VERDICT: Moderate degradation - partially overfit, but still works')
else:
    print('  VERDICT: SIGNIFICANT OVERFITTING - strategy does NOT generalize')

# Annual returns
def annual(eq,dates):
    yr=defaultdict(lambda:{'s':None,'e':None})
    for i,d in enumerate(eq):
        yk=dates[i][:4]
        if yr[yk]['s'] is None: yr[yk]['s']=d
        yr[yk]['e']=d
    return {y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}

print('\n  --- Annual Returns ---')
print('  %-6s %12s %12s' % ('Year','44-Train','50-OOS'))
for y in ['2024','2025','2026']:
    ar_train=annual(r_train['eq'],common_dates)
    ar_oos=annual(r_oos_nosector['eq'],common_dates)
    rt=ar_train.get(y,0);ro=ar_oos.get(y,0)
    print('  %-6s %+10.1f%% %+10.1f%%' % (y,rt,ro))

print('\nDone!')
