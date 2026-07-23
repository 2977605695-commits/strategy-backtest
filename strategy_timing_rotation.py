"""
择时策略 · 五阶段漏斗回测系统
=============================================
买入: N日区间震荡(涨幅≤X%, 跌幅≤Y%) + MA3/MA7乖离率≥阈值
卖出: Trail止损 / MA触碰 / 两者OR
轮动: 基本面季度排名 + 赛道去重 + 动态换仓

歧义澄清:
  1. 涨跌幅独立
  2. MA3在MA7上方+2%
  3. Trail/MA/两者 分别测试
  4. 当日收盘买入, 无合适空仓
  5. 当前季度数据排名
"""
import sys, io, os, math, json, csv, time, copy
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Import shared data loader
from data_loader import (
    load_prices, load_fundamentals, get_latest_fundamentals,
    zscore_fundamentals, calc_ma, get_common_dates
)

# ============================================================
# Constants
# ============================================================
INIT_CAP = 10_000_000
RISK_FREE = 0.025
TD = 252
SLIP = 0.003          # 0.3% 滑点
BUY_FEE = 0.00025     # 0.025% 佣金
SELL_FEE = 0.00025    # 0.025% 佣金
STAMP_TAX = 0.0005    # 0.05% 印花税(卖出)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")


# ============================================================
# Utility Functions
# ============================================================
def to_dash_date(d):
    """Convert YYYYMMDD → YYYY-MM-DD for fundamental comparison."""
    if len(str(d)) == 8:
        s = str(d)
        return f'{s[:4]}-{s[4:6]}-{s[6:8]}'
    return str(d)


def load_sector_map():
    """Load {code: sector} from the most recent fundamentals CSV."""
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
    """Compute performance statistics from equity curve and trade list."""
    vals = [d['equity'] for d in equity_curve]
    if len(vals) < 2 or vals[0] <= 0:
        return {'total_ret': 0, 'cagr': 0, 'ann_ret': 0, 'sharpe': 0,
                'mdd': 0, 'calmar': 0, 'n_trades': 0, 'win_rate': 0,
                'holding_pct': 0, 'exit_types': {}}

    init_val = vals[0]
    final_val = vals[-1]
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

    peak = vals[0]
    mdd = 0.0
    for v in vals:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > mdd:
            mdd = dd

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

    holding_days = sum(1 for d in equity_curve if d['positions'] > 0)

    return {
        'total_ret': total_ret, 'cagr': cagr, 'ann_ret': ann_ret,
        'sharpe': sharpe, 'mdd': mdd, 'calmar': calmar,
        'n_trades': n_trades, 'win_rate': win_rate,
        'holding_pct': holding_days / len(equity_curve) if equity_curve else 0,
        'exit_types': dict(exit_types),
        'n_days': len(equity_curve),
    }


