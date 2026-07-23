"""MDD reduction sweep for stable peakridge config"""
import sys,io,os,math,csv,json
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\home\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates

INIT=10_000_000;RF=0.025;TD=252
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
FUND_DIR='data/fundamentals_70stocks'
csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm={}
with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()
all_s=load_prices(stock_filter='all64')
stocks={c:i for c,i in all_s.items()}
date_counts=defaultdict(int)
for info in stocks.values():
    for d in info['dates']: date_counts[d]+=1
cd=[d for d in sorted(d for d,c in date_counts.items() if c>=30) if d>='2021-01-01']

def calc_factor(K,LB):
    fac={}
    for code,info in stocks.items():
        vols=info['volume'];dates=info['dates'];n=len(vols)
        ma_vol=calc_ma(vols,max(LB,20))
        vals={}
        for i in range(n):
            if i<LB or math.isnan(ma_vol[i]): continue
            wl=min(20,i+1);w=vols[i-wl+1:i+1]
            mu=sum(w)/wl;var=sum((v-mu)**2 for v in w)/wl;std=var**0.5
            thr=ma_vol[i]+K*std
            ps=0.0;rs=0.0
            for j in range(max(0,i-LB+1),i+1):
                erupt=vols[j]>=thr
                if erupt:
                    prev=(j>0 and vols[j-1]>=thr)
                    if prev: rs+=vols[j]
                    else: ps+=vols[j]
            vals[dates[i]]=ps/rs if rs>0 else float('nan')
        fac[code]=vals
    return fac

FAC={}
for K_ in [1.0,1.5,2.0]:
    for LB_ in [10,14,21,30]:
        if (K_,LB_) not in FAC: FAC[(K_,LB_)]=calc_factor(K_,LB_)

def backtest(factor,trail,rebal,max_same_sector,max_pos,factor_floor):
    cash=INIT;pos={};eq=[]
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    def px(c,d):
        di=idx[c].get(d);return stocks[c]['close'][di] if di is not None else None
    trades_n=0;wins=0
    for di,dt in enumerate(cd):
        for code,p in list(pos.items()):
            pk=px(code,dt)
            if pk is None: continue
            if pk>p['peak']:p['peak']=pk
            if pk<=p['peak']*(1-trail):
                sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades_n+=1
                if sp>p['bp']: wins+=1
                del pos[code]
        if di%rebal==0:
            cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and s>=factor_floor and c in idx and dt in idx[c]]
            cand.sort(key=lambda x:x[1],reverse=True)
            selected=[];sec_counts={}
            for c,s in cand:
                sec=sm.get(c,'');cnt=sec_counts.get(sec,0)
                if cnt>=max_same_sector: continue
                if len(selected)>=max_pos: break
                test_counts=dict(sec_counts);test_counts[sec]=test_counts.get(sec,0)+1
                majority=[ss for ss,cc in test_counts.items() if cc>=2]
                if len(majority)>1: continue
                selected.append((c,s));sec_counts[sec]=cnt+1
            top_codes=set(c for c,_ in selected)
            for code in list(pos.keys()):
                if code not in top_codes:
                    pk=px(code,dt)
                    if pk is None: continue
                    sp=pk*(1-SLIP-S_FEE-STAX);cash+=pos[code]['shares']*sp
                    trades_n+=1
                    if sp>pos[code]['bp']: wins+=1
                    del pos[code]
            if not selected: continue
            n_sel=len(selected)
            pv_current=0
            for c,p in pos.items():
                pk=px(c,dt)
                if pk is not None: pv_current+=p['shares']*pk
            total_equity=cash+pv_current
            target_val=total_equity/n_sel
            for code,score in selected:
                raw=px(code,dt)
                if raw is None: continue
                if code in pos:
                    curr_val=pos[code]['shares']*raw
                    diff=target_val-curr_val
                    if diff>0 and cash>0:
                        buy_val=min(diff,cash)
                        bp=raw*(1+SLIP+B_FEE)
                        if bp>0 and buy_val>bp*0.01:
                            sh=buy_val/bp;cash-=buy_val
                            old_sh=pos[code]['shares'];old_cost=pos[code]['bp']*old_sh
                            pos[code]['shares']+=sh;pos[code]['bp']=(old_cost+buy_val)/pos[code]['shares']
                    elif diff<-total_equity/max(n_sel,1)*0.1:
                        sell_val=min(-diff,cash*0.5)
                        sp=raw*(1-SLIP-S_FEE-STAX)
                        if sp>0:
                            sh=sell_val/sp;cash+=sell_val;pos[code]['shares']-=sh
                            if pos[code]['shares']<=0: del pos[code]
                else:
                    buy_val=min(target_val,cash)
                    bp=raw*(1+SLIP+B_FEE)
                    if cash>=buy_val*0.99 and bp>0 and buy_val>bp*0.01:
                        sh=buy_val/bp;cash-=buy_val
                        pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}
        cash*=(1+RF/TD)
        pv=0
        for c,p in pos.items():
            pk=px(c,dt)
            if pk is not None: pv+=p['shares']*pk
        eq.append(cash+pv)
    ld=cd[-1]
    for code,p in list(pos.items()):
        pk=px(code,ld)
        if pk is not None:
            sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades_n+=1
            if sp>p['bp']: wins+=1
    if not eq or eq[0]<=0: return None
    tr=(eq[-1]-eq[0])/eq[0]
    rs=[(eq[i]-eq[i-1])/eq[i-1] for i in range(1,len(eq)) if eq[i-1]>0]
    if not rs: return None
    y=len(rs)/TD;cagr=(eq[-1]/eq[0])**(1/y)-1 if y>0 else 0
    mu=sum(rs)/len(rs)
    sd=(sum((r-mu)**2 for r in rs)/len(rs))**0.5
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk_v=eq[0];mdd=0.0
    for x in eq:
        if x>pk_v:pk_v=x
        dd=(pk_v-x)/pk_v
        if dd>mdd:mdd=dd
    cm=cagr/mdd if mdd>0 else float('inf')
    wr=wins/trades_n if trades_n else 0
    yr=defaultdict(lambda:{'s':None,'e':None})
    for i,d in enumerate(cd):
        yk=d[:4];ev=eq[i]
        if yr[yk]['s'] is None: yr[yk]['s']=ev
        yr[yk]['e']=ev
    ann={y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}
    neg=sum(1 for a in ann.values() if a<0)
    return {'sh':sh,'tr':tr*100,'cagr':cagr*100,'mdd':mdd*100,'calmar':cm,'nt':trades_n,'wr':wr*100,'neg':neg,'ann':ann}

