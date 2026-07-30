"""Multi-Index DEF→ALL Switch · Any 1-2 of N indices turn bullish"""
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

# Index pool for bull/bear detection
INDEX_CODES={  # ETF codes that can serve as market barometers
    '510300':'HS300',
    '159915':'创业板',
    '588000':'科创50',
    '510050':'上证50',
    '515880':'通信ETF',  # tech proxy
    '512480':'半导体ETF', # tech proxy
}

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
all_trnd={};all_ratio={};above_ma60={};etf_highs={}
for c in codes:
    bars=etfs[c]['bars'];cl=[b['close'] for b in bars];hi=[b['high'] for b in bars]
    mf=ma(cl,F_MA);ms=ma(cl,S_MA);msl=ma(cl,SL_MA);slo_=slp(msl,max(SL_MA//2,3))
    m60=ma(cl,60);dts=[b['date'] for b in bars]
    trnd={};rat={};abv={};hgh={}
    for i in range(len(bars)):
        d=dts[i]
        if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
            sk=not math.isnan(slo_[i]) and slo_[i]>0
            trnd[d]=mf[i]>ms[i] and sk;rat[d]=mf[i]/ms[i]
        else:trnd[d]=False;rat[d]=1.0
        abv[d]=not math.isnan(m60[i]) and cl[i]>m60[i];hgh[d]=hi[i]
    all_trnd[c]=trnd;all_ratio[c]=rat;above_ma60[c]=abv;etf_highs[c]=hgh

# Build slope signals for each index
index_bull={}
for code,name in INDEX_CODES.items():
    if code not in etfs:continue
    cl=[b['close'] for b in etfs[code]['bars']]
    m60=ma(cl,60);sl=slp(m60,20);dts=[b['date'] for b in etfs[code]['bars']]
    index_bull[code]={dts[i]:not math.isnan(sl[i]) and sl[i]>0 for i in range(len(dts))}

# Also: price>MA60 signal
index_above_ma60={}
for code,name in INDEX_CODES.items():
    if code not in etfs:continue
    cl=[b['close'] for b in etfs[code]['bars']]
    m60=ma(cl,60);dts=[b['date'] for b in etfs[code]['bars']]
    index_above_ma60[code]={dts[i]:not math.isnan(m60[i]) and cl[i]>m60[i] for i in range(len(dts))}

dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
fd={c:etfs[c]['first_date'] for c in codes}
ad=set()
for c in codes:
    for k in dm[c]:ad.add(k)
all_dates=sorted(ad)

def how_many_bull(signals,d,min_count):
    """Check if at least min_count indices have their MA60 slope>0"""
    cnt=0
    for code in INDEX_CODES:
        if signals.get(code,{}).get(d,False):cnt+=1
        if cnt>=min_count:return True
    return False

def how_many_above(signals,d,min_count):
    cnt=0
    for code in INDEX_CODES:
        if signals.get(code,{}).get(d,False):cnt+=1
        if cnt>=min_count:return True
    return False

def run(switch_condition,param1,param2=0):
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]
    pool_mode='all';rolling_pnl=[]

    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')

        # Bear detection = HS300 slope only (unchanged)
        is_bear=index_bull.get('510300',{}).get(d,True)==False  # HS300 slope<0
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

                    # ALL→DEF check
                    if len(rolling_pnl)>=5 and pool_mode=='all':
                        if sum(rolling_pnl)<-0.10*INIT:pool_mode='defensive'

                    # DEF→ALL check
                    if pool_mode=='defensive':
                        should=False
                        if switch_condition=='index_slope':
                            should=how_many_bull(index_bull,d,param1)
                        elif switch_condition=='index_above':
                            should=how_many_above(index_above_ma60,d,param1)
                        elif switch_condition=='time_or_index':
                            sd_=datetime.strptime(switched_to_def_date,'%Y-%m-%d') if switched_to_def_date else dt_obj
                            should=(dt_obj-sd_).days>param1 or how_many_bull(index_bull,d,param2)
                        elif switch_condition=='time_and_index':
                            sd_=datetime.strptime(switched_to_def_date,'%Y-%m-%d') if switched_to_def_date else dt_obj
                            should=(dt_obj-sd_).days>param1 and how_many_bull(index_bull,d,param2)
                        if should:pool_mode='all';switched_to_def_date=''

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

        # Track switched_to_def_date
        if pool_mode=='defensive' and 'switched_to_def_date' not in dir()==False:
            if len(rolling_pnl)>=5 and pool_mode=='defensive':
                pass  # initialized elsewhere

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
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    st=[t for t in trades if t['b']!=''];w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0
    yr_pnl=defaultdict(float)
    for t in trades:
        if t.get('b'):yr_pnl[t['b'][:4]]+=t['pnl']
    return sh,tr,mdd,len(st),wr,yr_pnl

# Fix: handle switched_to_def_date
def run_fixed(switch_condition,param1,param2=0):
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]
    pool_mode='all';rolling_pnl=[];switched_to_def_date=''

    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=not index_bull.get('510300',{}).get(d,False)
        cur_trail=TRAIL_BEAR if is_bear else TRAIL_BULL;cur_mh=BEAR_MH if is_bear else BULL_MH

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

                    # ALL→DEF (always works the same)
                    if len(rolling_pnl)>=5 and pool_mode=='all' and sum(rolling_pnl)<-0.10*INIT:
                        pool_mode='defensive';switched_to_def_date=d

                    # DEF→ALL (various methods)
                    if pool_mode=='defensive':
                        should_switch=False
                        sd=datetime.strptime(switched_to_def_date,'%Y-%m-%d') if switched_to_def_date else dt_obj
                        days_in_def=(dt_obj-sd).days

                        if switch_condition=='index_slope':
                            should_switch=how_many_bull(index_bull,d,param1)
                        elif switch_condition=='index_above':
                            should_switch=how_many_above(index_above_ma60,d,param1)
                        elif switch_condition=='time_or_index':
                            should_switch=(days_in_def>param1 or how_many_bull(index_bull,d,param2))
                        elif switch_condition=='time_and_index':
                            should_switch=(days_in_def>param1 and how_many_bull(index_bull,d,param2))

                        if should_switch:pool_mode='all';switched_to_def_date=''

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
                            h=etf_highs[c].get(all_dates[pdi],0);hi20=max(hi20,h)
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
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    st=[t for t in trades if t['b']!=''];w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0
    yr_pnl=defaultdict(float)
    for t in trades:
        if t.get('b'):yr_pnl[t['b'][:4]]+=t['pnl']
    return sh,tr,mdd,len(st),wr,yr_pnl

