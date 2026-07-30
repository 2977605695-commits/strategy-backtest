"""
ETF 趋势轮动 V2 · 全MA组合 + 自适应Trail · 最优解搜索
=====================================================
Grid:
  Fast MA:  3, 5, 7
  Slow MA:  10, 14, 21
  Slope MA: 14, 21 (for slope calculation)
  Trend:    MA_fast > MA_slow AND MA_slope的N日斜率 > 0

  Adaptive Trail (gain-based):
    gain >= 15%  → Trail 5%
    gain >= 30%  → Trail 10%
    gain >= 50%  → Trail 15%
    gain >= 100% → Trail 20%
    gain >= 150% → Trail 10%
    default      → Trail default (T0 = 10%, 15%, 20%)

  = 3 × 3 × 2 × 3 = 54 combos
"""

import json, os, sys, io, math
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
START = '2020-01-01'; END = '2026-07-28'
RF = 0.025; TD = 252; INIT = 10_000_000; MAX_POS = 2
ETF_CODES = ['159782','588380','588870','588080','588300','518800','589720','588890','588170']

TRAIL_TIERS = [
    (0.15, 0.05),
    (0.30, 0.10),
    (0.50, 0.15),
    (1.00, 0.20),
    (1.50, 0.10),
]
T0_DEFAULTS = [0.10, 0.15, 0.20]  # default trail before first tier

def load_etf(code):
    path = os.path.join(DATA_DIR, f'etf_{code}.json')
    if not os.path.exists(path): return None
    d = json.load(open(path, encoding='utf-8'))
    bars = []
    for b in d['bars']:
        dt = b['date']
        if len(dt) == 8: dt = dt[:4] + '-' + dt[4:6] + '-' + dt[6:8]
        if START <= dt <= END:
            bars.append({'date': dt, 'close': float(b['close'])})
    return {'name': d['name'], 'first_date': d['first_date'], 'bars': bars}


def calc_ma(data, w):
    ma = []; n = len(data)
    for i in range(n):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1]) / w)
    return ma


def calc_slope(ma_series, lb):
    slopes = [float('nan')] * len(ma_series)
    for i in range(len(ma_series)):
        if i < lb: continue
        ys = ma_series[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys): continue
        n = len(ys); sx = sy = sxy = sxx = 0
        for j, y in enumerate(ys): sx += j; sy += y; sxy += j*y; sxx += j*j
        denom = n*sxx - sx*sx
        if denom > 0: slopes[i] = (n*sxy - sx*sy) / denom / ma_series[i] if ma_series[i] > 0 else 0
    return slopes


def get_adaptive_trail(gain, t0):
    """Return trail % based on gain tiers."""
    best = t0
    for threshold, trail_val in TRAIL_TIERS:
        if gain >= threshold:
            best = trail_val
    return best


def backtest(etfs, all_sigs, strat_key, t0):
    codes = [c for c in ETF_CODES if c in etfs]
    dm = {c: {b['date']: b for b in etfs[c]['bars']} for c in codes}
    first_dates = {c: etfs[c]['first_date'] for c in codes}
    all_dates = set()
    for c in codes: all_dates.update(dm[c].keys())
    all_dates = sorted(all_dates)

    cash = INIT; positions = {}; trades = []
    per_slot = INIT / MAX_POS
    trail_n = 0; trend_n = 0

    for d in all_dates:
        available = [c for c in codes if first_dates[c] <= d]

        # Step 1: exits
        for c in list(positions.keys()):
            bar = dm[c].get(d)
            if not bar: continue
            px = bar['close']; pos = positions[c]
            if px > pos['peak']: pos['peak'] = px
            gain = (px - pos['bp']) / pos['bp'] if pos['bp'] > 0 else 0
            cur_trail = get_adaptive_trail(gain, t0)
            trend_on = all_sigs[c]['trend'].get(d, False)

            exit_reason = None
            if px <= pos['peak'] * (1 - cur_trail):
                exit_reason = 'trail'; trail_n += 1
            elif not trend_on:
                exit_reason = 'trend_off'; trend_n += 1

            if exit_reason:
                sell_val = pos['shares'] * px
                pnl = sell_val - pos['shares'] * pos['bp']
                trades.append({
                    'code': c, 'bd': pos['entry_d'], 'sd': d,
                    'bp': pos['bp'], 'sp': px,
                    'ret': (px - pos['bp']) / pos['bp'],
                    'pnl': pnl, 'exit': exit_reason,
                })
                cash += sell_val; del positions[c]

        # Step 2: entries
        slots = MAX_POS - len(positions)
        if slots > 0 and cash > 0:
            candidates = []
            for c in available:
                if c in positions: continue
                trend_on = all_sigs[c]['trend'].get(d, False)
                if trend_on:
                    bar = dm[c].get(d)
                    candidates.append((c, all_sigs[c]['ratio'].get(d, 1.0), bar['close'] if bar else 0))
            candidates.sort(key=lambda x: x[1], reverse=True)
            for c, ratio, px in candidates:
                if len(positions) >= MAX_POS or cash <= 0: break
                invest = min(cash, per_slot)
                if invest <= 0: continue
                shares = invest / px
                positions[c] = {'shares': shares, 'bp': px, 'peak': px, 'entry_d': d}
                cash -= invest

    # Final
    ld = all_dates[-1]; fn = 0
    for c in list(positions.keys()):
        bar = dm[c].get(ld)
        if bar:
            px = bar['close']
            sell_val = positions[c]['shares'] * px
            pnl = sell_val - positions[c]['shares'] * positions[c]['bp']
            trades.append({'code': c, 'bd': positions[c]['entry_d'], 'sd': ld,
                           'bp': positions[c]['bp'], 'sp': px,
                           'ret': (px - positions[c]['bp']) / positions[c]['bp'],
                           'pnl': pnl, 'exit': 'final'})
            fn += 1; cash += sell_val
        del positions[c]

    fv = cash; rets = []
    # Daily values for metrics
    dvs = []
    # Recompute dvs... simplified
    # We'll compute from trades for metrics
    return trades


