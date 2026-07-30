"""Single-Position Rotation Robustness Tests"""
import json,os,sys,io,math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
DATA_DIR='data';START_BASE='2020-01-01';END_BASE='2026-07-29'
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
            bars.append({'date':dt,'close':float(b['close'])})
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

def run_one_period(etfs,all_sigs,init_cap,start_d,end_d):
    codes=[c for c in etfs if etfs[c]['first_date']<=start_d]
    dm={c:{b['date']:b for b in etfs[c]['bars'] if start_d<=b['date']<=end_d} for c in codes}
    first_dates={c:etfs[c]['first_date'] for c in codes}
    ad=set()
    for c in codes: ad.update(dm[c].keys())
    all_dates=sorted(ad)
    cash=init_cap;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';trades=[];tn=0;trn=0;dvs=[]

    for d in all_dates:
        avail=[c for c in codes if first_dates[c]<=d]
        if pos_code:
            bar=dm[pos_code].get(d)
            if bar:
                px=bar['close']
                if px>peak:peak=px
                trend_on=all_sigs[pos_code]['trend'].get(d,False)
                er=None
                if px<=peak*(1-TRAIL):er='trail';trn+=1
                elif not trend_on:er='off';tn+=1
                if er:
                    sell_val=shares*px;pnl=sell_val-shares*bp
                    trades.append({'c':pos_code,'b':entry_d,'s':d,'bp':bp,'sp':px,'r':(px-bp)/bp,'pnl':pnl,'e':er})
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
        # always append daily value
        pos_val=shares*dm[pos_code].get(d,{}).get('close',0) if pos_code else 0
        dvs.append(cash+pos_val if pos_code or cash>0 else init_cap)
    # ensure at least 1 entry
    if not dvs:
        dvs.append(init_cap)

    if pos_code:
        bar=dm[pos_code].get(all_dates[-1])
        if bar:
            px=bar['close'];sell_val=shares*px;pnl=sell_val-shares*bp
            trades.append({'c':pos_code,'b':entry_d,'s':all_dates[-1],'bp':bp,'sp':px,'r':(px-bp)/bp,'pnl':pnl,'e':'final'})
            cash=sell_val

    fv=cash;rets=[]
    for i in range(1,len(dvs)):
        p,c=dvs[i-1],dvs[i]
        if p>0:rets.append((c-p)/p)
    if not rets:rets=[0.0]
    if not dvs:
        return dict(sh=0,tr=0,mdd=0,np=0,wr=0,fv=init_cap,trades=[])
    pkv=dvs[0];md=0.0
    for v in dvs:
        if v>pkv:pkv=v
        dd=(pkv-v)/pkv
        if dd>md:md=dd
    tr=(fv-init_cap)/init_cap
    mu=sum(rets)/len(rets)
    if len(rets)>1:
        sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
    else:
        sd_=0.01
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    st=[t for t in trades if t['e'] in('trail','off','final')]
    wins=sum(1 for t in st if t['r']>0)
    wr=wins/len(st) if st else 0
    return dict(sh=sh,tr=tr,mdd=md,np=len(st),wr=wr,fv=fv,trades=trades)

