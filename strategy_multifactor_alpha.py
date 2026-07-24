"""
Strategy 4: Multi-Factor Alpha Composite (多因子Alpha复合)
============================================================
将A股最强的5个日频因子合成为单一Alpha信号，IDR 1.0-1.8。

因子组成（每个独立z-score后等权/IC加权）：
  F1 短期反转    -(5日收益率)               ICIR 0.8-1.2  Weight 25%
  F2 换手率      -log(20日均量)              ICIR 0.6-1.0  Weight 25%
  F3 隔夜-日内   on_avg - id_avg            ICIR 0.7-1.0  Weight 20%
  F4 低波动      -std(20日对数收益率)         ICIR 0.5-0.8  Weight 15%
  F5 MAX效应     -max(20日单日收益)           ICIR 0.5-0.7  Weight 15%

增强特性:
  - 赛道分散: 同行业最多N只持仓（避免行业集中风险）
  - 量能过滤: 剔除极度缩量股票
  - Trail止损: 个股移动止损

参考来源:
  - 广发金工《2024-2025 Alpha因子表现跟踪》
  - 中金《高频因子手册》(日频聚合部分)
  - 华泰金工《AI系列：混合频率量价因子模型》
  - Liu et al. (2024). IRFAE. "T+1 and contrarian effect"
  - 东吴证券换手率因子系列 (UTD, STR, UTR)
"""
import sys, io, json, math, os, csv
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
FUND_DIR = os.path.join(DATA_DIR, 'fundamentals_70stocks')
RF = 0.025; TD = 252; INIT = 10_000_000; MAX_POS = 5
SLIP = 0.003; COMM = 0.00025; STAMP = 0.0005

# Grid search space
WEIGHT_SETS = [
    # (w_rev, w_to, w_oi, w_vol, w_max) — must sum to 1.0
    (0.25, 0.25, 0.20, 0.15, 0.15),  # research-memo ICIR-weighted
    (0.20, 0.20, 0.20, 0.20, 0.20),  # equal weight
    (0.30, 0.30, 0.15, 0.15, 0.10),  # bias toward most proven
    (0.20, 0.30, 0.20, 0.15, 0.15),  # turnover-heavy
    (0.30, 0.20, 0.20, 0.15, 0.15),  # reversal-heavy
]
TRAILS = [0.10, 0.15, 0.20, 0.25]
REBAL_DAYS = [5, 10, 21]
MAX_SAME_SECTOR = [1, 2, 3]  # 1=strict, 3=relaxed
TOTAL = len(WEIGHT_SETS) * len(TRAILS) * len(REBAL_DAYS) * len(MAX_SAME_SECTOR)


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