def compute_metrics(etfs, all_sigs, strat_key, t0):
    """Full backtest with metrics."""
    codes = [c for c in ETF_CODES if c in etfs]
    dm = {c: {b['date']: b for b in etfs[c]['bars']} for c in codes}
    first_dates = {c: etfs[c]['first_date'] for c in codes}
    all_dates = set()
    for c in codes: all_dates.update(dm[c].keys())
    all_dates = sorted(all_dates)

    cash = INIT; positions = {}; trades = []; dvs = []
    per_slot = INIT / MAX_POS
    trail_n = 0; trend_n = 0; final_n = 0

    for d in all_dates:
        available = [c for c in codes if first_dates[c] <= d]
        for c in list(positions.keys()):
            bar = dm[c].get(d)
            if not bar: continue
            px = bar['close']; pos = positions[c]
            if px > pos['peak']: pos['peak'] = px
            gain = (px - pos['bp']) / pos['bp'] if pos['bp'] > 0 else 0
            cur_trail = get_adaptive_trail(gain, t0)
            trend_on = all_sigs[c]['trend'].get(d, False)

            exit_reason = None
            if px <= pos['peak'] * (1 - cur_trail):
                exit_reason = 'trail'; trail_n += 1
            elif not trend_on:
                exit_reason = 'trend_off'; trend_n += 1

            if exit_reason:
                sell_val = pos['shares'] * px
                pnl = sell_val - pos['shares'] * pos['bp']
                trades.append({'code': c, 'bd': pos['entry_d'], 'sd': d,
                               'bp': pos['bp'], 'sp': px,
                               'ret': (px - pos['bp']) / pos['bp'],
                               'pnl': pnl, 'exit': exit_reason, 'peak': pos['peak']})
                cash += sell_val; del positions[c]

        slots = MAX_POS - len(positions)
        if slots > 0 and cash > 0:
            candidates = []
            for c in available:
                if c in positions: continue
                trend_on = all_sigs[c]['trend'].get(d, False)
                if trend_on:
                    bar = dm[c].get(d)
                    candidates.append((c, all_sigs[c]['ratio'].get(d, 1.0), bar['close'] if bar else 0))
            candidates.sort(key=lambda x: x[1], reverse=True)
            for c, ratio, px in candidates:
                if len(positions) >= MAX_POS or cash <= 0: break
                invest = min(cash, per_slot)
                if invest <= 0: continue
                shares = invest / px
                positions[c] = {'shares': shares, 'bp': px, 'peak': px, 'entry_d': d}
                cash -= invest

        pos_val = sum(pos['shares'] * dm[c].get(d, {}).get('close', 0)
                      for c, pos in positions.items() if dm[c].get(d))
        dvs.append({'date': d, 'value': cash + pos_val})

    ld = all_dates[-1]
    for c in list(positions.keys()):
        bar = dm[c].get(ld)
        if bar:
            px = bar['close']
            sell_val = positions[c]['shares'] * px
            pnl = sell_val - positions[c]['shares'] * positions[c]['bp']
            trades.append({'code': c, 'bd': positions[c]['entry_d'], 'sd': ld,
                           'bp': positions[c]['bp'], 'sp': px,
                           'ret': (px - positions[c]['bp']) / positions[c]['bp'],
                           'pnl': pnl, 'exit': 'final'})
            final_n += 1; cash += sell_val
        del positions[c]

    fv = cash; rets = []
    for i in range(1, len(dvs)):
        p, c = dvs[i-1]['value'], dvs[i]['value']
        if p > 0: rets.append((c - p) / p)
    if not rets: rets = [0.0]

    pkv = dvs[0]['value']; mdd = 0.0
    for dv in dvs:
        if dv['value'] > pkv: pkv = dv['value']
        dd = (pkv - dv['value']) / pkv
        if dd > mdd: mdd = dd

    tr = (fv - INIT) / INIT
    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu)**2 for r in rets) / (len(rets) - 1))**0.5
        av = sd * math.sqrt(TD); ar_ = mu * TD
        sh = (ar_ - RF) / av if av > 0 else 0
    else: av = sh = ar_ = 0.0
    ar = (1 + tr)**(TD / len(rets)) - 1 if tr > -1 else -1
    cm = ar / mdd if mdd > 0 else float('inf')

    sell_tr = [t for t in trades if t['exit'] in ('trail', 'trend_off', 'final')]
    wins = sum(1 for t in sell_tr if t['ret'] > 0)
    wr = wins / len(sell_tr) if sell_tr else 0

    return {
        'sh': sh, 'tr': tr, 'ar': ar, 'mdd': mdd, 'cm': cm,
        'np': len(sell_tr), 'wr': wr,
        'trail_n': trail_n, 'trend_n': trend_n, 'final_n': final_n,
        'dvs': dvs, 'trades': trades,
    }


