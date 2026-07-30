"""V7 Improvements · Keep bull gains, reduce bear losses"""
import json,os,math
from collections import defaultdict
from datetime import datetime
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=int(1e7);F_MA=6;S_MA=15;SL_MA=8
TRAIL_BULL=0.03;BULL_MH=0

ETF_CODES=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
           '588200','159995','512480','515880','515050','159819','159992','512010',
           '518880','159937','513180','513050','513100','159509','588000','588220',
           '510300','159915','510050','511010','511260','510880','512890','159301']

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
print('Computing signals...')
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
        abv[d]=not math.isnan(m60[i]) and cl[i]>m60[i]
        hgh[d]=hi[i]
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

def run(trail_bear,bear_mh,bear_entry_filter,date_idx_lookup):
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]
    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=bear_slope.get(d,False)
        cur_trail=trail_bear if is_bear else TRAIL_BULL
        cur_mh=bear_mh if is_bear else BULL_MH

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
                    trades.append({'pnl':pnl,'r':(px-bp)/bp,'b':entry_d,'s':d})
                    cash=shares*px;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None

        if not pos and cash>0:
            cands=[]
            for c in avail:
                ton=all_trnd[c].get(d,False)
                if not ton:continue
                if not above_ma60.get(c,{}).get(d,False):continue
                # Bear entry filter: only buy if pulled back > X% from 20d high
                if is_bear and bear_entry_filter>0:
                    hi20=0
                    for lookback in range(20):
                        prev_d=all_dates[max(0,len(all_dates)-1-lookback)]
                        h=etf_highs[c].get(prev_d,0)
                        if h>hi20:hi20=h
                    if hi20>0:
                        pullback=(hi20-dm[c][d]['close'])/hi20
                        if pullback<bear_entry_filter:continue
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
        trades.append({'pnl':pnl,'r':(px-bp)/bp,'b':entry_d,'s':all_dates[-1]});cash=shares*px

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

# Sweep
print('\n'+'='*120)
print('  V7 IMPROVEMENTS · SWEEP')
print('  Baseline V7: TrailBear=5%, MH=7, NoEntryFilter, 2022=-43.4%, 2025=+333%')
print('='*120)
print('  %-40s %7s %7s %7s %7s %7s %7s %7s %7s %7s'%('Config','S','2020','2021','2022','2023','2024','2025','2026','Total'))
print('  '+'-'*120)

results=[]
for trail_bear in[0.05,0.06,0.07,0.08,0.10]:
    for bear_mh in[7,10,14,21]:
        for pullback in[0.0,0.03,0.05,0.08]:
            sh,tr,mdd,np,wr,yr=run(trail_bear,bear_mh,pullback,{})
            yr20=yr.get('2020',0)/INIT*100;yr21=yr.get('2021',0)/INIT*100
            yr22=yr.get('2022',0)/INIT*100;yr23=yr.get('2023',0)/INIT*100
            yr24=yr.get('2024',0)/INIT*100;yr25=yr.get('2025',0)/INIT*100
            yr26=yr.get('2026',0)/INIT*100
            cum=1.0
            for r in[yr20,yr21,yr22,yr23,yr24,yr25,yr26]:cum*=1+r/100
            total=(cum-1)*100
            results.append((trail_bear,bear_mh,pullback,sh,yr20,yr21,yr22,yr23,yr24,yr25,yr26,total,np,wr))
            # Only print interesting ones
            if yr22>-20 and yr25>200:
                print('  Tb=%.0f%% MH=%dd Pb=%.0f%%  %7.3f %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+7.1f%% %4d %4.0f%%'%(
                    trail_bear*100,bear_mh,pullback*100,sh,yr20,yr21,yr22,yr23,yr24,yr25,yr26,total,np,wr*100))

# Sort by best combination of 2022 + 2025
results.sort(key=lambda x:x[7]*100+x[10]*0.5,reverse=True)
print('\n\nTOP 10 (by 2022+2025/2 rank):')
for i,r in enumerate(results[:10]):
    trail_bear,bear_mh,pullback,sh,y20,y21,y22,y23,y24,y25,y26,total,np,wr=r
    print('  %2d. Tb=%.0f%% MH=%dd Pb=%.0f%% S=%.3f 2022=%+.1f%% 2025=%+.0f%% Total=%+.0f%% Trd=%d Win=%.0f%%'%(
        i+1,trail_bear*100,bear_mh,pullback*100,sh,y22,y25,total,np,wr*100))

# Best overall by 2022
results.sort(key=lambda x:x[7])
print('\n\nBEST 2022 (by lowest loss):')
for i,r in enumerate(results[:10]):
    trail_bear,bear_mh,pullback,sh,y20,y21,y22,y23,y24,y25,y26,total,np,wr=r
    print('  Tb=%.0f%% MH=%dd Pb=%.0f%% S=%.3f 2022=%+.1f%% 2025=%+.0f%% Total=%+.0f%%'%(
        trail_bear*100,bear_mh,pullback*100,sh,y22,y25,total))

# Best overall by Total
results.sort(key=lambda x:x[11])
print('\n\nBEST TOTAL RETURN:')
for i,r in enumerate(results[:15]):
    trail_bear,bear_mh,pullback,sh,y20,y21,y22,y23,y24,y25,y26,total,np,wr=r
    print('  Tb=%.0f%% MH=%dd Pb=%.0f%% S=%.3f 2022=%+.1f%% 2025=%+.0f%% Total=%+.0f%%'%(
        trail_bear*100,bear_mh,pullback*100,sh,y22,y25,total))

print('\nDone!')
