"""
Final Strategy · Comprehensive Stability Tests
===============================================
MA6>MA15 + MA8 slope>0 + Trail=5% + Adaptive MH(Bear=7,Bull=0)
Bear regime: HS300 MA60 slope < 0
"""

import json,os,sys,io,math
from collections import defaultdict
from datetime import datetime
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=10_000_000;MAX_POS=1;TRAIL=0.05;F_MA=6;S_MA=15;SL_MA=8
BEAR_MH=7;BULL_MH=0

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

def slp(ms,lb):
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
    mf=ma(c,F_MA);ms=ma(c,S_MA);msl=ma(c,SL_MA);slo_=slp(msl,max(SL_MA//2,3))
    dates=[b['date'] for b in bars];trnd={};rat={}
    for i in range(n):
        d=dates[i]
        if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
            sok=not math.isnan(slo_[i]) and slo_[i]>0
            trnd[d]=mf[i]>ms[i] and sok;rat[d]=mf[i]/ms[i]
        else:trnd[d]=False;rat[d]=1.0
    return{'trend':trnd,'ratio':rat}

def build_regime(etfs):
    for code in etfs:
        if code=='510300':
            c=[b['close'] for b in etfs[code]['bars']]
            m60=ma(c,60);sl=slp(m60,20);dates=[b['date'] for b in etfs[code]['bars']]
            return {dates[i]:not math.isnan(sl[i]) and sl[i]<0 for i in range(len(dates))}
    return {}

def run_period(etfs,all_sigs,market_sigs,init_cap,start_d,end_d):
    codes=[c for c in etfs if etfs[c]['first_date']<=max(start_d,end_d)]
    dm={c:{b['date']:b for b in etfs[c]['bars'] if start_d<=b['date']<=end_d} for c in codes}
    fd={c:etfs[c]['first_date'] for c in codes}
    ad=set()
    for c in codes:ad.update(dm[c].keys())
    all_dates=sorted(ad)
    if not all_dates:return None
    cash=init_cap;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]
    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=market_sigs.get(d,False);cur_mh=BEAR_MH if is_bear else BULL_MH
        if pos_code:
            bar=dm[pos_code].get(d)
            if bar:
                px=bar['close']
                if px>peak:peak=px
                ton=all_sigs[pos_code]['trend'].get(d,False);er=None
                if px<=peak*(1-TRAIL):er='trail'
                elif not ton:
                    if cur_mh>0 and entry_date:
                        if (dt_obj-entry_date).days>=cur_mh:er='off'
                    else:er='off'
                if er:
                    sell_val=shares*px;pnl=sell_val-shares*bp
                    trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':er})
                    cash=sell_val;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
        if not pos_code and cash>0:
            cands=[]
            for c in avail:
                ton=all_sigs[c]['trend'].get(d,False)
                if ton:
                    bar=dm[c].get(d);cands.append((c,all_sigs[c]['ratio'].get(d,1.0),bar['close'] if bar else 0))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px=cands[0];shares=cash/px;bp=px;peak=px;pos_code=c;entry_d=d;entry_date=dt_obj;cash=0.0
        pos_val=shares*dm[pos_code].get(d,{}).get('close',0) if pos_code else 0
        dvs.append(cash+pos_val)
    if pos_code:
        bar=dm[pos_code].get(all_dates[-1])
        if bar:
            px=bar['close'];sell_val=shares*px;pnl=sell_val-shares*bp
            trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':'final'});cash=sell_val
    fv=cash;rets=[]
    for i in range(1,len(dvs)):
        p,c=dvs[i-1],dvs[i]
        if p>0:rets.append((c-p)/p)
    if not rets:rets=[0.0]
    pkv=dvs[0];md=0.0
    for v in dvs:
        if v>pkv:pkv=v
        dd=(pkv-v)/pkv
        if dd>md:md=dd
    tr=(fv-init_cap)/init_cap;mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5 if len(rets)>1 else 0.01
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    st=[t for t in trades if t['e'] in('trail','off','final')]
    w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0
    return{'sh':sh,'tr':tr,'mdd':md,'np':len(st),'wr':wr,'fv':fv,'trades':trades}

def main():
    etfs_all=load_all()
    all_sigs={}
    for code in etfs_all:all_sigs[code]=gen_sigs(etfs_all[code]['bars'])
    mkt=build_regime(etfs_all)
    print('='*100)
    print('  FINAL STRATEGY · COMPREHENSIVE STABILITY TESTS')
    print('  MA%d/%d s%d Trail=%d%% BearMH=%d BullMH=%d HS300-60 slope<0'%(F_MA,S_MA,SL_MA,int(TRAIL*100),BEAR_MH,BULL_MH))
    print('='*100)

    # === TEST 1: Rolling 2yr windows ===
    print('\n\n  TEST 1: ROLLING 2-YEAR WINDOWS (6-month step)')
    print('  %-4s %-12s %-12s %7s %8s %7s %5s %5s %8s'%('Win','From','To','S','Ret','DD','Trd','Win','Final'))
    windows=[]
    for y in range(2020,2026):
        for m in[1,7]:
            s='%d-%02d-01'%(y,m)
            if m==1:e='%d-07-01'%(y+1)
            else:e='%d-01-01'%(y+2)
            windows.append((s,e))
    roll_s = []
    max_streak = 0
    r_full = None
    boot_s = []
    pos_roll = pos_yr = 0
    for s,e in windows:
        r=run_period(etfs_all,all_sigs,mkt,INIT,s,e)
        if r is None:continue
        roll_s.append(r['sh'])
        fv_str=str(int(r['fv'])) if r['fv']>0 else str(int(r['fv']))
        print('  %-4d %-12s %-12s %7.3f %7.2f%% %6.2f%% %5d %4.0f%% %8s'%(
            len(roll_s),s,e,r['sh'],r['tr']*100,r['mdd']*100,r['np'],r['wr']*100,fv_str))
    pos_roll=sum(1 for s in roll_s if s>0)
    print('\n  Rolling stats: mean S=%.3f  min S=%.3f  max S=%.3f  positive=%d/%d (%.0f%%)'%(
        sum(roll_s)/len(roll_s),min(roll_s),max(roll_s),pos_roll,len(roll_s),pos_roll/len(roll_s)*100))

    # === TEST 2: Annual breakdown ===
    print('\n\n  TEST 2: ANNUAL BREAKDOWN')
    years=[2020,2021,2022,2023,2024,2025,2026]
    print('  %-6s %7s %8s %7s %5s %5s %12s'%('Year','S','Ret','DD','Trd','Win','EndValue'))
    yr_results=[]
    for y in years:
        s='%d-01-01'%y;e='%d-12-31'%y if y<2026 else '2026-07-29'
        r=run_period(etfs_all,all_sigs,mkt,INIT,s,e)
        if r is None:continue
        yr_results.append(r)
        fv_str=str(int(r['fv']))
        print('  %-6s %7.3f %7.2f%% %6.2f%% %5d %4.0f%% %12s'%(
            str(y),r['sh'],r['tr']*100,r['mdd']*100,r['np'],r['wr']*100,fv_str))
    pos_yr=sum(1 for r in yr_results if r['sh']>0)
    print('\n  Annual consistency: %d/%d positive Sharpe years (%.0f%%)'%(pos_yr,len(yr_results),pos_yr/len(yr_results)*100))

    # === TEST 3: Max loss sequence ===
    print('\n\n  TEST 3: DRAWDOWN STRESS')
    r_full=run_period(etfs_all,all_sigs,mkt,INIT,'2020-01-01','2026-07-29')
    if r_full:
        st=[t for t in r_full['trades'] if t['e'] in('trail','off','final')]
        max_streak=0;cur_streak=0;cum_loss=0;max_cum=0
        for t in st:
            if t['r']<0:cur_streak+=1;cum_loss+=t['pnl'];max_cum=min(max_cum,cum_loss)
            else:cur_streak=0;cum_loss=0
            max_streak=max(max_streak,cur_streak)
        print('  Max consecutive losses: %d trades'%max_streak)
        print('  Max cumulative loss: %s (%.1f%% of initial)'%(str(int(abs(max_cum))),abs(max_cum)/INIT*100))
        worst_r=min(t['r'] for t in st)
        print('  Worst single trade: %.2f%%'%(worst_r*100))
        # Drawdown events > 10%
        dvs=r_full['dvs'] if 'dvs' in r_full else []
        if dvs:
            pk=dvs[0];dd_events=[]
            for i,v in enumerate(dvs):
                if v>pk:pk=v
                dd=(pk-v)/pk
                if dd>0.1 and (not dd_events or i-dd_events[-1][0]>30):
                    dd_events.append((i,len(dvs),dd))
            print('  Deep DD events (>10%): %d'%len(dd_events))
            for idx,total,dd in dd_events[:5]:
                print('    %.1f%% DD (event %d/%d)'%(dd*100,idx,total))

    # === TEST 4: Parameter stability (nearby MA combos) ===
    print('\n\n  TEST 4: PARAMETER STABILITY (MA neighborhood)')
    nearby=[(4,14,8),(5,14,8),(6,12,8),(6,15,10),(6,17,8),(7,15,8),(5,10,8)]
    for f,s,sl in nearby:
        sigs2={}
        for code in etfs_all:
            bars=etfs_all[code]['bars'];c=[b['close'] for b in bars];n=len(bars)
            mf=ma(c,f);ms=ma(c,s);msl=ma(c,sl);slo=slp(msl,max(sl//2,3))
            dates=[b['date'] for b in bars]
            trnd={};rat={}
            for i in range(n):
                d=dates[i]
                if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
                    sok=not math.isnan(slo[i]) and slo[i]>0
                    trnd[d]=mf[i]>ms[i] and sok;rat[d]=mf[i]/ms[i]
                else:trnd[d]=False;rat[d]=1.0
            sigs2[code]={'trend':trnd,'ratio':rat}
        nr=run_period(etfs_all,sigs2,mkt,INIT,'2020-01-01','2026-07-29')
        if nr:
            print('  MA%d/%d s%d: S=%.3f Ret=%.1f%% DD=%.1f%% Trd=%d Win=%.0f%%'%(
                f,s,sl,nr['sh'],nr['tr']*100,nr['mdd']*100,nr['np'],nr['wr']*100))

    # === TEST 5: Monte Carlo (bootstrap trades) ===
    print('\n\n  TEST 5: BOOTSTRAP CONFIDENCE')
    if r_full:
        st=[t for t in r_full['trades'] if t['e'] in('trail','off','final')]
        rets=[t['r'] for t in st]
        import random
        boot_s=[];n_trades=len(rets)
        for _ in range(1000):
            sampled=[random.choice(rets) for _ in range(n_trades)]
            mu=sum(sampled)/len(sampled);sd_=(sum((r-mu)**2 for r in sampled)/(len(sampled)-1))**0.5 if len(sampled)>1 else 0.01
            av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
            boot_s.append(sh)
        boot_s.sort()
        print('  Bootstrap Sharpe (1000 samples):')
        print('    Mean: %.3f  Median: %.3f  95%% CI: [%.3f, %.3f]'%(
            sum(boot_s)/len(boot_s),boot_s[500],boot_s[25],boot_s[975]))
        print('    Prob(S>0): %.1f%%'%(sum(1 for s in boot_s if s>0)/10))

    # === TEST 6: Return distribution ===
    print('\n\n  TEST 6: RETURN DISTRIBUTION')
    win_loss_data_ok = False
    if r_full:
        st=[t for t in r_full['trades'] if t['e'] in('trail','off','final')]
        wins=[t for t in st if t['r']>0];losses=[t for t in st if t['r']<=0]
        avg_w=sum(t['r'] for t in wins)/len(wins) if wins else 0
        avg_l=sum(t['r'] for t in losses)/len(losses) if losses else 0
        print('  Wins: %d trades  avg ret=%.2f%%'%(len(wins),avg_w*100))
        print('  Losses: %d trades  avg ret=%.2f%%'%(len(losses),avg_l*100))
        print('  Win/Loss ratio: %.2f'%(abs(avg_w/avg_l) if avg_l!=0 else 99))
        buckets=[(-99,-10),(-10,-5),(-5,-3),(-3,-1),(-1,0),(0,3),(3,5),(5,10),(10,20),(20,999)]
        print('  Return distribution:')
        for lo,hi in buckets:
            cnt=sum(1 for t in st if lo<t['r']*100<=hi)
            bar='#'*max(cnt,1) if cnt>0 else ''
            print('  %+4d%% ~ %+4d%%: %3d %s'%(lo,hi,cnt,bar))
        win_loss_data_ok = True

    # Summary
    boot_ok = 'boot_s' in dir()
    print('\n\n  '+('='*60))
    print('  STABILITY SUMMARY')
    print('  '+('='*60))
    if roll_s:
        print('  Rolling window positivity: %d/%d (%.0f%%)'%(pos_roll,len(roll_s),pos_roll/len(roll_s)*100))
    if yr_results:
        print('  Annual positivity:         %d/%d (%.0f%%)'%(pos_yr,len(yr_results),pos_yr/len(yr_results)*100))
    print('  Max consecutive losses:    %d trades'%max_streak)
    if boot_ok and boot_s:
        print('  Bootstrap P(S>0):          %.1f%%'%(sum(1 for s in boot_s if s>0)/10.0))
    if r_full:
        print('  Full period: S=%.3f Ret=%.1f%% DD=%.1f%% %d trades'%(
            r_full['sh'],r_full['tr']*100,r_full['mdd']*100,r_full['np']))

    print('\n  Done!')

if __name__=='__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
