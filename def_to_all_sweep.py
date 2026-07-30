"""V9b DEF→ALL Switch Strategies · 7 methods sweep"""
import json,os,math
from collections import defaultdict
from datetime import datetime
DATA_DIR='data';START='2020-01-01';END='2026-07-30'
RF=0.025;TD=252;INIT=int(1e7);F_MA=6;S_MA=15;SL_MA=8
TRAIL_BULL=0.03;TRAIL_BEAR=0.06;BULL_MH=0;BEAR_MH=7;PULLBACK_MIN=0.05

ETF_CODES=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
           '588200','159995','512480','515880','515050','159819','159992','512010',
           '518880','159937','513180','513050','513100','159509','588000','588220',
           '510300','159915','510050','511010','511260','510880','512890','159301']
DEFENSIVE=['518880','159937','518800','511010','511260','510880','512890','510050']
GROWTH=[c for c in ETF_CODES if c not in DEFENSIVE and c!='510300']

def load():
    etfs={}
    for code in ETF_CODES:
        p=os.path.join(DATA_DIR,'etf_'+code+'.json')
        if not os.path.exists(p):continue
        d=json.load(open(p,encoding='utf-8'))
        bars=[]
        for b in d['bars']:
            dt=b['date'];px=float(b['close'])
            if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            if START<=dt<=END:bars.append({'date':dt,'close':px,'high':float(b.get('high',px))})
        if bars:etfs[code]={'name':d['name'],'first_date':bars[0]['date'],'bars':bars}
    return etfs

def ma(d,w):
    m=[];n=len(d)
    for i in range(n):
        if i<w-1:m.append(float('nan'))
        else:m.append(sum(d[i-w+1:i+1])/w)
    return m
def slp(ms,lb=5):
    s=[float('nan')]*len(ms)
    for i in range(len(ms)):
        if i<lb:continue
        ys=ms[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys):continue
        n=len(ys);sx=sy=sxy=sxx=0
        for j,y in enumerate(ys):sx+=j;sy+=y;sxy+=j*y;sxx+=j*j
        d_=n*sxx-sx*sx
        if d_>0:s[i]=(n*sxy-sx*sy)/d_/ms[i] if ms[i]>0 else 0
    return s

