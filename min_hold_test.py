"""Minimum Hold Days Test + Final Strategy Output"""
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

def run(etfs,all_sigs,min_hold):
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
        if pos_code:
            bar=dm[pos_code].get(d)
            if bar:
                px=bar['close']
                if px>peak:peak=px
                trend_on=all_sigs[pos_code]['trend'].get(d,False)
                er=None
                if px<=peak*(1-TRAIL):er='trail';trn+=1
                elif not trend_on:
                    if min_hold>0 and entry_date:
                        held=(dt_obj-entry_date).days
                        if held>=min_hold:er='off';tn+=1
                    else:er='off';tn+=1
                if er:
                    sell_val=shares*px;pnl=sell_val-shares*bp
                    trades.append({'c':pos_code,'b':entry_d,'s':d,'bp':bp,'sp':px,'r':(px-bp)/bp,'pnl':pnl,'e':er,'days':(dt_obj-entry_date).days if entry_date else 0})
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
            dt_last=datetime.strptime(all_dates[-1],'%Y-%m-%d')
            trades.append({'c':pos_code,'b':entry_d,'s':all_dates[-1],'bp':bp,'sp':px,'r':(px-bp)/bp,'pnl':pnl,'e':'final','days':(dt_last-entry_date).days if entry_date else 0})
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
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    ar=(1+tr)**(TD/len(rets))-1 if tr>-1 else -1;cm=ar/md if md>0 else 0
    st=[t for t in trades if t['e'] in('trail','off','final')]
    w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0
    return{'sh':sh,'tr':tr,'ar':ar,'mdd':md,'cm':cm,'np':len(st),'wr':wr,'tn':tn,'trn':trn,'fv':fv,'trades':trades}

def main():
    etfs_all=load_all()
    n_etfs=len(etfs_all)
    print('='*100)
    print('  MINIMUM HOLD DAYS TEST | MA%d/%d s%d Trail=%d%% | %d ETFs Single-Position'%(F_MA,S_MA,SL_MA,int(TRAIL*100),n_etfs))
    print('='*100)

    all_sigs={}
    for code in etfs_all:all_sigs[code]=gen_sigs(etfs_all[code]['bars'])

    min_holds=[0,3,5,7,10,14,21]
    results=[]
    for mh in min_holds:
        r=run(etfs_all,all_sigs,mh)
        r['mh']=mh;results.append(r)
        # Annual breakdown
        yr_pnl=defaultdict(float);yr_trd=defaultdict(int)
        for t in r['trades']:
            if t['e'] in('trail','off','final'):
                yr=t['b'][:4];yr_pnl[yr]+=t['pnl'];yr_trd[yr]+=1
        yr_str=' | '.join('%s:%+.1f%%/%d'%(y,yr_pnl[y]/INIT*100,yr_trd[y]) for y in sorted(yr_pnl))
        print('  MH=%2dd: S=%7.3f Ret=%8.2f%% Ann=%6.2f%% DD=%5.2f%% Calmar=%7.3f Trd=%4d Win=%5.1f%% Trail=%4d Trend=%4d Final=%2d | %s'%(
            mh,r['sh'],r['tr']*100,r['ar']*100,r['mdd']*100,r['cm'],r['np'],r['wr']*100,r['trn'],r['tn'],
            sum(1 for t in r['trades'] if t['e']=='final'),yr_str[:120]))

    # Best
    best=max(results,key=lambda r:r['sh'])
    best_cm=max(results,key=lambda r:r['cm'])

    print('\n\n'+'='*80)
    print('  FINAL STRATEGY FORMULA')
    print('='*80)
    print('''
  MA%d > MA%d  AND  MA%d的%d日斜率 > 0
  Trail 5%% Flat (简单固定止损)
  最少持仓 %d 天 (趋势转空不触发, 除非超过最小持有天数)
  Top 1 by MA%d/MA%d比值, 满仓轮动

  夏普 %.3f | 收益 %.1f%% | 年化 %.1f%% | 回撤 %.1f%% | %d笔 | 胜率 %.0f%%
  Calmar最佳(回撤最优): MH=%dd S=%.3f Ret=%.1f%% DD=%.1f%%
  '''%(
        F_MA,S_MA,SL_MA,SL_MA//2,best['mh'],F_MA,S_MA,
        best['sh'],best['tr']*100,best['ar']*100,best['mdd']*100,best['np'],best['wr']*100,
        best_cm['mh'],best_cm['sh'],best_cm['tr']*100,best_cm['mdd']*100))

    # Best trades for top MH
    st=[t for t in best['trades'] if t['e'] in('trail','off','final')]
    st.sort(key=lambda x:x['r'],reverse=True)
    print('  Best 10 trades:')
    for t in st[:10]:
        name=etfs_all[t['c']]['name']
        print('    %s %s %s->%s %7.2f%% %-6s %dd'%(t['c'],name,t['b'],t['s'],t['r']*100,t['e'],t['days']))
    print('  Worst 5 trades:')
    for t in st[-5:]:
        name=etfs_all[t['c']]['name']
        print('    %s %s %s->%s %7.2f%% %-6s %dd'%(t['c'],name,t['b'],t['s'],t['r']*100,t['e'],t['days']))

    print('\n  Done!')

if __name__=='__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
