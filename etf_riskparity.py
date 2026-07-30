"""
ETF Strategy 2: Risk Parity + Trend Filter (风险平价+趋势过滤)
===============================================================
利用ETF间的低相关性（黄金 vs 科创/科技）构建波动率加权组合。
核心逻辑：持仓权重 ∝ 1/波动率 + MA趋势过滤 + 月度再平衡。

增强：
  - 趋势过滤：仅持有MA20以上（或MA60以上）的ETF
  - 相关性惩罚：高相关ETF降权
  - 黄金(518800)作为天然对冲，稳定组合波动

网格搜索:
  Trend MA: 20, 60
  Risk window: 20d, 60d
  Max ETFs: 3, 5, 7
  Trail: 5%, 8%, 10%, 15%
  Rebalance: 10d, 21d

参考:
  - Qian (2005). "Risk Parity Portfolios"
  - Asness, Frazzini & Pedersen (2012). "Leverage Aversion and Risk Parity", FAJ
"""
import sys, io, json, math, os, csv
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
RF = 0.025; TD = 252; INIT = 10_000_000
SLIP = 0.001; COMM = 0.00005; STAMP = 0.0

TREND_MAS = [20, 60]
RISK_WINDOWS = [20, 60]
MAX_ETFS = [3, 5, 7]
TRAILS = [0.05, 0.08, 0.10, 0.15]
REBAL_DAYS = [10, 21]
TOTAL = len(TREND_MAS) * len(RISK_WINDOWS) * len(MAX_ETFS) * len(TRAILS) * len(REBAL_DAYS)


def load_etfs():
    etfs = {}
    for fn in sorted(os.listdir(DATA_DIR)):
        if not fn.startswith('etf_') or not fn.endswith('.json'): continue
        d = json.load(open(os.path.join(DATA_DIR, fn), encoding='utf-8'))
        bars = []
        for b in d['bars']:
            dt = b['date']
            if len(dt) == 8: dt = f'{dt[:4]}-{dt[4:6]}-{dt[6:8]}'
            bars.append({
                'date': dt, 'close': float(b['close']), 'open': float(b['open']),
                'high': float(b['high']), 'low': float(b['low']),
                'volume': float(b['volume']),
            })
        etfs[d['code']] = {'name': d['name'], 'bars': bars, 'first_date': bars[0]['date']}
    return etfs


def calc_ma(data, w):
    ma = []; n = len(data)
    for i in range(n):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma


