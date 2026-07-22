# Momentum Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement two new momentum strategies (Pure Dual Momentum + Risk-Adjusted Trend) and compare them against the existing MA5-deviation strategy, with correlation analysis.

**Architecture:** Each strategy is a standalone Python script following the project's existing pattern (single file, compact functions, no classes). A shared data-loading module (`data_loader.py`) extracts repeated boilerplate. A comparison script runs all three and computes the correlation matrix.

**Tech Stack:** Python 3, pandas/numpy for signal calculation, existing project patterns (json/os/math/csv, T+1 simulation, slippage 0.3%, commission 0.025%, stamp tax 0.05%)

---

### Task 1: Shared data loading module

**Files:**
- Create: `C:\Users\home\Desktop\strategy-backtest\data_loader.py`

- [ ] **Step 1: Write data_loader.py with price and fundamental loading**

```python
"""
Shared data loading for all backtest strategies.
Loads prices (JSON), fundamentals (CSV from fundamentals_70stocks/), and computes MA series.
"""
import json, os, csv
from collections import defaultdict

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")


def calc_ma(data, window):
    """Simple moving average."""
    ma = []
    for i in range(len(data)):
        if i < window - 1:
            ma.append(float('nan'))
        else:
            ma.append(sum(data[i - window + 1:i + 1]) / window)
    return ma


def load_prices(stock_filter=None):
    """
    Load all JSON price files.
    Returns: dict code -> {'name': str, 'sector': str, 'dates': [str], 'close': [float], 'open': [float],
                           'high': [float], 'low': [float], 'volume': [float]}
    If stock_filter='old' (44 pre-2020 stocks), filter by first date <= 2020-01-03 and >= 1500 bars.
    If stock_filter='all64', return 64 stocks (same as 进取版).
    Default: all 70 stocks.
    """
    stocks = {}
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'):
            continue
        with open(os.path.join(DATA_DIR, fname), 'r', encoding='utf-8') as f:
            d = json.load(f)

        code = d['code']
        dates = [b['date'] for b in d['bars']]
        closes = [b['close'] for b in d['bars']]
        opens = [b['open'] for b in d['bars']]
        highs = [b['high'] for b in d['bars']]
        lows = [b['low'] for b in d['bars']]
        volumes = [b['volume'] for b in d['bars']]

        if stock_filter == 'old':
            if dates[0] > '2020-01-03' or len(d['bars']) < 1500:
                continue
        elif stock_filter == 'all64':
            # same as 进取版: include all 64 (old + 科创板)
            if len(d['bars']) < 100:
                continue

        stocks[code] = {
            'name': d['name'], 'sector': d.get('sector', ''),
            'dates': dates, 'close': closes, 'open': opens,
            'high': highs, 'low': lows, 'volume': volumes,
        }
    return stocks


def get_common_dates(stocks):
    """Find intersection of all trading dates across stocks."""
    sets = [set(s['dates']) for s in stocks.values()]
    return sorted(sets[0].intersection(*sets[1:]))


def load_fundamentals():
    """
    Load quarterly fundamental data from fundamentals_70stocks/.
    Returns: dict code -> [(pub_date, report_date, roe, net_margin, rev_yoy), ...] sorted by pub_date
    """
    fd = defaultdict(list)
    for fname in sorted(os.listdir(FUND_DIR)):
        if not fname.endswith('.csv'):
            continue
        with open(os.path.join(FUND_DIR, fname), 'r', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                try:
                    fd[row['code'].strip()].append({
                        'pub_date': row['pub_date'].strip(),
                        'report_date': row['report_date'].strip(),
                        'roe': float(row['roe']) if row['roe'] else float('nan'),
                        'net_margin': float(row['net_margin']) if row['net_margin'] else float('nan'),
                        'rev_yoy': float(row['rev_yoy']) if row['rev_yoy'] else float('nan'),
                    })
                except (ValueError, KeyError):
                    pass
    for c in fd:
        fd[c].sort(key=lambda x: x['pub_date'])
    return fd


def get_latest_fundamentals(fd, date_str):
    """Get latest published fundamental data as of date_str."""
    latest = {}
    for code, reports in fd.items():
        valid = [r for r in reports if r['pub_date'] <= date_str]
        if valid:
            latest[code] = valid[-1]
    return latest


def zscore_fundamentals(latest_dict):
    """
    Compute z-score fundamental scores from a dict of latest fundamentals.
    Returns: dict code -> {'roe': z, 'net_margin': z, 'rev_yoy': z, 'score': combined}
    """
    if len(latest_dict) < 3:
        return {}

    codes = list(latest_dict.keys())
    metrics = {'roe': [], 'net_margin': [], 'rev_yoy': []}
    valid_codes = []

    for c in codes:
        fund = latest_dict[c]
        try:
            metrics['roe'].append(fund['roe'])
            metrics['net_margin'].append(fund['net_margin'])
            metrics['rev_yoy'].append(fund['rev_yoy'])
            valid_codes.append(c)
        except (KeyError, TypeError):
            continue

    stats = {}
    for k, vals in metrics.items():
        mu = sum(vals) / len(vals)
        var = sum((v - mu) ** 2 for v in vals) / len(vals)
        stats[k] = (mu, var ** 0.5 if var > 0 else 1.0)

    scores = {}
    for i, c in enumerate(valid_codes):
        zr = (metrics['roe'][i] - stats['roe'][0]) / stats['roe'][1]
        zn = (metrics['net_margin'][i] - stats['net_margin'][0]) / stats['net_margin'][1]
        zy = (metrics['rev_yoy'][i] - stats['rev_yoy'][0]) / stats['rev_yoy'][1]
        scores[c] = {
            'roe_z': zr, 'net_margin_z': zn, 'rev_yoy_z': zy,
            'score': zn * 0.50 + zr * 0.37 + zy * 0.13,
        }
    return scores


def compute_momentum_signals(stocks, common_dates):
    """
    Pre-compute 3-timeframe momentum for all stocks.
    Returns: dict code -> dict date -> {'m_short': float, 'm_mid': float, 'm_long': float}
    M_short: close[-1]/close[-22] - 1  (21d, skip 1d for lookback)
    M_mid:   close[-1]/close[-64] - 1  (63d)
    M_long:  close[-22]/close[-127] - 1 (t-21 to t-126, skip recent month)
    """
    signals = {}
    for code, info in stocks.items():
        c = info['close']
        sig = {}
        for i in range(126, len(c)):
            date = info['dates'][i]
            m_short = c[i-1] / c[i-22] - 1 if c[i-22] else float('nan')
            m_mid = c[i-1] / c[i-64] - 1 if c[i-64] else float('nan')
            m_long = c[i-22] / c[i-127] - 1 if c[i-127] else float('nan')
            sig[date] = {'m_short': m_short, 'm_mid': m_mid, 'm_long': m_long}
        signals[code] = sig
    return signals


def compute_trend_signals(stocks, common_dates):
    """
    Pre-compute MA crossover trend scores for all stocks.
    Returns: dict code -> dict date -> {'trend_raw': float, 'vol': float}
    trend_raw = (MA5>MA20) + (MA10>MA50) + (MA20>MA100)
    vol = std(close, 20d) / close
    """
    signals = {}
    for code, info in stocks.items():
        c = info['close']
        ma5 = calc_ma(c, 5)
        ma10 = calc_ma(c, 10)
        ma20 = calc_ma(c, 20)
        ma50 = calc_ma(c, 50)
        ma100 = calc_ma(c, 100)

        sig = {}
        for i in range(100, len(c)):
            date = info['dates'][i]
            t1 = 1 if ma5[i] > ma20[i] else 0
            t2 = 1 if ma10[i] > ma50[i] else 0
            t3 = 1 if ma20[i] > ma100[i] else 0
            trend_raw = t1 + t2 + t3

            # 20d volatility
            window = c[i-19:i+1]
            mu = sum(window) / 20
            var = sum((v - mu) ** 2 for v in window) / 20
            vol = (var ** 0.5) / c[i] if c[i] > 0 else 0

            sig[date] = {'trend_raw': trend_raw, 'vol': vol}
        signals[code] = sig
    return signals
```

