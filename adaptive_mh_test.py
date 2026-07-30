"""Adaptive MH: sweep all bear_mh × bull_mh combos"""
import json,os,sys,io,math
from collections import defaultdict
from datetime import datetime
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=10_000_000;MAX_POS=1;TRAIL=0.05;F_MA=6;S_MA=15;SL_MA=8

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

def slope(ms,lb):
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
    mf=ma(c,F_MA);ms=ma(c,S_MA);msl=ma(c,SL_MA);slo=slope(msl,max(SL_MA//2,3))
    dates=[b['date'] for b in bars]
    trnd={};rat={}
    for i in range(n):
        d=dates[i]
        if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
            sok=not math.isnan(slo[i]) and slo[i]>0
            trnd[d]=mf[i]>ms[i] and sok;rat[d]=mf[i]/ms[i]
        else:trnd[d]=False;rat[d]=1.0
    return{'trend':trnd,'ratio':rat}

def run(etfs,all_sigs,bear_mh,bull_mh,market_sigs):
    codes=sorted(etfs.keys())
    dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    fd={c:etfs[c]['first_date'] for c in codes}
    ad=set()
    for c in codes:ad.update(dm[c].keys())
    all_dates=sorted(ad)
    cash=INIT;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[];tn=0;trn=0

    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d]
        dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=market_sigs.get(d,False)
        cur_mh=bear_mh if is_bear else bull_mh
        if pos_code:
            bar=dm[pos_code].get(d)
            if bar:
                px=bar['close']
                if px>peak:peak=px
                ton=all_sigs[pos_code]['trend'].get(d,False)
                er=None
                if px<=peak*(1-TRAIL):er='trail';trn+=1
                elif not ton:
                    if cur_mh>0 and entry_date:
                        held=(dt_obj-entry_date).days
                        if held>=cur_mh:er='off';tn+=1
                    else:er='off';tn+=1
                if er:
                    sell_val=shares*px;pnl=sell_val-shares*bp
                    trades.append({'c':pos_code,'b':entry_d,'s':d,'bp':bp,'sp':px,'r':(px-bp)/bp,'pnl':pnl,'e':er,'regime':'bear' if is_bear else 'bull'})
                    cash=sell_val;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
        if not pos_code and cash>0:
            cands=[]
            for c in avail:
                ton=all_sigs[c]['trend'].get(d,False)
                if ton:
                    bar=dm[c].get(d);cands.append((c,all_sigs[c]['ratio'].get(d,1.0),bar['close'] if bar else 0))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px=cands[0]
                shares=cash/px;bp=px;peak=px;pos_code=c;entry_d=d;entry_date=dt_obj;cash=0.0
        pos_val=shares*dm[pos_code].get(d,{}).get('close',0) if pos_code else 0
        dvs.append(cash+pos_val)

    if pos_code:
        bar=dm[pos_code].get(all_dates[-1])
        if bar:
            px=bar['close'];sell_val=shares*px;pnl=sell_val-shares*bp
            trades.append({'c':pos_code,'b':entry_d,'s':all_dates[-1],'bp':bp,'sp':px,'r':(px-bp)/bp,'pnl':pnl,'e':'final','regime':'?'})
            cash=sell_val

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
    return{'sh':sh,'tr':tr,'ar':ar,'mdd':md,'cm':cm,'np':len(st),'wr':wr,'tn':tn,'trn':trn,'fv':fv,'trades':trades}

def main():
    etfs_all=load_all()
    # Build HS300 regime
    hs300=None
    for code in etfs_all:
        if code=='510300':
            c=[b['close'] for b in etfs_all[code]['bars']]
            m60=ma(c,60);dates=[b['date'] for b in etfs_all[code]['bars']]
            hs300={'dates':dates,'c':c,'ma60':m60}
            break
    market_sigs={}
    if hs300:
        for i in range(len(hs300['dates'])):
            d=hs300['dates'][i]
            market_sigs[d]=not math.isnan(hs300['ma60'][i]) and hs300['c'][i]<hs300['ma60'][i]

    all_sigs={}
    for code in etfs_all:all_sigs[code]=gen_sigs(etfs_all[code]['bars'])

    bear_vals=[0,3,5,7,10,14,21]
    bull_vals=[0,3,5,7,10,14,21]
    TOTAL=len(bear_vals)*len(bull_vals)
    results=[];count=0
    print('Sweeping %d combos...'%TOTAL)
    best_s=-999
    for bm in bear_vals:
        for bu in bull_vals:
            count+=1
            r=run(etfs_all,all_sigs,bm,bu,market_sigs)
            r['bm']=bm;r['bu']=bu;results.append(r)
            if r['sh']>best_s:best_s=r['sh']
            if count%10==1 or count==TOTAL:
                print('  [%3d/%d] BearMH=%2d BullMH=%2d: S=%7.3f Ret=%8.2f%% DD=%5.2f%% BestS=%.4f'%(
                    count,TOTAL,bm,bu,r['sh'],r['tr']*100,r['mdd']*100,best_s))

    results.sort(key=lambda x:x['sh'],reverse=True)

    print('\n\n'+'='*120)
    print('  TOP 25 · ADAPTIVE MINIMUM HOLD')
    print('='*120)
    print('  %-3s %6s %6s %7s %9s %7s %7s %7s %5s %5s %6s %6s'%(
        'Rk','BearMH','BullMH','S','Ret','DD','Calmar','AnnRet','Trd','Win','Yr22','Yr25'))
    print('  '+'-'*100)
    for rank,r in enumerate(results[:25],1):
        yr_pnl=defaultdict(float)
        for t in r['trades']:
            if t['e'] in('trail','off','final'):yr_pnl[t['b'][:4]]+=t['pnl']
        yr22=yr_pnl.get('2022',0)/INIT*100
        yr25=yr_pnl.get('2025',0)/INIT*100
        print('  %-3d %6d %6d %7.3f %8.2f%% %6.2f%% %7.3f %6.2f%% %5d %4.0f%% %+5.1f%% %+5.0f%%'%(
            rank,r['bm'],r['bu'],r['sh'],r['tr']*100,r['mdd']*100,r['cm'],r['ar']*100,r['np'],r['wr']*100,yr22,yr25))

    # Heatmap
    print('\n\n  HEATMAP (Sharpe): BearMH (row) × BullMH (col)')
    print('  %-8s'%'Bear\\Bull',end='')
    for bu in bull_vals:print(' %6s'%('Bull'+str(bu)),end='')
    print()
    for bm in bear_vals:
        print('  %-8s'%('Bear'+str(bm)),end='')
        for bu in bull_vals:
            subset=[r for r in results if r['bm']==bm and r['bu']==bu]
            if subset:
                s=subset[0]['sh'];dd=subset[0]['mdd']*100
                # Color indicator
                if s>=1.0:mark='*'
                elif s>=0.8:mark='+'
                elif s>=0.5:mark='-'
                else:mark='.'
                print(' %s%5.2f'%(mark,s),end='')
            else:print('       ',end='')
        print()

    # Best
    best=results[0]
    print('\n\n  BEST: BearMH=%dd BullMH=%dd  S=%.3f Ret=%.1f%% DD=%.1f%% Calmar=%.3f'%(
        best['bm'],best['bu'],best['sh'],best['tr']*100,best['mdd']*100,best['cm']))

    # Best annual consistency
    print('\n  BEST BY 2022 SURVIVAL:')
    def yr_pnl22(r):
        yp=defaultdict(float)
        for t in r['trades']:
            if t['e'] in('trail','off','final'):yp[t['b'][:4]]+=t['pnl']
        return yp.get('2022',0)
    by22=sorted(results,key=lambda x:yr_pnl22(x),reverse=True)
    for r in by22[:5]:
        yp=defaultdict(float)
        for t in r['trades']:
            if t['e'] in('trail','off','final'):yp[t['b'][:4]]+=t['pnl']
        print('  BearMH=%2d BullMH=%2d  S=%.3f Ret=%.1f%% DD=%.1f%% 2022=%+.1f%%'%(
            r['bm'],r['bu'],r['sh'],r['tr']*100,r['mdd']*100,yp.get('2022',0)/INIT*100))

    print('\n  Done!')

if __name__=='__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
