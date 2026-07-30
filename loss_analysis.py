"""Single-Position Rotation · Loss Analysis"""
import json,os,sys,io,math
from collections import defaultdict
from datetime import datetime
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=10_000_000;MAX_POS=1;TRAIL=0.05
F_MA=6;S_MA=15;SL_MA=8

ETF_CODES=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
           '588200','159995','512480','515880','515050','159819','159992','512010',
           '518880','159937','513180','513050','513100','159509','588000','588220',
           '510300','159915','510050','511010','511260','510880','512890','159301']

def load_all():
    etfs={}
    for code in ETF_CODES:
        path=os.path.join(DATA_DIR,'etf_'+code+'.json')
        if not os.path.exists(path):continue
        d=json.load(open(path,encoding='utf-8'))
        bars=[]
        for b in d['bars']:
            dt=b['date']
            if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            bars.append({'date':dt,'close':float(b['close']),'high':float(b.get('high',b['close']))})
        etfs[code]={'name':d['name'],'first_date':bars[0]['date'],'bars':bars}
    return etfs

def ma(data,w):
    m=[];n=len(data)
    for i in range(n):
        if i<w-1:m.append(float('nan'))
        else:m.append(sum(data[i-w+1:i+1])/w)
    return m

def slope(ms,lb):
    s=[float('nan')]*len(ms)
    for i in range(len(ms)):
        if i<lb:continue
        ys=ms[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys):continue
        n=len(ys);sx=sy=sxy=sxx=0
        for j,y in enumerate(ys):sx+=j;sy+=y;sxy+=j*y;sxx+=j*j
        d=n*sxx-sx*sx
        if d>0:s[i]=(n*sxy-sx*sy)/d/ms[i] if ms[i]>0 else 0
    return s