def main():
    print('='*100)
    print('  ETF趋势轮动 V2 · 全MA组合 + 自适应Trail · 最优解搜索')
    print(f'  Grid: 3×3×2×3 = 54 combos')
    print('='*100)

    print('\n[DATA] Loading...')
    etfs = {}
    for code in ETF_CODES:
        e = load_etf(code)
        if e: etfs[code] = e
        print(f'  {code} {e["name"]}: {len(e["bars"])} bars')

    # Precompute all signal combinations
    from itertools import product
    fast_mas = [3, 5, 7]
    slow_mas = [10, 14, 21]
    slope_mas = [14, 21]

    print('\n[SIGNALS] Precomputing...')
    all_signal_sets = {}  # (fast, slow, slope) -> {code: {'trend':{date:bool}, 'ratio':{date:float}}}

    for f_ma, s_ma, sl_ma in product(fast_mas, slow_mas, slope_mas):
        key = (f_ma, s_ma, sl_ma)
        sigs_all = {}
        total_trend = 0
        for code in ETF_CODES:
            if code not in etfs: continue
            bars = etfs[code]['bars']; closes = [b['close'] for b in bars]; n = len(bars)
            ma_f = calc_ma(closes, f_ma); ma_s = calc_ma(closes, s_ma)
            ma_sl = calc_ma(closes, sl_ma)
            slopes = calc_slope(ma_sl, sl_ma // 2)  # slope lookback = half of slope MA
            dates = [b['date'] for b in bars]
            sigs = {'trend': {}, 'ratio': {}}
            for i in range(n):
                d = dates[i]
                if not math.isnan(ma_f[i]) and not math.isnan(ma_s[i]) and ma_s[i] > 0:
                    slope_ok = not math.isnan(slopes[i]) and slopes[i] > 0
                    sigs['trend'][d] = ma_f[i] > ma_s[i] and slope_ok
                    sigs['ratio'][d] = ma_f[i] / ma_s[i]
                else:
                    sigs['trend'][d] = False; sigs['ratio'][d] = 1.0
                if sigs['trend'][d]: total_trend += 1
            sigs_all[code] = sigs
        all_signal_sets[key] = sigs_all
        print(f'  MA{f_ma}/{s_ma} slope{sl_ma}: {total_trend} trend-on days')

    # Run grid
    TOTAL = len(fast_mas) * len(slow_mas) * len(slope_mas) * len(T0_DEFAULTS)
    results = []; count = 0
    print(f'\n[GRID] {TOTAL} combos...')

    for f_ma, s_ma, sl_ma in product(fast_mas, slow_mas, slope_mas):
        key = (f_ma, s_ma, sl_ma)
        sigs = all_signal_sets[key]
        for t0 in T0_DEFAULTS:
            count += 1
            label = f'MA{f_ma}/{s_ma} slp{sl_ma} T0={t0:.0%}'
            r = compute_metrics(etfs, sigs, key, t0)
            r.update({'f_ma': f_ma, 's_ma': s_ma, 'sl_ma': sl_ma, 't0': t0, 'label': label})
            results.append(r)
            print(f'  [{count:>3d}/{TOTAL}] {label:<30s} '
                  f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% DD={r["mdd"]*100:>5.2f}% '
                  f'Trd={r["np"]:>4d} Win={r["wr"]*100:>4.0f}% T={r["trail_n"]} TO={r["trend_n"]}')

    # ================================================================
    results.sort(key=lambda x: x['sh'], reverse=True)

    print('\n\n' + '='*120)
    print('  TOP 20')
    print('='*120)
    h = (f'  {"Rk":<3s} {"MA":>7s} {"Slope":>7s} {"T0":>5s} '
         f'{"S":>7s} {"Ret":>8s} {"Ann":>7s} {"DD":>6s} {"Calmar":>7s} '
         f'{"Trd":>4s} {"Win":>5s} {"T":>4s} {"TO":>4s}')
    print(h); print(f'  {"-"*85}')
    for rank, r in enumerate(results[:20], 1):
        print(f'  {rank:<3d} MA{r["f_ma"]}/{r["s_ma"]:<2d} MA{r["sl_ma"]:<4d} '
              f'{r["t0"]:>5.0%} {r["sh"]:>7.3f} {r["tr"]*100:>7.2f}% {r["ar"]*100:>6.2f}% '
              f'{r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} {r["np"]:>4d} {r["wr"]*100:>4.0f}% '
              f'{r["trail_n"]:>4d} {r["trend_n"]:>4d}')

    # Sensitivity
    print('\n\n  PARAMETER SENSITIVITY:')
    for pname, pkey in [('Fast MA', 'f_ma'), ('Slow MA', 's_ma'), ('Slope MA', 'sl_ma'), ('T0 Default', 't0')]:
        lv = defaultdict(list)
        for r in results: lv[r[pkey]].append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(lv.keys()):
            avg = sum(lv[v])/len(lv[v])
            bar = '#'*max(int(avg*60),1) if avg>0 else '·'
            print(f'    {v:>4}  avg S={avg:>7.3f} n={len(lv[v]):>2d}  {bar}')

    # Best detail
    best = results[0]
    print(f'\n\n  {"="*80}')
    print(f'  🏆 BEST: {best["label"]}')
    print(f'  S={best["sh"]:.4f} Ret={best["tr"]*100:.2f}% Ann={best["ar"]*100:.2f}% '
          f'DD={best["mdd"]*100:.2f}% Calmar={best["cm"]:.3f}')
    print(f'  Trades={best["np"]} Win={best["wr"]*100:.1f}% '
          f'Trail={best["trail_n"]} TrendOff={best["trend_n"]} Final={best["final_n"]}')

    # Per ETF
    ep = defaultdict(float)
    for t in best['trades']:
        if t['exit'] in ('trail', 'trend_off', 'final'):
            ep[t['code']] += t['pnl']
    print(f'\n  Per ETF:')
    for c in ETF_CODES:
        if c not in etfs: continue
        trd = [t for t in best['trades'] if t['code'] == c and t['exit'] in ('trail', 'trend_off', 'final')]
        wr = sum(1 for t in trd if t['ret'] > 0)/len(trd)*100 if trd else 0
        print(f'    {c} {etfs[c]["name"]:<15s} PnL={ep[c]:>12,.0f} Trd={len(trd):>3d} Win={wr:>4.0f}%')

    # Best trades
    sell_tr = [t for t in best['trades'] if t['exit'] in ('trail', 'trend_off', 'final')]
    sell_tr.sort(key=lambda x: x['ret'], reverse=True)
    print(f'\n  Best 10:')
    for t in sell_tr[:10]:
        n = etfs[t['code']]['name'] if t['code'] in etfs else '?'
        print(f'    {t["code"]} {n:<15s} {t["bd"]}->{t["sd"]} {t["ret"]*100:>7.2f}% {t["exit"]}')

    # Compare with V1
    print(f'\n\n  COMPARISON:')
    print(f'  {"Strategy":<40s} {"S":>7s} {"Ret":>8s} {"DD":>7s}')
    print(f'  {"-"*60}')
    print(f'  {"V1 MA5/MA20 slope20 Trail=5%":<40s} {"0.540":>7s} {"83.6%":>8s} {"19.2%":>7s}')
    print(f'  {"V2 (本策略)":<40s} {best["sh"]:>7.3f} {best["tr"]*100:>7.2f}% {best["mdd"]*100:>6.2f}%')

    # Adaptive trail usage stats
    trail_usage = defaultdict(int)
    for t in best['trades']:
        if t['exit'] == 'trail':
            gain_at_exit = (t['sp'] - t['bp']) / t['bp']
            trail_used = get_adaptive_trail(gain_at_exit, best['t0'])
            trail_usage[f'{trail_used:.0%}'] += 1
    print(f'\n  Adaptive Trail Usage: {dict(trail_usage)}')

    print('\n  Done!')


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
