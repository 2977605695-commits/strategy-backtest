"""峰岭因子 · 回撤归因分析"""
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

# Detailed backtest with position-level tracking
cash=INIT;pos={};eq=[];trades=[];daily_detail=[]
idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
def px(c,d):
    di=idx[c].get(d);return stocks[c]['close'][di] if di is not None else None

for di,dt in enumerate(cd):
    for code,p in list(pos.items()):
        pk=px(code,dt)
        if pk is None: continue
        if pk>p['peak']:p['peak']=pk
        if pk<=p['peak']*(1-0.18):
            sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'code':code,'name':stocks[code]['name'],'star':code not in old_codes,
                'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                'pnl':p['shares']*(sp-p['bp']),'exit':'trail','bp':p['bp']})
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
                trades.append({'code':code,'name':stocks[code]['name'],'star':code not in old_codes,
                    'bd':pos[code]['bd'],'sd':dt,
                    'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                    'pnl':pos[code]['shares']*(sp-pos[code]['bp']),'exit':'rebal','bp':pos[code]['bp']})
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
    pv=0;pos_list=[]
    for c,p in pos.items():
        pk=px(c,dt)
        if pk is not None:
            pv+=p['shares']*pk
            pos_list.append({'code':c,'name':stocks[c]['name'],'star':c not in old_codes,
                'sector':sm.get(c,'?'),'val':p['shares']*pk,'pnl':(pk-p['bp'])/p['bp']})
    eq.append(cash+pv)
    daily_detail.append({'date':dt,'equity':cash+pv,'cash':cash,'pos_val':pv,'n_pos':len(pos),'positions':pos_list})

ld=cd[-1]
for code,p in list(pos.items()):
    pk=px(code,ld)
    if pk is not None:
        sp=pk*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
        trades.append({'code':code,'name':stocks[code]['name'],'star':code not in old_codes,
            'bd':p['bd'],'sd':ld,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
            'pnl':p['shares']*(sp-p['bp']),'exit':'final','bp':p['bp']})

# ============ DD ANALYSIS ============
print('='*95)
print('  DRAWDOWN ATTRIBUTION ANALYSIS')
print('  Config: K=1.0 LB=21 Trail=18% Pos=8 Sec=relaxed (BEST_Pos8)')
print('='*95)

# 1. Find all DD periods (>5% from peak)
peak=eq[0];dd_periods=[]
in_dd=False;dd_start=0;dd_bottom=0;dd_max=0
for i,v in enumerate(eq):
    if v>peak:peak=v
    dd=(peak-v)/peak
    if dd>0.05 and not in_dd:
        in_dd=True;dd_start=i;dd_bottom=i;dd_max=dd
    elif dd>0.05 and in_dd:
        if dd>dd_max:dd_bottom=i;dd_max=dd
    elif dd<=0.02 and in_dd:
        dd_periods.append({'start':cd[dd_start],'end':cd[i],'bottom':cd[dd_bottom],
            'start_idx':dd_start,'end_idx':i,'bottom_idx':dd_bottom,
            'max_dd':dd_max,'start_eq':eq[dd_start],'bottom_eq':eq[dd_bottom]})
        in_dd=False
if in_dd:
    dd_periods.append({'start':cd[dd_start],'end':cd[-1],'bottom':cd[dd_bottom],
        'start_idx':dd_start,'end_idx':len(eq)-1,'bottom_idx':dd_bottom,
        'max_dd':dd_max,'start_eq':eq[dd_start],'bottom_eq':eq[dd_bottom]})

print('\n  Found %d drawdown periods (>5%% from peak):' % len(dd_periods))
print('  {:>3s}  {:<12s} {:<12s} {:<12s} {:>6s} {:>8s} {:>10s} {:>8s}'.format(
    '#','Start','Bottom','End','Days','MaxDD%','Loss','#Pos'))
print('  '+'-'*80)

for i,dp in enumerate(dd_periods):
    days=dp['end_idx']-dp['start_idx']
    loss=dp['bottom_eq']-dp['start_eq']
    avg_pos=sum(daily_detail[j]['n_pos'] for j in range(dp['start_idx'],dp['end_idx']+1))/(dp['end_idx']-dp['start_idx']+1)
    print('  {:>3d}  {:<12s} {:<12s} {:<12s} {:>4d}d {:>7.1f}% {:>10,.0f} {:>7.1f}'.format(
        i+1,dp['start'],dp['bottom'],dp['end'],days,dp['max_dd']*100,loss,avg_pos))

# 2. For each major DD, what positions were held?
print('\n'+'='*95)
print('  DD PERIOD COMPOSITION')
print('='*95)

for i,dp in enumerate(dd_periods[:5]):  # top 5
    print('\n  --- DD #%d: %s -> %s (%.1f%%, %dd) ---' % (
        i+1,dp['start'],dp['bottom'],dp['max_dd']*100,dp['bottom_idx']-dp['start_idx']))

    # Positions held at DD bottom
    bottom_pos=daily_detail[dp['bottom_idx']]['positions']
    if bottom_pos:
        bottom_pos.sort(key=lambda x:x['pnl'],reverse=False)
        print('  Positions at bottom (worst first):')
        for p in bottom_pos[:5]:
            print('    {:>6s} {:<10s} {:>20s} | PnL={:+.1f}% | val={:,.0f} | {}'.format(
                p['code'],p['name'],p['sector'],p['pnl']*100,p['val'],'STAR' if p['star'] else 'old'))

    # Trades closed during DD
    dd_trades=[t for t in trades if t['sd']>=dp['start'] and t['sd']<=dp['end'] and t['ret']<0]
    if dd_trades:
        dd_trades.sort(key=lambda x:x['ret'])
        total_loss=sum(t['pnl'] for t in dd_trades)
        print('  Losers closed during DD (worst 5):')
        for t in dd_trades[:5]:
            print('    {:>6s} {:<10s} | {} -> {} | {:+5.1f}% | PnL={:,.0f} | {} | {}'.format(
                t['code'],t['name'],t['bd'],t['sd'],t['ret']*100,t['pnl'],t['exit'],'STAR' if t['star'] else 'old'))
        print('  Total loss from DD exits: {:,.0f} ({:.1f}% of peak equity)'.format(total_loss,abs(total_loss)/dp['start_eq']*100))

# 3. Worst trades overall
print('\n'+'='*95)
print('  WORST 20 TRADES (all time)')
print('='*95)
worst=sorted(trades,key=lambda x:x['ret'])[:20]
print('  {:>6s} {:<10s} {:<12s} {:<12s} {:>7s} {:>12s} {:>6s} {:>4s} {:>6s}'.format(
    'Code','Name','Buy','Sell','Ret%','PnL','Exit','STAR','Sector'))
print('  '+'-'*85)
for t in worst:
    print('  {:>6s} {:<10s} {:<12s} {:<12s} {:>+6.1f}% {:>12,.0f} {:>6s} {:>4s} {:>6s}'.format(
        t['code'],t['name'],t['bd'],t['sd'],t['ret']*100,t['pnl'],t['exit'],
        '*' if t['star'] else '',sm.get(t['code'],'?')[:6]))

# 4. Trail exit analysis
print('\n'+'='*95)
print('  TRAIL STOP ANALYSIS')
print('='*95)
trail_trades=[t for t in trades if t['exit']=='trail']
print('  Total trail exits: %d (%.1f%% of all trades)' % (len(trail_trades),len(trail_trades)/len(trades)*100))
print('  Avg trail loss: {:.1f}%'.format(sum(t['ret'] for t in trail_trades)/len(trail_trades)*100 if trail_trades else 0))
print('  Trail PnL total: {:,.0f}'.format(sum(t['pnl'] for t in trail_trades)))

# Trail exits by year
print('\n  Trail exits by year:')
for y in ['2021','2022','2023','2024','2025','2026']:
    yt=[t for t in trail_trades if t['sd'][:4]==y]
    if yt:
        avg_r=sum(t['ret'] for t in yt)/len(yt)*100
        print('    %s: %d exits, avg ret=%+.1f%%, total PnL=%,.0f' % (y,len(yt),avg_r,sum(t['pnl'] for t in yt)))

# Trail exits by stock type
star_trail=[t for t in trail_trades if t['star']]
old_trail=[t for t in trail_trades if not t['star']]
print('\n  By stock type:')
print('    STAR: %d exits, avg=%+.1f%%, total=%,.0f' % (len(star_trail),
    sum(t['ret'] for t in star_trail)/len(star_trail)*100 if star_trail else 0,
    sum(t['pnl'] for t in star_trail)))
print('    Old:  %d exits, avg=%+.1f%%, total=%,.0f' % (len(old_trail),
    sum(t['ret'] for t in old_trail)/len(old_trail)*100 if old_trail else 0,
    sum(t['pnl'] for t in old_trail)))

# 5. Sector-level DD contribution
print('\n'+'='*95)
print('  SECTOR CONTRIBUTION TO LOSSES')
print('='*95)
sector_loss=defaultdict(lambda:{'cnt':0,'pnl':0.0,'trail_cnt':0,'trail_pnl':0.0})
for t in trades:
    sec=sm.get(t['code'],'?')
    sector_loss[sec]['cnt']+=1;sector_loss[sec]['pnl']+=t['pnl']
    if t['exit']=='trail':
        sector_loss[sec]['trail_cnt']+=1;sector_loss[sec]['trail_pnl']+=t['pnl']

print('  {:<25s} {:>5s} {:>12s} {:>5s} {:>12s}'.format('Sector','Trades','Total_PnL','Trail','Trail_PnL'))
print('  '+'-'*65)
for sec in sorted(sector_loss,key=lambda x:sector_loss[x]['pnl']):
    d=sector_loss[sec]
    if d['cnt']>=3:
        print('  {:<25s} {:>5d} {:>12,.0f} {:>5d} {:>12,.0f}'.format(sec,d['cnt'],d['pnl'],d['trail_cnt'],d['trail_pnl']))

# 6. Concentration during DD
print('\n'+'='*95)
print('  CONCENTRATION RISK DURING WORST DD')
print('='*95)
worst_dd=max(dd_periods,key=lambda x:x['max_dd'])
print('  Worst DD: %s -> %s (%.1f%%)' % (worst_dd['start'],worst_dd['bottom'],worst_dd['max_dd']*100))

# Sector concentration at bottom
bottom_pos=daily_detail[worst_dd['bottom_idx']]['positions']
sec_conc=defaultdict(lambda:{'val':0.0,'cnt':0})
for p in bottom_pos:
    sec=sm.get(p['code'],'?')
    sec_conc[sec]['val']+=p['val'];sec_conc[sec]['cnt']+=1
total_val_bottom=sum(d['val'] for d in sec_conc.values())
print('  Sector concentration at DD bottom (total pos value: {:,.0f}):'.format(total_val_bottom))
for sec in sorted(sec_conc,key=lambda x:sec_conc[x]['val'],reverse=True):
    d=sec_conc[sec];pct=d['val']/total_val_bottom*100 if total_val_bottom>0 else 0
    bar='#'*int(pct/2)
    print('    {:<25s} {:>2d} stocks {:>5.1f}% {:>12,.0f} {}'.format(sec,d['cnt'],pct,d['val'],bar))

# Star vs old at bottom
star_val=sum(p['val'] for p in bottom_pos if p['star'])
old_val=sum(p['val'] for p in bottom_pos if not p['star'])
print('  STAR vs Old at bottom: STAR={:,.0f} ({:.1f}%) Old={:,.0f} ({:.1f}%)'.format(
    star_val,star_val/total_val_bottom*100 if total_val_bottom>0 else 0,
    old_val,old_val/total_val_bottom*100 if total_val_bottom>0 else 0))

print('\nDone!')
