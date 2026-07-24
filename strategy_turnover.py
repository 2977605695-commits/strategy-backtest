"""
Strategy 2: Turnover Factor Family (换手率因子族)
===================================================
A股截面收益预测中最稳健的单因子族。逻辑基础：散户对高换手股票的过度
交易推高价格→后续回归，机构对低换手股票要求流动性溢价。

因子变体：
  Turn20:   -avg(换手率, 20d)               Rank IC -4~6%  ICIR 0.8-1.0
  PctTurn:  -(换手率变化率, 5d vs 20d)       Rank IC -3~5%  ICIR 0.6-0.9
  STR:      -1/CV(换手率, 20d)               Rank IC -3~5%  ICIR 0.6-0.8
  TO:       Turn20 + PctTurn + STR 等权复合  Rank IC -5~8%  ICIR 0.8-1.2

A股特有逻辑：
  1. 散户交易占比~60-70%，偏好高换手率、高关注度股票
  2. 机构偏好高流动性→对低换手股票要求溢价
  3. 卖空限制使套利者无法纠正高换手股票的定价偏差

参考来源:
  - 东吴证券《换手率分布均匀度UTD选股因子》《量稳换手率STR》《优加换手率UTR2.0》
  - 华泰金工《技术指标因子高频化》
  - 国盛金工《基于流动性冲击事件的逐笔羊群效应因子》
"""
import sys, io, json, math, os, csv
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
RF = 0.025; TD = 252; INIT = 10_000_000; MAX_POS = 5
SLIP = 0.003; COMM = 0.00025; STAMP = 0.0005

# Factor variants
FACTOR_VARIANTS = ['turn20', 'pctturn', 'str', 'composite']
TRAILS = [0.10, 0.15, 0.20, 0.25]
REBAL_DAYS = [5, 10, 21]
TOTAL = len(FACTOR_VARIANTS) * len(TRAILS) * len(REBAL_DAYS)


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


def calc_ma(data, w):
    ma = []; n = len(data)
    for i in range(n):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma


def compute_turnover_factors(stocks):
    """
    Compute all 4 turnover factor variants for every stock/date.

    turnover[t] = volume[t] as proxy (not market-cap normalized, but cross-sectionally
    we rank within date so absolute scale doesn't matter; volume is the standard A-share proxy)

    Returns: dict variant -> dict code -> dict date -> float
    """
    all_factors = {v: {} for v in FACTOR_VARIANTS}

    for code, info in stocks.items():
        bars = info['bars']
        volumes = [b['volume'] for b in bars]
        n = len(bars)

        ma_vol5 = calc_ma(volumes, 5)
        ma_vol20 = calc_ma(volumes, 20)

        # Pre-compute for STR: rolling 20d CV
        cv_20 = {}
        for i in range(60, n):
            if not math.isnan(ma_vol20[i]) and ma_vol20[i] > 0:
                w = volumes[i-19:i+1]
                mu = ma_vol20[i]
                var = sum((v - mu)**2 for v in w) / 20
                std = var ** 0.5
                cv_20[bars[i]['date']] = std / mu if mu > 0 else 0
            else:
                cv_20[bars[i]['date']] = float('nan')

        for variant in FACTOR_VARIANTS:
            vals = {}
            for i in range(60, n):
                dt = bars[i]['date']

                if variant == 'turn20':
                    # Average turnover (negative: low turnover → higher return)
                    if not math.isnan(ma_vol20[i]):
                        # Use log to reduce outlier impact
                        vals[dt] = -math.log(ma_vol20[i] + 1)

                elif variant == 'pctturn':
                    # Turnover change rate (negative: decreasing turnover → higher return)
                    if not math.isnan(ma_vol5[i]) and not math.isnan(ma_vol20[i]) and ma_vol20[i] > 0:
                        pct = (ma_vol5[i] - ma_vol20[i]) / ma_vol20[i]
                        vals[dt] = -pct
                    else:
                        vals[dt] = float('nan')

                elif variant == 'str':
                    # Stable turnover: 1/CV (negative sign: less stable → more likely over-traded)
                    cv = cv_20.get(dt, float('nan'))
                    if not math.isnan(cv) and cv > 0:
                        vals[dt] = -(cv)  # higher CV = more volume variability = bad
                    else:
                        vals[dt] = float('nan')

                elif variant == 'composite':
                    # Equal-weight composite of all three
                    t20 = float('nan'); pt = float('nan'); st = float('nan')

                    if not math.isnan(ma_vol20[i]):
                        t20 = -math.log(ma_vol20[i] + 1)
                    if not math.isnan(ma_vol5[i]) and not math.isnan(ma_vol20[i]) and ma_vol20[i] > 0:
                        pt = -(ma_vol5[i] - ma_vol20[i]) / ma_vol20[i]
                    cv = cv_20.get(dt, float('nan'))
                    if not math.isnan(cv) and cv > 0:
                        st = -cv

                    # Z-score normalization will happen cross-sectionally
                    parts = [v for v in [t20, pt, st] if not math.isnan(v)]
                    if parts:
                        vals[dt] = sum(parts) / len(parts)
                    else:
                        vals[dt] = float('nan')

            all_factors[variant][code] = vals

    return all_factors