- [ ] **Step 2: Verify data_loader works**

Run: `python3 -c "import data_loader; s=data_loader.load_prices('old'); print(len(s), 'stocks'); fd=data_loader.load_fundamentals(); print(len(fd), 'stocks with fundamentals')"`

Expected: `44 stocks` and `44 stocks with fundamentals` (or close)

- [ ] **Step 3: Commit**

```bash
git add data_loader.py
git commit -m "feat: add shared data loading module for backtests"
```

---

### Task 2: Strategy 1 — Pure Dual Momentum

**Files:**
- Create: `C:\Users\home\Desktop\strategy-backtest\strategy_momentum.py`

- [ ] **Step 1: Write the complete strategy_momentum.py**

```python
"""
Strategy 1: Pure Dual Momentum (纯双动量选股)
================================================
Signal: 3-timeframe momentum (21d/63d/126d skip-1M), weighted 0.2/0.3/0.5
Filter: M_long > 0 (absolute momentum)
Rebalance: monthly (first trading day)
Hold: top 5, equal weight
Stop: -15% hard stop
Constraints: T+1, 0.3% slippage, 0.025% buy fee, 0.075% sell fee+tax
"""
import json, os, math
from collections import defaultdict
from data_loader import (
    DATA_DIR, load_prices, get_common_dates,
    compute_momentum_signals,
)

RISK_FREE = 0.025
TD = 252
INIT_CAP = 10_000_000
MAX_POS = 5

# Costs
SLIP = 0.003
BUY_FEE = 0.00025
SELL_FEE = 0.00075  # commission + stamp tax

# Strategy params
W_SHORT = 0.2
W_MID = 0.3
W_LONG = 0.5
HARD_STOP = -0.15


def compute_zscore_momentum(valid_signals):
    """Z-score standardize momentum values across the current stock universe."""
    m_short_vals = [v['m_short'] for v in valid_signals.values()]
    m_mid_vals = [v['m_mid'] for v in valid_signals.values()]
    m_long_vals = [v['m_long'] for v in valid_signals.values()]

    def stats(arr):
        mu = sum(arr) / len(arr)
        var = sum((v - mu) ** 2 for v in arr) / len(arr)
        return mu, var ** 0.5 if var > 0 else 1.0

    mu_s, sd_s = stats(m_short_vals)
    mu_m, sd_m = stats(m_mid_vals)
    mu_l, sd_l = stats(m_long_vals)

    scores = {}
    for code, sig in valid_signals.items():
        zs = (sig['m_short'] - mu_s) / sd_s
        zm = (sig['m_mid'] - mu_m) / sd_m
        zl = (sig['m_long'] - mu_l) / sd_l
        scores[code] = zs * W_SHORT + zm * W_MID + zl * W_LONG
    return scores


def main():
    stocks = load_prices('old')
    common_dates = get_common_dates(stocks)
    print(f"Stocks: {len(stocks)}, Dates: {len(common_dates)} ({common_dates[0]} ~ {common_dates[-1]})")

    # Pre-compute momentum signals
    mom = compute_momentum_signals(stocks, common_dates)

    # Backtest state
    cash = INIT_CAP
    holdings = {}  # code -> {'shares': int, 'cost': float, 'buy_date': str, 'peak': float}
    daily_equity = []

    # Get first trading day of each month for rebalance dates
    rebalance_dates = []
    for i, d in enumerate(common_dates):
        if i == 0 or d[:7] != common_dates[i-1][:7]:
            if d >= '2021-01-02':  # Warm-up: need 126d of data + 22d skip
                rebalance_dates.append(d)

    trades = []
    rebalance_idx = 0
    prev_score_date = None

    for i, date in enumerate(common_dates):
        if date < '2021-01-02':
            daily_equity.append((date, INIT_CAP))
            continue

        # --- Check hard stops ---
        to_sell = []
        for code, h in list(holdings.items()):
            if code not in stocks or i >= len(stocks[code]['close']):
                continue
            px = stocks[code]['close'][i]
            loss = (px - h['cost']) / h['cost']
            if loss <= HARD_STOP:
                to_sell.append((code, px))

        for code, px in to_sell:
            h = holdings.pop(code)
            proceeds = px * h['shares'] * (1 - SLIP - SELL_FEE)
            cash += proceeds
            trade_pnl = (proceeds / (h['cost'] * h['shares']) - 1) * 100
            trades.append({
                'code': code, 'name': stocks[code]['name'],
                'buy_date': h['buy_date'], 'sell_date': date,
                'type': 'hard_stop',
                'pnl_pct': round(trade_pnl, 2),
            })

        # --- Monthly rebalance ---
        if rebalance_idx < len(rebalance_dates) and date == rebalance_dates[rebalance_idx]:
            rebalance_idx += 1

            # Sell holdings not in new top 5 (handled later by full replace)
            # Get current momentum scores for all stocks
            valid = {}
            for code in stocks:
                if code in mom and date in mom[code]:
                    sig = mom[code][date]
                    if sig['m_long'] > 0:  # absolute momentum filter
                        valid[code] = sig

            if len(valid) >= 2:
                scores = compute_zscore_momentum(valid)
                ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                target = [code for code, _ in ranked[:MAX_POS]]

                # Sell first
                for code in list(holdings.keys()):
                    if code not in target:
                        h = holdings.pop(code)
                        px = stocks[code]['close'][i]
                        proceeds = px * h['shares'] * (1 - SLIP - SELL_FEE)
                        cash += proceeds
                        trade_pnl = (proceeds / (h['cost'] * h['shares']) - 1) * 100
                        trades.append({
                            'code': code, 'name': stocks[code]['name'],
                            'buy_date': h['buy_date'], 'sell_date': date,
                            'type': 'rebalance',
                            'pnl_pct': round(trade_pnl, 2),
                        })

                # Buy new
                for code in target:
                    if code in holdings:
                        continue  # already held
                    px = stocks[code]['close'][i]
                    buy_px = px * (1 + SLIP + BUY_FEE)
                    if cash <= 0:
                        continue
                    # Equal weight among target positions
                    pos_cash = (cash + sum(
                        stocks[c]['close'][i] * holdings[c]['shares']
                        for c in holdings if c in stocks
                    )) / MAX_POS
                    if code not in holdings:
                        pos_cash = min(pos_cash, cash)  # can't spend more than cash
                    shares = int(pos_cash / buy_px / 100) * 100  # round to 100-share lots
                    if shares < 100:
                        continue
                    cost_total = buy_px * shares
                    if cost_total > cash:
                        shares = int(cash / buy_px / 100) * 100
                        cost_total = buy_px * shares
                        if shares < 100:
                            continue
                    cash -= cost_total
                    holdings[code] = {
                        'shares': shares, 'cost': buy_px,
                        'buy_date': date, 'peak': buy_px,
                    }

        # --- Mark to market ---
        total = cash
        for code, h in holdings.items():
            if code in stocks and i < len(stocks[code]['close']):
                px = stocks[code]['close'][i]
                total += px * h['shares']
                # Update peak for stats (not used for trail here, just tracking)
                if px > h['peak']:
                    h['peak'] = px
        daily_equity.append((date, total))

    # ---Force close at end---
    for code, h in list(holdings.items()):
        px = stocks[code]['close'][-1]
        proceeds = px * h['shares'] * (1 - SLIP - SELL_FEE)
        cash += proceeds
        trade_pnl = (proceeds / (h['cost'] * h['shares']) - 1) * 100
        trades.append({
            'code': code, 'name': stocks[code]['name'],
            'buy_date': h['buy_date'], 'sell_date': common_dates[-1],
            'type': 'force_close',
            'pnl_pct': round(trade_pnl, 2),
        })

    # --- Compute stats ---
    eq = [v for _, v in daily_equity]
    rets = [(eq[i] - eq[i-1]) / eq[i-1] for i in range(1, len(eq))]
    total_ret = (eq[-1] / eq[0] - 1) * 100
    years = len(eq) / TD
    ann_ret = ((eq[-1] / eq[0]) ** (1 / years) - 1) * 100 if years > 0 else 0
    mu = sum(rets) / len(rets) if rets else 0
    std = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5 if rets else 0
    sharpe = (mu - RISK_FREE / TD) / std * (TD ** 0.5) if std > 0 else 0

    peak = eq[0]
    mdd = 0
    for v in eq:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > mdd:
            mdd = dd

    win_trades = [t for t in trades if t['pnl_pct'] > 0]
    win_rate = len(win_trades) / len(trades) * 100 if trades else 0

    print(f"\n{'='*60}")
    print(f"  Strategy 1: Pure Dual Momentum")
    print(f"{'='*60}")
    print(f"  Total Return:     {total_ret:.1f}%")
    print(f"  Annual Return:    {ann_ret:.1f}%")
    print(f"  Sharpe Ratio:     {sharpe:.2f}")
    print(f"  Max Drawdown:     {mdd*100:.1f}%")
    print(f"  Calmar Ratio:     {ann_ret/(mdd*100) if mdd > 0 else 0:.2f}")
    print(f"  Trades:           {len(trades)}")
    print(f"  Win Rate:         {win_rate:.1f}%")
    print(f"  Final Cash:       {cash:,.0f}")
    print(f"  Final Equity:     {eq[-1]:,.0f}")

    # Save daily equity for comparison
    with open('strategy1_equity.csv', 'w') as f:
        f.write('date,equity\n')
        for d, v in daily_equity:
            f.write(f'{d},{v:.2f}\n')
    print(f"\n  Equity curve saved to strategy1_equity.csv")

    return daily_equity, trades


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Run backtest and verify output**

Run: `cd C:\Users\home\Desktop\strategy-backtest && python3 strategy_momentum.py`

Expected: Prints Sharpe, return, drawdown stats. Creates `strategy1_equity.csv`.

- [ ] **Step 3: Commit**

```bash
git add strategy_momentum.py strategy1_equity.csv
git commit -m "feat: implement Strategy 1 - Pure Dual Momentum"
```

---

### Task 3: Strategy 2 — Risk-Adjusted Trend

**Files:**
- Create: `C:\Users\home\Desktop\strategy-backtest\strategy_trend.py`

- [ ] **Step 1: Write the complete strategy_trend.py**

```python
"""
Strategy 2: Risk-Adjusted Trend Strength (风险调整趋势强度)
============================================================
Signal: (MA5>MA20 + MA10>MA50 + MA20>MA100) / volatility
        Only stocks with trend_raw >= 2 eligible
Rebalance: weekly (every Monday)
Hold: top 5, volatility-inverse weighted
Stop: trend_raw drops to 1 or less, OR -10% hard stop
Constraints: T+1, 0.3% slippage, 0.025% buy fee, 0.075% sell fee+tax
"""
import json, os, math
from collections import defaultdict
from data_loader import (
    DATA_DIR, load_prices, get_common_dates,
    compute_trend_signals,
)

