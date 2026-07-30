"""15-Index Pool Sweep · N/15 slope>0 DEF->ALL switch"""
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

# ===== 15 INDEX POOL =====
INDEX_CODES={
    # Layer 1: Broad market (4)
    '510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    # Layer 2: Tech sectors (4)
    '515880':'通信ETF','512480':'半导体ETF','159819':'人工智能ETF','588200':'科创芯片ETF',
    # Layer 3: Global/offshore (2)
    '513100':'纳指ETF','513050':'中概互联ETF',
    # Layer 4: Defensive/commodity (3)
    '511260':'十年国债','512890':'红利低波','518800':'黄金ETF',
    # Layer 5: Healthcare/consumption (2)
    '159992':'创新药ETF','512010':'医药ETF',
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
    m=[float('nan')]*(w-1)
    for i in range(w-1,len(d)):m.append(sum(d[i-w+1:i+1])/w)
    return m
def slp(ms,lb):
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

# Precompute trading signals
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

# Index slope signals
index_slope={}
for code,name in INDEX_CODES.items():
    if code not in etfs:continue
    cl=[b['close'] for b in etfs[code]['bars']];dts=[b['date'] for b in etfs[code]['bars']]
    m60=ma(cl,60);sl=slp(m60,20)
    index_slope[code]={dts[i]:not math.isnan(sl[i]) and sl[i]>0 for i in range(len(dts))}

dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
fd={c:etfs[c]['first_date'] for c in codes}
ad=set()
for c in codes:ad.update(dm[c].keys())
all_dates=sorted(ad)
TOTAL_INDEX=len(index_slope)
print('Signals ready. %d trading indices, %d ETFs in trading pool.'%(TOTAL_INDEX,len(codes)))

def run(need_n,has_time,time_days):
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]
    pool_mode='all';rolling_pnl=[];switched_date=''

    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=not index_slope.get('510300',{}).get(d,False)
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
                    trades.append({'pnl':pnl,'r':(px-bp)/bp,'b':entry_d,'s':d})
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl)>5:rolling_pnl.pop(0)
                    cash=shares*px;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None

                    if len(rolling_pnl)>=5 and pool_mode=='all' and sum(rolling_pnl)<-0.10*INIT:
                        pool_mode='defensive';switched_date=d

                    if pool_mode=='defensive':
                        n_pos=sum(1 for c in INDEX_CODES if index_slope.get(c,{}).get(d,False))
                        switch=False
                        if n_pos>=need_n:switch=True
                        if has_time and switched_date:
                            days_in=(dt_obj-datetime.strptime(switched_date,'%Y-%m-%d')).days
                            if days_in>time_days:switch=True
                        if switch:pool_mode='all';switched_date=''

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
                        if pdi>=0 and pdi<len(all_dates):hi20=max(hi20,etf_highs[c].get(all_dates[pdi],0))
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
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0;ar=(1+tr)**(TD/len(rets))-1 if tr>-1 else -1
    st=[t for t in trades if t['b']!=''];w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0
    yr_pnl=defaultdict(float)
    for t in trades:
        if t.get('b'):yr_pnl[t['b'][:4]]+=t['pnl']
    return sh,tr,mdd,ar,len(st),wr,yr_pnl

print('\n'+'='*120)
print('  15-INDEX POOL · N/%d slope>0 for DEF->ALL'%TOTAL_INDEX)
print('  Added: 人工智能,科创芯片,中概互联,黄金ETF,医药ETF')
print('='*120)
print('  %-40s %7s %7s %7s %7s %7s %7s %7s %7s %7s %7s %5s'%('Condition','S','2020','2021','2022','2023','2024','2025','2026','Total','Ann','Trd'))
print('  '+'-'*125)

results=[]
for n in[3,4,5,6,7,8,9,10,11,12]:
    for has_t,td in[(False,0),(True,90)]:
        sh,tr,mdd,ar,np,wr,yr=run(n,has_t,td)
        cum=1.0
        for y in['2020','2021','2022','2023','2024','2025','2026']:cum*=1+yr.get(y,0)/INIT/100
        label=('%d/%d slope>0'%(n,TOTAL_INDEX)) if not has_t else ('%d/%d OR 90d'%(n,TOTAL_INDEX))
        results.append((label,sh,yr,mdd,ar,np,wr,n,has_t,(cum-1)*100))
        print('  %-40s %7.3f %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+7.0f%% %6.2f%% %5d'%(
            label,sh,yr.get('2020',0)/INIT*100,yr.get('2021',0)/INIT*100,yr.get('2022',0)/INIT*100,
            yr.get('2023',0)/INIT*100,yr.get('2024',0)/INIT*100,yr.get('2025',0)/INIT*100,
            yr.get('2026',0)/INIT*100,(cum-1)*100,ar*100,np))

# Ranking
results.sort(key=lambda x:-x[1])
print('\n\nTOP 15 BY SHARPE:')
for i,(label,sh,yr,mdd,ar,np,wr,n,ht,total) in enumerate(results[:15]):
    y22=yr.get('2022',0)/INIT*100;y25=yr.get('2025',0)/INIT*100;y26=yr.get('2026',0)/INIT*100
    best_tag='★' if not ht else ''
    print('  %2d. %-40s S=%.3f 2022=%+.1f%% 2025=%+.0f%% 2026=%+.0f%% Total=%+.0f%% Ann=%.1f%% %s'%(i+1,label,sh,y22,y25,y26,total,ar*100,best_tag))

# Best by 2022
results.sort(key=lambda x:-(x[2].get('2022',0)))
print('\n\nTOP 2022 (least loss):')
for i,(label,sh,yr,mdd,ar,np,wr,n,ht,total) in enumerate(results[:8]):
    y22=yr.get('2022',0)/INIT*100;y26=yr.get('2026',0)/INIT*100
    print('  %-40s 2022=%+.1f%% 2026=%+.0f%% S=%.3f'%(label,y22,y26,sh))

# Compare with 10-index best
print('\n\n  VS 10-INDEX BASELINE:')
print('  7/10 slope>0 ★:          S=1.521  2022=-5.7%%  2025=+551%%  2026=+734%%')
print('  7/10 slope>0 OR 90d:     S=1.510  2022=-12.2%%  2025=+567%%  2026=+757%%')

print('\nDone!')
