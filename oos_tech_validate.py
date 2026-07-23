"""OOS过拟合验证: 44训练池 vs 100只新科技/AI股"""
import sys,io,os,math,json
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import calc_ma

INIT=10_000_000;RF=0.025;TD=252
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
K=1.5;LB=14;TRAIL=0.22;REBAL=21;MIN_F=0.8;NO_CHASE=0.10

def load_stocks(dir_path):
    stocks={}
    for fname in sorted(os.listdir(dir_path)):
        if not fname.endswith('.json') or fname.startswith('_'): continue
        with open(os.path.join(dir_path,fname),encoding='utf-8') as f:
            d=json.load(f)
        stocks[d['code']]={
            'name':d['name'],
            'dates':[b['date'] for b in d['bars']],
            'close':[b['close'] for b in d['bars']],
            'volume':[b['volume'] for b in d['bars']]}
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

def backtest(stocks,factor,dates):
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
            filtered=[]
            for c,s in cand:
                si=idx[c].get(dt)
                if si is not None and si>=5:
                    px_now=stocks[c]['close'][si];px_5d=stocks[c]['close'][si-5]
                    if px_5d>0 and (px_now-px_5d)/px_5d>NO_CHASE: continue
                filtered.append((c,s))
            cand=filtered
            cand.sort(key=lambda x:x[1],reverse=True)
            selected=cand[:5];n_select=len(selected);top=set(c for c,_ in selected)
            for code in list(pos.keys()):
                if code not in top:
                    if code in idx and dt in idx[code]:
                        px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                        cash+=pos[code]['shares']*sp
                        trades.append({'code':code,'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,'exit':'rebal'})
                        del pos[code]
            pv2=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
            nav=cash+pv2;target=nav/n_select if n_select>0 else 0
            for code,score in selected:
                if code in pos: continue
                if code not in idx or dt not in idx[code]: continue
                if cash<target*0.99: break
                bv=min(target,cash)
                if bv<=0: break
                raw=stocks[code]['close'][idx[code][dt]];bp=raw*(1+SLIP+B_FEE)
                if bp>0 and bv>bp*0.01:
                    sh=bv/bp;cash-=bv
                    pos[code]={'shares':sh,'bp':bp,'peak':raw}
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

def annual(eq,dates):
    yr=defaultdict(lambda:{'s':None,'e':None})
    for i,d in enumerate(eq):
        yk=dates[i][:4]
        if yr[yk]['s'] is None: yr[yk]['s']=d
        yr[yk]['e']=d
    return {y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}

# Load
print('[LOAD] Training pool (44 tech)...')
s_train=load_stocks('data')
s_train={c:i for c,i in s_train.items() if i['dates'][0]<='20200103' and len(i['dates'])>=1500}
cd_train=get_common_dates(s_train)
print('  %d stocks, %d days' % (len(s_train),len(cd_train)))

print('[LOAD] OOS tech pool (100 new)...')
s_oos=load_stocks('data_oos_tech')
cd_oos=get_common_dates(s_oos)
print('  %d stocks, %d days' % (len(s_oos),len(cd_oos)))

# Majority dates
START='20240121'
all_dates=set()
for s in s_oos.values(): all_dates.update(s['dates'])
majority_dates=sorted([d for d in all_dates if sum(1 for s in s_oos.values() if d in s['dates'])>=80 and d in cd_train and d>=START])
common_dates=majority_dates
print('\n  Majority (>=80 stocks): %d days (%.1fyr)' % (len(common_dates),len(common_dates)/252))

# Factors
print('\n[FACTOR] Computing...')
f_train=calc_factor(s_train)
f_oos=calc_factor(s_oos)
print('  Train: %d vals | OOS: %d vals' % (
    sum(len(v) for v in f_train.values()),
    sum(len(v) for v in f_oos.values())))

# Backtest
print('\n[BACKTEST]')
r_train=backtest(s_train,f_train,common_dates)
r_oos=backtest(s_oos,f_oos,common_dates)

print('\n'+'='*95)
print('  OOS Validation: 44训练池 vs 100新科技/AI股')
print('  Params: K=%.1f LB=%dd Trail=%d%% NC=%d%% min_f=%.1f Rebal=%dd' % (
    K,LB,TRAIL*100,NO_CHASE*100,MIN_F,REBAL))
print('='*95)

print('\n  %-25s %7s %8s %6s %6s %6s %5s %5s %6s' % (
    'Pool','Sharpe','Ret','CAGR','MDD','Calmar','Trd','Win','Loss'))
print('  '+'-'*78)
for label,r in [
    ('44-Train (in-sample)',r_train),
    ('100-OOS Tech (out-sample)',r_oos),
]:
    print('  %-25s %7.3f %7.1f%% %5.2f%% %5.1f%% %6.3f %4d %4.0f%% %5.0f%%' % (
        label[:25],r['sh'],r['tr']*100,r['cagr']*100,r['mdd']*100,r['calmar'],
        r['nt'],r['wr']*100,r['loss_rate']))

# Degradation
sh_ratio=r_oos['sh']/r_train['sh'] if r_train['sh']>0 else 0
print('\n  --- Degradation ---')
print('  Sharpe ratio (OOS/Train): %.2f' % sh_ratio)
if sh_ratio>0.8:
    print('  VERDICT: Excellent generalization - strategy transfers well to new tech stocks')
elif sh_ratio>0.5:
    print('  VERDICT: Moderate generalization - works but with some degradation')
else:
    print('  VERDICT: Poor generalization')

# Annual
ar_train=annual(r_train['eq'],common_dates)
ar_oos=annual(r_oos['eq'],common_dates)
print('\n  --- Annual ---')
print('  %-6s %12s %12s' % ('Year','44-Train','100-OOS'))
for y in ['2024','2025','2026']:
    rt=ar_train.get(y,0);ro=ar_oos.get(y,0)
    print('  %-6s %+10.1f%% %+10.1f%%' % (y,rt,ro))

# Top OOS performers
per_stock={}
for code in s_oos:
    sub_s={code:s_oos[code]};sub_f={code:f_oos[code]}
    sub_dates=[d for d in common_dates if d in sub_s[code]['dates']]
    if len(sub_dates)<50: continue
    r=backtest(sub_s,sub_f,sub_dates)
    per_stock[code]=(r['tr']*100,r['sh'])

print('\n  --- Top 10 OOS Stocks ---')
for code,(ret,sh) in sorted(per_stock.items(),key=lambda x:x[1][1],reverse=True)[:10]:
    print('  %s %-12s R=%+.1f%% S=%.2f' % (code,s_oos[code]['name'],ret,sh))

print('\nDone!')