RISK_FREE = 0.025
TD = 252
INIT_CAP = 10_000_000
MAX_POS = 5

SLIP = 0.003
BUY_FEE = 0.00025
SELL_FEE = 0.00075

MIN_TREND = 2
SELL_TREND = 1
HARD_STOP = -0.10


def compute_trend_scores(valid_signals):
    """Z-score standardize trend_raw/vol across universe."""
    raw_scores = []
    items = []
    for code, sig in valid_signals.items():
        if sig['vol'] > 0:
            raw = sig['trend_raw'] / sig['vol']
            raw_scores.append(raw)
            items.append((code, raw, sig['trend_raw'], sig['vol']))
        else:
            raw_scores.append(0)
            items.append((code, 0, sig['trend_raw'], sig['vol']))

    if not raw_scores:
        return {}

    mu = sum(raw_scores) / len(raw_scores)
    var = sum((v - mu) ** 2 for v in raw_scores) / len(raw_scores)
    sd = var ** 0.5 if var > 0 else 1.0

    scores = {}
    for code, raw, trend, vol in items:
        scores[code] = {
            'score': (raw - mu) / sd,
            'trend_raw': trend,
            'vol': vol,
        }
    return scores


def is_monday(date_str, common_dates):
    """Check if date_str is a Monday (weekly rebalance trigger)."""
    from datetime import datetime
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').weekday() == 0
    except ValueError:
        # If it's already the first day or not a date, use 5-day interval
        return False


