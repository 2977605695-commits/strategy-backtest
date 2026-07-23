"""峰岭因子 · 64只全池网格搜索（含科创板，缺失值自动跳过）"""
import sys,io,os,math,csv,json
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.chdir(r'C:\Users\home\Desktop\strategy-backtest')
from data_loader import load_prices,calc_ma,get_common_dates

INIT=10_000_000;RF=0.025;TD=252
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005

# Load all 64 stocks (old + STAR market)
FUND_DIR='data/fundamentals_70stocks'
csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm={}
with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()

# ALL stocks — use the full 64-pool (same as 进取版)
all_s=load_prices(stock_filter='all64')
stocks={c:i for c,i in all_s.items()}
# Build date range: union of all stock dates, from earliest common to latest
all_dates=set()
for info in stocks.values(): all_dates.update(info['dates'])
# Use the 44 old stocks' common dates as baseline, then extend with all dates
old_s={c:i for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
cd_base=get_common_dates(old_s)
# Full date range: all dates that appear in at least 30 stocks
date_counts=defaultdict(int)
for info in stocks.values():
    for d in info['dates']: date_counts[d]+=1
cd_full=sorted(d for d,c in date_counts.items() if c>=30)
print('[DATA] %d stocks (full pool with STAR), %d baseline days, %d full days (%s ~ %s)' % (
    len(stocks),len(cd_base),len(cd_full),cd_full[0],cd_full[-1]))

# Full period
cd_all=[d for d in cd_full if d>='2021-01-01']
# OOS period
cd_oos=[d for d in cd_full if d>='2024-01-01']

# ============ Factor cache ============
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

print('[FACTOR] Pre-computing 20 variants for full pool...')
FAC={}
for K_ in [0.5,1.0,1.5,2.0,2.5]:
    for LB_ in [10,14,21,30]:
        FAC[(K_,LB_)]=calc_factor(K_,LB_)
print('  Done.')

# ============ Backtest ============
def backtest(factor,trail,rebal,max_same_sector,max_pos,factor_floor,cd):
    cash=INIT;pos={};eq=[];trades=[]
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    def px(c,d):
        """Safe price lookup, returns None if date missing"""
        di=idx[c].get(d)
        return stocks[c]['close'][di] if di is not None else None
    for di,dt in enumerate(cd):
        for code,p in list(pos.items()):
            pk=px(code,dt)
            if pk is None: continue
            if pk>p['peak']:p['peak']=pk
            if pk<=p['peak']*(1-trail):
                sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'code':code,'bd':p['bd'],'sd':dt,
                    'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'trail'})
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
                    trades.append({'code':code,'bd':pos[code]['bd'],'sd':dt,
                        'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,'exit':'rebal'})
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
                            pos[code]['shares']+=sh
                            pos[code]['bp']=(old_cost+buy_val)/pos[code]['shares']
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
        eq.append({'date':dt,'equity':cash+pv,'pos':len(pos)})
    ld=cd[-1]
    for code,p in list(pos.items()):
        pk=px(code,ld)
        if pk is not None:
            sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'bd':p['bd'],'sd':ld,
                'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'exit':'final'})
    v=[d['equity'] for d in eq]
    if v[0]<=0: return None
    tr=(v[-1]-v[0])/v[0]
    rs=[(v[i]-v[i-1])/v[i-1] for i in range(1,len(v)) if v[i-1]>0]
    if not rs: return None
    y=len(rs)/TD;cagr=(v[-1]/v[0])**(1/y)-1 if y>0 else 0
    mu=sum(rs)/len(rs)
    sd=(sum((r-mu)**2 for r in rs)/len(rs))**0.5
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk=v[0];mdd=0.0
    for x in v:
        if x>pk:pk=x
        dd=(pk-x)/pk
        if dd>mdd:mdd=dd
    cm=cagr/mdd if mdd>0 else float('inf')
    w=sum(1 for t in trades if t['ret']>0)
    wr=w/len(trades) if trades else 0
    yr=defaultdict(lambda:{'s':None,'e':None})
    for d in eq:
        yk=d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s']=d['equity']
        yr[yk]['e']=d['equity']
    ann={y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}
    neg=sum(1 for a in ann.values() if a<0)
    return {'sh':sh,'tr':tr*100,'cagr':cagr*100,'mdd':mdd*100,'calmar':cm,
            'nt':len(trades),'wr':wr*100,'neg':neg,'ann':ann}

# ============ 3 runs ============
for run_label, start_date in [('FULL (2021-2026)', '2021-01-01'),
                                ('OOS  (2024-2026)', '2024-01-01')]:
    cd=[d for d in cd_all if d>=start_date]
    print('\n'+'='*90)
    print('  %s | %d stocks | %d days (%s ~ %s)' % (run_label, len(stocks), len(cd), cd[0], cd[-1]))
    print('='*90)

    results=[]
    total=5*4*7*2*2*2;cnt=0  # reduced trails to speed up
    for K_ in [0.5,1.0,1.5,2.0,2.5]:
        for LB_ in [10,14,21,30]:
            fac=FAC[(K_,LB_)]
            for tr_ in [0.10,0.18,0.22,0.28,0.30,0.35]:
                for mp_ in [5,8]:
                    for sec_ in [1,3]:
                        for floor_ in [0,0.8]:
                            cnt+=1
                            r=backtest(fac,tr_,21,sec_,mp_,floor_,cd)
                            if r:
                                results.append({'K':K_,'LB':LB_,'Trail':tr_,'Pos':mp_,
                                    'Sec':sec_,'Floor':floor_,'S':r['sh'],'R':r['tr'],
                                    'CAGR':r['cagr'],'MDD':r['mdd'],'CM':r['calmar'],
                                    'Trd':r['nt'],'Win':r['wr'],'Neg':r['neg']})

    results.sort(key=lambda x:x['S'],reverse=True)

    # Check 44-pool optimal on 64-pool
    r_global=backtest(FAC[(1.5,14)],0.30,21,1,5,0.8,cd)
    if r_global:
        print('\n  44-pool BEST (K=1.5 LB=14 Trail=30% 5pos strict Floor=0.8) on 64-pool:')
        print('  S=%.4f | R=%.1f%% | MDD=%.1f%% | CM=%.3f | Trd=%d | Win=%.0f%% | NegY=%d' % (
            r_global['sh'],r_global['tr'],r_global['mdd'],r_global['calmar'],r_global['nt'],r_global['wr'],r_global['neg']))
        if r_global['ann']:
            print('  Annual: '+' '.join('%s:%+.1f%%'%(y,r_global['ann'][y]) for y in sorted(r_global['ann'].keys())))

    # 64-pool best
    print('\n  TOP 15 for 64-pool:')
    print('  %4s %4s %4s %5s %4s %4s %5s %7s %7s %5s %6s %5s %4s' % (
        'Rank','K','LB','Trail','Pos','Sec','Floor','Sharpe','Ret%','CAGR%','MDD%','Calmar','Win%'))
    print('  %s'%('-'*80))
    for i,r in enumerate(results[:15]):
        print('  %4d %4.1f %4d %4.0f%% %4d %4d %5.1f %7.4f %7.1f %5.1f %5.1f %6.3f %4.0f%%' % (
            i+1,r['K'],r['LB'],r['Trail']*100,r['Pos'],r['Sec'],r['Floor'],
            r['S'],r['R'],r['CAGR'],r['MDD'],r['CM'],r['Win']))

    # Best annual
    best=results[0]
    r_best=backtest(FAC[(best['K'],best['LB'])],best['Trail'],21,best['Sec'],best['Pos'],best['Floor'],cd)
    print('\n  BEST: K=%.1f LB=%d Trail=%d%% Pos=%d Sec=%d Floor=%.1f' % (
        best['K'],best['LB'],best['Trail']*100,best['Pos'],best['Sec'],best['Floor']))
    if r_best:
        print('  S=%.4f | R=%.1f%% | MDD=%.1f%% | CM=%.3f' % (best['S'],best['R'],best['MDD'],best['CM']))
        print('  Annual: '+' '.join('%s:%+.1f%%'%(y,r_best['ann'][y]) for y in sorted(r_best['ann'].keys())))

    # Param importance
    print('\n  Param importance (avg Sharpe):')
    for param in ['K','LB','Pos','Sec','Floor','Trail']:
        groups=defaultdict(list)
        for r in results:
            if param=='Trail': groups[int(r[param]*100)].append(r['S'])
            else: groups[r[param]].append(r['S'])
        avgs={k:sum(v)/len(v) for k,v in groups.items()}
        best_k=max(avgs,key=avgs.get)
        print('  %s:'%param,' '.join(('%s:%.3f'%(k,avgs[k]))+(('*'if k==best_k else '')) for k in sorted(avgs.keys())))

with open('peakridge_64pool_results.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=['K','LB','Trail','Pos','Sec','Floor','S','R','CAGR','MDD','CM','Trd','Win','Neg'])
    w.writeheader()
    # save the last run's results (OOS)
    w.writerows(results)

print('\n\nDone! Saved peakridge_64pool_results.csv')