# ============================================================
# Signal Generation
# ============================================================
def generate_signals(stocks, N, cap_up, cap_dn, ma_threshold):
    """
    Pre-compute buy signals for all stocks.
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

            # MA3/MA7 cross deviation (乖离率)
            if math.isnan(ma3[i]) or math.isnan(ma7[i]) or ma7[i] == 0:
                ma_cross_dev = float('nan')
            else:
                ma_cross_dev = (ma3[i] - ma7[i]) / abs(ma7[i])

            # Range-bound check (N-day window max/min vs today's close)
            if i < N:
                is_range_bound = False
            else:
                window = closes[i - N + 1:i + 1]
                cur = closes[i]
                if cur > 0 and min(window) > 0:
                    mx_up = (max(window) - cur) / cur
                    mx_dn = (cur - min(window)) / min(window)
                    is_range_bound = (mx_up <= cap_up and mx_dn <= cap_dn)
                else:
                    is_range_bound = False

            signal_buy = (is_range_bound and
                          not math.isnan(ma_cross_dev) and
                          ma_cross_dev >= ma_threshold)

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
def backtest_rotation(stocks, signals, fund_data, sector_map, common_dates,
                      trail_pct, ma_sell_w, sell_framework, max_pos,
                      date_start=None, date_end=None, verbose=False):
    """
    Portfolio-level rotation backtest with fundamental ranking.

    Args:
        stocks: {code: {name, dates, close, open, high, low, volume}}
        signals: {code: {date: {signal_buy, ma_cross_dev, ma7, ma10, ma14}}}
        fund_data: from load_fundamentals()
        sector_map: {code: sector}
        common_dates: sorted list of date strings
        trail_pct: trail stop % (e.g. 0.20 for 20% trail)
        ma_sell_w: MA window for touch exit (7/10/14)
        sell_framework: 'trail_only' | 'ma_only' | 'trail_or_ma'
        max_pos: max positions (1-5)
        date_start/date_end: optional date filter (YYYYMMDD strings)
    """
    # Filter date range
    dates = common_dates
    if date_start:
        dates = [d for d in dates if d >= date_start]
    if date_end:
        dates = [d for d in dates if d <= date_end]
    dates_dash = [to_dash_date(d) for d in dates]

    # State
    cash = INIT_CAP
    slot_cap = INIT_CAP / max_pos
    positions = {}        # code -> {shares, cost, buy_price, peak, buy_date, buy_idx}
    daily_equity = []
    trades = []

    # Build date→index lookup per stock
    stock_idx = {}
    for code in stocks:
        stock_idx[code] = {d: i for i, d in enumerate(stocks[code]['dates'])}

    # Quarterly ranking state
    current_snapshot = None
    current_ranking = []  # [(code, score), ...] sorted desc

    ma_field = f'ma{ma_sell_w}'

    for day_i, date_str in enumerate(dates):
        date_dash = dates_dash[day_i]

        # 1. Update quarterly ranking if new data published
        latest = get_latest_fundamentals(fund_data, date_dash)
        if latest != current_snapshot:
            current_snapshot = latest
            zs = zscore_fundamentals(latest)
            ranking_list = [(c, zs[c]['score']) for c in zs]
            ranking_list.sort(key=lambda x: x[1], reverse=True)
            current_ranking = ranking_list

        # 2. Check exits for current positions
        sold_codes = set()
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
                should_sell = True
                exit_reason = 'trail'
            elif sell_framework == 'ma_only' and ma_hit:
                should_sell = True
                exit_reason = f'ma{ma_sell_w}'
            elif sell_framework == 'trail_or_ma':
                if trail_hit:
                    should_sell = True
                    exit_reason = 'trail'
                elif ma_hit:
                    should_sell = True
                    exit_reason = f'ma{ma_sell_w}'

            if should_sell:
                sell_px = px * (1 - SLIP - SELL_FEE - STAMP_TAX)
                proceeds = pos['shares'] * sell_px
                pnl = proceeds - pos['shares'] * pos['buy_price']
                cash += proceeds

                trades.append({
                    'code': code, 'name': stocks[code]['name'],
                    'buy_date': pos['buy_date'], 'sell_date': date_str,
                    'buy_px': pos['buy_price'],
                    'sell_px': sell_px,
                    'ret': (sell_px - pos['buy_price']) / pos['buy_price'] if pos['buy_price'] > 0 else 0,
                    'pnl': pnl, 'exit': exit_reason,
                    'hold_days': day_i - pos['buy_idx'],
                    'peak': pos['peak'],
                })
                del positions[code]
                sold_codes.add(code)

        # 3. Rotation: fill empty slots
        if current_ranking and cash >= slot_cap * 0.99:
            held_codes = set(positions.keys())
            held_sectors = {sector_map.get(c, '') for c in held_codes}

            while len(positions) < max_pos and cash >= slot_cap * 0.99:
                selected = None
                for cand_code, cand_score in current_ranking:
                    if cand_code in held_codes:
                        continue
                    if cand_code not in stock_idx:
                        continue
                    cand_sector = sector_map.get(cand_code, '')
                    if cand_sector and cand_sector in held_sectors:
                        continue
                    # Check buy signal
                    sig = signals.get(cand_code, {}).get(date_str, {})
                    if sig.get('signal_buy', False):
                        selected = cand_code
                        break

                if selected is None:
                    break

                # Execute buy
                si = stock_idx[selected][date_str]
                buy_px_raw = stocks[selected]['close'][si]
                buy_px = buy_px_raw * (1 + SLIP + BUY_FEE)
                shares = slot_cap / buy_px

                cash -= slot_cap
                positions[selected] = {
                    'shares': shares,
                    'cost': slot_cap,
                    'buy_price': buy_px,
                    'peak': buy_px_raw,
                    'buy_date': date_str,
                    'buy_idx': day_i,
                }
                held_codes.add(selected)
                held_sectors.add(sector_map.get(selected, ''))

        # 4. Risk-free interest on idle cash
        cash *= (1 + RISK_FREE / TD)

        # 5. Mark-to-market
        pos_val = 0.0
        for code, pos in positions.items():
            if code in stock_idx and date_str in stock_idx[code]:
                si = stock_idx[code][date_str]
                pos_val += pos['shares'] * stocks[code]['close'][si]

        total_val = cash + pos_val
        daily_equity.append({
            'date': date_str,
            'equity': total_val,
            'cash': cash,
            'positions': len(positions),
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
                'peak': pos['peak'],
            })
    positions.clear()

    if daily_equity:
        daily_equity[-1]['equity'] = cash
        daily_equity[-1]['cash'] = cash
        daily_equity[-1]['positions'] = 0

    stats = compute_stats(daily_equity, trades)
    return {'equity': daily_equity, 'trades': trades, 'stats': stats}


# ============================================================
# Phase Runners
# ============================================================

def print_header(title):
    print(f'\n{"="*100}')
    print(f'  {title}')
    print(f'{"="*100}')


def print_result_table(results, sort_key='sharpe', top_n=30, min_trades=0):
    """Print ranked result table."""
    filtered = [r for r in results if r['stats']['n_trades'] >= min_trades]
    filtered.sort(key=lambda x: x['stats'][sort_key], reverse=True)

    s = results[0]['stats']
    print(f'  {"Rank":<4s} {"Params":<55s} '
          f'{"Sharpe":>7s} {"TotRet":>9s} {"AnnRet":>8s} {"MaxDD":>7s} '
          f'{"Calmar":>7s} {"Trd":>5s} {"Win":>5s} {"Hold%":>6s}')
    print(f'  {"-"*110}')
    for rank, r in enumerate(filtered[:top_n], 1):
        s = r['stats']
        print(f'  {rank:<4d} {r["label"]:<55s} '
              f'{s["sharpe"]:>7.3f} {s["total_ret"]*100:>8.2f}% {s["cagr"]*100:>7.2f}% '
              f'{s["mdd"]*100:>6.2f}% {s["calmar"]:>7.3f} '
              f'{s["n_trades"]:>5d} {s["win_rate"]*100:>4.0f}% {s["holding_pct"]*100:>5.1f}%')
    return filtered


# ============================================================
# Phase 1: Buy Signal Grid Search
# ============================================================
def run_phase1(stocks, fund_data, sector_map, common_dates):
    """
    Grid: N × cap_up × cap_dn × ma_threshold
    Fixed: Trail=20%, trail_only, max_pos=5, old pool (44 stocks)
    """
    print_header('PHASE 1: Buy Signal Coarse Screening')

    Ns = [5, 7, 10, 14]
    caps_up = [0.05, 0.07, 0.10]
    caps_dn = [0.05, 0.07, 0.10]
    ma_thresholds = [0.015, 0.02, 0.025, 0.03]

    TOTAL = len(Ns) * len(caps_up) * len(caps_dn) * len(ma_thresholds)
    print(f'  Grid: N∈{Ns}  up∈{[f"{c:.0%}" for c in caps_up]}  dn∈{[f"{c:.0%}" for c in caps_dn]}  '
          f'ma_thr∈{[f"{t:.1%}" for t in ma_thresholds]}')
    print(f'  Fixed: Trail=20%, trail_only, max_pos=5, pool=old(44 stocks)')
    print(f'  Total: {TOTAL} combos\n')

    # Pre-compute & cache all signal sets
    signal_cache = {}
    print('  Pre-computing signals...')
    for N in Ns:
        for cap_up in caps_up:
            for cap_dn in caps_dn:
                for ma_thr in ma_thresholds:
                    key = (N, cap_up, cap_dn, ma_thr)
                    sigs = generate_signals(stocks, N, cap_up, cap_dn, ma_thr)
                    signal_cache[key] = sigs
                    # Count total buy signals
                    total = sum(
                        sum(1 for s in sigs[c].values() if s['signal_buy'])
                        for c in sigs
                    )
                    print(f'    N={N} up={cap_up:.0%} dn={cap_dn:.0%} thr={ma_thr:.1%} → {total} buy signals')

    # Run backtests
    results = []
    count = 0
    print(f'\n  Running {TOTAL} backtests...')
    for N in Ns:
        for cap_up in caps_up:
            for cap_dn in caps_dn:
                for ma_thr in ma_thresholds:
                    count += 1
                    key = (N, cap_up, cap_dn, ma_thr)
                    sigs = signal_cache[key]

                    bt = backtest_rotation(
                        stocks, sigs, fund_data, sector_map, common_dates,
                        trail_pct=0.20, ma_sell_w=14, sell_framework='trail_only',
                        max_pos=5,
                    )

                    label = (f'N={N} up={cap_up:.0%} dn={cap_dn:.0%} thr={ma_thr:.1%} '
                             f'T20% trail-only')
                    results.append({
                        'label': label,
                        'params': {'N': N, 'cap_up': cap_up, 'cap_dn': cap_dn,
                                   'ma_threshold': ma_thr},
                        'stats': bt['stats'],
                    })

                    if count % 20 == 1 or count == TOTAL:
                        s = bt['stats']
                        print(f'    [{count:>3d}/{TOTAL}] {label:<45s} '
                              f'S={s["sharpe"]:>6.3f} Ret={s["total_ret"]*100:>7.2f}% '
                              f'DD={s["mdd"]*100:>5.2f}% Trd={s["n_trades"]:>3d} '
                              f'Win={s["win_rate"]*100:>4.0f}% Hold={s["holding_pct"]*100:>4.1f}%')

    print(f'\n  --- Phase 1 Results (sorted by Sharpe, min 15 trades) ---')
    ranked = print_result_table(results, sort_key='sharpe', min_trades=15)

    # Best params detail
    if ranked:
        best = ranked[0]
        s = best['stats']
        p = best['params']
        print(f'\n  🏆 Best: {best["label"]}')
        print(f'     Sharpe={s["sharpe"]:.4f}  Ret={s["total_ret"]*100:.2f}%  '
              f'CAGR={s["cagr"]*100:.2f}%  MDD={s["mdd"]*100:.2f}%  '
              f'Calmar={s["calmar"]:.3f}  Trd={s["n_trades"]}  Win={s["win_rate"]*100:.0f}%')
        print(f'     Exit types: {s["exit_types"]}')
        print(f'     Hold days: {s["holding_pct"]*100:.1f}% of {s["n_days"]} days')

    return results


# ============================================================
# Phase 2: Sell Signal Fine-Tuning
# ============================================================
def run_phase2(stocks, fund_data, sector_map, common_dates, phase1_results):
    """
    Grid: Trail × MA_window × sell_framework
    Fixed: Top 3 buy params from Phase 1
    """
    print_header('PHASE 2: Sell Signal Fine-Tuning')

    # Get top 3 unique buy param sets from Phase 1
    ranked = [r for r in phase1_results if r['stats']['n_trades'] >= 15]
    ranked.sort(key=lambda x: x['stats']['sharpe'], reverse=True)
    top_buy_params = []
    seen = set()
    for r in ranked:
        key = (r['params']['N'], r['params']['cap_up'], r['params']['cap_dn'],
               r['params']['ma_threshold'])
        if key not in seen:
            seen.add(key)
            top_buy_params.append(r['params'])
            if len(top_buy_params) >= 3:
                break

    trails = [0.10, 0.15, 0.20, 0.25, 0.30]
    ma_windows = [7, 10, 14]
    frameworks = ['trail_only', 'ma_only', 'trail_or_ma']

    print(f'  Sell Grid: Trail∈{[f"{t:.0%}" for t in trails]}  '
          f'MA∈{ma_windows}  Framework∈{frameworks}')
    print(f'  Using Top {len(top_buy_params)} buy param sets from Phase 1')
    for i, p in enumerate(top_buy_params, 1):
        print(f'    #{i}: N={p["N"]} up={p["cap_up"]:.0%} '
              f'dn={p["cap_dn"]:.0%} thr={p["ma_threshold"]:.1%}')

    # Strategy: test all 45 sell combos with the #1 buy param first,
    # then verify top sell combos with #2 and #3 buy params
    all_results = []
    primary_buy = top_buy_params[0]

    # Pre-compute signals for all buy param sets we'll use
    signal_cache = {}
    for bp in top_buy_params:
        key = (bp['N'], bp['cap_up'], bp['cap_dn'], bp['ma_threshold'])
        signal_cache[key] = generate_signals(
            stocks, bp['N'], bp['cap_up'], bp['cap_dn'], bp['ma_threshold']
        )

    # Full grid with primary buy param
    print(f'\n  --- Full Sell Grid (buy param #1: '
          f'N={primary_buy["N"]} up={primary_buy["cap_up"]:.0%} '
          f'dn={primary_buy["cap_dn"]:.0%} thr={primary_buy["ma_threshold"]:.1%}) ---')

    pk = (primary_buy['N'], primary_buy['cap_up'], primary_buy['cap_dn'],
          primary_buy['ma_threshold'])
    sigs = signal_cache[pk]

    count = 0
    TOTAL = len(trails) * len(ma_windows) * len(frameworks)
    primary_results = []
    for trail in trails:
        for ma_w in ma_windows:
            for fw in frameworks:
                count += 1
                bt = backtest_rotation(
                    stocks, sigs, fund_data, sector_map, common_dates,
                    trail_pct=trail, ma_sell_w=ma_w, sell_framework=fw,
                    max_pos=5,
                )
                label = f'T={trail:.0%} MA{ma_w} {fw}'
                primary_results.append({
                    'label': label,
                    'params': {**primary_buy, 'trail': trail, 'ma_sell_w': ma_w,
                               'framework': fw},
                    'stats': bt['stats'],
                })
                if count % 10 == 1 or count == TOTAL:
                    s = bt['stats']
                    print(f'    [{count:>2d}/{TOTAL}] {label:<25s} '
                          f'S={s["sharpe"]:>7.3f} Ret={s["total_ret"]*100:>7.2f}% '
                          f'DD={s["mdd"]*100:>6.2f}% Calmar={s["calmar"]:>7.3f} '
                          f'Trd={s["n_trades"]:>3d} Win={s["win_rate"]*100:>4.0f}%')

    all_results.extend(primary_results)

    # Top 2 sell combos from primary
    primary_ranked = sorted(primary_results, key=lambda x: x['stats']['calmar'], reverse=True)
    top_sell = primary_ranked[:2]

    print(f'\n  --- Verification with buy params #2 and #3 ---')
    for i, bp in enumerate(top_buy_params[1:], 2):
        pk = (bp['N'], bp['cap_up'], bp['cap_dn'], bp['ma_threshold'])
        sigs = signal_cache[pk]
        for ts in top_sell:
            bt = backtest_rotation(
                stocks, sigs, fund_data, sector_map, common_dates,
                trail_pct=ts['params']['trail'], ma_sell_w=ts['params']['ma_sell_w'],
                sell_framework=ts['params']['framework'], max_pos=5,
            )
            label = (f'buy#{i} N={bp["N"]} up={bp["cap_up"]:.0%} '
                     f'+ T={ts["params"]["trail"]:.0%} '
                     f'MA{ts["params"]["ma_sell_w"]} {ts["params"]["framework"]}')
            s = bt['stats']
            print(f'    {label:<55s}  '
                  f'S={s["sharpe"]:>7.3f} Ret={s["total_ret"]*100:>7.2f}% '
                  f'DD={s["mdd"]*100:>6.2f}% Calmar={s["calmar"]:>7.3f} '
                  f'Trd={s["n_trades"]:>3d}')
            all_results.append({
                'label': label, 'params': {**bp, **ts['params']}, 'stats': s,
            })

    print(f'\n  --- Phase 2 Results (sorted by Calmar) ---')
    ranked_final = print_result_table(all_results, sort_key='calmar', top_n=20)

    if ranked_final:
        best = ranked_final[0]
        s = best['stats']
        print(f'\n  🏆 Best Sell Combo: {best["label"]}')
        print(f'     Sharpe={s["sharpe"]:.4f}  Calmar={s["calmar"]:.3f}  '
              f'Ret={s["total_ret"]*100:.2f}%  MDD={s["mdd"]*100:.2f}%')
        print(f'     Exit: {s["exit_types"]}')
        p = best['params']
        print(f'     Params: N={p["N"]} up={p["cap_up"]:.0%} dn={p["cap_dn"]:.0%} '
              f'thr={p["ma_threshold"]:.1%} T={p["trail"]:.0%} '
              f'MA{p["ma_sell_w"]} {p["framework"]}')

    return all_results


# ============================================================
# Phase 3: Position Count Optimization
# ============================================================
def run_phase3(stocks, fund_data, sector_map, common_dates, best_params):
    """
    Test max_pos ∈ {1, 2, 3, 4, 5}
    """
    print_header('PHASE 3: Position Count Optimization')

    p = best_params
    # Pre-compute signals
    sigs = generate_signals(stocks, p['N'], p['cap_up'], p['cap_dn'], p['ma_threshold'])

    results = []
    for max_pos in [1, 2, 3, 4, 5]:
        bt = backtest_rotation(
            stocks, sigs, fund_data, sector_map, common_dates,
            trail_pct=p['trail'], ma_sell_w=p['ma_sell_w'],
            sell_framework=p['framework'], max_pos=max_pos,
        )
        s = bt['stats']
        results.append({'label': f'max_pos={max_pos}', 'params': {**p, 'max_pos': max_pos},
                        'stats': s})

    print(f'  Fixed: N={p["N"]} up={p["cap_up"]:.0%} dn={p["cap_dn"]:.0%} '
          f'thr={p["ma_threshold"]:.1%} T={p["trail"]:.0%} '
          f'MA{p["ma_sell_w"]} {p["framework"]}')
    print(f'\n  {"Pos":<5s} {"Sharpe":>7s} {"TotRet":>9s} {"CAGR":>7s} '
          f'{"MaxDD":>7s} {"Calmar":>7s} {"Trd":>5s} {"Win":>5s} {"Hold%":>6s} {"CashUtil":>8s}')
    print(f'  {"-"*75}')

    for r in results:
        s = r['stats']
        # Cash utilization: average % of capital actually invested
        avg_invested = 1.0 - (sum(d['cash'] for d in r['stats'].get('_daily', [])
                                  ) / len(r['stats'].get('_daily', [{'cash': 0}])) / INIT_CAP)
        cash_util = s['holding_pct']
        print(f'  {r["params"]["max_pos"]:<5d} '
              f'{s["sharpe"]:>7.3f} {s["total_ret"]*100:>8.2f}% {s["cagr"]*100:>7.2f}% '
              f'{s["mdd"]*100:>6.2f}% {s["calmar"]:>7.3f} '
              f'{s["n_trades"]:>5d} {s["win_rate"]*100:>4.0f}% {s["holding_pct"]*100:>5.1f}%')

    results.sort(key=lambda x: x['stats']['sharpe'], reverse=True)
    best_pos = results[0]
    print(f'\n  🏆 Best: max_pos={best_pos["params"]["max_pos"]} '
          f'Sharpe={best_pos["stats"]["sharpe"]:.4f} '
          f'Ret={best_pos["stats"]["total_ret"]*100:.2f}% '
          f'MDD={best_pos["stats"]["mdd"]*100:.2f}%')

    return results


# ============================================================
# Phase 4: Stock Pool Comparison
# ============================================================
def run_phase4(fund_data, sector_map, best_params):
    """
    Compare: 44 old stocks vs 64 all stocks vs 70 full
    """
    print_header('PHASE 4: Stock Pool Comparison')

    pools = [
        ('44-old', 'old'),
        ('64-all', 'all64'),
        ('70-full', None),
    ]

    results = []
    for pool_name, pool_filter in pools:
        stocks = load_prices(stock_filter=pool_filter)
        if not stocks:
            print(f'  {pool_name}: No stocks loaded, skipping')
            continue

        cd = get_common_dates(stocks)
        print(f'\n  Pool: {pool_name} ({len(stocks)} stocks, {len(cd)} common dates)')

        p = best_params
        sigs = generate_signals(stocks, p['N'], p['cap_up'], p['cap_dn'], p['ma_threshold'])
        bt = backtest_rotation(
            stocks, sigs, fund_data, sector_map, cd,
            trail_pct=p['trail'], ma_sell_w=p['ma_sell_w'],
            sell_framework=p['framework'], max_pos=p.get('max_pos', 5),
        )
        s = bt['stats']
        results.append({'label': f'Pool={pool_name}', 'params': {**p, 'pool': pool_name},
                        'stats': s, 'n_stocks': len(stocks)})

    print(f'\n  {"Pool":<10s} {"N":>5s} {"Sharpe":>7s} {"TotRet":>9s} {"CAGR":>7s} '
          f'{"MaxDD":>7s} {"Calmar":>7s} {"Trd":>5s} {"Win":>5s} {"Hold%":>6s}')
    print(f'  {"-"*75}')
    for r in results:
        s = r['stats']
        print(f'  {r["params"]["pool"]:<10s} {r["n_stocks"]:>5d} '
              f'{s["sharpe"]:>7.3f} {s["total_ret"]*100:>8.2f}% {s["cagr"]*100:>7.2f}% '
              f'{s["mdd"]*100:>6.2f}% {s["calmar"]:>7.3f} '
              f'{s["n_trades"]:>5d} {s["win_rate"]*100:>4.0f}% {s["holding_pct"]*100:>5.1f}%')

    return results


# ============================================================
# Phase 5: Out-of-Sample Validation
# ============================================================
def run_phase5(stocks, fund_data, sector_map, common_dates, best_params):
    """
    Validate robustness: train/validate/test split + rolling windows
    """
    print_header('PHASE 5: Out-of-Sample Validation')

    p = best_params
    sigs = generate_signals(stocks, p['N'], p['cap_up'], p['cap_dn'], p['ma_threshold'])

    # Time splits
    splits = [
        ('Full Period', None, None),
        ('Train 2020-22', None, '20221231'),
        ('Validate 2023-24', '20230101', '20241231'),
        ('Test 2025-26', '20250101', None),
    ]

    # Rolling 2-year train → 1-year test
    rolling = [
        ('Roll 20-21→22', None, '20211231', '20220101', '20221231'),
        ('Roll 21-22→23', None, '20221231', '20230101', '20231231'),
        ('Roll 22-23→24', None, '20231231', '20240101', '20241231'),
    ]

    print(f'\n  {"Split":<22s} {"Sharpe":>7s} {"TotRet":>9s} {"CAGR":>7s} '
          f'{"MaxDD":>7s} {"Calmar":>7s} {"Trd":>5s} {"Win":>5s}')
    print(f'  {"-"*70}')

    all_results = []

    # Fixed splits
    for name, start, end in splits:
        bt = backtest_rotation(
            stocks, sigs, fund_data, sector_map, common_dates,
            trail_pct=p['trail'], ma_sell_w=p['ma_sell_w'],
            sell_framework=p['framework'], max_pos=p.get('max_pos', 5),
            date_start=start, date_end=end,
        )
        s = bt['stats']
        all_results.append({'label': name, 'stats': s, 'type': 'fixed'})
        print(f'  {name:<22s} {s["sharpe"]:>7.3f} {s["total_ret"]*100:>8.2f}% '
              f'{s["cagr"]*100:>7.2f}% {s["mdd"]*100:>6.2f}% {s["calmar"]:>7.3f} '
              f'{s["n_trades"]:>5d} {s["win_rate"]*100:>4.0f}%')

    # Rolling windows
    for name, train_start, train_end, test_start, test_end in rolling:
        bt = backtest_rotation(
            stocks, sigs, fund_data, sector_map, common_dates,
            trail_pct=p['trail'], ma_sell_w=p['ma_sell_w'],
            sell_framework=p['framework'], max_pos=p.get('max_pos', 5),
            date_start=test_start, date_end=test_end,
        )
        s = bt['stats']
        all_results.append({'label': name, 'stats': s, 'type': 'rolling'})
        print(f'  {name:<22s} {s["sharpe"]:>7.3f} {s["total_ret"]*100:>8.2f}% '
              f'{s["cagr"]*100:>7.2f}% {s["mdd"]*100:>6.2f}% {s["calmar"]:>7.3f} '
              f'{s["n_trades"]:>5d} {s["win_rate"]*100:>4.0f}%')

    # Robustness check
    full = next(r for r in all_results if r['label'] == 'Full Period')
    train = next(r for r in all_results if 'Train' in r['label'])
    valid = next(r for r in all_results if 'Validate' in r['label'])
    test = next(r for r in all_results if 'Test 2025' in r['label'])

    print(f'\n  --- Robustness Check ---')
    fs = full['stats']; ts = train['stats']; vs = valid['stats']; xs = test['stats']
    ratio_v = vs['sharpe'] / ts['sharpe'] if ts['sharpe'] > 0 else 0
    ratio_t = xs['sharpe'] / ts['sharpe'] if ts['sharpe'] > 0 else 0
    print(f'  Full Sharpe:   {fs["sharpe"]:.3f}  (Ret={fs["total_ret"]*100:.1f}% MDD={fs["mdd"]*100:.1f}%)')
    print(f'  Train Sharpe:  {ts["sharpe"]:.3f}  (Ret={ts["total_ret"]*100:.1f}% MDD={ts["mdd"]*100:.1f}%)')
    print(f'  Valid Sharpe:  {vs["sharpe"]:.3f}  (Ret={vs["total_ret"]*100:.1f}% MDD={vs["mdd"]*100:.1f}%)')
    print(f'  Test Sharpe:   {xs["sharpe"]:.3f}  (Ret={xs["total_ret"]*100:.1f}% MDD={xs["mdd"]*100:.1f}%)')
    print(f'  Valid/Train:   {ratio_v:.2f}  {"✅ PASS" if ratio_v >= 0.6 else "⚠️  BELOW 0.6"}')
    print(f'  Test/Train:    {ratio_t:.2f}  {"✅ PASS" if ratio_t >= 0.5 else "⚠️  BELOW 0.5"}')

    # Rolling window consistency
    rolling_results = [r for r in all_results if r['type'] == 'rolling']
    if rolling_results:
        rs = [r['stats']['sharpe'] for r in rolling_results]
        roll_mean = sum(rs) / len(rs)
        roll_std = (sum((r - roll_mean) ** 2 for r in rs) / len(rs)) ** 0.5
        print(f'\n  Rolling Window Sharpe: mean={roll_mean:.3f} std={roll_std:.3f}')
        all_positive = all(r > 0 for r in rs)
        print(f'  All windows positive: {"✅ YES" if all_positive else "⚠️  NO"}')

    return all_results


# ============================================================
# Sensitivity Analysis (Phase 6 optional)
# ============================================================
def run_sensitivity(stocks, fund_data, sector_map, common_dates, best_params):
    """±1 perturbation around optimal params to check stability."""
    print_header('PHASE 6 (Optional): Parameter Sensitivity')

    p = best_params

    # Test ±1 for each continuous param
    tests = []
    # N perturbation
    for N in set([p['N'], max(5, p['N'] - 2), min(14, p['N'] + 2)]):
        if N != p['N']:
            tests.append({**p, 'N': N, 'label': f'N={N}'})
    # cap_up perturbation
    for cu in set([p['cap_up'], max(0.03, p['cap_up'] - 0.02), min(0.15, p['cap_up'] + 0.02)]):
        if cu != p['cap_up']:
            tests.append({**p, 'cap_up': cu, 'label': f'cap_up={cu:.0%}'})
    # trail perturbation
    for tr in set([p['trail'], max(0.05, p['trail'] - 0.05), min(0.35, p['trail'] + 0.05)]):
        if tr != p['trail']:
            tests.append({**p, 'trail': tr, 'label': f'trail={tr:.0%}'})
    # ma_threshold perturbation
    for thr in set([p['ma_threshold'], max(0.01, p['ma_threshold'] - 0.005),
                    min(0.04, p['ma_threshold'] + 0.005)]):
        if thr != p['ma_threshold']:
            tests.append({**p, 'ma_threshold': thr, 'label': f'thr={thr:.1%}'})
    # Baseline
    tests.insert(0, {**p, 'label': 'BASELINE'})

    # Deduplicate
    seen = set()
    unique_tests = []
    for t in tests:
        k = (t['N'], t['cap_up'], t['cap_dn'], t['ma_threshold'], t['trail'])
        if k not in seen:
            seen.add(k)
            unique_tests.append(t)

    results = []
    base_sharpe = None
    for tp in unique_tests:
        sigs = generate_signals(stocks, tp['N'], tp['cap_up'], tp['cap_dn'],
                                tp['ma_threshold'])
        bt = backtest_rotation(
            stocks, sigs, fund_data, sector_map, common_dates,
            trail_pct=tp['trail'], ma_sell_w=p['ma_sell_w'],
            sell_framework=p['framework'], max_pos=p.get('max_pos', 5),
        )
        s = bt['stats']
        results.append({'label': tp['label'], 'stats': s})
        if tp['label'] == 'BASELINE':
            base_sharpe = s['sharpe']

    print(f'  {"Perturbation":<30s} {"Sharpe":>7s} {"ΔSharpe":>8s} {"Ret":>9s} {"MDD":>7s}')
    print(f'  {"-"*65}')
    for r in results:
        s = r['stats']
        delta = s['sharpe'] - base_sharpe if base_sharpe else 0
        flag = ' ⚠️' if abs(delta) > abs(base_sharpe) * 0.2 and base_sharpe else ''
        print(f'  {r["label"]:<30s} {s["sharpe"]:>7.3f} {delta:>+8.3f} '
              f'{s["total_ret"]*100:>8.2f}% {s["mdd"]*100:>6.2f}%{flag}')

    if base_sharpe:
        max_delta = max(abs(r['stats']['sharpe'] - base_sharpe) for r in results)
        pct_delta = max_delta / abs(base_sharpe) * 100
        print(f'\n  Max Sharpe deviation: {max_delta:.3f} ({pct_delta:.0f}% of base)')
        if pct_delta <= 20:
            print(f'  ✅ Parameters are STABLE (≤20% deviation)')
        else:
            print(f'  ⚠️  Parameters show SIGNIFICANT sensitivity (>20% deviation)')

    return results


# ============================================================
# Trade Log Export
# ============================================================
def export_trades(trades, filepath):
    """Export trade log to CSV."""
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['code', 'name', 'buy_date', 'sell_date', 'buy_px', 'sell_px',
                     'ret', 'pnl', 'exit', 'hold_days', 'peak'])
        for t in trades:
            w.writerow([t['code'], t['name'], t['buy_date'], t['sell_date'],
                        f'{t["buy_px"]:.4f}', f'{t["sell_px"]:.4f}',
                        f'{t["ret"]:.6f}', f'{t["pnl"]:.2f}',
                        t['exit'], t['hold_days'], f'{t.get("peak", 0):.4f}'])


def export_equity(equity_curve, filepath):
    """Export equity curve to CSV."""
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
    print('  择时策略 · 五阶段漏斗回测系统')
    print('  Buy: N-day range-bound + MA3/MA7乖离率≥阈值')
    print('  Sell: Trail止损 / MA触碰 / 两者OR')
    print('  Rotation: 基本面季度排名 + 赛道去重')
    print('=' * 100)
    print(f'  INIT={INIT_CAP:,.0f}  SLIP={SLIP:.1%}  BUY_FEE={BUY_FEE:.3%}  '
          f'SELL_FEE={SELL_FEE:.3%}  STAMP={STAMP_TAX:.3%}  RF={RISK_FREE:.1%}')
    print(f'  Cost: buy={SLIP+BUY_FEE:.3%}  sell={SLIP+SELL_FEE+STAMP_TAX:.3%}')

    # Load data
    print_header('DATA LOADING')

    print('  Loading sector map from fundamentals...')
    sector_map = load_sector_map()
    print(f'    {len(sector_map)} stocks with sector mapping')
    print(f'    {len(set(sector_map.values()))} unique sectors')

    print('  Loading fundamentals...')
    fund_data = load_fundamentals()
    print(f'    {len(fund_data)} stocks with quarterly data')

    print('  Loading prices (all 70 stocks, then filter to 44 old)...')
    all_stocks = load_prices(stock_filter=None)
    # Manual filter: pre-2020 stocks with >=1500 bars
    # (data_loader's 'old' filter has a date-format mismatch bug)
    stocks = {}
    for code, info in all_stocks.items():
        if info['dates'] and info['dates'][0] <= '20200103' and len(info['dates']) >= 1500:
            stocks[code] = info
    print(f'    {len(stocks)} stocks loaded (filtered from {len(all_stocks)} total)')
    common_dates = get_common_dates(stocks)
    print(f'    {len(common_dates)} common trading dates')
    print(f'    Range: {common_dates[0]} → {common_dates[-1]}')
    years = len(common_dates) / 252
    print(f'    Period: {years:.1f} years')

    # Run phases
    # Phase 1: Buy signal screening
    p1_results = run_phase1(stocks, fund_data, sector_map, common_dates)

    # Get best buy params
    ranked_p1 = [r for r in p1_results if r['stats']['n_trades'] >= 15]
    ranked_p1.sort(key=lambda x: x['stats']['sharpe'], reverse=True)
    if not ranked_p1:
        print('\n❌ No Phase 1 results passed the 15-trade minimum. Aborting.')
        return
    best_p1 = ranked_p1[0]['params']

    # Phase 2: Sell signal tuning
    p2_results = run_phase2(stocks, fund_data, sector_map, common_dates, p1_results)

    ranked_p2 = sorted(p2_results, key=lambda x: x['stats']['calmar'], reverse=True)
    best_p2 = ranked_p2[0]['params'] if ranked_p2 else best_p1

    # Phase 3: Position count
    p3_results = run_phase3(stocks, fund_data, sector_map, common_dates, best_p2)
    best_p3 = {**best_p2, 'max_pos': sorted(p3_results,
                                             key=lambda x: x['stats']['sharpe'],
                                             reverse=True)[0]['params']['max_pos']}

    # Phase 4: Pool comparison
    p4_results = run_phase4(fund_data, sector_map, best_p3)

    # Phase 5: OOS
    p5_results = run_phase5(stocks, fund_data, sector_map, common_dates, best_p3)

    # Phase 6: Sensitivity (optional)
    sen_results = run_sensitivity(stocks, fund_data, sector_map, common_dates, best_p3)

    # Final report
    print_header('FINAL OPTIMAL STRATEGY')
    bp = best_p3
    print(f'  Buy:   N={bp["N"]} 涨幅≤{bp["cap_up"]:.0%}  跌幅≤{bp["cap_dn"]:.0%}  '
          f'MA3/MA7乖离率≥{bp["ma_threshold"]:.1%}')
    print(f'  Sell:  Trail={bp["trail"]:.0%}  MA窗口={bp.get("ma_sell_w", "?")}  '
          f'框架={bp["framework"]}')
    print(f'  Risk:  持仓={bp["max_pos"]}只  等权  赛道不重复')

    # Run final backtest with best params
    print(f'\n  Running final backtest with optimal params...')
    sigs_final = generate_signals(stocks, bp['N'], bp['cap_up'], bp['cap_dn'],
                                   bp['ma_threshold'])
    bt_final = backtest_rotation(
        stocks, sigs_final, fund_data, sector_map, common_dates,
        trail_pct=bp['trail'], ma_sell_w=bp['ma_sell_w'],
        sell_framework=bp['framework'], max_pos=bp['max_pos'],
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
              f'+{t["ret"]*100:.1f}%  {t["exit"]}  {t["hold_days"]}d')
    print(f'\n  Worst 5 trades:')
    for t in trades_sorted[-5:]:
        print(f'    {t["name"]:<10s} {t["buy_date"]} → {t["sell_date"]}  '
              f'{t["ret"]*100:.1f}%  {t["exit"]}  {t["hold_days"]}d')

    # Export files
    print_header('EXPORTING')
    base = os.path.dirname(os.path.abspath(__file__))
    export_trades(bt_final['trades'], os.path.join(base, 'timing_rotation_trades.csv'))
    export_equity(bt_final['equity'], os.path.join(base, 'timing_rotation_equity.csv'))
    print(f'  ✅ timing_rotation_trades.csv ({len(bt_final["trades"])} trades)')
    print(f'  ✅ timing_rotation_equity.csv ({len(bt_final["equity"])} days)')

    print('\n' + '=' * 100)
    print('  回测完成!')
    print('=' * 100)


if __name__ == '__main__':
    main()
