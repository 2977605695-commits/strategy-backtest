"""Final comparison matrix for all configs"""
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
    for LB_ in [10,14,21]:
        if (K_,LB_) not in FAC: FAC[(K_,LB_)]=calc_factor(K_,LB_)

def backtest(factor,trail,rebal,max_same_sector,max_pos,factor_floor):
    cash=INIT;pos={};eq=[]
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    def px(c,d):
        di=idx[c].get(d);return stocks[c]['close'][di] if di is not None else None
    t_n=0;wins=0
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
            pk=px(c,dt)
            if pk is not None: pv+=p['shares']*pk
        eq.append(cash+pv)
    ld=cd[-1]
    for code,p in list(pos.items()):
        pk=px(code,ld)
        if pk is not None:
            sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp;t_n+=1
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
    cm=cagr/mdd if mdd>0 else 0
    wr=wins/t_n if t_n else 0
    yr=defaultdict(lambda:{'s':None,'e':None})
    n=min(len(cd),len(eq))
    for i in range(n):
        yk=cd[i][:4];ev=eq[i]
        if yr[yk]['s'] is None: yr[yk]['s']=ev
        yr[yk]['e']=ev
    ann={y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}
    neg=sum(1 for a in ann.values() if a<0)
    return {'sh':sh,'tr':tr*100,'cagr':cagr*100,'mdd':mdd*100,'calmar':cm,'nt':t_n,'wr':wr*100,'neg':neg,'ann':ann}

# ============ RUN ============
configs=[
    ('BEST_Pos3',  2.0,10,0.18,1,3,0.8, '激进集中'),
    ('BEST_Pos5',  1.0,14,0.15,3,5,0.0, '均衡5股'),
    ('BEST_Pos8',  1.0,21,0.18,3,8,0.0, '稳健分散'),
    ('BEST_Pos10', 1.0,21,0.15,3,10,0.0,'极致分散'),
    ('OLD_44BEST', 1.5,14,0.30,1,5,0.8, '44池旧最优'),
]

results={}
for label,K_,LB_,tr_,sec_,mp_,fl_,tag in configs:
    r=backtest(FAC[(K_,LB_)],tr_,21,sec_,mp_,fl_)
    results[label]=r

print()
print('='*105)
print('  PEAK RIDGE - FULL COMPARISON MATRIX')
print('  64-stock pool | 2021-2026 | Limit>=30 stocks per date')
print('='*105)

print()
print('  ---- PART 1: PERFORMANCE METRICS ----')
hdr = '  {:<18s} {:<10s} {:>4s} {:>3s} {:>4s} {:>3s} {:>4s} {:>3s} {:>7s} {:>8s} {:>6s} {:>5s} {:>7s} {:>6s} {:>5s} {:>3s}'.format(
    'Config','Tag','K','LB','Trl','Pos','Sec','Flr','Sharpe','Return%','CAGR%','MDD%','Calmar','Trades','Win%','Neg')
print(hdr)
print('  ' + '-'*100)
for label,K_,LB_,tr_,sec_,mp_,fl_,tag in configs:
    r=results[label]
    if r:
        s=r['sh']; rt=r['tr']; cg=r['cagr']; md=r['mdd']; cm=r['calmar']; nt=r['nt']; wr=r['wr']; ng=r['neg']
        print('  {:<18s} {:<10s} {:>4.1f} {:>3d} {:>3.0f}% {:>3d} {:>4d} {:>3.1f} {:>7.4f} {:>7.1f}% {:>5.1f}% {:>4.1f}% {:>7.3f} {:>6d} {:>4.0f}% {:>3d}'.format(
            label,tag,K_,LB_,tr_*100,mp_,sec_,fl_,s,rt,cg,md,cm,nt,wr,ng))

print()
print('  ---- PART 2: ANNUAL RETURNS ----')
header='  %-18s'%'Config'
years=sorted(results[list(results.keys())[0]]['ann'].keys())
for y in years:
    header+=' %8s'%y
print(header)
print('  '+'-'*(18+9*len(years)))
for label,_,_,_,_,_,_,tag in configs:
    r=results[label]
    if r:
        row='  %-18s'%label
        for y in years:
            row+=' %+7.1f%%'%r['ann'].get(y,0)
        print(row+'  '+tag)

print()
print('  ---- PART 3: POSITION COUNT COMPARISON ----')
# Load grid results
grid=list(csv.DictReader(open('peakridge_10pos_grid.csv','r',encoding='utf-8-sig')))
print('  {:<5s} {:>10s} {:>9s} {:>7s} {:>8s} {:>7s} {:>7s}'.format('Pos','Avg_Sharpe','In_Top30','Best_S','Best_Ret','Best_DD','Best_CM'))
print('  '+'-'*60)
for mp in ['3','5','8','10']:
    sub=[r for r in grid if r['Pos']==mp]
    if not sub: continue
    vals=[float(r['S']) for r in sub]
    avg_s=sum(vals)/len(vals)
    top30=sum(1 for r in grid[:30] if r['Pos']==mp)
    best_s=max(sub,key=lambda x:float(x['S']))
    best_r=max(sub,key=lambda x:float(x['R']))
    best_d=min(sub,key=lambda x:float(x['MDD']))
    best_c=max(sub,key=lambda x:float(x['CM']))
    print('  Pos={:<3s} {:>10.4f} {:>9d} {:>7.4f} {:>7.1f}% {:>6.1f}% {:>7.3f}'.format(
            mp,avg_s,top30,float(best_s['S']),float(best_r['R']),float(best_d['MDD']),float(best_c['CM'])))

print()
print('  ---- PART 4: TRAIL SENSITIVITY (avg Sharpe) ----')
trails=sorted(set(r['Trail'] for r in grid))
header2 = '  {:<7s}'.format('Trail')
for mp in ['3','5','8','10']:
    header2 += ' {:>17s}'.format('Pos='+mp)
print(header2)
print('  '+'-'*75)
for tr in trails:
    row = '  {:>4.0f}%  '.format(float(tr)*100)
    for mp in ['3','5','8','10']:
        sub=[r for r in grid if r['Pos']==mp and r['Trail']==tr]
        avg_s=sum(float(r['S']) for r in sub)/len(sub) if sub else 0
        bar='#'*int(avg_s*25)
        row += ' {:>7.4f} {:<10s}'.format(avg_s,bar)
    print(row)

print()
print('  ---- PART 5: SUMMARY ----')
best_all=max(grid,key=lambda x:float(x['S']))
best10=max((r for r in grid if r['Pos']=='10'),key=lambda x:float(x['S']))
ba=best_all;b10=best10
print('  Global best: Pos={} K={} LB={} Trail={:.0f}% | S={:.4f} R={:.1f}% DD={:.1f}%'.format(
    ba['Pos'],ba['K'],ba['LB'],float(ba['Trail'])*100,float(ba['S']),float(ba['R']),float(ba['MDD'])))
print('  Pos=10 best: K={} LB={} Trail={:.0f}% | S={:.4f} R={:.1f}% DD={:.1f}%'.format(
    b10['K'],b10['LB'],float(b10['Trail'])*100,float(b10['S']),float(b10['R']),float(b10['MDD'])))
print('  Pos=10 vs Pos=8 delta: S={:.4f}'.format(float(b10['S'])-float(ba['S'])))
print()
print('Done!')
