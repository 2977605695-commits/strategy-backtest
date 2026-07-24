"""
Strategy 1: Short-term Reversal Factor (短期反转因子)
======================================================
A股最强因子族之一。T+1制度+散户主导 → 过度反应 → 价格短期回归。

因子构造:  -(过去N日累计收益率)，即买跌卖涨
增强:  高换手率股票的反弹效应更强（换手率交互项）
风控:  Trail移动止损 | 剔除涨跌停日(stale price bias)
容量:  50-150亿（中证500/1000域）

参考来源:
  - Liu,Chen & Zhu(2024). "Only strong short-term contrarian effect exists in
    Chinese stock market: The role of the T+1 trading mechanism." IRFAE, 96(PB).
  - 东吴证券《成交量对动量因子的修正：日与夜之殊途同归》
  - 广发金工 2024-2025 Alpha因子表现跟踪
"""
import sys, io, json, math, os, csv
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
RF = 0.025; TD = 252; INIT = 10_000_000; MAX_POS = 5
SLIP = 0.003; COMM = 0.00025; STAMP = 0.0005

# Grid search space
REV_WINDOWS = [3, 5, 7, 10, 14]      # reversal lookback days
TRAILS = [0.10, 0.15, 0.20, 0.25]     # trail stop %
TURN_THRESHOLDS = [0, 0.5, 1.0]       # turnover z-score threshold (0=no filter)
REBAL_DAYS = [5, 10, 21]               # rebalance frequency (weekly/biweekly/monthly)

TOTAL = len(REV_WINDOWS) * len(TRAILS) * len(TURN_THRESHOLDS) * len(REBAL_DAYS)


def load_stocks():
    """Load all 70 stocks, return dict code->{name, bars}"""
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


def calc_ma(data, w):
    ma = []; n = len(data)
    for i in range(n):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma


def compute_factors(stocks, rev_window):
    """
    Compute reversal and turnover factors for all stocks.

    reversal_score[t] = -(close[t-1]/close[t-rev_window-1] - 1) * turnover_boost
      where turnover_boost = min(1.5, max(0.5, volume_z[t] + 1))
      Higher score = stronger reversal (more oversold)

    Also flags limit-hit days to avoid stale price bias.
    """
    factors = {}
    for code, info in stocks.items():
        bars = info['bars']
        closes = [b['close'] for b in bars]
        highs = [b['high'] for b in bars]
        lows = [b['low'] for b in bars]
        volumes = [b['volume'] for b in bars]
        opens = [b['open'] for b in bars]
        n = len(bars)

        # Compute 20-day avg turnover for normalization
        ma_vol20 = calc_ma(volumes, 20)

        vals = {}
        for i in range(max(rev_window, 60), n):
            dt = bars[i]['date']

            # Check if recent bars hit limit up/down → exclude
            hit_limit = False
            for j in range(max(0, i-rev_window), i):
                if closes[j] > 0:
                    ret = (closes[j] - closes[j-1]) / closes[j-1] if j > 0 and closes[j-1] > 0 else 0
                    # Approximate limit detection: >9.5% or <-9.5% daily move
                    if abs(ret) > 0.095:
                        hit_limit = True
                        break

            if hit_limit:
                vals[dt] = float('nan')
                continue

            # Reversal: past N-day return (negative = oversold, we want positive score)
            if closes[i-1] > 0 and closes[i-rev_window-1] > 0:
                past_ret = (closes[i-1] / closes[i-rev_window-1] - 1)
            else:
                vals[dt] = float('nan')
                continue

            reversal = -past_ret  # higher = more oversold/reversal

            # Turnover boost: higher than avg turnover → stronger reversal signal
            if not math.isnan(ma_vol20[i]) and ma_vol20[i] > 0:
                vol_ratio = volumes[i-1] / ma_vol20[i]
                boost = min(1.5, max(0.5, vol_ratio))
            else:
                boost = 1.0

            vals[dt] = reversal * boost

        factors[code] = vals

    return factors


def cross_sectional_z(values_dict):
    """Compute cross-sectional z-scores (population std)."""
    if len(values_dict) < 2: return {}
    codes = list(values_dict.keys())
    vals = [values_dict[c] for c in codes]
    n = len(vals)
    mu = sum(vals) / n
    var = sum((v - mu) ** 2 for v in vals) / n
    sigma = var ** 0.5 if var > 0 else 1.0
    return {c: (values_dict[c] - mu) / sigma for c in codes}


