"""
择时2策略 · MA金叉轮动 + 赛道约束 · 63只网格搜索
===================================================
Buy: MA3 > MA7 + threshold (2/3/4%), pick top 5 by 乖离率
Sell: Trail (10~30%) + MA touch (MA7/10/14)
Constraint: sector no repeat, max 5 positions, equal weight
Rotation: sell -> immediately buy next best
T+1, slippage 0.3%, commission 0.025%, stamp 0.05%

Grid: 3 x 5 x 4 = 60 combos (cross_thr × trail × ma_sell)
"""

import sys, io, json, math, os, csv
from collections import defaultdict
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
FUND_DIR = os.path.join(DATA_DIR, 'fundamentals_70stocks')
START = '2024-01-01'; END = '2026-07-22'
RF = 0.025; TD = 252; INIT = 10_000_000
MAX_POS = 5
SLIPPAGE = 0.003; COMM = 0.00025; STAMP = 0.0005

CROSS_THRS = [0.02, 0.03, 0.04]
TRAILS = [0.10, 0.15, 0.20, 0.25, 0.30]
MA_SELLS = [7, 10, 14, None]  # None = trail only


def load_stocks_and_sectors():
    """Load bars + latest sector from fundamentals."""
    # Load sectors from latest CSV
    sectors = {}
    fs = sorted(os.listdir(FUND_DIR))
    latest = fs[-1]
    with open(os.path.join(FUND_DIR, latest), encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            sectors[row['code']] = row.get('sector', '?')

    # Load prices
    stocks = {}
    for f in sorted(os.listdir(DATA_DIR)):
        if not f.endswith('.json') or f.startswith('_'): continue
        d = json.load(open(os.path.join(DATA_DIR, f), encoding='utf-8'))
        if len(d['bars']) < 200: continue
        if d['first_date'] > '2024-06': continue
        code = d['code']
        if code not in sectors: sectors[code] = '?'
        bars = []
        for b in d['bars']:
            dt = b['date']
            if len(dt) == 8: dt = f'{dt[:4]}-{dt[4:6]}-{dt[6:8]}'
            if START <= dt <= END:
                bars.append({'date': dt, 'close': float(b['close'])})
        if bars:
            stocks[code] = {'name': d['name'], 'bars': bars, 'sector': sectors[code]}
    return stocks


def calc_ma(data, w):
    ma = []; n = len(data)
    for i in range(n):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma


def generate_signals(bars, cross_thr, ma_sell_w):
    closes = [b['close'] for b in bars]
    ma3 = calc_ma(closes, 3); ma7 = calc_ma(closes, 7)
    ma_sell = calc_ma(closes, ma_sell_w) if ma_sell_w else [float('nan')]*len(bars)
    for i, b in enumerate(bars):
        b['ma3'] = ma3[i]; b['ma7'] = ma7[i]; b['ma_sell'] = ma_sell[i]
        if math.isnan(ma3[i]) or math.isnan(ma7[i]) or ma7[i]==0:
            b['dev'] = float('nan'); b['signal_buy'] = False
        else:
            b['dev'] = (ma3[i]-ma7[i])/abs(ma7[i])
            b['signal_buy'] = b['dev'] >= cross_thr
        if ma_sell_w and not math.isnan(ma_sell[i]) and ma_sell[i] > 0:
            b['touches_ma'] = b['close'] <= ma_sell[i]
        else:
            b['touches_ma'] = False
    return bars


def backtest_rotation(stocks_dict, signal_dict, cross_thr, trail_pct, ma_sell_w):
    """
    Rotation backtest: max 5 positions, sector constraint.
    On each day: check sells first, then fill empty slots with best buys.
    """
    codes = sorted(stocks_dict.keys())
    n_codes = len(codes)
    per_pos_cap = INIT / MAX_POS

    # State
    positions = {}  # code -> {shares, buy_px, peak, entry_date}
    cash = INIT
    trades = []
    daily_values = []
    trail_n = 0; ma_n = 0; fin_n = 0

    # Align dates
    date_maps = {c: {b['date']: b for b in signal_dict[c]} for c in codes}
    common = sorted(set.intersection(*[set(m.keys()) for m in date_maps.values()]))

    for d in common:
        # --- Step 1: Check sells ---
        sold_codes = []
        for code in list(positions.keys()):
            bar = date_maps[code].get(d)
            if not bar: continue
            px = bar['close']; pos = positions[code]
            if px > pos['peak']: pos['peak'] = px
            pnl_per_share = 0; exit_type = None

            # Trail stop
            if px <= pos['peak'] * (1 - trail_pct):
                exit_type = 'trail'; trail_n += 1
            # MA touch
            elif ma_sell_w and bar.get('touches_ma', False):
                exit_type = 'ma'; ma_n += 1

            if exit_type:
                sold_codes.append(code)
                sell_cash = pos['shares'] * px * (1 - STAMP)
                pnl = sell_cash - pos['shares'] * pos['buy_px']
                trades.append({
                    'code': code, 'buy_d': pos['entry_date'], 'sell_d': d,
                    'bp': pos['buy_px'], 'sp': px,
                    'ret': (px - pos['buy_px'])/pos['buy_px'],
                    'pnl': pnl, 'exit': exit_type,
                })
                cash += sell_cash
                del positions[code]

        # --- Step 2: Fill empty slots ---
        slots = MAX_POS - len(positions)
        if slots > 0:
            # Find all buy candidates: signal_buy=True, not in current positions
            candidates = []
            for code in codes:
                if code in positions: continue
                bar = date_maps[code].get(d)
                if bar and bar.get('signal_buy', False) and not math.isnan(bar['dev']):
                    candidates.append((code, bar['dev']))

            # Sort by deviation descending
            candidates.sort(key=lambda x: x[1], reverse=True)

            # Apply sector constraint
            used_sectors = set()
            for code in positions:
                used_sectors.add(stocks_dict[code]['sector'])

            bought = 0
            for code, dev in candidates:
                if bought >= slots: break
                if cash <= 0: break
                sec = stocks_dict[code]['sector']
                if sec in used_sectors: continue  # sector constraint

                bar = date_maps[code][d]
                px = bar['close']
                buy_px_real = px * (1 + SLIPPAGE)
                invest = min(cash / (slots - bought), per_pos_cap)  # equal weight per slot
                shares = invest / buy_px_real
                positions[code] = {'shares': shares, 'buy_px': px, 'peak': px, 'entry_date': d}
                cash -= shares * buy_px_real
                used_sectors.add(sec)
                bought += 1

        # Daily value
        pos_value = 0
        for code, pos in positions.items():
            bar = date_maps[code].get(d)
            pos_value += pos['shares'] * bar['close'] * (1 - STAMP) if bar else 0
        total = cash + pos_value
        daily_values.append({'date': d, 'value': total, 'n_pos': len(positions)})

    # Final liquidation
    last_d = common[-1]
    for code in list(positions.keys()):
        bar = date_maps[code].get(last_d)
        if bar:
            px = bar['close']
            sell_cash = positions[code]['shares'] * px * (1 - STAMP)
            pnl = sell_cash - positions[code]['shares'] * positions[code]['buy_px']
            trades.append({
                'code': code, 'buy_d': positions[code]['entry_date'], 'sell_d': last_d,
                'bp': positions[code]['buy_px'], 'sp': px,
                'ret': (px - positions[code]['buy_px'])/positions[code]['buy_px'],
                'pnl': pnl, 'exit': 'final',
            })
            fin_n += 1
            cash += sell_cash
        del positions[code]

    fv = cash

    # Metrics
    rets = []
    for i in range(1, len(daily_values)):
        p, c = daily_values[i-1]['value'], daily_values[i]['value']
        if p > 0: rets.append((c-p)/p)
    if not rets: rets = [0.0]
    pkv = daily_values[0]['value']; mdd = 0.0
    for dv in daily_values:
        if dv['value'] > pkv: pkv = dv['value']
        dd = (pkv - dv['value'])/pkv
        if dd > mdd: mdd = dd
    tr = (fv - INIT)/INIT
    if len(rets) > 1:
        mu = sum(rets)/len(rets); sd = (sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av = sd*math.sqrt(TD); ar_ = mu*TD
        sh = (ar_-RF)/av if av>0 else 0
    else: av = sh = ar_ = 0.0
    ar = (1+tr)**(TD/max(len(rets),1))-1 if tr>-1 else -1
    cm = ar/mdd if mdd>0 else float('inf')

    pairs = trades  # already have buy_d/sell_d
    wins = sum(1 for t in pairs if t['ret']>0)
    wr = wins/len(pairs) if pairs else 0

    return {'tr': tr, 'ar': ar, 'vol': av, 'sh': sh, 'cm': cm, 'mdd': mdd,
            'np': len(pairs), 'wr': wr, 'trail_n': trail_n, 'ma_n': ma_n, 'fin_n': fin_n,
            'dvs': daily_values, 'trades': pairs, 'fv': fv}


def main():
    print('='*100)
    print('  择时2 · MA金叉轮动+赛道约束 · 63只等权网格搜索')
    print(f'  Buy: MA3>MA7+{CROSS_THRS}  Top5 by 乖离率  Sector不重复')
    print(f'  Sell: Trail{TRAILS} + MA{MA_SELLS}')
    print(f'  Grid: {len(CROSS_THRS)}x{len(TRAILS)}x{len(MA_SELLS)} = {len(CROSS_THRS)*len(TRAILS)*len(MA_SELLS)} combos')
    print('='*100)

    print('\n[DATA] Loading...')
    stocks = load_stocks_and_sectors()
    print(f'  Loaded {len(stocks)} stocks')
    codes = sorted(stocks.keys())
    n_stocks = len(codes)
    # Sector distribution
    sec_counts = defaultdict(int)
    for c in codes: sec_counts[stocks[c]['sector']] += 1
    print(f'  Sectors: {len(sec_counts)} unique (max={max(sec_counts.values())})')

    # Pre-compute signals for all parameter combos
    print(f'\n[SIGNALS] Pre-computing {len(CROSS_THRS)}x{len(MA_SELLS)} signal sets...')
    signal_cache = {}
    for thr in CROSS_THRS:
        for msw in MA_SELLS:
            key = (thr, msw)
            sigs = {}
            for code in codes:
                bars = [dict(b) for b in stocks[code]['bars']]
                sigs[code] = generate_signals(bars, thr, msw)
            signal_cache[key] = sigs
            buy_tot = sum(sum(1 for b in sigs[c] if b.get('signal_buy',False)) for c in codes)
            print(f'    thr={thr:.0%} MA={msw}: {buy_tot} buy signals')

    # Run grid
    TOTAL = len(CROSS_THRS) * len(TRAILS) * len(MA_SELLS)
    results = []; count = 0
    print(f'\n[GRID] {TOTAL} combos...')

    for thr in CROSS_THRS:
        for msw in MA_SELLS:
            sigs = signal_cache[(thr, msw)]
            for trail in TRAILS:
                count += 1
                label = f'Thr={thr:.0%} Trail={trail:.0%} MA={msw}'
                r = backtest_rotation(stocks, sigs, thr, trail, msw)
                r['label'] = label; r['thr'] = thr; r['trail'] = trail; r['ma_sell'] = msw
                results.append(r)

                print(f'  [{count:>3d}/{TOTAL}] {label:<30s} '
                      f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% '
                      f'DD={r["mdd"]*100:>5.2f}% Trd={r["np"]:>4d} '
                      f'Win={r["wr"]*100:>4.0f}% Trail={r["trail_n"]:>4d} MA={r["ma_n"]:>4d}')

    # ================================================================
    results.sort(key=lambda x: x['sh'], reverse=True)

    print('\n\n' + '='*100)
    print('  TOP 20 BY SHARPE')
    print('='*100)
    header = (f'  {"Rk":<3s} {"Thr":>5s} {"Trail":>6s} {"MA":>4s} '
              f'{"S":>7s} {"Ret":>9s} {"Ann":>8s} {"DD":>6s} '
              f'{"Calmar":>7s} {"Trd":>4s} {"Win":>5s} {"Trl#":>5s} {"MA#":>5s}')
    print(header); print(f'  {"-"*90}')
    for rank, r in enumerate(results[:20], 1):
        ma_str = f'MA{r["ma_sell"]}' if r['ma_sell'] else 'None'
        print(f'  {rank:<3d} {r["thr"]:>5.0%} {r["trail"]:>6.0%} {ma_str:>4s} '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>8.2f}% {r["ar"]*100:>7.2f}% '
              f'{r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} '
              f'{r["np"]:>4d} {r["wr"]*100:>4.0f}% {r["trail_n"]:>5d} {r["ma_n"]:>5d}')

    # Bottom 5
    print(f'\n  BOTTOM 5:')
    for r in results[-5:]: print(f'    {r["label"]:<30s} S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}%')

    # ================================================================
    # Parameter sensitivity
    print('\n\n  PARAMETER SENSITIVITY (avg Sharpe)')
    for pname, pkey in [('MA Cross Threshold', 'thr'), ('Trail Stop', 'trail'), ('MA Sell', 'ma_sell')]:
        levels = defaultdict(list)
        for r in results: levels[r[pkey]].append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(levels.keys(), key=lambda x: (x is None, x)):
            avg_s = sum(levels[v])/len(levels[v])
            lbl = f'{v:.0%}' if isinstance(v, float) else (f'MA{v}' if v else 'None')
            bar_len = max(int(avg_s * 30), 1) if avg_s > 0 else int(abs(avg_s) * 30)
            bar = '#' * bar_len if avg_s > 0 else '·' * bar_len
            print(f'    {lbl:>6s}  avg S={avg_s:>7.3f}  {bar}')

    # ================================================================
    # Best detail
    best = results[0]
    print(f'\n\n  ========================================')
    print(f'  BEST: {best["label"]}')
    print(f'  S={best["sh"]:.4f} Ret={best["tr"]*100:.2f}% Ann={best["ar"]*100:.2f}% '
          f'DD={best["mdd"]*100:.2f}% Calmar={best["cm"]:.3f}')
    print(f'  Trades={best["np"]} Win={best["wr"]*100:.0f}% '
          f'Trail={best["trail_n"]} MA={best["ma_n"]} Final={best["fin_n"]}')

    # Per-stock stats
    code_stats = defaultdict(lambda: {'np': 0, 'pnl': 0.0, 'wins': 0})
    for t in best['trades']:
        cs = code_stats[t['code']]
        cs['np'] += 1; cs['pnl'] += t['pnl']; cs['wins'] += 1 if t['ret'] > 0 else 0
    top_codes = sorted(code_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
    print(f'\n  Top 10 by P&L:')
    for c, s in top_codes[:10]:
        name = stocks[c]['name']
        print(f'    {c} {name:<10s}  PnL={s["pnl"]:>12,.0f}  Trd={s["np"]:>2d}  '
              f'Win={s["wins"]}/{s["np"]}')
    print(f'\n  Bottom 5:')
    for c, s in top_codes[-5:]:
        name = stocks[c]['name']
        print(f'    {c} {name:<10s}  PnL={s["pnl"]:>12,.0f}  Trd={s["np"]:>2d}')

    # Best trades
    best['trades'].sort(key=lambda x: x['ret'], reverse=True)
    print(f'\n  Best 10 trades:')
    for t in best['trades'][:10]:
        name = stocks[t['code']]['name']
        print(f'    {t["code"]} {name:<10s} {t["buy_d"]} -> {t["sell_d"]}  '
              f'{t["ret"]*100:>7.2f}%  {t["exit"]:>6s}  PnL={t["pnl"]:>12,.0f}')

    # Compare to previous best
    print(f'\n\n  +{"-"*75}+')
    print(f'  | COMPARISON WITH PREVIOUS STRATEGIES                               |')
    print(f'  +{"-"*75}+')
    print(f'  | {"Strategy":<35s} | {"Sharpe":>7s} | {"Ret":>8s} | {"DD":>7s} |')
    print(f'  |{"-"*73}|')
    comparisons = [
        ('区间突破追涨 Trail=30% 63只等权', 1.932, 396.0, 22.9),
        ('MA金叉轮动 赛道5只 (本策略)', best['sh'], best['tr']*100, best['mdd']*100),
        ('ETF 优先轮动', 3.37, 244.6, 19.5),
    ]
    for label, sh, ret, dd in comparisons:
        print(f'  | {label:<35s} | {sh:>7.3f} | {ret:>7.2f}% | {dd:>6.2f}% |')
    print(f'  +{"-"*75}+')

    print('\n  Done!')


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