# BASELINE
r_base=backtest(FAC[(1.0,21)],0.18,21,3,8,0.0)
print('BASELINE: K=1.0 LB=21 Trail=18% Pos=8 Sec=relaxed Floor=0')
print('  S=%.4f R=%.1f%% MDD=%.1f%% CM=%.3f Trd=%d Win=%.0f%% Neg=%d' % (
    r_base['sh'],r_base['tr'],r_base['mdd'],r_base['calmar'],r_base['nt'],r_base['wr'],r_base['neg']))

print('\n'+'='*75)
print('  MDD REDUCTION TESTS')
print('='*75)
print('  %-35s %7s %7s %6s %6s %5s %4s' % ('Change','Sharpe','Ret%','MDD%','Calmar','Win%','Neg'))
print('  %s' % ('-'*65))
print('  %-35s %7.4f %7.1f %5.1f %6.3f %4.0f%% %4d  [BASELINE]' % (
    'BASELINE',r_base['sh'],r_base['tr'],r_base['mdd'],r_base['calmar'],r_base['wr'],r_base['neg']))

trials=[]

# Single changes
tests=[
    ('Floor=0.8',1.0,21,0.18,3,8,0.8),
    ('Floor=1.0',1.0,21,0.18,3,8,1.0),
    ('Floor=1.2',1.0,21,0.18,3,8,1.2),
    ('Strict sector',1.0,21,0.18,1,8,0.0),
    ('K=1.5',1.5,21,0.18,3,8,0.0),
    ('K=2.0',2.0,21,0.18,3,8,0.0),
    ('LB=10',1.0,10,0.18,3,8,0.0),
    ('LB=14',1.0,14,0.18,3,8,0.0),
    ('Trail=12%',1.0,21,0.12,3,8,0.0),
    ('Trail=15%',1.0,21,0.15,3,8,0.0),
    ('Pos=5',1.0,21,0.18,3,5,0.0),
    ('Pos=3',1.0,21,0.18,3,3,0.0),
]
for label,K_,LB_,tr_,sec_,mp_,fl_ in tests:
    r=backtest(FAC[(K_,LB_)],tr_,21,sec_,mp_,fl_)
    if r:
        trials.append((label,r))
        delta=r['mdd']-r_base['mdd']
        print('  %-35s %7.4f %7.1f %5.1f %6.3f %4.0f%% %4d  [MDD %+.1f%%]' % (
            label,r['sh'],r['tr'],r['mdd'],r['calmar'],r['wr'],r['neg'],delta))

# Combinations
combos=[
    ('Floor=0.8 + Strict + K=1.5',1.5,21,0.18,1,8,0.8),
    ('Floor=1.0 + Strict + K=1.5',1.5,21,0.18,1,8,1.0),
    ('Floor=0.8 + K=2.0 + Strict + LB=14',2.0,14,0.18,1,8,0.8),
    ('K=2.0 + Floor=0.8 + Strict + Pos=5',2.0,21,0.18,1,5,0.8),
    ('K=2.0 + Floor=0.8 + Strict + LB=14 + Pos=5',2.0,14,0.18,1,5,0.8),
    ('K=1.5 + Floor=1.2 + Strict',1.5,21,0.18,1,8,1.2),
]
print('\n  --- Combinations ---')
for label,K_,LB_,tr_,sec_,mp_,fl_ in combos:
    r=backtest(FAC[(K_,LB_)],tr_,21,sec_,mp_,fl_)
    if r:
        trials.append((label,r))
        delta=r['mdd']-r_base['mdd']
        print('  %-35s %7.4f %7.1f %5.1f %6.3f %4.0f%% %4d  [MDD %+.1f%%]' % (
            label,r['sh'],r['tr'],r['mdd'],r['calmar'],r['wr'],r['neg'],delta))

# Summary
print('\n'+'='*75)
print('  BEST MDD REDUCTION (sorted by MDD)')
print('='*75)
sorted_trials=sorted(trials,key=lambda x:x[1]['mdd'])
for i,(label,r) in enumerate(sorted_trials[:8]):
    mdd_cut=(r_base['mdd']-r['mdd'])/r_base['mdd']*100
    print('  %d. %-35s S=%.4f R=%.1f%% MDD=%.1f%% (%.0f%% less) CM=%.3f' % (
        i+1,label,r['sh'],r['tr'],r['mdd'],mdd_cut,r['calmar']))

print('\nDone!')