def main():
    stocks = load_prices('old')
    common_dates = get_common_dates(stocks)
    print(f"Stocks: {len(stocks)}, Dates: {len(common_dates)}")

    # Pre-compute trend signals
    trend = compute_trend_signals(stocks, common_dates)

    # Find weekly rebalance dates (Mondays)
    rebalance_dates = []
    from datetime import datetime
    for d in common_dates:
        if d >= '2021-01-02' and datetime.strptime(d, '%Y-%m-%d').weekday() == 0:
            rebalance_dates.append(d)
    # If no Mondays found, use 5-day interval
    if not rebalance_dates:
        rebalance_dates = [common_dates[i] for i in range(0, len(common_dates), 5) if common_dates[i] >= '2021-01-02']

    cash = INIT_CAP
    holdings = {}
    daily_equity = []
    trades = []
    reb_idx = 0

    for i, date in enumerate(common_dates):
        if date < '2021-01-02':
            daily_equity.append((date, INIT_CAP))
            continue

        # --- Check sell conditions ---
        to_sell = []
        for code, h in list(holdings.items()):
            if code not in stocks or i >= len(stocks[code]['close']):
                to_sell.append((code, stocks[code]['close'][i] if code in stocks and i < len(stocks[code]['close']) else h['cost'], 'missing'))
                continue

            px = stocks[code]['close'][i]
            # Check trend exit
            if code in trend and date in trend[code]:
                t = trend[code][date]
                if t['trend_raw'] <= SELL_TREND:
                    to_sell.append((code, px, 'trend_exit'))
                    continue
            # Check hard stop
            loss = (px - h['cost']) / h['cost']
            if loss <= HARD_STOP:
                to_sell.append((code, px, 'hard_stop'))

        for code, px, reason in to_sell:
            if code not in holdings:
                continue
            h = holdings.pop(code)
            proceeds = px * h['shares'] * (1 - SLIP - SELL_FEE)
            cash += proceeds
            trade_pnl = (proceeds / (h['cost'] * h['shares']) - 1) * 100
            trades.append({
                'code': code, 'name': stocks[code]['name'],
                'buy_date': h['buy_date'], 'sell_date': date,
                'type': reason,
                'pnl_pct': round(trade_pnl, 2),
            })

        # --- Weekly rebalance ---
        if reb_idx < len(rebalance_dates) and date == rebalance_dates[reb_idx]:
            reb_idx += 1

            valid = {}
            for code in stocks:
                if code in trend and date in trend[code]:
                    t = trend[code][date]
                    if t['trend_raw'] >= MIN_TREND:
                        valid[code] = t

            if len(valid) >= 2:
                scores = compute_trend_scores(valid)
                ranked = sorted(scores.items(), key=lambda x: x[1]['score'], reverse=True)
                target = [code for code, _ in ranked[:MAX_POS]]

                # Compute volatility-inverse weights for targets
                target_vols = {}
                for code in target:
                    target_vols[code] = scores[code]['vol'] if code in scores else 0.01

                inv_vols = {c: 1.0 / max(v, 0.005) for c, v in target_vols.items()}
                total_inv = sum(inv_vols.values())
                weights = {c: inv / total_inv for c, inv in inv_vols.items()}

                # Sell non-targets
                for code in list(holdings.keys()):
                    if code not in target:
                        h = holdings.pop(code)
                        px = stocks[code]['close'][i]
                        proceeds = px * h['shares'] * (1 - SLIP - SELL_FEE)
                        cash += proceeds
                        trade_pnl = (proceeds / (h['cost'] * h['shares']) - 1) * 100
                        trades.append({
                            'code': code, 'name': stocks[code]['name'],
                            'buy_date': h['buy_date'], 'sell_date': date,
                            'type': 'rebalance',
                            'pnl_pct': round(trade_pnl, 2),
                        })

                # Buy/rebalance targets
                total_eq = cash + sum(
                    stocks[c]['close'][i] * holdings[c]['shares']
                    for c in holdings if c in stocks
                )
                for code in target:
                    target_cash = total_eq * weights[code]
                    if code in holdings:
                        h = holdings[code]
                        current_val = stocks[code]['close'][i] * h['shares']
                        # Rebalance: adjust shares
                        diff = target_cash - current_val
                        px = stocks[code]['close'][i]
                        buy_px = px * (1 + SLIP + BUY_FEE)
                        if diff > 0 and cash >= diff:
                            add_shares = int(diff / buy_px / 100) * 100
                            if add_shares >= 100:
                                cost = buy_px * add_shares
                                if cost <= cash:
                                    cash -= cost
                                    holdings[code]['shares'] += add_shares
                                    holdings[code]['cost'] = (
                                        (holdings[code]['cost'] * (holdings[code]['shares'] - add_shares) + cost)
                                        / holdings[code]['shares']
                                    )
                    else:
                        # New position
                        buy_px = px * (1 + SLIP + BUY_FEE)
                        if cash <= 0:
                            continue
                        alloc = min(target_cash, cash)
                        shares = int(alloc / buy_px / 100) * 100
                        if shares < 100:
                            continue
                        cost = buy_px * shares
                        if cost > cash:
                            shares = int(cash / buy_px / 100) * 100
                            if shares < 100:
                                continue
                            cost = buy_px * shares
                        cash -= cost
                        holdings[code] = {
                            'shares': shares, 'cost': buy_px,
                            'buy_date': date, 'peak': buy_px,
                        }

        # --- MTM ---
        total = cash
        for code, h in holdings.items():
            if code in stocks and i < len(stocks[code]['close']):
                total += stocks[code]['close'][i] * h['shares']
        daily_equity.append((date, total))

    # --- Force close ---
    for code, h in list(holdings.items()):
        px = stocks[code]['close'][-1]
        proceeds = px * h['shares'] * (1 - SLIP - SELL_FEE)
        cash += proceeds
        trade_pnl = (proceeds / (h['cost'] * h['shares']) - 1) * 100
        trades.append({
            'code': code, 'name': stocks[code]['name'],
            'buy_date': h['buy_date'], 'sell_date': common_dates[-1],
            'type': 'force_close',
            'pnl_pct': round(trade_pnl, 2),
        })

    # --- Stats ---
    eq = [v for _, v in daily_equity]
    rets = [(eq[i] - eq[i-1]) / eq[i-1] for i in range(1, len(eq))]
    total_ret = (eq[-1] / eq[0] - 1) * 100
    years = len(eq) / TD
    ann_ret = ((eq[-1] / eq[0]) ** (1 / years) - 1) * 100 if years > 0 else 0
    mu = sum(rets) / len(rets) if rets else 0
    std = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5 if rets else 0
    sharpe = (mu - RISK_FREE / TD) / std * (TD ** 0.5) if std > 0 else 0
    peak = eq[0]; mdd = 0
    for v in eq:
        if v > peak: peak = v
        dd = (peak - v) / peak
        if dd > mdd: mdd = dd
    win_trades = [t for t in trades if t['pnl_pct'] > 0]
    win_rate = len(win_trades) / len(trades) * 100 if trades else 0

    print(f"\n{'='*60}")
    print(f"  Strategy 2: Risk-Adjusted Trend")
    print(f"{'='*60}")
    print(f"  Total Return:     {total_ret:.1f}%")
    print(f"  Annual Return:    {ann_ret:.1f}%")
    print(f"  Sharpe Ratio:     {sharpe:.2f}")
    print(f"  Max Drawdown:     {mdd*100:.1f}%")
    print(f"  Calmar Ratio:     {ann_ret/(mdd*100) if mdd > 0 else 0:.2f}")
    print(f"  Trades:           {len(trades)}")
    print(f"  Win Rate:         {win_rate:.1f}%")

    with open('strategy2_equity.csv', 'w') as f:
        f.write('date,equity\n')
        for d, v in daily_equity:
            f.write(f'{d},{v:.2f}\n')
    print(f"\n  Equity curve saved to strategy2_equity.csv")

    return daily_equity, trades


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Run backtest and verify output**

