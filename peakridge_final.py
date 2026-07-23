"""
峰岭因子策略 · 修正版(FIXED NAV rebalancing) · 全量重跑
=============================================================
修复: 资金管理从固定slot_cap改为NAV-based等权再平衡
重跑: Trail敏感度 / KxLB热力图 / 赛道放宽 / 基准对比
"""
import sys,io,os,math,json
from collections import defaultdict, Counter
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\home\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates
import csv

INIT=10_000_000;RF=0.025;TD=252;MAX_POS=8
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005

FUND_DIR='data/fundamentals_70stocks'
csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm={}
with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()

all_s=load_prices(stock_filter=None)
stocks={c:i for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
cd=get_common_dates(stocks)
print('[DATA] %d stocks, %d days (%.1fyr)' % (len(stocks),len(cd),len(cd)/252))

# ================================================================
# Factor cache (all variants): K x LB x ftype
# ================================================================
def calc_factor(K,LB,ftype):
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
            if ftype=='peak': vals[dates[i]]=ps
            else: vals[dates[i]]=ps/rs if rs>0 else float('nan')
        fac[code]=vals
    return fac

print('[FACTOR] Pre-computing all variants...')
FAC_CACHE={}
for K_ in [0.5,1.0,1.5,2.0,2.5]:
    for LB_ in [10,14,21,30]:
        for FT_ in ['ratio','peak']:
            key=(K_,LB_,FT_)
            FAC_CACHE[key]=calc_factor(K_,LB_,FT_)
            nv=sum(len(v) for v in FAC_CACHE[key].values())
print('  Done. %d factor variants cached.' % len(FAC_CACHE))

# ================================================================
# FIXED backtest: NAV-based equal-weight rebalancing
# ================================================================
def backtest(factor, trail, rebal=21, max_same_sector=1):
    """
    FIXED: positions rebalanced to equal NAV weight every rebal day.
    max_same_sector: 1=strict sector uniqueness, 3=relaxed
    """
    cash=INIT;pos={};eq=[];trades=[]
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}

    for di,dt in enumerate(cd):
        # Trail exits
        for code,p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px=stocks[code]['close'][idx[code][dt]]
            if px>p['peak']:p['peak']=px
            if px<=p['peak']*(1-trail):
                sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'exit':'trail'})
                del pos[code]

        if di%rebal==0:
            # Rank & select
            cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and s >= 0.8 and c in idx and dt in idx[c]]
            cand.sort(key=lambda x:x[1],reverse=True)

            # Sector filter
            selected=[];sec_counts={}
            for c,s in cand:
                sec=sm.get(c,'');cnt=sec_counts.get(sec,0)
                if cnt>=max_same_sector: continue
                if len(selected)>=MAX_POS: break
                # Non-majority constraint: only 1 sector can have >=2
                test_counts=dict(sec_counts);test_counts[sec]=test_counts.get(sec,0)+1
                majority=[ss for ss,cc in test_counts.items() if cc>=2]
                if len(majority)>1: continue
                selected.append((c,s));sec_counts[sec]=cnt+1

            top_codes=set(c for c,_ in selected)

            # Sell positions not in top
            for code in list(pos.keys()):
                if code not in top_codes:
                    px=stocks[code]['close'][idx[code][dt]]
                    sp=px*(1-SLIP-S_FEE-STAX);cash+=pos[code]['shares']*sp
                    trades.append({'code':code,'name':stocks[code]['name'],
                        'bd':pos[code]['bd'],'sd':dt,
                        'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                        'exit':'rebal'})
                    del pos[code]

            # NAV-based equal-weight rebalancing
            if not selected: continue
            n_sel=len(selected)

            # Calculate total equity
            pv=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
            total_equity=cash+pv
            # But we also need to account for positions we just sold (cash already added)
            # Actually the cash already includes sold proceeds, so:
            pv_current=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
            total_equity=cash+pv_current
            target_val=total_equity/n_sel

            # Scale existing positions to target, buy new ones
            for code,score in selected:
                if code in pos:
                    # Adjust existing position
                    curr_val=pos[code]['shares']*stocks[code]['close'][idx[code][dt]]
                    diff=target_val-curr_val
                    raw=stocks[code]['close'][idx[code][dt]]
                    if diff>0 and cash>0:
                        # Buy more
                        buy_val=min(diff,cash)
                        bp=raw*(1+SLIP+B_FEE)
                        if bp>0 and buy_val>bp*0.01:
                            sh=buy_val/bp;cash-=buy_val
                            old_sh=pos[code]['shares'];old_cost=pos[code]['bp']*old_sh
                            pos[code]['shares']+=sh
                            pos[code]['bp']=(old_cost+buy_val)/pos[code]['shares']
                    elif diff<-slot_cap_approx(total_equity,n_sel)*0.1:
                        # Sell some (only if diff is material)
                        sell_val=min(-diff,cash*0.5)
                        sp=raw*(1-SLIP-S_FEE-STAX)
                        if sp>0:
                            sh=sell_val/sp;cash+=sell_val;pos[code]['shares']-=sh
                            if pos[code]['shares']<=0:
                                trades.append({'code':code,'name':stocks[code]['name'],
                                    'bd':pos[code]['bd'],'sd':dt,
                                    'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                                    'exit':'rebal_partial'})
                                del pos[code]
                else:
                    # New position: buy at target value
                    buy_val=min(target_val,cash)
                    raw=stocks[code]['close'][idx[code][dt]]
                    bp=raw*(1+SLIP+B_FEE)
                    if cash>=buy_val*0.99 and bp>0 and buy_val>bp*0.01:
                        sh=buy_val/bp;cash-=buy_val
                        pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}

        cash*=(1+RF/TD)
        pv=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date':dt,'equity':cash+pv,'pos':len(pos)})

    # Final liquidation
    ld=cd[-1]
    for code,p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px=stocks[code]['close'][idx[code][ld]]
            sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'name':stocks[code]['name'],
                'bd':p['bd'],'sd':ld,
                'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'final'})
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

    exits={}
    for t in trades:
        e=t['exit'];exits[e]=exits.get(e,{'cnt':0,'ret':0.0})
        exits[e]['cnt']+=1;exits[e]['ret']+=t['ret']
    for e in exits:
        exits[e]['avg']=exits[e]['ret']/exits[e]['cnt']*100 if exits[e]['cnt'] else 0

    return {'equity':eq,'trades':trades,'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,
        'nt':len(trades),'wr':w/len(trades) if trades else 0,
        'hp':sum(1 for d in eq if d['pos']>0)/len(eq),'exits':exits}

def slot_cap_approx(total_eq,n_pos):
    return total_eq/max(n_pos,1)

def annual(eq):
    yr=defaultdict(lambda:{'s':None,'e':None})
    for d in eq:
        yk=d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s']=d['equity']
        yr[yk]['e']=d['equity']
    return {y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}

# ================================================================
# BENCHMARKS
# ================================================================
def load_index(path):
    with open(path,'r',encoding='utf-8') as f: d=json.load(f)
    dates=[b['date'].replace('-','') for b in d['bars']]
    closes=[b['close'] for b in d['bars']]
    return {'name':d['name'],'code':d['code'],'dates':dates,'close':closes,'map':dict(zip(dates,closes))}

hs300=load_index('benchmarks/sh000300.json')
zz500=load_index('benchmarks/sh000905.json')
bench_dates=set(hs300['map'].keys())&set(zz500['map'].keys())
common_all=sorted(set(cd)&bench_dates)

def bench_stats(idx_data):
    vals=[];first=None
    for dt in common_all:
        px=idx_data['map'].get(dt)
        if px:
            if first is None: first=px
            vals.append(px)
    if not vals or first is None or first<=0: return {}
    final=vals[-1];tr=(final-first)/first
    rets=[(vals[i]-vals[i-1])/vals[i-1] for i in range(1,len(vals)) if vals[i-1]>0]
    y=len(rets)/TD;cagr=(final/first)**(1/y)-1 if y>0 else 0
    mu=sum(rets)/len(rets) if rets else 0
    sd=(sum((r-mu)**2 for r in rets)/len(rets))**0.5 if rets else 0
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk=vals[0];mdd=0.0
    for v in vals:
        if v>pk:pk=v
        dd=(pk-v)/pk
        if dd>mdd:mdd=dd
    cm=cagr/mdd if mdd>0 else float('inf')
    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm}

bs300=bench_stats(hs300)
bs500=bench_stats(zz500)

# ================================================================
# TEST 1: Trail Sensitivity (K=1.5, LB=14, ratio, relaxed sector)
# ================================================================
print('\n'+'='*90)
print('  TEST 1: Trail Sensitivity (FIXED NAV)')
print('  K=1.5 LB=14 ratio | max_same_sector=3 | rebal=21d')
print('='*90)

fac_default=FAC_CACHE[(1.5,14,'ratio')]
trails=[0.10,0.15,0.18,0.20,0.22,0.25,0.28,0.30,0.35]
print('  %-6s %7s %8s %7s %6s %7s %5s %5s' % ('Trail','Sharpe','Ret','CAGR','MDD','Calmar','Trd','Win'))
print('  %s' % ('-'*55))
trail_results={}
for tr in trails:
    r=backtest(fac_default,tr,21,3)
    trail_results[tr]=r
    best=' *' if r['sh']==max(trail_results[t]['sh'] for t in trail_results) else ''
    print('  %5.0f%%  %7.3f %7.1f%% %6.2f%% %5.1f%% %7.3f %4d %4.0f%%%s' % (
        tr*100,r['sh'],r['tr']*100,r['cagr']*100,r['mdd']*100,r['calmar'],r['nt'],r['wr']*100,best))

# ================================================================
# TEST 2: K x LB Heatmap (ratio, trail=best, relaxed sector)
# ================================================================
best_trail=max(trail_results,key=lambda x:trail_results[x]['sh'])

print('\n'+'='*90)
print('  TEST 2: K x LB Heatmap (FIXED NAV, ratio, Trail=%d%%)' % (best_trail*100))
print('='*90)

ks=[0.5,1.0,1.5,2.0,2.5]
lbs=[10,14,21,30]
heat_results={}
print('  %s' % ('K\\LB'+''.join(' %7dd'%l for l in lbs)+'  %7s'%'Avg'))
print('  %s' % ('-'*60))
for k in ks:
    row=[]
    print('  K=%.1f'%k,end='')
    for lb in lbs:
        fac=FAC_CACHE[(k,lb,'ratio')]
        r=backtest(fac,best_trail,21,3)
        heat_results[(k,lb)]=r
        row.append(r['sh'])
        print('  %5.2f'%r['sh'],end='')
    print('  %6.2f'%(sum(row)/len(row)))
# Best
best_heat=max(heat_results,key=lambda x:heat_results[x]['sh'])
br=heat_results[best_heat]
print('\n  Best: K=%.1f LB=%dd -> S=%.3f R=%.1f%% DD=%.1f%% CM=%.3f Trd=%d' % (
    best_heat[0],best_heat[1],br['sh'],br['tr']*100,br['mdd']*100,br['calmar'],br['nt']))

# ================================================================
# TEST 3: Sector constraint (best params)
# ================================================================
best_k,best_lb=best_heat
print('\n'+'='*90)
print('  TEST 3: Sector Constraint (K=%.1f LB=%dd Trail=%d%%)' % (best_k,best_lb,best_trail*100))
print('='*90)

fac_best=FAC_CACHE[(best_k,best_lb,'ratio')]
for max_sec,label in [(1,'Strict(per1)'),(3,'Relaxed(<=3)')]:
    r=backtest(fac_best,best_trail,21,max_sec)
    yr=annual(r['equity'])
    print('  %-15s S=%.3f R=%7.1f%% DD=%5.1f%% CM=%.3f Trd=%d Win=%.0f%%' % (
        label,r['sh'],r['tr']*100,r['mdd']*100,r['calmar'],r['nt'],r['wr']*100))
    print('    Annual: '+' '.join('%s:%+5.1f%%'%(y,yr.get(y,0)) for y in sorted(yr.keys())))

# ================================================================
# TEST 4: FINAL CONFIGS vs Benchmarks
# ================================================================
print('\n'+'='*90)
print('  TEST 4: FINAL CONFIGS vs BENCHMARKS')
print('  K=%.1f LB=%dd Trail=%d%% Relaxed-Sector' % (best_k,best_lb,best_trail*100))
print('='*90)

# Strategy A: Trail=22%
fac_a=FAC_CACHE[(best_k,best_lb,'ratio')]
rA=backtest(fac_a,0.22,21,3)

# Strategy B: Trail=30% (or best)
rB=backtest(fac_best,best_trail,21,3)

print('\n  %-18s %7s %7s %7s %7s %7s %5s %5s' % ('','Sharpe','Ret','CAGR','MDD','Calmar','Trd','Win'))
print('  %s' % ('-'*60))
for label,r in [('Strategy A T=22%',rA),('Strategy B T=%d%%'%(best_trail*100),rB),
                ('沪深300',bs300),('中证500',bs500)]:
    rr=r if isinstance(r,dict) else r
    print('  %-18s %7.3f %6.1f%% %6.2f%% %5.1f%% %7.3f %4d' % (
        label[:18],rr['sh'],rr['tr']*100,rr['cagr']*100,
        rr['mdd']*100,rr['calmar'],rr.get('nt',0)))

# Annual comparison
yrA=annual(rA['equity'])
yrB=annual(rB['equity'])
print('\n  Year-by-Year:')
print('  %-6s %10s %10s %10s %10s' % ('Year','Strat A','Strat B','HS300','ZZ500'))
print('  %s' % ('-'*42))
# Build benchmark annual
for y in sorted(yrA.keys()):
    # Benchmark annual
    hs_s=next((hs300['map'].get(d) for d in common_all if d[:4]==y),None)
    hs_e=next((hs300['map'].get(d) for d in reversed(common_all) if d[:4]==y),None)
    zz_s=next((zz500['map'].get(d) for d in common_all if d[:4]==y),None)
    zz_e=next((zz500['map'].get(d) for d in reversed(common_all) if d[:4]==y),None)
    rh=(hs_e-hs_s)/hs_s*100 if hs_s and hs_e and hs_s>0 else 0
    rz=(zz_e-zz_s)/zz_s*100 if zz_s and zz_e and zz_s>0 else 0
    print('  %-6s %+9.1f%% %+9.1f%% %+9.1f%% %+9.1f%%' % (y,yrA.get(y,0),yrB.get(y,0),rh,rz))

# Excess return
print('\n  Excess Return vs HS300:')
for label,r,eq_curve in [('Strat A',rA,rA['equity']),('Strat B',rB,rB['equity'])]:
    eq_map={d['date']:d['equity'] for d in eq_curve}
    excess=[]
    for i in range(1,len(common_all)):
        d=common_all[i];dp=common_all[i-1]
        sv=eq_map.get(dp);ev=eq_map.get(d)
        bv=hs300['map'].get(dp);bve=hs300['map'].get(d)
        if sv and ev and bv and bve and sv>0 and bv>0:
            excess.append((ev/sv-1)-(bve/bv-1))
    if excess:
        mu=sum(excess)/len(excess);sd=(sum((r-mu)**2 for r in excess)/len(excess))**0.5
        ir=mu/sd*(TD**0.5) if sd>0 else 0;alpha=mu*TD*100
        print('  %-18s IR=%.3f Alpha=%.1f%%/yr TE=%.1f%%' % (label,ir,alpha,sd*(TD**0.5)*100))

# ================================================================
# BEST CONFIG DETAIL
# ================================================================
print('\n'+'='*90)
print('  FINAL BEST CONFIG: K=%.1f LB=%dd Trail=%d%% Relaxed-Sector' % (best_k,best_lb,best_trail*100))
print('='*90)

s=rB
print('  Sharpe:     %.4f' % s['sh'])
print('  Total Ret:  %.2f%%' % (s['tr']*100))
print('  CAGR:       %.2f%%' % (s['cagr']*100))
print('  Max DD:     %.2f%%' % (s['mdd']*100))
print('  Calmar:     %.3f' % s['calmar'])
print('  Trades:     %d' % s['nt'])
print('  Win Rate:   %.1f%%' % (s['wr']*100))
print('  Hold%%:      %.1f%%' % (s['hp']*100))
print('  Exits:')
for e,d in sorted(s['exits'].items()):
    print('    %-15s %3d trades  avg=%.1f%%' % (e,d['cnt'],d['avg']))

# Top/Bottom trades
ts=sorted(rB['trades'],key=lambda x:x['ret'],reverse=True)
for tag,subset in [('Best 5',ts[:5]),('Worst 5',ts[-5:])]:
    print('\n  %s:'%tag)
    print('  %-12s %-12s %-12s %8s %5s %s' % ('Stock','Buy','Sell','Ret','Hold','Exit'))
    print('  %s'%('-'*55))
    for t in subset:
        print('  %-12s %-12s %-12s %7.1f%% %4dd %s' % (
            t['name'],t['bd'],t['sd'],t['ret']*100,t.get('hold',t.get('hold_days',0)),t['exit']))

# Sector exposure
sp={}
for t in rB['trades']:
    sec=sm.get(t['code'],'?')
    sp[sec]=sp.get(sec,{'cnt':0,'ret':0.0})
    sp[sec]['cnt']+=1;sp[sec]['ret']+=t['ret']
print('\n  Sector Performance (>=3 trades):')
print('  %-30s %5s %8s' % ('Sector','Trd','AvgRet'))
print('  %s'%('-'*45))
for sec in sorted(sp,key=lambda x:sp[x]['ret']/sp[x]['cnt'],reverse=True):
    d=sp[sec]
    if d['cnt']>=3:
        print('  %-30s %4d %7.1f%%' % (sec,d['cnt'],d['ret']/d['cnt']*100))

# ================================================================
# EXPORT
# ================================================================
base=os.path.dirname(os.path.abspath(__file__))
for label,r,fn in [
    ('StratA_T22',rA,'peakridge_fixed_A_equity.csv'),
    ('StratB_Best',rB,'peakridge_fixed_B_equity.csv'),
]:
    with open(os.path.join(base,fn),'w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f);w.writerow(['date','equity','positions'])
        for d in r['equity']: w.writerow([d['date'],'%.2f'%d['equity'],d['pos']])
    print('\n  Exported: %s' % fn)

# Final summary
print('\n'+'='*90)
print('  SUMMARY')
print('='*90)
print('  Best Params:  K=%.1f | LB=%dd | Trail=%d%% | Sector=%s | Rebal=21d | Top5 | Equal-NAV' % (
    best_k,best_lb,best_trail*100,'relaxed'))
print('  Best Sharpe:  %.3f' % best_heat[1])
bhr=heat_results[best_heat]
print('  Best Return:  %.1f%%' % (bhr['tr']*100))
print('  Best MDD:     %.1f%%' % (bhr['mdd']*100))
print('  Best Calmar:  %.2f' % bhr['calmar'])
print('  vs HS300 IR:  see above')
print('  Fixed: NAV-based equal-weight rebalancing (not fixed slot_cap)')
print('='*90)
print('\nDone!')
