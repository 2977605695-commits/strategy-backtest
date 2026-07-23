"""Combined: No-Surge + Crash Protection"""
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

def backtest(trail,rebal,max_same_sector,max_pos,factor_floor,surge_d,surge_t,crash_d,crash_t):
    cash=INIT;pos={};eq=[];t_n=0;wins=0;crash_cut=0;trail_cut=0;surge_skip=0
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    for di,dt in enumerate(cd):
        for code,p in list(pos.items()):
            pk=px(code,dt,idx)
            if pk is None: continue
            if pk>p['peak']:p['peak']=pk
            loss=(pk-p['bp'])/p['bp'];hold_days=di-p['bi']
            crash_trigger=(crash_d>0 and hold_days<=crash_d and loss<=crash_t)
            trail_trigger=(pk<=p['peak']*(1-trail))
            if crash_trigger or trail_trigger:
                sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                t_n+=1
                if sp>p['bp']: wins+=1
                if crash_trigger:crash_cut+=1
                elif trail_trigger:trail_cut+=1
                del pos[code]
                continue
        if di%rebal==0:
            cand=[(c,FAC.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and s>=factor_floor and c in idx and dt in idx[c]]
            if surge_d>0:
                filtered=[]
                for c,s in cand:
                    di_c=idx[c].get(dt)
                    if di_c is None or di_c<surge_d:
                        filtered.append((c,s)); continue
                    px_now=stocks[c]['close'][di_c];px_past=stocks[c]['close'][di_c-surge_d]
                    if px_past>0:
                        surge_ret=(px_now-px_past)/px_past
                        if surge_ret>=surge_t:surge_skip+=1;continue
                    filtered.append((c,s))
                cand=filtered
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
    mu=sum(rs)/len(rs);sd=(sum((r-mu)**2 for r in rs)/len(rs))**0.5
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk_v=eq[0];mdd=0.0
    for x in eq:
        if x>pk_v:pk_v=x
        dd=(pk_v-x)/pk_v
        if dd>mdd:mdd=dd
    cm=cagr/mdd if mdd>0 else 0
    wr=wins/t_n if t_n else 0
    return {'sh':sh,'tr':tr*100,'mdd':mdd*100,'calmar':cm,'nt':t_n,'wr':wr*100,'crash':crash_cut,'trail':trail_cut,'surge':surge_skip}

# Run
r_base=backtest(0.18,21,3,8,0.0,0,0,0,0)
r_surge=backtest(0.18,21,3,8,0.0,14,0.15,0,0)

print('='*95)
print('  COMBINED: No-Surge (Rise>15% in 14d) + Crash Protection')
print('='*95)
print('  BASELINE:       S={:.4f}  R={:.1f}%  MDD={:.1f}%  CM={:.3f}  Trd={:d}'.format(
    r_base['sh'],r_base['tr'],r_base['mdd'],r_base['calmar'],r_base['nt']))
print('  No-Surge only:  S={:.4f}  R={:.1f}%  MDD={:.1f}%  CM={:.3f}  Trd={:d}'.format(
    r_surge['sh'],r_surge['tr'],r_surge['mdd'],r_surge['calmar'],r_surge['nt']))
print()
hdr='  {:<30s} {:>7s} {:>8s} {:>6s} {:>7s} {:>6s} {:>5s} {:>5s} {:>5s} {:>5s}'.format(
    'Crash Protection','Sharpe','Return%','MDD%','Calmar','Trades','Win%','Crash','Trail','Surge')
print(hdr)
print('  '+'-'*85)

best=None
for crash_d in [7,10,14]:
    for crash_t in [-0.08,-0.10,-0.12,-0.15]:
        r=backtest(0.18,21,3,8,0.0,14,0.15,crash_d,crash_t)
        if r:
            tag=''
            if r['sh']>r_surge['sh']+0.002: tag=' SH+'
            if r['mdd']<r_surge['mdd']-0.5: tag+=' DD-'
            if best is None or r['sh']>best['sh']:best=r
            label='Drop>{}% in {}d'.format(int(abs(crash_t)*100),crash_d)
            print('  {:<30s} {:>7.4f} {:>7.1f}% {:>5.1f}% {:>7.3f} {:>6d} {:>4.0f}% {:>5d} {:>5d} {:>5d}{}'.format(
                label,r['sh'],r['tr'],r['mdd'],r['calmar'],r['nt'],r['wr'],r['crash'],r['trail'],r['surge'],tag))

print()
print('  BEST: S={:.4f} R={:.1f}% MDD={:.1f}% CM={:.3f}'.format(best['sh'],best['tr'],best['mdd'],best['calmar']))

# ---- DD Analysis on best config ----
print('\n'+'='*95)
print('  DD ANALYSIS: No-Surge(15% 14d) + Crash(Drop>15% 7d)')
print('='*95)

# Re-run with full details
cash=INIT;pos={};eq=[];trades=[];daily_pos=[]
idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
surge_d=14;surge_t=0.15;crash_d=7;crash_th=-0.15;trail=0.18
old_codes={c for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
for di,dt in enumerate(cd):
    for code,p in list(pos.items()):
        pk=px(code,dt,idx)
        if pk is None: continue
        if pk>p['peak']:p['peak']=pk
        loss=(pk-p['bp'])/p['bp'];hold_days=di-p['bi']
        crash_trig=(crash_d>0 and hold_days<=crash_d and loss<=crash_th)
        trail_trig=(pk<=p['peak']*(1-trail))
        if crash_trig or trail_trig:
            sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            pt=None
            bi=idx[code].get(p['bd'])
            if bi is not None and bi>=10:
                pp=stocks[code]['close'][max(0,bi-10)]
                if pp>0: pt=(stocks[code]['close'][bi]-pp)/pp
            trades.append({'code':code,'name':stocks[code]['name'],'star':code not in old_codes,
                'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,'pnl':p['shares']*(sp-p['bp']),
                'exit':('crash' if crash_trig else 'trail'),'pre_trend':pt,'hold_days':hold_days})
            del pos[code]
            continue
    if di%21==0:
        cand=[(c,FAC.get(c,{}).get(dt,float('nan'))) for c in stocks]
        cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
        if surge_d>0:
            filtered=[]
            for c,s in cand:
                di_c=idx[c].get(dt)
                if di_c is None or di_c<surge_d: filtered.append((c,s)); continue
                px_now=stocks[c]['close'][di_c];px_past=stocks[c]['close'][di_c-surge_d]
                if px_past>0 and (px_now-px_past)/px_past>=surge_t: continue
                filtered.append((c,s))
            cand=filtered
        cand.sort(key=lambda x:x[1],reverse=True)
        selected=[];sec_counts={}
        for c,s in cand:
            sec=sm.get(c,'');cnt=sec_counts.get(sec,0)
            if cnt>=3: continue
            if len(selected)>=8: break
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
                pt=None
                bi=idx[code].get(pos[code]['bd'])
                if bi is not None and bi>=10:
                    pp=stocks[code]['close'][max(0,bi-10)]
                    if pp>0: pt=(stocks[code]['close'][bi]-pp)/pp
                trades.append({'code':code,'name':stocks[code]['name'],'star':code not in old_codes,
                    'bd':pos[code]['bd'],'sd':dt,'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                    'pnl':pos[code]['shares']*(sp-pos[code]['bp']),'exit':'rebal','pre_trend':pt,
                    'hold_days':di-bi if bi else 0})
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
    pv=0;pl=[]
    for c,p in pos.items():
        pk=px(c,dt,idx)
        if pk is not None:
            pv+=p['shares']*pk
            pl.append({'code':c,'name':stocks[c]['name'],'sector':sm.get(c,'?'),'val':p['shares']*pk,'pnl':(pk-p['bp'])/p['bp']})
    eq.append(cash+pv);daily_pos.append({'date':dt,'equity':cash+pv,'pos':len(pos),'positions':pl})
ld=cd[-1]
for code,p in list(pos.items()):
    pk=px(code,ld,idx)
    if pk is not None:
        sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
        trades.append({'code':code,'name':stocks[code]['name'],'star':code not in old_codes,
            'bd':p['bd'],'sd':ld,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
            'pnl':p['shares']*(sp-p['bp']),'exit':'final','pre_trend':None,'hold_days':0})

losers=[t for t in trades if t['ret']<0]
print('\n  1. PRE-TREND:')
for tag,ts in [('ALL',trades),('WINNERS',[t for t in trades if t['ret']>0]),('LOSERS',losers)]:
    v=[t for t in ts if t.get('pre_trend') is not None]
    if not v: continue
    a=sum(t['pre_trend'] for t in v)/len(v)*100;f=sum(1 for t in v if t['pre_trend']<0)
    print('  {}: avg pre={:+.1f}% | fall={:.0f}% | rise={:.0f}% | n={}'.format(tag,a,f/len(v)*100,(len(v)-f)/len(v)*100,len(v)))

print('\n  2. EXIT BREAKDOWN:')
ex=defaultdict(list)
for t in trades: ex[t['exit']].append(t)
for e in ['rebal','trail','crash','final']:
    if e in ex:
        ts=ex[e];ar=sum(t['ret'] for t in ts)/len(ts)*100;wr=sum(1 for t in ts if t['ret']>0)/len(ts)*100
        print('  {}: {} trades | avg ret={:+.1f}% | win={:.0f}%'.format(e,len(ts),ar,wr))

print('\n  3. WORST 10 TRADES:')
for t in sorted(trades,key=lambda x:x['ret'])[:10]:
    pt=t.get('pre_trend');ps='pre={:+.1f}%'.format(pt*100) if pt is not None else 'pre=N/A'
    print('  {} {} | {}->{} | {:+5.1f}% | {}d | {} | {}'.format(t['code'],t['name'],t['bd'],t['sd'],t['ret']*100,t.get('hold_days',0),t['exit'],ps))

print('\n  4. DD PERIODS (>5%):')
peak=eq[0];dds=[]
in_dd=False;ds=0;db=0;dm=0
for i,v in enumerate(eq):
    if v>peak:peak=v
    d=(peak-v)/peak
    if d>0.05 and not in_dd: in_dd=True;ds=i;db=i;dm=d
    elif d>0.05 and in_dd and d>dm: db=i;dm=d
    elif d<=0.02 and in_dd: dds.append((cd[ds],cd[db],cd[i],i-ds,dm*100));in_dd=False
if in_dd: dds.append((cd[ds],cd[db],cd[-1],len(eq)-1-ds,dm*100))
dds.sort(key=lambda x:x[4],reverse=True)
for i,(s,b,e,days,dd) in enumerate(dds[:5]):
    print('  #{}: {} -> {} | {:.1f}% over {}d'.format(i+1,s,b,dd,days))

print('Done!')
