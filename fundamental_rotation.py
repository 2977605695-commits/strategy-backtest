"""
基本面评分 + MA5偏离买入 + Trail止损轮动 · 网格搜索
=====================================================
策略:
  基本面得分 = Z(净利率)×0.50 + Z(ROE)×0.37 + Z(营收YoY)×0.13
  每季度重排，持有得分最高的5只（细分赛道不重复）
  买入: DEV(MA5) < buy_thr
  卖出: Trail stop
  轮动: 卖出后立即买入得分最高且赛道不重复的满足买入条件的股票
约束: T+1, 滑点0.3%, 手续费
"""
import json, os, math, csv
from collections import defaultdict
from datetime import datetime, timedelta

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")
RISK_FREE = 0.025
TRADING_DAYS = 252
INIT_CAP = 10_000_000
MA_WIN = 5
MAX_POSITIONS = 5

# Costs
BUY_SLIPPAGE = 0.003
SELL_SLIPPAGE = 0.003
BUY_FEE = 0.00025
SELL_FEE = 0.00075

# Fundamental weights
W_NET_MARGIN = 0.50
W_ROE = 0.37
W_REV_YOY = 0.13


def calc_ma(data, w):
    ma = []
    for i in range(len(data)):
        if i < w - 1:
            ma.append(float('nan'))
        else:
            ma.append(sum(data[i - w + 1:i + 1]) / w)
    return ma


