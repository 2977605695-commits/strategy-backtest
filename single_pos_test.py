"""ETF Single-Position Rotation Test (MAX_POS=1)"""
import json,os,sys,io,math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=10_000_000;MAX_POS=1;TRAIL=0.05

def load():
    etfs={}
    for f in os.listdir(DATA_DIR):
        if f.startswith('etf_') and f.endswith('.json'):
            d=json.load(open(os.path.join(DATA_DIR,f),encoding='utf-8'))
            bars=[]
            for b in d['bars']:
                dt=b['date']
                if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
                if START<=dt<=END:bars.append({'date':dt,'close':float(b['close'])})
            if bars:etfs[d['code']]={'name':d['name'],'first_date':bars[0]['date'],'bars':bars}
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

def bt(etfs,all_sigs):
    codes=sorted(etfs.keys())
    dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    first_dates={c:etfs[c]['first_date'] for c in codes}
    all_dates=sorted(set.union(*[set(dm[c].keys()) for c in codes]))
    cash=INIT;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';dvs=[];trs=[];tn=0;trn=0

    for d in all_dates:
        avail=[c for c in codes if first_dates[c]<=d]
        # Check exit
        if pos_code:
            bar=dm[pos_code].get(d)
            if bar:
                px=bar['close']
                if px>peak:peak=px
                trend_on=all_sigs[pos_code]['trend'].get(d,False)
                er=None
                if px<=peak*(1-TRAIL):er='trail';trn+=1
                elif not trend_on:er='off';tn+=1
                if er:
                    sell_val=shares*px;pnl=sell_val-shares*bp
                    trs.append({'code':pos_code,'bd':entry_d,'sd':d,'bp':bp,'sp':px,'r':(px-bp)/bp,'pnl':pnl,'e':er})
                    cash=sell_val;pos_code=None;shares=0.0;bp=0.0;peak=0.0
        # Fill
        if not pos_code and cash>0:
            cands=[]
            for c in avail:
                trend_on=all_sigs[c]['trend'].get(d,False)
                if trend_on:
                    bar=dm[c].get(d)
                    cands.append((c,all_sigs[c]['ratio'].get(d,1.0),bar['close'] if bar else 0))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px=cands[0]
                shares=cash/px;bp=px;peak=px;pos_code=c;entry_d=d
                cash=0.0
        pos_val=shares*dm[pos_code].get(d,{}).get('close',0) if pos_code else 0
        dvs.append(cash+pos_val)

    if pos_code:
        bar=dm[pos_code].get(all_dates[-1])
        if bar:
            px=bar['close'];sell_val=shares*px;pnl=sell_val-shares*bp
            trs.append({'code':pos_code,'bd':entry_d,'sd':all_dates[-1],'bp':bp,'sp':px,'r':(px-bp)/bp,'pnl':pnl,'e':'final'})
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
    tr=(fv-INIT)/INIT
    mu=sum(rets)/len(rets);sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5 if len(rets)>1 else 0.01
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    ar=(1+tr)**(TD/len(rets))-1 if tr>-1 else -1
    st=[t for t in trs if t['e'] in ('trail','off','final')]
    wins=sum(1 for t in st if t['r']>0)
    return {'sh':sh,'tr':tr,'ar':ar,'mdd':md,'np':len(st),'wr':wins/len(st) if st else 0,'tn':tn,'trn':trn,'trades':trs}

def main():
    etfs=load()
    FAST=[3,4,5,6,7,8];SLOW=[8,10,12,14,15,17,19,21];SLOPE=[8,10,12,14,15,17]
    combos=[(f,s,sl) for f in FAST for s in SLOW for sl in SLOPE if f<s]
    print(f'{len(combos)} combos. Computing signals...')
    sig_cache={}
    for idx,(f_ma,s_ma,sl_ma) in enumerate(combos):
        sigs={}
        for code in sorted(etfs.keys()):
            bars=etfs[code]['bars'];c=[b['close'] for b in bars];n=len(bars)
            mf=ma(c,f_ma);ms=ma(c,s_ma);msl=ma(c,sl_ma);slo=slope(msl,max(sl_ma//2,3))
            dates=[b['date'] for b in bars]
            trnd={};rat={}
            for i in range(n):
                d=dates[i]
                if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
                    sok=not math.isnan(slo[i]) and slo[i]>0
                    trnd[d]=mf[i]>ms[i] and sok;rat[d]=mf[i]/ms[i]
                else:trnd[d]=False;rat[d]=1.0
            sigs[code]={'trend':trnd,'ratio':rat}
        sig_cache[(f_ma,s_ma,sl_ma)]=sigs
        if (idx+1)%80==0:print(f'  {idx+1}/{len(combos)} done')

    TOTAL=len(combos);results=[];count=0
    print(f'Running {TOTAL}...')
    best_s=-999
    for f_ma,s_ma,sl_ma in combos:
        count+=1
        r=bt(etfs,sig_cache[(f_ma,s_ma,sl_ma)])
        r.update({'f':f_ma,'s':s_ma,'sl':sl_ma})
        results.append(r)
        if r['sh']>best_s:best_s=r['sh']
        if count%60==1 or count==TOTAL:
            print(f'  [{count:>4d}/{TOTAL}] MA{f_ma}/{s_ma}s{sl_ma} S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% DD={r["mdd"]*100:>5.2f}% Trd={r["np"]:>4d} Win={r["wr"]*100:>4.0f}% Best={best_s:.4f}')

    results.sort(key=lambda x:x['sh'],reverse=True)

    print('\n\n'+'='*120)
    print('  SINGLE POSITION ROTATION TOP 30')
    print('='*120)
    print(f'  {"Rk":<3s} {"Fast":>4s} {"Slow":>4s} {"Slope":>5s} {"S":>7s} {"Ret":>8s} {"Ann":>7s} {"DD":>6s} {"Calmar":>7s} {"Trd":>5s} {"Win":>5s}')
    for rank,r in enumerate(results[:30],1):
        cm=r['ar']/r['mdd'] if r['mdd']>0 else 0
        print(f'  {rank:<3d} {r["f"]:>4d} {r["s"]:>4d} {r["sl"]:>5d} '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>7.2f}% {r["ar"]*100:>6.2f}% '
              f'{r["mdd"]*100:>5.2f}% {cm:>7.3f} {r["np"]:>5d} {r["wr"]*100:>4.0f}%')

    print(f'\n\n  COMPARISON:')
    print(f'  {"Strategy":<35s} {"S":>7s} {"Ret":>8s} {"DD":>7s}')
    print(f'  {"-"*55}')
    print(f'  {"33 ETFs 2-pos rotation (V4 best)":<35s} {"0.797":>7s} {"182.4%":>8s} {"26.2%":>7s}')
    print(f'  {"33 ETFs 2-pos rotation (Calmar)":<35s} {"0.742":>7s} {"149.4%":>8s} {"18.8%":>7s}')
    best=results[0]
    print(f'  {"33 ETFs 1-pos rotation":<35s} {best["sh"]:>7.3f} {best["tr"]*100:>7.2f}% {best["mdd"]*100:>6.2f}%')
    print(f'  {"Best single ETF (科创半导体)":<35s} {"2.357":>7s} {"180.6%":>8s} {"21.8%":>7s}')

    print('\n  Done!')

if __name__=='__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
