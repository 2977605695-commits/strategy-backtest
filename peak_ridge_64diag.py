# -*- coding: utf-8 -*-
import sys,io,os,math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates

INIT=10_000_000;RF=0.025;TD=252;MAX_POS=5
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
TRAIL=0.30;REBAL=21;K=1.5;LB=14

FUND_DIR='data/fundamentals_70stocks'
csv_files=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm={}
import csv
with open(os.path.join(FUND_DIR,csv_files[-1]),'r',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()

all_s=load_prices(stock_filter=None)
stocks_44={c:i for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
stocks_64={c:i for c,i in all_s.items() if len(i['dates'])>=200}

def calc_factor(stocks):
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
    return factor

def backtest(stocks,factor,dates):
    cash=INIT;slot=INIT/MAX_POS;pos={};eq=[];trades=[]
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    for di,dt in enumerate(dates):
        for code,p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px=stocks[code]['close'][idx[code][dt]]
            if px>p['peak']:p['peak']=px
            if px<=p['peak']*(1-TRAIL):
                sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'exit':'trail'})
                del pos[code]
        if di%REBAL==0:
            cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
            cand.sort(key=lambda x:x[1],reverse=True)
            top=set(c for c,_ in cand[:MAX_POS])
            for code in list(pos.keys()):
                if code not in top:
                    px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                    cash+=pos[code]['shares']*sp
                    trades.append({'code':code,'name':stocks[code]['name'],
                        'bd':pos[code]['bd'],'sd':dt,
                        'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                        'exit':'rebal'})
                    del pos[code]
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
    ld=dates[-1]
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
    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,
        'nt':len(trades),'wr':w/len(trades) if trades else 0,
        'trades':trades,'eq':eq}

print('44-old: %d stocks' % len(stocks_44))
print('64-all: %d stocks' % len(stocks_64))

print('\n[FACTOR] Computing...')
f44=calc_factor(stocks_44)
f64=calc_factor(stocks_64)
print('  44-old: %d vals' % sum(len(v) for v in f44.values()))
print('  64-all: %d vals' % sum(len(v) for v in f64.values()))

cd44=get_common_dates(stocks_44)
cd64=get_common_dates(stocks_64)
start='20240121'
cd44s=[d for d in cd44 if d>=start]
cd64s=[d for d in cd64 if d>=start]
print('\n  From %s:' % start)
print('  44-old: %d days' % len(cd44s))
print('  64-all: %d days' % len(cd64s))

print('\n[BACKTEST] Full & 2024+')
r44_full=backtest(stocks_44,f44,cd44)
r44_24=backtest(stocks_44,f44,cd44s)
r64_24=backtest(stocks_64,f64,cd64s)

print('\n  %-18s %11s %11s %11s' % ('Metric','44-full','44-2024+','64-2024+'))
print('  %s' % ('-'*52))
for name,key,scale in [('Sharpe','sh',1),('Return','tr',100),('MDD','mdd',100),
                       ('Trades','nt',1),('Win Rate','wr',100)]:
    v44=r44_full[key]*scale;v44s=r44_24[key]*scale;v64=r64_24[key]*scale
    print('  %-18s %10.2f %10.2f %10.2f' % (name, v44, v44s, v64))

# ===== DIAGNOSIS =====
new_codes=set(stocks_64.keys())-set(stocks_44.keys())

print('\n%s' % ('-'*70))
print('  DIAGNOSIS: 64-all vs 44-old')
print('%s' % ('-'*70))

# 1. New stocks
print('\n  [1] %d new stocks (STAR/ChiNext):' % len(new_codes))
for code in sorted(new_codes):
    name=stocks_64[code]['name'];nb=len(stocks_64[code]['dates'])
    first=stocks_64[code]['dates'][0];last=stocks_64[code]['dates'][-1]
    print('    %s %-12s %4dbars [%s~%s]' % (code,name,nb,first,last))

# 2. New stock trades
new_trades=[t for t in r64_24['trades'] if t['code'] in new_codes]
print('\n  [2] New stock trades: %d / %d total' % (len(new_trades), r64_24['nt']))
if new_trades:
    avg=sum(t['ret'] for t in new_trades)/len(new_trades)*100
    wins=sum(1 for t in new_trades if t['ret']>0)
    print('    avg=%.1f%% win=%d/%d' % (avg, wins, len(new_trades)))
    for code in sorted(new_codes):
        subs=[t for t in new_trades if t['code']==code]
        if subs:
            a=sum(t['ret'] for t in subs)/len(subs)*100
            w=sum(1 for t in subs if t['ret']>0)
            print('    %s %-12s %2d trades avg=%.1f%% win=%d/%d' % (code,stocks_64[code]['name'],len(subs),a,w,len(subs)))
else:
    print('    NO NEW STOCK TRADES!')

# 3. Why new stocks not selected?
print('\n  [3] Why new stocks not getting selected?')
# Get latest date from 44 pool
sample_code=list(stocks_44.keys())[0]
latest_date=sorted(f44.get(sample_code,{}).keys())[-1]
print('    Latest factor date: %s' % latest_date)

for code in sorted(new_codes):
    fv=f64.get(code,{}).get(latest_date,float('nan'))
    if math.isnan(fv):
        print('    %s %-12s: NO FACTOR (data too short, need %d days)' % (code,stocks_64[code]['name'],LB))
    else:
        all_f=[(c,f64[c].get(latest_date,float('nan'))) for c in stocks_64]
        all_f=[(c,s) for c,s in all_f if not math.isnan(s)]
        all_f.sort(key=lambda x:x[1],reverse=True)
        rank=next((i+1 for i,(c,_) in enumerate(all_f) if c==code),999)
        print('    %s %-12s: f=%.4f rank=%d/%d' % (code,stocks_64[code]['name'],fv,rank,len(all_f)))

# 4. Factor competition
print('\n  [4] Factor Top 10 comparison at %s:' % latest_date)
f44_top=[(c,f44[c].get(latest_date,float('nan'))) for c in stocks_44 if latest_date in f44.get(c,{})]
f44_top.sort(key=lambda x:x[1],reverse=True)
f64_top=[(c,f64[c].get(latest_date,float('nan'))) for c in stocks_64 if latest_date in f64.get(c,{})]
f64_top.sort(key=lambda x:x[1],reverse=True)

print('    44-pool Top 10:')
for c,v in f44_top[:10]:
    name=stocks_44[c]['name']
    r64=next((i+1 for i,(c2,_) in enumerate(f64_top) if c2==c),999)
    print('      %s %-12s f=%.4f  (64-rank: %d)' % (c,name,v,r64))

print('    64-pool Top 10:')
for c,v in f64_top[:10]:
    name=stocks_64[c]['name']
    newtag='[NEW]' if c in new_codes else '      '
    r44=next((i+1 for i,(c2,_) in enumerate(f44_top) if c2==c),999)
    in44='(44-rank: %d)'%r44 if c in stocks_44 else '(not in 44)'
    print('      %s %s %-12s f=%.4f  %s' % (newtag,c,name,v,in44))

# 5. Sector overlap
print('\n  [5] Sector overlap: does new compete with old?')
new_sectors=set(sm.get(c,'') for c in new_codes)
old_sectors=set(sm.get(c,'') for c in stocks_44)
overlap=new_sectors & old_sectors
only_new=new_sectors-old_sectors
print('    Overlapping sectors: %d' % len(overlap))
for s in sorted(overlap):
    n_new=sum(1 for c in new_codes if sm.get(c,'')==s)
    n_old=sum(1 for c in stocks_44 if sm.get(c,'')==s)
    print('      %-30s %d new + %d old = COMPETING' % (s,n_new,n_old))
print('    Brand new sectors: %d (no old stock competition)' % len(only_new))
for s in sorted(only_new):
    n_new=sum(1 for c in new_codes if sm.get(c,'')==s)
    print('      %-30s %d new (exclusive!)' % (s,n_new))

print('\nDone!')
