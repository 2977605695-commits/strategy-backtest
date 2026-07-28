"""
ETF 趋势轮动 · Top 2 by MA5/MA20 · 紧Trail · 9只池
==================================================
Entry: trend on AND selected by MA5/MA20 ratio rank (top 2)
Exit:  trail stop OR trend off
Pool:  shared cash, rotate immediately on exit

Grid: 3 trend defs × 4 trails = 12 combos
"""

import json, time, os, sys, io, math
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
START = '2020-01-01'; END = '2026-07-28'
RF = 0.025; TD = 252; INIT = 10_000_000; MAX_POS = 2

ETF_CODES = ['159782','588380','588870','588080','588300','518800','589720','588890','588170']

def load_etf(code):
    path = os.path.join(DATA_DIR, f'etf_{code}.json')
    if not os.path.exists(path): return None
    d = json.load(open(path, encoding='utf-8'))
    bars=[]
    for b in d['bars']:
        dt=b['date']
        if len(dt)==8: dt=f'{dt[:4]}-{dt[4:6]}-{dt[6:8]}'
        if START<=dt<=END: bars.append({'date':dt,'close':float(b['close'])})
    return {'name':d['name'],'first_date':d['first_date'],'bars':bars}

def calc_ma(data,w):
    ma=[];n=len(data)
    for i in range(n):
        if i<w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma

def calc_slope(ma_series, lb=5):
    slopes=[float('nan')]*len(ma_series)
    for i in range(len(ma_series)):
        if i<lb: continue
        ys=ma_series[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys): continue
        n=len(ys);sx=sy=sxy=sxx=0
        for j,y in enumerate(ys): sx+=j;sy+=y;sxy+=j*y;sxx+=j*j
        denom=n*sxx-sx*sx
        if denom>0: slopes[i]=(n*sxy-sx*sy)/denom/ma_series[i] if ma_series[i]>0 else 0
    return slopes

def compute_signals(bars):
    closes=[b['close'] for b in bars]; n=len(bars)
    mas={w:calc_ma(closes,w) for w in [3,5,7,10,20,30]}
    ma20_slope=calc_slope(mas[20],10)

    dates=[b['date'] for b in bars]
    result={}
    # MA5/MA20 ratio for ranking
    result['ma5_ma20']={}
    result['trend']={}

    # 3 trend definitions
    for sname in ['MA3>MA7+MA20斜率正','MA5>MA20+MA20斜率正','MA5斜率正+价>MA20']:
        result['trend'][sname]={}

    # MA5斜率
    ma5_slope=calc_slope(mas[5],5)

    for i in range(n):
        d=dates[i];px=closes[i]

        # MA5/MA20 ratio
        if not math.isnan(mas[5][i]) and not math.isnan(mas[20][i]) and mas[20][i]>0:
            result['ma5_ma20'][d]={'ratio':mas[5][i]/mas[20][i],'px':px}
        else:
            result['ma5_ma20'][d]={'ratio':1.0,'px':px}

        # Trend signals
        ms20_ok = not math.isnan(ma20_slope[i]) and ma20_slope[i]>0
        ms5_ok = not math.isnan(ma5_slope[i]) and ma5_slope[i]>0

        # S1: MA3>MA7 + MA20斜率正
        if not math.isnan(mas[3][i]) and not math.isnan(mas[7][i]):
            result['trend']['MA3>MA7+MA20斜率正'][d]=mas[3][i]>mas[7][i] and ms20_ok
        else:
            result['trend']['MA3>MA7+MA20斜率正'][d]=False

        # S2: MA5>MA20 + MA20斜率正
        if not math.isnan(mas[5][i]) and not math.isnan(mas[20][i]):
            result['trend']['MA5>MA20+MA20斜率正'][d]=mas[5][i]>mas[20][i] and ms20_ok
        else:
            result['trend']['MA5>MA20+MA20斜率正'][d]=False

        # S3: MA5斜率正+价>MA20
        if not math.isnan(mas[20][i]):
            result['trend']['MA5斜率正+价>MA20'][d]=ms5_ok and px>mas[20][i]
        else:
            result['trend']['MA5斜率正+价>MA20'][d]=False

    return result