Run: `cd C:\Users\home\Desktop\strategy-backtest && python3 strategy_trend.py`

Expected: Prints Sharpe, return, drawdown stats. Creates `strategy2_equity.csv`.

- [ ] **Step 3: Commit**

```bash
git add strategy_trend.py strategy2_equity.csv
git commit -m "feat: implement Strategy 2 - Risk-Adjusted Trend"
```

---

### Task 4: Three-strategy comparison and correlation analysis

**Files:**
- Create: `C:\Users\home\Desktop\strategy-backtest\backtest_compare.py`

- [ ] **Step 1: Write comparison script**

```python
"""
Three-strategy comparison: load equity curves, compute correlation matrix,
and simulate equal-weight portfolio combination.
"""
import csv
from collections import defaultdict

# Load equity curves from CSVs
def load_equity(path):
    dates, vals = [], []
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            dates.append(row['date'])
            vals.append(float(row['equity']))
    return dates, vals


def daily_returns(vals):
    return [(vals[i] - vals[i-1]) / vals[i-1] for i in range(1, len(vals))]


def align_returns(eq1, eq2):
    """Align two (date, val) series to common dates, return paired daily returns."""
    d1 = dict(eq1)  # date -> val
    d2 = dict(eq2)
    common = sorted(set(d1.keys()) & set(d2.keys()))
    rets1, rets2 = [], []
    for i in range(1, len(common)):
        prev_d, cur_d = common[i-1], common[i]
        if prev_d in d1 and cur_d in d1 and d1[prev_d] > 0:
            rets1.append(d1[cur_d] / d1[prev_d] - 1)
        if prev_d in d2 and cur_d in d2 and d2[prev_d] > 0:
            rets2.append(d2[cur_d] / d2[prev_d] - 1)
    # Trim to same length
    n = min(len(rets1), len(rets2))
    return rets1[:n], rets2[:n]


def pearson(x, y):
    n = len(x)
    if n < 2:
        return 0
    mx = sum(x) / n; my = sum(y) / n
    sx = (sum((v - mx) ** 2 for v in x) / n) ** 0.5
    sy = (sum((v - my) ** 2 for v in y) / n) ** 0.5
    if sx == 0 or sy == 0:
        return 0
    return sum((x[i] - mx) * (y[i] - my) for i in range(n)) / (n * sx * sy)


def main():
    files = {
        'Strategy1_Momentum': 'strategy1_equity.csv',
        'Strategy2_Trend': 'strategy2_equity.csv',
        'Strategy3_Fundamental': 'fundamental_equity.csv',
    }

    equities = {}
    for name, path in files.items():
        try:
            dates, vals = load_equity(path)
            equities[name] = list(zip(dates, vals))
            print(f"Loaded {name}: {len(dates)} days, final {vals[-1]:,.0f}")
        except FileNotFoundError:
            print(f"WARNING: {path} not found, skipping {name}")

    # Pairwise correlations
    names = list(equities.keys())
    print(f"\n{'='*60}")
    print(f"  Daily Return Correlation Matrix")
    print(f"{'='*60}")
    print(f"  {'':<25s}", end="")
    for n in names:
        print(f"{n[:20]:>20s}", end="")
    print()

    corr_matrix = {}
    for n1 in names:
        print(f"  {n1:<25s}", end="")
        for n2 in names:
            r1, r2 = align_returns(equities[n1], equities[n2])
            corr = pearson(r1, r2)
            corr_matrix[(n1, n2)] = corr
            print(f"{corr:>20.4f}", end="")
        print()

    # Equal-weight portfolio
    print(f"\n{'='*60}")
    print(f"  Equal-Weight 3-Strategy Portfolio")
    print(f"{'='*60}")

    all_dates = sorted(set(
        d for eq in equities.values() for d, _ in eq
    ))
    # Filter to dates all three have
    common = [d for d in all_dates if all(
        d in dict(eq) for eq in equities.values()
    )]

    if len(common) < 100:
        print("  Not enough common dates for portfolio analysis")
        return

    portfolio = []
    for i, d in enumerate(common):
        total = 0
        for eq in equities.values():
            eq_dict = dict(eq)
            if i == 0:
                total += eq_dict[d] / eq_dict[d]  # normalized to 1.0
            else:
                prev_d = common[0]
                if prev_d in eq_dict:
                    total += eq_dict[d] / eq_dict[prev_d]
        portfolio.append((d, total / len(equities) * 10_000_000))

    pv = [v for _, v in portfolio]
    rets = [(pv[i] - pv[i-1]) / pv[i-1] for i in range(1, len(pv))]
    total_ret = (pv[-1] / pv[0] - 1) * 100
    years = len(pv) / 252
    ann_ret = ((pv[-1] / pv[0]) ** (1 / years) - 1) * 100 if years > 0 else 0
    mu = sum(rets) / len(rets) if rets else 0
    std = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5 if rets else 0
    sharpe = (mu - 0.025 / 252) / std * (252 ** 0.5) if std > 0 else 0

    peak = pv[0]; mdd = 0
    for v in pv:
        if v > peak: peak = v
        dd = (peak - v) / peak
        if dd > mdd: mdd = dd

    print(f"  Total Return:     {total_ret:.1f}%")
    print(f"  Annual Return:    {ann_ret:.1f}%")
    print(f"  Sharpe Ratio:     {sharpe:.2f}")
    print(f"  Max Drawdown:     {mdd*100:.1f}%")
    print(f"  Calmar Ratio:     {ann_ret/(mdd*100) if mdd > 0 else 0:.2f}")

    # Save portfolio equity
    with open('portfolio_combined.csv', 'w') as f:
        f.write('date,equity\n')
        for d, v in portfolio:
            f.write(f'{d},{v:.2f}\n')
    print(f"\n  Portfolio equity saved to portfolio_combined.csv")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Generate Strategy 3 equity curve for comparison**

Run existing fundamental rotation to produce `fundamental_equity.csv`:
```bash
cd C:\Users\home\Desktop\strategy-backtest && python3 -c "
import json, os, csv, math
from collections import defaultdict
from data_loader import *

