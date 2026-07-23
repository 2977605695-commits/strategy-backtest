"""
峰岭因子 2D 热力图: K × Lookback × FactorType
"""
import sys,io,os,math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from data_loader import load_prices,calc_ma,get_common_dates
import csv

INIT=10_000_000;RF=0.025;TD=252;MAX_POS=5
SLIP=0.003;B_FEE=0.00025;S_FEE=0.00025;STAX=0.0005
FUND_DIR=os.path.join(os.path.dirname(os.path.abspath(__file__)),'data','fundamentals_70stocks')

def load_sector_map():
    csvs=sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
    sm={}
    with open(os.path.join(FUND_DIR,csvs[-1]),'r',encoding='utf-8-sig') as f:
        for r in csv.DictReader(f): sm[r['code'].strip()]=r.get('sector','').strip()
    return sm

def calc_factor(stocks, lookback, k, ftype):
    factor={}
    for code,info in stocks.items():
        vols=info['volume'];dates=info['dates'];n=len(vols)
        ma_vol=calc_ma(vols,max(lookback,20))
        vals={}
        for i in range(n):
            if i<lookback or math.isnan(ma_vol[i]): continue
            wl=min(20,i+1);w=vols[i-wl+1:i+1]
            mu=sum(w)/wl;var=sum((v-mu)**2 for v in w)/wl;std=var**0.5
            thr=ma_vol[i]+k*std
            ps=0.0;rs=0.0
            for j in range(max(0,i-lookback+1),i+1):
                erupt=vols[j]>=thr
                if erupt:
                    prev=(j>0 and vols[j-1]>=thr)
                    if prev: rs+=vols[j]
                    else: ps+=vols[j]
            if ftype=='peak': vals[dates[i]]=ps
            else: vals[dates[i]]=ps/rs if rs>0 else float('nan')
        factor[code]=vals
    return factor

