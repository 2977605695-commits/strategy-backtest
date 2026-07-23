"""峰岭因子 · 64池全量网格搜索（2160组合）"""
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
old_codes={c for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
star_codes=set(stocks.keys())-old_codes
date_counts=defaultdict(int)
for info in stocks.values():
    for d in info['dates']: date_counts[d]+=1
cd_full=sorted(d for d,c in date_counts.items() if c>=30)
cd=[d for d in cd_full if d>='2021-01-01']
print('[DATA] %d stocks (%d old + %d STAR), %d days (%s ~ %s)' % (
    len(stocks),len(old_codes),len(star_codes),len(cd),cd[0],cd[-1]))

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

print('[FACTOR] Pre-computing 20 variants...')
FAC={}
for K_ in [0.5,1.0,1.5,2.0,2.5]:
    for LB_ in [10,14,21,30]:
        FAC[(K_,LB_)]=calc_factor(K_,LB_)
print('  Done.')

def backtest(factor,trail,rebal,max_same_sector,max_pos,factor_floor):
    cash=INIT;pos={};trades=[]
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    def px(c,d):
        di=idx[c].get(d);return stocks[c]['close'][di] if di is not None else None
    for di,dt in enumerate(cd):
        for code,p in list(pos.items()):
            pk=px(code,dt)
            if pk is None: continue
            if pk>p['peak']:p['peak']=pk
            if pk<=p['peak']*(1-trail):
                sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'trail'})
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
                    trades.append({'code':code,'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,'exit':'rebal'})
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
    ld=cd[-1]
    for code,p in list(pos.items()):
        pk=px(code,ld)
        if pk is not None:
            sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'final'})
    # Stats
    eq_vals=[]
    pv=INIT
    for di,dt in enumerate(cd):
        pv=INIT
        # simplified stats
    # Use trade-based stats
    if not trades: return None
    # Compute returns from trade PnL
    v=[INIT]
    # Simplified: track via cash+trade tracking
    all_ret=[]
    for t in trades:
        if t['exit']!='final':
            all_ret.append(t['ret'])
    if not all_ret: return None
    # Use CAGR approximation
    total_pnl=sum(t['ret'] for t in trades)
    tr=total_pnl  # crude
    # Better: rebuild equity from daily equity tracking
    # Re-run lightweight version without trades dict for speed
    return None  # placeholder - need equity tracking

# Actually re-use the simpler approach
def backtest_fast(factor,trail,rebal,max_same_sector,max_pos,factor_floor):
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
    return {'sh':sh,'tr':tr*100,'cagr':cagr*100,'mdd':mdd*100,'calmar':cm,'nt':trades_n,'wr':wr*100}

# Grid
results=[]
total=5*4*9*3*2*2;cnt=0
print('\n[GRID] %d combos...' % total)
for K_ in [0.5,1.0,1.5,2.0,2.5]:
    for LB_ in [10,14,21,30]:
        fac=FAC[(K_,LB_)]
        for tr_ in [0.10,0.15,0.18,0.20,0.22,0.25,0.28,0.30,0.35]:
            for mp_ in [3,5,8]:
                for sec_ in [1,3]:
                    for floor_ in [0,0.8]:
                        cnt+=1
                        r=backtest_fast(fac,tr_,21,sec_,mp_,floor_)
                        if r:
                            results.append({'K':K_,'LB':LB_,'Trail':tr_,'Pos':mp_,'Sec':sec_,'Floor':floor_,'S':r['sh'],'R':r['tr'],'CAGR':r['cagr'],'MDD':r['mdd'],'CM':r['calmar'],'Trd':r['nt'],'Win':r['wr']})
                        if cnt%400==0: print('  [%d/%d] %.0f%%'%(cnt,total,cnt/total*100))

print('\n  Done. %d valid results.' % len(results))
results.sort(key=lambda x:x['S'],reverse=True)

