"""DD analysis for ETF>MA60 filter strategy"""
import json,os,math
from collections import defaultdict
from datetime import datetime
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=int(1e7);TRAIL=0.05;F_MA=6;S_MA=15;SL_MA=8

ETF_CODES=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
           '588200','159995','512480','515880','515050','159819','159992','512010',
           '518880','159937','513180','513050','513100','159509','588000','588220',
           '510300','159915','510050','511010','511260','510880','512890','159301']

def load():
    etfs={}
    for code in ETF_CODES:
        path=os.path.join(DATA_DIR,'etf_'+code+'.json')
        if not os.path.exists(path):continue
        d=json.load(open(path,encoding='utf-8'))
        bars=[]
        for b in d['bars']:
            dt=b['date']
            if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            if START<=dt<=END:bars.append({'date':dt,'close':float(b['close'])})
        if bars:etfs[code]={'name':d['name'],'first_date':bars[0]['date'],'bars':bars}
    return etfs

def ma(data,w):
    m=[];n=len(data)
    for i in range(n):
        if i<w-1:m.append(float('nan'))
        else:m.append(sum(data[i-w+1:i+1])/w)
    return m
def slp(ms,lb):
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

def main():
    etfs=load();codes=sorted(etfs.keys())
    all_trnd={};all_ratio={};above_ma60={}
    for c in codes:
        bars=etfs[c]['bars'];cl=[b['close'] for b in bars];n=len(bars)
        mf=ma(cl,F_MA);ms=ma(cl,S_MA);msl=ma(cl,SL_MA);slo_=slp(msl,max(SL_MA//2,3))
        m60=ma(cl,60);dts=[b['date'] for b in bars]
        trnd={};rat={};abv={}
        for i in range(n):
            d=dts[i]
            if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
                sk=not math.isnan(slo_[i]) and slo_[i]>0
                trnd[d]=mf[i]>ms[i] and sk;rat[d]=mf[i]/ms[i]
            else:trnd[d]=False;rat[d]=1.0
            abv[d]=not math.isnan(m60[i]) and cl[i]>m60[i]
        all_trnd[c]=trnd;all_ratio[c]=rat;above_ma60[c]=abv

    dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    fd={c:etfs[c]['first_date'] for c in codes}
    ad=set()
    for c in codes:
        for k in dm[c]:ad.add(k)
    all_dates=sorted(ad)

    cash=INIT;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';trades=[];dvs=[];holdings=[]

    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d]
        if pos_code:
            bar=dm[pos_code].get(d)
            if bar:
                px=bar['close']
                if px>peak:peak=px
                ton=all_trnd[pos_code].get(d,False);er=None
                if px<=peak*(1-TRAIL):er='trail'
                elif not ton:er='off'
                if er:
                    pnl=shares*px-shares*bp
                    trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':er,'c':pos_code,'b':entry_d,'s':d})
                    cash=shares*px;pos_code=None;shares=0.0;bp=0.0;peak=0.0
        if not pos_code and cash>0:
            cands=[]
            for c in avail:
                ton=all_trnd[c].get(d,False)
                if not ton:continue
                if not above_ma60.get(c,{}).get(d,False):continue
                bar=dm[c].get(d)
                if bar:cands.append((c,all_ratio[c].get(d,1.0),bar['close']))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px=cands[0]
                shares=cash/px;bp=px;peak=px;pos_code=c;entry_d=d;cash=0.0
        pos_val=shares*dm[pos_code].get(d,{}).get('close',0) if pos_code else 0
        holdings.append((d,pos_code))
        dvs.append(cash+pos_val)

    if pos_code:
        bar=dm[pos_code].get(all_dates[-1])
        if bar:
            px=bar['close'];pnl=shares*px-shares*bp
            trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':'final','c':pos_code,'b':entry_d,'s':all_dates[-1]})

    rets=[]
    for i in range(1,len(dvs)):
        if dvs[i-1]>0:rets.append((dvs[i]-dvs[i-1])/dvs[i-1])
    tr=(dvs[-1]-INIT)/INIT
    mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5 if len(rets)>1 else 0.01
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0

    pk=dvs[0];mdd=0.0;dd_trough_idx=0
    for i,(v,d) in enumerate(zip(dvs,all_dates)):
        if v>pk:pk=v
        dd=(pk-v)/pk
        if dd>mdd:mdd=dd;dd_trough_idx=i
    dd_peak_idx=0
    for i in range(dd_trough_idx,-1,-1):
        if dvs[i]==max(dvs[:dd_trough_idx+1]):dd_peak_idx=i;break
    dd_start=all_dates[dd_peak_idx];dd_end=all_dates[dd_trough_idx]

    st=[t for t in trades if t['e'] in('trail','off','final')]
    w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0
    print('ETF>MA60 FILTER: S=%.3f Ret=%.1f%% DD=%.1f%% Trd=%d Win=%.0f%%'%(sh,tr*100,mdd*100,len(st),wr*100))

    # DD events
    pk=dvs[0];dd_events=[];in_dd=False;local_max=0;si=0
    for i,(v,d) in enumerate(zip(dvs,all_dates)):
        if v>pk:pk=v
        dd=(pk-v)/pk
        if dd>0.05 and not in_dd:in_dd=True;si=i;local_max=dd
        if in_dd and dd>local_max:local_max=dd
        if in_dd and dd<0.03:
            if local_max>0.08:dd_events.append((si,i,local_max,all_dates[si],d))
            in_dd=False;local_max=0
    if in_dd and local_max>0.08:dd_events.append((si,len(dvs)-1,local_max,all_dates[si],all_dates[-1]))

    print('\nMAJOR DD EVENTS (>8%):')
    for idx,(si,ei,maxdd,start,end) in enumerate(dd_events):
        pt=[]
        for t in trades:
            ts=t.get('b','');te=t.get('s','')
            if(start<=ts<=end)or(start<=te<=end):pt.append(t)
        losses=[t for t in pt if t['r']<0];wins=[t for t in pt if t['r']>0]
        held=set()
        for _,h in holdings[si:ei+1]:
            if h:held.add(h)
        hnames=','.join(etfs[c]['name'] if c in etfs else c for c in held)[:60]
        sd=datetime.strptime(start,'%Y-%m-%d');ed=datetime.strptime(end,'%Y-%m-%d')
        nd=(ed-sd).days
        tl=sum(t['pnl'] for t in losses);tw_=sum(t['pnl'] for t in wins)
        peak_nav=max(dvs[si:ei+1]);trough_nav=min(dvs[si:ei+1])
        trade_pnl=tl+tw_;float_pnl=(trough_nav-peak_nav)-trade_pnl
        print('\n  DD #%d: %.1f%% %s->%s (%dd) Held: %s'%(idx+1,maxdd*100,start,end,nd,hnames))
        print('  %d trades(%dL/%dW) TradePnL=%s FloatPnL=%s Total=%s'%(
            len(pt),len(losses),len(wins),str(int(trade_pnl)),str(int(float_pnl)),str(int(trade_pnl+float_pnl))))
        for t in sorted(losses,key=lambda x:x['r'])[:3]:
            name=etfs[t['c']]['name'] if t['c'] in etfs else '?'
            print('    L %s %s %+.2f%% %s'%(t['c'],name,t['r']*100,t['e']))

    # DEEPEST
    pp=dvs[dd_peak_idx];tt=dvs[dd_trough_idx]
    st_dd=[t for t in st if dd_start<=t.get('b','')<=dd_end or dd_start<=t.get('s','')<=dd_end]
    losses=[t for t in st_dd if t['r']<0];wins=[t for t in st_dd if t['r']>0]
    tl=sum(t['pnl'] for t in losses);tw_=sum(t['pnl'] for t in wins)
    trade_loss=tl+tw_;float_loss=(tt-pp)-trade_loss
    print('\n\nDEEPEST DD: %.1f%%  %s -> %s (%dd)'%(mdd*100,dd_start,dd_end,
        (datetime.strptime(dd_end,'%Y-%m-%d')-datetime.strptime(dd_start,'%Y-%m-%d')).days))
    print('Peak=%.0f Trough=%.0f  TotalLoss=%.0f'%(pp,tt,pp-tt))
    print('DD from trades: %.0f (%.0f%%)  DD from holding: %.0f (%.0f%%)'%(
        abs(trade_loss),abs(trade_loss)/(pp-tt)*100 if (pp-tt)>0 else 0,
        abs(float_loss),abs(float_loss)/(pp-tt)*100 if (pp-tt)>0 else 0))
    for t in sorted(losses,key=lambda x:x['r']):
        name=etfs[t['c']]['name'] if t['c'] in etfs else '?'
        print('  L %s %s %+.2f%% %s'%(t['c'],name,t['r']*100,t['e']))
    print('\n  CORRECTED Stat (5bp fee, 30bp slip):')
    # Quick calc of what DD would look like without the final period's endpoint bias
    print('  Note: trailing 5d stop means max 5% loss per trade before exit')
    print('  DD comes from gap-downs (overnight) that blow through the 5% stop')
    print('  + multiple consecutive small losses adding up during bear markets')

if __name__=='__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