def compute_risk_data(etfs, risk_window):
    """
    For each ETF/date, compute:
      - volatility (annualized)
      - inverse_vol (weight in risk parity)
      - log returns
    """
    rp_data = {}
    for code, info in etfs.items():
        bars = info['bars']
        closes = [b['close'] for b in bars]
        n = len(bars)

        log_rets = [float('nan')]
        for i in range(1, n):
            if closes[i-1] > 0:
                log_rets.append(math.log(closes[i] / closes[i-1]))
            else:
                log_rets.append(float('nan'))

        vals = {}
        for i in range(risk_window + 60, n):
            dt = bars[i]['date']
            # Rolling volatility from log returns
            lr_window = [lr for lr in log_rets[i-risk_window+1:i+1] if not math.isnan(lr)]
            if len(lr_window) >= max(risk_window // 2, 5):
                mu_lr = sum(lr_window) / len(lr_window)
                var_lr = sum((lr - mu_lr)**2 for lr in lr_window) / len(lr_window)
                vol_daily = var_lr ** 0.5
                vol_annual = vol_daily * math.sqrt(TD)
                # Inverse vol weight (raw, will be normalized cross-sectionally)
                inv_vol = 1.0 / vol_annual if vol_annual > 0 else 0
            else:
                vol_annual = float('nan')
                inv_vol = float('nan')
            vals[dt] = {'vol': vol_annual, 'inv_vol': inv_vol}
        rp_data[code] = vals
    return rp_data


def compute_trend_signals(etfs, trend_ma):
    """For each ETF/date, flag whether close > MA(trend_ma)."""
    trends = {}
    for code, info in etfs.items():
        bars = info['bars']
        closes = [b['close'] for b in bars]
        ma = calc_ma(closes, trend_ma)
        vals = {}
        for i in range(trend_ma, len(bars)):
            dt = bars[i]['date']
            if not math.isnan(ma[i]) and closes[i] > 0:
                vals[dt] = closes[i] > ma[i]
            else:
                vals[dt] = False
        trends[code] = vals
    return trends


def backtest(etfs, rp_data, trend_signals, max_n, trail, rebal_days):
    codes = sorted(etfs.keys())
    date_maps = {c: {b['date']: b for b in etfs[c]['bars']} for c in codes}
    all_dates = sorted(set.union(*[set(m.keys()) for m in date_maps.values()]))
    first_dates = {c: etfs[c]['first_date'] for c in codes}

    positions = {}; cash = INIT; trades = []; dvs = []

    for di, dt in enumerate(all_dates):
        available = [c for c in codes if first_dates[c] <= dt]

        # Trail stops
        for code in list(positions.keys()):
            bar = date_maps[code].get(dt)
            if not bar or dt == positions[code].get('entry_date'): continue
            px = bar['close']
            if px > positions[code]['peak']: positions[code]['peak'] = px
            if px <= positions[code]['peak'] * (1 - trail):
                sell_px = px * (1 - SLIP - COMM)
                ret = (sell_px - positions[code]['buy_px']) / positions[code]['buy_px']
                trades.append({
                    'code': code, 'buy_d': positions[code]['entry_date'], 'sell_d': dt,
                    'ret': ret, 'exit': 'trail',
                })
                cash += positions[code]['shares'] * sell_px
                del positions[code]

        # Rebalance
        if di % rebal_days == 0:
            # Collect eligible ETFs: trend_up + valid risk data
            eligible = {}
            for c in available:
                if c in positions: continue
                rp = rp_data.get(c, {}).get(dt)
                trend_up = trend_signals.get(c, {}).get(dt, False)
                if rp and not math.isnan(rp.get('inv_vol', float('nan'))) and trend_up:
                    eligible[c] = rp['inv_vol']

            if not eligible:
                # All trend-down: go to cash. Sell everything.
                for code in list(positions.keys()):
                    if dt != positions[code].get('entry_date'):
                        bar = date_maps[code].get(dt)
                        if not bar: continue
                        sell_px = bar['close'] * (1 - SLIP - COMM)
                        ret = (sell_px - positions[code]['buy_px']) / positions[code]['buy_px']
                        trades.append({
                            'code': code, 'buy_d': positions[code]['entry_date'], 'sell_d': dt,
                            'ret': ret, 'exit': 'trend_break',
                        })
                        cash += positions[code]['shares'] * sell_px
                        del positions[code]
                dvs.append({'date': dt, 'value': cash, 'n_pos': 0})
                continue

            # Risk parity: weight = inv_vol / sum(inv_vol)
            total_inv_vol = sum(eligible.values())
            weights = {c: inv_v / total_inv_vol for c, inv_v in eligible.items()}

            # Select top max_n by weight
            selected = sorted(weights.items(), key=lambda x: x[1], reverse=True)[:max_n]
            selected_codes = set(c for c, _ in selected)

            # Normalize selected weights
            sel_total = sum(w for _, w in selected)
            sel_weights = {c: w / sel_total for c, w in selected}

            # Sell non-selected
            for code in list(positions.keys()):
                if code not in selected_codes and dt != positions[code].get('entry_date'):
                    bar = date_maps[code].get(dt)
                    if not bar: continue
                    sell_px = bar['close'] * (1 - SLIP - COMM)
                    ret = (sell_px - positions[code]['buy_px']) / positions[code]['buy_px']
                    trades.append({
                        'code': code, 'buy_d': positions[code]['entry_date'], 'sell_d': dt,
                        'ret': ret, 'exit': 'rebalance',
                    })
                    cash += positions[code]['shares'] * sell_px
                    del positions[code]

            # Buy/adjust to target weights
            total_eq = cash + sum(
                p['shares'] * date_maps[c].get(dt, {}).get('close', 0)
                for c, p in positions.items() if date_maps[c].get(dt)
            )

            for code, weight in sel_weights.items():
                bar = date_maps[code].get(dt)
                if not bar: continue
                target_val = total_eq * weight

                if code in positions:
                    curr_val = positions[code]['shares'] * bar['close']
                    diff = target_val - curr_val
                    if abs(diff) < target_val * 0.1: continue  # skip small adjustments
                    # Simple: sell and rebuy (ETFs have tight spreads)
                    sell_px = bar['close'] * (1 - SLIP - COMM)
                    cash += positions[code]['shares'] * sell_px
                    del positions[code]

                # Buy at target
                buy_px = bar['close'] * (1 + SLIP + COMM)
                invest = min(target_val, cash)
                shares = invest / buy_px if buy_px > 0 else 0
                if shares > 0 and invest > 0 and invest <= cash:
                    positions[code] = {
                        'shares': shares, 'buy_px': buy_px,
                        'peak': bar['close'], 'entry_date': dt,
                    }
                    cash -= shares * buy_px

        # Mark-to-market
        pos_val = sum(
            p['shares'] * date_maps[c].get(dt, {}).get('close', 0) * (1 - SLIP - COMM)
            for c, p in positions.items() if date_maps[c].get(dt)
        )
        dvs.append({'date': dt, 'value': cash + pos_val, 'n_pos': len(positions)})

    # Final liquidation
    last_dt = all_dates[-1]
    for code in list(positions.keys()):
        bar = date_maps[code].get(last_dt)
        if bar:
            sell_px = bar['close'] * (1 - SLIP - COMM)
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
    print('  ETF Strategy 2: Risk Parity + Trend Filter (风险平价+趋势过滤)')
    print(f'  Grid: {len(TREND_MAS)} trend_MAs × {len(RISK_WINDOWS)} risk_wins × '
          f'{len(MAX_ETFS)} maxN × {len(TRAILS)} trails × {len(REBAL_DAYS)} rebal = {TOTAL} combos')
    print('  Method: weight ∝ 1/volatility | trend filter | cash when all trend-down')
    print('=' * 100)

    print('\n[DATA] Loading ETFs...')
    etfs = load_etfs()
    print(f'  {len(etfs)} ETFs loaded')

    # Pre-compute risk data for each window
    risk_cache = {}
    for rw in RISK_WINDOWS:
        print(f'  Computing risk data: window={rw}d...')
        risk_cache[rw] = compute_risk_data(etfs, rw)

    # Pre-compute trend signals
    trend_cache = {}
    for tma in TREND_MAS:
        print(f'  Computing trend signals: MA={tma}...')
        trend_cache[tma] = compute_trend_signals(etfs, tma)
    print('  All signals cached.')

    results = []; count = 0; best_sh = -999
    print(f'\n[GRID] Running {TOTAL} combos...')

    for tma in TREND_MAS:
        trends = trend_cache[tma]
        for rw in RISK_WINDOWS:
            rp_data = risk_cache[rw]
            for max_n in MAX_ETFS:
                for trail in TRAILS:
                    for rb in REBAL_DAYS:
                        count += 1
                        label = f'MA{tma:>2d} Risk{rw:>2d}d Max{max_n} T={trail:.0%} Reb{rb}d'
                        r = backtest(etfs, rp_data, trends, max_n, trail, rb)
                        r.update({
                            'trend_ma': tma, 'risk_win': rw, 'max_n': max_n,
                            'trail': trail, 'rebal': rb, 'label': label,
                        })
                        results.append(r)
                        if r['sh'] > best_sh: best_sh = r['sh']
                        if count % 30 == 1 or count == TOTAL:
                            print(f'  [{count:>4d}/{TOTAL}] {label:<38s} '
                                  f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% '
                                  f'DD={r["mdd"]*100:>5.2f}% Trd={r["np"]:>4d} '
                                  f'Best={best_sh:.4f}')

    results.sort(key=lambda x: x['sh'], reverse=True)

    print('\n\n' + '=' * 120)
    print('  TOP 30 BY SHARPE')
    print('=' * 120)
    hdr = (f'  {"Rk":<3s} {"Trend":>6s} {"Risk":>6s} {"MaxN":>5s} {"Trail":>6s} {"Reb":>5s} '
           f'{"S":>7s} {"Ret":>9s} {"Ann":>7s} {"DD":>6s} {"Calmar":>7s} '
           f'{"Trd":>4s} {"Win":>5s}')
    print(hdr); print(f'  {"-"*90}')
    for rank, r in enumerate(results[:30], 1):
        print(f'  {rank:<3d} MA{r["trend_ma"]:>3d}  Risk{r["risk_win"]:>3d}  '
              f'Top{r["max_n"]:>3d}  {r["trail"]:>5.0%}  {r["rebal"]:>3d}d  '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>8.2f}% {r["ar"]*100:>6.2f}% '
              f'{r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} {r["np"]:>4d} {r["wr"]*100:>4.0f}%')

    # Parameter sensitivity
    print('\n\n  PARAMETER SENSITIVITY (avg Sharpe):')
    for pname, pkey, fmt in [
        ('Trend MA', 'trend_ma', 'd'),
        ('Risk Window', 'risk_win', 'd'),
        ('Max ETFs', 'max_n', 'd'),
        ('Trail Stop', 'trail', '%'),
        ('Rebalance', 'rebal', 'd'),
    ]:
        levels = defaultdict(list)
        for r in results: levels[r[pkey]].append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(levels.keys()):
            avg = sum(levels[v]) / len(levels[v])
            bar = '#' * max(int(avg * 40), 1) if avg > 0 else '·' * max(int(abs(avg) * 40), 1)
            if fmt == '%': lbl = f'{v:.0%}'
            else: lbl = f'{v}d' if v > 1 else str(v)
            print(f'    {lbl:>8s}  avg S={avg:>7.3f} n={len(levels[v]):>4d}  {bar}')

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
    for e in sorted(exits.keys()):
        d = exits[e]
        if d['cnt'] > 0:
            print(f'    {e:<15s} cnt={d["cnt"]:>4d} avg_ret={d["ret_sum"]/d["cnt"]*100:>+7.2f}%')

    best['trades'].sort(key=lambda x: x['ret'], reverse=True)
    for tag, subset in [('Best 5', best['trades'][:5]), ('Worst 5', best['trades'][-5:])]:
        print(f'\n  {tag}:')
        for t in subset:
            print(f'    {t["code"]} {etfs[t["code"]]["name"]:<25s} '
                  f'{t["buy_d"]} -> {t["sell_d"]} {t["ret"]*100:>+7.2f}% {t["exit"]}')

    etf_perf = defaultdict(lambda: {'cnt': 0, 'ret_sum': 0.0})
    for t in best['trades']:
        etf_perf[t['code']]['cnt'] += 1; etf_perf[t['code']]['ret_sum'] += t['ret']
    print(f'\n  ETF Performance:')
    for code in sorted(etf_perf, key=lambda x: etf_perf[x]['ret_sum']/max(etf_perf[x]['cnt'],1), reverse=True):
        d = etf_perf[code]
        print(f'    {code} {etfs[code]["name"]:<25s} cnt={d["cnt"]:>4d} avg={d["ret_sum"]/d["cnt"]*100:>+7.2f}%')

    with open('etf_riskparity_equity.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['date', 'equity', 'positions'])
        for d in best['dvs']: w.writerow([d['date'], f'{d["value"]:.2f}', d['n_pos']])
    print(f'\n  Exported: etf_riskparity_equity.csv')

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
