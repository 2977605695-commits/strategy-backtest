"""
ETF Strategy 1: Momentum Rotation (动量轮动)
============================================
ETF动量是ETF领域最稳健的策略。与个股不同，ETF不存在T+1制度的反转效应
——ETF是组合，噪音已被分散，动量持续性强得多。

逻辑: Buy top-N ETFs by past M-day return, monthly/weekly rebalance
增强: Trail止损 + 空仓机制（所有ETF动量均<0时全仓现金）

网格搜索:
  Momentum windows: 21d, 42d, 63d, 126d
  Top N: 2, 3, 5
  Rebalance: 5d, 10d, 21d
  Trail: 5%, 8%, 10%, 15%, 20%

参考:
  - Jegadeesh & Titman (1993). "Returns to Buying Winners and Selling Losers"
  - Moskowitz, Ooi & Pedersen (2012). "Time Series Momentum", JFE
  - 华泰金工《ETF动量轮动策略》
"""
import sys, io, json, math, os, csv
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
RF = 0.025; TD = 252; INIT = 10_000_000
SLIP = 0.001; COMM = 0.00005; STAMP = 0.0  # ETFs: no stamp tax, very low commission

MOM_WINDOWS = [21, 42, 63, 126]
TOP_N = [2, 3, 5]
REBAL_DAYS = [5, 10, 21]
TRAILS = [0.05, 0.08, 0.10, 0.15, 0.20]
TOTAL = len(MOM_WINDOWS) * len(TOP_N) * len(REBAL_DAYS) * len(TRAILS)


def load_etfs():
    """Load all ETF JSON data. Returns dict code -> {name, bars}"""
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


def compute_momentum(etfs, window):
    """Compute past N-day momentum for each ETF at each date."""
    mom = {}
    for code, info in etfs.items():
        bars = info['bars']
        closes = [b['close'] for b in bars]
        vals = {}
        for i in range(window, len(bars)):
            dt = bars[i]['date']
            if closes[i-window] > 0:
                vals[dt] = (closes[i-1] / closes[i-window] - 1)
            else:
                vals[dt] = float('nan')
        mom[code] = vals
    return mom