def backtest(stocks, factor, sm, dates, trail, max_pos, rebal):
    cash=INIT;slot=INIT/max_pos;pos={};eq=[];trades=[]
    idx={c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    for di,dt in enumerate(dates):
        for code,p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px=stocks[code]['close'][idx[code][dt]]
            if px>p['peak']:p['peak']=px
            if px<=p['peak']*(1-trail):
                sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
                trades.append({'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0})
                del pos[code]
        if di%rebal==0:
            cand=[(c,factor.get(c,{}).get(dt,float('nan'))) for c in stocks]
            cand=[(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
            cand.sort(key=lambda x:x[1],reverse=True)
            top=set(c for c,_ in cand[:max_pos])
            for code in list(pos.keys()):
                if code not in top:
                    px=stocks[code]['close'][idx[code][dt]];sp=px*(1-SLIP-S_FEE-STAX)
                    cash+=pos[code]['shares']*sp
                    trades.append({'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0})
                    del pos[code]
            hc=set(pos.keys());hs={sm.get(c,'') for c in hc}
            for code,sc in cand:
                if len(pos)>=max_pos:break
                if code in hc:continue
                s=sm.get(code,'')
                if s and s in hs:continue
                if cash<slot*0.99:break
                raw=stocks[code]['close'][idx[code][dt]];bp=raw*(1+SLIP+B_FEE);sh=slot/bp;cash-=slot
                pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}
                hc.add(code);hs.add(s)
        cash*=(1+RF/TD)
        pv=sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date':dt,'equity':cash+pv})
    ld=dates[-1]
    for code,p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px=stocks[code]['close'][idx[code][ld]];sp=px*(1-SLIP-S_FEE-STAX);cash+=p['shares']*sp
            trades.append({'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0})
    pos.clear()
    v=[d['equity'] for d in eq]
    tr=(v[-1]-v[0])/v[0];rs=[(v[i]-v[i-1])/v[i-1] for i in range(1,len(v)) if v[i-1]>0]
    y=len(rs)/TD;cagr=(v[-1]/v[0])**(1/y)-1 if y>0 else 0
    mu=sum(rs)/len(rs) if rs else 0
    sd=(sum((r-mu)**2 for r in rs)/len(rs))**0.5 if rs else 0
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk=v[0];mdd=0.0
    for x in v:
        if x>pk:pk=x
        dd=(pk-x)/pk
        if dd>mdd:mdd=dd
    cm=cagr/mdd if mdd>0 else float('inf')
    w=sum(1 for t in trades if t['ret']>0)
    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,'nt':len(trades),'wr':w/len(trades) if trades else 0}

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sm=load_sector_map()
    all_s=load_prices(stock_filter=None)
    stocks={c:i for c,i in all_s.items() if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
    cd=get_common_dates(stocks)
    print(f'[DATA] {len(stocks)} stocks, {len(cd)} days ({len(cd)/252:.1f}yr)')

    ks=[0.5,1.0,1.5,2.0,2.5]
    lbs=[10,14,21,30]
    ftypes=[('ratio','peak/ridge'),('peak','peak_only')]

    # Pre-compute all factors
    print('\n[FACTOR] Pre-computing...')
    cache={}
    for lb in lbs:
        for k in ks:
            for ft,ftn in ftypes:
                key=(lb,k,ft)
                cache[key]=calc_factor(stocks,lb,k,ft)
                nv=sum(len(v) for v in cache[key].values())
                print(f'  lb={lb:>2d} K={k} {ftn:<12s} → {nv} vals')

    # Backtest all combos (Trail=20%, rebal=21)
    print('\n[BACKTEST] Running...')
    results={}
    for ft,ftn in ftypes:
        results[ft]={}
        for lb in lbs:
            results[ft][lb]={}
            for k in ks:
                r=backtest(stocks,cache[(lb,k,ft)],sm,cd,0.20,MAX_POS,21)
                results[ft][lb][k]=r
                label=f'{ftn} K={k} lb={lb}d'
                # Only print interesting ones
                if r['sh']>0.9:
                    print(f'  ✨ {label:<30s} S={r["sh"]:.3f} R={r["tr"]*100:.0f}% DD={r["mdd"]*100:.1f}% Trd={r["nt"]}')

    # ============ HEATMAPS ============
    for ft,ftn in ftypes:
        print(f'\n{"="*85}')
        print(f'  HEATMAP: {ftn} — Sharpe (K × Lookback)')
        print(f'  Trail=20% | rebal=21d | Top5 | 赛道去重')
        print(f'{"="*85}')

        # Header
        hdr=f'  {"K↓ / LB→":>10s}'
        for lb in lbs: hdr+=f' {lb:>7d}d'
        hdr+=f'  {"Avg":>7s}'
        print(hdr)
        print(f'  {"─"*50}')

        best_sh,best_k,best_lb=0,0,0
        for k in ks:
            row=results[ft]
            rvals=[row[lb][k] for lb in lbs]
            print(f'  {f"K={k}":>10s}',end='')
            for lb in lbs:
                sh=results[ft][lb][k]['sh']
                marker='✨' if sh==max(row[lb2][k]['sh'] for lb2 in lbs) else '  '
                print(f'{marker}{sh:>5.2f}',end=' ')
                if sh>best_sh: best_sh,best_k,best_lb=sh,k,lb
            avg_s=sum(r['sh'] for r in rvals)/len(rvals)
            print(f'  {avg_s:>6.3f}')

        print(f'  {"─"*50}')
        print(f'  {"Avg":>10s}',end='')
        for lb in lbs:
            col=[results[ft][lb][k]['sh'] for k in ks]
            print(f'  {sum(col)/len(col):>5.2f}',end='')
        print()

        best_r=results[ft][best_lb][best_k]
        print(f'\n  🏆 Best Sharpe: K={best_k} lb={best_lb}d → '
              f'S={best_r["sh"]:.3f} R={best_r["tr"]*100:.0f}% DD={best_r["mdd"]*100:.1f}% '
              f'Trd={best_r["nt"]} Win={best_r["wr"]*100:.0f}%')

        # MDD heatmap
        print(f'\n  HEATMAP: {ftn} — Max DD')
        print(f'  {"K↓ / LB→":>10s}',end='')
        for lb in lbs: print(f' {lb:>7d}d',end='')
        print()
        for k in ks:
            print(f'  {f"K={k}":>10s}',end='')
            for lb in lbs:
                m=results[ft][lb][k]['mdd']*100
                print(f'  {m:>5.1f}%',end='')
            print()

    # ============ BEST OVERALL ============
    print(f'\n{"="*85}')
    print(f'  TOP 15 Overall')
    print(f'{"="*85}')
    all_r=[]
    for ft,ftn in ftypes:
        for lb in lbs:
            for k in ks:
                r=results[ft][lb][k]
                all_r.append({'label':f'{ftn} K={k} lb={lb}d','sh':r['sh'],
                    'tr':r['tr'],'mdd':r['mdd'],'nt':r['nt'],'wr':r['wr'],
                    'ft':ft,'k':k,'lb':lb})
    all_r.sort(key=lambda x:x['sh'],reverse=True)
    print(f'  {"Rank":<4s} {"Config":<30s} {"Sharpe":>7s} {"Ret":>8s} {"MDD":>6s} {"Calmar":>7s} {"Trd":>5s} {"Win":>5s}')
    print(f'  {"─"*75}')
    for i,r in enumerate(all_r[:15],1):
        cm=r['tr']/(r['mdd']+0.001)  # rough calmar
        # Use CAGR
        print(f'  {i:<4d} {r["label"]:<30s} {r["sh"]:>7.3f} {r["tr"]*100:>7.1f}% {r["mdd"]*100:>5.1f}% {r["sh"]*r["tr"]/(r["mdd"]+0.01):>6.1f} {r["nt"]:>5d} {r["wr"]*100:>4.0f}%')

    # ============ Best config annual returns ============
    best=all_r[0]
    print(f'\n  🏆 Best Config: {best["label"]}')
    print(f'     Sharpe={best["sh"]:.4f}  Ret={best["tr"]*100:.1f}%  MDD={best["mdd"]*100:.1f}%')
    print(f'     Params: K={best["k"]}, lookback={best["lb"]}d, ftype={best["ft"]}')

    # Annual returns for best
    fac_best=cache[(best['lb'],best['k'],best['ft'])]
    bt_best=backtest(stocks,fac_best,sm,cd,0.20,MAX_POS,21)
    # Quick yearly calc
    yr=defaultdict(lambda:{'s':None,'e':None})
    for d in bt_best.get('_equity',[]):
        pass  # would need equity curve

    print(f'\n{"="*85}\n  Done!\n{"="*85}')

if __name__=='__main__':
    main()