etfs=load();codes=sorted(etfs.keys())
all_trnd={};all_ratio={};above_ma60={};etf_highs={};etf_closes={}
for c in codes:
    bars=etfs[c]['bars'];cl=[b['close'] for b in bars];hi=[b['high'] for b in bars]
    mf=ma(cl,F_MA);ms=ma(cl,S_MA);msl=ma(cl,SL_MA);slo_=slp(msl,max(SL_MA//2,3))
    m60=ma(cl,60);dts=[b['date'] for b in bars]
    trnd={};rat={};abv={};hgh={};cls={}
    for i in range(len(bars)):
        d=dts[i]
        if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
            sk=not math.isnan(slo_[i]) and slo_[i]>0
            trnd[d]=mf[i]>ms[i] and sk;rat[d]=mf[i]/ms[i]
        else:trnd[d]=False;rat[d]=1.0
        abv[d]=not math.isnan(m60[i]) and cl[i]>m60[i];hgh[d]=hi[i];cls[d]=cl[i]
    all_trnd[c]=trnd;all_ratio[c]=rat;above_ma60[c]=abv;etf_highs[c]=hgh;etf_closes[c]=cls

# Bear regime
bear_slope={};hs300_cl={}
for c in codes:
    if c=='510300':
        cl=[b['close'] for b in etfs[c]['bars']]
        m60=ma(cl,60);sl=slp(m60,20);dts=[b['date'] for b in etfs[c]['bars']]
        for i in range(len(dts)):
            bear_slope[dts[i]]=not math.isnan(sl[i]) and sl[i]<0
            hs300_cl[dts[i]]=cl[i]
        break

dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
fd={c:etfs[c]['first_date'] for c in codes}
ad=set()
for c in codes:
    for k in dm[c]:ad.add(k)
all_dates=sorted(ad)

def run(switch_method,param):
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]
    pool_mode='all';rolling_pnl=[];switched_to_def_date='';switched_to_def_hs300=0

    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=bear_slope.get(d,False)
        cur_trail=TRAIL_BEAR if is_bear else TRAIL_BULL
        cur_mh=BEAR_MH if is_bear else BULL_MH

        if pos:
            bar=dm[pos].get(d)
            if bar:
                px=bar['close']
                if px>peak:peak=px
                ton=all_trnd[pos].get(d,False);er=None
                if px<=peak*(1-cur_trail):er='trail'
                elif not ton:
                    if cur_mh>0 and entry_date and (dt_obj-entry_date).days>=cur_mh:er='off'
                    else:er='off'
                if er:
                    pnl=shares*px-shares*bp
                    trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':er,'c':pos,'b':entry_d,'s':d,'pool':pool_mode})
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl)>5:rolling_pnl.pop(0)
                    cash=shares*px;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None

                    # ALL→DEF: rolling P&L < -10%
                    if len(rolling_pnl)>=5 and pool_mode=='all':
                        if sum(rolling_pnl)<-0.10*INIT:
                            pool_mode='defensive';switched_to_def_date=d
                            switched_to_def_hs300=hs300_cl.get(d,0)

                    # DEF→ALL: various methods
                    if pool_mode=='defensive':
                        should_switch=False
                        if switch_method=='pnl' and len(rolling_pnl)>=5:
                            if sum(rolling_pnl)>param*INIT:should_switch=True
                        elif switch_method=='time':
                            if switched_to_def_date:
                                sd=datetime.strptime(switched_to_def_date,'%Y-%m-%d')
                                if (dt_obj-sd).days>param:should_switch=True
                        elif switch_method=='hs300_slope':
                            if not bear_slope.get(d,False):should_switch=True
                        elif switch_method=='hs300_recovery':
                            if switched_to_def_hs300>0:
                                curr_hs=hs300_cl.get(d,0)
                                if (curr_hs-switched_to_def_hs300)/switched_to_def_hs300>param:should_switch=True
                        elif switch_method=='growth_above_ma60':
                            cnt=sum(1 for c in GROWTH if above_ma60.get(c,{}).get(d,False))
                            if cnt>=param:should_switch=True
                        elif switch_method=='time_or_pnl':
                            sd=datetime.strptime(switched_to_def_date,'%Y-%m-%d') if switched_to_def_date else dt_obj
                            if (dt_obj-sd).days>param or sum(rolling_pnl)>0.15*INIT:should_switch=True
                        elif switch_method=='time_and_hs300':
                            sd=datetime.strptime(switched_to_def_date,'%Y-%m-%d') if switched_to_def_date else dt_obj
                            if (dt_obj-sd).days>param and not bear_slope.get(d,False):should_switch=True
                        elif switch_method=='time_and_growth':
                            sd=datetime.strptime(switched_to_def_date,'%Y-%m-%d') if switched_to_def_date else dt_obj
                            cnt=sum(1 for c in GROWTH if above_ma60.get(c,{}).get(d,False))
                            if (dt_obj-sd).days>param and cnt>=len(GROWTH)//3:should_switch=True
                        elif switch_method=='time_or_hs300':
                            sd=datetime.strptime(switched_to_def_date,'%Y-%m-%d') if switched_to_def_date else dt_obj
                            if (dt_obj-sd).days>param or not bear_slope.get(d,False):should_switch=True

                        if should_switch:
                            pool_mode='all';switched_to_def_date=''

        if not pos and cash>0:
            cands=[]
            for c in avail:
                if pool_mode=='defensive' and c not in DEFENSIVE:continue
                ton=all_trnd[c].get(d,False)
                if not ton:continue
                if not above_ma60.get(c,{}).get(d,False):continue
                if PULLBACK_MIN>0:
                    hi20=0
                    for lb in range(1,21):
                        pdi=max(0,len(all_dates)-1-lb)
                        if pdi>=0 and pdi<len(all_dates):
                            h=etf_highs[c].get(all_dates[pdi],0)
                            if h>hi20:hi20=h
                    if hi20>0 and (hi20-dm[c][d]['close'])/hi20<PULLBACK_MIN:continue
                bar=dm[c].get(d)
                if bar:cands.append((c,all_ratio[c].get(d,1.0),bar['close']))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px=cands[0]
                shares=cash/px;bp=px;peak=px;pos=c;entry_d=d;entry_date=dt_obj;cash=0.0
        pos_val=shares*dm[pos].get(d,{}).get('close',0) if pos else 0
        dvs.append(cash+pos_val)

    if pos:
        bar=dm[pos].get(all_dates[-1])
        if bar:px=bar['close'];pnl=shares*px-shares*bp
        trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':'final','c':pos,'b':entry_d,'s':all_dates[-1],'pool':pool_mode});cash=shares*px

    fv=cash;rets=[]
    for i in range(1,len(dvs)):
        if dvs[i-1]>0:rets.append((dvs[i]-dvs[i-1])/dvs[i-1])
    if not rets:rets=[0.0]
    pk=dvs[0];mdd=0.0
    for v in dvs:
        if v>pk:pk=v
        dd=(pk-v)/pk
        if dd>mdd:mdd=dd
    tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5 if len(rets)>1 else 0.01
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0;ar=(1+tr)**(TD/len(rets))-1 if tr>-1 else -1
    st=[t for t in trades if t['b']!=''];w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0
    yr_pnl=defaultdict(float);def_days=0
    for t in trades:
        if t.get('b'):yr_pnl[t['b'][:4]]+=t['pnl']
        if t.get('pool')=='defensive':def_days+=1
    return sh,tr,mdd,ar,len(st),wr,yr_pnl,def_days

