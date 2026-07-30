"""
ETF 终极网格 · 2/3/4/5只轮动 · 精细MA · 全33只
==============================================
Sweep: MA_fast × MA_slow × MA_slope × position_count
Focus: minimize DD, maximize Sharpe
"""

import json, os, sys, io, math
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
START = '2020-01-01'; END = '2026-07-29'
RF = 0.025; TD = 252; INIT = 10_000_000; TRAIL = 0.05

# Fine mesh around the best areas
FAST_MAS = [3,4,5,6,7,8]
SLOW_MAS = [8,10,12,14,15,17,19,21]
SLOPE_MAS = [8,9,10,11,12,13,14,15,17]
MAX_POS_LIST = [2,3,4,5]

def load_stocks():
    etfs = {}
    for f in os.listdir(DATA_DIR):
        if f.startswith('etf_') and f.endswith('.json'):
            d = json.load(open(os.path.join(DATA_DIR, f), encoding='utf-8'))
            bars = []
            for b in d['bars']:
                dt = b['date']
                if len(dt) == 8: dt = dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
                if START <= dt <= END:
                    bars.append({'date': dt, 'close': float(b['close'])})
            if bars: etfs[d['code']] = {'name': d['name'], 'first_date': bars[0]['date'], 'bars': bars}
    return etfs

def calc_ma(data, w):
    ma = []; n = len(data)
    for i in range(n):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma

def calc_slope(ma_series, lb):
    slopes = [float('nan')]*len(ma_series)
    for i in range(len(ma_series)):
        if i < lb: continue
        ys = ma_series[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys): continue
        n = len(ys); sx=sy=sxy=sxx=0
        for j,y in enumerate(ys): sx+=j; sy+=y; sxy+=j*y; sxx+=j*j
        denom = n*sxx-sx*sx
        if denom>0: slopes[i] = (n*sxy-sx*sy)/denom/ma_series[i] if ma_series[i]>0 else 0
    return slopes