print('='*110)
print('  MULTI-INDEX DEF->ALL SWITCH')
print('  Indices: HS300, 创业板, 科创50, 上证50, 通信ETF, 半导体ETF')
print('  ALL->DEF (unchanged): rolling 5-trade PnL < -10%')
print('='*110)
print('  %-45s %7s %7s %7s %7s %7s %7s %7s %7s %7s'%('DEF->ALL Condition','S','2020','2021','2022','2023','2024','2025','2026','Total'))
print('  '+'-'*120)

all_res=[]
# 1. N indices slope>0
for n in[1,2,3]:
    sh,tr,mdd,np,wr,yr=run_fixed('index_slope',n)
    all_res.append(('%d index slope>0'%n,sh,yr,mdd,np,wr))

# 2. N indices price>MA60
for n in[1,2,3,4]:
    sh,tr,mdd,np,wr,yr=run_fixed('index_above',n)
    all_res.append(('%d index >MA60'%n,sh,yr,mdd,np,wr))

# 3. Time OR N indices
for t_d in[60,90]:
    for n in[1,2,3]:
        sh,tr,mdd,np,wr,yr=run_fixed('time_or_index',t_d,n)
        all_res.append(('Time>%dd OR %d index>'%(t_d,n),sh,yr,mdd,np,wr))

# 4. Time AND N indices
for t_d in[30,60]:
    for n in[1,2]:
        sh,tr,mdd,np,wr,yr=run_fixed('time_and_index',t_d,n)
        all_res.append(('Time>%dd & %d index>'%(t_d,n),sh,yr,mdd,np,wr))

# Print
for label,sh,yr,mdd,np,wr in all_res:
    cum=1.0
    for y in['2020','2021','2022','2023','2024','2025','2026']:cum*=1+yr.get(y,0)/INIT/100
    total=(cum-1)*100
    y22=yr.get('2022',0)/INIT*100;y25=yr.get('2025',0)/INIT*100;y26=yr.get('2026',0)/INIT*100
    y20=yr.get('2020',0)/INIT*100;y21=yr.get('2021',0)/INIT*100;y23=yr.get('2023',0)/INIT*100;y24=yr.get('2024',0)/INIT*100
    print('  %-45s %7.3f %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+7.0f%% %5d %4.0f%%'%(
        label,sh,y20,y21,y22,y23,y24,y25,y26,total,np,wr*100))

# Ranking
all_res.sort(key=lambda x:-x[1])
print('\n\nTOP 15:')
for i,(label,sh,yr,_,np,wr) in enumerate(all_res[:15]):
    y22=yr.get('2022',0)/INIT*100;y25=yr.get('2025',0)/INIT*100;y26=yr.get('2026',0)/INIT*100
    cum=1.0
    for y in['2020','2021','2022','2023','2024','2025','2026']:cum*=1+yr.get(y,0)/INIT/100
    print('  %2d. %-45s S=%.3f 2022=%+.1f%% 2025=%+.0f%% 2026=%+.0f%% Total=%+.0f%%'%(i+1,label,sh,y22,y25,y26,(cum-1)*100))

print('\nDone!')
