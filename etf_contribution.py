"""Analyze which ETFs actually contributed in V10 strategy"""
import json,os,math
from collections import defaultdict
from datetime import datetime
DATA_DIR='data';START='2020-01-01';END='2026-07-30'
RF=0.025;TD=252;INIT=int(1e7);F_MA=6;S_MA=15;SL_MA=8
TRAIL_BULL=0.03;TRAIL_BEAR=0.06;BULL_MH=0;BEAR_MH=7;PULLBACK_MIN=0.05
NEED_N=7

ETF_CODES=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
           '588200','159995','512480','515880','515050','159819','159992','512010',
           '518880','159937','513180','513050','513100','159509','588000','588220',
           '510300','159915','510050','511010','511260','510880','512890','159301']
DEFENSIVE=['518880','159937','518800','511010','511260','510880','512890','510050']
INDEX_CODES={
    '510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF',
    '513100':'纳指ETF','511260':'十年国债','512890':'红利低波','159992':'创新药ETF',
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

# Run strategy
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
                trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':er,'c':pos,'b':entry_d,'s':d,'pool':pool_mode})
                rolling_pnl.append(pnl)
                if len(rolling_pnl)>5:rolling_pnl.pop(0)
                cash=shares*px;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None

                if len(rolling_pnl)>=5 and pool_mode=='all' and sum(rolling_pnl)<-0.10*INIT:
                    pool_mode='defensive';switched_date=d

                if pool_mode=='defensive':
                    n_pos=sum(1 for c in INDEX_CODES if index_slope.get(c,{}).get(d,False))
                    if n_pos>=NEED_N:pool_mode='all';switched_date=''

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
    trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':'final','c':pos,'b':entry_d,'s':all_dates[-1],'pool':pool_mode});cash=shares*px

st=[t for t in trades if t['b']!='']

# ===== ANALYSIS =====
print('='*100)
print('  ETF CONTRIBUTION ANALYSIS · 33 ETFs')
print('='*100)

# Per ETF: PnL, trade count, win rate, signal count, avg ratio
etf_pnl=defaultdict(float);etf_trd=defaultdict(int);etf_ret=defaultdict(list)
etf_selected=defaultdict(int)  # times selected as #1 candidate

for t in st:
    etf_pnl[t['c']]+=t['pnl'];etf_trd[t['c']]+=1;etf_ret[t['c']].append(t['r'])

# Also count buy signal days per ETF
etf_signal_days=defaultdict(int)
for c in codes:
    for d in all_dates:
        if all_trnd.get(c,{}).get(d,False) and above_ma60.get(c,{}).get(d,False):
            etf_signal_days[c]+=1

print('  %-6s %-20s %5s %5s %6s %10s %8s %6s %5s %-15s'%('Code','Name','Trd','Win%','PnL','SigDays','1stBar','Sharpe','Bars','Sector'))
print('  '+'-'*100)

for c in sorted(etf_pnl.keys(),key=lambda x:-etf_pnl[x]):
    name=etfs[c]['name'];nt=etf_trd[c];pnl=etf_pnl[c]
    wr=sum(1 for r in etf_ret[c] if r>0)/nt*100 if nt else 0
    sig=etf_signal_days[c]
    fb=etfs[c]['first_date'];nb=len(etfs[c]['bars'])
    # Calculate single-ETF Sharpe
    rets_cl=[]
    for t in st:
        if t['c']==c:rets_cl.append(t['r'])
    if rets_cl and len(rets_cl)>1:
        mu=sum(rets_cl)/len(rets_cl);sd_=(sum((r-mu)**2 for r in rets_cl)/(len(rets_cl)-1))**0.5
        sh_=mu/sd_*math.sqrt(TD) if sd_>0 else 0
    else:sh_=0
    # Sector classification
    sector=''
    if c in['518880','159937','518800']:sector='黄金'
    elif c in['511010','511260']:sector='国债'
    elif c in['510880','512890']:sector='红利'
    elif c in['510300','159915','510050']:sector='宽基'
    elif c in['513100','159509','513050','513180']:sector='海外'
    elif c in['159992','512010']:sector='医药'
    else:sector='科技'
    marker='<< ZOMBIE' if nt==0 else (' NEG' if pnl<0 and nt>0 else (' HOT' if pnl>5e6 else ''))
    print('  %-6s %-20s %5d %4.0f%% %+9.0f %6d %8s %6.2f %5d %-15s %s'%(
        c,name,nt,wr,pnl,sig,fb,sh_,nb,sector,marker))

# Summary
print()
unused=[c for c in codes if etf_trd[c]==0]
print('  NEVER TRADED (%d ETFs): %s'%(len(unused),','.join(unused)))
neg=[c for c in codes if etf_pnl[c]<0 and etf_trd[c]>0]
print('  NEGATIVE PnL (%d ETFs): %s'%(len(neg),','.join(neg)))
pos=[c for c in codes if etf_pnl[c]>0]
print('  POSITIVE PnL (%d ETFs): total PnL=%+.0f'%(len(pos),sum(etf_pnl[c] for c in pos)))

# Redundancy analysis
print()
print('  REDUNDANCY ANALYSIS (same-sector ETFs, similar data):')
groups={
    '科创50(3只)':['159782','588080','588000'],
    '科创100(2只)':['588380','588220'],
    '科创芯片(2只)':['588300','588200'],
    '黄金(3只)':['518880','159937','518800'],
    '国债(2只)':['511010','511260'],
}
for label,codes in groups.items():
    pnls=[(c,etf_pnl[c]) for c in codes if c in etf_pnl]
    best=max(pnls,key=lambda x:x[1])
    print('  %s: %s  → keep %s (PnL=%+.0f)'%(label,','.join('%s(%+.0f)'%(c,p) for c,p in pnls),best[0],best[1]))

print()
print('  RECOMMENDATION: Remove NEVER-TRADED + NEGATIVE + REDUNDANT')
# Candidate to keep
keep=set()
for c in codes:
    if etf_trd[c]==0:continue  # never traded
    if etf_pnl[c]<-1e6:continue  # big loser
keep.add(c)
# For redundant groups, keep best
for label,gc in groups.items():
    best=max(gc,key=lambda c:etf_pnl.get(c,-999))
    for c in gc:
        if c!=best and c in keep:keep.discard(c)
# Also keep the DEFENSIVE pool
for c in DEFENSIVE:keep.add(c)
print('  Keep: %d ETFs -> %s'%(len(keep),','.join(sorted(keep))))

print('\nDone!')
