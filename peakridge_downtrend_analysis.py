"""Analyze whether drawdowns are from buying into declines or black swans"""
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

# Detailed backtest with pre-purchase analysis
cash=INIT;pos={};eq=[];trades=[]
idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
def px(c,d):
    di=idx[c].get(d);return stocks[c]['close'][di] if di is not None else None
def get_idx(c,d):
    return idx[c].get(d)

for di,dt in enumerate(cd):
    for code,p in list(pos.items()):
        pk=px(code,dt)
        if pk is None: continue
        if pk>p['peak']:p['peak']=pk
        if pk<=p['peak']*(1-0.18):
            sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            # Record pre-purchase and post-purchase context
            bi=get_idx(code,p['bd'])
            ci=get_idx(code,dt)
            pre_trend=None
            if bi is not None and bi>=10:
                pre_px=stocks[code]['close'][max(0,bi-10)]
                if pre_px>0:
                    pre_trend=(stocks[code]['close'][bi]-pre_px)/pre_px
            post_max_px=max(stocks[code]['close'][bi:ci+1]) if bi is not None and ci is not None else pk
            drawdown_path='crash' if (post_max_px-p['bp'])/p['bp']<-0.05 and (ci-bi)<10 else 'grind'
            trades.append({'code':code,'name':stocks[code]['name'],'star':code not in old_codes,
                'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                'pnl':p['shares']*(sp-p['bp']),'exit':'trail','bp':p['bp'],
                'pre_trend':pre_trend,'dd_path':drawdown_path,
                'factor_val':FAC[code].get(p['bd'],float('nan')),
                'hold_days':ci-bi if bi is not None and ci is not None else 0})
            del pos[code]
    if di%21==0:
        cand=[(c,FAC.get(c,{}).get(dt,float('nan'))) for c in stocks]
        cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
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
                pk=px(code,dt)
                if pk is None: continue
                sp=pk*(1-SLIP-S_FEE-STAX);cash+=pos[code]['shares']*sp
                bi=get_idx(code,pos[code]['bd']);ci=get_idx(code,dt)
                pre_trend=None
                if bi is not None and bi>=10:
                    pre_px=stocks[code]['close'][max(0,bi-10)]
                    if pre_px>0:
                        pre_trend=(stocks[code]['close'][bi]-pre_px)/pre_px
                post_max_px=max(stocks[code]['close'][bi:ci+1]) if bi is not None and ci is not None else pk
                drawdown_path='crash' if (post_max_px-p['bp'])/p['bp']<-0.05 and (ci-bi)<10 else 'grind'
                trades.append({'code':code,'name':stocks[code]['name'],'star':code not in old_codes,
                    'bd':pos[code]['bd'],'sd':dt,
                    'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                    'pnl':pos[code]['shares']*(sp-pos[code]['bp']),'exit':'rebal','bp':pos[code]['bp'],
                    'pre_trend':pre_trend,'dd_path':drawdown_path,
                    'factor_val':FAC[code].get(pos[code]['bd'],float('nan')),
                    'hold_days':ci-bi if bi is not None and ci is not None else 0})
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
        sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
        bi=get_idx(code,p['bd']);ci=get_idx(code,ld)
        pre_trend=None
        if bi is not None and bi>=10:
            pre_px=stocks[code]['close'][max(0,bi-10)]
            if pre_px>0: pre_trend=(stocks[code]['close'][bi]-pre_px)/pre_px
        trades.append({'code':code,'name':stocks[code]['name'],'star':code not in old_codes,
            'bd':p['bd'],'sd':ld,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
            'pnl':p['shares']*(sp-p['bp']),'exit':'final','bp':p['bp'],
            'pre_trend':pre_trend,'dd_path':'grind',
            'factor_val':FAC[code].get(p['bd'],float('nan')),
            'hold_days':ci-bi if bi is not None and ci is not None else 0})

# ============ ANALYSIS ============
print('='*95)
print('  DOWNTURN ANALYSIS: Accident vs Strategy Risk')
print('='*95)

# 1. Pre-purchase trend analysis
losers=[t for t in trades if t['ret']<0]
winners=[t for t in trades if t['ret']>0]

print('\n  ---- 1. PRE-PURCHASE TREND ----')
print('  Did we buy into already-falling stocks, or were crashes a surprise?')
print()
for tag,ts in [('ALL trades',trades),('WINNERS',winners),('LOSERS',losers)]:
    valid=[t for t in ts if t.get('pre_trend') is not None]
    if not valid: continue
    avg_pre=sum(t['pre_trend'] for t in valid)/len(valid)*100
    falling=sum(1 for t in valid if t['pre_trend']<0)
    rising=sum(1 for t in valid if t['pre_trend']>0)
    print('  {:<12s}: avg pre-trend={:+.1f}% | falling={:d} ({:.0f}%) | rising={:d} ({:.0f}%)'.format(
        tag,avg_pre,falling,falling/len(valid)*100,rising,rising/len(valid)*100))

# 2. By loss severity
print('\n  ---- 2. PRE-TREND BY LOSS SEVERITY ----')
brackets=[(-0.05,0,'-5%~0%'),(-0.10,-0.05,'-10%~-5%'),(-0.20,-0.10,'-20%~-10%'),(-99,-0.20,'<-20%')]
for lo,hi,label in brackets:
    sub=[t for t in losers if t['ret']>=lo and t['ret']<hi and t.get('pre_trend') is not None]
    if sub:
        avg_pre=sum(t['pre_trend'] for t in sub)/len(sub)*100
        fall_pct=sum(1 for t in sub if t['pre_trend']<0)/len(sub)*100
        print('  {:<12s}: {:d} trades | avg pre-trend={:+.1f}% | bought-falling={:.0f}%'.format(
            label,len(sub),avg_pre,fall_pct))

