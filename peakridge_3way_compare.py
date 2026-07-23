"""峰岭因子 · 三种配置详细对比（64池全期）+ 买卖清单"""
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

print('[FACTOR] Computing K=1.5/LB=14, K=1.0/LB=21, K=1.0/LB=10...')
FAC={(1.5,14):calc_factor(1.5,14),(1.0,21):calc_factor(1.0,21),(1.0,10):calc_factor(1.0,10)}
print('  Done.')

def backtest_detail(factor,trail,rebal,max_same_sector,max_pos,factor_floor,cd):
    cash=INIT;pos={};eq=[];trades=[];daily_pos=[]
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
                trades.append({'code':code,'name':stocks[code]['name'],'star':code in star_codes,
                    'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'pnl':p['shares']*(sp-p['bp']),'exit':'trail'})
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
                    trades.append({'code':code,'name':stocks[code]['name'],'star':code in star_codes,
                        'bd':pos[code]['bd'],'sd':dt,
                        'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                        'pnl':pos[code]['shares']*(sp-pos[code]['bp']),'exit':'rebal'})
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
        daily_pos.append({'date':dt,'codes':list(pos.keys())})
    ld=cd[-1]
    for code,p in list(pos.items()):
        pk=px(code,ld)
        if pk is not None:
            sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'name':stocks[code]['name'],'star':code in star_codes,
                'bd':p['bd'],'sd':ld,
                'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                'pnl':p['shares']*(sp-p['bp']),'exit':'final'})
    v=[d['equity'] for d in eq]
    if v[0]<=0: return None
    tr=(v[-1]-v[0])/v[0]
    rs=[(v[i]-v[i-1])/v[i-1] for i in range(1,len(v)) if v[i-1]>0]
    if not rs: return None
    y=len(rs)/TD;cagr=(v[-1]/v[0])**(1/y)-1 if y>0 else 0
    mu=sum(rs)/len(rs)
    sd=(sum((r-mu)**2 for r in rs)/len(rs))**0.5
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk_v=v[0];mdd=0.0;mdd_dates=(cd[0],cd[0])
    for i,x in enumerate(v):
        if x>pk_v:pk_v=x
        dd=(pk_v-x)/pk_v
        if dd>mdd:mdd=dd;mdd_dates=(cd[max(0,i-1)],cd[i])
    cm=cagr/mdd if mdd>0 else float('inf')
    w=sum(1 for t in trades if t['ret']>0)
    wr=w/len(trades) if trades else 0
    yr=defaultdict(lambda:{'s':None,'e':None})
    for d in eq:
        yk=d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s']=d['equity']
        yr[yk]['e']=d['equity']
    ann={y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}
    return {'sh':sh,'tr':tr*100,'cagr':cagr*100,'mdd':mdd*100,'calmar':cm,
        'nt':len(trades),'wr':wr*100,'ann':ann,'eq':eq,'trades':trades,
        'mdd_dates':mdd_dates,'daily_pos':daily_pos,'cash':cash}

# ============ 3 configs ============
configs=[
    ('CFG_A_44BEST',  (1.5,14), 0.30, 1, 5, 0.8, '44-pool最优 → 64池'),
    ('CFG_B_64FULL',  (1.0,21), 0.18, 3, 8, 0.0, '64池全期最优'),
    ('CFG_C_64OOS',   (1.0,10), 0.35, 3, 8, 0.8, '64池OOS最优'),
]

results={}
for label,(K,LB),trail,sec,mp,floor,desc in configs:
    print('\n'+'='*90)
    print('  %s: K=%.1f LB=%d Trail=%d%% Pos=%d Sec=%d Floor=%.1f' % (label,K,LB,trail*100,mp,sec,floor))
    print('  %s' % desc)
    print('='*90)
    r=backtest_detail(FAC[(K,LB)],trail,21,sec,mp,floor,cd)
    results[label]=r
    if r:
        print('  Sharpe=%.4f | Ret=%.1f%% | MDD=%.1f%% | Calmar=%.3f | Trd=%d | Win=%.0f%%' % (
            r['sh'],r['tr'],r['mdd'],r['calmar'],r['nt'],r['wr']))
        print('  MDD period: %s ~ %s' % r['mdd_dates'])
        print('  Annual: '+' '.join('%s:%+.1f%%'%(y,r['ann'][y]) for y in sorted(r['ann'].keys())))

        # Trade analysis
        trades=r['trades']
        star_trades=[t for t in trades if t['star']]
        old_trades=[t for t in trades if not t['star']]
        print('\n  --- Trade Analysis ---')
        print('  Total: %d trades | Old-stock: %d | STAR: %d' % (len(trades),len(old_trades),len(star_trades)))
        for tag,ts in [('Old',old_trades),('STAR',star_trades),('All',trades)]:
            if not ts: continue
            wins=[t for t in ts if t['ret']>0]
            wr_=len(wins)/len(ts)*100
            avg_r=sum(t['ret'] for t in ts)/len(ts)*100
            avg_pnl=sum(t['pnl'] for t in ts)/len(ts)
            print('  %s: %d trades | Win=%.0f%% | AvgRet=%.1f%% | AvgPnL=%.0f' % (tag,len(ts),wr_,avg_r,avg_pnl))

        # Exit breakdown
        for exit_type in ['trail','rebal','final']:
            ts=[t for t in trades if t['exit']==exit_type]
            if ts:
                avg_r=sum(t['ret'] for t in ts)/len(ts)*100
                wr_=sum(1 for t in ts if t['ret']>0)/len(ts)*100
                print('  Exit-%s: %d trades | AvgRet=%.1f%% | Win=%.0f%%' % (exit_type,len(ts),avg_r,wr_))

        # Year-by-year trade detail
        print('\n  --- Year-by-Year Trade Detail ---')
        for y in sorted(r['ann'].keys()):
            yt=[t for t in trades if t['bd'][:4]==y or t['sd'][:4]==y]
            if not yt: continue
            wins=[t for t in yt if t['ret']>0]
            wr_=len(wins)/len(yt)*100
            avg_r=sum(t['ret'] for t in yt)/len(yt)*100
            star_yt=[t for t in yt if t['star']]
            print('  %s: %d trades (%d STAR) | Win=%.0f%% | AvgRet=%.1f%%' % (y,len(yt),len(star_yt),wr_,avg_r))

        # Sector concentration
        sector_trades=defaultdict(lambda:{'cnt':0,'ret':0.0,'code':''})
        for t in trades:
            sec=sm.get(t['code'],'?')
            sector_trades[sec]['cnt']+=1;sector_trades[sec]['ret']+=t['ret']
            sector_trades[sec]['code']=t['code']
        print('\n  --- Sector Concentration (top 10 by count) ---')
        for sec in sorted(sector_trades,key=lambda x:sector_trades[x]['cnt'],reverse=True)[:10]:
            d=sector_trades[sec];avg_r=d['ret']/d['cnt']*100 if d['cnt'] else 0
            print('  %-25s %3d trades | AvgRet=%.1f%%' % (sec,d['cnt'],avg_r))

        # Save trades
        with open('%s_trades.csv'%label,'w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=['code','name','star','bd','sd','ret','pnl','exit'])
            w.writeheader()
            for t in sorted(trades,key=lambda x:x['bd']):
                w.writerow({k:t[k] for k in ['code','name','star','bd','sd','ret','pnl','exit']})
        print('  Trades saved: %s_trades.csv' % label)

# ============ Comparison ============
print('\n'+'='*90)
print('  DEGRADATION ANALYSIS: Why does 44-pool BEST fail on 64-pool?')
print('='*90)

rA=results['CFG_A_44BEST']
rB=results['CFG_B_64FULL']

if rA and rB:
    # 1. STAR stock participation
    tA_star=[t for t in rA['trades'] if t['star']]
    tB_star=[t for t in rB['trades'] if t['star']]
    print('\n  1. STAR stock participation:')
    print('     CFG_A (44-BEST on 64): %d STAR trades (%.0f%% of total)' % (len(tA_star),len(tA_star)/len(rA['trades'])*100))
    print('     CFG_B (64-FULL):       %d STAR trades (%.0f%% of total)' % (len(tB_star),len(tB_star)/len(rB['trades'])*100))

    # 2. Factor coverage
    facA=FAC[(1.5,14)];facB=FAC[(1.0,21)]
    for code in sorted(star_codes)[:5]:
        na=sum(1 for v in facA[code].values() if not math.isnan(v))
        nb=sum(1 for v in facB[code].values() if not math.isnan(v))
        print('     %s %s: K=1.5/LB=14 → %d valid days | K=1.0/LB=21 → %d valid days' % (code,stocks[code]['name'],na,nb))

    # 3. Factor value distribution on a sample date
    dt='2024-06-15'
    print('\n  2. Factor values on %s (mid-2024, STAR stocks active):' % dt)
    for label,fac in [('CFG_A K=1.5/LB=14',facA),('CFG_B K=1.0/LB=21',facB)]:
        vals=[v for c,v in fac.items() if c in star_codes and dt in fac[c] and not math.isnan(fac[c][dt])]
        above08=sum(1 for v in vals if v>=0.8)
        print('     %s: %d/%d STAR stocks have signal, %d >= 0.8' % (label,above08,len(vals),above08 if len(vals)>0 else 0))

    # 4. Worst trades
    print('\n  3. Worst 10 trades in CFG_A (44-BEST on 64):')
    worst=sorted(rA['trades'],key=lambda x:x['ret'])[:10]
    for t in worst:
        print('     %s %s | %s→%s | ret=%.1f%% | exit=%s | %s' % (
            t['code'],t['name'],t['bd'],t['sd'],t['ret']*100,t['exit'],
            'STAR' if t['star'] else 'old'))

    # 5. Missing opportunities
    print('\n  4. STAR stocks that CFG_A NEVER traded but CFG_B DID:')
    codesA=set(t['code'] for t in rA['trades'] if t['star'])
    codesB=set(t['code'] for t in rB['trades'] if t['star'])
    missed=codesB-codesA
    for c in sorted(missed):
        tB=[t for t in rB['trades'] if t['code']==c]
        avg_r=sum(t['ret'] for t in tB)/len(tB)*100 if tB else 0
        print('     %s %s: CFG_B traded %d times, avg ret=%.1f%%' % (c,stocks[c]['name'],len(tB),avg_r))

    # 6. Concentration vs diversification
    print('\n  5. Position concentration:')
    # Count unique stocks traded
    uniqA=len(set(t['code'] for t in rA['trades']))
    uniqB=len(set(t['code'] for t in rB['trades']))
    print('     CFG_A: %d unique stocks traded (max %d positions)' % (uniqA,5))
    print('     CFG_B: %d unique stocks traded (max %d positions)' % (uniqB,8))

    # 7. Timeline of DD
    print('\n  6. Drawdown comparison:')
    print('     CFG_A MDD: %.1f%% (%s ~ %s)' % (rA['mdd'],rA['mdd_dates'][0],rA['mdd_dates'][1]))
    print('     CFG_B MDD: %.1f%% (%s ~ %s)' % (rB['mdd'],rB['mdd_dates'][0],rB['mdd_dates'][1]))

    # 8. Annual breakdown comparison
    print('\n  7. Annual return comparison:')
    print('     %-6s %12s %12s' % ('Year','CFG_A_44BEST','CFG_B_64FULL'))
    for y in sorted(rA['ann'].keys()):
        print('     %-6s %+11.1f%% %+11.1f%%' % (y,rA['ann'].get(y,0),rB['ann'].get(y,0)))

print('\n\nDone!')