def cross_sectional_z(values_dict):
    if len(values_dict) < 2: return {}
    codes = list(values_dict.keys())
    vals = [values_dict[c] for c in codes]
    n = len(vals)
    mu = sum(vals) / n
    var = sum((v - mu) ** 2 for v in vals) / n
    sigma = var ** 0.5 if var > 0 else 1.0
    return {c: (values_dict[c] - mu) / sigma for c in codes}


def backtest(stocks, factors, trail, rebal_days):
    codes = sorted(stocks.keys())
    date_maps = {c: {b['date']: b for b in stocks[c]['bars']} for c in codes}
    all_dates = sorted(set.union(*[set(m.keys()) for m in date_maps.values()]))
    first_dates = {c: stocks[c]['first_date'] for c in codes}

    positions = {}; cash = INIT; trades = []; dvs = []

    for di, dt in enumerate(all_dates):
        # Trail stops (T+1 safe)
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

            # Sell non-top (T+1 safe)
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

            # Buy top (equal weight)
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
    print('  Strategy 2: Turnover Factor Family (换手率因子族)')
    print(f'  Grid: {len(FACTOR_VARIANTS)} variants × {len(TRAILS)} trails × '
          f'{len(REBAL_DAYS)} rebal = {TOTAL} combos')
    print('  Variants: turn20 | pctturn | str | composite')
    print('  Hypothesis: low turnover → higher future returns (liquidity premium)')
    print('=' * 100)

    print('\n[DATA] Loading...')
    stocks = load_stocks()
    print(f'  {len(stocks)} stocks loaded')

    print('[FACTORS] Computing all 4 turnover variants...')
    all_factors = compute_turnover_factors(stocks)
    for v in FACTOR_VARIANTS:
        nv = sum(len(vals) for vals in all_factors[v].values())
        print(f'  {v:<12s}: {nv} date-points')
    print('  Factors cached.')

    results = []; count = 0; best_sh = -999

    print(f'\n[GRID] Running {TOTAL} combos...')
    for variant in FACTOR_VARIANTS:
        fac = all_factors[variant]
        for trail in TRAILS:
            for rb in REBAL_DAYS:
                count += 1
                label = f'{variant:<12s} T={trail:.0%} Rebal={rb}d'
                r = backtest(stocks, fac, trail, rb)
                r.update({'variant': variant, 'trail': trail, 'rebal': rb, 'label': label})
                results.append(r)
                if r['sh'] > best_sh: best_sh = r['sh']
                if count % 20 == 1 or count == TOTAL:
                    print(f'  [{count:>4d}/{TOTAL}] {label:<35s} '
                          f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% '
                          f'DD={r["mdd"]*100:>5.2f}% Trd={r["np"]:>4d} '
                          f'Win={r["wr"]*100:>4.0f}% Best={best_sh:.4f}')

    results.sort(key=lambda x: x['sh'], reverse=True)

    print('\n\n' + '=' * 120)
    print('  TOP 30 BY SHARPE')
    print('=' * 120)
    hdr = (f'  {"Rk":<3s} {"Variant":<12s} {"Trail":>6s} {"Rebal":>6s} '
           f'{"S":>7s} {"Ret":>9s} {"Ann":>7s} {"DD":>6s} {"Calmar":>7s} '
           f'{"Trd":>4s} {"Win":>5s}')
    print(hdr); print(f'  {"-"*90}')
    for rank, r in enumerate(results[:30], 1):
        print(f'  {rank:<3d} {r["variant"]:<12s} {r["trail"]:>5.0%}  {r["rebal"]:>3d}d  '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>8.2f}% {r["ar"]*100:>6.2f}% '
              f'{r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} {r["np"]:>4d} {r["wr"]*100:>4.0f}%')

    # Factor variant comparison
    print('\n\n  FACTOR VARIANT COMPARISON (avg Sharpe):')
    for variant in FACTOR_VARIANTS:
        subset = [r for r in results if r['variant'] == variant]
        if subset:
            best_v = max(subset, key=lambda x: x['sh'])
            avg_s = sum(r['sh'] for r in subset) / len(subset)
            avg_ret = sum(r['tr'] for r in subset) / len(subset) * 100
            print(f'    {variant:<12s} best S={best_v["sh"]:.4f} avg S={avg_s:.4f} '
                  f'avg Ret={avg_ret:.1f}% best params: T={best_v["trail"]:.0%} Rebal={best_v["rebal"]}d')

    # Parameter sensitivity
    print('\n\n  PARAMETER SENSITIVITY (avg Sharpe):')
    for pname, pkey in [('Trail Stop', 'trail'), ('Rebalance Days', 'rebal')]:
        levels = defaultdict(list)
        for r in results: levels[r[pkey]].append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(levels.keys()):
            avg = sum(levels[v]) / len(levels[v])
            bar = '#' * max(int(avg * 40), 1) if avg > 0 else '·' * max(int(abs(avg) * 40), 1)
            lbl = f'{v}d' if isinstance(v, int) else f'{v:.0%}'
            print(f'    {lbl:>8s}  avg S={avg:>7.3f} n={len(levels[v]):>4d}  {bar}')

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

    with open('strategy_turnover_equity.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['date', 'equity', 'positions'])
        for d in best['dvs']: w.writerow([d['date'], f'{d["value"]:.2f}', d['n_pos']])
    print(f'\n  Exported: strategy_turnover_equity.csv')

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