# 3. Factor value at purchase vs outcome
print('\n  ---- 3. FACTOR VALUE AT PURCHASE ----')
print('  Higher factor = stronger signal. Did bad trades have weak signals?')
for tag,ts in [('WINNERS',winners),('LOSERS',losers),('Worst 20%',sorted(losers,key=lambda x:x['ret'])[:len(losers)//5])]:
    valid=[t for t in ts if not math.isnan(t.get('factor_val',float('nan')))]
    if not valid: continue
    avg_fv=sum(t['factor_val'] for t in valid)/len(valid)
    avg_ret=sum(t['ret'] for t in valid)/len(valid)*100
    print('  {:<12s}: avg factor={:.2f} | avg ret={:+.1f}% | {:d} trades'.format(tag,avg_fv,avg_ret,len(valid)))

# 4. Crash vs grind
print('\n  ---- 4. CRASH vs GRIND ----')
crashes=[t for t in losers if t.get('dd_path')=='crash']
grinds=[t for t in losers if t.get('dd_path')=='grind']
for tag,ts in [('CRASH (<10d, -5%+)',crashes),('GRIND (slow decline)',grinds)]:
    if ts:
        avg_ret=sum(t['ret'] for t in ts)/len(ts)*100
        avg_hold=sum(t['hold_days'] for t in ts)/len(ts)
        trail_pct=sum(1 for t in ts if t['exit']=='trail')/len(ts)*100
        print('  {:<25s}: {:d} trades | avg ret={:+.1f}% | avg hold={:.0f}d | trail={:.0f}%'.format(
            tag,len(ts),avg_ret,avg_hold,trail_pct))

# 5. When we buy: market context
print('\n  ---- 5. BUY TIMING vs MARKET ----')
# Load HS300 for context
hs300_data=json.load(open('benchmarks/sh000300.json','r',encoding='utf-8'))
hs300_map={b['date']:b['close'] for b in hs300_data['bars']}
buy_dates=defaultdict(list)
for t in trades:
    bd=t['bd']
    if bd in cd:
        buy_dates[bd].append(t)
# Group by year, check if buying on down days or up days
for y in ['2021','2022','2023','2024','2025','2026']:
    yt=[t for t in trades if t['bd'][:4]==y]
    if not yt: continue
    # Market trend on buy dates
    mkt_ret=[]
    for t in yt:
        bd=t['bd']
        if bd in cd:
            bi=cd.index(bd)
            if bi>=5:
                mkt_ret.append((hs300_map.get(cd[bi],0)-hs300_map.get(cd[bi-5],0))/hs300_map.get(cd[bi-5],1)*100)
    avg_mkt=sum(mkt_ret)/len(mkt_ret) if mkt_ret else 0
    mkt_falling=sum(1 for r in mkt_ret if r<0)
    avg_ret=sum(t['ret'] for t in yt)/len(yt)*100
    print('  {}: {:d} buys | mkt-5d={:+.1f}% | mkt-falling={:.0f}% | avg-trade-ret={:+.1f}%'.format(
        y,len(yt),avg_mkt,mkt_falling/len(mkt_ret)*100 if mkt_ret else 0,avg_ret))

# 6. Trail exits: when did we buy into them?
print('\n  ---- 6. TRAIL EXITS: PRE-PURCHASE DECLINE ANALYSIS ----')
trail_losers=[t for t in trades if t['exit']=='trail' and t.get('pre_trend') is not None]
if trail_losers:
    bought_falling=sum(1 for t in trail_losers if t['pre_trend']<0)
    bought_rising=sum(1 for t in trail_losers if t['pre_trend']>0)
    avg_pre=sum(t['pre_trend'] for t in trail_losers)/len(trail_losers)*100
    avg_hold=sum(t['hold_days'] for t in trail_losers)/len(trail_losers)
    avg_fv=sum(t['factor_val'] for t in trail_losers if not math.isnan(t['factor_val']))/len([t for t in trail_losers if not math.isnan(t['factor_val'])])
    print('  {:d} trail exits | avg pre-trend={:+.1f}% | bought-falling={:.0f}% | bought-rising={:.0f}%'.format(
        len(trail_losers),avg_pre,bought_falling/len(trail_losers)*100,bought_rising/len(trail_losers)*100))
    print('  avg hold={:.0f}d | avg factor at purchase={:.2f}'.format(avg_hold,avg_fv))
    # Factor distribution
    fv_high=sum(1 for t in trail_losers if not math.isnan(t['factor_val']) and t['factor_val']>=2.0)
    fv_mid=sum(1 for t in trail_losers if not math.isnan(t['factor_val']) and 1.0<=t['factor_val']<2.0)
    fv_low=sum(1 for t in trail_losers if not math.isnan(t['factor_val']) and t['factor_val']<1.0)
    print('  factor>=2.0: {:d} | 1.0-2.0: {:d} | <1.0: {:d}'.format(fv_high,fv_mid,fv_low))

# 7. Key insight: consecutive losers
print('\n  ---- 7. BOTTOM LINE ----')
all_valid=[t for t in losers if t.get('pre_trend') is not None]
bought_falling=sum(1 for t in all_valid if t['pre_trend']<0)
total_losers=len(all_valid)
print('  Of all losing trades: {:.0f}% were bought into already-falling stocks'.format(
    bought_falling/total_losers*100 if total_losers>0 else 0))
print('  Of all losing trades: {:.0f}% were bought into rising stocks (crashed after)'.format(
    (1-bought_falling/total_losers)*100 if total_losers>0 else 0))

print('\nDone!')