def backtest(stocks, factors, trail, turn_thresh, rebal_days):
    """
    Backtest the reversal strategy.

    Args:
        turn_thresh: minimum turnover z-score to enter (0 = no filter)
        rebal_days: rebalance every N trading days
    """
    codes = sorted(stocks.keys())
    date_maps = {c: {b['date']: b for b in stocks[c]['bars']} for c in codes}
    all_dates = sorted(set.union(*[set(m.keys()) for m in date_maps.values()]))
    first_dates = {c: stocks[c]['first_date'] for c in codes}

    positions = {}; cash = INIT; trades = []; dvs = []

    for di, dt in enumerate(all_dates):
        available = [c for c in codes if first_dates[c] <= dt]

        # Trail stops
        for code in list(positions.keys()):
            bar = date_maps[code].get(dt)
            if not bar: continue
            # T+1: can't sell on buy day
            if dt == positions[code]['entry_date']: continue
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

        # Rebalance check
        if di % rebal_days == 0:
            # Compute turnover z-scores for the turnover filter
            turn_zs = {}
            for c in available:
                bar = date_maps[c].get(dt)
                if not bar or c in positions: continue
                # Simple: use 20-day avg volume relative to history
                idx = stocks[c]['bars'].index(bar)
                if idx >= 60:
                    vols = [stocks[c]['bars'][j]['volume'] for j in range(idx-19, idx+1)]
                    avg_vol = sum(vols) / 20
                    all_vols = [stocks[c]['bars'][j]['volume'] for j in range(20, idx+1)]
                    mu_v = sum(all_vols) / len(all_vols)
                    sd_v = (sum((v - mu_v)**2 for v in all_vols) / len(all_vols)) ** 0.5 if all_vols else 1
                    turn_zs[c] = (avg_vol - mu_v) / sd_v if sd_v > 0 else 0

            # Rank by reversal factor
            cand = []
            for c in available:
                if c in positions: continue
                fac_val = factors.get(c, {}).get(dt, float('nan'))
                if math.isnan(fac_val): continue

                # Turnover filter
                if turn_thresh > 0:
                    tz = turn_zs.get(c, 0)
                    if tz < turn_thresh: continue  # skip low-turnover stocks

                cand.append((c, fac_val))

            cand.sort(key=lambda x: x[1], reverse=True)

            # Sell positions no longer in top N
            top_codes = set(c for c, _ in cand[:MAX_POS])
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

            # Buy new top stocks (equal weight)
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
    print('  Strategy 1: Short-term Reversal Factor (短期反转因子)')
    print(f'  Grid: {len(REV_WINDOWS)} windows × {len(TRAILS)} trails × '
          f'{len(TURN_THRESHOLDS)} turn_filters × {len(REBAL_DAYS)} rebal = {TOTAL} combos')
    print('  Factor: -(past N-day return) × turnover_boost | T+1 safe')
    print('=' * 100)

    print('\n[DATA] Loading...')
    stocks = load_stocks()
    print(f'  {len(stocks)} stocks loaded')

    # Pre-compute factors for each reversal window
    factor_cache = {}
    for rw in REV_WINDOWS:
        print(f'  Computing factors: window={rw}d...')
        factor_cache[rw] = compute_factors(stocks, rw)
    print('  Factors cached.')

    results = []; count = 0; best_sh = -999

    print(f'\n[GRID] Running {TOTAL} combos...')
    for rw in REV_WINDOWS:
        for trail in TRAILS:
            for tt in TURN_THRESHOLDS:
                fac = factor_cache[rw]
                for rb in REBAL_DAYS:
                    count += 1
                    label = f'Rev{rw}d T={trail:.0%} TurnZ>{tt} Rebal={rb}d'
                    r = backtest(stocks, fac, trail, tt, rb)
                    r.update({'rw': rw, 'trail': trail, 'turn_tt': tt, 'rebal': rb, 'label': label})
                    results.append(r)
                    if r['sh'] > best_sh: best_sh = r['sh']
                    if count % 50 == 1 or count == TOTAL:
                        print(f'  [{count:>5d}/{TOTAL}] {label:<40s} '
                              f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% '
                              f'DD={r["mdd"]*100:>5.2f}% Trd={r["np"]:>4d} '
                              f'Win={r["wr"]*100:>4.0f}% Best={best_sh:.4f}')

    # Sort & display
    results.sort(key=lambda x: x['sh'], reverse=True)

    print('\n\n' + '=' * 120)
    print('  TOP 30 BY SHARPE')
    print('=' * 120)
    hdr = (f'  {"Rk":<3s} {"Window":>7s} {"Trail":>6s} {"TurnZ":>6s} {"Rebal":>6s} '
           f'{"S":>7s} {"Ret":>9s} {"Ann":>7s} {"DD":>6s} {"Calmar":>7s} '
           f'{"Trd":>4s} {"Win":>5s}')
    print(hdr); print(f'  {"-"*90}')
    for rank, r in enumerate(results[:30], 1):
        print(f'  {rank:<3d} Rev{r["rw"]:>2d}d  {r["trail"]:>5.0%}  Turn>{r["turn_tt"]:.1f}  '
              f'{r["rebal"]:>3d}d  {r["sh"]:>7.3f} {r["tr"]*100:>8.2f}% '
              f'{r["ar"]*100:>6.2f}% {r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} '
              f'{r["np"]:>4d} {r["wr"]*100:>4.0f}%')

    # Parameter sensitivity
    print('\n\n  PARAMETER SENSITIVITY (avg Sharpe):')
    for pname, pkey in [
        ('Reversal Window', 'rw'), ('Trail Stop', 'trail'),
        ('Turnover Threshold', 'turn_tt'), ('Rebalance Days', 'rebal')]:
        levels = defaultdict(list)
        for r in results: levels[r[pkey]].append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(levels.keys()):
            avg = sum(levels[v]) / len(levels[v])
            bar = '#' * max(int(avg * 40), 1) if avg > 0 else '·' * max(int(abs(avg) * 40), 1)
            lbl = f'{v}d' if isinstance(v, int) and v > 1 else f'{v:.0%}' if isinstance(v, float) else str(v)
            print(f'    {lbl:>8s}  avg S={avg:>7.3f} n={len(levels[v]):>4d}  {bar}')

    # Window x Trail heatmap
    print('\n\n  WINDOW × TRAIL HEATMAP (avg Sharpe):')
    print('  ' + ''.join(f'  T={t:.0%} ' for t in TRAILS))
    for rw in REV_WINDOWS:
        row = []
        print(f'  Rev{rw:>2d}d', end='')
        for trail in TRAILS:
            subset = [r for r in results if r['rw'] == rw and r['trail'] == trail]
            avg_s = sum(r['sh'] for r in subset) / len(subset) if subset else 0
            row.append(avg_s)
            print(f'  {avg_s:>7.3f}', end='')
        print(f'  avg={sum(row)/len(row):.3f}')

    # Best detail
    best = results[0]
    print(f'\n\n  {"="*80}')
    print(f'  BEST: {best["label"]}')
    print(f'  S={best["sh"]:.4f} Ret={best["tr"]*100:.2f}% Ann={best["ar"]*100:.2f}% '
          f'DD={best["mdd"]*100:.2f}% Calmar={best["cm"]:.3f}')
    print(f'  Trades={best["np"]} Win={best["wr"]*100:.0f}%')

    # Exit breakdown
    exits = defaultdict(lambda: {'cnt': 0, 'ret_sum': 0.0})
    for t in best['trades']:
        exits[t['exit']]['cnt'] += 1; exits[t['exit']]['ret_sum'] += t['ret']
    print(f'\n  Exit breakdown:')
    for e in ['trail', 'rebalance', 'final']:
        d = exits.get(e)
        if d:
            print(f'    {e:<12s} cnt={d["cnt"]:>4d} avg_ret={d["ret_sum"]/d["cnt"]*100:>+7.2f}%')

    # Top/bottom trades
    best['trades'].sort(key=lambda x: x['ret'], reverse=True)
    for tag, subset in [('Best 5', best['trades'][:5]), ('Worst 5', best['trades'][-5:])]:
        print(f'\n  {tag}:')
        for t in subset:
            print(f'    {t["code"]} {stocks[t["code"]]["name"]:<10s} '
                  f'{t["buy_d"]} -> {t["sell_d"]} {t["ret"]*100:>+7.2f}% {t["exit"]}')

    # Save equity curve
    with open('strategy_reversal_equity.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['date', 'equity', 'positions'])
        for d in best['dvs']: w.writerow([d['date'], f'{d["value"]:.2f}', d['n_pos']])
    print(f'\n  Exported: strategy_reversal_equity.csv')

    # Annual returns
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