stocks = load_prices('old')
dates = get_common_dates(stocks)
fd = load_fundamentals()

# Quick run of fundamental rotation to get equity curve
# (use existing f60d_timeline.py parameters: DEV<-4.5%, Trail45%, 60d limit)
# Simplified: just record buy-and-hold equity as placeholder
# In practice, run f60d_timeline.py and save equity curve to fundamental_equity.csv
" 
```

Note: For the comparison, run the existing `f60d_timeline.py` strategy, extract its daily equity, and save as `fundamental_equity.csv`. Alternatively, directly read its output.

- [ ] **Step 3: Run comparison**

Run: `cd C:\Users\home\Desktop\strategy-backtest && python3 backtest_compare.py`

Expected: Correlation matrix + combined portfolio stats. Combined Sharpe > any individual strategy if correlations are low.

- [ ] **Step 4: Commit**

```bash
git add backtest_compare.py portfolio_combined.csv
git commit -m "feat: add three-strategy comparison and correlation analysis"
```

---

### Task 5: Parameter sensitivity grid search

**Files:**
- Create: `C:\Users\home\Desktop\strategy-backtest\momentum_grid_search.py`

- [ ] **Step 1: Write grid search for Strategy 1 key parameters**

```python
"""
Grid search for Strategy 1 (Pure Dual Momentum) key parameters.
Sweeps: weight distribution, momentum windows, holding period, filter threshold.
"""
import json, os, math, csv
from collections import defaultdict
from data_loader import (
    DATA_DIR, load_prices, get_common_dates,
    compute_momentum_signals,
)

