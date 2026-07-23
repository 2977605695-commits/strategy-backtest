"""Peak Ridge Robustness Tests #2-7"""
import sys,io,os,math,csv,json,random
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\home\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma

random.seed(42)
INIT=10_000_000;RF=0.025;TD=252
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
FUND_DIR='data/fundamentals_70stocks'
csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm={}
with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()
all_s=load_prices(stock_filter='all64')
stocks={c:i for c,i in all_s.items()}
old_codes={c for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
star_codes=set(stocks.keys())-old_codes
date_counts=defaultdict(int)
for info in stocks.values():
    for d in info['dates']: date_counts[d]+=1
cd_full=sorted(d for d,c in date_counts.items() if c>=30)
cd=[d for d in cd_full if d>='2021-01-01']
all_codes=list(stocks.keys())

print('[DATA] %d stocks, %d days, %d old + %d STAR' % (len(stocks),len(cd),len(old_codes),len(star_codes)))

def calc_factor(K,LB,subset_stocks=None):
    fac={}
    pool=subset_stocks if subset_stocks else stocks
    for code in pool:
        info=stocks[code]
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

def px(c,d,idx):
    di=idx[c].get(d);return stocks[c]['close'][di] if di is not None else None

def backtest_light(factor,cd_period,trail=0.18,surge_d=14,surge_t=0.15,idx=None):
    if idx is None:
        idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    cash=INIT;pos={};eq=[]
    for di,dt in enumerate(cd_period):
        for code,p in list(pos.items()):
            pk=px(code,dt,idx)
            if pk is None: continue
            if pk>p['peak']:p['peak']=pk
            if pk<=p['peak']*(1-trail):
                sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
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
    ld=cd_period[-1]
    for code,p in list(pos.items()):
        pk=px(code,ld,idx)
        if pk is not None:
            sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
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
    return {'sh':sh,'tr':tr*100,'mdd':mdd*100,'nt':0,'n_pos':0}

# Baseline
FAC_full=calc_factor(1.0,21)
r_base=backtest_light(FAC_full,cd)
print('\n  BASELINE: S={:.4f} R={:.1f}% MDD={:.1f}%'.format(r_base['sh'],r_base['tr'],r_base['mdd']))

# ================================================================
# TEST 2: RANDOM STOCK SUBSETS
# ================================================================
print('\n'+'='*80)
print('  TEST 2: RANDOM STOCK SUBSETS (10 runs x 50 stocks each)')
print('='*80)
sub_results=[]
for run in range(10):
    subset=random.sample(all_codes,50)
    fac_sub=calc_factor(1.0,21,subset)
    r=backtest_light(fac_sub,cd)
    if r: sub_results.append(r)
if sub_results:
    ss=[r['sh'] for r in sub_results];ms=[r['mdd'] for r in sub_results]
    print('  Sharpe: avg={:.4f} std={:.4f} min={:.4f} max={:.4f}'.format(
        sum(ss)/len(ss),(sum((s-sum(ss)/len(ss))**2 for s in ss)/len(ss))**0.5,min(ss),max(ss)))
    print('  MDD:    avg={:.1f}% std={:.1f}% min={:.1f}% max={:.1f}%'.format(
        sum(ms)/len(ms),(sum((m-sum(ms)/len(ms))**2 for m in ms)/len(ms))**0.5,min(ms),max(ms)))
    print('  All 10 subsets: positive Sharpe? {}'.format('YES' if min(ss)>0 else 'NO ({}/10 positive)'.format(sum(1 for s in ss if s>0))))

# ================================================================
# TEST 3: PARAMETER PERTURBATION
# ================================================================
print('\n'+'='*80)
print('  TEST 3: PARAMETER PERTURBATION SENSITIVITY')
print('='*80)
print('  Perturbing K and LB around optimal (1.0, 21)')
perturbations=[
    ('OPTIMAL',1.0,21),
    ('K-40%',0.6,21),('K+40%',1.4,21),
    ('LB-33%',1.0,14),('LB+43%',1.0,30),
    ('K-40% LB-33%',0.6,14),('K+40% LB+43%',1.4,30),
]
pert_results=[]
for label,K_,LB_ in perturbations:
    fac=calc_factor(K_,LB_)
    r=backtest_light(fac,cd)
    if r:
        pert_results.append((label,r))
        delta_s=r['sh']-r_base['sh']
        print('  {:<20s}: K={:.1f} LB={:d} S={:.4f} ({:+.4f}) MDD={:.1f}%'.format(label,K_,LB_,r['sh'],delta_s,r['mdd']))
# Range
ps=[r['sh'] for _,r in pert_results]
print('  Sharpe range: {:.4f} ~ {:.4f} (spread={:.4f})'.format(min(ps),max(ps),max(ps)-min(ps)))

# ================================================================
# TEST 4: FACTOR DECAY (yearly Rank IC)
# ================================================================
print('\n'+'='*80)
print('  TEST 4: YEARLY RANK IC DECAY')
print('='*80)
# Compute monthly cross-sectional IC: factor value vs forward 63d return
import numpy as np
fac=FAC_full
ic_by_year=defaultdict(list)
for di in range(63,len(cd)):
    dt=cd[di]
    if di%21!=0: continue
    # Get factor values
    fvals=[]
    rets=[]
    for code in stocks:
        fv=fac.get(code,{}).get(dt)
        pv=px(code,dt,{c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks})
        if fv is None or math.isnan(fv) or pv is None: continue
        # Forward 63d return
        di2=None
        for j in range(di+1,len(cd)):
            if cd[j]>=dt:
                pv2=px(code,cd[j],{c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks})
                if pv2 is not None:
                    di2=j
                if j>=di+63: break
        if di2 is None: continue
        pv2=px(code,cd[di2],{c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks})
        if pv2 is None: continue
        fvals.append(fv);rets.append(pv2/pv-1)
    if len(fvals)<10: continue
    # Spearman rank IC
    n=len(fvals)
    f_rank=sorted(range(n),key=lambda i:fvals[i])
    r_rank=sorted(range(n),key=lambda i:rets[i])
    f_rank_vals=[0]*n;r_rank_vals=[0]*n
    for i,idx in enumerate(f_rank): f_rank_vals[idx]=i+1
    for i,idx in enumerate(r_rank): r_rank_vals[idx]=i+1
    mu_f=sum(f_rank_vals)/n;mu_r=sum(r_rank_vals)/n
    cov=sum((f_rank_vals[i]-mu_f)*(r_rank_vals[i]-mu_r) for i in range(n))
    sf=(sum((v-mu_f)**2 for v in f_rank_vals))**0.5
    sr=(sum((v-mu_r)**2 for v in r_rank_vals))**0.5
    ic=cov/(sf*sr) if sf>0 and sr>0 else 0
    y=dt[:4];ic_by_year[y].append(ic)
print('  Year   N_months   Mean_IC   Std_IC   |IC>0|%')
for y in sorted(ic_by_year.keys()):
    vals=ic_by_year[y];avg=sum(vals)/len(vals);sd=(sum((v-avg)**2 for v in vals)/len(vals))**0.5
    pos=sum(1 for v in vals if v>0)/len(vals)*100
    bar='#'*int(abs(avg)*100)
    print('  {}   {:>8d}   {:>+7.4f}   {:>6.4f}   {:>5.0f}%   {}'.format(y,len(vals),avg,sd,pos,bar))

# ================================================================
# TEST 5: PSEUDO-FACTOR (randomized ranking)
# ================================================================
print('\n'+'='*80)
print('  TEST 5: PSEUDO-FACTOR (random ranking)')
print('='*80)
pseudo_results=[]
for run in range(5):
    # Build a fake factor: random values for each stock on each date
    fake_fac={}
    for code in stocks:
        fake_fac[code]={}
        for dt in cd:
            if code in FAC_full and dt in FAC_full[code]:
                fake_fac[code][dt]=random.uniform(0,5)
    r=backtest_light(fake_fac,cd)
    if r: pseudo_results.append(r)
if pseudo_results:
    ps=[r['sh'] for r in pseudo_results]
    print('  Pseudo-factor Sharpe: avg={:.4f} std={:.4f} min={:.4f} max={:.4f}'.format(
        sum(ps)/len(ps),(sum((s-sum(ps)/len(ps))**2 for s in ps)/len(ps))**0.5,min(ps),max(ps)))
    print('  Real factor Sharpe:   {:.4f}'.format(r_base['sh']))
    print('  Real / Pseudo ratio:  {:.1f}x (should be >2x to confirm edge)'.format(r_base['sh']/(sum(ps)/len(ps)) if sum(ps)/len(ps)>0 else 999))

# ================================================================
# TEST 6: SINGLE-YEAR CROSS VALIDATION
# ================================================================
print('\n'+'='*80)
print('  TEST 6: SINGLE-YEAR CROSS VALIDATION')
print('='*80)
print('  Year   Sharpe   Return%   MDD%')
for y in ['2021','2022','2023','2024','2025','2026']:
    cd_year=[d for d in cd if d[:4]==y]
    if len(cd_year)<50: continue
    r=backtest_light(FAC_full,cd_year)
    if r:
        flag='✅' if r['sh']>0 else '❌'
        print('  {}   {:>7.4f}   {:>6.1f}%   {:>5.1f}%  {}'.format(y,r['sh'],r['tr'],r['mdd'],flag))

# ================================================================
# TEST 7: EXTREME VALUE SENSITIVITY
# ================================================================
print('\n'+'='*80)
print('  TEST 7: EXTREME VALUE SENSITIVITY')
print('='*80)
# Re-run with detailed trades to find top winners
cash=INIT;pos={};eq=[];trades=[]
idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
for di,dt in enumerate(cd):
    for code,p in list(pos.items()):
        pk=px(code,dt,idx)
        if pk is None: continue
        if pk>p['peak']:p['peak']=pk
        if pk<=p['peak']*(1-0.18):
            sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'pnl':p['shares']*(sp-p['bp'])})
            del pos[code]
    if di%21==0:
        cand=[(c,FAC_full.get(c,{}).get(dt,float('nan'))) for c in stocks]
        cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
        filtered=[]
        for c,s in cand:
            di_c=idx[c].get(dt)
            if di_c is None or di_c<14: filtered.append((c,s)); continue
            px_now=stocks[c]['close'][di_c];px_past=stocks[c]['close'][di_c-14]
            if px_past>0 and (px_now-px_past)/px_past>=0.15: continue
            filtered.append((c,s))
        cand=filtered
        cand.sort(key=lambda x:x[1],reverse=True)
        selected=[];sec_counts={}
        for c,s in cand:
            sec=sm.get(c,'');cnt=sec_counts.get(sec,0)
            if cnt>=3: continue
            if len(selected)>=8: break
            selected.append((c,s));sec_counts[sec]=cnt+1
        top_codes=set(c for c,_ in selected)
        for code in list(pos.keys()):
            if code not in top_codes:
                pk=px(code,dt,idx)
                if pk is None: continue
                sp=pk*(1-SLIP-S_FEE-STAX);cash+=pos[code]['shares']*sp
                trades.append({'code':code,'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,'pnl':pos[code]['shares']*(sp-pos[code]['bp'])})
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

trades.sort(key=lambda x:x['ret'],reverse=True)
total_pnl=sum(t['pnl'] for t in trades)
top5_pnl=sum(t['pnl'] for t in trades[:5])
top10_pnl=sum(t['pnl'] for t in trades[:10])
print('  Total trades: {} | Total PnL: {:,.0f}'.format(len(trades),total_pnl))
print('  Top 5 trades: {:,d} PnL ({:.1f}% of total)'.format(top5_pnl,top5_pnl/total_pnl*100))
print('  Top 10 trades: {:,d} PnL ({:.1f}% of total)'.format(top10_pnl,top10_pnl/total_pnl*100))

# Remove top 5 and see what happens
remaining_pnl=sum(t['pnl'] for t in trades[5:])
print('  Without top 5: remaining PnL={:,.0f} ({:.1f}% of total)'.format(remaining_pnl,remaining_pnl/total_pnl*100))
print('  Without top 10: remaining PnL={:,.0f} ({:.1f}% of total)'.format(sum(t['pnl'] for t in trades[10:]),sum(t['pnl'] for t in trades[10:])/total_pnl*100))

# Check: if top 5 didn't exist, would strategy still be profitable?
all_ret=[t['ret'] for t in trades[5:]]
if all_ret:
    avg_r=sum(all_ret)/len(all_ret)*100
    wr=sum(1 for r in all_ret if r>0)/len(all_ret)*100
    print('  Stats without top 5: avg ret={:.1f}% | WR={:.0f}% | trades={:d}'.format(avg_r,wr,len(all_ret)))

# ================================================================
print('\n'+'='*80)
print('  SUMMARY')
print('='*80)
print('  Test 2 (Random subsets): passed' if min(ss)>0 else '  Test 2: FAILED')
print('  Test 3 (Perturbation): spread={:.3f} (should be <.5)'.format(max(ps)-min(ps)))
print('  Test 4 (IC decay): check table above')
pseudo_avg_s=sum(ps)/len(ps) if ps else 0
print('  Test 5 (Pseudo factor): {:.1f}x edge (real S={:.3f} vs pseudo S={:.3f})'.format(r_base['sh']/pseudo_avg_s if pseudo_avg_s>0 else 999,r_base['sh'],pseudo_avg_s))
print('  Test 7 (Extreme): top5={:.1f}% of PnL'.format(top5_pnl/total_pnl*100))
print('Done!')