def load_fundamentals():
    """Load all fundamental data, return dict: code -> [(pub_date, report_date, metrics), ...]"""
    fund_data = defaultdict(list)

    for fname in sorted(os.listdir(FUND_DIR)):
        if not fname.endswith('.csv'):
            continue
        fpath = os.path.join(FUND_DIR, fname)
        with open(fpath, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row['code'].strip()
                try:
                    pub_date = row['pub_date'].strip()
                    report_date = row['report_date'].strip()
                    roe = float(row['roe']) if row['roe'] else None
                    net_margin = float(row['net_margin']) if row['net_margin'] else None
                    rev_yoy = float(row['rev_yoy']) if row['rev_yoy'] else None
                    sector = row['sector'].strip()

                    if roe is None or net_margin is None or rev_yoy is None:
                        continue

                    fund_data[code].append({
                        'pub_date': pub_date,
                        'report_date': report_date,
                        'roe': roe,
                        'net_margin': net_margin,
                        'rev_yoy': rev_yoy,
                        'sector': sector,
                    })
                except (ValueError, KeyError):
                    continue

    # Sort by pub_date for each stock
    for code in fund_data:
        fund_data[code].sort(key=lambda x: x['pub_date'])

    return fund_data


def get_latest_fundamentals(fund_data, current_date):
    """Get the latest fundamental data available on or before current_date for each stock."""
    latest = {}
    for code, reports in fund_data.items():
        valid = [r for r in reports if r['pub_date'] <= current_date]
        if valid:
            latest[code] = valid[-1]  # Most recent by pub_date
    return latest


def compute_zscores(latest_fund):
    """Compute Z-scores cross-sectionally from latest available fundamentals."""
    if len(latest_fund) < 3:
        return {}

    metrics = {'roe': [], 'net_margin': [], 'rev_yoy': []}
    codes = []
    for code, fund in latest_fund.items():
        codes.append(code)
        metrics['roe'].append(fund['roe'])
        metrics['net_margin'].append(fund['net_margin'])
        metrics['rev_yoy'].append(fund['rev_yoy'])

    # Compute mean/std for each metric
    stats = {}
    for key, vals in metrics.items():
        n = len(vals)
        mu = sum(vals) / n
        var = sum((v - mu) ** 2 for v in vals) / n
        sd = math.sqrt(var) if var > 0 else 1.0
        stats[key] = (mu, sd)

    # Compute scores
    scores = {}
    for i, code in enumerate(codes):
        z_roe = (metrics['roe'][i] - stats['roe'][0]) / stats['roe'][1]
        z_nm = (metrics['net_margin'][i] - stats['net_margin'][0]) / stats['net_margin'][1]
        z_ry = (metrics['rev_yoy'][i] - stats['rev_yoy'][0]) / stats['rev_yoy'][1]
        score = z_nm * W_NET_MARGIN + z_roe * W_ROE + z_ry * W_REV_YOY
        scores[code] = {
            'score': score,
            'sector': latest_fund[code]['sector'],
            'roe': metrics['roe'][i],
            'net_margin': metrics['net_margin'][i],
            'rev_yoy': metrics['rev_yoy'][i],
        }

    return scores


def backtest_rotation(buy_thr, trail_pct, stocks_data, fund_data, common_dates):
    """
    Fundamental-score rotation backtest with T+1, slippage, fees.

    Portfolio: up to MAX_POSITIONS stocks, each from different sector.
    When a stock exits (trail stop), immediately rotate to next best eligible.
    """
    per_stock_cap = INIT_CAP / MAX_POSITIONS

    # Pre-compute MA5 and deviations for all stocks
    common_set = set(common_dates)
    precomputed = {}
    for code, info in stocks_data.items():
        bars = [b for b in info['bars'] if b['date'] in common_set]
        closes = [b['close'] for b in bars]
        ma5 = calc_ma(closes, MA_WIN)
        devs = []
        for i, bar in enumerate(bars):
            ma = ma5[i]
            if math.isnan(ma) or ma == 0:
                devs.append(float('nan'))
            else:
                devs.append((bar['close'] - ma) / abs(ma))
        precomputed[code] = {
            'bars': bars,
            'closes': closes,
            'ma5': ma5,
            'devs': devs,
            'sector': info['sector'],
            'name': info['name'],
        }

    # State
    holdings = {}  # code -> {pos, buy_px, peak, buy_day, buy_date, sector, capital}
    cash_pool = INIT_CAP  # Unallocated cash
    trades = []
    daily_values = []
    trail_count = 0
    final_count = 0
    current_scores = {}  # Latest fundamental scores
    last_fund_update = ""

    n_days = len(common_dates)

    for day_idx, date_str in enumerate(common_dates):
        # Check for fundamental update
        new_fund = get_latest_fundamentals(fund_data, date_str)
        if new_fund:
            new_scores = compute_zscores(new_fund)
            if new_scores:
                current_scores = new_scores

        # Process each holding
        sell_events = []
        for code, h in list(holdings.items()):
            pc = precomputed[code]
            # Find this stock's bar index for today
            # All bars are aligned to common_dates
            bar = pc['bars'][day_idx]
            px = bar['close']

            # T+1: only check trail if held from previous day
            if day_idx > h['buy_day']:
                if px > h['peak']:
                    h['peak'] = px

                trail_px = h['peak'] * (1 - trail_pct)
                if px <= trail_px:
                    sell_px = px * (1 - SELL_SLIPPAGE)
                    gross = h['pos'] * sell_px
                    fee = gross * SELL_FEE
                    net_cash = gross - fee
                    pnl = net_cash - h['pos'] * h['buy_px']

                    trades.append({
                        'code': code, 'name': pc['name'],
                        'buy_date': h['buy_date'], 'sell_date': date_str,
                        'buy_px': h['buy_px'], 'sell_px': sell_px,
                        'ret': (sell_px - h['buy_px']) / h['buy_px'],
                        'pnl': pnl, 'exit': 'trail',
                        'peak': h['peak'], 'days_held': day_idx - h['buy_day'],
                    })
                    trail_count += 1
                    sell_events.append((code, net_cash, h['sector']))

        # Remove sold positions
        for code, cash_return, sector in sell_events:
            cash_pool += cash_return
            del holdings[code]

        # Try to fill positions
        # Rank eligible stocks by score
        eligible = []
        held_sectors = set(h['sector'] for h in holdings.values())
        held_codes = set(holdings.keys())

        for code, sc in sorted(current_scores.items(), key=lambda x: x[1]['score'], reverse=True):
            if code in held_codes:
                continue
            if sc['sector'] in held_sectors:
                continue
            if code not in precomputed:
                continue

            pc = precomputed[code]
            dev = pc['devs'][day_idx]

            if math.isnan(dev):
                continue

            if dev < buy_thr:
                eligible.append(code)

        # Buy until portfolio full or no more eligible
        while len(holdings) < MAX_POSITIONS and eligible and cash_pool >= per_stock_cap:
            code = eligible.pop(0)
            if code in holdings:
                continue

            pc = precomputed[code]
            px = pc['bars'][day_idx]['close']
            buy_px = px * (1 + BUY_SLIPPAGE)
            fee = per_stock_cap * BUY_FEE
            investable = per_stock_cap - fee
            pos = investable / buy_px

            holdings[code] = {
                'pos': pos,
                'buy_px': buy_px,
                'peak': px,
                'buy_day': day_idx,
                'buy_date': date_str,
                'sector': current_scores[code]['sector'],
                'capital': per_stock_cap,
            }
            cash_pool -= per_stock_cap

            held_sectors.add(current_scores[code]['sector'])

        # Compute daily portfolio value
        portfolio_value = cash_pool
        for code, h in holdings.items():
            pc = precomputed[code]
            px = pc['bars'][day_idx]['close']
            portfolio_value += h['pos'] * px

        daily_values.append({
            'date': date_str,
            'value': portfolio_value,
            'cash': cash_pool,
            'positions': len(holdings),
        })

    # Final liquidation
    for code, h in list(holdings.items()):
        pc = precomputed[code]
        fp = pc['bars'][-1]['close']
        sell_px = fp * (1 - SELL_SLIPPAGE)
        gross = h['pos'] * sell_px
        fee = gross * SELL_FEE
        net_cash = gross - fee
        pnl = net_cash - h['pos'] * h['buy_px']

        trades.append({
            'code': code, 'name': pc['name'],
            'buy_date': h['buy_date'], 'sell_date': common_dates[-1],
            'buy_px': h['buy_px'], 'sell_px': sell_px,
            'ret': (sell_px - h['buy_px']) / h['buy_px'],
            'pnl': pnl, 'exit': 'final',
            'days_held': n_days - 1 - h['buy_day'],
        })
        final_count += 1
        cash_pool += net_cash
        del holdings[code]

    # Metrics
    fv = daily_values[-1]['value']
    rets = []
    for i in range(1, len(daily_values)):
        p, c = daily_values[i - 1]['value'], daily_values[i]['value']
        if p > 0:
            rets.append((c - p) / p)

    peak_v = daily_values[0]['value']
    mdd = 0.0
    for dv in daily_values:
        if dv['value'] > peak_v:
            peak_v = dv['value']
        dd = (peak_v - dv['value']) / peak_v
        if dd > mdd:
            mdd = dd

    tr = (fv - INIT_CAP) / INIT_CAP

    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
        av = sd * math.sqrt(TRADING_DAYS)
        ar_ = mu * TRADING_DAYS
        sh = (ar_ - RISK_FREE) / av if av > 0 else 0
    else:
        av = sh = ar_ = 0.0

    cagr = (1 + tr) ** (TRADING_DAYS / max(len(rets), 1)) - 1 if tr > -1 else -1
    cm = cagr / mdd if mdd > 0 else float('inf')

    wins = sum(1 for t in trades if t['ret'] > 0)
    wr = wins / len(trades) if trades else 0

    # Position stats
    pos_days = sum(1 for dv in daily_values if dv['positions'] > 0)
    avg_pos = sum(dv['positions'] for dv in daily_values) / len(daily_values)

    # Trade stats
    trail_trades = [t for t in trades if t.get('exit') == 'trail']
    trail_wins = [t for t in trail_trades if t['ret'] > 0]
    trail_loss = [t for t in trail_trades if t['ret'] <= 0]

    return {
        'buy_thr': buy_thr,
        'trail_pct': trail_pct,
        'tr': tr, 'ar': cagr, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
        'np': len(trades), 'wr': wr,
        'trail_count': trail_count, 'final_count': final_count,
        'avg_win': sum(t['ret'] for t in trail_wins) / len(trail_wins) if trail_wins else 0,
        'avg_loss': sum(t['ret'] for t in trail_loss) / len(trail_loss) if trail_loss else 0,
        'pos_days': pos_days, 'avg_positions': avg_pos,
        'fv': fv,
        'trades': trades,
        'daily_values': daily_values,
    }


def main():
    print("=" * 80)
    print("  基本面评分 + MA5偏离 + Trail轮动 · 网格搜索")
    print(f"  得分 = Z(净利率)×{W_NET_MARGIN:.2f} + Z(ROE)×{W_ROE:.2f} + Z(营收YoY)×{W_REV_YOY:.2f}")
    print(f"  持仓: ≤{MAX_POSITIONS}只, 赛道不重复, 卖出后即时轮动")
    print(f"  T+1 + 滑点{BUY_SLIPPAGE:.1%} + 手续费")
    print("=" * 80)

    # Load fundamental data
    print("\n[LOAD] Fundamentals...")
    fund_data = load_fundamentals()
    print(f"  Loaded fundamentals for {len(fund_data)} stocks")

    # Load price data
    print("  Loading price data...")
    stocks = {}
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'):
            continue
        with open(os.path.join(DATA_DIR, fname), 'r', encoding='utf-8') as f:
            data = json.load(f)
        stocks[data['code']] = {
            'name': data['name'],
            'sector': data['sector'],
            'bars': data['bars'],
        }

    # Filter to stocks with both price and fundamental data
    qualified = {}
    for code in stocks:
        if code in fund_data and len(stocks[code]['bars']) >= 500:
            qualified[code] = stocks[code]

    # Common trading days
    date_sets = [set(b['date'] for b in info['bars']) for info in qualified.values()]
    common_dates = sorted(date_sets[0].intersection(*date_sets[1:]))

    print(f"  {len(qualified)} stocks with price + fundamentals, {len(common_dates)} common days")
    print(f"  Period: {common_dates[0]} -> {common_dates[-1]}")

    # Grid search
    # DEV: test around the original -4.5%
    buy_thresholds = [-0.030, -0.035, -0.040, -0.045, -0.050, -0.055]
    # Trail: test the best range from previous results
    trail_pcts = [0.15, 0.17, 0.20, 0.22, 0.25, 0.27, 0.30, 0.35]

    print(f"\n[GRID] {len(buy_thresholds)}×{len(trail_pcts)} = {len(buy_thresholds)*len(trail_pcts)} scenarios\n")

    all_results = []
    total = len(buy_thresholds) * len(trail_pcts)
    done = 0

    for buy_thr in buy_thresholds:
        for trail_pct in trail_pcts:
            done += 1
            label = f"DEV<{buy_thr:.1%} Trail{trail_pct:.0%}"
            r = backtest_rotation(buy_thr, trail_pct, qualified, fund_data, common_dates)
            all_results.append(r)
            print(
                f"  [{done:>2d}/{total}] {label:<24s} | "
                f"Sharpe={r['sh']:>7.4f} | Ret={r['tr']*100:>7.2f}% | "
                f"DD={r['mdd']*100:>5.2f}% | Calmar={r['cm']:>7.3f} | "
                f"Trd={r['np']:>4d} | Win={r['wr']*100:>5.1f}% | "
                f"+{r['avg_win']*100:>5.1f}% / {r['avg_loss']*100:>5.1f}% | "
                f"持仓={r['avg_positions']:.1f}只"
            )

    # Sort and rank
    sorted_all = sorted(all_results, key=lambda r: r['sh'], reverse=True)

    print(f"\n\n{'='*120}")
    print(f"  排名（按夏普）")
    print(f"{'='*120}")
    print(
        f"  {'#':<3s} {'策略':<26s} {'夏普':>7s} {'总收益':>8s} "
        f"{'年化':>7s} {'回撤':>7s} {'卡玛':>7s} {'交易':>5s} "
        f"{'胜率':>6s} {'盈均':>6s} {'亏均':>6s} {'均持仓':>6s}"
    )
    print(f"  {'-'*110}")

    for rank, r in enumerate(sorted_all, 1):
        tag = " << BEST" if rank == 1 else ""
        print(
            f"  {rank:<3d} DEV<{r['buy_thr']:.1%} Trail{r['trail_pct']:.0%}     "
            f"{r['sh']:>7.4f} {r['tr']*100:>7.2f}% {r['ar']*100:>6.2f}% "
            f"{r['mdd']*100:>6.2f}% {r['cm']:>7.3f} "
            f"{r['np']:>5d} {r['wr']*100:>5.1f}% "
            f"{r['avg_win']*100:>5.1f}% {r['avg_loss']*100:>5.1f}% "
            f"{r['avg_positions']:>5.1f}{tag}"
        )

    # Best: per-parameter analysis
    print(f"\n\n{'='*80}")
    print(f"  参数维度分析")
    print(f"{'='*80}")

    # By DEV threshold
    print(f"\n  --- 按买入 DEV 阈值 ---")
    for buy_thr in buy_thresholds:
        dev_results = [r for r in all_results if r['buy_thr'] == buy_thr]
        avg_sh = sum(r['sh'] for r in dev_results) / len(dev_results)
        max_sh = max(r['sh'] for r in dev_results)
        avg_ret = sum(r['tr'] for r in dev_results) / len(dev_results)
        avg_pos = sum(r['avg_positions'] for r in dev_results) / len(dev_results)
        print(f"  DEV<{buy_thr:.1%}  均夏普={avg_sh:.4f}  最高={max_sh:.4f}  "
              f"均收益={avg_ret*100:.1f}%  均持仓={avg_pos:.1f}只")

    # By Trail
    print(f"\n  --- 按 Trail 止损 ---")
    for trail_pct in trail_pcts:
        trail_results = [r for r in all_results if r['trail_pct'] == trail_pct]
        avg_sh = sum(r['sh'] for r in trail_results) / len(trail_results)
        max_sh = max(r['sh'] for r in trail_results)
        avg_ret = sum(r['tr'] for r in trail_results) / len(trail_results)
        avg_trd = sum(r['np'] for r in trail_results) / len(trail_results)
        print(f"  Trail{trail_pct:.0%}  均夏普={avg_sh:.4f}  最高={max_sh:.4f}  "
              f"均收益={avg_ret*100:.1f}%  均交易={avg_trd:.0f}笔")

    # Heatmap
    print(f"\n\n  --- 夏普热力图 ---")
    print(f"  {'':>12s}", end="")
    for trail_pct in trail_pcts:
        print(f"  Trail{trail_pct:.0%}", end="")
    print()
    for buy_thr in buy_thresholds:
        print(f"  {'DEV<'+f'{buy_thr:.1%}':<12s}", end="")
        for trail_pct in trail_pcts:
            r = [x for x in all_results if x['buy_thr'] == buy_thr and x['trail_pct'] == trail_pct][0]
            # Find if this is the global max
            is_max = r['sh'] == max(x['sh'] for x in all_results)
            marker = f"*{r['sh']:.3f}*" if is_max else f" {r['sh']:.3f} "
            print(f" {marker}", end="")
        print()

    # Best scenario: trade log
    best = sorted_all[0]
    print(f"\n\n  BEST: DEV<{best['buy_thr']:.1%} Trail{best['trail_pct']:.0%} — 交易明细")
    print(f"  {'股票':<12s} {'买入日':<12s} {'卖出日':<12s} {'买入价':>8s} {'卖出价':>8s} "
          f"{'收益':>8s} {'退出':>8s} {'持天':>5s}")
    print(f"  {'-'*80}")
    for t in best['trades'][:30]:
        print(
            f"  {t['name']:<12s} {t['buy_date']:<12s} {t['sell_date']:<12s} "
            f"{t['buy_px']:>8.2f} {t['sell_px']:>8.2f} {t['ret']*100:>7.2f}% "
            f"{t['exit']:>8s} {t['days_held']:>5d}"
        )
    if len(best['trades']) > 30:
        print(f"  ... ({len(best['trades']) - 30} more trades)")

    print(f"\n  Backtest complete!")


if __name__ == '__main__':
    main()