# Baseline V9b
sh,tr,mdd,ar,np,wr,yr,dd=run('pnl',0.15)
y22=yr.get('2022',0)/INIT*100;y25=yr.get('2025',0)/INIT*100;y26=yr.get('2026',0)/INIT*100
cum=1.0
for y in['2020','2021','2022','2023','2024','2025','2026']:cum*=1+yr.get(y,0)/INIT/100
v9b_total=(cum-1)*100
print('BASELINE V9b: S=%.3f  2022=%.1f%%  2025=%.0f%%  2026=%.1f%%  Total=%.0f%%  DD=%.1f%%'%(sh,y22,y25,y26,v9b_total,mdd*100))

# ALL→DEF always via PnL. Sweep DEF→ALL methods
print()
print('='*120)
print('  DEF→ALL SWITCH METHODS')
print('='*120)
print('  %-40s %7s %7s %7s %7s %7s %7s %7s %7s %7s'%('Method','S','2020','2021','2022','2023','2024','2025','2026','Total'))
print('  '+'-'*120)

methods=[]
# 1. PnL-based (original, sweep thresholds)
for th in[0.05,0.08,0.10,0.12,0.15,0.20,0.25]:
    sh,tr,mdd,ar,np,wr,yr,dd=run('pnl',th)
    methods.append(('PnL>%.0f%%'%(th*100),sh,yr,mdd,np,wr,dd))

# 2. Time-based
for t_days in[30,45,60,90,120,180]:
    sh,tr,mdd,ar,np,wr,yr,dd=run('time',t_days)
    methods.append(('Time>%dd'%t_days,sh,yr,mdd,np,wr,dd))

# 3. HS300 slope turns positive
sh,tr,mdd,ar,np,wr,yr,dd=run('hs300_slope',0)
methods.append(('HS300 slope>0',sh,yr,mdd,np,wr,dd))

