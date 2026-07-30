"""Adaptive Pool Switching · Based on rolling P&L performance"""
import json,os,math
from collections import defaultdict
from datetime import datetime
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=int(1e7);F_MA=6;S_MA=15;SL_MA=8
TRAIL_BULL=0.03;TRAIL_BEAR=0.06;BULL_MH=0;BEAR_MH=7;PULLBACK_MIN=0.05

ETF_CODES=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
           '588200','159995','512480','515880','515050','159819','159992','512010',
           '518880','159937','513180','513050','513100','159509','588000','588220',
           '510300','159915','510050','511010','511260','510880','512890','159301']

DEFENSIVE=['518880','159937','518800','511010','511260','510880','512890','510050']
GROWTH_POOL=[c for c in ETF_CODES if c not in DEFENSIVE and c!='510300']

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

bear_slope={}
for c in codes:
    if c=='510300':
        cl=[b['close'] for b in etfs[c]['bars']]
        m60=ma(cl,60);sl=slp(m60,20);dts=[b['date'] for b in etfs[c]['bars']]
        for i in range(len(dts)):bear_slope[dts[i]]=not math.isnan(sl[i]) and sl[i]<0
        break

dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
fd={c:etfs[c]['first_date'] for c in codes}
ad=set()
for c in codes:
    for k in dm[c]:ad.add(k)
all_dates=sorted(ad)

def run(loss_threshold,gain_threshold,lookback_n):
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]
    current_pool=codes[:]  # start with all ETFs
    rolling_pnl=[]  # track last N trades
    pool_mode='all'

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
                    trades.append({'pnl':pnl,'r':(px-bp)/bp,'b':entry_d,'s':d,'pool':pool_mode})
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl)>lookback_n:rolling_pnl.pop(0)
                    cash=shares*px;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None

                    # Check pool switching
                    if len(rolling_pnl)>=lookback_n:
                        recent_pnl=sum(rolling_pnl)
                        if recent_pnl<loss_threshold*INIT and pool_mode!='defensive':
                            pool_mode='defensive'
                        elif recent_pnl>gain_threshold*INIT and pool_mode!='all':
                            pool_mode='all'

        if not pos and cash>0:
            cands=[]
            for c in avail:
                # Pool filter
                if pool_mode=='defensive' and is_bear and c not in DEFENSIVE:continue
                ton=all_trnd[c].get(d,False)
                if not ton:continue
                if not above_ma60.get(c,{}).get(d,False):continue
                if is_bear and pool_mode!='defensive' and PULLBACK_MIN>0:
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
        trades.append({'pnl':pnl,'r':(px-bp)/bp,'b':entry_d,'s':all_dates[-1],'pool':pool_mode});cash=shares*px

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
    yr_pnl=defaultdict(float);pool_days={'all':0,'defensive':0}
    for t in trades:
        if t.get('b'):yr_pnl[t['b'][:4]]+=t['pnl'];pool_days[t.get('pool','all')]+=1
    return sh,tr,mdd,len(st),wr,yr_pnl,pool_days

print('='*110)
print('  ADAPTIVE POOL SWITCHING · Based on rolling P&L')
print('='*110)

# Grid
loss_levels=[-0.10,-0.15,-0.20,-0.30]  # loss threshold as fraction of INIT
gain_levels=[0.05,0.10,0.15]  # gain to switch back
lookbacks=[5,8,12]

results=[]
for lt in loss_levels:
    for gt in gain_levels:
        for lb in lookbacks:
            sh,tr,mdd,np,wr,yr,pd=run(lt,gt,lb)
            y22=yr.get('2022',0)/INIT*100;y25=yr.get('2025',0)/INIT*100
            y20=yr.get('2020',0)/INIT*100;y21=yr.get('2021',0)/INIT*100
            y23=yr.get('2023',0)/INIT*100;y24=yr.get('2024',0)/INIT*100
            y26=yr.get('2026',0)/INIT*100
            cum=1.0
            for r in[y20,y21,y22,y23,y24,y25,y26]:cum*=1+r/100
            total=(cum-1)*100
            def_pct=pd.get('defensive',0)/max(np,1)*100
            label='Loss<%.0f%% Gain>%.0f%% LB=%d'%(lt*100,gt*100,lb)
            results.append((label,lt,gt,lb,sh,y20,y21,y22,y23,y24,y25,y26,total,np,wr,def_pct))

# Print all
print('  %-35s %7s %7s %7s %7s %7s %7s %7s %7s %7s %7s %7s %7s'%('Config','S','2020','2021','2022','2023','2024','2025','2026','Total','Trd','Win','Def%'))
print('  '+'-'*130)
for r in results:
    label,lt,gt,lb,sh,y20,y21,y22,y23,y24,y25,y26,total,np,wr,def_pct=r
    print('  %-35s %7.3f %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+7.0f%% %5d %4.0f%% %5.0f%%'%(
        label,sh,y20,y21,y22,y23,y24,y25,y26,total,np,wr*100,def_pct))

# Best by 2022
results.sort(key=lambda x:x[7])
print('\n\nBEST 2022 (lowest loss):')
for r in results[:8]:
    print('  %-35s 2022=%+.1f%% 2025=%+.0f%% Total=%+.0f%% Def%%=%.0f%%'%(r[0],r[7],r[10],r[11],r[13]))

# Best by Total
results.sort(key=lambda x:-x[11])
print('\nBEST TOTAL RETURN:')
for r in results[:8]:
    print('  %-35s Total=%+.0f%% 2022=%+.1f%% 2025=%+.0f%% Def%%=%.0f%%'%(r[0],r[11],r[7],r[10],r[13]))

# Best balanced
results.sort(key=lambda x:x[7]*100+x[10]*0.5)
print('\nBEST BALANCED (2022*100+2025/2):')
for r in results[:8]:
    print('  %-35s 2022=%+.1f%% 2025=%+.0f%% Total=%+.0f%% S=%.3f Def%%=%.0f%%'%(r[0],r[7],r[10],r[11],r[4],r[13]))

# Comparison
print('\n\nBASELINES:')
print('  V7+Pullback (no switch): 2022=-19.5%%  2025=+426%%')
print('  V8 (always defensive):   2022=-2.1%%   2025=+248%%')
print('  V6 (ETF>MA60 flat):      2022=-39.4%%  2025=+392%%')

print('\nDone!')