def load_sectors(stocks):
    """Load sector info from the latest fundamentals CSV."""
    csvs = sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
    if not csvs:
        return {c: '' for c in stocks}
    sector_map = {}
    with open(os.path.join(FUND_DIR, csvs[-1]), 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            code = row['code'].strip()
            sec = row.get('sector', '').strip()
            if code and sec:
                sector_map[code] = sec
    # Fallback for missing codes
    for c in stocks:
        if c not in sector_map:
            sector_map[c] = ''
    return sector_map


def calc_ma(data, w):
    ma = []; n = len(data)
    for i in range(n):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma


def compute_all_factors(stocks):
    """
    Compute all 5 factors for every stock/date.
    Returns dict with keys: 'reversal', 'turnover', 'oi', 'vol', 'max'
    Each maps to dict code -> dict date -> float
    """
    all_fac = {k: {} for k in ['reversal', 'turnover', 'oi', 'vol', 'max']}

    for code, info in stocks.items():
        bars = info['bars']
        n = len(bars)
        closes = [b['close'] for b in bars]
        opens = [b['open'] for b in bars]
        volumes = [b['volume'] for b in bars]

        # Pre-compute helpers
        ma_vol20 = calc_ma(volumes, 20)
        log_rets = [float('nan')]
        for i in range(1, n):
            if closes[i-1] > 0:
                log_rets.append(math.log(closes[i] / closes[i-1]))
            else:
                log_rets.append(float('nan'))

        # Overnight and intraday returns
        on_ret = [float('nan')] * n
        id_ret = [float('nan')] * n
        for i in range(1, n):
            if closes[i-1] > 0:
                on_ret[i] = opens[i] / closes[i-1] - 1
            if opens[i] > 0:
                id_ret[i] = closes[i] / opens[i] - 1

        rev_vals = {}; to_vals = {}; oi_vals = {}; vol_vals = {}; max_vals = {}

        for i in range(60, n):
            dt = bars[i]['date']

            # ---- F1: Short-term Reversal (5-day) ----
            if i >= 6 and closes[i-1] > 0 and closes[i-6] > 0:
                ret_5d = (closes[i-1] / closes[i-6] - 1)
                # Check for limit hits
                hit_limit = False
                for j in range(max(0, i-5), i):
                    if closes[j] > 0 and closes[j-1] > 0:
                        if abs(closes[j]/closes[j-1] - 1) > 0.095:
                            hit_limit = True; break
                rev_vals[dt] = -ret_5d if not hit_limit else float('nan')
            else:
                rev_vals[dt] = float('nan')

            # ---- F2: Turnover (20-day avg volume, log) ----
            if not math.isnan(ma_vol20[i]):
                to_vals[dt] = -math.log(ma_vol20[i] + 1)
            else:
                to_vals[dt] = float('nan')

            # ---- F3: Overnight-Intraday (5-day avg) ----
            on_sum = 0.0; on_cnt = 0
            id_sum = 0.0; id_cnt = 0
            for j in range(5):
                idx = i - 1 - j
                if idx >= 0:
                    if not math.isnan(on_ret[idx]):
                        on_sum += on_ret[idx]; on_cnt += 1
                    if not math.isnan(id_ret[idx]):
                        id_sum += id_ret[idx]; id_cnt += 1
            if on_cnt >= 2 and id_cnt >= 2:
                oi_vals[dt] = (on_sum / on_cnt) - (id_sum / id_cnt)  # overnight mom - intraday rev
            else:
                oi_vals[dt] = float('nan')

            # ---- F4: Realized Volatility (20-day, negative) ----
            lr_window = [lr for lr in log_rets[i-19:i+1] if not math.isnan(lr)]
            if len(lr_window) >= 10:
                mu_lr = sum(lr_window) / len(lr_window)
                var_lr = sum((lr - mu_lr)**2 for lr in lr_window) / len(lr_window)
                std_lr = var_lr ** 0.5
                vol_vals[dt] = -std_lr  # lower vol → higher expected return
            else:
                vol_vals[dt] = float('nan')

            # ---- F5: MAX Effect (max daily return in past 20 days, negative) ----
            daily_rets = []
            for j in range(max(0, i-19), i):
                if closes[j] > 0 and closes[j-1] > 0:
                    daily_rets.append((closes[j] - closes[j-1]) / closes[j-1])
            if daily_rets:
                max_ret = max(daily_rets)
                max_vals[dt] = -max_ret  # negative: high max ret → gambling premium → lower future return
            else:
                max_vals[dt] = float('nan')

        all_fac['reversal'][code] = rev_vals
        all_fac['turnover'][code] = to_vals
        all_fac['oi'][code] = oi_vals
        all_fac['vol'][code] = vol_vals
        all_fac['max'][code] = max_vals

    return all_fac


def cross_sectional_z(values_dict, winsorize_pct=0.02):
    """
    Cross-sectional z-score with optional winsorization.
    Returns dict code -> float z-score (NaN inputs → NaN outputs)
    """
    # Separate valid and NaN
    valid = {c: v for c, v in values_dict.items() if not math.isnan(v)}
    if len(valid) < 3:
        return {c: float('nan') for c in values_dict}

    codes = list(valid.keys())
    vals = [valid[c] for c in codes]

    # Winsorize
    if winsorize_pct > 0:
        sorted_vals = sorted(vals)
        n = len(sorted_vals)
        lo_idx = max(0, int(n * winsorize_pct))
        hi_idx = min(n - 1, int(n * (1 - winsorize_pct)))
        lo, hi = sorted_vals[lo_idx], sorted_vals[hi_idx]
        vals = [max(lo, min(hi, v)) for v in vals]

    n = len(vals)
    mu = sum(vals) / n
    var = sum((v - mu) ** 2 for v in vals) / n
    sigma = var ** 0.5 if var > 0 else 1.0

    z_map = {c: (valid[c] - mu) / sigma for c in codes}
    # NaN codes get NaN z-score
    result = {}
    for c in values_dict:
        result[c] = z_map.get(c, float('nan'))
    return result


def compute_composite_z(factors_by_code, weights):
    """
    Given {code: {factor_name: value}} for one date, compute composite z-score.
    Weights: (w_rev, w_to, w_oi, w_vol, w_max)
    """
    fnames = ['reversal', 'turnover', 'oi', 'vol', 'max']
    # Cross-sectional z-score each factor
    zs = {}
    for fn in fnames:
        raw = {c: factors_by_code.get(c, {}).get(fn, float('nan')) for c in factors_by_code}
        zs[fn] = cross_sectional_z(raw)

    # Composite
    composite = {}
    for c in factors_by_code:
        score = 0.0; valid_weight = 0.0
        for fi, fn in enumerate(fnames):
            z = zs[fn].get(c, float('nan'))
            if not math.isnan(z):
                score += z * weights[fi]
                valid_weight += weights[fi]
        if valid_weight > 0:
            composite[c] = score / valid_weight  # renormalize
        else:
            composite[c] = float('nan')
    return composite


def backtest(stocks, all_factors, weights, trail, rebal_days, max_same_sector, sector_map):
    """
    Multi-factor composite backtest with sector diversification.
    """
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
            # Collect raw factor values for this date
            factors_by_code = {}
            for c in codes:
                fvals = {}
                for fn in ['reversal', 'turnover', 'oi', 'vol', 'max']:
                    fvals[fn] = all_factors[fn].get(c, {}).get(dt, float('nan'))
                # Only include if all 5 factors are valid
                if all(not math.isnan(fvals[fn]) for fn in fvals):
                    factors_by_code[c] = fvals

            # Compute composite z-scores
            if len(factors_by_code) >= MAX_POS:
                composite = compute_composite_z(factors_by_code, weights)

                # Build candidate list with sector constraint
                cand = [(c, composite[c]) for c in composite if not math.isnan(composite[c])]
                cand.sort(key=lambda x: x[1], reverse=True)

                # Sector-constrained selection
                selected = []; sec_counts = {}
                for c, sc in cand:
                    if c in positions:  # keep existing
                        sec = sector_map.get(c, '')
                        sec_counts[sec] = sec_counts.get(sec, 0) + 1
                        selected.append(c)
                        continue
                    sec = sector_map.get(c, '')
                    cnt = sec_counts.get(sec, 0)
                    if cnt >= max_same_sector: continue
                    # Non-majority constraint: only 1 sector can have >=2
                    test_counts = dict(sec_counts)
                    test_counts[sec] = test_counts.get(sec, 0) + 1
                    majority = [s for s, cc in test_counts.items() if cc >= 2]
                    if len(majority) > 1: continue
                    if len(selected) >= MAX_POS: break
                    selected.append(c)
                    sec_counts[sec] = test_counts[sec]

                top_codes = set(selected[:MAX_POS])

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

                # Buy new top stocks (equal weight)
                to_buy = [c for c in top_codes if c not in positions]
                if to_buy:
                    total_slots = len([c for c in top_codes])  # including existing
                    total_eq = cash + sum(
                        p['shares'] * date_maps[c].get(dt, {}).get('close', 0)
                        for c, p in positions.items() if date_maps[c].get(dt)
                    )
                    per_pos = total_eq / max(total_slots, 1)

                    for code in to_buy:
                        bar = date_maps[code].get(dt)
                        if not bar: continue
                        buy_px = bar['close'] * (1 + SLIP + COMM)
                        invest = min(per_pos, cash)
                        shares = invest / buy_px if buy_px > 0 else 0
                        if shares > 0 and invest > 0 and invest <= cash:
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
        'avg_pos': sum(d['n_pos'] for d in dvs) / len(dvs) if dvs else 0,
    }


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print('=' * 100)
    print('  Strategy 4: Multi-Factor Alpha Composite (多因子Alpha复合)')
    print(f'  Grid: {len(WEIGHT_SETS)} weight_sets × {len(TRAILS)} trails × '
          f'{len(REBAL_DAYS)} rebal × {len(MAX_SAME_SECTOR)} sector = {TOTAL} combos')
    print('  Factors: Reversal(25%) + Turnover(25%) + OI(20%) + LowVol(15%) + MAX(15%)')
    print('  Enhancement: sector diversification + winsorization')
    print('=' * 100)

    print('\n[DATA] Loading stocks...')
    stocks = load_stocks()
    print(f'  {len(stocks)} stocks loaded')

    print('[DATA] Loading sectors...')
    sector_map = load_sectors(stocks)
    unique_sectors = set(sector_map.values()) - {''}
    print(f'  {len(unique_sectors)} unique sectors found')

    print('[FACTORS] Computing all 5 factors...')
    all_factors = compute_all_factors(stocks)
    for fn in ['reversal', 'turnover', 'oi', 'vol', 'max']:
        nv = sum(len(vals) for vals in all_factors[fn].values())
        print(f'  {fn:<12s}: {nv} date-points')
    print('  Factors cached.')

    results = []; count = 0; best_sh = -999

    print(f'\n[GRID] Running {TOTAL} combos...')
    for weights in WEIGHT_SETS:
        w_label = f'R{weights[0]:.0%}T{weights[1]:.0%}O{weights[2]:.0%}V{weights[3]:.0%}M{weights[4]:.0%}'
        for trail in TRAILS:
            for rb in REBAL_DAYS:
                for mss in MAX_SAME_SECTOR:
                    count += 1
                    label = f'{w_label} T={trail:.0%} Rebal={rb}d Sec<={mss}'
                    r = backtest(stocks, all_factors, weights, trail, rb, mss, sector_map)
                    r.update({
                        'weights': weights, 'w_label': w_label,
                        'trail': trail, 'rebal': rb, 'max_sector': mss, 'label': label,
                    })
                    results.append(r)
                    if r['sh'] > best_sh: best_sh = r['sh']
                    if count % 30 == 1 or count == TOTAL:
                        print(f'  [{count:>5d}/{TOTAL}] {label:<45s} '
                              f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% '
                              f'DD={r["mdd"]*100:>5.2f}% Trd={r["np"]:>4d} '
                              f'Win={r["wr"]*100:>4.0f}% Best={best_sh:.4f}')

    results.sort(key=lambda x: x['sh'], reverse=True)

    print('\n\n' + '=' * 130)
    print('  TOP 30 BY SHARPE')
    print('=' * 130)
    hdr = (f'  {"Rk":<3s} {"Weights":<22s} {"Trail":>6s} {"Rebal":>6s} {"Sec":>4s} '
           f'{"S":>7s} {"Ret":>9s} {"Ann":>7s} {"DD":>6s} {"Calmar":>7s} '
           f'{"Trd":>4s} {"Win":>5s} {"Pos":>5s}')
    print(hdr); print(f'  {"-"*100}')
    for rank, r in enumerate(results[:30], 1):
        print(f'  {rank:<3d} {r["w_label"]:<22s} {r["trail"]:>5.0%}  {r["rebal"]:>3d}d  '
              f'{r["max_sector"]:>4d}  {r["sh"]:>7.3f} {r["tr"]*100:>8.2f}% '
              f'{r["ar"]*100:>6.2f}% {r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} '
              f'{r["np"]:>4d} {r["wr"]*100:>4.0f}% {r["avg_pos"]:>4.1f}')

    # Weight set comparison
    print('\n\n  WEIGHT SET COMPARISON:')
    for weights in WEIGHT_SETS:
        w_label = f'R{weights[0]:.0%}T{weights[1]:.0%}O{weights[2]:.0%}V{weights[3]:.0%}M{weights[4]:.0%}'
        subset = [r for r in results if r['w_label'] == w_label]
        if subset:
            best_w = max(subset, key=lambda x: x['sh'])
            avg_s = sum(r['sh'] for r in subset) / len(subset)
            print(f'    {w_label:<22s} best S={best_w["sh"]:.4f} avg S={avg_s:.4f} '
                  f'(best: T={best_w["trail"]:.0%} Rebal={best_w["rebal"]}d Sec<={best_w["max_sector"]})')

    # Parameter sensitivity
    print('\n\n  PARAMETER SENSITIVITY (avg Sharpe):')
    for pname, pkey, fmt in [
        ('Trail Stop', 'trail', '%'),
        ('Rebalance Days', 'rebal', 'd'),
        ('Max Same Sector', 'max_sector', 'd'),
    ]:
        levels = defaultdict(list)
        for r in results: levels[r[pkey]].append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(levels.keys()):
            avg = sum(levels[v]) / len(levels[v])
            bar = '#' * max(int(avg * 40), 1) if avg > 0 else '·' * max(int(abs(avg) * 40), 1)
            if fmt == '%': lbl = f'{v:.0%}'
            elif fmt == 'd': lbl = f'{v}d' if v > 1 else f'{v}'
            else: lbl = str(v)
            print(f'    {lbl:>8s}  avg S={avg:>7.3f} n={len(levels[v]):>4d}  {bar}')

    # BEST DETAIL
    best = results[0]
    print(f'\n\n  {"="*80}')
    print(f'  BEST CONFIG: {best["label"]}')
    print(f'  Sharpe={best["sh"]:.4f}  Ret={best["tr"]*100:.2f}%  Ann={best["ar"]*100:.2f}%  '
          f'DD={best["mdd"]*100:.2f}%  Calmar={best["cm"]:.3f}')
    print(f'  Trades={best["np"]}  WinRate={best["wr"]*100:.0f}%  '
          f'AvgPos={best["avg_pos"]:.1f}')
    print(f'  Weights: Rev={best["weights"][0]:.0%} TO={best["weights"][1]:.0%} '
          f'OI={best["weights"][2]:.0%} Vol={best["weights"][3]:.0%} '
          f'MAX={best["weights"][4]:.0%}')

    # Exit breakdown
    exits = defaultdict(lambda: {'cnt': 0, 'ret_sum': 0.0})
    for t in best['trades']:
        exits[t['exit']]['cnt'] += 1; exits[t['exit']]['ret_sum'] += t['ret']
    print(f'\n  Exit breakdown:')
    for e in ['trail', 'rebalance', 'final']:
        d = exits.get(e)
        if d and d['cnt'] > 0:
            print(f'    {e:<12s} cnt={d["cnt"]:>4d} avg_ret={d["ret_sum"]/d["cnt"]*100:>+7.2f}%')

    # Sector performance
    sec_perf = defaultdict(lambda: {'cnt': 0, 'ret_sum': 0.0})
    for t in best['trades']:
        sec = sector_map.get(t['code'], '?')
        sec_perf[sec]['cnt'] += 1; sec_perf[sec]['ret_sum'] += t['ret']
    print(f'\n  Sector Performance (>=3 trades):')
    for sec in sorted(sec_perf, key=lambda x: sec_perf[x]['ret_sum']/max(sec_perf[x]['cnt'],1), reverse=True):
        d = sec_perf[sec]
        if d['cnt'] >= 3:
            print(f'    {sec:<25s} cnt={d["cnt"]:>4d} avg_ret={d["ret_sum"]/d["cnt"]*100:>+7.2f}%')

    # Top/bottom trades
    best['trades'].sort(key=lambda x: x['ret'], reverse=True)
    for tag, subset in [('Best 5', best['trades'][:5]), ('Worst 5', best['trades'][-5:])]:
        print(f'\n  {tag}:')
        for t in subset:
            print(f'    {t["code"]} {stocks[t["code"]]["name"]:<10s} '
                  f'{t["buy_d"]} -> {t["sell_d"]} {t["ret"]*100:>+7.2f}% {t["exit"]}')

    # Annual returns
    with open('strategy_multifactor_equity.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['date', 'equity', 'positions'])
        for d in best['dvs']: w.writerow([d['date'], f'{d["value"]:.2f}', d['n_pos']])
    print(f'\n  Exported: strategy_multifactor_equity.csv')

    yr = defaultdict(lambda: {'s': None, 'e': None})
    for d in best['dvs']:
        yk = d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s'] = d['value']
        yr[yk]['e'] = d['value']
    print('\n  Annual Returns:')
    for y in sorted(yr.keys()):
        ret = (yr[y]['e'] - yr[y]['s']) / yr[y]['s'] * 100 if yr[y]['s'] and yr[y]['s'] > 0 else 0
        print(f'    {y}: {ret:+.1f}%')

    # Cross-strategy comparison table (placeholder - fill in after other strategies run)
    print(f'\n  {"="*80}')
    print(f'  STRATEGY 4 COMPLETE')
    print(f'  Best Sharpe: {best["sh"]:.4f}')
    print(f'  Best Return: {best["tr"]*100:.2f}%')
    print(f'  Best Max DD: {best["mdd"]*100:.2f}%')
    print(f'  Expected ICIR: 1.0-1.8 (theoretical, from research memo)')
    print(f'  {"="*80}')

    print('\n  Done!')
    return best


if __name__ == '__main__':
    main()