def backtest(etfs, all_sigs, max_pos):
    codes = sorted(etfs.keys())
    dm = {c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    first_dates = {c:etfs[c]['first_date'] for c in codes}
    all_dates = sorted(set.union(*[set(dm[c].keys()) for c in codes]))
    cash=INIT; positions={}; dvs=[]; per_slot=INIT/max_pos
    tn=0; trn=0; fn=0; trades=[]

    for d in all_dates:
        available = [c for c in codes if first_dates[c]<=d]
        for c in list(positions.keys()):
            bar = dm[c].get(d)
            if not bar: continue
            px = bar['close']; pos = positions[c]
            if px > pos['peak']: pos['peak'] = px
            trend_on = all_sigs[c]['trend'].get(d, False)
            exit_reason = None
            if px <= pos['peak']*(1-TRAIL): exit_reason='trail'; trn+=1
            elif not trend_on: exit_reason='trend_off'; tn+=1
            if exit_reason:
                sell_val = pos['shares']*px
                trades.append({'code':c,'bd':pos['entry_d'],'sd':d,'bp':pos['bp'],'sp':px,
                               'ret':(px-pos['bp'])/pos['bp'],'pnl':sell_val-pos['shares']*pos['bp'],
                               'exit':exit_reason})
                cash += sell_val; del positions[c]
        slots = max_pos - len(positions)
        if slots>0 and cash>0:
            candidates = []
            for c in available:
                if c in positions: continue
                trend_on = all_sigs[c]['trend'].get(d, False)
                if trend_on:
                    bar = dm[c].get(d)
                    candidates.append((c, all_sigs[c]['ratio'].get(d,1.0), bar['close'] if bar else 0))
            candidates.sort(key=lambda x: x[1], reverse=True)
            for c, ratio, px in candidates:
                if len(positions)>=max_pos or cash<=0: break
                invest = min(cash, per_slot)
                if invest<=0: continue
                shares = invest/px
                positions[c] = {'shares':shares,'bp':px,'peak':px,'entry_d':d}
                cash -= invest
        pos_val = sum(pos['shares']*dm[c].get(d,{}).get('close',0) for c,pos in positions.items() if dm[c].get(d))
        dvs.append({'date':d,'value':cash+pos_val})

    ld = all_dates[-1]
    for c in list(positions.keys()):
        bar = dm[c].get(ld)
        if bar:
            px = bar['close']; sell_val = positions[c]['shares']*px
            pnl = sell_val - positions[c]['shares']*positions[c]['bp']
            trades.append({'code':c,'bd':positions[c]['entry_d'],'sd':ld,'bp':positions[c]['bp'],'sp':px,
                           'ret':(px-positions[c]['bp'])/positions[c]['bp'],'pnl':pnl,'exit':'final'})
            fn+=1; cash+=sell_val
        del positions[c]

    fv = cash; rets = []
    for i in range(1, len(dvs)):
        p,c = dvs[i-1]['value'], dvs[i]['value']
        if p>0: rets.append((c-p)/p)
    if not rets: rets=[0.0]
    pkv = dvs[0]['value']; mdd=0.0
    for dv in dvs:
        if dv['value']>pkv: pkv=dv['value']
        dd = (pkv-dv['value'])/pkv
        if dd>mdd: mdd=dd
    tr = (fv-INIT)/INIT
    if len(rets)>1:
        mu=sum(rets)/len(rets); sd=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av=sd*math.sqrt(TD); ar_=mu*TD
        sh=(ar_-RF)/av if av>0 else 0
    else: av=sh=0.0
    ar = (1+tr)**(TD/len(rets))-1 if tr>-1 else -1
    cm = ar/mdd if mdd>0 else 0
    sell_tr = [t for t in trades if t['exit'] in ('trail','trend_off','final')]
    wins = sum(1 for t in sell_tr if t['ret']>0)
    wr = wins/len(sell_tr) if sell_tr else 0
    return {'sh':sh,'tr':tr,'ar':ar,'mdd':mdd,'cm':cm,'np':len(sell_tr),'wr':wr,
            'trail_n':trn,'trend_n':tn,'final_n':fn}

def main():
    combos = [(f,s,sl) for f in FAST_MAS for s in SLOW_MAS for sl in SLOPE_MAS if f < s]
    TOTAL = len(combos) * len(MAX_POS_LIST)
    print('='*110)
    print(f'  ETF终极网格 · {TOTAL} combos (MA网格={len(combos)} × 持仓={MAX_POS_LIST})')
    print('='*110)

    etfs = load_stocks()
    codes = sorted(etfs.keys())
    print(f'\n  {len(codes)} ETFs loaded')
    print(f'  Fast={FAST_MAS}  Slow={SLOW_MAS}  Slope={SLOPE_MAS}  Pos={MAX_POS_LIST}')

    # Precompute signals
    print(f'\n  Computing {len(combos)} signal sets...')
    signal_cache = {}
    for idx, (f_ma, s_ma, sl_ma) in enumerate(combos):
        sigs_all = {}
        for code in codes:
            if code not in etfs: continue
            bars = etfs[code]['bars']; closes = [b['close'] for b in bars]; n = len(bars)
            ma_f = calc_ma(closes, f_ma); ma_s = calc_ma(closes, s_ma)
            ma_sl = calc_ma(closes, sl_ma)
            slopes = calc_slope(ma_sl, max(sl_ma//2, 3))
            dates = [b['date'] for b in bars]
            sigs = {'trend':{}, 'ratio':{}}
            for i in range(n):
                d = dates[i]
                if not math.isnan(ma_f[i]) and not math.isnan(ma_s[i]) and ma_s[i]>0:
                    slope_ok = not math.isnan(slopes[i]) and slopes[i]>0
                    sigs['trend'][d] = ma_f[i] > ma_s[i] and slope_ok
                    sigs['ratio'][d] = ma_f[i]/ma_s[i]
                else:
                    sigs['trend'][d]=False; sigs['ratio'][d]=1.0
            sigs_all[code] = sigs
        signal_cache[(f_ma,s_ma,sl_ma)] = sigs_all
        if (idx+1) % 80 == 0: print(f'    {idx+1}/{len(combos)} signal sets done')

    # Run grid
    results = []; count = 0; best_sh = -999; best_mdd = 999
    print(f'\n  Running {TOTAL} backtests...')
    for f_ma, s_ma, sl_ma in combos:
        sigs = signal_cache[(f_ma,s_ma,sl_ma)]
        for mp in MAX_POS_LIST:
            count += 1
            r = backtest(etfs, sigs, mp)
            r.update({'f_ma':f_ma,'s_ma':s_ma,'sl_ma':sl_ma,'mp':mp,
                      'label':f'MA{f_ma}/{s_ma} slp{sl_ma} pos={mp}'})
            results.append(r)
            if r['sh'] > best_sh: best_sh = r['sh']
            if r['mdd'] < best_mdd: best_mdd = r['mdd']
            if count % 200 == 1:
                print(f'  [{count:>5d}/{TOTAL}] MA{f_ma}/{s_ma} slp{sl_ma} pos={mp} '
                      f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>6.1f}% DD={r["mdd"]*100:>5.2f}% '
                      f'Trd={r["np"]:>4d} BestS={best_sh:.4f} BestDD={best_mdd*100:.2f}%')

    results.sort(key=lambda x: x['sh'], reverse=True)

    # ================================================================
    print('\n\n' + '='*130)
    print('  TOP 30 · 按夏普排序')
    print('='*130)
    print(f'  {"Rk":<3s} {"Fast":>4s} {"Slow":>4s} {"Slope":>5s} {"Pos":>3s} '
          f'{"S":>7s} {"Ret":>8s} {"Ann":>7s} {"DD":>6s} {"Calmar":>7s} {"Trd":>5s} {"Win":>5s} {"T#":>5s} {"TO#":>5s}')
    print(f'  {"-"*105}')
    for rank, r in enumerate(results[:30], 1):
        print(f'  {rank:<3d} {r["f_ma"]:>4d} {r["s_ma"]:>4d} {r["sl_ma"]:>5d} {r["mp"]:>3d} '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>7.2f}% {r["ar"]*100:>6.2f}% '
              f'{r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} {r["np"]:>5d} {r["wr"]*100:>4.0f}% '
              f'{r["trail_n"]:>5d} {r["trend_n"]:>5d}')

    # TOP 30 by lowest DD
    results_dd = sorted(results, key=lambda x: x['mdd'])
    print(f'\n\n  TOP 30 · 按回撤排序 (最低回撤)')
    print(f'  {"Rk":<3s} {"Fast":>4s} {"Slow":>4s} {"Slope":>5s} {"Pos":>3s} '
          f'{"S":>7s} {"Ret":>8s} {"DD":>6s} {"Calmar":>7s} {"Trd":>5s}')
    print(f'  {"-"*65}')
    for rank, r in enumerate(results_dd[:30], 1):
        print(f'  {rank:<3d} {r["f_ma"]:>4d} {r["s_ma"]:>4d} {r["sl_ma"]:>5d} {r["mp"]:>3d} '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>7.2f}% {r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} {r["np"]:>5d}')

    # Best by Calmar (return/DD)
    results_cm = sorted(results, key=lambda x: x['cm'], reverse=True)
    print(f'\n\n  TOP 20 · 按卡玛比率 (收益/回撤)')
    print(f'  {"Rk":<3s} {"Fast":>4s} {"Slow":>4s} {"Slope":>5s} {"Pos":>3s} '
          f'{"S":>7s} {"Ret":>8s} {"DD":>6s} {"Calmar":>7s} {"Trd":>5s}')
    print(f'  {"-"*65}')
    for rank, r in enumerate(results_cm[:20], 1):
        print(f'  {rank:<3d} {r["f_ma"]:>4d} {r["s_ma"]:>4d} {r["sl_ma"]:>5d} {r["mp"]:>3d} '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>7.2f}% {r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} {r["np"]:>5d}')

    # Sensitivity by position count
    print(f'\n\n  POSITION COUNT SENSITIVITY:')
    for mp in MAX_POS_LIST:
        subset = [r for r in results if r['mp']==mp]
        avg_s = sum(r['sh'] for r in subset)/len(subset)
        avg_dd = sum(r['mdd'] for r in subset)/len(subset)
        avg_ret = sum(r['tr'] for r in subset)/len(subset)
        best_s = max(r['sh'] for r in subset)
        best_dd = min(r['mdd'] for r in subset)
        print(f'    Pos={mp}: avg S={avg_s:.4f} avg DD={avg_dd*100:.2f}% avg Ret={avg_ret*100:.1f}% '
              f'best S={best_s:.4f} best DD={best_dd*100:.2f}%')

    # MA sensitivity
    for pname, pkey in [('Fast MA','f_ma'),('Slow MA','s_ma'),('Slope MA','sl_ma')]:
        lv = defaultdict(list)
        for r in results: lv[r[pkey]].append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(lv.keys()):
            avg=sum(lv[v])/len(lv[v]); top=max(lv[v])
            bar='#'*max(int(avg*50),1) if avg>0 else '·'
            print(f'    {v:>4d}  avg S={avg:>7.3f} best={top:>7.4f} n={len(lv[v]):>4d}  {bar}')

    # Best overall
    best = results[0]
    print(f'\n\n  {"="*80}')
    print(f'  🏆 BEST OVERALL: {best["label"]}')
    print(f'  S={best["sh"]:.4f} Ret={best["tr"]*100:.2f}% Ann={best["ar"]*100:.2f}% DD={best["mdd"]*100:.2f}% Calmar={best["cm"]:.4f}')
    print(f'  Trades={best["np"]} Win={best["wr"]*100:.1f}% Trail={best["trail_n"]} TrendOff={best["trend_n"]} Final={best["final_n"]}')

    # Best low-DD
    best_lowdd = results_dd[0]
    print(f'\n  🛡️ BEST LOW DD: {best_lowdd["label"]}')
    print(f'  S={best_lowdd["sh"]:.4f} Ret={best_lowdd["tr"]*100:.2f}% DD={best_lowdd["mdd"]*100:.2f}%')

    # Best Calmar
    best_cm = results_cm[0]
    print(f'\n  📈 BEST CALMAR: {best_cm["label"]}')
    print(f'  S={best_cm["sh"]:.4f} Ret={best_cm["tr"]*100:.2f}% DD={best_cm["mdd"]*100:.2f}% Calmar={best_cm["cm"]:.4f}')

    print('\n  Done!')

if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
