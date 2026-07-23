"""Dip-buying analysis with optimized protection"""
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
old_codes={c for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
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

# Full backtest with all details
cash=INIT;pos={};eq=[];trades=[]
idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
surge_d=14;surge_t=0.15;crash_d=7;crash_th=-0.15;trail=0.18

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
            pt5=None;pt10=None;pt20=None;bi=idx[code].get(p['bd'])
            if bi is not None:
                if bi>=5:
                    pp=stocks[code]['close'][max(0,bi-5)]
                    if pp>0: pt5=(stocks[code]['close'][bi]-pp)/pp
                if bi>=10:
                    pp=stocks[code]['close'][max(0,bi-10)]
                    if pp>0: pt10=(stocks[code]['close'][bi]-pp)/pp
                if bi>=20:
                    pp=stocks[code]['close'][max(0,bi-20)]
                    if pp>0: pt20=(stocks[code]['close'][bi]-pp)/pp
            trades.append({'code':code,'name':stocks[code]['name'],'star':code not in old_codes,
                'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                'pnl':p['shares']*(sp-p['bp']),'exit':('crash' if crash_trig else 'trail'),
                'pre_trend_5':pt5,'pre_trend_10':pt10,'pre_trend_20':pt20,
                'factor_val':FAC.get(code,{}).get(p['bd'],float('nan')),
                'bp':p['bp'],'hold_days':hold_days})
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
                pt5=None;pt10=None;pt20=None;bi=idx[code].get(pos[code]['bd'])
                if bi is not None:
                    if bi>=5:
                        pp=stocks[code]['close'][max(0,bi-5)]
                        if pp>0: pt5=(stocks[code]['close'][bi]-pp)/pp
                    if bi>=10:
                        pp=stocks[code]['close'][max(0,bi-10)]
                        if pp>0: pt10=(stocks[code]['close'][bi]-pp)/pp
                    if bi>=20:
                        pp=stocks[code]['close'][max(0,bi-20)]
                        if pp>0: pt20=(stocks[code]['close'][bi]-pp)/pp
                trades.append({'code':code,'name':stocks[code]['name'],'star':code not in old_codes,
                    'bd':pos[code]['bd'],'sd':dt,
                    'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                    'pnl':pos[code]['shares']*(sp-pos[code]['bp']),'exit':'rebal',
                    'pre_trend_5':pt5,'pre_trend_10':pt10,'pre_trend_20':pt20,
                    'factor_val':FAC.get(code,{}).get(pos[code]['bd'],float('nan')),
                    'bp':pos[code]['bp'],'hold_days':di-bi if bi else 0})
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

# ---- ANALYSIS ----
valid=[t for t in trades if t.get('pre_trend_10') is not None]
print('='*90)
print('  DIP-BUYING ANALYSIS')
print('='*90)

# 1. Dip vs Rise split
dips=[t for t in valid if t['pre_trend_10']<0]
rises=[t for t in valid if t['pre_trend_10']>0]
print('\n  ##### 1. OVERVIEW #####')
for tag,ts in [('ALL',valid),('DIP (pre<0)',dips),('RISE (pre>0)',rises)]:
    if not ts: continue
    avg_r=sum(t['ret'] for t in ts)/len(ts)*100
    avg_pre=sum(t['pre_trend_10'] for t in ts)/len(ts)*100
    wr=sum(1 for t in ts if t['ret']>0)/len(ts)*100
    trail_p=sum(1 for t in ts if t['exit'] in ('trail','crash'))/len(ts)*100
    avg_fv=sum(t['factor_val'] for t in ts if not math.isnan(t['factor_val']))/len([t for t in ts if not math.isnan(t['factor_val'])])
    print('  {:<15s}: {:d} trades | pre10={:+.1f}% | ret={:+.1f}% | WR={:.0f}% | stop={:.0f}% | fv={:.2f}'.format(
        tag,len(ts),avg_pre,avg_r,wr,trail_p,avg_fv))

# 2. Dip severity
print('\n  ##### 2. DIP SEVERITY x OUTCOME #####')
bands=[(-0.15,-0.08,'Deep dip<-8%'),(-0.08,-0.04,'Dip -8%~-4%'),(-0.04,-0.02,'Dip -4%~-2%'),(-0.02,0,'Shallow -2%~0%')]
for lo,hi,label in bands:
    sub=[t for t in dips if lo<=t['pre_trend_10']<hi]
    if not sub: continue
    avg_r=sum(t['ret'] for t in sub)/len(sub)*100
    wr=sum(1 for t in sub if t['ret']>0)/len(sub)*100
    stop_p=sum(1 for t in sub if t['exit'] in ('trail','crash'))/len(sub)*100
    avg_fv=sum(t['factor_val'] for t in sub if not math.isnan(t['factor_val']))/len([t for t in sub if not math.isnan(t['factor_val'])])
    avg_hd=sum(t['hold_days'] for t in sub)/len(sub)
    print('  {:<15s}: {:d} trades | ret={:+.1f}% | WR={:.0f}% | stop={:.0f}% | hold={:.0f}d | fv={:.2f}'.format(
        label,len(sub),avg_r,wr,stop_p,avg_hd,avg_fv))

# 3. Dip losers: when do they fail?
dip_losers=[t for t in dips if t['ret']<0]
dip_winners=[t for t in dips if t['ret']>0]
print('\n  ##### 3. DIP LOSERS vs WINNERS #####')
for tag,ts in [('Dip WINNERS',dip_winners),('Dip LOSERS',dip_losers)]:
    if not ts: continue
    avg_r=sum(t['ret'] for t in ts)/len(ts)*100
    avg_pre=sum(t['pre_trend_10'] for t in ts)/len(ts)*100
    avg_fv=sum(t['factor_val'] for t in ts if not math.isnan(t['factor_val']))/len([t for t in ts if not math.isnan(t['factor_val'])])
    avg_hd=sum(t['hold_days'] for t in ts)/len(ts)
    trail_p=sum(1 for t in ts if t['exit'] in ('trail','crash'))/len(ts)*100
    print('  {:<15s}: {:d} trades | pre={:+.1f}% | ret={:+.1f}% | hold={:.0f}d | stop={:.0f}% | fv={:.2f}'.format(
        tag,len(ts),avg_pre,avg_r,avg_hd,trail_p,avg_fv))

# 4. Factor value of dip losers
print('\n  ##### 4. FACTOR VALUE x DIP OUTCOME #####')
fv_bands=[(0,1,'fv<1'),(1,2,'fv 1-2'),(2,3,'fv 2-3'),(3,5,'fv 3-5'),(5,99,'fv>5')]
for lo,hi,label in fv_bands:
    sub=[t for t in dips if not math.isnan(t['factor_val']) and lo<=t['factor_val']<hi]
    if not sub:
        sub=[t for t in dips if not math.isnan(t['factor_val']) and t['factor_val']>=lo]
    if not sub: continue
    avg_r=sum(t['ret'] for t in sub)/len(sub)*100
    wr=sum(1 for t in sub if t['ret']>0)/len(sub)*100
    print('  {:<10s}: {:d} dips | avg ret={:+.1f}% | WR={:.0f}%'.format(label,len(sub),avg_r,wr))

# 5. Dip by sector
print('\n  ##### 5. DIP WIN RATE BY SECTOR (>5 dips) #####')
sec_dips=defaultdict(list)
for t in dips: sec_dips[sm.get(t['code'],'?')].append(t)
for sec in sorted(sec_dips,key=lambda x:sum(t['ret'] for t in sec_dips[x])/len(sec_dips[x])):
    ts=sec_dips[sec]
    if len(ts)<5: continue
    avg_r=sum(t['ret'] for t in ts)/len(ts)*100
    wr=sum(1 for t in ts if t['ret']>0)/len(ts)*100
    avg_pre=sum(t['pre_trend_10'] for t in ts)/len(ts)*100
    print('  {:<25s}: {:d} dips | pre={:+.1f}% | ret={:+.1f}% | WR={:.0f}%'.format(sec,len(ts),avg_pre,avg_r,wr))

# 6. Dip by year
print('\n  ##### 6. DIP PERFORMANCE BY YEAR #####')
for y in ['2021','2022','2023','2024','2025','2026']:
    yd=[t for t in dips if t['bd'][:4]==y]
    if not yd: continue
    avg_r=sum(t['ret'] for t in yd)/len(yd)*100
    wr=sum(1 for t in yd if t['ret']>0)/len(yd)*100
    stop_p=sum(1 for t in yd if t['exit'] in ('trail','crash'))/len(yd)*100
    avg_fv=sum(t['factor_val'] for t in yd if not math.isnan(t['factor_val']))/len([t for t in yd if not math.isnan(t['factor_val'])])
    print('  {}: {:d} dips | ret={:+.1f}% | WR={:.0f}% | stop={:.0f}% | fv={:.2f}'.format(
        y,len(yd),avg_r,wr,stop_p,avg_fv))

# 7. Summary
print('\n  ##### 7. SUMMARY #####')
dip_ratio=len(dips)/len(valid)*100
dip_lose_ratio=len(dip_losers)/len(dips)*100 if dips else 0
rise_lose_ratio=len([t for t in rises if t['ret']<0])/len(rises)*100 if rises else 0
print('  Strategy is now {:.0f}% dip-buying (was 50% before protection)'.format(dip_ratio))
print('  Dip-buy win rate: {:.0f}% | Rise-buy win rate: {:.0f}%'.format(100-dip_lose_ratio,100-rise_lose_ratio))
print('  Avg dip loss severity: {:.1f}% | Avg rise loss severity: {:.1f}%'.format(
    sum(t['ret'] for t in dip_losers)/len(dip_losers)*100 if dip_losers else 0,
    sum(t['ret'] for t in [r for r in rises if r['ret']<0])/len([r for r in rises if r['ret']<0])*100 if rises else 0))
print('Done!')
