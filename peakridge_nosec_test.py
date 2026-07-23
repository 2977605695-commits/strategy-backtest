"""No sector constraint test"""
import sys,io,os,math,json,csv
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
def backtest(trail,rebal,max_same_sector,max_pos,factor_floor):
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
        if di%rebal==0:
            cand=[(c,FAC.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and s>=factor_floor and c in idx and dt in idx[c]]
            cand.sort(key=lambda x:x[1],reverse=True)
            selected=[];sec_counts={}
            for c,s in cand:
                if max_same_sector<99:
                    sec=sm.get(c,'');cnt=sec_counts.get(sec,0)
                    if cnt>=max_same_sector: continue
                if len(selected)>=max_pos: break
                if max_same_sector<99:
                    test_counts=dict(sec_counts);test_counts[sec]=test_counts.get(sec,0)+1
                    majority=[ss for ss,cc in test_counts.items() if cc>=2]
                    if len(majority)>1: continue
                selected.append((c,s))
                if max_same_sector<99:
                    sec_counts[sec]=sec_counts.get(sec,0)+1
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
    n=min(len(cd),len(eq))
    yr=defaultdict(lambda:{'s':None,'e':None})
    for i in range(n):
        yk=cd[i][:4];ev=eq[i]
        if yr[yk]['s'] is None: yr[yk]['s']=ev
        yr[yk]['e']=ev
    ann={y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}
    neg=sum(1 for a in ann.values() if a<0)
    return {'sh':sh,'tr':tr*100,'cagr':cagr*100,'mdd':mdd*100,'calmar':cm,'nt':t_n,'wr':wr*100,'neg':neg,'ann':ann}

print('='*85)
print('  SECTOR CONSTRAINT: None(99) vs Relaxed(3) vs Strict(1)')
print('  K=1.0 LB=21 Trail=18% Floor=0')
print('='*85)
hdr='  {:<5s} {:<10s} {:>7s} {:>8s} {:>6s} {:>6s} {:>7s} {:>5s} {:>5s} {:>4s}'.format(
    'Pos','Sector','Sharpe','Return%','CAGR%','MDD%','Calmar','Trades','Win%','Neg')
print(hdr)
print('  '+'-'*70)
for mp in [3,5,8,10]:
    for sec_label,sec_val in [('None(99)',99),('Relax(3)',3),('Strict(1)',1)]:
        r=backtest(0.18,21,sec_val,mp,0.0)
        if r:
            print('  {:<5d} {:<10s} {:>7.4f} {:>7.1f}% {:>5.1f}% {:>5.1f}% {:>7.3f} {:>5d} {:>4.0f}% {:>4d}'.format(
                mp,sec_label,r['sh'],r['tr'],r['cagr'],r['mdd'],r['calmar'],r['nt'],r['wr'],r['neg']))

print('\n'+'='*85)
print('  NO-SECTOR: Trail sensitivity (K=1.0 LB=21 Pos=8)')
print('='*85)
for tr in [0.10,0.12,0.15,0.18,0.22,0.25,0.28,0.30,0.35]:
    r=backtest(tr,21,99,8,0.0)
    if r:
        print('  Trail={:>3.0f}%: S={:.4f} R={:.1f}% MDD={:.1f}% CM={:.3f} Trd={:d} Win={:.0f}%'.format(
            tr*100,r['sh'],r['tr'],r['mdd'],r['calmar'],r['nt'],r['wr']))

print('\n'+'='*85)
print('  BEST NO-SECTOR (small sweep)')
print('='*85)
best=None
for mp in [3,5,8,10]:
    for tr in [0.15,0.18,0.22,0.28,0.35]:
        r=backtest(tr,21,99,mp,0.0)
        if r and (best is None or r['sh']>best['sh']):
            best=r;best_label='Pos={} Trail={:.0f}%'.format(mp,tr*100)
if best:
    print('  {} | S={:.4f} R={:.1f}% MDD={:.1f}% CM={:.3f} Trd={:d} Win={:.0f}% Neg={:d}'.format(
        best_label,best['sh'],best['tr'],best['mdd'],best['calmar'],best['nt'],best['wr'],best['neg']))
    print('  Annual: '+' '.join('{}:{:+.1f}%'.format(y,best['ann'][y]) for y in sorted(best['ann'].keys())))

# Direct comparison
r_relaxed=backtest(0.18,21,3,8,0.0)
r_none=backtest(0.18,21,99,8,0.0)
if r_relaxed and r_none:
    print('\n  Pos=8 direct: Relax(3) vs None(99)')
    print('  {:<10s} {:>7s} {:>8s} {:>6s} {:>7s} {:>5s} {:>4s}'.format(
        'Sector','Sharpe','Return%','MDD%','Calmar','Win%','Neg'))
    print('  {:<10s} {:>7.4f} {:>7.1f}% {:>5.1f}% {:>7.3f} {:>4.0f}% {:>4d}'.format(
        'Relax(3)',r_relaxed['sh'],r_relaxed['tr'],r_relaxed['mdd'],r_relaxed['calmar'],r_relaxed['wr'],r_relaxed['neg']))
    print('  {:<10s} {:>7.4f} {:>7.1f}% {:>5.1f}% {:>7.3f} {:>4.0f}% {:>4d}'.format(
        'None(99)',r_none['sh'],r_none['tr'],r_none['mdd'],r_none['calmar'],r_none['wr'],r_none['neg']))

print('\nDone!')
