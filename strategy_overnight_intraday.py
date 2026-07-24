"""
Strategy 3: Overnight-Intraday Return Decomposition (隔夜-日内收益分解)
========================================================================
A股最具原创性的因子研究方向。核心发现：

  隔夜收益率 (close→next open)  = 动量延续 (positive IC ~+3~5%)
  日内收益率 (open→close)      = 反转效应 (negative IC ~-4~6%)
  → 传统月频动量失效，因为两个相反方向的成分互相抵消

机制（T+1特异）：
  - 隔夜信息在开盘集合竞价释放 → 跳空方向持续（T+1锁定期+涨跌停限制）
  - 日内交易由散户情绪驱动 → 过度反应后反转
  - 成交量修正：高成交量日的隔夜跳空动量更强

因子构造（东吴证券《日与夜之殊途同归》方法论）：
  Overnight_Nd  = avg(open[t-i]/close[t-i-1] - 1, for i=0..N-1)
  Intraday_Nd   = avg(close[t-i]/open[t-i] - 1, for i=0..N-1)
  Composite = Z(overnight_Nd) × w_on + (-Z(intraday_Nd)) × w_id

参考来源:
  - 东吴证券《成交量对动量因子的修正：日与夜之殊途同归》(2019)
  - 东方证券《隔夜上涨和日内反转中的隐藏Alpha》(2024)
  - 广发证券《基于隔夜相关性的因子研究》
  - Liu,Chen & Zhu (2024). IRFAE. "T+1 and contrarian effect"
"""
import sys, io, json, math, os, csv
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
RF = 0.025; TD = 252; INIT = 10_000_000; MAX_POS = 5
SLIP = 0.003; COMM = 0.00025; STAMP = 0.0005

# Grid search space
LOOKBACKS = [3, 5, 10, 14, 21]     # N-day averaging for overnight & intraday returns
W_ON = [0.3, 0.5, 0.7]            # weight on overnight (momentum) vs intraday (reversal)
TRAILS = [0.10, 0.15, 0.20, 0.25]
REBAL_DAYS = [5, 10, 21]
VOL_FILTER = [False, True]          # whether to adjust by volume

TOTAL = len(LOOKBACKS) * len(W_ON) * len(TRAILS) * len(REBAL_DAYS) * len(VOL_FILTER)


def load_stocks():
    stocks = {}
    for fn in sorted(os.listdir(DATA_DIR)):
        if not fn.endswith('.json') or fn.startswith('_'): continue
        d = json.load(open(os.path.join(DATA_DIR, fn), encoding='utf-8'))
        if len(d['bars']) < 126: continue
        bars = []
        for b in d['bars']:
            dt = b['date']
            if len(dt) == 8: dt = f'{dt[:4]}-{dt[4:6]}-{dt[6:8]}'
            bars.append({
                'date': dt, 'close': float(b['close']), 'open': float(b['open']),
                'high': float(b['high']), 'low': float(b['low']),
                'volume': float(b['volume']),
            })
        stocks[d['code']] = {'name': d['name'], 'bars': bars, 'first_date': bars[0]['date']}
    return stocks


