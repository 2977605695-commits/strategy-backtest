"""峰岭因子 · 10股池全量网格（Pos=3,5,8,10）"""
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
print('[DATA] %d stocks, %d days (%s ~ %s)' % (len(stocks),len(cd),cd[0],cd[-1]))

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

print('[FACTOR] Computing 20 variants...')
FAC={}
for K_ in [0.5,1.0,1.5,2.0,2.5]:
    for LB_ in [10,14,21,30]:
        FAC[(K_,LB_)]=calc_factor(K_,LB_)
print('  Done.')

def backtest(factor,trail,rebal,max_same_sector,max_pos,factor_floor):
    cash=INIT;pos={};eq=[];t_n=0;wins=0
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
                t_n+=1
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
                    t_n+=1
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
            t_n+=1
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
    wr=wins/t_n if t_n else 0
    return {'sh':sh,'tr':tr*100,'cagr':cagr*100,'mdd':mdd*100,'calmar':cm,'nt':t_n,'wr':wr*100}

# Grid: 5*4*7*4*2*2 = 2240 combos (reduced trails to speed up)
results=[]
total=5*4*7*4*2*2;cnt=0
trails_to_test=[0.10,0.15,0.18,0.22,0.28,0.30,0.35]  # 7 key trails
print('[GRID] %d combos...' % total)
for K_ in [0.5,1.0,1.5,2.0,2.5]:
    for LB_ in [10,14,21,30]:
        fac=FAC[(K_,LB_)]
        for tr_ in trails_to_test:
            for mp_ in [3,5,8,10]:
                for sec_ in [1,3]:
                    for floor_ in [0,0.8]:
                        cnt+=1
                        r=backtest(fac,tr_,21,sec_,mp_,floor_)
                        if r:
                            results.append({'K':K_,'LB':LB_,'Trail':tr_,'Pos':mp_,'Sec':sec_,'Floor':floor_,'S':r['sh'],'R':r['tr'],'CAGR':r['cagr'],'MDD':r['mdd'],'CM':r['calmar'],'Trd':r['nt'],'Win':r['wr']})
                        if cnt%400==0: print('  [%d/%d]'%(cnt,total))

print('\n  Done. %d results.' % len(results))
results.sort(key=lambda x:x['S'],reverse=True)

# Compare pos values
print('\n'+'='*80)
print('  POSITION COUNT BREAKDOWN (avg Sharpe by pos)')
print('='*80)
for mp in [3,5,8,10]:
    sub=[r for r in results if r['Pos']==mp]
    avg_s=sum(r['S'] for r in sub)/len(sub) if sub else 0
    top10=[r for r in results[:100] if r['Pos']==mp]  # how many in top 100
    best_s=max(sub,key=lambda x:x['S']) if sub else None
    print('  Pos=%2d: avg S=%.4f | top100: %d | best: S=%.4f R=%.1f%% DD=%.1f%% (K=%.1f LB=%d T=%d%%)' % (
        mp,avg_s,len(top10),
        best_s['S'],best_s['R'],best_s['MDD'],best_s['K'],best_s['LB'],best_s['Trail']*100) if best_s else print('  Pos=%2d: no data'%mp))

# TOP 30
print('\n'+'='*80)
print('  TOP 30 BY SHARPE (all pos values)')
print('='*80)
print('  %4s %4s %4s %5s %4s %4s %5s %7s %7s %5s %6s %5s %4s' % ('Rank','K','LB','Trail','Pos','Sec','Floor','Sharpe','Ret%','CAGR%','MDD%','Calmar','Win%'))
print('  %s'%('-'*78))
for i,r in enumerate(results[:30]):
    mark=' <--' if r['Pos']==10 else ''
    print('  %4d %4.1f %4d %4.0f%% %4d %4d %5.1f %7.4f %7.1f %5.1f %5.1f %6.3f %4.0f%%%s' % (
        i+1,r['K'],r['LB'],r['Trail']*100,r['Pos'],r['Sec'],r['Floor'],r['S'],r['R'],r['CAGR'],r['MDD'],r['CM'],r['Win'],mark))

# Best for each pos
print('\n'+'='*80)
print('  BEST CONFIG FOR EACH POSITION COUNT')
print('='*80)
for mp in [3,5,8,10]:
    sub=[r for r in results if r['Pos']==mp]
    best=max(sub,key=lambda x:x['S'])
    print('  Pos=%2d: K=%.1f LB=%d Trail=%d%% Sec=%d Floor=%.1f | S=%.4f R=%.1f%% MDD=%.1f%% CM=%.3f Trd=%d' % (
        mp,best['K'],best['LB'],best['Trail']*100,best['Sec'],best['Floor'],best['S'],best['R'],best['MDD'],best['CM'],best['Trd']))

# Param importance by pos
print('\n'+'='*80)
print('  TRAIL SENSITIVITY BY POS (avg Sharpe)')
print('='*80)
for tr in trails_to_test:
    row=[]
    for mp in [3,5,8,10]:
        sub=[r for r in results if r['Pos']==mp and abs(r['Trail']-tr)<0.001]
        avg_s=sum(r['S'] for r in sub)/len(sub) if sub else 0
        row.append('%s:%.3f'%(mp,avg_s))
    print('  Trail=%d%%: %s' % (tr*100,' | '.join(row)))

# Save
with open('peakridge_10pos_grid.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=results[0].keys())
    w.writeheader();w.writerows(results)
print('\n  Saved peakridge_10pos_grid.csv')
print('Done!')