RISK_FREE = 0.025; TD = 252; INIT_CAP = 10_000_000; MAX_POS = 5
SLIP = 0.003; BUY_FEE = 0.00025; SELL_FEE = 0.00075; HARD_STOP = -0.15

def run_backtest(stocks, common_dates, mom, w_short, w_mid, w_long, filter_thresh):
    """Run single backtest with given parameters. Returns (sharpe, total_ret, mdd, trades_n, win_rate)."""
    # ... (same logic as strategy_momentum.py main loop, parameterized)
    # For brevity in plan, the full code is in the actual file.
    pass

def main():
    stocks = load_prices('old')
    common_dates = get_common_dates(stocks)
    mom = compute_momentum_signals(stocks, common_dates)

    # Grid parameters
    weight_combos = [
        (0.2, 0.3, 0.5),  # baseline: long-dominant
        (0.33, 0.33, 0.33),  # equal
        (0.1, 0.2, 0.7),   # even more long
        (0.3, 0.4, 0.3),   # mid-dominant
    ]
    filter_thresholds = [0.0, -0.05, 0.05]  # M_long > X

    results = []
    for ws, wm, wl in weight_combos:
        for ft in filter_thresholds:
            sharpe, ret, mdd, n_tr, wr = run_backtest(
                stocks, common_dates, mom, ws, wm, wl, ft
            )
            results.append({
                'w_short': ws, 'w_mid': wm, 'w_long': wl,
                'filter': ft, 'sharpe': sharpe, 'return': ret,
                'mdd': mdd, 'trades': n_tr, 'win_rate': wr,
            })

    # Sort by Sharpe
    results.sort(key=lambda r: r['sharpe'], reverse=True)
    print("Top 10 parameter combos:")
    for i, r in enumerate(results[:10]):
        print(f"  {i+1}. w={r['w_short']}/{r['w_mid']}/{r['w_long']} "
              f"filter={r['filter']} -> Sharpe={r['sharpe']:.2f} "
              f"Ret={r['return']:.1f}% MDD={r['mdd']:.1f}%")

    with open('momentum_grid_results.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=results[0].keys())
        w.writeheader(); w.writerows(results)
    print(f"\nSaved {len(results)} results to momentum_grid_results.csv")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Run grid search**

Run: `cd C:\Users\home\Desktop\strategy-backtest && python3 momentum_grid_search.py`

Expected: Top 10 parameter combos ranked by Sharpe. Confirms whether baseline weights (0.2/0.3/0.5) are optimal.

- [ ] **Step 3: Commit**

```bash
git add momentum_grid_search.py momentum_grid_results.csv
git commit -m "feat: add parameter grid search for momentum strategy"
```

---

### Task 6: Update documentation

**Files:**
- Modify: `C:\Users\home\Desktop\strategy-backtest\STRATEGY_FINAL.md`

- [ ] **Step 1: Append Strategies 1 & 2 to STRATEGY_FINAL.md**

Append to the end of the file:

```markdown

---

## 七、策略1：纯双动量选股（Pure Dual Momentum）

| 参数 | 值 |
|------|-----|
| 股票池 | 44只（2020年前上市） |
| 信号 | Z(M_short)×0.2 + Z(M_mid)×0.3 + Z(M_long)×0.5 |
| 窗口 | M_short=21d, M_mid=63d, M_long=126d(skip 1M) |
| 过滤 | M_long > 0 |
| 持仓 | Top 5 等权 |
| 调仓 | 月度 |
| 止损 | -15% 硬止损 |

| 绩效 | 值 |
|------|------|
| 夏普比率 | [待填入] |
| 总收益率 | [待填入] |
| 年化收益率 | [待填入] |
| 最大回撤 | [待填入] |
| 胜率 | [待填入] |

---

## 八、策略2：风险调整趋势强度（Risk-Adjusted Trend）

| 参数 | 值 |
|------|-----|
| 股票池 | 44只（2020年前上市） |
| 信号 | (MA5>MA20 + MA10>MA50 + MA20>MA100) / vol |
| 门槛 | 趋势强度 ≥ 2 |
| 持仓 | Top 5 波动率倒数加权 |
| 调仓 | 周度 |
| 卖出 | 趋势强度 ≤ 1 或 -10% 硬止损 |

| 绩效 | 值 |
|------|------|
| 夏普比率 | [待填入] |
| 总收益率 | [待填入] |
| 年化收益率 | [待填入] |
| 最大回撤 | [待填入] |
| 胜率 | [待填入] |

---

## 九、三策略组合

| 策略 | 夏普 | 收益 | 回撤 | 胜率 |
|------|:---:|:---:|:---:|:---:|
| 策略1·双动量 | [ ] | [ ] | [ ] | [ ] |
| 策略2·趋势强度 | [ ] | [ ] | [ ] | [ ] |
| 策略3·基本面抄底 | 1.54 | 384% | 16.9% | 61% |
| **等权组合** | **[ ]** | **[ ]** | **[ ]** | **[ ]** |

**相关性矩阵：**
| | S1·动量 | S2·趋势 | S3·基本面 |
|---|:---:|:---:|:---:|
| S1·动量 | 1.00 | [ ] | [ ] |
| S2·趋势 | [ ] | 1.00 | [ ] |
| S3·基本面 | [ ] | [ ] | 1.00 |
```

- [ ] **Step 2: Fill in actual numbers after backtests complete**

Re-run all strategies, update the `[待填入]` placeholders with actual results.

- [ ] **Step 3: Commit**

```bash
git add STRATEGY_FINAL.md
git commit -m "docs: add Strategies 1 & 2 and three-strategy comparison to final report"
```

---

*Plan · 2026-07-22 · Execute with superpowers:subagent-driven-development or superpowers:executing-plans*
