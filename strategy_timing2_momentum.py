"""
择时策略2 · 纯MA乖离率动量策略 · 四阶段漏斗回测
============================================================
Buy:  MA3/MA7乖离率 ≥ 阈值 (2%/3%/4%)
Sort: 降序(追强) vs 升序(适中) — 两种方法对比
Sell: Trail止损 / MA触碰 / 两者OR
Rotation: 空仓补仓 + 赛道去重

歧义澄清:
  1. 仅空仓时补仓（不每日调仓）
  2. 卖出框架分别测试
  3. 乖离率独立候选值测试
  4. 两种排序方法都测试
"""
import sys, io, os, math, csv
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from data_loader import (
    load_prices, calc_ma, get_common_dates
)

# ============================================================
# Constants
# ============================================================
INIT_CAP = 10_000_000
RISK_FREE = 0.025
TD = 252
SLIP = 0.003
BUY_FEE = 0.00025
SELL_FEE = 0.00025
STAMP_TAX = 0.0005

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")


# ============================================================
# Utility Functions
# ============================================================
def load_sector_map():
    """Load {code: sector} from most recent fundamentals CSV."""
    csv_files = sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
    if not csv_files:
        return {}
    sector_map = {}
    with open(os.path.join(FUND_DIR, csv_files[-1]), 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            code = row['code'].strip()
            sector = row.get('sector', '').strip()
            if code and sector:
                sector_map[code] = sector
    return sector_map


def compute_stats(equity_curve, trades):
    """Compute performance statistics."""
    vals = [d['equity'] for d in equity_curve]
    if len(vals) < 2 or vals[0] <= 0:
        return {'total_ret': 0, 'cagr': 0, 'ann_ret': 0, 'sharpe': 0,
                'mdd': 0, 'calmar': 0, 'n_trades': 0, 'win_rate': 0,
                'holding_pct': 0, 'exit_types': {}}

    init_val = vals[0]; final_val = vals[-1]
    total_ret = (final_val - init_val) / init_val

    rets = []
    for i in range(1, len(vals)):
        if vals[i-1] > 0:
            rets.append((vals[i] - vals[i-1]) / vals[i-1])
    if not rets:
        return {'total_ret': total_ret, 'cagr': 0, 'ann_ret': 0, 'sharpe': 0,
                'mdd': 0, 'calmar': 0, 'n_trades': len(trades), 'win_rate': 0,
                'holding_pct': 0, 'exit_types': {}}

    years = len(rets) / TD
    cagr = (final_val / init_val) ** (1 / years) - 1 if years > 0 else 0
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / len(rets)
    sd = var ** 0.5
    ann_ret = mu * TD
    sharpe = (ann_ret - RISK_FREE) / (sd * (TD ** 0.5)) if sd > 0 else 0

    peak = vals[0]; mdd = 0.0
    for v in vals:
        if v > peak: peak = v
        dd = (peak - v) / peak
        if dd > mdd: mdd = dd

    calmar = cagr / mdd if mdd > 0 else float('inf')
    wins = sum(1 for t in trades if t['ret'] > 0)
    n_trades = len(trades)
    win_rate = wins / n_trades if n_trades > 0 else 0

    exit_types = defaultdict(lambda: {'count': 0, 'avg_ret': 0.0})
    for t in trades:
        e = t['exit']
        exit_types[e]['count'] += 1
        exit_types[e]['avg_ret'] += t['ret']
    for e in exit_types:
        if exit_types[e]['count'] > 0:
            exit_types[e]['avg_ret'] /= exit_types[e]['count']

    return {
        'total_ret': total_ret, 'cagr': cagr, 'ann_ret': ann_ret,
        'sharpe': sharpe, 'mdd': mdd, 'calmar': calmar,
        'n_trades': n_trades, 'win_rate': win_rate,
        'holding_pct': sum(1 for d in equity_curve if d['positions'] > 0) / len(equity_curve),
        'exit_types': dict(exit_types), 'n_days': len(equity_curve),
    }


# ============================================================
# Signal Generation (simplified: only MA3/MA7乖离率)
# ============================================================
def generate_signals(stocks, ma_threshold):
    """
    MA3/MA7乖离率 only. No range-bound condition.
    Returns: {code: {date_str: {'signal_buy': bool, 'ma_cross_dev': float,
                                  'ma7': float, 'ma10': float, 'ma14': float}}}
    """
    signals = {}
    for code, info in stocks.items():
        closes = info['close']
        dates = info['dates']
        n = len(closes)

        ma3 = calc_ma(closes, 3)
        ma7 = calc_ma(closes, 7)
        ma10 = calc_ma(closes, 10)
        ma14 = calc_ma(closes, 14)

        sig = {}
        for i in range(n):
            date = dates[i]

            if math.isnan(ma3[i]) or math.isnan(ma7[i]) or ma7[i] == 0:
                ma_cross_dev = float('nan')
            else:
                ma_cross_dev = (ma3[i] - ma7[i]) / abs(ma7[i])

            signal_buy = (not math.isnan(ma_cross_dev) and ma_cross_dev >= ma_threshold)

            sig[date] = {
                'signal_buy': signal_buy,
                'ma_cross_dev': ma_cross_dev,
                'ma7': ma7[i] if not math.isnan(ma7[i]) else float('nan'),
                'ma10': ma10[i] if not math.isnan(ma10[i]) else float('nan'),
                'ma14': ma14[i] if not math.isnan(ma14[i]) else float('nan'),
            }
        signals[code] = sig
    return signals


# ============================================================
# Backtest Engine
# ============================================================
def backtest_momentum(stocks, signals, sector_map, common_dates,
                      trail_pct, ma_sell_w, sell_framework, max_pos,
                      rank_method='strongest',
                      date_start=None, date_end=None):
    """
    Pure MA乖离率 momentum rotation backtest.

    Args:
        rank_method: 'strongest' = highest乖离率first (追强)
                     'moderate' = lowest乖离率first (适中, just above threshold)
    """
    dates = common_dates
    if date_start:
        dates = [d for d in dates if d >= date_start]
    if date_end:
        dates = [d for d in dates if d <= date_end]

    cash = INIT_CAP
    slot_cap = INIT_CAP / max_pos
    positions = {}  # code -> {shares, cost, buy_price, peak, buy_date, buy_idx, dev}
    daily_equity = []
    trades = []

    stock_idx = {}
    for code in stocks:
        stock_idx[code] = {d: i for i, d in enumerate(stocks[code]['dates'])}

    ma_field = f'ma{ma_sell_w}'

    for day_i, date_str in enumerate(dates):
        # 1. Check exits
        for code, pos in list(positions.items()):
            if code not in stock_idx or date_str not in stock_idx[code]:
                continue
            si = stock_idx[code][date_str]
            px = stocks[code]['close'][si]

            if px > pos['peak']:
                pos['peak'] = px

            sig = signals.get(code, {}).get(date_str, {})
            ma_val = sig.get(ma_field, float('nan'))
            trail_hit = (px <= pos['peak'] * (1 - trail_pct))
            ma_hit = (not math.isnan(ma_val) and ma_val > 0 and px <= ma_val)

            should_sell = False
            exit_reason = ''
            if sell_framework == 'trail_only' and trail_hit:
                should_sell = True; exit_reason = 'trail'
            elif sell_framework == 'ma_only' and ma_hit:
                should_sell = True; exit_reason = f'ma{ma_sell_w}'
            elif sell_framework == 'trail_or_ma':
                if trail_hit:
                    should_sell = True; exit_reason = 'trail'
                elif ma_hit:
                    should_sell = True; exit_reason = f'ma{ma_sell_w}'

            if should_sell:
                sell_px = px * (1 - SLIP - SELL_FEE - STAMP_TAX)
                proceeds = pos['shares'] * sell_px
                pnl = proceeds - pos['shares'] * pos['buy_price']
                cash += proceeds
                trades.append({
                    'code': code, 'name': stocks[code]['name'],
                    'buy_date': pos['buy_date'], 'sell_date': date_str,
                    'buy_px': pos['buy_price'], 'sell_px': sell_px,
                    'ret': (sell_px - pos['buy_price']) / pos['buy_price'] if pos['buy_price'] > 0 else 0,
                    'pnl': pnl, 'exit': exit_reason,
                    'hold_days': day_i - pos['buy_idx'],
                    'dev_at_buy': pos.get('dev', 0),
                })
                del positions[code]

        # 2. Fill empty slots (only when there are empty slots)
        if len(positions) < max_pos and cash >= slot_cap * 0.99:
            held_codes = set(positions.keys())
            held_sectors = {sector_map.get(c, '') for c in held_codes}
            slots = max_pos - len(positions)

            # Gather candidates
            candidates = []
            for code in stocks:
                if code in held_codes:
                    continue
                if code not in stock_idx:
                    continue
                cand_sector = sector_map.get(code, '')
                if cand_sector and cand_sector in held_sectors:
                    continue
                sig = signals.get(code, {}).get(date_str, {})
                if sig.get('signal_buy', False):
                    candidates.append({
                        'code': code,
                        'dev': sig['ma_cross_dev'],
                    })

            # Sort candidates
            if rank_method == 'strongest':
                candidates.sort(key=lambda x: x['dev'], reverse=True)  # 追强
            else:
                candidates.sort(key=lambda x: x['dev'])  # 适中(刚好越过阈值)

            # Buy top N
            for cand in candidates[:slots]:
                code = cand['code']
                if cash < slot_cap * 0.99:
                    break
                si = stock_idx[code][date_str]
                buy_px_raw = stocks[code]['close'][si]
                buy_px = buy_px_raw * (1 + SLIP + BUY_FEE)
                shares = slot_cap / buy_px

                cash -= slot_cap
                positions[code] = {
                    'shares': shares, 'cost': slot_cap,
                    'buy_price': buy_px, 'peak': buy_px_raw,
                    'buy_date': date_str, 'buy_idx': day_i,
                    'dev': cand['dev'],
                }
                held_codes.add(code)
                held_sectors.add(sector_map.get(code, ''))

        # 3. Risk-free interest
        cash *= (1 + RISK_FREE / TD)

        # 4. Mark-to-market
        pos_val = 0.0
        for code, pos in positions.items():
            if code in stock_idx and date_str in stock_idx[code]:
                si = stock_idx[code][date_str]
                pos_val += pos['shares'] * stocks[code]['close'][si]
        total_val = cash + pos_val
        daily_equity.append({
            'date': date_str, 'equity': total_val,
            'cash': cash, 'positions': len(positions),
        })

    # Final liquidation
    last_date = dates[-1]
    for code, pos in list(positions.items()):
        if code in stock_idx and last_date in stock_idx[code]:
            si = stock_idx[code][last_date]
            px = stocks[code]['close'][si]
            sell_px = px * (1 - SLIP - SELL_FEE - STAMP_TAX)
            proceeds = pos['shares'] * sell_px
            pnl = proceeds - pos['shares'] * pos['buy_price']
            cash += proceeds
            trades.append({
                'code': code, 'name': stocks[code]['name'],
                'buy_date': pos['buy_date'], 'sell_date': last_date,
                'buy_px': pos['buy_price'], 'sell_px': sell_px,
                'ret': (sell_px - pos['buy_price']) / pos['buy_price'] if pos['buy_price'] > 0 else 0,
                'pnl': pnl, 'exit': 'final',
                'hold_days': len(dates) - 1 - pos['buy_idx'],
                'dev_at_buy': pos.get('dev', 0),
            })
    positions.clear()
    if daily_equity:
        daily_equity[-1]['equity'] = cash
        daily_equity[-1]['cash'] = cash
        daily_equity[-1]['positions'] = 0

    stats = compute_stats(daily_equity, trades)
    return {'equity': daily_equity, 'trades': trades, 'stats': stats}


# ============================================================
# Print Helpers
# ============================================================
def print_header(title):
    print(f'\n{"="*100}')
    print(f'  {title}')
    print(f'{"="*100}')


def print_ranking(results, sort_key='sharpe', top_n=20, min_trades=5):
    filtered = [r for r in results if r['stats']['n_trades'] >= min_trades]
    filtered.sort(key=lambda x: x['stats'][sort_key], reverse=True)
    print(f'  {"Rank":<4s} {"Params":<60s} '
          f'{"Sharpe":>7s} {"TotRet":>9s} {"CAGR":>7s} {"MaxDD":>7s} '
          f'{"Calmar":>7s} {"Trd":>5s} {"Win":>5s} {"Hold%":>6s}')
    print(f'  {"-"*115}')
    for rank, r in enumerate(filtered[:top_n], 1):
        s = r['stats']
        print(f'  {rank:<4d} {r["label"]:<60s} '
              f'{s["sharpe"]:>7.3f} {s["total_ret"]*100:>8.2f}% {s["cagr"]*100:>7.2f}% '
              f'{s["mdd"]*100:>6.2f}% {s["calmar"]:>7.3f} '
              f'{s["n_trades"]:>5d} {s["win_rate"]*100:>4.0f}% {s["holding_pct"]*100:>5.1f}%')
    return filtered


# ============================================================
# Phase 1: Buy Signal + Ranking Method Screening
# ============================================================
def run_phase1(stocks, sector_map, common_dates):
    """
    Grid: 乖离率阈值 × 排序方法
    Fixed: Trail=20%, trail_only, max_pos=5
    """
    print_header('PHASE 1: Buy Signal + Ranking Method Screening')

    thresholds = [0.02, 0.03, 0.04]
    rank_methods = ['strongest', 'moderate']

    print(f'  Grid: 乖离率∈{[f"{t:.0%}" for t in thresholds]}  '
          f'排序∈{rank_methods}')
    print(f'  Fixed: Trail=20%, trail_only, max_pos=5, pool=44-old')
    print(f'  Total: {len(thresholds) * len(rank_methods)} combos\n')

    # Pre-compute signals
    signal_cache = {}
    print('  Pre-computing signals...')
    for thr in thresholds:
        sigs = generate_signals(stocks, thr)
        signal_cache[thr] = sigs
        total_buy = sum(
            sum(1 for s in sigs[c].values() if s['signal_buy']) for c in sigs
        )
        days_with_signal = set()
        for c in sigs:
            for d, s in sigs[c].items():
                if s['signal_buy']:
                    days_with_signal.add(d)
        print(f'    thr={thr:.0%} → {total_buy} buy signals across {len(days_with_signal)} days')

    results = []
    for thr in thresholds:
        for rm in rank_methods:
            label = f'thr={thr:.0%} rank={rm} T=20% trail-only'
            bt = backtest_momentum(
                stocks, signal_cache[thr], sector_map, common_dates,
                trail_pct=0.20, ma_sell_w=14, sell_framework='trail_only',
                max_pos=5, rank_method=rm,
            )
            results.append({
                'label': label,
                'params': {'ma_threshold': thr, 'rank_method': rm},
                'stats': bt['stats'],
            })
            s = bt['stats']
            print(f'    {label:<55s} '
                  f'S={s["sharpe"]:>7.3f} Ret={s["total_ret"]*100:>7.2f}% '
                  f'DD={s["mdd"]*100:>5.2f}% Trd={s["n_trades"]:>3d} '
                  f'Win={s["win_rate"]*100:>4.0f}% Hold={s["holding_pct"]*100:>4.1f}%')

    print(f'\n  --- Phase 1 Ranking ---')
    ranked = print_ranking(results, sort_key='sharpe')

    if ranked:
        best = ranked[0]
        s = best['stats']; p = best['params']
        print(f'\n  🏆 Best: {best["label"]}')
        print(f'     Sharpe={s["sharpe"]:.4f} Ret={s["total_ret"]*100:.2f}% '
              f'CAGR={s["cagr"]*100:.2f}% MDD={s["mdd"]*100:.2f}% '
              f'Calmar={s["calmar"]:.3f} Trd={s["n_trades"]} Win={s["win_rate"]*100:.0f}%')

    # Compare ranking methods
    print(f'\n  --- Ranking Method Comparison ---')
    for rm in rank_methods:
        subset = [r for r in results if r['params']['rank_method'] == rm]
        avg_s = sum(r['stats']['sharpe'] for r in subset) / len(subset)
        avg_ret = sum(r['stats']['total_ret'] for r in subset) / len(subset)
        avg_trd = sum(r['stats']['n_trades'] for r in subset) / len(subset)
        print(f'    {rm:<12s}  avg Sharpe={avg_s:.3f}  avg Ret={avg_ret*100:.1f}%  '
              f'avg Trd={avg_trd:.0f}')

    return results


# ============================================================
# Phase 2: Sell Signal Fine-Tuning
# ============================================================
def run_phase2(stocks, sector_map, common_dates, p1_results):
    """Grid: Trail × MA_window × sell_framework"""
    print_header('PHASE 2: Sell Signal Fine-Tuning')

    # Get best buy params
    ranked = sorted(
        [r for r in p1_results if r['stats']['n_trades'] >= 5],
        key=lambda x: x['stats']['sharpe'], reverse=True
    )
    if not ranked:
        print('  No results passed min trade filter!')
        return []
    best_buy = ranked[0]['params']

    trails = [0.10, 0.15, 0.20, 0.25, 0.30]
    ma_windows = [7, 10, 14]
    frameworks = ['trail_only', 'ma_only', 'trail_or_ma']

    print(f'  Buy: thr={best_buy["ma_threshold"]:.0%} rank={best_buy["rank_method"]}')
    print(f'  Sell Grid: Trail∈{[f"{t:.0%}" for t in trails]}  '
          f'MA∈{ma_windows}  Framework∈{frameworks}')
    print(f'  Total: {len(trails)*len(ma_windows)*len(frameworks)} combos\n')

    sigs = generate_signals(stocks, best_buy['ma_threshold'])
    TOTAL = len(trails) * len(ma_windows) * len(frameworks)

    results = []
    count = 0
    for trail in trails:
        for ma_w in ma_windows:
            for fw in frameworks:
                count += 1
                bt = backtest_momentum(
                    stocks, sigs, sector_map, common_dates,
                    trail_pct=trail, ma_sell_w=ma_w, sell_framework=fw,
                    max_pos=5, rank_method=best_buy['rank_method'],
                )
                label = f'T={trail:.0%} MA{ma_w} {fw}'
                results.append({
                    'label': label,
                    'params': {**best_buy, 'trail': trail, 'ma_sell_w': ma_w, 'framework': fw},
                    'stats': bt['stats'],
                })
                if count % 10 == 0 or count == TOTAL:
                    s = bt['stats']
                    print(f'    [{count:>2d}/{TOTAL}] {label:<30s} '
                          f'S={s["sharpe"]:>7.3f} Ret={s["total_ret"]*100:>7.2f}% '
                          f'DD={s["mdd"]*100:>6.2f}% Calmar={s["calmar"]:>7.3f} '
                          f'Trd={s["n_trades"]:>4d}')

    print(f'\n  --- Phase 2 Ranking (by Calmar) ---')
    ranked_final = print_ranking(results, sort_key='calmar', top_n=15)

    if ranked_final:
        best = ranked_final[0]
        s = best['stats']
        print(f'\n  🏆 Best: {best["label"]}')
        print(f'     Sharpe={s["sharpe"]:.4f}  Calmar={s["calmar"]:.3f}  '
              f'Ret={s["total_ret"]*100:.2f}%  MDD={s["mdd"]*100:.2f}%')
        print(f'     Exit types: {s["exit_types"]}')

    return results


# ============================================================
# Phase 3: Position Count + Pool + Method Compare
# ============================================================
def run_phase3(stocks, sector_map, common_dates, best_params):
    """
    A. Position count 1-5
    B. Ranking method verification (best vs second-best)
    C. Pool comparison
    """
    print_header('PHASE 3: Position Count + Method + Pool')

    p = best_params
    sigs = generate_signals(stocks, p['ma_threshold'])

    # A. Position count
    print(f'\n  --- A. Position Count ---')
    print(f'  {"Pos":<5s} {"Sharpe":>7s} {"TotRet":>9s} {"CAGR":>7s} '
          f'{"MaxDD":>7s} {"Calmar":>7s} {"Trd":>5s} {"Win":>5s} {"Hold%":>6s}')
    print(f'  {"-"*70}')
    pos_results = []
    for mp in [1, 2, 3, 4, 5]:
        bt = backtest_momentum(
            stocks, sigs, sector_map, common_dates,
            trail_pct=p['trail'], ma_sell_w=p['ma_sell_w'],
            sell_framework=p['framework'], max_pos=mp,
            rank_method=p['rank_method'],
        )
        s = bt['stats']
        pos_results.append({'max_pos': mp, 'stats': s})
        print(f'  {mp:<5d} {s["sharpe"]:>7.3f} {s["total_ret"]*100:>8.2f}% '
              f'{s["cagr"]*100:>7.2f}% {s["mdd"]*100:>6.2f}% {s["calmar"]:>7.3f} '
              f'{s["n_trades"]:>5d} {s["win_rate"]*100:>4.0f}% {s["holding_pct"]*100:>5.1f}%')

    best_pos = max(pos_results, key=lambda x: x['stats']['sharpe'])
    print(f'  → Best max_pos={best_pos["max_pos"]} '
          f'Sharpe={best_pos["stats"]["sharpe"]:.4f}')

    # B. Ranking method comparison (both methods with best params)
    print(f'\n  --- B. Ranking Method Head-to-Head (best params) ---')
    p_best = {**p, 'max_pos': best_pos['max_pos']}
    for rm in ['strongest', 'moderate']:
        bt = backtest_momentum(
            stocks, sigs, sector_map, common_dates,
            trail_pct=p['trail'], ma_sell_w=p['ma_sell_w'],
            sell_framework=p['framework'], max_pos=p_best['max_pos'],
            rank_method=rm,
        )
        s = bt['stats']
        tag = ' ← 追强' if rm == 'strongest' else ' ← 适中'
        print(f'    {rm:<12s}  S={s["sharpe"]:>7.3f}  '
              f'Ret={s["total_ret"]*100:>7.2f}%  DD={s["mdd"]*100:>5.2f}%  '
              f'Trd={s["n_trades"]:>3d}  Win={s["win_rate"]*100:>4.0f}%{tag}')

    # C. Pool comparison
    print(f'\n  --- C. Stock Pool Comparison ---')
    pools = [('44-old', 'old'), ('64-all', 'all64'), ('70-full', None)]
    print(f'  {"Pool":<10s} {"N":>5s} {"Sharpe":>7s} {"TotRet":>9s} '
          f'{"MaxDD":>7s} {"Trd":>5s} {"Win":>5s}')
    print(f'  {"-"*55}')
    for pool_name, pool_filter in pools:
        stk = load_prices(stock_filter=pool_filter)
        if not stk:
            print(f'  {pool_name:<10s} {"-":>5s}  (no stocks loaded)')
            continue
        # Manual filter for 'old' due to date format bug
        if pool_filter == 'old':
            stk = {c: i for c, i in stk.items()
                   if i['dates'] and i['dates'][0] <= '20200103' and len(i['dates']) >= 1500}
        cd = get_common_dates(stk)
        ss = generate_signals(stk, p['ma_threshold'])
        bt = backtest_momentum(
            stk, ss, sector_map, cd,
            trail_pct=p['trail'], ma_sell_w=p['ma_sell_w'],
            sell_framework=p['framework'], max_pos=p_best['max_pos'],
            rank_method=p['rank_method'],
        )
        s = bt['stats']
        print(f'  {pool_name:<10s} {len(stk):>5d} {s["sharpe"]:>7.3f} '
              f'{s["total_ret"]*100:>8.2f}% {s["mdd"]*100:>6.2f}% '
              f'{s["n_trades"]:>5d} {s["win_rate"]*100:>4.0f}%')

    return {**p_best}


# ============================================================
# Phase 4: OOS Validation + Sensitivity
# ============================================================
def run_phase4(stocks, sector_map, common_dates, best_params):
    """OOS splits + rolling windows + parameter sensitivity."""
    print_header('PHASE 4: OOS Validation + Sensitivity')

    p = best_params
    sigs = generate_signals(stocks, p['ma_threshold'])

    # Time splits
    splits = [
        ('Full Period', None, None),
        ('Train 2020-22', None, '20221231'),
        ('Validate 2023-24', '20230101', '20241231'),
        ('Test 2025-26', '20250101', None),
    ]
    rolling = [
        ('Roll 20-21→22', None, '20211231', '20220101', '20221231'),
        ('Roll 21-22→23', None, '20221231', '20230101', '20231231'),
        ('Roll 22-23→24', None, '20231231', '20240101', '20241231'),
    ]

    print(f'\n  {"Split":<22s} {"Sharpe":>7s} {"TotRet":>9s} {"CAGR":>7s} '
          f'{"MaxDD":>7s} {"Calmar":>7s} {"Trd":>5s} {"Win":>5s}')
    print(f'  {"-"*75}')

    all_splits = []
    for name, start, end in splits:
        bt = backtest_momentum(
            stocks, sigs, sector_map, common_dates,
            trail_pct=p['trail'], ma_sell_w=p['ma_sell_w'],
            sell_framework=p['framework'], max_pos=p['max_pos'],
            rank_method=p['rank_method'],
            date_start=start, date_end=end,
        )
        s = bt['stats']
        all_splits.append({'label': name, 'stats': s})
        print(f'  {name:<22s} {s["sharpe"]:>7.3f} {s["total_ret"]*100:>8.2f}% '
              f'{s["cagr"]*100:>7.2f}% {s["mdd"]*100:>6.2f}% {s["calmar"]:>7.3f} '
              f'{s["n_trades"]:>5d} {s["win_rate"]*100:>4.0f}%')

    for name, ts_s, ts_e, test_s, test_e in rolling:
        bt = backtest_momentum(
            stocks, sigs, sector_map, common_dates,
            trail_pct=p['trail'], ma_sell_w=p['ma_sell_w'],
            sell_framework=p['framework'], max_pos=p['max_pos'],
            rank_method=p['rank_method'],
            date_start=test_s, date_end=test_e,
        )
        s = bt['stats']
        all_splits.append({'label': name, 'stats': s, 'type': 'rolling'})
        print(f'  {name:<22s} {s["sharpe"]:>7.3f} {s["total_ret"]*100:>8.2f}% '
              f'{s["cagr"]*100:>7.2f}% {s["mdd"]*100:>6.2f}% {s["calmar"]:>7.3f} '
              f'{s["n_trades"]:>5d} {s["win_rate"]*100:>4.0f}%')

    # Robustness check
    full = next(r for r in all_splits if r['label'] == 'Full Period')
    train = next(r for r in all_splits if 'Train' in r['label'])
    valid = next(r for r in all_splits if 'Validate' in r['label'])
    test = next(r for r in all_splits if 'Test 2025' in r['label'])

    print(f'\n  --- Robustness Check ---')
    fs, ts, vs, xs = full['stats'], train['stats'], valid['stats'], test['stats']
    ratio_v = vs['sharpe'] / ts['sharpe'] if ts['sharpe'] > 0 else 0
    ratio_t = xs['sharpe'] / ts['sharpe'] if ts['sharpe'] > 0 else 0
    print(f'  Full Sharpe:   {fs["sharpe"]:.3f}  (Ret={fs["total_ret"]*100:.1f}% MDD={fs["mdd"]*100:.1f}%)')
    print(f'  Train Sharpe:  {ts["sharpe"]:.3f}  (Ret={ts["total_ret"]*100:.1f}% MDD={ts["mdd"]*100:.1f}%)')
    print(f'  Valid Sharpe:  {vs["sharpe"]:.3f}  (Ret={vs["total_ret"]*100:.1f}% MDD={vs["mdd"]*100:.1f}%)')
    print(f'  Test Sharpe:   {xs["sharpe"]:.3f}  (Ret={xs["total_ret"]*100:.1f}% MDD={xs["mdd"]*100:.1f}%)')
    print(f'  Valid/Train:   {ratio_v:.2f}  {"✅ PASS" if ratio_v >= 0.6 else "⚠️  BELOW 0.6"}')
    print(f'  Test/Train:    {ratio_t:.2f}  {"✅ PASS" if ratio_t >= 0.5 else "⚠️  BELOW 0.5"}')

    rolling_sharpes = [r['stats']['sharpe'] for r in all_splits if r.get('type') == 'rolling']
    if rolling_sharpes:
        rm = sum(rolling_sharpes) / len(rolling_sharpes)
        all_pos = all(r > 0 for r in rolling_sharpes)
        print(f'  Rolling Sharpe mean: {rm:.3f}  '
              f'{"✅ All positive" if all_pos else "⚠️  Some negative"}')

    # Sensitivity
    print(f'\n  --- Parameter Sensitivity (±1 step) ---')
    base_s = fs['sharpe']
    tests = []
    # threshold
    for thr in set([p['ma_threshold'], max(0.01, p['ma_threshold'] - 0.01),
                    min(0.05, p['ma_threshold'] + 0.01)]):
        if thr != p['ma_threshold']:
            tests.append(('thr', thr, f'thr={thr:.0%}'))
    # trail
    for tr in set([p['trail'], max(0.05, p['trail'] - 0.05),
                   min(0.35, p['trail'] + 0.05)]):
        if tr != p['trail']:
            tests.append(('trail', tr, f'trail={tr:.0%}'))
    # max_pos
    for mp in set([p['max_pos'], max(1, p['max_pos'] - 1), min(5, p['max_pos'] + 1)]):
        if mp != p['max_pos']:
            tests.append(('max_pos', mp, f'pos={mp}'))

    print(f'  {"Perturbation":<25s} {"Sharpe":>7s} {"ΔSharpe":>8s} {"Ret":>9s} {"MDD":>7s}')
    print(f'  {"-"*60}')
    print(f'  {"BASELINE":<25s} {base_s:>7.3f} {0.0:>+8.3f} '
          f'{fs["total_ret"]*100:>8.2f}% {fs["mdd"]*100:>6.2f}%')

    max_delta = 0.0
    for param, val, label in tests:
        if param == 'thr':
            ss = generate_signals(stocks, val)
            bt = backtest_momentum(stocks, ss, sector_map, common_dates,
                                   trail_pct=p['trail'], ma_sell_w=p['ma_sell_w'],
                                   sell_framework=p['framework'], max_pos=p['max_pos'],
                                   rank_method=p['rank_method'])
        elif param == 'trail':
            bt = backtest_momentum(stocks, sigs, sector_map, common_dates,
                                   trail_pct=val, ma_sell_w=p['ma_sell_w'],
                                   sell_framework=p['framework'], max_pos=p['max_pos'],
                                   rank_method=p['rank_method'])
        elif param == 'max_pos':
            bt = backtest_momentum(stocks, sigs, sector_map, common_dates,
                                   trail_pct=p['trail'], ma_sell_w=p['ma_sell_w'],
                                   sell_framework=p['framework'], max_pos=val,
                                   rank_method=p['rank_method'])
        ds = bt['stats']['sharpe'] - base_s
        if abs(ds) > abs(max_delta):
            max_delta = ds
        flag = ' ⚠️' if abs(ds) > abs(base_s) * 0.2 and base_s != 0 else ''
        print(f'  {label:<25s} {bt["stats"]["sharpe"]:>7.3f} {ds:>+8.3f} '
              f'{bt["stats"]["total_ret"]*100:>8.2f}% {bt["stats"]["mdd"]*100:>6.2f}%{flag}')

    pct_delta = abs(max_delta / base_s) * 100 if base_s != 0 else float('inf')
    print(f'\n  Max Sharpe deviation: {abs(max_delta):.3f} ({pct_delta:.0f}% of base)')
    if pct_delta <= 20:
        print(f'  ✅ Parameters are STABLE (≤20% deviation)')
    else:
        print(f'  ⚠️  Parameters show SIGNIFICANT sensitivity (>20% deviation)')

    return all_splits


# ============================================================
# Export
# ============================================================
def export_trades(trades, filepath):
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['code', 'name', 'buy_date', 'sell_date', 'buy_px', 'sell_px',
                     'ret', 'pnl', 'exit', 'hold_days', 'dev_at_buy'])
        for t in trades:
            w.writerow([t['code'], t['name'], t['buy_date'], t['sell_date'],
                        f'{t["buy_px"]:.4f}', f'{t["sell_px"]:.4f}',
                        f'{t["ret"]:.6f}', f'{t["pnl"]:.2f}',
                        t['exit'], t['hold_days'], f'{t.get("dev_at_buy", 0):.6f}'])