def gen_sigs_custom(bars,f,s,sl):
    c=[b['close'] for b in bars];n=len(bars)
    mf=ma(c,f);ms_=ma(c,s);msl=ma(c,sl);slo=slope(msl,max(sl//2,3))
    dates=[b['date'] for b in bars]
    trnd={};rat={}
    for i in range(n):
        d=dates[i]
        if not math.isnan(mf[i]) and not math.isnan(ms_[i]) and ms_[i]>0:
            sok=not math.isnan(slo[i]) and slo[i]>0
            trnd[d]=mf[i]>ms_[i] and sok;rat[d]=mf[i]/ms_[i]
        else:trnd[d]=False;rat[d]=1.0
    return{'trend':trnd,'ratio':rat}

def main():
    etfs_all=load_all()
    n_etfs=len(etfs_all)
    s_name='MA'+str(F_MA)+'/'+str(S_MA)+' s'+str(SL_MA)
    print('='*100)
    print('  SINGLE-POSITION ROTATION ROBUSTNESS TESTS')
    print('  '+str(n_etfs)+' ETFs  |  '+s_name+'  |  Trail='+str(int(TRAIL*100))+'%  |  MAX_POS=1')
    print('='*100)

    # ===== Test 1: Rolling Windows =====
    print('\n\n  TEST 1: ROLLING WINDOWS (2-year windows, 6-month step)')
    print('  '+'-'*75)
    print('  %-6s %-12s %-12s %7s %8s %7s %5s %5s'%('Window','From','To','S','Ret','DD','Trd','Win'))
    windows=[]
    for y in range(2020,2026):
        for m in[1,7]:
            start='%d-%02d-01'%(y,m)
            if m==1: ey=y+1;em=7
            else: ey=y+2;em=1
            end='%d-%02d-01'%(ey,em)
            windows.append((start,end))

    all_roll_s=[]
    for wi,(start,end) in enumerate(windows):
        all_sigs={}
        for code in etfs_all:
            bars=[b for b in etfs_all[code]['bars'] if start<=b['date']<=end]
            if bars:all_sigs[code]=gen_sigs(bars)
        if len(all_sigs)<5:continue
        r=run_one_period(etfs_all,all_sigs,INIT,start,end)
        all_roll_s.append(r['sh'])
        f_str=str(int(r['fv'])) if r['fv']>0 else str(int(r['fv']))
        print('  W%-4d %-12s %-12s %7.3f %7.2f%% %6.2f%% %5d %4.0f%%'%(
            wi,start,end,r['sh'],r['tr']*100,r['mdd']*100,r['np'],r['wr']*100))

    pos_wins=sum(1 for s in all_roll_s if s>0)
    print('\n  Rolling: mean S=%.3f  min S=%.3f  max S=%.3f  positive=%d/%d'%(
        sum(all_roll_s)/len(all_roll_s),min(all_roll_s),max(all_roll_s),pos_wins,len(all_roll_s)))

    # ===== Test 2: Annual =====
    print('\n\n  TEST 2: ANNUAL BREAKDOWN')
    print('  %-6s %7s %8s %7s %5s %5s %12s'%('Year','S','Ret','DD','Trd','Win','FinalValue'))
    years=[2020,2021,2022,2023,2024,2025,2026]
    yr_results=[]
    for y in years:
        if y<2026: start=str(y)+'-01-01';end=str(y)+'-12-31'
        else: start='2026-01-01';end='2026-07-29'
        all_sigs={}
        for code in etfs_all:
            bars=[b for b in etfs_all[code]['bars'] if start<=b['date']<=end]
            if bars:all_sigs[code]=gen_sigs(bars)
        if len(all_sigs)<5:continue
        r=run_one_period(etfs_all,all_sigs,INIT,start,end)
        yr_results.append(r)
        fv_str=str(int(r['fv']))
        print('  %-6s %7.3f %7.2f%% %6.2f%% %5d %4.0f%% %12s'%(
            str(y),r['sh'],r['tr']*100,r['mdd']*100,r['np'],r['wr']*100,fv_str))

    pos_yrs=sum(1 for r in yr_results if r['sh']>0)
    print('\n  Annual consistency: %d/%d positive Sharpe years'%(pos_yrs,len(yr_results)))

    # ===== Test 3: Drawdown Stress =====
    print('\n\n  TEST 3: DRAWDOWN STRESS TEST')
    all_sigs_full={}
    for code in etfs_all:all_sigs_full[code]=gen_sigs(etfs_all[code]['bars'])
    r_full=run_one_period(etfs_all,all_sigs_full,INIT,'2020-01-01','2026-07-29')

    sell_tr=[t for t in r_full['trades'] if t['e'] in('trail','off','final')]
    if not sell_tr:
        print('  No sell trades in full period')
        print('\n  Done!');return
    cum_losing=0;max_cum_loss=0;losing_streak=0;max_streak=0;cur_streak=0
    for t in sell_tr:
        if t['r']<0:
            cum_losing+=t['pnl'];losing_streak+=1
            max_cum_loss=min(max_cum_loss,cum_losing)
            max_streak=max(max_streak,losing_streak)
        else:cum_losing=0;losing_streak=0
    print('  Max losing streak: %d consecutive trades'%max_streak)
    print('  Max cumulative loss: %s (%.1f%% of initial)'%(str(int(abs(max_cum_loss))),abs(max_cum_loss)/INIT*100))

    worst_ret=min(t['r'] for t in sell_tr)
    print('  Worst single trade: %.2f%%'%(worst_ret*100))

    losses=sorted([t for t in sell_tr if t['r']<0],key=lambda t:t['r'])
    print('\n  Worst 5 trades:')
    for t in losses[:5]:
        name=etfs_all[t['c']]['name']
        print('    %s %s %s->%s %7.2f%% %-6s PnL=%s'%(
            t['c'],name,t['b'],t['s'],t['r']*100,t['e'],str(int(t['pnl']))))

    # ===== Test 4: Parameter Stability =====
    print('\n\n  TEST 4: PARAMETER STABILITY (MA neighborhood)')
    print('  %-15s %7s %8s %7s %5s'%('MA','S','Ret','DD','Trd'))
    nearby=[(5,14,8),(6,12,8),(6,15,10),(6,15,12),(7,15,8),(4,17,8),(6,17,8)]
    for f,s,sl in nearby:
        sigs2={}
        for code in etfs_all:
            bars=etfs_all[code]['bars']
            sigs2[code]=gen_sigs_custom(bars,f,s,sl)
        nr=run_one_period(etfs_all,sigs2,INIT,'2020-01-01','2026-07-29')
        print('  MA%d/%d s%-3d %7.3f %7.2f%% %6.2f%% %5d'%(f,s,sl,nr['sh'],nr['tr']*100,nr['mdd']*100,nr['np']))

    # ===== Test 5: Commission Impact =====
    print('\n\n  TEST 5: FULL PERIOD SUMMARY')
    print('  %-20s %7s %8s %7s %5s %5s'%('Metric','S','Ret','DD','Trd','Win'))
    print('  %-20s %7.3f %7.2f%% %6.2f%% %5d %4.0f%%'%(
        'Full period',r_full['sh'],r_full['tr']*100,r_full['mdd']*100,r_full['np'],r_full['wr']*100))

    print('\n  Done!')

if __name__=='__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