# TOP 30
print('\n'+'='*90)
print('  64-pool FULL GRID  TOP 30 BY SHARPE')
print('='*90)
print('  %4s %4s %4s %5s %4s %4s %5s %7s %7s %5s %6s %5s %4s %4s' % (
    'Rank','K','LB','Trail','Pos','Sec','Floor','Sharpe','Ret%','CAGR%','MDD%','Calmar','Win%','Trd'))
print('  %s' % ('-'*88))
for i,r in enumerate(results[:30]):
    print('  %4d %4.1f %4d %4.0f%% %4d %4d %5.1f %7.4f %7.1f %5.1f %5.1f %6.3f %4.0f%% %4d' % (
        i+1,r['K'],r['LB'],r['Trail']*100,r['Pos'],r['Sec'],r['Floor'],r['S'],r['R'],r['CAGR'],r['MDD'],r['CM'],r['Win'],r['Trd']))

# Best by metric
print('\n'+'='*90)
print('  BEST BY METRIC')
print('='*90)
for metric,label in [('S','Sharpe'),('CM','Calmar'),('R','Ret%'),('Win','Win%')]:
    if metric=='MDD':
        best=min(results,key=lambda x:x[metric])
    else:
        best=max(results,key=lambda x:x[metric])
    print('  %s: K=%.1f LB=%d Trail=%d%% Pos=%d Sec=%d Floor=%.1f S=%.4f R=%.1f%% MDD=%.1f%% CM=%.3f' % (
        label,best['K'],best['LB'],best['Trail']*100,best['Pos'],best['Sec'],best['Floor'],best['S'],best['R'],best['MDD'],best['CM']))

# Param importance
print('\n'+'='*90)
print('  PARAMETER IMPORTANCE (avg Sharpe)')
print('='*90)
for param in ['K','LB','Trail','Pos','Sec','Floor']:
    groups=defaultdict(list)
    for r in results:
        if param=='Trail': groups[int(r[param]*100)].append(r['S'])
        else: groups[r[param]].append(r['S'])
    avgs={k:sum(v)/len(v) for k,v in groups.items()}
    best_k=max(avgs,key=avgs.get)
    print('  %s:' % param, ' | '.join('%s:%.4f%s'%(k,avgs[k],'*' if k==best_k else '') for k in sorted(avgs.keys())))

# Global best detail
best=results[0]
print('\n'+'='*90)
print('  GLOBAL BEST: K=%.1f LB=%d Trail=%d%% Pos=%d Sec=%d Floor=%.1f' % (
    best['K'],best['LB'],best['Trail']*100,best['Pos'],best['Sec'],best['Floor']))
print('  Sharpe=%.4f | Ret=%.1f%% | CAGR=%.1f%% | MDD=%.1f%% | Calmar=%.3f | Trd=%d | Win=%.0f%%' % (
    best['S'],best['R'],best['CAGR'],best['MDD'],best['CM'],best['Trd'],best['Win']))

# Compare vs 44-pool best on same pool
r_old=backtest_fast(FAC[(1.5,14)],0.30,21,1,5,0.8)
if r_old:
    print('\n  44-pool BEST on 64-pool: S=%.4f R=%.1f%% DD=%.1f%% -> 64-pool BEST: S=%.4f R=%.1f%% DD=%.1f%%' % (
        r_old['sh'],r_old['tr'],r_old['mdd'],best['S'],best['R'],best['MDD']))
    print('  Improvement: Sharpe +%.2f, Return +%.0f%%, MDD %.0f%% -> %.0f%%' % (
        best['S']-r_old['sh'],best['R']-r_old['tr'],r_old['mdd'],best['MDD']))

# Save
with open('peakridge_64_fullgrid_results.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=results[0].keys())
    w.writeheader();w.writerows(results)
print('\n  Saved peakridge_64_fullgrid_results.csv (%d rows)' % len(results))
print('Done!')
