"""
峰岭因子 · 全参数网格搜索
K x LB x Trail x MAX_POS x Sector x FactorFloor
"""
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

all_s=load_prices(stock_filter=None)
stocks={c:i for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
cd=get_common_dates(stocks)
print('[DATA] %d stocks, %d days' % (len(stocks),len(cd)))

# ============ Factor cache ============
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
            vals[dates[i]]=ps/rs if rs>0 else float('nan')
        fac[code]=vals
    return fac

print('[FACTOR] Pre-computing...')
FAC={}
for K_ in [0.5,1.0,1.5,2.0,2.5]:
    for LB_ in [10,14,21,30]:
        key=(K_,LB_)
        if key not in FAC: FAC[key]=calc_factor(K_,LB_,'ratio')
print('  Done. %d variants.' % len(FAC))

# ============ Single backtest ============
def backtest(factor,trail,rebal,max_same_sector,max_pos,factor_floor):
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
                    px=stocks[code]['close'][idx[code][dt]]
                    sp=px*(1-SLIP-S_FEE-STAX);cash+=pos[code]['shares']*sp
                    trades.append({'code':code,'name':stocks[code]['name'],
                        'bd':pos[code]['bd'],'sd':dt,
                        'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                        'exit':'rebal'})
                    del pos[code]

            if not selected: continue
            n_sel=len(selected)
            pv_current=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
            total_equity=cash+pv_current
            target_val=total_equity/n_sel

            for code,score in selected:
                if code in pos:
                    curr_val=pos[code]['shares']*stocks[code]['close'][idx[code][dt]]
                    diff=target_val-curr_val
                    raw=stocks[code]['close'][idx[code][dt]]
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
                    raw=stocks[code]['close'][idx[code][dt]]
                    bp=raw*(1+SLIP+B_FEE)
                    if cash>=buy_val*0.99 and bp>0 and buy_val>bp*0.01:
                        sh=buy_val/bp;cash-=buy_val
                        pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}

        cash*=(1+RF/TD)
        pv=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date':dt,'equity':cash+pv,'pos':len(pos)})

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
    if v[0]<=0: return None
    tr=(v[-1]-v[0])/v[0];rs=[(v[i]-v[i-1])/v[i-1] for i in range(1,len(v)) if v[i-1]>0]
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

    # Annual returns
    yr=defaultdict(lambda:{'s':None,'e':None})
    for d in eq:
        yk=d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s']=d['equity']
        yr[yk]['e']=d['equity']
    ann={y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}
    negative_years=sum(1 for a in ann.values() if a<0)

    # Exit breakdown
    exits=defaultdict(lambda:{'cnt':0,'ret':0.0})
    for t in trades:
        e=t['exit'];exits[e]['cnt']+=1;exits[e]['ret']+=t['ret']

    # Average hold days
    holds=[]
    for t in trades:
        if 'bd' in t and 'sd' in t:
            try:
                bi=cd.index(t['bd']);si=cd.index(t['sd'])
                holds.append(si-bi)
            except: pass
    avg_hold=sum(holds)/len(holds) if holds else 0

    return {'sh':sh,'tr':tr*100,'cagr':cagr*100,'mdd':mdd*100,'calmar':cm,
            'nt':len(trades),'wr':wr*100,'neg_yrs':negative_years,'avg_hold':avg_hold,
            'ann':ann,'exits':exits}

# ============ Grid ============
print('\n'+'='*90)
print('  FULL GRID SEARCH')
print('  K=[0.5,1.0,1.5,2.0,2.5]  LB=[10,14,21,30]')
print('  Trail=[0.10,0.15,0.18,0.20,0.22,0.25,0.28,0.30,0.35]')
print('  MAX_POS=[3,5,8]  Sector=[1(strict),3(relaxed)]  Floor=[0,0.8]')
print('  Total: %d combos' % (5*4*9*3*2*2))
print('='*90)

results=[]
total=5*4*9*3*2*2;cnt=0

for K_ in [0.5,1.0,1.5,2.0,2.5]:
    for LB_ in [10,14,21,30]:
        fac=FAC[(K_,LB_)]
        for tr_ in [0.10,0.15,0.18,0.20,0.22,0.25,0.28,0.30,0.35]:
            for mp_ in [3,5,8]:
                for sec_ in [1,3]:
                    for floor_ in [0,0.8]:
                        cnt+=1
                        r=backtest(fac,tr_,21,sec_,mp_,floor_)
                        if r is None: continue
                        results.append({
                            'K':K_,'LB':LB_,'Trail':tr_,'MAX_POS':mp_,
                            'Sector':sec_,'Floor':floor_,
                            'Sharpe':r['sh'],'Ret%':r['tr'],'CAGR%':r['cagr'],
                            'MDD%':r['mdd'],'Calmar':r['calmar'],
                            'Trades':r['nt'],'Win%':r['wr'],
                            'NegYrs':r['neg_yrs'],'AvgHold':r['avg_hold'],
                        })
                        if cnt%200==0:
                            print('  [%d/%d]' % (cnt,total))

print('\n  Done. %d valid results.' % len(results))
results.sort(key=lambda x:x['Sharpe'],reverse=True)

# ============ Output ============
print('\n'+'='*90)
print('  TOP 30 BY SHARPE')
print('='*90)
print('  %4s %4s %4s %5s %4s %4s %5s %7s %7s %5s %6s %5s %4s %5s' % (
    'Rank','K','LB','Trail','Pos','Sec','Floor','Sharpe','Ret%','CAGR%','MDD%','Calmar','Win%','NegY'))
print('  %s' % ('-'*85))
for i,r in enumerate(results[:30]):
    print('  %4d %4.1f %4d %4.0f%% %4d %4d %5.1f %7.4f %7.1f %5.1f %5.1f %6.3f %4.0f%% %4d' % (
        i+1,r['K'],r['LB'],r['Trail']*100,r['MAX_POS'],r['Sector'],r['Floor'],
        r['Sharpe'],r['Ret%'],r['CAGR%'],r['MDD%'],r['Calmar'],r['Win%'],r['NegYrs']))

# --- Top by metric ---
print('\n'+'='*90)
print('  BEST BY METRIC')
print('='*90)
for metric,label in [('Sharpe','夏普'),('Calmar','卡玛'),('Ret%','总收益'),('MDD%','最小回撤'),('NegYrs','无负年')]:
    if metric=='MDD%':
        best=min(results,key=lambda x:x[metric])
    elif metric=='NegYrs':
        best=[r for r in results if r['NegYrs']==0]
        best=sorted(best,key=lambda x:x['Sharpe'],reverse=True)[:3] if best else []
        if best:
            for b in best:
                print('  %s: K=%.1f LB=%d Trail=%d%% Pos=%d Sec=%d Floor=%.1f S=%.4f R=%.1f%% DD=%.1f%%' % (
                    label,b['K'],b['LB'],b['Trail']*100,b['MAX_POS'],b['Sector'],b['Floor'],
                    b['Sharpe'],b['Ret%'],b['MDD%']))
        continue
    else:
        best=max(results,key=lambda x:x[metric])
    print('  %s: K=%.1f LB=%d Trail=%d%% Pos=%d Sec=%d Floor=%.1f S=%.4f R=%.1f%% DD=%.1f%% CM=%.3f' % (
        label,best['K'],best['LB'],best['Trail']*100,best['MAX_POS'],best['Sector'],best['Floor'],
        best['Sharpe'],best['Ret%'],best['MDD%'],best['Calmar']))

# --- Parameter importance ---
print('\n'+'='*90)
print('  PARAMETER IMPORTANCE (avg Sharpe by value)')
print('='*90)
for param in ['K','LB','MAX_POS','Sector','Floor','Trail']:
    groups=defaultdict(list)
    for r in results:
        if param=='Trail':
            groups[int(r[param]*100)].append(r['Sharpe'])
        else:
            groups[r[param]].append(r['Sharpe'])
    avgs={k:sum(v)/len(v) for k,v in groups.items()}
    print('  %s:' % param)
    for k in sorted(avgs.keys()):
        bar='#'*int(avgs[k]*30)
        print('    %s: %.4f %s' % (str(k),avgs[k],bar))

# --- Best config detail ---
best=results[0]
print('\n'+'='*90)
print('  GLOBAL BEST CONFIG')
print('='*90)
print('  K=%.1f | LB=%d | Trail=%d%% | MAX_POS=%d | Sector=%d | Floor=%.1f' % (
    best['K'],best['LB'],best['Trail']*100,best['MAX_POS'],best['Sector'],best['Floor']))
print('  Sharpe=%.4f | Ret=%.1f%% | CAGR=%.1f%% | MDD=%.1f%% | Calmar=%.3f' % (
    best['Sharpe'],best['Ret%'],best['CAGR%'],best['MDD%'],best['Calmar']))
print('  Trades=%d | Win=%.0f%% | NegYears=%d | AvgHold=%.0fd' % (
    best['Trades'],best['Win%'],best['NegYrs'],best['AvgHold']))

# Rerun best config for annual detail
fac_best=FAC[(best['K'],best['LB'])]
r_full=backtest(fac_best,best['Trail'],21,best['Sector'],best['MAX_POS'],best['Floor'])
if r_full:
    print('\n  Annual:')
    for y in sorted(r_full['ann'].keys()):
        print('    %s: %+6.1f%%' % (y,r_full['ann'][y]))
    print('\n  Exits:')
    for e,d in sorted(r_full['exits'].items()):
        avg_r=d['ret']/d['cnt']*100 if d['cnt'] else 0
        print('    %s: %d trades, avg=%.1f%%' % (e,d['cnt'],avg_r))

# Save
with open('peakridge_grid_results.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=results[0].keys())
    w.writeheader();w.writerows(results)
print('\n  Saved %d results to peakridge_grid_results.csv' % len(results))
print('Done!')