def compute_oi_factors(stocks, lookback, vol_filter):
    """
    Compute overnight-intraday decomposition factors.

    For each date t, compute:
      overnight_ret[i] = open[t-i] / close[t-i-1] - 1   (close→next open)
      intraday_ret[i]  = close[t-i] / open[t-i] - 1      (open→close)

    Then average over lookback window:
      on_avg = mean(overnight_ret[i] for i in 0..lookback-1)
      id_avg = mean(intraday_ret[i] for i in 0..lookback-1)

    Score = on_avg (momentum) - id_avg (reversal: negative of intraday return)

    If vol_filter: downweight signals from low-volume days
    """
    factors = {}
    for code, info in stocks.items():
        bars = info['bars']
        n = len(bars)
        closes = [b['close'] for b in bars]
        opens = [b['open'] for b in bars]
        volumes = [b['volume'] for b in bars]

        # Pre-compute overnight and intraday returns
        on_ret = [float('nan')] * n
        id_ret = [float('nan')] * n
        for i in range(1, n):
            if closes[i-1] > 0:
                on_ret[i] = opens[i] / closes[i-1] - 1
            if opens[i] > 0:
                id_ret[i] = closes[i] / opens[i] - 1

        # Volume for filtering
        ma_vol20 = []
        for i in range(n):
            if i < 19: ma_vol20.append(float('nan'))
            else:
                w = volumes[i-19:i+1]
                ma_vol20.append(sum(w) / 20)

        vals = {}
        min_idx = max(lookback, 60)
        for i in range(min_idx, n):
            dt = bars[i]['date']

            # Average overnight return over lookback (using t-1 to t-lookback)
            on_vals = []
            id_vals = []
            for j in range(lookback):
                idx = i - 1 - j
                if idx >= 0:
                    if not math.isnan(on_ret[idx]):
                        on_vals.append(on_ret[idx])
                    if not math.isnan(id_ret[idx]):
                        id_vals.append(id_ret[idx])

            if len(on_vals) < max(lookback // 2, 2) or len(id_vals) < max(lookback // 2, 2):
                vals[dt] = float('nan')
                continue

            on_avg = sum(on_vals) / len(on_vals)
            id_avg = sum(id_vals) / len(id_vals)

            # Volume adjustment: scale by relative volume
            if vol_filter and not math.isnan(ma_vol20[i-1]) and ma_vol20[i-1] > 0:
                vol_ratio = min(2.0, max(0.5, volumes[i-1] / ma_vol20[i-1]))
            else:
                vol_ratio = 1.0

            # Composite: overnight momentum (+) + intraday reversal (-id_avg because reversal)
            # Higher = more overnight momentum AND more intraday oversold
            score = (on_avg - id_avg) * vol_ratio
            vals[dt] = score

        factors[code] = vals
    return factors


def cross_sectional_z(values_dict):
    if len(values_dict) < 2: return {}
    codes = list(values_dict.keys())
    vals = [values_dict[c] for c in codes]
    n = len(vals)
    mu = sum(vals) / n
    var = sum((v - mu) ** 2 for v in vals) / n
    sigma = var ** 0.5 if var > 0 else 1.0
    return {c: (values_dict[c] - mu) / sigma for c in codes}


def backtest(stocks, factors, w_on, trail, rebal_days):
    """
    Backtest with overnight-intraday composite.
    w_on: weight on overnight-momentum component (1-w_on = weight on intraday-reversal)
    """
    codes = sorted(stocks.keys())
    date_maps = {c: {b['date']: b for b in stocks[c]['bars']} for c in codes}
    all_dates = sorted(set.union(*[set(m.keys()) for m in date_maps.values()]))
    first_dates = {c: stocks[c]['first_date'] for c in codes}

    positions = {}; cash = INIT; trades = []; dvs = []

    for di, dt in enumerate(all_dates):
        # Trail stops
        for code in list(positions.keys()):
            bar = date_maps[code].get(dt)
            if not bar or dt == positions[code]['entry_date']: continue
            px = bar['close']
            if px > positions[code]['peak']: positions[code]['peak'] = px
            if px <= positions[code]['peak'] * (1 - trail):
                sell_px = px * (1 - STAMP - SLIP)
                ret = (sell_px - positions[code]['buy_px']) / positions[code]['buy_px']
                trades.append({
                    'code': code, 'buy_d': positions[code]['entry_date'], 'sell_d': dt,
                    'ret': ret, 'exit': 'trail',
                })
                cash += positions[code]['shares'] * sell_px
                del positions[code]

        # Rebalance
        if di % rebal_days == 0:
            cand = []
            for c in codes:
                if c in positions: continue
                fac_val = factors.get(c, {}).get(dt, float('nan'))
                if math.isnan(fac_val): continue
                cand.append((c, fac_val))
            cand.sort(key=lambda x: x[1], reverse=True)

            top_codes = set(c for c, _ in cand[:MAX_POS])

            # Sell non-top
            for code in list(positions.keys()):
                if code not in top_codes and dt != positions[code]['entry_date']:
                    bar = date_maps[code].get(dt)
                    if not bar: continue
                    sell_px = bar['close'] * (1 - STAMP - SLIP)
                    ret = (sell_px - positions[code]['buy_px']) / positions[code]['buy_px']
                    trades.append({
                        'code': code, 'buy_d': positions[code]['entry_date'], 'sell_d': dt,
                        'ret': ret, 'exit': 'rebalance',
                    })
                    cash += positions[code]['shares'] * sell_px
                    del positions[code]

            # Buy top
            to_buy = [c for c, _ in cand[:MAX_POS] if c not in positions]
            if to_buy:
                per_pos = cash / max(len(to_buy), 1)
                for code in to_buy:
                    bar = date_maps[code][dt]
                    buy_px = bar['close'] * (1 + SLIP + COMM)
                    shares = per_pos / buy_px if buy_px > 0 else 0
                    if shares > 0 and per_pos <= cash:
                        positions[code] = {
                            'shares': shares, 'buy_px': buy_px,
                            'peak': bar['close'], 'entry_date': dt,
                        }
                        cash -= shares * buy_px

        # Mark-to-market
        pos_val = sum(
            p['shares'] * date_maps[c].get(dt, {}).get('close', 0) * (1 - STAMP - SLIP)
            for c, p in positions.items() if date_maps[c].get(dt)
        )
        dvs.append({'date': dt, 'value': cash + pos_val, 'n_pos': len(positions)})

    # Final liquidation
    last_dt = all_dates[-1]
    for code in list(positions.keys()):
        bar = date_maps[code].get(last_dt)
        if bar:
            sell_px = bar['close'] * (1 - STAMP - SLIP)
            ret = (sell_px - positions[code]['buy_px']) / positions[code]['buy_px']
            trades.append({
                'code': code, 'buy_d': positions[code]['entry_date'], 'sell_d': last_dt,
                'ret': ret, 'exit': 'final',
            })
            cash += positions[code]['shares'] * sell_px
        del positions[code]

    # Statistics
    fv = cash
    rets = []
    for i in range(1, len(dvs)):
        p, c = dvs[i-1]['value'], dvs[i]['value']
        if p > 0: rets.append((c - p) / p)
    if not rets: rets = [0.0]

    tr = (fv - INIT) / INIT
    years = len(rets) / TD
    ar = (1 + tr) ** (1 / years) - 1 if tr > -1 and years > 0 else -1

    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu)**2 for r in rets) / (len(rets) - 1)) ** 0.5
        av = sd * math.sqrt(TD)
        ar_ = mu * TD
        sh = (ar_ - RF) / av if av > 0 else 0
    else:
        av = sh = ar_ = 0.0

    pkv = dvs[0]['value']; mdd = 0.0
    for dv in dvs:
        if dv['value'] > pkv: pkv = dv['value']
        dd = (pkv - dv['value']) / pkv
        if dd > mdd: mdd = dd
    cm = ar / mdd if mdd > 0 else float('inf')
    wins = sum(1 for t in trades if t['ret'] > 0)
    wr = wins / len(trades) if trades else 0

    return {
        'tr': tr, 'ar': ar, 'vol': av, 'sh': sh, 'cm': cm, 'mdd': mdd,
        'np': len(trades), 'wr': wr, 'dvs': dvs, 'trades': trades, 'fv': fv,
    }


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print('=' * 100)
    print('  Strategy 3: Overnight-Intraday Return Decomposition (隔夜-日内收益分解)')
    print(f'  Grid: {len(LOOKBACKS)} windows × {len(W_ON)} weights × {len(TRAILS)} trails × '
          f'{len(REBAL_DAYS)} rebal × {len(VOL_FILTER)} vol_filt = {TOTAL} combos')
    print('  Overnight (close→open) = momentum continuation')
    print('  Intraday (open→close)  = reversal')
    print('  Score = on_avg - id_avg  (higher = momentum + oversold)')
    print('=' * 100)

    print('\n[DATA] Loading...')
    stocks = load_stocks()
    print(f'  {len(stocks)} stocks loaded')

    # Pre-compute factors
    factor_cache = {}
    for lb in LOOKBACKS:
        for vf in VOL_FILTER:
            key = (lb, vf)
            print(f'  Computing: lb={lb}d vol_filter={vf}...')
            factor_cache[key] = compute_oi_factors(stocks, lb, vf)
    print(f'  {len(factor_cache)} factor variants cached.')

    results = []; count = 0; best_sh = -999

    print(f'\n[GRID] Running {TOTAL} combos...')
    for lb in LOOKBACKS:
        for vf in VOL_FILTER:
            fac = factor_cache[(lb, vf)]
            for w_on in W_ON:
                for trail in TRAILS:
                    for rb in REBAL_DAYS:
                        count += 1
                        label = f'LB={lb:>2d}d ONw={w_on:.1f} T={trail:.0%} Rebal={rb}d VF={vf}'
                        r = backtest(stocks, fac, w_on, trail, rb)
                        r.update({
                            'lb': lb, 'w_on': w_on, 'trail': trail,
                            'rebal': rb, 'vol_filter': vf, 'label': label,
                        })
                        results.append(r)
                        if r['sh'] > best_sh: best_sh = r['sh']
                        if count % 50 == 1 or count == TOTAL:
                            print(f'  [{count:>5d}/{TOTAL}] {label:<40s} '
                                  f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% '
                                  f'DD={r["mdd"]*100:>5.2f}% Trd={r["np"]:>4d} '
                                  f'Best={best_sh:.4f}')

    results.sort(key=lambda x: x['sh'], reverse=True)

    print('\n\n' + '=' * 120)
    print('  TOP 30 BY SHARPE')
    print('=' * 120)
    hdr = (f'  {"Rk":<3s} {"LB":>4s} {"ONw":>5s} {"Trail":>6s} {"Rebal":>6s} {"VF":>3s} '
           f'{"S":>7s} {"Ret":>9s} {"Ann":>7s} {"DD":>6s} {"Calmar":>7s} '
           f'{"Trd":>4s} {"Win":>5s}')
    print(hdr); print(f'  {"-"*90}')
    for rank, r in enumerate(results[:30], 1):
        print(f'  {rank:<3d} {r["lb"]:>4d} {r["w_on"]:>4.1f}  {r["trail"]:>5.0%}  '
              f'{r["rebal"]:>3d}d  {"Y" if r["vol_filter"] else "N":>3s}  '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>8.2f}% {r["ar"]*100:>6.2f}% '
              f'{r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} {r["np"]:>4d} {r["wr"]*100:>4.0f}%')

    # Parameter sensitivity
    print('\n\n  PARAMETER SENSITIVITY (avg Sharpe):')
    for pname, pkey, fmt in [
        ('Lookback Window', 'lb', 'd'),
        ('Overnight Weight', 'w_on', '.1f'),
        ('Trail Stop', 'trail', '.0%'),
        ('Rebalance Days', 'rebal', 'd'),
        ('Volume Filter', 'vol_filter', ''),
    ]:
        levels = defaultdict(list)
        for r in results: levels[r[pkey]].append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(levels.keys()):
            avg = sum(levels[v]) / len(levels[v])
            bar = '#' * max(int(avg * 40), 1) if avg > 0 else '·' * max(int(abs(avg) * 40), 1)
            if fmt == '.1f': lbl = f'{v:.1f}'
            elif fmt == '.0%': lbl = f'{v:.0%}'
            elif fmt == 'd': lbl = f'{v}d'
            else: lbl = 'Yes' if v else 'No'
            print(f'    {lbl:>8s}  avg S={avg:>7.3f} n={len(levels[v]):>4d}  {bar}')

    # Lookback × Overnight Weight heatmap
    print('\n\n  LOOKBACK × OVERNIGHT WEIGHT HEATMAP (avg Sharpe):')
    print('  ' + ''.join(f'  ONw={w:.1f}' for w in W_ON))
    for lb in LOOKBACKS:
        print(f'  LB={lb:>2d}d', end='')
        for w_on in W_ON:
            subset = [r for r in results if r['lb'] == lb and r['w_on'] == w_on]
            avg_s = sum(r['sh'] for r in subset) / len(subset) if subset else 0
            print(f'  {avg_s:>8.3f}', end='')
        print()

    # Best detail
    best = results[0]
    print(f'\n\n  {"="*80}')
    print(f'  BEST: {best["label"]}')
    print(f'  S={best["sh"]:.4f} Ret={best["tr"]*100:.2f}% Ann={best["ar"]*100:.2f}% '
          f'DD={best["mdd"]*100:.2f}% Calmar={best["cm"]:.3f}')
    print(f'  Trades={best["np"]} Win={best["wr"]*100:.0f}%')

    exits = defaultdict(lambda: {'cnt': 0, 'ret_sum': 0.0})
    for t in best['trades']:
        exits[t['exit']]['cnt'] += 1; exits[t['exit']]['ret_sum'] += t['ret']
    print(f'\n  Exit breakdown:')
    for e in ['trail', 'rebalance', 'final']:
        d = exits.get(e)
        if d:
            print(f'    {e:<12s} cnt={d["cnt"]:>4d} avg_ret={d["ret_sum"]/d["cnt"]*100:>+7.2f}%')

    best['trades'].sort(key=lambda x: x['ret'], reverse=True)
    for tag, subset in [('Best 5', best['trades'][:5]), ('Worst 5', best['trades'][-5:])]:
        print(f'\n  {tag}:')
        for t in subset:
            print(f'    {t["code"]} {stocks[t["code"]]["name"]:<10s} '
                  f'{t["buy_d"]} -> {t["sell_d"]} {t["ret"]*100:>+7.2f}% {t["exit"]}')

    with open('strategy_overnight_intraday_equity.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['date', 'equity', 'positions'])
        for d in best['dvs']: w.writerow([d['date'], f'{d["value"]:.2f}', d['n_pos']])
    print(f'\n  Exported: strategy_overnight_intraday_equity.csv')

    yr = defaultdict(lambda: {'s': None, 'e': None})
    for d in best['dvs']:
        yk = d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s'] = d['value']
        yr[yk]['e'] = d['value']
    print('\n  Annual:')
    for y in sorted(yr.keys()):
        ret = (yr[y]['e'] - yr[y]['s']) / yr[y]['s'] * 100 if yr[y]['s'] and yr[y]['s'] > 0 else 0
        print(f'    {y}: {ret:+.1f}%')

    print('\n  Done!')
    return best


if __name__ == '__main__':
    main()
