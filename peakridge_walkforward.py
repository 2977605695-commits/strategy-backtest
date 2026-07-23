"""Walk-forward / rolling window test for overfitting"""
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
cd_full=sorted(d for d,c in date_counts.items() if c>=30)
print('[DATA] %d stocks, %d total days (%s ~ %s)' % (len(stocks),len(cd_full),cd_full[0],cd_full[-1]))

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

# Pre-compute all factor variants
FAC={}
for K_ in [0.5,1.0,1.5,2.0]:
    for LB_ in [10,14,21,30]:
        if (K_,LB_) not in FAC: FAC[(K_,LB_)]=calc_factor(K_,LB_)
print('[FACTOR] %d variants cached' % len(FAC))

def px(c,d,idx):
    di=idx[c].get(d);return stocks[c]['close'][di] if di is not None else None

def backtest(factor,trail,surge_d,surge_t,cd):
    cash=INIT;pos={};eq=[];t_n=0;wins=0
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    for di,dt in enumerate(cd):
        for code,p in list(pos.items()):
            pk=px(code,dt,idx)
            if pk is None: continue
            if pk>p['peak']:p['peak']=pk
            if pk<=p['peak']*(1-trail):
                sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                t_n+=1
                if sp>p['bp']: wins+=1
                del pos[code]
        if di%21==0:
            cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
            if surge_d>0:
                filtered=[]
                for c,s in cand:
                    di_c=idx[c].get(dt)
                    if di_c is None or di_c<surge_d: filtered.append((c,s)); continue
                    px_now=stocks[c]['close'][di_c];px_past=stocks[c]['close'][di_c-surge_d]
                    if px_past>0 and (px_now-px_past)/px_past>=surge_t: continue
                    filtered.append((c,s))
                cand=filtered
            cand.sort(key=lambda x:x[1],reverse=True)
            selected=[];sec_counts={}
            for c,s in cand:
                sec=sm.get(c,'');cnt=sec_counts.get(sec,0)
                if cnt>=3: continue
                if len(selected)>=8: break
                test_counts=dict(sec_counts);test_counts[sec]=test_counts.get(sec,0)+1
                majority=[ss for ss,cc in test_counts.items() if cc>=2]
                if len(majority)>1: continue
                selected.append((c,s));sec_counts[sec]=cnt+1
            top_codes=set(c for c,_ in selected)
            for code in list(pos.keys()):
                if code not in top_codes:
                    pk=px(code,dt,idx)
                    if pk is None: continue
                    sp=pk*(1-SLIP-S_FEE-STAX);cash+=pos[code]['shares']*sp
                    t_n+=1
                    if sp>pos[code]['bp']: wins+=1
                    del pos[code]
            if not selected: continue
            n_sel=len(selected)
            pv_current=0
            for c,p in pos.items():
                pk=px(c,dt,idx)
                if pk is not None: pv_current+=p['shares']*pk
            total_equity=cash+pv_current
            target_val=total_equity/n_sel
            for code,score in selected:
                raw=px(code,dt,idx)
                if raw is None: continue
                if code in pos:
                    curr_val=pos[code]['shares']*raw
                    diff=target_val-curr_val
                    if diff>0 and cash>0:
                        buy_val=min(diff,cash);bp=raw*(1+SLIP+B_FEE)
                        if bp>0 and buy_val>bp*0.01:
                            sh=buy_val/bp;cash-=buy_val
                            old_sh=pos[code]['shares'];old_cost=pos[code]['bp']*old_sh
                            pos[code]['shares']+=sh;pos[code]['bp']=(old_cost+buy_val)/pos[code]['shares']
                    elif diff<-total_equity/max(n_sel,1)*0.1:
                        sell_val=min(-diff,cash*0.5);sp=raw*(1-SLIP-S_FEE-STAX)
                        if sp>0:
                            sh=sell_val/sp;cash+=sell_val;pos[code]['shares']-=sh
                            if pos[code]['shares']<=0: del pos[code]
                else:
                    buy_val=min(target_val,cash);bp=raw*(1+SLIP+B_FEE)
                    if cash>=buy_val*0.99 and bp>0 and buy_val>bp*0.01:
                        sh=buy_val/bp;cash-=buy_val
                        pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}
        cash*=(1+RF/TD)
        pv=0
        for c,p in pos.items():
            pk=px(c,dt,idx)
            if pk is not None: pv+=p['shares']*pk
        eq.append(cash+pv)
    ld=cd[-1]
    for code,p in list(pos.items()):
        pk=px(code,ld,idx)
        if pk is not None:
            sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp;t_n+=1
            if sp>p['bp']: wins+=1
    if not eq or eq[0]<=0: return None
    tr=(eq[-1]-eq[0])/eq[0]
    rs=[(eq[i]-eq[i-1])/eq[i-1] for i in range(1,len(eq)) if eq[i-1]>0]
    if not rs: return None
    y=len(rs)/TD;cagr=(eq[-1]/eq[0])**(1/y)-1 if y>0 else 0
    mu=sum(rs)/len(rs);sd=(sum((r-mu)**2 for r in rs)/len(rs))**0.5
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk_v=eq[0];mdd=0.0
    for x in eq:
        if x>pk_v:pk_v=x
        dd=(pk_v-x)/pk_v
        if dd>mdd:mdd=dd
    cm=cagr/mdd if mdd>0 else 0
    wr=wins/t_n if t_n else 0
    return {'sh':sh,'tr':tr*100,'cagr':cagr*100,'mdd':mdd*100,'calmar':cm,'nt':t_n,'wr':wr*100}