def backtest_rotation(etfs, all_sigs, strat_name, trail_pct):
    codes=sorted([c for c in ETF_CODES if c in etfs])
    dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    first_dates={c:etfs[c]['first_date'] for c in codes}

    # All dates
    all_dates=set()
    for c in codes: all_dates.update(dm[c].keys())
    all_dates=sorted(all_dates)

    cash=INIT; positions={}; trades=[]; dvs=[]; t_n=0; tr_n=0; f_n=0
    per_slot=INIT/MAX_POS

    for d in all_dates:
        available=[c for c in codes if first_dates[c]<=d]
        n_avail=max(len(available),1)

        # Step 1: Check exits
        for c in list(positions.keys()):
            bar=dm[c].get(d)
            if not bar: continue
            px=bar['close']; pos=positions[c]
            if px>pos['peak']: pos['peak']=px
            trend_on = all_sigs[c]['trend'][strat_name].get(d,False)

            exit_reason=None
            if px <= pos['peak']*(1-trail_pct):
                exit_reason='trail'; tr_n+=1
            elif not trend_on:
                exit_reason='trend_off'; t_n+=1

            if exit_reason:
                sell_val=pos['shares']*px
                pnl=sell_val-pos['shares']*pos['bp']
                trades.append({'code':c,'bd':pos['entry_d'],'sd':d,
                               'bp':pos['bp'],'sp':px,
                               'ret':(px-pos['bp'])/pos['bp'],
                               'pnl':pnl,'exit':exit_reason})
                cash+=sell_val; del positions[c]

        # Step 2: Fill slots
        slots=MAX_POS-len(positions)
        if slots>0 and cash>0:
            candidates=[]
            for c in available:
                if c in positions: continue
                trend_on = all_sigs[c]['trend'][strat_name].get(d,False)
                if trend_on:
                    r = all_sigs[c]['ma5_ma20'].get(d,{})
                    candidates.append((c, r.get('ratio',1.0), r.get('px',0)))
            candidates.sort(key=lambda x:x[1], reverse=True)  # highest ratio first

            for c, ratio, px in candidates:
                if len(positions)>=MAX_POS or cash<=0: break
                invest=min(cash, per_slot)
                if invest<=0: continue
                shares=invest/px
                positions[c]={'shares':shares,'bp':px,'peak':px,'entry_d':d}
                cash-=invest

        # NAV
        pos_val=sum(pos['shares']*dm[c].get(d,{}).get('close',0)
                    for c,pos in positions.items() if dm[c].get(d))
        dvs.append({'date':d,'value':cash+pos_val,'n_pos':len(positions)})

    # Final
    ld=all_dates[-1]
    for c in list(positions.keys()):
        bar=dm[c].get(ld)
        if bar:
            px=bar['close']; sell_val=positions[c]['shares']*px
            pnl=sell_val-positions[c]['shares']*positions[c]['bp']
            trades.append({'code':c,'bd':positions[c]['entry_d'],'sd':ld,
                           'bp':positions[c]['bp'],'sp':px,
                           'ret':(px-positions[c]['bp'])/positions[c]['bp'],
                           'pnl':pnl,'exit':'final'})
            f_n+=1; cash+=sell_val
        del positions[c]

    fv=cash; rets=[]
    for i in range(1,len(dvs)):
        p,c=dvs[i-1]['value'],dvs[i]['value']
        if p>0: rets.append((c-p)/p)
    if not rets:rets=[0.0]
    pkv=dvs[0]['value'];mdd=0.0
    for dv in dvs:
        if dv['value']>pkv:pkv=dv['value']
        dd=(pkv-dv['value'])/pkv
        if dd>mdd:mdd=dd
    tr=(fv-INIT)/INIT
    if len(rets)>1:
        mu=sum(rets)/len(rets);sd=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av=sd*math.sqrt(TD);ar_=mu*TD;sh=(ar_-RF)/av if av>0 else 0
    else:av=sh=ar_=0.0
    ar=(1+tr)**(TD/max(len(rets),1))-1 if tr>-1 else -1;cm=ar/mdd if mdd>0 else float('inf')
    sell_tr=[t for t in trades if t['exit'] in ('trail','trend_off','final')]
    wins=sum(1 for t in sell_tr if t['ret']>0)
    wr=wins/len(sell_tr) if sell_tr else 0
    return {'sh':sh,'tr':tr,'ar':ar,'mdd':mdd,'cm':cm,'np':len(sell_tr),'wr':wr,
            'trail_n':tr_n,'trend_n':t_n,'final_n':f_n,'dvs':dvs,'trades':trades}

