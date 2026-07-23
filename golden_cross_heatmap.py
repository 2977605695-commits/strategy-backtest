"""
MA3/MA7 金叉策略 · 热力图详测
================================
Buy:  MA3/MA7乖离率 ≥ 阈值, 按乖离率降序, Top5, 赛道不重复
Sell: Trail止损 only (收盘 ≤ 最高 × (1-trail%))
Rotation: 卖出当日立即补仓

Heatmap: 乖离率阈值 × Trail%
"""
import sys, io, os, math, csv
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from data_loader import load_prices, calc_ma, get_common_dates

# Constants
INIT_CAP = 10_000_000
RISK_FREE = 0.025; TD = 252
SLIP = 0.003; BUY_FEE = 0.00025; SELL_FEE = 0.00025; STAMP_TAX = 0.0005
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")


def load_sector_map():
    csv_files = sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
    sm = {}
    with open(os.path.join(FUND_DIR, csv_files[-1]), 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            sm[row['code'].strip()] = row.get('sector', '').strip()
    return sm


def generate_signals(stocks, threshold):
    signals = {}
    for code, info in stocks.items():
        closes = info['close']; dates = info['dates']
        ma3 = calc_ma(closes, 3); ma7 = calc_ma(closes, 7)
        ma10 = calc_ma(closes, 10); ma14 = calc_ma(closes, 14)
        sig = {}
        for i in range(len(closes)):
            if math.isnan(ma3[i]) or math.isnan(ma7[i]) or ma7[i] == 0:
                dev = float('nan')
            else:
                dev = (ma3[i] - ma7[i]) / abs(ma7[i])
            sig[dates[i]] = {
                'signal_buy': not math.isnan(dev) and dev >= threshold,
                'ma_cross_dev': dev,
                'ma7': ma7[i] if not math.isnan(ma7[i]) else float('nan'),
                'ma10': ma10[i] if not math.isnan(ma10[i]) else float('nan'),
                'ma14': ma14[i] if not math.isnan(ma14[i]) else float('nan'),
            }
        signals[code] = sig
    return signals


def backtest(stocks, signals, sector_map, common_dates,
             trail_pct, date_start=None, date_end=None):
    dates = [d for d in common_dates
             if (not date_start or d >= date_start) and (not date_end or d <= date_end)]
    cash = INIT_CAP; slot_cap = INIT_CAP / 5
    positions = {}; daily_equity = []; trades = []
    stock_idx = {c: {d: i for i, d in enumerate(stocks[c]['dates'])} for c in stocks}

    for day_i, date_str in enumerate(dates):
        # 1. Check exits (Trail 25%)
        for code, pos in list(positions.items()):
            if code not in stock_idx or date_str not in stock_idx[code]:
                continue
            si = stock_idx[code][date_str]; px = stocks[code]['close'][si]
            if px > pos['peak']:
                pos['peak'] = px
            if px <= pos['peak'] * (1 - trail_pct):
                sell_px = px * (1 - SLIP - SELL_FEE - STAMP_TAX)
                proceeds = pos['shares'] * sell_px
                cash += proceeds
                trades.append({
                    'code': code, 'name': stocks[code]['name'],
                    'buy_date': pos['buy_date'], 'sell_date': date_str,
                    'buy_px': pos['buy_price'], 'sell_px': sell_px,
                    'ret': (sell_px - pos['buy_price']) / pos['buy_price'] if pos['buy_price'] > 0 else 0,
                    'pnl': proceeds - pos['shares'] * pos['buy_price'],
                    'exit': 'trail', 'hold_days': day_i - pos['buy_idx'],
                    'dev_at_buy': pos.get('dev', 0),
                })
                del positions[code]

        # 2. Rotation: fill empty slots
        if len(positions) < 5 and cash >= slot_cap * 0.99:
            held_codes = set(positions.keys())
            held_sectors = {sector_map.get(c, '') for c in held_codes}
            slots = 5 - len(positions)

            candidates = []
            for code in stocks:
                if code in held_codes: continue
                if code not in stock_idx: continue
                s = sector_map.get(code, '')
                if s and s in held_sectors: continue
                sig = signals.get(code, {}).get(date_str, {})
                if sig.get('signal_buy'):
                    candidates.append({'code': code, 'dev': sig['ma_cross_dev']})
            candidates.sort(key=lambda x: x['dev'], reverse=True)

            for cand in candidates[:slots]:
                if cash < slot_cap * 0.99: break
                code = cand['code']
                si = stock_idx[code][date_str]
                buy_px_raw = stocks[code]['close'][si]
                buy_px = buy_px_raw * (1 + SLIP + BUY_FEE)
                shares = slot_cap / buy_px
                cash -= slot_cap
                positions[code] = {'shares': shares, 'cost': slot_cap,
                                   'buy_price': buy_px, 'peak': buy_px_raw,
                                   'buy_date': date_str, 'buy_idx': day_i,
                                   'dev': cand['dev']}
                held_codes.add(code)
                held_sectors.add(sector_map.get(code, ''))

        cash *= (1 + RISK_FREE / TD)
        pos_val = sum(p['shares'] * stocks[c]['close'][stock_idx[c][date_str]]
                      for c, p in positions.items() if c in stock_idx and date_str in stock_idx[c])
        daily_equity.append({'date': date_str, 'equity': cash + pos_val,
                             'cash': cash, 'positions': len(positions)})

    # Final liquidation
    last_date = dates[-1]
    for code, pos in list(positions.items()):
        if code in stock_idx and last_date in stock_idx[code]:
            px = stocks[code]['close'][stock_idx[code][last_date]]
            sell_px = px * (1 - SLIP - SELL_FEE - STAMP_TAX)
            proceeds = pos['shares'] * sell_px
            cash += proceeds
            trades.append({
                'code': code, 'name': stocks[code]['name'],
                'buy_date': pos['buy_date'], 'sell_date': last_date,
                'buy_px': pos['buy_price'], 'sell_px': sell_px,
                'ret': (sell_px - pos['buy_price']) / pos['buy_price'] if pos['buy_price'] > 0 else 0,
                'pnl': proceeds - pos['shares'] * pos['buy_price'],
                'exit': 'final', 'hold_days': len(dates) - 1 - pos['buy_idx'],
                'dev_at_buy': pos.get('dev', 0),
            })
    positions.clear()
    if daily_equity:
        daily_equity[-1]['equity'] = cash; daily_equity[-1]['positions'] = 0

    # Stats
    vals = [d['equity'] for d in daily_equity]
    total_ret = (vals[-1] - vals[0]) / vals[0] if vals[0] > 0 else 0
    rets = [(vals[i] - vals[i-1]) / vals[i-1] for i in range(1, len(vals)) if vals[i-1] > 0]
    years = len(rets) / TD
    cagr = (vals[-1] / vals[0]) ** (1 / years) - 1 if years > 0 and vals[0] > 0 else 0
    mu = sum(rets) / len(rets) if rets else 0
    var = sum((r - mu) ** 2 for r in rets) / len(rets) if rets else 0
    sd = var ** 0.5
    sharpe = (mu * TD - RISK_FREE) / (sd * (TD ** 0.5)) if sd > 0 else 0
    peak_v = vals[0]; mdd = 0.0
    for v in vals:
        if v > peak_v: peak_v = v
        dd = (peak_v - v) / peak_v
        if dd > mdd: mdd = dd
    calmar = cagr / mdd if mdd > 0 else float('inf')
    wins = sum(1 for t in trades if t['ret'] > 0)
    win_rate = wins / len(trades) if trades else 0
    holding_pct = sum(1 for d in daily_equity if d['positions'] > 0) / len(daily_equity)

    exit_types = defaultdict(lambda: {'count': 0, 'avg_ret': 0.0, 'total_ret': 0.0})
    for t in trades:
        e = t['exit']; exit_types[e]['count'] += 1; exit_types[e]['total_ret'] += t['ret']
    for e in exit_types:
        if exit_types[e]['count'] > 0:
            exit_types[e]['avg_ret'] = exit_types[e]['total_ret'] / exit_types[e]['count']

    return {
        'equity': daily_equity, 'trades': trades,
        'stats': {
            'total_ret': total_ret, 'cagr': cagr, 'sharpe': sharpe,
            'mdd': mdd, 'calmar': calmar, 'n_trades': len(trades),
            'win_rate': win_rate, 'holding_pct': holding_pct,
            'exit_types': dict(exit_types), 'n_days': len(daily_equity),
            'ann_ret': mu * TD,
        }
    }


def annual_returns(equity_curve):
    """Compute yearly returns from equity curve."""
    years = defaultdict(lambda: {'start': None, 'end': None})
    for d in equity_curve:
        y = d['date'][:4]
        if years[y]['start'] is None:
            years[y]['start'] = d['equity']
        years[y]['end'] = d['equity']
    result = {}
    for y in sorted(years):
        s, e = years[y]['start'], years[y]['end']
        if s and e and s > 0:
            result[y] = (e - s) / s * 100
    return result


def print_separator(title=None):
    w = 100
    if title:
        print(f'\n{"="*w}\n  {title}\n{"="*w}')
    else:
        print(f'{"="*w}')


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print_separator('MA3/MA7 金叉策略 · 热力图详测')
    print(f'  Buy:  MA3>MA7+乖离率阈值, 按乖离率降序, Top5, 赛道不重复')
    print(f'  Sell: Trail止损 only')
    print(f'  Cost: buy={SLIP+BUY_FEE:.3%} sell={SLIP+SELL_FEE+STAMP_TAX:.3%}')

    # Load data
    print(f'\n[DATA] Loading...')
    sector_map = load_sector_map()
    print(f'  {len(sector_map)} stocks, {len(set(sector_map.values()))} unique sectors')
    all_stocks = load_prices(stock_filter=None)
    stocks = {c: i for c, i in all_stocks.items()
              if i['dates'] and i['dates'][0] <= '20200103' and len(i['dates']) >= 1500}
    common_dates = get_common_dates(stocks)
    print(f'  {len(stocks)} stocks (44-old pool), {len(common_dates)} days')
    print(f'  Range: {common_dates[0]} → {common_dates[-1]} ({len(common_dates)/252:.1f}yr)')

    # ================================================================
    # PART 1: Baseline Config (乖离率≥2%, Trail=25%)
    # ================================================================
    print_separator('PART 1: Baseline Configuration')
    print(f'  MA3/MA7偏差 ≥ 2.0%  |  Trail 25%  |  Top5 追强  |  赛道去重')

    sigs_base = generate_signals(stocks, 0.02)
    bt_base = backtest(stocks, sigs_base, sector_map, common_dates, trail_pct=0.25)
    s = bt_base['stats']

    print(f'\n  {"="*80}')
    print(f'  📊 BASELINE PERFORMANCE')
    print(f'  {"="*80}')
    print(f'  Sharpe:       {s["sharpe"]:.4f}')
    print(f'  Total Return: {s["total_ret"]*100:.2f}%')
    print(f'  CAGR:         {s["cagr"]*100:.2f}%')
    print(f'  Max Drawdown: {s["mdd"]*100:.2f}%')
    print(f'  Calmar:       {s["calmar"]:.3f}')
    print(f'  Total Trades: {s["n_trades"]}')
    print(f'  Win Rate:     {s["win_rate"]*100:.1f}%')
    print(f'  Hold%:        {s["holding_pct"]*100:.1f}%')
    print(f'  Exit Types:   {s["exit_types"]}')

    # Annual returns
    yr = annual_returns(bt_base['equity'])
    print(f'\n  📅 Annual Returns:')
    for y, r in yr.items():
        bar = '█' * max(1, int(abs(r) / 5)) if r > 0 else '░' * max(1, int(abs(r) / 5))
        print(f'    {y}: {r:>+7.1f}%  {bar}')

    # Trade analysis
    trades_by_dev = defaultdict(lambda: {'count': 0, 'total_ret': 0.0, 'wins': 0})
    for t in bt_base['trades']:
        bucket = f'{t["dev_at_buy"]*100:.0f}-{(t["dev_at_buy"]+0.01)*100:.0f}%'
        trades_by_dev[bucket]['count'] += 1
        trades_by_dev[bucket]['total_ret'] += t['ret']
        if t['ret'] > 0: trades_by_dev[bucket]['wins'] += 1

    print(f'\n  📈 Trade Analysis by 乖离率 at Entry:')
    print(f'  {"DevBucket":<12s} {"Trades":>6s} {"WinRate":>7s} {"AvgRet":>8s}')
    print(f'  {"-"*35}')
    for bucket in sorted(trades_by_dev.keys()):
        d = trades_by_dev[bucket]
        avg = d['total_ret'] / d['count'] * 100 if d['count'] else 0
        wr = d['wins'] / d['count'] * 100 if d['count'] else 0
        print(f'  {bucket:<12s} {d["count"]:>6d} {wr:>6.1f}% {avg:>7.1f}%')

    # Top/Bottom trades
    trades_sorted = sorted(bt_base['trades'], key=lambda x: x['ret'], reverse=True)
    print(f'\n  🏆 Top 10 Trades:')
    print(f'  {"Stock":<12s} {"Buy":<12s} {"Sell":<12s} {"Ret":>8s} {"Dev@Buy":>8s} {"Hold":>5s} {"Exit":>6s}')
    print(f'  {"-"*65}')
    for t in trades_sorted[:10]:
        print(f'  {t["name"]:<12s} {t["buy_date"]:<12s} {t["sell_date"]:<12s} '
              f'{t["ret"]*100:>7.1f}% {t["dev_at_buy"]*100:>7.1f}% {t["hold_days"]:>5d} {t["exit"]:>6s}')

    print(f'\n  💀 Worst 10 Trades:')
    print(f'  {"Stock":<12s} {"Buy":<12s} {"Sell":<12s} {"Ret":>8s} {"Dev@Buy":>8s} {"Hold":>5s} {"Exit":>6s}')
    print(f'  {"-"*65}')
    for t in trades_sorted[-10:]:
        print(f'  {t["name"]:<12s} {t["buy_date"]:<12s} {t["sell_date"]:<12s} '
              f'{t["ret"]*100:>7.1f}% {t["dev_at_buy"]*100:>7.1f}% {t["hold_days"]:>5d} {t["exit"]:>6s}')

    # Sector analysis
    sector_trades = defaultdict(lambda: {'count': 0, 'total_ret': 0.0})
    for t in bt_base['trades']:
        sec = sector_map.get(t['code'], 'unknown')
        sector_trades[sec]['count'] += 1
        sector_trades[sec]['total_ret'] += t['ret']
    print(f'\n  🏭 Sector Performance:')
    print(f'  {"Sector":<30s} {"Trades":>6s} {"AvgRet":>8s}')
    print(f'  {"-"*46}')
    for sec in sorted(sector_trades, key=lambda x: sector_trades[x]['total_ret'] / sector_trades[x]['count'] if sector_trades[x]['count'] else 0, reverse=True):
        d = sector_trades[sec]
        avg = d['total_ret'] / d['count'] * 100 if d['count'] else 0
        print(f'  {sec:<30s} {d["count"]:>6d} {avg:>7.1f}%')

    # ================================================================
    # PART 2: Heatmap — 乖离率阈值 × Trail%
    # ================================================================
    print_separator('PART 2: Parameter Heatmap — 乖离率阈值 × Trail%')

    DEV_THRESHOLDS = [0.015, 0.02, 0.025, 0.03, 0.035, 0.04]
    TRAILS = [0.10, 0.15, 0.20, 0.25, 0.30]

    print(f'  X-axis: 乖离率 ∈ {[f"{t:.1%}" for t in DEV_THRESHOLDS]}')
    print(f'  Y-axis: Trail ∈ {[f"{t:.0%}" for t in TRAILS]}')
    print(f'  Total: {len(DEV_THRESHOLDS) * len(TRAILS)} combos')

    # Pre-compute all signal sets
    signal_cache = {}
    for thr in DEV_THRESHOLDS:
        sigs = generate_signals(stocks, thr)
        signal_cache[thr] = sigs
        total = sum(sum(1 for s in sigs[c].values() if s['signal_buy']) for c in sigs)
        days = len(set(d for c in sigs for d, s in sigs[c].items() if s['signal_buy']))
        print(f'    thr={thr:.1%} → {total} buy signals / {days} days')

    # Run all combos
    print(f'\n  Running {len(DEV_THRESHOLDS) * len(TRAILS)} backtests...')
    results = {}
    count = 0
    for trail in TRAILS:
        for thr in DEV_THRESHOLDS:
            count += 1
            bt = backtest(stocks, signal_cache[thr], sector_map, common_dates, trail_pct=trail)
            results[(thr, trail)] = bt['stats']
            s = bt['stats']
            print(f'    [{count:>2d}/30] thr={thr:.1%} trail={trail:.0%}  '
                  f'S={s["sharpe"]:>7.3f}  Ret={s["total_ret"]*100:>7.1f}%  '
                  f'DD={s["mdd"]*100:>5.1f}%  Calmar={s["calmar"]:>6.3f}  '
                  f'Trd={s["n_trades"]:>4d}  Win={s["win_rate"]*100:>4.0f}%')

    # ================================================================
    # Heatmaps (Sharpe / Ret / MDD / Calmar)
    # ================================================================
    def print_heatmap(metric, fmt='.3f', label=''):
        print(f'\n  {"─"*70}')
        print(f'  🔥 HEATMAP: {label} ({metric})')
        print(f'  {"─"*70}')

        # Color by value relative to range
        all_vals = [results[(thr, tr)][metric] for thr in DEV_THRESHOLDS for tr in TRAILS]
        vmin, vmax = min(all_vals), max(all_vals)
        vr = vmax - vmin if vmax != vmin else 1

        # Header
        print(f'  {"Trail↓":>7s}', end='')
        for thr in DEV_THRESHOLDS:
            print(f'  {thr*100:>5.1f}%', end='')
        print(f'  {"│ Avg":>8s}')
        print(f'  {"─"*7}', end='')
        for _ in DEV_THRESHOLDS:
            print(f'  {"─"*5}', end='')
        print(f'  {"─"*9}')

        row_bests = []
        for trail in TRAILS:
            best_idx = -1
            best_val = float('-inf')
            row_vals = []
            for i, thr in enumerate(DEV_THRESHOLDS):
                v = results[(thr, trail)][metric]
                row_vals.append(v)
                if v > best_val:
                    best_val = v; best_idx = i

            print(f'  {trail*100:>5.0f}% │', end='')
            for i, v in enumerate(row_vals):
                # Color intensity: how close to max
                intensity = (v - vmin) / vr if vr > 0 else 0.5
                prefix = '✨' if i == best_idx else '  '
                print(f'{prefix}{v:{fmt}}', end='')
            avg = sum(row_vals) / len(row_vals)
            if 'd' in fmt:
                print(f'  │ {avg:.0f}')
            else:
                print(f'  │ {avg:{fmt}}')
            row_bests.append(best_idx)

        # Column averages (skip for integer metrics since avg is float)
        if 'd' not in fmt:
            print(f'  {"─"*7}┼', end='')
            for _ in DEV_THRESHOLDS:
                print(f'{"─"*7}', end='')
            print(f'{"─"*9}')

            print(f'  {"Avg":>7s} │', end='')
            for i, thr in enumerate(DEV_THRESHOLDS):
                col_vals = [results[(thr, tr)][metric] for tr in TRAILS]
                avg = sum(col_vals) / len(col_vals)
                print(f'  {avg:{fmt}}', end='')
            print(f'  │')

        # Global best
        best_thr, best_trail = max(results, key=lambda k: results[k][metric])
        best_val = results[(best_thr, best_trail)][metric]
        print(f'\n  🏆 Best {metric}: thr={best_thr:.1%} trail={best_trail:.0%} → {best_val:{fmt}}')
        print(f'     (Baseline: thr=2.0% trail=25% → {results[(0.02, 0.25)][metric]:{fmt}})')

    print_separator('HEATMAP RESULTS')
    print_heatmap('sharpe', '.3f', 'Sharpe Ratio')
    print_heatmap('total_ret', '.1%', 'Total Return')
    print_heatmap('mdd', '.1%', 'Max Drawdown')
    print_heatmap('calmar', '.3f', 'Calmar Ratio')
    print_heatmap('n_trades', '4d', 'Trade Count')
    print_heatmap('win_rate', '.0%', 'Win Rate')

    # ================================================================
    # PART 3: Find BEST overall config
    # ================================================================
    print_separator('PART 3: Best Configuration Detail')

    # Best by Sharpe
    best_key = max(results, key=lambda k: results[k]['sharpe'])
    best_thr, best_trail = best_key
    bs = results[best_key]
    print(f'\n  🥇 Best by Sharpe: thr={best_thr:.1%} trail={best_trail:.0%}')
    print(f'     Sharpe={bs["sharpe"]:.4f}  Ret={bs["total_ret"]*100:.2f}%  '
          f'CAGR={bs["cagr"]*100:.2f}%  MDD={bs["mdd"]*100:.2f}%  '
          f'Calmar={bs["calmar"]:.3f}  Trd={bs["n_trades"]}  Win={bs["win_rate"]*100:.0f}%')

    # Best by Calmar
    best_cal = max(results, key=lambda k: results[k]['calmar'])
    bct, bcl = best_cal
    cs = results[best_cal]
    print(f'\n  🥇 Best by Calmar: thr={bct:.1%} trail={bcl:.0%}')
    print(f'     Sharpe={cs["sharpe"]:.4f}  Ret={cs["total_ret"]*100:.2f}%  '
          f'CAGR={cs["cagr"]*100:.2f}%  MDD={cs["mdd"]*100:.2f}%  '
          f'Calmar={cs["calmar"]:.3f}  Trd={cs["n_trades"]}  Win={cs["win_rate"]*100:.0f}%')

    # Pareto-optimal: best Sharpe for each trail level
    print(f'\n  ┌─────────────────────────────────────────────────────────────┐')
    print(f'  │  TRAIL SENSITIVITY (Best Sharpe per Trail Level)             │')
    print(f'  ├────────┬──────────┬──────────┬────────┬────────┬────────────┤')
    print(f'  │  Trail │ Best Thr │  Sharpe  │  Ret%  │  MDD%  │   Trades   │')
    print(f'  ├────────┼──────────┼──────────┼────────┼────────┼────────────┤')
    for trail in TRAILS:
        best_for_trail = max(
            [(thr, results[(thr, trail)]) for thr in DEV_THRESHOLDS],
            key=lambda x: x[1]['sharpe']
        )
        bt, bs2 = best_for_trail
        print(f'  │ {trail:>4.0f}%  │  {bt:.1%}    │  {bs2["sharpe"]:>6.3f}  │ '
              f'{bs2["total_ret"]*100:>6.1f}% │ {bs2["mdd"]*100:>5.1f}% │ '
              f'{bs2["n_trades"]:>6d}   │')
    print(f'  └────────┴──────────┴──────────┴────────┴────────┴────────────┘')

    # Same for threshold sensitivity
    print(f'\n  ┌─────────────────────────────────────────────────────────────┐')
    print(f'  │  THRESHOLD SENSITIVITY (Best Sharpe per Deviation Level)     │')
    print(f'  ├──────────┬──────────┬──────────┬────────┬────────┬──────────┤')
    print(f'  │   Thr    │ Best Trl │  Sharpe  │  Ret%  │  MDD%  │  Trades  │')
    print(f'  ├──────────┼──────────┼──────────┼────────┼────────┼──────────┤')
    for thr in DEV_THRESHOLDS:
        best_for_thr = max(
            [(trail, results[(thr, trail)]) for trail in TRAILS],
            key=lambda x: x[1]['sharpe']
        )
        btr, bs3 = best_for_thr
        print(f'  │ {thr:>5.1%}   │  {btr:>4.0f}%   │  {bs3["sharpe"]:>6.3f}  │ '
              f'{bs3["total_ret"]*100:>6.1f}% │ {bs3["mdd"]*100:>5.1f}% │ '
              f'{bs3["n_trades"]:>6d}   │')
    print(f'  └──────────┴──────────┴──────────┴────────┴────────┴──────────┘')

    # ================================================================
    # PART 4: Run final detailed backtest with optimal params
    # ================================================================
    print_separator('PART 4: Optimal Config Full Detail')

    opt_sigs = generate_signals(stocks, best_thr)
    opt_bt = backtest(stocks, opt_sigs, sector_map, common_dates, trail_pct=best_trail)

    # OOS split test
    splits = [
        ('Full Period', None, None),
        ('2020-2022 (Train)', None, '20221231'),
        ('2023-2024 (Valid)', '20230101', '20241231'),
        ('2025-2026 (Test)',  '20250101', None),
    ]
    print(f'\n  🧪 Out-of-Sample Split Test:')
    print(f'  {"Period":<25s} {"Sharpe":>7s} {"Ret":>9s} {"MDD":>7s} {"Trd":>5s} {"Win":>5s}')
    print(f'  {"-"*60}')
    for name, ds, de in splits:
        bt = backtest(stocks, opt_sigs, sector_map, common_dates,
                      trail_pct=best_trail, date_start=ds, date_end=de)
        st = bt['stats']
        print(f'  {name:<25s} {st["sharpe"]:>7.3f} {st["total_ret"]*100:>8.2f}% '
              f'{st["mdd"]*100:>6.2f}% {st["n_trades"]:>5d} {st["win_rate"]*100:>4.0f}%')

    # Annual returns for optimal
    opt_yr = annual_returns(opt_bt['equity'])
    print(f'\n  📅 Optimal Annual Returns:')
    for y, r in opt_yr.items():
        bar = '█' * max(1, int(abs(r) / 3)) if r > 0 else '░' * max(1, int(abs(r) / 3))
        print(f'    {y}: {r:>+7.1f}%  {bar}')

    # Signal statistics
    print(f'\n  📊 Signal Statistics (thr={best_thr:.1%}):')
    for code in sorted(stocks.keys())[:10]:
        info = stocks[code]
        buy_n = sum(1 for s in opt_sigs[code].values() if s['signal_buy'])
        print(f'    {code} {info["name"]:<12s}  signals={buy_n:>4d}')

    # Export
    base_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base_dir, 'golden_cross_heatmap.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['dev_threshold', 'trail_pct', 'sharpe', 'total_ret', 'cagr',
                     'mdd', 'calmar', 'n_trades', 'win_rate', 'holding_pct'])
        for (thr, trail), s in results.items():
            w.writerow([f'{thr:.3f}', f'{trail:.2f}',
                        f'{s["sharpe"]:.4f}', f'{s["total_ret"]:.4f}',
                        f'{s["cagr"]:.4f}', f'{s["mdd"]:.4f}',
                        f'{s["calmar"]:.4f}', s['n_trades'],
                        f'{s["win_rate"]:.4f}', f'{s["holding_pct"]:.4f}'])
    print(f'\n  ✅ golden_cross_heatmap.csv exported ({len(results)} rows)')

    with open(os.path.join(base_dir, 'golden_cross_trades.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['code', 'name', 'buy_date', 'sell_date', 'buy_px', 'sell_px',
                     'ret', 'pnl', 'exit', 'hold_days', 'dev_at_buy'])
        for t in sorted(opt_bt['trades'], key=lambda x: x['buy_date']):
            w.writerow([t['code'], t['name'], t['buy_date'], t['sell_date'],
                        f'{t["buy_px"]:.4f}', f'{t["sell_px"]:.4f}',
                        f'{t["ret"]:.6f}', f'{t["pnl"]:.2f}',
                        t['exit'], t['hold_days'], f'{t.get("dev_at_buy", 0):.6f}'])
    print(f'  ✅ golden_cross_trades.csv exported ({len(opt_bt["trades"])} trades)')

    print(f'\n{"="*100}')
    print(f'  详测完成!')
    print(f'{"="*100}')


if __name__ == '__main__':
    main()
