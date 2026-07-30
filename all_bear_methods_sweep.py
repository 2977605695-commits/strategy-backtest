"""Dual-Trail + Bear Protection · 7 methods · Clean"""
import json,os,math
from collections import defaultdict
from datetime import datetime
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=int(1e7);F_MA=6;S_MA=15;SL_MA=8
TRAIL_BULL=0.03;TRAIL_BEAR=0.05;BEAR_MH=7;BULL_MH=0

ETF_CODES=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
           '588200','159995','512480','515880','515050','159819','159992','512010',
           '518880','159937','513180','513050','513100','159509','588000','588220',
           '510300','159915','510050','511010','511260','510880','512890','159301']

DEFENSIVE=['518880','159937','518800','511010','511260','510880','512890','510050']

def load():
    etfs={}
    for code in ETF_CODES:
        path=os.path.join(DATA_DIR,'etf_'+code+'.json')
        if not os.path.exists(path):continue
        d=json.load(open(path,encoding='utf-8'))
        bars=[]
        for b in d['bars']:
            dt=b['date'];px=float(b['close'])
            if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            if START<=dt<=END:bars.append({'date':dt,'close':px})
        if bars:etfs[code]={'name':d['name'],'first_date':bars[0]['date'],'bars':bars}
    return etfs

def ma(data,w):
    m=[];n=len(data)
    for i in range(n):
        if i<w-1:m.append(float('nan'))
        else:m.append(sum(data[i-w+1:i+1])/w)
    return m
def slp(ms,lb=5):
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

etfs=load();codes=sorted(etfs.keys())
print('Computing signals for %d ETFs...'%len(codes))

all_trnd={};all_ratio={};above_ma60={}
for c in codes:
    bars=etfs[c]['bars'];cl=[b['close'] for b in bars]
    mf=ma(cl,F_MA);ms=ma(cl,S_MA);msl=ma(cl,SL_MA);slo_=slp(msl,max(SL_MA//2,3))
    m60=ma(cl,60);dts=[b['date'] for b in bars]
    trnd={};rat={};abv={}
    for i in range(len(bars)):
        d=dts[i]
        if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
            sk=not math.isnan(slo_[i]) and slo_[i]>0
            trnd[d]=mf[i]>ms[i] and sk;rat[d]=mf[i]/ms[i]
        else:trnd[d]=False;rat[d]=1.0
        abv[d]=not math.isnan(m60[i]) and cl[i]>m60[i]
    all_trnd[c]=trnd;all_ratio[c]=rat;above_ma60[c]=abv

# Bear regime
bear_slope={}
for c in codes:
    if c=='510300':
        cl=[b['close'] for b in etfs[c]['bars']]
        m60=ma(cl,60);sl=slp(m60,20);dts=[b['date'] for b in etfs[c]['bars']]
        for i in range(len(dts)):bear_slope[dts[i]]=not math.isnan(sl[i]) and sl[i]<0
        # HS300/MA200 ratio
        m200=ma(cl,200)
        hs200_ratio={}
        for i in range(len(dts)):
            hs200_ratio[dts[i]]=cl[i]/m200[i] if not math.isnan(m200[i]) and m200[i]>0 else 1.0
        break

dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
fd={c:etfs[c]['first_date'] for c in codes}
ad=set()
for c in codes:
    for k in dm[c]:ad.add(k)
all_dates=sorted(ad)

def run(use_ma60=True,bear_pool='all',hs200_max=None):
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]
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
                    trades.append({'pnl':pnl,'r':(px-bp)/bp,'b':entry_d,'s':d,'is_bear_entry':bear_slope.get(entry_d,False)})
                    cash=shares*px;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None

        if not pos and cash>0:
            cands=[]
            for c in avail:
                ton=all_trnd[c].get(d,False)
                if not ton:continue
                # Filter 1: ETF>MA60
                if use_ma60 and not above_ma60.get(c,{}).get(d,False):continue
                # Filter 2: bear pool restriction
                if is_bear and bear_pool=='defensive' and c not in DEFENSIVE:continue
                # Filter 3: HS300/MA200 ceiling
                if is_bear and hs200_max:
                    r=hs200_ratio.get(d,1.0)
                    if r>hs200_max:continue
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
        if bar:
            px=bar['close'];pnl=shares*px-shares*bp
            trades.append({'pnl':pnl,'r':(px-bp)/bp,'b':entry_d,'s':all_dates[-1],'is_bear_entry':bear_slope.get(entry_d,False)})
            cash=shares*px

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
    st=[t for t in trades if t['b']!='']
    w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0
    yr_pnl=defaultdict(float)
    for t in trades:
        if t.get('b'):yr_pnl[t['b'][:4]]+=t['pnl']
    return sh,tr,mdd,len(st),wr,yr_pnl

# Run all
configs=[
    ('1.BASELINE(无MA60无保护)', False, 'all', None),
    ('2.ETF>MA60', True, 'all', None),
    ('3.ETF>MA60+Bear防御', True, 'defensive', None),
    ('4.HS200<1.05', True, 'all', 1.05),
    ('5.HS200<1.10', True, 'all', 1.10),
    ('6.HS200<1.15', True, 'all', 1.15),
    ('7.HS200<1.20', True, 'all', 1.20),
    ('8.ETF>MA60+防御+HS200<1.10', True, 'defensive', 1.10),
    ('9.ETF>MA60+防御+HS200<1.15', True, 'defensive', 1.15),
]

print('\n'+'='*110)
print('  DUAL-TRAIL + 7 BEAR PROTECTIONS')
print('='*110)
print('  %-35s %7s %9s %7s %5s %5s %7s %7s %7s %7s'%('Method','S','Ret','DD','Trd','Win','2020','2021','2022','2025'))
print('  '+'-'*110)

all_res=[]
for label,use_ma60,bear_pool,hs200_max in configs:
    sh,tr,mdd,np,wr,yr=run(use_ma60,bear_pool,hs200_max)
    all_res.append((label,sh,tr,mdd,np,wr,yr))
    print('  %-35s %7.3f %8.2f%% %6.2f%% %5d %4.0f%% %+7.1f%% %+7.1f%% %+7.1f%% %+7.0f%%'%(
        label,sh,tr*100,mdd*100,np,wr*100,
        yr.get('2020',0)/INIT*100,yr.get('2021',0)/INIT*100,
        yr.get('2022',0)/INIT*100,yr.get('2025',0)/INIT*100))

all_res.sort(key=lambda x:-x[1])
print('\n\nRANKING:')
for i,(label,sh,tr,mdd,np,wr,yr) in enumerate(all_res):
    print('  %2d. %-35s S=%.3f DD=%.1f%% 2022=%+.1f%% 2025=%+.0f%%'%(
        i+1,label,sh,mdd*100,yr.get('2022',0)/INIT*100,yr.get('2025',0)/INIT*100))

print('\nDone!')
