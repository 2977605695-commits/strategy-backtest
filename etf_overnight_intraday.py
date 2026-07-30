"""
ETF Strategy 3: Overnight-Intraday Decomposition (隔夜-日内分解)
=================================================================
将A股个股层面验证的隔夜-日内分解因子应用于ETF。ETF同样受T+1约束。
假设：ETF的开盘跳空由机构隔夜配置驱动（动量），日内波动由散户情绪驱动（反转）。

因子构造 (同个股策略):
  Overnight_Nd  = avg(open[t-i]/close[t-i-1] - 1, for i=0..N-1)
  Intraday_Nd   = avg(close[t-i]/open[t-i] - 1, for i=0..N-1)
  Score = on_avg - id_avg  (overnight momentum + intraday reversal)

网格搜索:
  Lookback: 5d, 10d, 14d
  Trail: 5%, 8%, 10%, 15%
  Rebal: 5d, 10d, 21d
  Volume filter: on/off
  Max pos: 2, 3

参考:
  - 东吴证券《日与夜之殊途同归》
  - 个股策略 strategy_overnight_intraday.py (Sharpe 1.21)
"""
import sys, io, json, math, os, csv
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
RF = 0.025; TD = 252; INIT = 10_000_000
SLIP = 0.001; COMM = 0.00005; STAMP = 0.0

LOOKBACKS = [5, 10, 14]
TRAILS = [0.05, 0.08, 0.10, 0.15]
REBAL_DAYS = [5, 10, 21]
VOL_FILTER = [False, True]
MAX_POS = [2, 3]
TOTAL = len(LOOKBACKS) * len(TRAILS) * len(REBAL_DAYS) * len(VOL_FILTER) * len(MAX_POS)


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


