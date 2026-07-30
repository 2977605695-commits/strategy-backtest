"""Sweep HS300 MA windows + threshold types for adaptive bear/bull regime"""
import json,os,sys,io,math
from collections import defaultdict
from datetime import datetime
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=10_000_000;MAX_POS=1;TRAIL=0.05;F_MA=6;S_MA=15;SL_MA=8
BEAR_MH=7;BULL_MH=0

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
            bars.append({'date':dt,'close':float(b['close'])})
        etfs[code]={'name':d['name'],'first_date':bars[0]['date'],'bars':bars}
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

def gen_sigs(bars):
    c=[b['close'] for b in bars];n=len(bars)
    mf=ma(c,F_MA);ms=ma(c,S_MA);msl=ma(c,SL_MA);slo_=slp(msl,max(SL_MA//2,3))
    dates=[b['date'] for b in bars];trnd={};rat={}
    for i in range(n):
        d=dates[i]
        if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
            sok=not math.isnan(slo_[i]) and slo_[i]>0
            trnd[d]=mf[i]>ms[i] and sok;rat[d]=mf[i]/ms[i]
        else:trnd[d]=False;rat[d]=1.0
    return{'trend':trnd,'ratio':rat}

def run(etfs,all_sigs,market_sigs):
    codes=sorted(etfs.keys());dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    fd={c:etfs[c]['first_date'] for c in codes}
    ad=set()
    for c in codes:ad.update(dm[c].keys())
    all_dates=sorted(ad)
    cash=INIT;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]
    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=market_sigs.get(d,False);cur_mh=BEAR_MH if is_bear else BULL_MH
        if pos_code:
            bar=dm[pos_code].get(d)
            if bar:
                px=bar['close']
                if px>peak:peak=px
                ton=all_sigs[pos_code]['trend'].get(d,False);er=None
                if px<=peak*(1-TRAIL):er='trail'
                elif not ton:
                    if cur_mh>0 and entry_date:
                        if (dt_obj-entry_date).days>=cur_mh:er='off'
                    else:er='off'
                if er:
                    sell_val=shares*px;pnl=sell_val-shares*bp
                    trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':er,'b':entry_d})
                    cash=sell_val;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
        if not pos_code and cash>0:
            cands=[]
            for c in avail:
                ton=all_sigs[c]['trend'].get(d,False)
                if ton:
                    bar=dm[c].get(d);cands.append((c,all_sigs[c]['ratio'].get(d,1.0),bar['close'] if bar else 0))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px=cands[0];shares=cash/px;bp=px;peak=px;pos_code=c;entry_d=d;entry_date=dt_obj;cash=0.0
        pos_val=shares*dm[pos_code].get(d,{}).get('close',0) if pos_code else 0
        dvs.append(cash+pos_val)
    if pos_code:
        bar=dm[pos_code].get(all_dates[-1])
        if bar:
            px=bar['close'];sell_val=shares*px;pnl=sell_val-shares*bp
            trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':'final','b':entry_d});cash=sell_val
    fv=cash;rets=[]
    for i in range(1,len(dvs)):
        p,c=dvs[i-1],dvs[i]
        if p>0:rets.append((c-p)/p)
    if not rets:rets=[0.0]
    pkv=dvs[0];md=0.0
    for v in dvs:
        if v>pkv:pkv=v
        dd=(pkv-v)/pkv
        if dd>md:md=dd
    tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5 if len(rets)>1 else 0.01
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0;ar=(1+tr)**(TD/len(rets))-1 if tr>-1 else -1
    cm=ar/md if md>0 else 0
    st=[t for t in trades if t['e'] in('trail','off','final')]
    w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0
    return{'sh':sh,'tr':tr,'ar':ar,'mdd':md,'cm':cm,'np':len(st),'wr':wr,'fv':fv}

def build_market(etfs,ma_w,tt):
    for code in etfs:
        if code=='510300':
            c=[b['close'] for b in etfs[code]['bars']]
            m60=ma(c,ma_w);dates=[b['date'] for b in etfs[code]['bars']]
            if tt=='slope':
                sl=slp(m60,ma_w//3)
                return {dates[i]:not math.isnan(sl[i]) and sl[i]<0 for i in range(len(dates))}
            elif tt=='strict':
                return {dates[i]:not math.isnan(m60[i]) and c[i]<m60[i]*0.95 for i in range(len(dates))}
            else:
                return {dates[i]:not math.isnan(m60[i]) and c[i]<m60[i] for i in range(len(dates))}
    return {}

def main():
    etfs_all=load_all()
    all_sigs={}
    for code in etfs_all:all_sigs[code]=gen_sigs(etfs_all[code]['bars'])

    ma_wins=[30,40,50,60,70,80,100]
    tt_list=['normal','strict','slope']

    print('='*110)
    print('  HS300 REGIME FILTER SWEEP | BearMH=%d BullMH=%d'%(BEAR_MH,BULL_MH))
    print('  Baseline (no filter): S=1.048 Ret=675.6% DD=38.8%')
    print('='*110)
    print('  %-6s %-8s %7s %9s %7s %7s %7s %5s %5s %8s'%('MA','Type','S','Ret','DD','Calmar','Ann','Trd','Win','Bear%'))
    print('  '+'-'*90)

    results=[]
    for ma_w in ma_wins:
        for tt in tt_list:
            mkt=build_market(etfs_all,ma_w,tt)
            bear_d=sum(1 for v in mkt.values() if v)
            total_d=len(mkt)
            r=run(etfs_all,all_sigs,mkt)
            r['ma_w']=ma_w;r['tt']=tt;r['bear_pct']=bear_d/total_d*100 if total_d>0 else 0
            results.append(r)
            print('  MA%-3d  %-8s %7.3f %8.2f%% %6.2f%% %7.3f %6.2f%% %5d %4.0f%% %7.1f%%'%(
                ma_w,tt,r['sh'],r['tr']*100,r['mdd']*100,r['cm'],r['ar']*100,r['np'],r['wr']*100,r['bear_pct']))

    results.sort(key=lambda x:x['sh'],reverse=True)
    print('\n\n  TOP 5:')
    for r in results[:5]:
        print('  MA%-3d %-8s S=%.3f Ret=%.1f%% DD=%.1f%% BearDays=%.1f%%'%(
            r['ma_w'],r['tt'],r['sh'],r['tr']*100,r['mdd']*100,r['bear_pct']))

    # Also test combined: close<MA AND slope<0
    print('\n\n  COMBO: close<MA AND slope<0')
    hs300_c=None;hs300_dates=None
    for code in etfs_all:
        if code=='510300':
            hs300_c=[b['close'] for b in etfs_all[code]['bars']]
            hs300_dates=[b['date'] for b in etfs_all[code]['bars']]
            break
    for ma_w_test in [40,50,60]:
        m60=ma(hs300_c,ma_w_test);sl=slp(m60,ma_w_test//3)
        sigs={}
        for i in range(len(hs300_dates)):
            d=hs300_dates[i]
            below=not math.isnan(m60[i]) and hs300_c[i]<m60[i]
            sn=not math.isnan(sl[i]) and sl[i]<0
            sigs[d]=below and sn
        bear_d=sum(1 for v in sigs.values() if v);total_d=len(sigs)
        r=run(etfs_all,all_sigs,sigs)
        cm=r['ar']/r['mdd'] if r['mdd']>0 else 0
        print('  MA%d close<MA AND slope<0: S=%.3f Ret=%.1f%% DD=%.1f%% BearDays=%.1f%%'%(
            ma_w_test,r['sh'],r['tr']*100,r['mdd']*100,bear_d/total_d*100))

    print('\n  Done!')

if __name__=='__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