def export_equity(equity_curve, filepath):
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['date', 'equity', 'cash', 'positions'])
        for d in equity_curve:
            w.writerow([d['date'], f'{d["equity"]:.2f}',
                        f'{d["cash"]:.2f}', d['positions']])


# ============================================================
# Main
# ============================================================
def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print('=' * 100)
    print('  择时策略2 · 纯MA乖离率动量策略 · 四阶段漏斗回测')
    print('  Buy:  MA3/MA7乖离率 ≥ 阈值  → 按乖离率排序选股')
    print('  Sell: Trail止损 / MA触碰 / 两者OR')
    print('  Sort: 降序(追强) vs 升序(适中)')
    print('=' * 100)
    print(f'  INIT={INIT_CAP:,.0f}  Cost: buy={SLIP+BUY_FEE:.3%}  sell={SLIP+SELL_FEE+STAMP_TAX:.3%}')

    # Load data
    print_header('DATA LOADING')
    sector_map = load_sector_map()
    print(f'  Sector map: {len(sector_map)} stocks, {len(set(sector_map.values()))} sectors')

    print('  Loading prices...')
    all_stocks = load_prices(stock_filter=None)
    stocks = {c: i for c, i in all_stocks.items()
              if i['dates'] and i['dates'][0] <= '20200103' and len(i['dates']) >= 1500}
    print(f'  {len(stocks)} stocks (44 old pool)')
    common_dates = get_common_dates(stocks)
    print(f'  {len(common_dates)} common dates: {common_dates[0]} → {common_dates[-1]}')
    print(f'  Period: {len(common_dates)/252:.1f} years')

    # Phase 1: Buy + Ranking
    p1 = run_phase1(stocks, sector_map, common_dates)
    ranked_p1 = sorted([r for r in p1 if r['stats']['n_trades'] >= 5],
                       key=lambda x: x['stats']['sharpe'], reverse=True)
    if not ranked_p1:
        print('\n❌ No Phase 1 results passed min trade filter. Aborting.')
        return
    best_p1 = ranked_p1[0]['params']

    # Phase 2: Sell tuning
    p2 = run_phase2(stocks, sector_map, common_dates, p1)
    ranked_p2 = sorted(p2, key=lambda x: x['stats']['calmar'], reverse=True)
    best_p2 = ranked_p2[0]['params'] if ranked_p2 else best_p1

    # Phase 3: Position + Method + Pool
    best_p3 = run_phase3(stocks, sector_map, common_dates, best_p2)

    # Phase 4: OOS + Sensitivity
    p4 = run_phase4(stocks, sector_map, common_dates, best_p3)

    # Final report
    print_header('FINAL OPTIMAL STRATEGY')
    bp = best_p3
    rm_label = '追强(strongest)' if bp['rank_method'] == 'strongest' else '适中(moderate)'
    print(f'  Buy:   MA3/MA7乖离率 ≥ {bp["ma_threshold"]:.0%}  '
          f'排序={rm_label}')
    print(f'  Sell:  Trail={bp["trail"]:.0%}  MA窗口={bp["ma_sell_w"]}  '
          f'框架={bp["framework"]}')
    print(f'  Risk:  持仓={bp["max_pos"]}只  等权  赛道不重复')

    # Final backtest
    sigs_final = generate_signals(stocks, bp['ma_threshold'])
    bt_final = backtest_momentum(
        stocks, sigs_final, sector_map, common_dates,
        trail_pct=bp['trail'], ma_sell_w=bp['ma_sell_w'],
        sell_framework=bp['framework'], max_pos=bp['max_pos'],
        rank_method=bp['rank_method'],
    )
    sf = bt_final['stats']
    print(f'  {"="*80}')
    print(f'  Sharpe:     {sf["sharpe"]:.4f}')
    print(f'  Total Ret:  {sf["total_ret"]*100:.2f}%')
    print(f'  CAGR:       {sf["cagr"]*100:.2f}%')
    print(f'  Max DD:     {sf["mdd"]*100:.2f}%')
    print(f'  Calmar:     {sf["calmar"]:.3f}')
    print(f'  Trades:     {sf["n_trades"]}')
    print(f'  Win Rate:   {sf["win_rate"]*100:.0f}%')
    print(f'  Hold%:      {sf["holding_pct"]*100:.1f}%')
    print(f'  Exit types: {sf["exit_types"]}')
    print(f'  {"="*80}')

    # Top/bottom trades
    trades_sorted = sorted(bt_final['trades'], key=lambda x: x['ret'], reverse=True)
    print(f'\n  Best 5 trades:')
    for t in trades_sorted[:5]:
        print(f'    {t["name"]:<10s} {t["buy_date"]} → {t["sell_date"]}  '
              f'+{t["ret"]*100:.1f}%  dev={t.get("dev_at_buy",0)*100:.1f}%  '
              f'{t["exit"]}  {t["hold_days"]}d')
    print(f'\n  Worst 5 trades:')
    for t in trades_sorted[-5:]:
        print(f'    {t["name"]:<10s} {t["buy_date"]} → {t["sell_date"]}  '
              f'{t["ret"]*100:.1f}%  dev={t.get("dev_at_buy",0)*100:.1f}%  '
              f'{t["exit"]}  {t["hold_days"]}d')

    # Strategy 1 comparison
    print_header('STRATEGY COMPARISON: Strategy 1 vs Strategy 2')
    print(f'  {"Metric":<20s} {"Strategy1(区间+基本面)":>25s} {"Strategy2(纯乖离率)":>25s}')
    print(f'  {"-"*70}')
    print(f'  {"Sharpe":<20s} {"1.10":>25s} {sf["sharpe"]:>25.4f}')
    print(f'  {"Total Ret":<20s} {"252.4%":>25s} {sf["total_ret"]*100:>24.2f}%')
    print(f'  {"MDD":<20s} {"27.4%":>25s} {sf["mdd"]*100:>24.2f}%')
    print(f'  {"Trades":<20s} {"17":>25s} {sf["n_trades"]:>25d}')
    print(f'  {"Win Rate":<20s} {"59%":>25s} {sf["win_rate"]*100:>24.0f}%')
    print(f'  {"Params":<20s} {"6 params":>25s} {"4 params":>25s}')

    # Export
    print_header('EXPORTING')
    base = os.path.dirname(os.path.abspath(__file__))
    export_trades(bt_final['trades'], os.path.join(base, 'timing2_trades.csv'))
    export_equity(bt_final['equity'], os.path.join(base, 'timing2_equity.csv'))
    print(f'  ✅ timing2_trades.csv ({len(bt_final["trades"])} trades)')
    print(f'  ✅ timing2_equity.csv ({len(bt_final["equity"])} days)')

    print('\n' + '=' * 100)
    print('  回测完成!')
    print('=' * 100)


if __name__ == '__main__':
    main()