def backtest(etfs, momentum, top_n, rebal_days, trail):
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
            # Rank by momentum
            cand = []
            for c in available:
                if c in positions: continue
                mom_val = momentum.get(c, {}).get(dt, float('nan'))
                if math.isnan(mom_val): continue
                # Only buy if momentum > 0 (absolute momentum filter)
                if mom_val > 0:
                    cand.append((c, mom_val))
            cand.sort(key=lambda x: x[1], reverse=True)

            top_codes = set(c for c, _ in cand[:top_n])

            # Sell non-top
            for code in list(positions.keys()):
                if code not in top_codes and dt != positions[code].get('entry_date'):
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

            # Buy top (equal weight)
            to_buy = [c for c in top_codes if c not in positions]
            n_target = len(top_codes)  # including existing
            if n_target == 0: continue
            total_eq = cash + sum(
                p['shares'] * date_maps[c].get(dt, {}).get('close', 0)
                for c, p in positions.items() if date_maps[c].get(dt)
            )
            per_pos = total_eq / n_target

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
    print('  ETF Strategy 1: Momentum Rotation (动量轮动)')
    print(f'  Grid: {len(MOM_WINDOWS)} windows × {len(TOP_N)} topN × '
          f'{len(REBAL_DAYS)} rebal × {len(TRAILS)} trails = {TOTAL} combos')
    print('  Filter: absolute momentum > 0 (all-negative → cash)')
    print('=' * 100)

    print('\n[DATA] Loading ETFs...')
    etfs = load_etfs()
    for code in sorted(etfs.keys()):
        info = etfs[code]
        print(f'  {code} {info["name"]:<25s} {info["first_date"]} -> {info["bars"][-1]["date"]} ({len(info["bars"])} bars)')
    print(f'  {len(etfs)} ETFs loaded')

    # Pre-compute momentum for each window
    mom_cache = {}
    for mw in MOM_WINDOWS:
        print(f'  Computing momentum: window={mw}d...')
        mom_cache[mw] = compute_momentum(etfs, mw)
    print('  Momentum cached.')

    results = []; count = 0; best_sh = -999

    print(f'\n[GRID] Running {TOTAL} combos...')
    for mw in MOM_WINDOWS:
        mom = mom_cache[mw]
        for top_n in TOP_N:
            for rb in REBAL_DAYS:
                for trail in TRAILS:
                    count += 1
                    label = f'Mom{mw:>3d}d Top{top_n} Rebal{rb:>2d}d T={trail:.0%}'
                    r = backtest(etfs, mom, top_n, rb, trail)
                    r.update({'mw': mw, 'top_n': top_n, 'rebal': rb, 'trail': trail, 'label': label})
                    results.append(r)
                    if r['sh'] > best_sh: best_sh = r['sh']
                    if count % 50 == 1 or count == TOTAL:
                        print(f'  [{count:>4d}/{TOTAL}] {label:<35s} '
                              f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% '
                              f'DD={r["mdd"]*100:>5.2f}% Trd={r["np"]:>4d} '
                              f'Best={best_sh:.4f}')

    results.sort(key=lambda x: x['sh'], reverse=True)

    print('\n\n' + '=' * 120)
    print('  TOP 30 BY SHARPE')
    print('=' * 120)
    hdr = (f'  {"Rk":<3s} {"Window":>7s} {"TopN":>5s} {"Rebal":>6s} {"Trail":>6s} '
           f'{"S":>7s} {"Ret":>9s} {"Ann":>7s} {"DD":>6s} {"Calmar":>7s} '
           f'{"Trd":>4s} {"Win":>5s}')
    print(hdr); print(f'  {"-"*90}')
    for rank, r in enumerate(results[:30], 1):
        print(f'  {rank:<3d} Mom{r["mw"]:>3d}d  Top{r["top_n"]:>3d}  '
              f'{r["rebal"]:>3d}d  {r["trail"]:>5.0%}  '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>8.2f}% {r["ar"]*100:>6.2f}% '
              f'{r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} {r["np"]:>4d} {r["wr"]*100:>4.0f}%')

    # Parameter sensitivity
    print('\n\n  PARAMETER SENSITIVITY (avg Sharpe):')
    for pname, pkey, fmt in [
        ('Momentum Window', 'mw', 'd'),
        ('Top N', 'top_n', 'd'),
        ('Rebalance Days', 'rebal', 'd'),
        ('Trail Stop', 'trail', '%'),
    ]:
        levels = defaultdict(list)
        for r in results: levels[r[pkey]].append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(levels.keys()):
            avg = sum(levels[v]) / len(levels[v])
            bar = '#' * max(int(avg * 40), 1) if avg > 0 else '·' * max(int(abs(avg) * 40), 1)
            if fmt == '%': lbl = f'{v:.0%}'
            elif fmt == 'd': lbl = f'{v}d' if v > 1 else str(v)
            else: lbl = str(v)
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
        if d and d['cnt'] > 0:
            print(f'    {e:<12s} cnt={d["cnt"]:>4d} avg_ret={d["ret_sum"]/d["cnt"]*100:>+7.2f}%')

    best['trades'].sort(key=lambda x: x['ret'], reverse=True)
    for tag, subset in [('Best 5', best['trades'][:5]), ('Worst 5', best['trades'][-5:])]:
        print(f'\n  {tag}:')
        for t in subset:
            print(f'    {t["code"]} {etfs[t["code"]]["name"]:<25s} '
                  f'{t["buy_d"]} -> {t["sell_d"]} {t["ret"]*100:>+7.2f}% {t["exit"]}')

    # ETF exposure
    etf_perf = defaultdict(lambda: {'cnt': 0, 'ret_sum': 0.0})
    for t in best['trades']:
        etf_perf[t['code']]['cnt'] += 1; etf_perf[t['code']]['ret_sum'] += t['ret']
    print(f'\n  ETF Performance:')
    for code in sorted(etf_perf, key=lambda x: etf_perf[x]['ret_sum']/max(etf_perf[x]['cnt'],1), reverse=True):
        d = etf_perf[code]
        print(f'    {code} {etfs[code]["name"]:<25s} cnt={d["cnt"]:>4d} avg={d["ret_sum"]/d["cnt"]*100:>+7.2f}%')

    with open('etf_momentum_equity.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['date', 'equity', 'positions'])
        for d in best['dvs']: w.writerow([d['date'], f'{d["value"]:.2f}', d['n_pos']])
    print(f'\n  Exported: etf_momentum_equity.csv')

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
