"""Crash protection: quick stop within N days after purchase"""
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
FAC=calc_factor(1.0,21)
def px(c,d,idx):
    di=idx[c].get(d);return stocks[c]['close'][di] if di is not None else None

def backtest(trail,rebal,max_same_sector,max_pos,factor_floor,crash_days,crash_thresh):
    cash=INIT;pos={};eq=[];t_n=0;wins=0
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    crash_cut=0;trail_cut=0
    for di,dt in enumerate(cd):
        for code,p in list(pos.items()):
            pk=px(code,dt,idx)
            if pk is None: continue
            if pk>p['peak']:p['peak']=pk
            # Crash protection: quick stop within crash_days of purchase
            loss=(pk-p['bp'])/p['bp']
            hold_days=di-p['bi']
            crash_trigger=(crash_days>0 and hold_days<=crash_days and loss<=crash_thresh)
            trail_trigger=(pk<=p['peak']*(1-trail))
            if crash_trigger or trail_trigger:
                sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                t_n+=1
                if sp>p['bp']: wins+=1
                if crash_trigger: crash_cut+=1
                elif trail_trigger: trail_cut+=1
                del pos[code]
                continue
        if di%rebal==0:
            cand=[(c,FAC.get(c,{}).get(dt,float('nan'))) for c in stocks]
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
    return {'sh':sh,'tr':tr*100,'cagr':cagr*100,'mdd':mdd*100,'calmar':cm,'nt':t_n,'wr':wr*100,'neg':neg,'ann':ann,'crash':crash_cut,'trail':trail_cut}

# BASELINE: no crash protection
r_base=backtest(0.18,21,3,8,0.0,0,0)
print('='*95)
print('  CRASH PROTECTION TEST (quick stop after purchase)')
print('  Base: K=1.0 LB=21 Trail=18% Pos=8 Sec=Relaxed')
print('='*95)
print('  {:<30s} {:>7s} {:>8s} {:>6s} {:>7s} {:>6s} {:>5s} {:>4s} {:>5s} {:>5s}'.format(
    'Protection','Sharpe','Return%','MDD%','Calmar','Trades','Win%','Neg','Crash','Trail'))
print('  '+'-'*88)

s_base=r_base['sh'];mdd_base=r_base['mdd']
print('  {:<30s} {:>7.4f} {:>7.1f}% {:>5.1f}% {:>7.3f} {:>6d} {:>4.0f}% {:>4d} {:>5s} {:>5s}  [BASELINE]'.format(
    'BASELINE (no protection)',r_base['sh'],r_base['tr'],r_base['mdd'],r_base['calmar'],r_base['nt'],r_base['wr'],r_base['neg'],'—','—'))

best=None
for crash_d in [7,10,14]:
    for crash_t in [-0.06,-0.08,-0.10,-0.12]:
        r=backtest(0.18,21,3,8,0.0,crash_d,crash_t)
        if r:
            mdd_cut=(r_base['mdd']-r['mdd'])/r_base['mdd']*100
            label='Drop>{:.0f}% in {}d'.format(abs(crash_t)*100,crash_d)
            tag=''
            if r['sh']>r_base['sh']: tag=' SH++'
            if r['mdd']<r_base['mdd']: tag+=' DD--'
            if best is None or r['sh']>best['sh']: best=r;best_label=label
            print('  {:<30s} {:>7.4f} {:>7.1f}% {:>5.1f}% {:>7.3f} {:>6d} {:>4.0f}% {:>4d} {:>5d} {:>5d}{}'.format(
                label,r['sh'],r['tr'],r['mdd'],r['calmar'],r['nt'],r['wr'],r['neg'],
                r['crash'],r['trail'],tag))

if best:
    print('\n'+'='*95)
    print('  BEST CRASH PROTECTION: {}'.format(best_label))
    print('  S={:.4f} R={:.1f}% MDD={:.1f}% ({:.0f}% less) CM={:.3f} Trd={:d} Win={:.0f}%'.format(
        best['sh'],best['tr'],best['mdd'],(r_base['mdd']-best['mdd'])/r_base['mdd']*100,
        best['calmar'],best['nt'],best['wr']))
    print('  Crash exits: {:d} | Trail exits: {:d}'.format(best['crash'],best['trail']))
    print('  Annual: '+' '.join('{}:{:+.1f}%'.format(y,best['ann'][y]) for y in sorted(best['ann'].keys())))

print('\nDone!')
