"""V6 vs V7 vs V8 · Head-to-head annual comparison"""
import json,os,math
from collections import defaultdict
from datetime import datetime
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=int(1e7);F_MA=6;S_MA=15;SL_MA=8

ETF_CODES=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
           '588200','159995','512480','515880','515050','159819','159992','512010',
           '518880','159937','513180','513050','513100','159509','588000','588220',
           '510300','159915','510050','511010','511260','510880','512890','159301']

DEFENSIVE=['518880','159937','518800','511010','511260','510880','512890','510050']

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
            if START<=dt<=END:bars.append({'date':dt,'close':px})
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
        break

dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
fd={c:etfs[c]['first_date'] for c in codes}
ad=set()
for c in codes:
    for k in dm[c]:ad.add(k)
all_dates=sorted(ad)

def run(version):
    """V6: Trail=5%+ETF>MA60 | V7: Dual-Trail | V8: Dual-Trail + bear defensive pool"""
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]

    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=bear_slope.get(d,False)

        # V6: flat Trail=5%
        if version==6:cur_trail=0.05;cur_mh=0
        elif version==7:cur_trail=0.05 if is_bear else 0.03;cur_mh=7 if is_bear else 0
        else:cur_trail=0.05 if is_bear else 0.03;cur_mh=7 if is_bear else 0

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
                # V6: always ETF>MA60; V7: always ETF>MA60; V8: ETF>MA60 + bear defensive
                if not above_ma60.get(c,{}).get(d,False):continue
                if version==8 and is_bear and c not in DEFENSIVE:continue
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
    return sh,tr,mdd,len(st),wr,yr_pnl,dvs

print('='*120)
print('  V6 vs V7 vs V8 · HEAD-TO-HEAD ANNUAL COMPARISON')
print('='*120)
print()
print('  V6: Trail=5%% + ETF>MA60')
print('  V7: Dual-Trail (Bull=3%% Bear=5%%+MH7) + ETF>MA60')
print('  V8: V7 + Bear防御池(8只黄金/债券/红利)')
print()
print('  %-8s %7s %7s %7s %7s %7s %7s %7s %7s %7s %7s %7s'%(
    'Version','2020','2021','2022','2023','2024','2025','2026','Total','S','DD','Trd'))
print('  '+'-'*110)

all_v=[]
for v,label in[(6,'V6'),(7,'V7'),(8,'V8')]:
    sh,tr,mdd,np,wr,yr,dvs=run(v)
    yr_rets=[]
    for y in range(2020,2027):
        yr_rets.append(yr.get(str(y),0)/INIT*100)
    # Compound total
    cum=1.0
    for r in yr_rets:cum*=(1+r/100)
    cum_ret=(cum-1)*100
    print('  %-8s %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %+7.1f%% %7.3f %5.1f%% %5d'%(
        label,yr_rets[0],yr_rets[1],yr_rets[2],yr_rets[3],yr_rets[4],yr_rets[5],yr_rets[6],
        cum_ret,sh,mdd*100,np))
    all_v.append((label,yr_rets,cum_ret,sh,mdd,np))

# Cumulative growth comparison
print()
print('  累计净值对比 (初始1000万):')
for label,yr_rets,_,_,_,_ in all_v:
    navs=[1000]
    for r in yr_rets:navs.append(navs[-1]*(1+r/100))
    nav_str=' '.join('%6.0f'%n for n in navs[1:])
    print('  %-8s NAV=%s  期末=%.0f万'%(label,nav_str,navs[-1]))

# Best year analysis
print()
print('  关键维度对比:')
print('  %-8s %8s %8s %8s %8s %8s'%('','Sharpe','MaxDD','BestYr','WorstYr','Trd/Yr'))
for label,yr_rets,cum_ret,sh,mdd,np in all_v:
    best=max(yr_rets);worst=min(yr_rets)
    trd_per_yr=np/6.5
    print('  %-8s %8.3f %7.1f%% %+7.1f%% %+7.1f%% %7.1f'%(label,sh,mdd*100,best,worst,trd_per_yr))

print('\nDone!')