def compute_oi_factors(etfs, lookback, vol_filter):
    factors = {}
    for code, info in etfs.items():
        bars = info['bars']
        n = len(bars)
        closes = [b['close'] for b in bars]
        opens = [b['open'] for b in bars]
        volumes = [b['volume'] for b in bars]

        on_ret = [float('nan')] * n
        id_ret = [float('nan')] * n
        for i in range(1, n):
            if closes[i-1] > 0:
                on_ret[i] = opens[i] / closes[i-1] - 1
            if opens[i] > 0:
                id_ret[i] = closes[i] / opens[i] - 1

        ma_vol20 = calc_ma(volumes, 20)
        vals = {}
        min_idx = max(lookback, 60)
        for i in range(min_idx, n):
            dt = bars[i]['date']
            on_vals = []; id_vals = []
            for j in range(lookback):
                idx = i - 1 - j
                if idx >= 0:
                    if not math.isnan(on_ret[idx]): on_vals.append(on_ret[idx])
                    if not math.isnan(id_ret[idx]): id_vals.append(id_ret[idx])
            if len(on_vals) < max(lookback//2, 2) or len(id_vals) < max(lookback//2, 2):
                vals[dt] = float('nan')
                continue
            on_avg = sum(on_vals)/len(on_vals)
            id_avg = sum(id_vals)/len(id_vals)

            if vol_filter and not math.isnan(ma_vol20[i-1]) and ma_vol20[i-1] > 0:
                vol_ratio = min(2.0, max(0.5, volumes[i-1]/ma_vol20[i-1]))
            else:
                vol_ratio = 1.0

            vals[dt] = (on_avg - id_avg) * vol_ratio
        factors[code] = vals
    return factors


def backtest(etfs, factors, trail, rebal_days, max_pos):
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

        if di % rebal_days == 0:
            cand = []
            for c in available:
                if c in positions: continue
                fac_val = factors.get(c, {}).get(dt, float('nan'))
                if math.isnan(fac_val): continue
                cand.append((c, fac_val))
            cand.sort(key=lambda x: x[1], reverse=True)
            top_codes = set(c for c, _ in cand[:max_pos])

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

            to_buy = [c for c in top_codes if c not in positions]
            if to_buy:
                n_target = len(top_codes)
                total_eq = cash + sum(
                    p['shares'] * date_maps[c].get(dt, {}).get('close', 0)
                    for c, p in positions.items() if date_maps[c].get(dt)
                )
                per_pos = total_eq / max(n_target, 1)
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

        pos_val = sum(
            p['shares'] * date_maps[c].get(dt, {}).get('close', 0) * (1 - SLIP - COMM)
            for c, p in positions.items() if date_maps[c].get(dt)
        )
        dvs.append({'date': dt, 'value': cash + pos_val, 'n_pos': len(positions)})

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
    print('  ETF Strategy 3: Overnight-Intraday Decomposition (隔夜-日内分解)')
    print(f'  Grid: {len(LOOKBACKS)} windows × {len(TRAILS)} trails × '
          f'{len(REBAL_DAYS)} rebal × {len(VOL_FILTER)} vf × {len(MAX_POS)} maxpos = {TOTAL} combos')
    print('  Score = avg(overnight_ret) - avg(intraday_ret)')
    print('=' * 100)

    print('\n[DATA] Loading ETFs...')
    etfs = load_etfs()
    print(f'  {len(etfs)} ETFs loaded')

    factor_cache = {}
    for lb in LOOKBACKS:
        for vf in VOL_FILTER:
            key = (lb, vf)
            print(f'  Computing OI factors: lb={lb}d vol_filter={vf}...')
            factor_cache[key] = compute_oi_factors(etfs, lb, vf)
    print(f'  {len(factor_cache)} factor variants cached.')

    results = []; count = 0; best_sh = -999
    print(f'\n[GRID] Running {TOTAL} combos...')

    for lb in LOOKBACKS:
        for vf in VOL_FILTER:
            fac = factor_cache[(lb, vf)]
            for trail in TRAILS:
                for rb in REBAL_DAYS:
                    for mp in MAX_POS:
                        count += 1
                        label = f'LB={lb:>2d}d VF={vf} T={trail:.0%} Rebal={rb}d Max{mp}'
                        r = backtest(etfs, fac, trail, rb, mp)
                        r.update({
                            'lb': lb, 'vol_filter': vf, 'trail': trail,
                            'rebal': rb, 'max_pos': mp, 'label': label,
                        })
                        results.append(r)
                        if r['sh'] > best_sh: best_sh = r['sh']
                        if count % 40 == 1 or count == TOTAL:
                            print(f'  [{count:>4d}/{TOTAL}] {label:<35s} '
                                  f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% '
                                  f'DD={r["mdd"]*100:>5.2f}% Trd={r["np"]:>4d} '
                                  f'Best={best_sh:.4f}')

    results.sort(key=lambda x: x['sh'], reverse=True)

    print('\n\n' + '=' * 120)
    print('  TOP 30 BY SHARPE')
    print('=' * 120)
    hdr = (f'  {"Rk":<3s} {"LB":>4s} {"VF":>4s} {"Trail":>6s} {"Reb":>5s} {"MaxP":>5s} '
           f'{"S":>7s} {"Ret":>9s} {"Ann":>7s} {"DD":>6s} {"Calmar":>7s} '
           f'{"Trd":>4s} {"Win":>5s}')
    print(hdr); print(f'  {"-"*90}')
    for rank, r in enumerate(results[:30], 1):
        print(f'  {rank:<3d} {r["lb"]:>4d} {"Y" if r["vol_filter"] else "N":>4s}  '
              f'{r["trail"]:>5.0%}  {r["rebal"]:>3d}d  {r["max_pos"]:>4d}  '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>8.2f}% {r["ar"]*100:>6.2f}% '
              f'{r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} {r["np"]:>4d} {r["wr"]*100:>4.0f}%')

    print('\n\n  PARAMETER SENSITIVITY (avg Sharpe):')
    for pname, pkey, fmt in [
        ('Lookback', 'lb', 'd'),
        ('Trail', 'trail', '%'),
        ('Rebalance', 'rebal', 'd'),
        ('Volume Filter', 'vol_filter', ''),
        ('Max Positions', 'max_pos', 'd'),
    ]:
        levels = defaultdict(list)
        for r in results: levels[r[pkey]].append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(levels.keys()):
            avg = sum(levels[v]) / len(levels[v])
            bar = '#' * max(int(avg * 40), 1) if avg > 0 else '·' * max(int(abs(avg) * 40), 1)
            if fmt == '%': lbl = f'{v:.0%}'
            elif fmt == 'd': lbl = f'{v}d' if v > 1 else str(v)
            else: lbl = 'Yes' if v else 'No'
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

    with open('etf_overnight_intraday_equity.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['date', 'equity', 'positions'])
        for d in best['dvs']: w.writerow([d['date'], f'{d["value"]:.2f}', d['n_pos']])
    print(f'\n  Exported: etf_overnight_intraday_equity.csv')

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