def main():
    print('='*100)
    print('  ETF趋势轮动 · Top 2 by MA5/MA20比值 · 紧Trail')
    print('='*100)

    print('\n[DATA] Loading...')
    etfs={}
    for code in ETF_CODES:
        e=load_etf(code)
        if e:etfs[code]=e;print(f'  {code} {e["name"]}: {len(e["bars"])} bars')

    print('\n[SIGNALS] Computing...')
    all_sigs={}
    for code in ETF_CODES:
        if code not in etfs: continue
        all_sigs[code]=compute_signals(etfs[code]['bars'])

    strat_names=list(all_sigs[ETF_CODES[0]]['trend'].keys())
    trails=[0.05,0.10,0.15,0.20]
    TOTAL=len(strat_names)*len(trails)
    results=[];count=0
    print(f'\n[GRID] {len(strat_names)} strats × {len(trails)} trails = {TOTAL} combos')

    for sn in strat_names:
        for trail in trails:
            count+=1
            r=backtest_rotation(etfs,all_sigs,sn,trail)
            r.update({'strat':sn,'trail':trail,'label':f'{sn} T={trail:.0%}'})
            results.append(r)
            print(f'  [{count:>2d}/{TOTAL}] {r["label"]:<45s} '
                  f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% DD={r["mdd"]*100:>5.2f}% '
                  f'Trd={r["np"]:>4d} Win={r["wr"]*100:>4.0f}% '
                  f'Trail={r["trail_n"]:>3d} Trend={r["trend_n"]:>3d}')

    results.sort(key=lambda x:x['sh'],reverse=True)

    # ================================================================
    print('\n\n' + '='*110)
    print('  RANKING: Top 2 趋势轮动')
    print('='*110)
    h=(f'  {"Rk":<3s} {"Strategy":<35s} {"Trail":>5s} '
       f'{"S":>7s} {"Ret":>8s} {"Ann":>7s} {"DD":>6s} {"Calmar":>7s} '
       f'{"Trd":>4s} {"Win":>5s} {"Trail#":>6s} {"Trend#":>6s}')
    print(h);print(f'  {"-"*105}')
    for rank,r in enumerate(results,1):
        print(f'  {rank:<3d} {r["strat"]:<35s} {r["trail"]:>5.0%} '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>7.2f}% {r["ar"]*100:>6.2f}% '
              f'{r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} {r["np"]:>4d} {r["wr"]*100:>4.0f}% '
              f'{r["trail_n"]:>6d} {r["trend_n"]:>6d}')

    # Best detail
    best=results[0]
    print(f'\n\n  {"="*80}')
    print(f'  BEST: {best["label"]}')
    print(f'  S={best["sh"]:.4f} Ret={best["tr"]*100:.2f}% Ann={best["ar"]*100:.2f}% '
          f'DD={best["mdd"]*100:.2f}% Calmar={best["cm"]:.3f}')
    print(f'  Trades={best["np"]} Win={best["wr"]*100:.1f}% '
          f'Trail={best["trail_n"]} TrendOff={best["trend_n"]} Final={best["final_n"]}')

    # Per ETF
    ep=defaultdict(float);etr=defaultdict(int);eret=defaultdict(list)
    for t in best['trades']:ep[t['code']]+=t['pnl'];etr[t['code']]+=1;eret[t['code']].append(t['ret'])
    print(f'\n  Per ETF:')
    for c in ETF_CODES:
        if c not in etfs:continue
        wr=sum(1 for r in eret[c] if r>0)/len(eret[c])*100 if eret[c] else 0
        print(f'    {c} {etfs[c]["name"]:<15s} PnL={ep[c]:>12,.0f} Trd={etr[c]:>3d} Win={wr:>4.0f}%')

    # Trades
    best['trades'].sort(key=lambda x:x['ret'],reverse=True)
    print(f'\n  Best 10:')
    for t in best['trades'][:10]:
        n=etfs[t['code']]['name'] if t['code'] in etfs else '?'
        print(f'    {t["code"]} {n:<15s} {t["bd"]}->{t["sd"]} {t["ret"]*100:>7.2f}% {t["exit"]}')
    print(f'\n  Worst 5:')
    for t in best['trades'][-5:]:
        n=etfs[t['code']]['name'] if t['code'] in etfs else '?'
        print(f'    {t["code"]} {n:<15s} {t["bd"]}->{t["sd"]} {t["ret"]*100:>7.2f}% {t["exit"]}')

    # Sensitivity
    print(f'\n\n  PARAMETER SENSITIVITY:')
    for pn,pk in [('Trend Strategy','strat'),('Trail %','trail')]:
        lv=defaultdict(list)
        for r in results:lv[r[pk]].append(r['sh'])
        print(f'\n  {pn}:')
        for v in sorted(lv.keys(),key=lambda x:str(x)):
            avg=sum(lv[v])/len(lv[v])
            lbl=f'{v:.0%}' if isinstance(v,float) else str(v)
            bar='#'*max(int(avg*60),1) if avg>0 else '·'
            print(f'    {lbl:<35s} avg S={avg:>7.3f} n={len(lv[v])}  {bar}')

    # Comparison
    print(f'\n\n  {"="*80}')
    print(f'  COMPARISON: Rotation vs Equal-Weight vs Single ETF')
    print(f'  {"="*80}')
    print(f'  {"Method":<40s} {"S":>7s} {"Ret":>8s} {"DD":>7s} {"Trd":>5s}')
    print(f'  {"-"*70}')
    print(f'  {"ETF 9只等权 Trail=5%":<40s} {"0.82":>7s} {"61%":>8s} {"47%":>7s} {"288":>5s}')
    print(f'  {"ETF 2只轮动 (本策略)":<40s} {best["sh"]:>7.3f} {best["tr"]*100:>7.2f}% {best["mdd"]*100:>6.2f}% {best["np"]:>5d}')
    print(f'  {"单ETF最优 (科创半导体)":<40s} {"2.53":>7s} {"190%":>8s} {"26%":>7s} {"17":>5s}')

    # Formula
    print(f'\n\n  最优轮动公式:')
    print(f'  ┌──────────────────────────────────────────────────────────┐')
    print(f'  │  趋势定义: {best["strat"]:<44s} │')
    print(f'  │  选股:     MA5/MA20比值最高 2只 (趋势条件满足)             │')
    print(f'  │  买入:     等权各 50% 资金                                │')
    print(f'  │  卖出:     Trail {best["trail"]:.0%} 或 趋势反转                       │')
    print(f'  │  轮动:     卖出当日立即补入                                │')
    print(f'  │  夏普 {best["sh"]:.2f} | 收益 {best["tr"]*100:.1f}% | 回撤 {best["mdd"]*100:.1f}% | {best["np"]}笔交易     │')
    print(f'  └──────────────────────────────────────────────────────────┘')

    print('\n  Done!')

if __name__=='__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