# ============ WALK-FORWARD ============
# Fixed params: Trail=18%, Pos=8, Sec=3, surge=14d/15%
# Search: K in [0.5,1.0,1.5,2.0], LB in [10,14,21,30]
# Windows: train 2yr, test next period

windows=[
    ('2020H2→2021-22 Train / 2023 Test', '2020-06-01','2022-12-31','2023-01-01','2023-12-31'),
    ('2021-23 Train / 2024 Test',         '2021-01-01','2023-12-31','2024-01-01','2024-12-31'),
    ('2022-24 Train / 2025 Test',         '2022-01-01','2024-12-31','2025-01-01','2025-12-31'),
    ('2023-25 Train / 2026H1 Test',       '2023-01-01','2025-12-31','2026-01-01','2026-07-01'),
]

print('\n'+'='*95)
print('  WALK-FORWARD / ROLLING WINDOW TEST')
print('  Fixed: Trail=18% Pos=8 Sec=3 Surge(15%,14d)')
print('  Searched per window: K x LB -> best Sharpe on train -> test OOS')
print('='*95)

all_train_best=[]
for label,tr_start,tr_end,te_start,te_end in windows:
    cd_train=[d for d in cd_full if tr_start<=d<=tr_end]
    cd_test=[d for d in cd_full if te_start<=d<=te_end]
    print('\n  {}: train={}d test={}d'.format(label,len(cd_train),len(cd_test)))

    # Grid search on train
    best=None
    grid_results=[]
    for K_ in [0.5,1.0,1.5,2.0]:
        for LB_ in [10,14,21,30]:
            r=backtest(FAC[(K_,LB_)],0.18,14,0.15,cd_train)
            if r:
                grid_results.append((K_,LB_,r))
                if best is None or r['sh']>best[2]['sh']:best=(K_,LB_,r)
    if not best: continue

    # Test best on OOS
    Kb,Lb,_=best
    r_test=backtest(FAC[(Kb,Lb)],0.18,14,0.15,cd_test)
    r_full=backtest(FAC[(Kb,Lb)],0.18,14,0.15,cd_train+cd_test)

    # Also test global best (K=1.0 LB=21) on this window
    r_global_test=backtest(FAC[(1.0,21)],0.18,14,0.15,cd_test)

    best_s=best[2]['sh']
    print('  Train best: K={:.1f} LB={:d} S={:.3f} R={:.1f}%'.format(Kb,Lb,best_s,best[2]['tr']))
    print('  Test (window-best): S={:.3f} R={:.1f}% MDD={:.1f}%'.format(r_test['sh'],r_test['tr'],r_test['mdd']))
    print('  Test (global K=1.0 LB=21): S={:.3f} R={:.1f}% MDD={:.1f}%'.format(r_global_test['sh'],r_global_test['tr'],r_global_test['mdd']))

    # Top 3 on train
    grid_results.sort(key=lambda x:x[2]['sh'],reverse=True)
    print('  Top3 on train:')
    for i,(K_,LB_,r) in enumerate(grid_results[:3]):
        r_t=backtest(FAC[(K_,LB_)],0.18,14,0.15,cd_test)
        print('    #{:d}: K={:.1f} LB={:d} Train_S={:.3f} -> Test_S={:.3f}'.format(i+1,K_,LB_,r['sh'],r_t['sh'] if r_t else 0))

    all_train_best.append((label,Kb,Lb,best_s))

# Summary
print('\n'+'='*95)
print('  WALK-FORWARD SUMMARY')
print('='*95)
print('  {:<45s} {:>6s} {:>6s}'.format('Window','K_best','LB_best'))
for label,Kb,Lb,best_s in all_train_best:
    print('  {:<45s} {:>5.1f} {:>6d}'.format(label,Kb,Lb))

# Final: global best tested on ALL sub-periods
print('\n  Global best (K=1.0 LB=21) tested on each sub-period:')
for label,tr_start,tr_end,te_start,te_end in windows:
    cd_period=[d for d in cd_full if te_start<=d<=te_end]
    r=backtest(FAC[(1.0,21)],0.18,14,0.15,cd_period)
    if r:
        print('  {}: S={:.3f} R={:.1f}% MDD={:.1f}% Trd={:d}'.format(
            label.split('/')[1].strip(),r['sh'],r['tr'],r['mdd'],r['nt']))

# Check K=1.0 stability across all windows
print('\n  K=1.0 rank across windows:')
for K_ in [0.5,1.0,1.5,2.0]:
    ranks=[]
    for label,tr_start,tr_end,te_start,te_end in windows:
        cd_train=[d for d in cd_full if tr_start<=d<=tr_end]
        scores=[]
        for LB_ in [10,14,21,30]:
            r=backtest(FAC[(K_,LB_)],0.18,14,0.15,cd_train)
            if r: scores.append(r['sh'])
        if scores: ranks.append(max(scores))
    avg_rk=sum(ranks)/len(ranks) if ranks else 0
    print('  K={:.1f}: avg best train S={:.4f} (across {:d} windows)'.format(K_,avg_rk,len(ranks)))

print('\nDone!')
