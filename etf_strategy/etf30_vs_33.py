"""30 vs 33 ETF Pool 路 Detailed Head-to-Head Comparison"""
import json,os,math
from collections import defaultdict
from datetime import datetime
DATA_DIR='data';START='2020-01-01';END='2026-07-30'
RF=0.025;TD=252;INIT=int(1e7);F_MA=6;S_MA=15;SL_MA=8
TRAIL_BULL=0.03;TRAIL_BEAR=0.06;BULL_MH=0;BEAR_MH=7;PULLBACK_MIN=0.05;NEED_N=7

ETF_33=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
        '588200','159995','512480','515880','515050','159819','159992','512010',
        '518880','159937','513180','513050','513100','159509','588000','588220',
        '510300','159915','510050','511010','511260','510880','512890','159201']
# Remove: 589720(浜忔崯绗竴), 159995(浜忔崯绗簩), 159782(绉戝垱50鍐椾綑)
ETF_30=[c for c in ETF_33 if c not in['589720','159995','159782']]

DEF_POOL=['518880','159937','518800','511010','511260','510880','512890','159201','510050']

INDEX_CODES={
    '510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF',
    '513100':'纳指ETF','511260':'十年国债','512890':'红利低波','159992':'创新药ETF',
}

def load(pool):
    etfs={}
    for code in pool:
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