# 4. HS300 recovers X% from switch point
for pct in[0.05,0.10,0.15]:
    sh,tr,mdd,ar,np,wr,yr,dd=run('hs300_recovery',pct)
    methods.append(('HS300 +%.0f%%'%(pct*100),sh,yr,mdd,np,wr,dd))

# 5. N growth ETFs above MA60
for n_g in[5,8,12,16]:
    sh,tr,mdd,ar,np,wr,yr,dd=run('growth_above_ma60',n_g)
    methods.append(('Growth>MA60=%d'%n_g,sh,yr,mdd,np,wr,dd))

# 6. Time OR PnL
for t_days in[60,90,120]:
    sh,tr,mdd,ar,np,wr,yr,dd=run('time_or_pnl',t_days)
    methods.append(('Time>%dd OR PnL>15%%'%t_days,sh,yr,mdd,np,wr,dd))

# 7. Time AND HS300 not bear
for t_days in[30,60,90]:
    sh,tr,mdd,ar,np,wr,yr,dd=run('time_and_hs300',t_days)
    methods.append(('Time>%dd & HS300rise'%t_days,sh,yr,mdd,np,wr,dd))

# 8. Time AND growth ETFs recovering
for t_days in[30,60,90]:
    sh,tr,mdd,ar,np,wr,yr,dd=run('time_and_growth',t_days)
    methods.append(('Time>%dd & GrowthMA60'%t_days,sh,yr,mdd,np,wr,dd))

# 9. Time OR HS300 not bear
for t_days in[30,60,90]:
    sh,tr,mdd,ar,np,wr,yr,dd=run('time_or_hs300',t_days)
    methods.append(('Time>%dd OR HS300rise'%t_days,sh,yr,mdd,np,wr,dd))

# Print all
for label,sh,yr,mdd,np,wr,dd in methods:
    cum=1.0
    for y in['2020','2021','2022','2023','2024','2025','2026']:cum*=1+yr.get(y,0)/INIT/100
    total=(cum-1)*100
    y22=yr.get('2022',0)/INIT*100;y25=yr.get('2025',0)/INIT*100;y26=yr.get('2026',0)/INIT*100
    y20=yr.get('2020',0)/INIT*100;y21=yr.get('2021',0)/INIT*100
    y23=yr.get('2023',0)/INIT*100;y24=yr.get('2024',0)/INIT*100
    def_pct=dd/max(np,1)*100
    # Filter to interesting combos
    print('  %-40s %7.3f %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+7.0f%% %4d %4.0f%% %4.0f%%'%(
        label,sh,y20,y21,y22,y23,y24,y25,y26,total,np,wr*100,def_pct))

# Best by balanced score
methods.sort(key=lambda x:x[1]*100,reverse=True)
print('\n\nTOP 15 BY SHARPE:')
for i,(label,sh,yr,_,_,_,_) in enumerate(methods[:15]):
    y22=yr.get('2022',0)/INIT*100;y25=yr.get('2025',0)/INIT*100;y26=yr.get('2026',0)/INIT*100
    print('  %2d. %-40s S=%.3f  2022=%+.1f%%  2025=%+.0f%%  2026=%+.1f%%'%(i+1,label,sh,y22,y25,y26))

# Best 2026 specifically
methods.sort(key=lambda x:-(x[2].get('2026',0)))
print('\n\nTOP 2026:')
for i,(label,sh,yr,_,_,_,_) in enumerate(methods[:10]):
    y22=yr.get('2022',0)/INIT*100;y26=yr.get('2026',0)/INIT*100
    cum=1.0
    for y in['2020','2021','2022','2023','2024','2025','2026']:cum*=1+yr.get(y,0)/INIT/100
    print('  %2d. %-40s 2026=%+.1f%%  2022=%+.1f%%  Total=%.0f%%  S=%.3f'%(i+1,label,y26,y22,(cum-1)*100,sh))

print('\nDone!')
