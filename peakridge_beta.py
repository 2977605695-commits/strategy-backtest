"""Beta / Alpha analysis for Peak Ridge strategy"""
import sys,io,os,math,csv,json
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\home\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma

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

FAC=calc_factor(1.0,21)
def px(c,d,idx):
    di=idx[c].get(d);return stocks[c]['close'][di] if di is not None else None

# Generate strategy daily equity
cash=INIT;pos={};eq=[];daily_returns=[]
idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
for di,dt in enumerate(cd):
    for code,p in list(pos.items()):
        pk=px(code,dt,idx)
        if pk is None: continue
        if pk>p['peak']:p['peak']=pk
        if pk<=p['peak']*(1-0.18):
            sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            del pos[code]
    if di%21==0:
        cand=[(c,FAC.get(c,{}).get(dt,float('nan'))) for c in stocks]
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

strat_rets=[(eq[i]-eq[i-1])/eq[i-1] for i in range(1,len(eq)) if eq[i-1]>0]

# Load benchmarks
benchmarks={}
for fname in ['sh000300.json','sh000905.json','000688_科创50.json']:
    path=os.path.join('benchmarks',fname)
    if os.path.exists(path):
        with open(path,'r',encoding='utf-8') as f:
            bm=json.load(f)
        dates=[b['date'] for b in bm['bars']]
        closes=[b['close'] for b in bm['bars']]
        benchmarks[bm.get('name',bm['code'])]={'dates':dates,'close':closes,'map':dict(zip(dates,closes))}

print('='*80)
print('  BETA / ALPHA ANALYSIS')
print('='*80)

# Align strategy returns with each benchmark
for bm_name,bm in benchmarks.items():
    bm_rets=[]
    strat_aligned=[]
    for i in range(1,len(cd)):
        dt=cd[i];dt_prev=cd[i-1]
        if dt in bm['map'] and dt_prev in bm['map'] and bm['map'][dt_prev]>0:
            bm_rets.append(bm['map'][dt]/bm['map'][dt_prev]-1)
            strat_aligned.append(strat_rets[i-1])
    if len(bm_rets)<100: continue

    n=len(bm_rets)
    # Beta = cov(strat, bm) / var(bm)
    mu_s=sum(strat_aligned)/n;mu_b=sum(bm_rets)/n
    cov=sum((strat_aligned[i]-mu_s)*(bm_rets[i]-mu_b) for i in range(n))/n
    var_b=sum((r-mu_b)**2 for r in bm_rets)/n
    beta=cov/var_b if var_b>0 else 0
    # Alpha (annualized)
    alpha_daily=mu_s-beta*mu_b
    alpha_annual=alpha_daily*TD*100
    # Correlation
    sd_s=(sum((r-mu_s)**2 for r in strat_aligned)/n)**0.5
    sd_b=var_b**0.5
    corr=cov/(sd_s*sd_b) if sd_s>0 and sd_b>0 else 0
    # R-squared
    rsq=corr**2
    # Tracking error
    te=sd_s*TD**0.5*100
    # IR
    ir=alpha_daily/sd_s*TD**0.5 if sd_s>0 else 0
    # Upside/downside capture
    up_months=[];dn_months=[]
    for i in range(n):
        if bm_rets[i]>0: up_months.append((strat_aligned[i],bm_rets[i]))
        else: dn_months.append((strat_aligned[i],bm_rets[i]))
    up_capture=(sum(s for s,_ in up_months)/sum(b for _,b in up_months) if up_months else 0)
    dn_capture=(sum(s for s,_ in dn_months)/sum(b for _,b in dn_months) if dn_months else 0)

    print('\n  --- {} ---'.format(bm_name))
    print('  Beta:           {:.3f}'.format(beta))
    print('  Alpha (annual): {:.1f}%'.format(alpha_annual))
    print('  Correlation:    {:.3f}'.format(corr))
    print('  R-squared:      {:.1%}'.format(rsq))
    print('  Tracking Error: {:.1f}%'.format(te))
    print('  Info Ratio:     {:.2f}'.format(ir))
    print('  Up Capture:     {:.1%}'.format(up_capture))
    print('  Down Capture:   {:.1%}'.format(dn_capture))

# Multi-beta regression: strat ~ b1*HS300 + b2*ZZ500 + b3*KC50
print('\n'+'='*80)
print('  MULTI-FACTOR REGRESSION')
print('='*80)
bm_names=list(benchmarks.keys())
# Align all
common_idx=set(range(len(cd)-1))
for bm_name,bm in benchmarks.items():
    valid=set()
    for i in range(1,len(cd)):
        dt=cd[i];dt_prev=cd[i-1]
        if dt in bm['map'] and dt_prev in bm['map'] and bm['map'][dt_prev]>0:
            valid.add(i-1)
    common_idx&=valid

idx_list=sorted(common_idx)
Y=[strat_rets[i] for i in idx_list]
X={name:[benchmarks[name]['map'][cd[i+1]]/benchmarks[name]['map'][cd[i]]-1 for i in idx_list] for name in bm_names}

# Simple OLS with 3 factors
import numpy as np
X_mat=np.column_stack([[1.0]*len(Y)]+[X[name] for name in bm_names])
Y_arr=np.array(Y)
try:
    beta_hat=np.linalg.lstsq(X_mat,Y_arr,rcond=None)[0]
    alpha_multi=beta_hat[0]*TD*100
    resid=Y_arr-X_mat.dot(beta_hat)
    resid_sd=np.std(resid)
    ir_multi=beta_hat[0]/resid_sd*TD**0.5 if resid_sd>0 else 0
    r2=1-np.var(resid)/np.var(Y_arr)
    print('  Alpha (annual): {:.1f}%'.format(alpha_multi))
    print('  R-squared:      {:.1%}'.format(r2))
    print('  Residual Sharpe:{:.2f}'.format(ir_multi))
    for i,name in enumerate(bm_names):
        print('  Beta_{}:        {:.3f}'.format(name,beta_hat[i+1]))
except: pass

# Yearly beta
print('\n'+'='*80)
print('  YEARLY BETA (vs top benchmark)')
print('='*80)
top_bm=list(benchmarks.keys())[0]  # HS300
bm=benchmarks[top_bm]
for y in ['2021','2022','2023','2024','2025','2026']:
    yr_rets_s=[];yr_rets_b=[]
    for i in range(1,len(cd)):
        dt=cd[i];dt_prev=cd[i-1]
        if dt[:4]!=y: continue
        if dt in bm['map'] and dt_prev in bm['map'] and bm['map'][dt_prev]>0:
            yr_rets_b.append(bm['map'][dt]/bm['map'][dt_prev]-1)
            yr_rets_s.append(strat_rets[i-1])
    if len(yr_rets_s)<30: continue
    mu_s=sum(yr_rets_s)/len(yr_rets_s);mu_b=sum(yr_rets_b)/len(yr_rets_b)
    cov=sum((yr_rets_s[i]-mu_s)*(yr_rets_b[i]-mu_b) for i in range(len(yr_rets_s)))/len(yr_rets_s)
    var_b=sum((r-mu_b)**2 for r in yr_rets_b)/len(yr_rets_b)
    b=cov/var_b if var_b>0 else 0;corr=cov/((sum((r-mu_s)**2 for r in yr_rets_s)/len(yr_rets_s))**0.5*var_b**0.5) if var_b>0 else 0
    excess=(mu_s-b*mu_b)*TD*100
    print('  {}: beta={:.3f} corr={:.3f} alpha={:+.1f}%'.format(y,b,corr,excess))

print('\nDone!')