def precompute(etfs):
    global all_trnd,all_ratio,above_ma60,etf_highs,index_slope
    all_trnd={};all_ratio={};above_ma60={};etf_highs={};index_slope={}
    for c in etfs:
        bars=etfs[c]['bars'];cl=[b['close'] for b in bars];hi=[b['high'] for b in bars]
        mf=ma(cl,F_MA);ms=ma(cl,S_MA);msl=ma(cl,SL_MA);slo_=slp(msl,max(SL_MA//2,3))
        m60=ma(cl,60);dts=[b['date'] for b in bars]
        for i in range(len(bars)):
            d=dts[i]
            if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
                sk=not math.isnan(slo_[i]) and slo_[i]>0
                all_trnd.setdefault(c,{})[d]=mf[i-1]>ms[i-1] and sk if i>0 else False
                all_ratio.setdefault(c,{})[d]=mf[i-1]/ms[i-1] if i>0 and ms[i-1]>0 else 1.0
            else:
                all_trnd.setdefault(c,{})[d]=False;all_ratio.setdefault(c,{})[d]=1.0
            above_ma60.setdefault(c,{})[d]=not math.isnan(m60[i-1]) and cl[i-1]>m60[i-1] if i>0 else False
            etf_highs.setdefault(c,{})[d]=hi[i]
    for code,name in INDEX_CODES.items():
        if code not in etfs:continue
        cl=[b['close'] for b in etfs[code]['bars']];dts=[b['date'] for b in etfs[code]['bars']]
        m60=ma(cl,60);sl=slp(m60,20)
        index_slope[code]={dts[i]:(not math.isnan(sl[i-1]) and sl[i-1]>0) if i>0 else False for i in range(len(dts))}

def run(etfs,def_codes):
    codes=sorted(etfs.keys())
    dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    fd={c:etfs[c]['first_date'] for c in codes}
    ad=set()
    for c in codes:ad.update(dm[c].keys())
    all_dates=sorted(ad)
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[]
    pool_mode='all';rolling_pnl=[];dvs=[];switches=[]

    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=not index_slope.get('510300',{}).get(d,False)
        cur_trail=TRAIL_BEAR if is_bear else TRAIL_BULL;cur_mh=BEAR_MH if is_bear else BULL_MH

        if pos:
            bar=dm[pos].get(d)
            if bar:
                px=bar['close']
                if px>peak:peak=px
                er=None
                if px<=peak*(1-cur_trail):er='trail'
                elif not all_trnd.get(pos,{}).get(d,False):
                    if cur_mh>0 and entry_date and (dt_obj-entry_date).days>=cur_mh:er='off'
                    else:er='off'
                if er:
                    pnl=shares*px-shares*bp
                    trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':er,'c':pos,'b':entry_d,'s':d,'pool':pool_mode})
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl)>5:rolling_pnl.pop(0)
                    cash=shares*px;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
                    prev_mode=pool_mode
                    if len(rolling_pnl)>=5 and pool_mode=='all' and sum(rolling_pnl)<-0.10*INIT:
                        pool_mode='defensive'
                    if pool_mode=='defensive':
                        if sum(1 for c2 in INDEX_CODES if index_slope.get(c2,{}).get(d,False))>=NEED_N:
                            pool_mode='all'
                    if pool_mode!=prev_mode:switches.append((d,prev_mode,pool_mode,sum(t['pnl'] for t in trades[-5:])))

        if not pos and cash>0:
            cands=[]
            for c in avail:
                if pool_mode=='defensive' and c not in def_codes:continue
                if not all_trnd.get(c,{}).get(d,False):continue
                if not above_ma60.get(c,{}).get(d,False):continue
                if PULLBACK_MIN>0:
                    hi20=0
                    for lb in range(1,21):
                        pdi=max(0,len(all_dates)-1-lb)
                        if pdi>=0 and pdi<len(all_dates):hi20=max(hi20,etf_highs[c].get(all_dates[pdi],0))
                    if hi20>0 and (hi20-dm[c][d]['close'])/hi20<PULLBACK_MIN:continue
                bar=dm[c].get(d)
                if bar:cands.append((c,all_ratio.get(c,{}).get(d,1.0),bar['close']))
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
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0;ar=(1+tr)**(TD/len(rets))-1 if tr>-1 else -1
    st=[t for t in trades if t.get('b')];w=sum(1 for t in st if t['r']>0)
    yr_pnl=defaultdict(float);yr_trd=defaultdict(int)
    for t in trades:
        if t.get('b'):yr_pnl[t['b'][:4]]+=t['pnl'];yr_trd[t['b'][:4]]+=1
    return sh,tr,mdd,ar,len(st),w/len(st) if st else 0,yr_pnl,yr_trd,st,switches,dvs

# ===== RUN =====
print('Computing 33-ETF pool...')
all_trnd={};all_ratio={};above_ma60={};etf_highs={};index_slope={}
e33=load(ETF_33);precompute(e33)
s33,tr33,mdd33,ar33,na33,wr33,yr33,yt33,tr33_list,sw33,dv33=run(e33,DEF_POOL)

print('Computing 30-ETF pool...')
all_trnd={};all_ratio={};above_ma60={};etf_highs={};index_slope={}
e30=load(ETF_30);precompute(e30)
s30,tr30,mdd30,ar30,na30,wr30,yr30,yt30,tr30_list,sw30,dv30=run(e30,DEF_POOL)

# ===== OUTPUT =====
print('\n'+'='*110)
print('  30 ETFs vs 33 ETFs 路 DETAILED COMPARISON')
print('='*110)

# 1. Performance table
print('\n  [1] PERFORMANCE OVERVIEW')
print('  %-15s %5s %8s %8s %7s %6s %6s %5s %5s'%('Pool','ETFs','Sharpe','Ret','DD','Ann','Trd','Win','Switches'))
print('  '+'-'*80)
for label,sh,tr,mdd,ar,na,wr,ne,nsw in[
    ('33 ETFs',s33,tr33,mdd33,ar33,na33,wr33,33,len(sw33)),
    ('30 ETFs',s30,tr30,mdd30,ar30,na30,wr30,30,len(sw30))]:
    print('  %-15s %5d %8.3f %7.2f%% %6.2f%% %5.2f%% %5d %4.0f%% %5d'%(label,ne,sh,tr*100,mdd*100,ar*100,na,wr*100,nsw))

# 2. Annual comparison
print('\n  [2] ANNUAL RETURN COMPARISON')
print('  %-15s %7s %7s %7s %7s %7s %7s %7s'%('Pool','2020','2021','2022','2023','2024','2025','2026'))
print('  '+'-'*70)
for label,yr in[('33 ETFs',yr33),('30 ETFs',yr30)]:
    vals=[yr.get(y,0)/INIT*100 for y in['2020','2021','2022','2023','2024','2025','2026']]
    print('  %-15s '+' '.join('%+6.1f%%'%v for v in vals))

# 3. Trade count per year
print('\n  [3] TRADE COUNT PER YEAR')
print('  %-15s %7s %7s %7s %7s %7s %7s %7s'%('Pool','2020','2021','2022','2023','2024','2025','2026'))
print('  '+'-'*70)
for label,yt in[('33 ETFs',yt33),('30 ETFs',yt30)]:
    counts=[yt.get(y,0) for y in['2020','2021','2022','2023','2024','2025','2026']]
    print('  %-15s '+' %6d'*7%tuple(counts))

# 4. Drawdown comparison
print('\n  [4] DRAWDOWN & SHARPE')
print('  %-15s %8s %8s %8s %8s'%('Pool','MaxDD','Calmar','Win/Loss','AvgHold'))
print('  '+'-'*55)
for label,sh,mdd,ar,trades in[('33 ETFs',s33,mdd33,ar33,tr33_list),('30 ETFs',s30,mdd30,ar30,tr30_list)]:
    cm=ar/mdd if mdd>0 else 0
    wl=[t for t in trades if t['r']>0];ll=[t for t in trades if t['r']<=0]
    aw=sum(t['r'] for t in wl)/len(wl) if wl else 0;al_=sum(t['r'] for t in ll)/len(ll) if ll else 0
    wldays=sum((datetime.strptime(t['s'],'%Y-%m-%d')-datetime.strptime(t['b'],'%Y-%m-%d')).days for t in wl)/len(wl) if wl else 0
    ldays=sum((datetime.strptime(t['s'],'%Y-%m-%d')-datetime.strptime(t['b'],'%Y-%m-%d')).days for t in ll)/len(ll) if ll else 0
    print('  %-15s %7.2f%% %8.3f %7.2f %5.0fd/%-4.0fd'%(label,mdd*100,cm,abs(aw/al_) if al_!=0 else 99,wldays,ldays))

# 5. ETF P&L breakdown
print('\n  [5] ETF P&L BREAKDOWN (Top 10 by net contribution)')
etf_pnl30=defaultdict(float);etf_trd30=defaultdict(int)
for t in tr30_list:etf_pnl30[t['c']]+=t['pnl'];etf_trd30[t['c']]+=1
etf_pnl33=defaultdict(float);etf_trd33=defaultdict(int)
for t in tr33_list:etf_pnl33[t['c']]+=t['pnl'];etf_trd33[t['c']]+=1
all_etfs=set(list(etf_pnl33.keys())+list(etf_pnl30.keys()))
print('  %-8s %-20s %10s %5s %10s %5s %8s'%('Code','Name','PnL_33','Trd','PnL_30','Trd','Delta'))
print('  '+'-'*70)
top_by33=sorted(all_etfs,key=lambda c:etf_pnl33.get(c,0),reverse=True)
for c in top_by33[:15]:
    name=e33.get(c,{}).get('name',e30.get(c,{}).get('name','?'));pnl33=etf_pnl33.get(c,0);pnl30=etf_pnl30.get(c,0)
    t33=etf_trd33.get(c,0);t30=etf_trd30.get(c,0)
    delta=pnl30-pnl33
    marker='' if c not in['589720','159995','159782'] else ' << REMOVED'
    print('  %-8s %-20s %+10.0f %5d %+10.0f %5d %+8.0f%s'%(c,name,pnl33,t33,pnl30,t30,delta,marker))

# Removed ETFs specifically
print('\n  [6] REMOVED ETFs IMPACT')
for c in['589720','159995','159782']:
    name=e33[c]['name'];pnl33=etf_pnl33.get(c,0);t33=etf_trd33.get(c,0)
    print('  %s %-15s: 33-pool PnL=%+.0f (%d trades) -> REMOVED'%(c,name,pnl33,t33))

# 7. Switch events comparison
print('\n  [7] POOL SWITCH EVENTS')
print('  %-15s %5s'%('Pool','Switches'))
for label,sw_list in[('33 ETFs',sw33),('30 ETFs',sw30)]:
    print('  %-15s %5d'%(label,len(sw_list)))
    for d,prev,new,pnl in sw_list:
        print('    %s  %s->%s  (PnL=%.0f)'%(d,prev,new,pnl))

# 8. Net equity curve differences
print('\n  [8] NAV GROWTH')
print('  %-15s %12s %12s'%('Pool','Start','End'))
print('  '+'-'*45)
for label,dvs in[('33 ETFs',dv33),('30 ETFs',dv30)]:
    print('  %-15s %12s %12s'%(label,str(int(dvs[0])),str(int(dvs[-1]))))
    # Compute compound growth
    yrs=(datetime.strptime('2026-07-30','%Y-%m-%d')-datetime.strptime('2020-01-02','%Y-%m-%d')).days/365.25
    cagr=((dvs[-1]/dvs[0])**(1/yrs)-1)*100
    print('  %-15s CAGR=%.2f%%'%('',cagr))

# 9. Verdict
print('\n  [9] VERDICT')
d_sh=(s30-s33)/abs(s33)*100 if s33!=0 else 0
d_ret=(tr30-tr33)/abs(tr33)*100 if tr33!=0 else 0
d_dd=(mdd30-mdd33)/abs(mdd33)*100 if mdd33!=0 else 0
print('  Delta Sharpe: %+.1f%%  Delta Return: %+.1f%%  Delta DD: %+.1f%%'%(d_sh,d_ret,d_dd))
if s30>s33:print('  >> 30-ETF pool is BETTER')
else:print('  >> 33-ETF pool is BETTER')

print('\nDone!')