def gen_sigs(bars):
    c=[b['close'] for b in bars];n=len(bars)
    mf=ma(c,F_MA);ms=ma(c,S_MA);msl=ma(c,SL_MA);slo=slope(msl,max(SL_MA//2,3))
    dates=[b['date'] for b in bars]
    trnd={};rat={}
    for i in range(n):
        d=dates[i]
        if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
            sok=not math.isnan(slo[i]) and slo[i]>0
            trnd[d]=mf[i]>ms[i] and sok;rat[d]=mf[i]/ms[i]
        else:trnd[d]=False;rat[d]=1.0
    return{'trend':trnd,'ratio':rat}

def run_full(etfs,all_sigs):
    codes=sorted(etfs.keys())
    dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    first_dates={c:etfs[c]['first_date'] for c in codes}
    ad=set()
    for c in codes:ad.update(dm[c].keys())
    all_dates=sorted(ad)
    cash=INIT;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';trades=[];dvs=[]
    daily_holding=[]

    for d in all_dates:
        avail=[c for c in codes if first_dates[c]<=d]
        if pos_code:
            bar=dm[pos_code].get(d)
            if bar:
                px=bar['close']
                if px>peak:peak=px
                trend_on=all_sigs[pos_code]['trend'].get(d,False)
                er=None
                if px<=peak*(1-TRAIL):er='trail'
                elif not trend_on:er='off'
                if er:
                    sell_val=shares*px;pnl=sell_val-shares*bp
                    # Find entry bar for contextual analysis
                    entry_bar=dm[pos_code].get(entry_d)
                    entry_ma_info=''
                    if entry_bar:
                        entry_ma_f=all_sigs[pos_code].get('ma_f',{}).get(entry_d,0)
                    trades.append({
                        'code':pos_code,'b':entry_d,'s':d,'bp':bp,'sp':px,
                        'r':(px-bp)/bp,'pnl':pnl,'e':er,
                        'peak':peak,'peak_r':(peak-bp)/bp,
                        'days':(datetime.strptime(d,'%Y-%m-%d')-datetime.strptime(entry_d,'%Y-%m-%d')).days
                    })
                    cash=sell_val;pos_code=None;shares=0.0;bp=0.0;peak=0.0
        if not pos_code and cash>0:
            cands=[]
            for c in avail:
                trend_on=all_sigs[c]['trend'].get(d,False)
                if trend_on:
                    bar=dm[c].get(d);cands.append((c,all_sigs[c]['ratio'].get(d,1.0),bar['close'] if bar else 0))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px=cands[0]
                shares=cash/px;bp=px;peak=px;pos_code=c;entry_d=d;cash=0.0
        pos_val=shares*dm[pos_code].get(d,{}).get('close',0) if pos_code else 0
        dvs.append(cash+pos_val)
        daily_holding.append((d,pos_code))

    if pos_code:
        bar=dm[pos_code].get(all_dates[-1])
        if bar:
            px=bar['close'];sell_val=shares*px;pnl=sell_val-shares*bp
            trades.append({
                'code':pos_code,'b':entry_d,'s':all_dates[-1],'bp':bp,'sp':px,
                'r':(px-bp)/bp,'pnl':pnl,'e':'final',
                'peak':peak,'peak_r':(peak-bp)/bp,
                'days':(datetime.strptime(all_dates[-1],'%Y-%m-%d')-datetime.strptime(entry_d,'%Y-%m-%d')).days
            })
    return trades,dvs,daily_holding

def main():
    etfs_all=load_all()
    print(str(len(etfs_all))+' ETFs')

    all_sigs={}
    for code in etfs_all:all_sigs[code]=gen_sigs(etfs_all[code]['bars'])
    trades,dvs,daily_holding=run_full(etfs_all,all_sigs)

    sell_tr=[t for t in trades if t['e'] in('trail','off','final')]
    wins=[t for t in sell_tr if t['r']>0]
    losses=[t for t in sell_tr if t['r']<=0]

    print('\n'+'='*110)
    print('  LOSS ANALYSIS: '+str(len(sell_tr))+' total trades, '+str(len(wins))+' wins, '+str(len(losses))+' losses ('+str(round(len(losses)/len(sell_tr)*100))+'%)')
    print('='*110)

    # ===== 1. Loss by category =====
    print('\n\n  1. LOSS BY EXIT TYPE')
    trail_losses=[t for t in losses if t['e']=='trail']
    off_losses=[t for t in losses if t['e']=='off']
    final_losses=[t for t in losses if t['e']=='final']
    for label,ls in [('Trail止损',trail_losses),('趋势转空',off_losses),('期末平仓',final_losses)]:
        if ls:
            avg_r=sum(t['r'] for t in ls)/len(ls)
            avg_pnl=sum(t['pnl'] for t in ls)/len(ls)
            print('  %s: %d笔  均收益=%.2f%%  均亏损=%s  总亏损=%s'%(
                label,len(ls),avg_r*100,str(int(avg_pnl)),str(int(sum(t['pnl'] for t in ls)))))

    # ===== 2. Loss by ETF =====
    print('\n\n  2. LOSS BY ETF')
    etf_losses=defaultdict(list)
    for t in losses:etf_losses[t['code']].append(t)
    ranked=sorted(etf_losses.items(),key=lambda x:sum(t['pnl'] for t in x[1]))
    print('  %-8s %-18s %5s %8s %8s %10s'%('Code','Name','N','AvgRet','AvgPeak','TotalLoss'))
    for code,ls in ranked:
        name=etfs_all[code]['name'];n=len(ls)
        avg_r=sum(t['r'] for t in ls)/n
        avg_pk=sum(t['peak_r'] for t in ls)/n
        total=sum(t['pnl'] for t in ls)
        print('  %-8s %-18s %5d %7.2f%% %7.2f%% %10s'%(code,name,n,avg_r*100,avg_pk*100,str(int(total))))

    # ===== 3. Loss by year =====
    print('\n\n  3. LOSS BY YEAR')
    yr_losses=defaultdict(list)
    for t in losses:yr_losses[t['b'][:4]].append(t)
    yr_wins=defaultdict(list)
    for t in wins:yr_wins[t['b'][:4]].append(t)
    for y in sorted(set(list(yr_losses.keys())+list(yr_wins.keys()))):
        ls=yr_losses[y];ws=yr_wins[y]
        n_all=len(ls)+len(ws)
        wr=len(ws)/n_all*100 if n_all>0 else 0
        if ls:
            avg_l=sum(t['r'] for t in ls)/len(ls)
            total_l=sum(t['pnl'] for t in ls)
        else:avg_l=0;total_l=0
        if ws:
            avg_w=sum(t['r'] for t in ws)/len(ws)
            total_w=sum(t['pnl'] for t in ws)
        else:avg_w=0;total_w=0
        net=total_w+total_l
        print('  %s: %d笔 (%d胜/%d负=%.0f%%)  胜均=%.2f%%  负均=%.2f%%  净PnL=%s'%(
            y,n_all,len(ws),len(ls),wr,avg_w*100,avg_l*100,str(int(net))))

    # ===== 4. Loss pattern: holding days =====
    print('\n\n  4. HOLDING DAYS ANALYSIS')
    win_days=[t['days'] for t in wins]
    loss_days=[t['days'] for t in losses]
    for label,ds in [('Wins',win_days),('Losses',loss_days)]:
        if ds:
            avg_d=sum(ds)/len(ds);med_d=sorted(ds)[len(ds)//2]
            print('  %s: avg=%d天  median=%d天  min=%d天  max=%d天  <5天=%d'%(
                label,avg_d,med_d,min(ds),max(ds),sum(1 for d in ds if d<5)))
    # Quick-flip losses (held <5d)
    quick_losses=[t for t in losses if t['days']<5]
    print('\n  Quick-flip losses (<5 days): %d / %d (%.0f%%)'%(len(quick_losses),len(losses),len(quick_losses)/len(losses)*100))
    if quick_losses:
        avg_qr=sum(t['r'] for t in quick_losses)/len(quick_losses)
        print('  Avg quick-flip loss: %.2f%%'%(avg_qr*100))

    # ===== 5. Peak-to-exit drawdown =====
    print('\n\n  5. PEAK-TO-EXIT DRAWDOWN')
    # How much did losses give back from their peak?
    for t in losses:
        pk_to_exit=(t['peak']-t['sp'])/t['peak']
        t['pk_dd']=pk_to_exit
    avg_pk_dd=sum(t['pk_dd'] for t in losses)/len(losses)
    print('  Avg peak-to-exit DD in losing trades: %.2f%%'%(avg_pk_dd*100))

    # Trail-losses: did they hit trail or trend_off?
    trail_l=[t for t in losses if t['e']=='trail']
    off_l=[t for t in losses if t['e']=='off']
    if trail_l:
        avg_trail_r=sum(t['r'] for t in trail_l)/len(trail_l)
        print('  Trail-stop losses: %d笔  avg ret=%.2f%%'%(len(trail_l),avg_trail_r*100))
    if off_l:
        avg_off_r=sum(t['r'] for t in off_l)/len(off_l)
        print('  Trend-off losses:  %d笔  avg ret=%.2f%%'%(len(off_l),avg_off_r*100))

    # ===== 6. MA state at entry for losing trades =====
    print('\n\n  6. ROOT CAUSE ANALYSIS')
    print('  '+'='*80)

    # Check: did losing trades enter during strong MA setup or weak?
    # We'll look at the ratio at entry
    for label,ls in [('ALL LOSSES',losses),('TRAIL LOSSES',trail_losses),('TREND-OFF LOSSES',off_losses)]:
        if not ls:continue
        # Check if losses cluster in time
        date_clusters=[]
        dates=[t['b'] for t in ls]
        dates.sort()
        streak=1;max_cluster=1;cluster_start=dates[0]
        for i in range(1,len(dates)):
            d1=datetime.strptime(dates[i-1],'%Y-%m-%d')
            d2=datetime.strptime(dates[i],'%Y-%m-%d')
            if (d2-d1).days<=30:streak+=1
            else:
                if streak>max_cluster:max_cluster=streak
                streak=1
        if streak>max_cluster:max_cluster=streak
        print('\n  %s:'%label)
        print('    Count=%d  Max cluster (30d window)=%d'%(len(ls),max_cluster))
        # Show ETF distribution
        etf_cnt=defaultdict(int)
        for t in ls:etf_cnt[t['code']]+=1
        top=','.join('%s(%d)'%(k,v) for k,v in sorted(etf_cnt.items(),key=lambda x:-x[1])[:5])
        print('    Top ETFs: '+top)

    # ===== 7. SUGGESTIONS =====
    print('\n\n  7. AVOIDANCE STRATEGIES')
    print('  '+'='*80)

    # Suggestion 1: Minimum hold days
    quick_pct=len(quick_losses)/len(losses)*100 if losses else 0
    print('\n  S1: MINIMUM HOLD DAYS (avoid 1-2 day whipsaws)')
    by_min_hold=[]
    for min_hold in[0,3,5,7,10]:
        filtered_trades=[]
        for t in sell_tr:
            if t['r']<0 and t['days']<min_hold:continue
            filtered_trades.append(t)
        fwins=sum(1 for t in filtered_trades if t['r']>0)
        fwr=fwins/len(filtered_trades)*100 if filtered_trades else 0
        fpnl=sum(t['pnl'] for t in filtered_trades)
        by_min_hold.append((min_hold,len(filtered_trades),fwr,fpnl))
    print('  %-10s %6s %6s %12s'%('MinHold','Trd','Win%','NetPnL'))
    for mh,nt,wr,pnl in by_min_hold:
        print('  %-10s %6d %5.1f%% %12s'%(str(mh)+'d',nt,wr,str(int(pnl))))

    # Suggestion 2: Require trend strength
    print('\n  S2: TREND STRENGTH FILTER (only buy when MA_fast/MA_slow > threshold)')
    for ratio_thr in[1.0,1.005,1.01,1.015,1.02]:
        filtered=[]
        for t in sell_tr:
            # For now, approximate: if we could have filtered by ratio at entry
            filtered.append(t)
        # This needs entry-ratio data, simplified here
    print('  (Needs entry-ratio data - would filter weak signals)')

    # Suggestion 3: Avoid specific ETFs
    print('\n  S3: BLACKLIST WORST ETFs')
    worst_etfs=sorted(etf_losses.items(),key=lambda x:sum(t['pnl'] for t in x[1]))[:8]
    blacklist=[c for c,_ in worst_etfs]
    after_blacklist=[t for t in sell_tr if t['code'] not in blacklist]
    ab_wins=sum(1 for t in after_blacklist if t['r']>0)
    ab_wr=ab_wins/len(after_blacklist)*100 if after_blacklist else 0
    ab_pnl=sum(t['pnl'] for t in after_blacklist)
    print('  Blacklist: '+','.join(etfs_all[c]['name'] for c in blacklist))
    print('  After blacklist: %d trades  Win=%.1f%%  NetPnL=%s'%(len(after_blacklist),ab_wr,str(int(ab_pnl))))

    # Suggestion 4: Position sizing
    print('\n  S4: POSITION SIZING (50% of capital instead of 100%)')
    half_trades=[]
    for t in sell_tr:half_trades.append(dict(t,pnl=t['pnl']/2))
    hwins=sum(1 for t in half_trades if t['r']>0)
    hpnl=sum(t['pnl'] for t in half_trades)
    print('  50%% position: NetPnL=%s  WinRate=%.1f%%'%(str(int(hpnl)),hwins/len(half_trades)*100))

    # Suggestion 5: Market regime filter
    print('\n  S5: MARKET REGIME FILTER (only buy when broad index is above MA60)')
    # Check if HS300 above MA60 reduces losses
    hs300=None
    for code in etfs_all:
        if code=='510300':
            c=[b['close'] for b in etfs_all[code]['bars']]
            ma60=ma(c,60)
            dates=[b['date'] for b in etfs_all[code]['bars']]
            hs300={'dates':dates,'c':c,'ma60':ma60}
            break
    if hs300:
        above_ma60={}
        for i in range(len(hs300['dates'])):
            d=hs300['dates'][i]
            above_ma60[d]=not math.isnan(hs300['ma60'][i]) and hs300['c'][i]>hs300['ma60'][i]

        regime_trades=[]
        for t in sell_tr:
            is_above=above_ma60.get(t['b'],True)  # default True if no data
            regime_trades.append(dict(t,regime='bull' if is_above else 'bear'))
        # Split
        bull_t=[t for t in regime_trades if t['regime']=='bull']
        bear_t=[t for t in regime_trades if t['regime']=='bear']
        for label,ts in [('Bull (HS300>MA60)',bull_t),('Bear (HS300<MA60)',bear_t)]:
            if ts:
                w=sum(1 for t in ts if t['r']>0);wr=w/len(ts)*100
                net=sum(t['pnl'] for t in ts)
                print('  %s: %d trades  Win=%.1f%%  NetPnL=%s'%(label,len(ts),wr,str(int(net))))
        # What if we skip bear-market trades?
        print('  If skip bear entries: %d trades  NetPnL=%s'%(
            len(bull_t),str(int(sum(t['pnl'] for t in bull_t)))))

    print('\n  Done!')

if __name__=='__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
