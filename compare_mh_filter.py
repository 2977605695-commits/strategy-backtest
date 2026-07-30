"""ETF>MA60 filter vs MH=7 · Clean v3"""
import json,os,sys,io,math
from collections import defaultdict
from datetime import datetime
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=int(1e7);TRAIL=0.05;F_MA=6;S_MA=15;SL_MA=8

ETF_CODES=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
           '588200','159995','512480','515880','515050','159819','159992','512010',
           '518880','159937','513180','513050','513100','159509','588000','588220',
           '510300','159915','510050','511010','511260','510880','512890','159301']

def load_all():
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
        n_=len(ys);sx=sy=sxy=sxx=0
        for j,y in enumerate(ys):sx+=j;sy+=y;sxy+=j*y;sxx+=j*j
        d=n_*sxx-sx*sx
        if d>0:s[i]=(n_*sxy-sx*sy)/d/ms[i] if ms[i]>0 else 0
    return s

class Backtest:
    def __init__(self,etfs):
        self.etfs=etfs;self.codes=sorted(etfs.keys())
        self.dm={c:{b['date']:b for b in etfs[c]['bars']} for c in self.codes}
        self.fd={c:etfs[c]['first_date'] for c in self.codes}
        self.all_dates=sorted(set.union(*(set(self.dm[c].keys()) for c in self.codes)))

        # Precompute all single-ETF signals
        self.sigs={}
        for c in self.codes:
            bars=etfs[c]['bars']
            cl=[b['close'] for b in bars]
            mf=ma(cl,F_MA);ms=ma(cl,S_MA);msl=ma(cl,SL_MA)
            slo=slp(msl,max(SL_MA//2,3))
            dts=[b['date'] for b in bars]
            trnd={};rat={}
            for i in range(len(bars)):
                d=dts[i]
                if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
                    sk=not math.isnan(slo[i]) and slo[i]>0
                    trnd[d]=mf[i]>ms[i] and sk
                    rat[d]=mf[i]/ms[i]
                else:trnd[d]=False;rat[d]=1.0
            self.sigs[c]={'trend':trnd,'ratio':rat}

        # Precompute HS300 slope regime
        self.bear_regime={}
        for c in self.codes:
            if c=='510300':
                cl=[b['close'] for b in etfs[c]['bars']]
                m=ma(cl,60);sl=slp(m,20)
                dts=[b['date'] for b in etfs[c]['bars']]
                for i in range(len(dts)):
                    self.bear_regime[dts[i]]=not math.isnan(sl[i]) and sl[i]<0
                break

        # Precompute ETF-level MA60
        self.etf_above_ma60={}
        for c in self.codes:
            cl=[b['close'] for b in etfs[c]['bars']]
            m=ma(cl,60);dts=[b['date'] for b in etfs[c]['bars']]
            d={}
            for i in range(len(dts)):
                d[dts[i]]=not math.isnan(m[i]) and cl[i]>m[i]
            self.etf_above_ma60[c]=d

        # Compute ETF ATR for vol sizing
        self.atr={}
        for c in self.codes:
            cl=[b['close'] for b in etfs[c]['bars']]
            dts=[b['date'] for b in etfs[c]['bars']]
            atr_d={}
            for i in range(1,len(cl)):
                atr_d[dts[i]]=abs(cl[i]-cl[i-1])/cl[i-1]
            self.atr[c]=atr_d

    def run(self,bear_mh=0,bull_mh=0,etf_filter=False,vol_filter=False):
        cash=INIT;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None;trades=[];dvs=[]
        for d in self.all_dates:
            avail=[c for c in self.codes if self.fd[c]<=d]
            dt_obj=datetime.strptime(d,'%Y-%m-%d')
            is_bear=self.bear_regime.get(d,False)
            cur_mh=bear_mh if is_bear else bull_mh

            # Exit check
            if pos_code:
                bar=self.dm[pos_code].get(d)
                if bar:
                    px=bar['close']
                    if px>peak:peak=px
                    ton=self.sigs[pos_code]['trend'].get(d,False)
                    er=None
                    if px<=peak*(1-TRAIL):er='trail'
                    elif not ton:
                        if cur_mh>0 and entry_date and (dt_obj-entry_date).days>=cur_mh:er='off'
                        else:er='off'
                    if er:
                        pnl=shares*px-shares*bp
                        trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':er,'bear':is_bear,'code':pos_code,'b':entry_date.isoformat()[:10] if entry_date else ''})
                        cash=shares*px;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None

            # Entry check
            if not pos_code and cash>0:
                cands=[]
                for c in avail:
                    ton=self.sigs[c]['trend'].get(d,False)
                    if not ton:continue
                    if etf_filter and not self.etf_above_ma60.get(c,{}).get(d,False):continue
                    if vol_filter and self.atr.get(c,{}).get(d,0)>0.04:continue
                    bar=self.dm[c].get(d)
                    if bar:cands.append((c,self.sigs[c]['ratio'].get(d,1.0),bar['close']))
                if cands:
                    cands.sort(key=lambda x:x[1],reverse=True)
                    c,ratio,px=cands[0]
                    shares=cash/px;bp=px;peak=px;pos_code=c;entry_date=dt_obj;cash=0.0

            # Daily NAV
            nav=cash
            if pos_code:
                bar=self.dm[pos_code].get(d)
                if bar:nav+=shares*bar['close']
            dvs.append(nav)

        # Final
        if pos_code:
            bar=self.dm[pos_code].get(self.all_dates[-1])
            if bar:
                px=bar['close'];pnl=shares*px-shares*bp
                trades.append({'pnl':pnl,'r':(px-bp)/bp,'bear':False,'code':pos_code,'final':True})
                cash=shares*px

        # Metrics
        fv=cash;rets=[]
        for i in range(1,len(dvs)):
            if dvs[i-1]>0:rets.append((dvs[i]-dvs[i-1])/dvs[i-1])
        if not rets:rets=[0.0]
        pk=dvs[0];mdd=0.0
        for v in dvs:
            if v>pk:pk=v
            dd=(pk-v)/pk
            if dd>mdd:mdd=dd
        tr=(fv-INIT)/INIT
        mu=sum(rets)/len(rets)
        sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5 if len(rets)>1 else 0.01
        av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
        ar=(1+tr)**(TD/len(rets))-1 if tr>-1 else -1

        st=[t for t in trades if t.get('e','?') in('trail','off','final')]
        w=sum(1 for t in st if t['r']>0)
        wr=w/len(st) if st else 0
        bt=[t for t in st if t.get('bear',False)];bu=[t for t in st if not t.get('bear',False)]
        return{'sh':sh,'tr':tr,'mdd':mdd,'ar':ar,'np':len(st),'wr':wr,
               'bp':sum(t['pnl']for t in bt),'bup':sum(t['pnl']for t in bu),
               'trades':trades,'dvs':dvs,'fv':fv}

etfs=load_all()
bt=Backtest(etfs)
print('HS300 slope<0 days: %d (%.1f%%)'%(sum(1 for v in bt.bear_regime.values() if v),sum(v for v in bt.bear_regime.values())/max(len(bt.bear_regime),1)*100))

# Verify bear regime works
print('Sample dates:')
for d in['2020-03-16','2021-01-04','2022-04-25','2023-06-01','2024-02-01','2025-01-02']:
    print('  %s: bear=%s'%(d,bt.bear_regime.get(d,'MISSING')))

print('\n'+'='*100)
print('  COMPARISON: ETF>MA60 Filter vs MH=7')
print('='*100)
tests=[('BASELINE',0,0,False,False),('MH=7',7,0,False,False),
       ('ETF>MA60',0,0,True,False),('ETF>MA60+MH=7',7,0,True,False),
       ('ETF>MA60+Vol',0,0,True,True),('ETF>MA60+MH=7+Vol',7,0,True,True)]
print('  %-22s %7s %9s %7s %7s %5s %5s %8s %8s'%('Config','S','Ret','DD','Ann','Trd','Win','BearPnL','BullPnL'))
print('  '+'-'*95)
all_res=[]
for label,bm,bu,ef,vf in tests:
    r=bt.run(bm,bu,ef,vf)
    r['label']=label;all_res.append(r)
    yr_pnl=defaultdict(float)
    for t in r['trades']:
        if t.get('e','')in('trail','off','final'):
            bd=t.get('b','');yr_pnl[bd[:4] if bd else '?']+=t['pnl']
    yr_str=' '.join('%s:%+.0f%%'%(y,yr_pnl[y]/INIT*100) for y in sorted(yr_pnl))
    print('  %-22s %7.3f %8.2f%% %6.2f%% %6.2f%% %5d %4.0f%% %+8s %+8s  %s'%(
        label,r['sh'],r['tr']*100,r['mdd']*100,r['ar']*100,
        r['np'],r['wr']*100,str(int(r['bp'])),str(int(r['bup'])),yr_str))

all_res.sort(key=lambda x:x['sh'],reverse=True)
print('\n  RANKED:')
for i,r in enumerate(all_res):
    print('  %2d. %-22s S=%.3f Ret=%.1f%% DD=%.1f%%'%(i+1,r['label'],r['sh'],r['tr']*100,r['mdd']*100))
print('\n  Done!')
