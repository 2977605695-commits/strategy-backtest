"""V9 Adaptive Pool · Two Best Versions · Full Detail"""
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
ETF_NAMES={c:'' for c in ETF_CODES}
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

def run(loss_thr,gain_thr,lb_n,label):
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]
    pool_mode='all';rolling_pnl=[];switches=[]

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
                    trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':er,'c':pos,'b':entry_d,'s':d,
                                   'pool':pool_mode,'is_bear_entry':bear_slope.get(entry_d,False)})
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl)>lb_n:rolling_pnl.pop(0)
                    cash=shares*px;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None

                    if len(rolling_pnl)>=lb_n:
                        recent=sum(rolling_pnl)
                        if recent<loss_thr*INIT and pool_mode!='defensive':
                            switches.append((d,'ALL→DEF',recent/1e4))
                            pool_mode='defensive'
                        elif recent>gain_thr*INIT and pool_mode!='all':
                            switches.append((d,'DEF→ALL',recent/1e4))
                            pool_mode='all'

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
        trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':'final','c':pos,'b':entry_d,'s':all_dates[-1],
                       'pool':pool_mode,'is_bear_entry':False});cash=shares*px

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
    return sh,tr,mdd,ar,len(st),wr,yr_pnl,trades,dvs,switches

# Run two configs
configs=[(-0.10,0.10,5,'V9a: Loss<-10% Gain>10% LB=5'),(-0.10,0.15,5,'V9b: Loss<-10% Gain>15% LB=5')]
for lt,gt,lb,label in configs:
    sh,tr,mdd,ar,np,wr,yr,trades,dvs,switches=run(lt,gt,lb,label)

    print('='*120)
    print('  V9 ADAPTIVE POOL · %s  (Loss<%.0f%% Gain>%.0f%% LB=%d)'%(label,lt*100,gt*100,lb))
    print('='*120)
    print()
    print('  PERFORMANCE:')
    print('  Sharpe: %.4f  |  Return: %.2f%%  |  Ann: %.2f%%  |  DD: %.2f%%'%(sh,tr*100,ar*100,mdd*100))
    print('  Trades: %d  |  Win: %.1f%% (%d/%d)'%(np,wr*100,sum(1 for t in trades if t.get('b','')!='' and t['r']>0),np))
    print()

    # Annual
    print('  ANNUAL BREAKDOWN:')
    yr_pnl=defaultdict(float);yr_trd=defaultdict(int);yr_wr=defaultdict(list)
    for t in trades:
        if t.get('b',''):
            yr_pnl[t['b'][:4]]+=t['pnl'];yr_trd[t['b'][:4]]+=1;yr_wr[t['b'][:4]].append(t['r']>0)
    print('  %-6s %9s %7s %5s %5s'%('Year','Ret','DD','Trd','Win'))
    for y in sorted(yr_pnl):
        wrs=sum(yr_wr[y])/len(yr_wr[y])*100 if yr_wr[y] else 0
        print('  %-6s %+8.1f%% %7s %5d %4.0f%%'%(y,yr_pnl[y]/INIT*100,'',yr_trd[y],wrs))
    cum=1.0
    for y in sorted(yr_pnl):cum*=1+yr_pnl[y]/INIT/100
    print('  Cumulative: %.1f×'%cum)
    print()

    # Pool switch timeline
    print('  POOL SWITCH TIMELINE (%d switches):'%len(switches))
    def_trd=[t for t in trades if t.get('pool')=='defensive' and t.get('b','')!='']
    all_trd_num=[t for t in trades if t.get('pool')=='all' and t.get('b','')!='']
    print('  All-pool trades: %d  |  Defensive-pool trades: %d  |  Switch events: %d'%(
        len(all_trd_num),len(def_trd),len(switches)))
    for d,direction,pnl in switches:
        print('    %s  %s  (recent PnL=%.0f万)'%(d,direction,pnl))
    print()

    # Trades by pool
    print('  TRADES BY POOL:')
    for pool_label,pool_data in[('ALL POOL',all_trd_num),('DEFENSIVE POOL',def_trd)]:
        if pool_data:
            l_=[t for t in pool_data if t['r']<0];w_=[t for t in pool_data if t['r']>0]
            tl=sum(t['pnl'] for t in l_);tw=sum(t['pnl'] for t in w_)
            avg_d=sum((datetime.strptime(t['s'],'%Y-%m-%d')-datetime.strptime(t['b'],'%Y-%m-%d')).days for t in pool_data)/len(pool_data)
            print('  %s: %d trades (%dL/%dW=%.0f%%) avg=%.0fd Net=%+.0f Loss=%+.0f Win=%+.0f'%(
                pool_label,len(pool_data),len(l_),len(w_),len(w_)/len(pool_data)*100,avg_d,tl+tw,tl,tw))
    print()

    # Top/bottom ETFs
    etf_pnl=defaultdict(float);etf_trd=defaultdict(int);etf_ret=defaultdict(list)
    for t in trades:
        if t.get('b'):etf_pnl[t['c']]+=t['pnl'];etf_trd[t['c']]+=1;etf_ret[t['c']].append(t['r'])
    print('  TOP 10 ETFs:')
    for c,pnl in sorted(etf_pnl.items(),key=lambda x:-x[1])[:10]:
        name=etfs[c]['name'];wr_=sum(1 for r in etf_ret[c] if r>0)/len(etf_ret[c])*100
        print('  %s %s: PnL=%+.0f Trd=%d Win=%.0f%%'%(c,name,pnl,etf_trd[c],wr_))
    print()
    print('  BOTTOM 5:')
    for c,pnl in sorted(etf_pnl.items(),key=lambda x:x[1])[:5]:
        name=etfs[c]['name']
        print('  %s %s: PnL=%+.0f Trd=%d'%(c,name,pnl,etf_trd[c]))
    print()

    # Win/loss ratio
    st=[t for t in trades if t.get('b')]
    losses=[t for t in st if t['r']<=0];wins_=[t for t in st if t['r']>0]
    al_=sum(t['r'] for t in losses)/len(losses) if losses else 0;aw_=sum(t['r'] for t in wins_)/len(wins_) if wins_ else 0
    print('  WIN/LOSS:')
    print('  Wins: %d avg=%.2f%%  Losses: %d avg=%.2f%%  Ratio=%.2f'%(len(wins_),aw_*100,len(losses),al_*100,abs(aw_/al_) if al_!=0 else 99))
    print()

    # Best/worst trades
    st.sort(key=lambda x:x['r'],reverse=True)
    print('  BEST 10 TRADES:')
    for t in st[:10]:
        name=etfs[t['c']]['name'];bd=datetime.strptime(t['b'],'%Y-%m-%d');sd_=datetime.strptime(t['s'],'%Y-%m-%d')
        nd_=(sd_-bd).days
        print('    %s %s %s→%s %+7.2f%% %-6s %dd %s'%(t['c'],name,t['b'],t['s'],t['r']*100,t['e'],nd_,t.get('pool','?')))
    print()
    print('  WORST 5 TRADES:')
    for t in st[-5:]:
        name=etfs[t['c']]['name']
        print('    %s %s %s→%s %+7.2f%% %-6s %s'%(t['c'],name,t['b'],t['s'],t['r']*100,t['e'],t.get('pool','?')))
    print()
    print()

print('Done!')
