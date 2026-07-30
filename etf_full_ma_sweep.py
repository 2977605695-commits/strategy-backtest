"""
V4 Grid on 33 ETFs · MA9/17 slp17 area + surrounding mesh
==========================================================
Since 33 ETFs are more diverse, re-scan around the best area
+ also test wider parameter ranges.
"""

import json, os, sys, io, math
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
START = '2020-01-01'; END = '2026-07-29'
RF = 0.025; TD = 252; INIT = 10_000_000; MAX_POS = 2; TRAIL = 0.05

# Scan around MA9/17 best area + wider
FAST_MAS = [4,5,6,7,8,9,10,12,14]
SLOW_MAS = [10,12,14,17,21,26,30,34,40,50]
SLOPE_MAS = [8,10,12,14,17,21]

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

def backtest(etfs, all_sigs):
    codes = sorted(etfs.keys())
    dm = {c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    first_dates = {c:etfs[c]['first_date'] for c in codes}
    all_dates = sorted(set.union(*[set(dm[c].keys()) for c in codes]))
    cash=INIT; positions={}; dvs=[]; per_slot=INIT/MAX_POS
    tn=0; trn=0; fn=0

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
                sell_val = pos['shares']*px; pnl = sell_val - pos['shares']*pos['bp']
                cash += sell_val; del positions[c]
        slots = MAX_POS - len(positions)
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
                if len(positions)>=MAX_POS or cash<=0: break
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
            cash += sell_val; del positions[c]

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
        av=sd*math.sqrt(TD); ar_=mu*TD; sh=(ar_-RF)/av if av>0 else 0
    else: av=sh=ar_=0.0
    return {'sh':sh,'tr':tr,'mdd':mdd}

def main():
    print('='*100)
    print(f'  V4 33ETF全MA Mesh · Fast{FAST_MAS} Slow{SLOW_MAS} Slope{SLOPE_MAS}')
    print('='*100)

    etfs = load_stocks()
    codes = sorted(etfs.keys())
    print(f'\n  {len(codes)} ETFs loaded')

    combos = [(f,s,sl) for f in FAST_MAS for s in SLOW_MAS for sl in SLOPE_MAS if f < s]

    print(f'  {len(combos)} MA combos. Precomputing signals...')
    signal_cache = {}
    for f_ma, s_ma, sl_ma in combos:
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

    TOTAL = len(combos)
    results = []; count = 0; best_sh = -999
    print(f'\n[GRID] {TOTAL} combos...')
    for f_ma, s_ma, sl_ma in combos:
        count += 1
        r = backtest(etfs, signal_cache[(f_ma,s_ma,sl_ma)])
        r.update({'f_ma':f_ma,'s_ma':s_ma,'sl_ma':sl_ma})
        results.append(r)
        if r['sh'] > best_sh: best_sh = r['sh']
        if count % 80 == 1 or count == TOTAL:
            print(f'  [{count:>4d}/{TOTAL}] MA{f_ma}/{s_ma} slp{sl_ma} S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% DD={r["mdd"]*100:>5.2f}% Best={best_sh:.4f}')

    results.sort(key=lambda x: x['sh'], reverse=True)

    print('\n\n' + '='*120)
    print('  TOP 30 · 33 ETFs · MA网格')
    print('='*120)
    print(f'  {"Rk":<3s} {"Fast":>4s} {"Slow":>4s} {"Slope":>5s} {"S":>7s} {"Ret":>8s} {"DD":>6s} {"Trd":>5s}')
    print(f'  {"-"*50}')
    for rank, r in enumerate(results[:30], 1):
        print(f'  {rank:<3d} {r["f_ma"]:>4d} {r["s_ma"]:>4d} {r["sl_ma"]:>5d} '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>7.2f}% {r["mdd"]*100:>5.2f}% {r.get("np","?"):>5s}')

    # Sensitivity
    for pname, pkey in [('Fast MA','f_ma'),('Slow MA','s_ma'),('Slope MA','sl_ma')]:
        lv = defaultdict(list)
        for r in results: lv[r[pkey]].append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(lv.keys()):
            avg = sum(lv[v])/len(lv[v]); top = max(lv[v])
            bar = '#'*max(int(avg*40),1) if avg>0 else '·'*max(int(abs(avg)*40),1)
            print(f'    {v:>4d}  avg S={avg:>7.3f}  best S={top:>7.4f}  {bar}')

    # Best detail
    best = results[0]
    print(f'\n\n  BEST: MA{best["f_ma"]}/{best["s_ma"]} slp{best["sl_ma"]} Trail=5%')
    print(f'  S={best["sh"]:.4f} Ret={best["tr"]*100:.2f}% DD={best["mdd"]*100:.2f}%')

    print('\n  Done!')

if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
