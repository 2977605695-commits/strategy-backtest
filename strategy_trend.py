"""
Strategy 2: Risk-Adjusted Trend Strength (风险调整趋势强度)
============================================================
Three-layer MA crossover scoring (0-3) divided by volatility, cross-sectional
z-scored. Weekly rebalance (Mondays), top 5, volatility-inverse weighted.
Hard stop -10%, T+1, round lots.
"""
import math, os, csv, datetime
from collections import defaultdict
from data_loader import (
    DATA_DIR, load_prices, get_common_dates,
    compute_trend_signals,
)

# ----- Constants -----
RISK_FREE  = 0.025
TD         = 252
INIT_CAP   = 10_000_000
MAX_POS    = 5
SLIP       = 0.003
BUY_FEE    = 0.00025
SELL_FEE   = 0.00075
MIN_TREND  = 2
SELL_TREND = 1
HARD_STOP  = -0.10

ROUND_LOT  = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _weekday(date_str):
    """Return Python weekday for a 'YYYY-MM-DD' string (0=Monday ... 6=Sunday)."""
    y, m, d = map(int, date_str.split('-'))
    return datetime.date(y, m, d).weekday()


def build_rebalance_set(common_dates, start_idx):
    """
    Return a function that, given a day index, says whether it is a rebalance day.
    Prefers Mondays; falls back to every 5th trading day if no Monday exists.
    """
    # Check if any Monday exists in the trading range
    has_monday = any(_weekday(d) == 0 for d in common_dates[start_idx:])

    if has_monday:
        def is_rebalance(day_idx):
            return _weekday(common_dates[day_idx]) == 0
        return is_rebalance
    else:
        # Every 5th trading day
        rebalance_indices = set(range(start_idx, len(common_dates), 5))
        def is_rebalance(day_idx):
            return day_idx in rebalance_indices
        return is_rebalance


def cross_sectional_z(values_dict):
    """
    Compute cross-sectional population z-scores (dividing by N, not N-1).
    *values_dict*:  {code: float}
    Returns:         {code: float}
    """
    if len(values_dict) < 2:
        return {}
    codes = list(values_dict.keys())
    vals  = [values_dict[c] for c in codes]
    n     = len(vals)

    mu    = sum(vals) / n
    var   = sum((v - mu) ** 2 for v in vals) / n
    sigma = var ** 0.5 if var > 0 else 1.0

    return {c: (values_dict[c] - mu) / sigma for c in codes}


# ---------------------------------------------------------------------------
# Main backtest
# ---------------------------------------------------------------------------

def main():
    # 1. Load data ----------------------------------------------------------
    print("Loading prices (old stocks)...")
    stocks = load_prices('old')
    print(f"  {len(stocks)} stocks loaded.")

    common_dates = get_common_dates(stocks)
    print(f"  {len(common_dates)} common trading days "
          f"({common_dates[0]} -> {common_dates[-1]})")

    print("Computing trend signals...")
    trend = compute_trend_signals(stocks, common_dates)

    # 2. Pre-build date-to-index map (O(1) price lookups) -------------------
    date_idx = {code: {d: i for i, d in enumerate(s['dates'])}
                for code, s in stocks.items()}

    # 3. Find trading start (first date >= 2021-01-02) ----------------------
    start_idx = None
    for i, d in enumerate(common_dates):
        if d >= '2021-01-02':
            start_idx = i
            break

    if start_idx is None:
        print("ERROR: No common date >= 2021-01-02 found.")
        return

    print(f"Trading period: {common_dates[start_idx]} -> {common_dates[-1]} "
          f"({len(common_dates) - start_idx} days)")

    # 4. State variables ----------------------------------------------------
    cash         = float(INIT_CAP)
    holdings     = {}          # code -> {shares, cost, buy_date, buy_day_idx}
    daily_equity = []          # [{date, equity}, ...]
    trades       = []          # [{code, name, ret, buy_date, sell_date, exit}, ...]

    risk_free_daily = (1 + RISK_FREE) ** (1 / TD) - 1

    # Build rebalance-day detector
    is_rebalance = build_rebalance_set(common_dates, start_idx)

    # Record flat equity for warm-up dates (before start_idx)
    for i in range(start_idx):
        daily_equity.append({
            'date': common_dates[i],
            'equity': INIT_CAP,
        })

    # 5. Day-by-day loop ----------------------------------------------------
    for day_idx in range(start_idx, len(common_dates)):
        date_str = common_dates[day_idx]

        # --- 5a. Risk-free interest on cash ---------------------------------
        cash *= (1 + risk_free_daily)

        # --- 5b. Hard-stop check (any day) ----------------------------------
        # Track stocks stopped out on a rebalance day to block same-day re-buy
        blocked = set()

        stop_codes = []
        for code, h in list(holdings.items()):
            # T+1: cannot sell on the same day purchased
            if date_str == h['buy_date']:
                continue

            di = date_idx[code].get(date_str)
            if di is None:
                continue
            close_px = stocks[code]['close'][di]

            loss_pct = (close_px - h['cost']) / h['cost']
            if loss_pct <= HARD_STOP:
                stop_codes.append(code)

        for code in stop_codes:
            h = holdings[code]
            di = date_idx[code][date_str]
            close_px = stocks[code]['close'][di]
            sell_px   = close_px * (1 - SLIP - SELL_FEE)
            proceeds  = h['shares'] * sell_px
            cash     += proceeds

            ret = (sell_px - h['cost']) / h['cost']
            trades.append({
                'code': code, 'name': stocks[code]['name'],
                'ret': ret, 'buy_date': h['buy_date'],
                'sell_date': date_str, 'exit': 'stop',
            })
            del holdings[code]

            # If stopped out on a rebalance day, block same-day re-buy
            if is_rebalance(day_idx):
                blocked.add(code)

        # --- 5c. Weekly rebalance (Monday or every 5th day) -----------------
        if is_rebalance(day_idx):
            # Score every stock: raw_score = trend_raw / vol, then z-score
            # Only stocks with trend_raw >= MIN_TREND are eligible
            raw_scores = {}
            for code in stocks:
                sig = trend.get(code, {}).get(date_str)
                if sig is None:
                    continue
                tr = sig['trend_raw']
                vl = sig['vol']
                if tr < MIN_TREND:
                    continue
                if vl <= 0:
                    vl = 1e-6   # avoid division by zero
                raw_scores[code] = tr / vl

            # Cross-sectional z-score of raw scores
            z_scores = cross_sectional_z(raw_scores)

            # Rank: top MAX_POS (only eligible, not blocked)
            eligible = [(code, z_scores[code]) for code in raw_scores
                        if code not in blocked]
            eligible.sort(key=lambda x: x[1], reverse=True)
            top5 = [code for code, _ in eligible[:MAX_POS]]

            # --- Sell stocks no longer in Top 5 or with trend_raw <= SELL_TREND
            for code in list(holdings.keys()):
                h = holdings[code]
                # T+1: cannot sell on the same day purchased
                if date_str == h['buy_date']:
                    continue

                sig = trend.get(code, {}).get(date_str)
                tr_now = sig['trend_raw'] if sig else 0

                should_sell = (code not in top5) or (tr_now <= SELL_TREND)
                if should_sell:
                    di = date_idx[code][date_str]
                    close_px  = stocks[code]['close'][di]
                    sell_px   = close_px * (1 - SLIP - SELL_FEE)
                    proceeds  = h['shares'] * sell_px
                    cash     += proceeds

                    ret = (sell_px - h['cost']) / h['cost']
                    exit_reason = 'trend' if tr_now <= SELL_TREND else 'rebalance'
                    trades.append({
                        'code': code, 'name': stocks[code]['name'],
                        'ret': ret, 'buy_date': h['buy_date'],
                        'sell_date': date_str, 'exit': exit_reason,
                    })
                    del holdings[code]

            # --- Compute total equity ----------------------------------------
            total_eq = cash
            for code, h in holdings.items():
                di = date_idx[code].get(date_str)
                if di is None:
                    continue
                px = stocks[code]['close'][di]
                total_eq += h['shares'] * px

            # --- Volatility-inverse weights for top5 -------------------------
            if top5:
                vols_top = {}
                for code in top5:
                    sig = trend.get(code, {}).get(date_str)
                    v = sig['vol'] if sig else 0.0
                    if v <= 0:
                        v = 1e-6
                    vols_top[code] = v

                inv_vol = {code: 1.0 / v for code, v in vols_top.items()}
                total_inv = sum(inv_vol.values())
                weights = {code: inv_vol[code] / total_inv for code in top5}

                # --- Adjust existing positions / buy new ----------------------
                # First pass: compute target values
                targets = {code: total_eq * weights[code] for code in top5}

                # Sell excess from existing positions in top5 (respect T+1)
                for code in top5:
                    if code not in holdings:
                        continue
                    h = holdings[code]
                    if date_str == h['buy_date']:
                        # Cannot sell on purchase day -- skip downward adjustment
                        continue

                    di = date_idx[code][date_str]
                    close_px = stocks[code]['close'][di]
                    current_val = h['shares'] * close_px
                    target_val = targets[code]

                    if current_val > target_val:
                        # Sell excess
                        excess_val = current_val - target_val
                        sell_px = close_px * (1 - SLIP - SELL_FEE)
                        if sell_px <= 0:
                            continue
                        shares_to_sell = int(excess_val / sell_px // ROUND_LOT) * ROUND_LOT
                        if shares_to_sell >= ROUND_LOT and shares_to_sell <= h['shares']:
                            proceeds = shares_to_sell * sell_px
                            cash += proceeds
                            h['shares'] -= shares_to_sell
                            if h['shares'] <= 0:
                                del holdings[code]

                # Recompute total equity after sells (cash changed, holdings changed)
                total_eq = cash
                for code, h in holdings.items():
                    di = date_idx[code].get(date_str)
                    if di is None:
                        continue
                    px = stocks[code]['close'][di]
                    total_eq += h['shares'] * px

                # Recompute targets after equity update
                targets = {code: total_eq * weights[code] for code in top5}

                # Buy up to target for positions in top5 (new or existing)
                # Sort by weight descending so we allocate cash to highest-weight first
                sorted_top5 = sorted(top5, key=lambda c: weights[c], reverse=True)
                for code in sorted_top5:
                    if code in blocked:
                        continue

                    di = date_idx[code].get(date_str)
                    if di is None:
                        continue
                    close_px = stocks[code]['close'][di]
                    buy_px = close_px * (1 + SLIP + BUY_FEE)
                    if buy_px <= 0:
                        continue

                    current_val = 0.0
                    if code in holdings:
                        current_val = holdings[code]['shares'] * close_px

                    target_val = targets[code]

                    if target_val > current_val:
                        # Buy more
                        buy_val = target_val - current_val
                        raw_shares = buy_val / buy_px
                        lots = int(raw_shares // ROUND_LOT) * ROUND_LOT
                        if lots >= ROUND_LOT:
                            cost = lots * buy_px
                            if cost <= cash:
                                cash -= cost
                                if code in holdings:
                                    # Average up cost basis
                                    old_cost = holdings[code]['cost']
                                    old_shares = holdings[code]['shares']
                                    total_shares = old_shares + lots
                                    holdings[code]['cost'] = (
                                        (old_cost * old_shares + cost) / total_shares
                                    )
                                    holdings[code]['shares'] = total_shares
                                    # Keep original buy_date for cost-basis tracking
                                else:
                                    holdings[code] = {
                                        'shares':      lots,
                                        'cost':        buy_px,
                                        'buy_date':    date_str,
                                        'buy_day_idx': day_idx,
                                    }

        # --- 5d. Mark-to-market equity --------------------------------------
        equity = cash
        for code, h in holdings.items():
            di = date_idx[code].get(date_str)
            if di is None:
                continue
            close_px = stocks[code]['close'][di]
            equity += h['shares'] * close_px

        daily_equity.append({'date': date_str, 'equity': equity})

    # 6. Force-close on last day --------------------------------------------
    last_date = common_dates[-1]
    for code, h in list(holdings.items()):
        di = date_idx[code].get(last_date)
        if di is None:
            continue
        close_px = stocks[code]['close'][di]
        sell_px  = close_px * (1 - SLIP - SELL_FEE)
        proceeds = h['shares'] * sell_px
        cash    += proceeds

        ret = (sell_px - h['cost']) / h['cost']
        trades.append({
            'code': code, 'name': stocks[code]['name'],
            'ret': ret, 'buy_date': h['buy_date'],
            'sell_date': last_date, 'exit': 'final',
        })
    holdings.clear()

    # Append final equity entry after force-close (liquidation value)
    daily_equity.append({'date': last_date, 'equity': cash})

    # 7. Statistics ----------------------------------------------------------
    final_equity = daily_equity[-1]['equity']
    total_ret    = (final_equity - INIT_CAP) / INIT_CAP

    # Daily return series
    daily_rets = []
    for i in range(1, len(daily_equity)):
        prev = daily_equity[i - 1]['equity']
        curr = daily_equity[i]['equity']
        if prev > 0:
            daily_rets.append((curr - prev) / prev)

    # Arithmetic annual return
    if daily_rets:
        mean_daily  = sum(daily_rets) / len(daily_rets)
        ann_ret     = mean_daily * TD
    else:
        mean_daily = ann_ret = 0.0

    # CAGR (geometric annual return) for Calmar
    if daily_rets and final_equity > 0:
        years = len(daily_rets) / TD
        cagr  = (final_equity / INIT_CAP) ** (1 / years) - 1
    else:
        years = cagr = 0.0

    # Sharpe ratio
    if len(daily_rets) > 1:
        mu  = sum(daily_rets) / len(daily_rets)
        sd  = (sum((r - mu) ** 2 for r in daily_rets) / (len(daily_rets) - 1)) ** 0.5
        av  = sd * math.sqrt(TD)
        sharpe = (ann_ret - RISK_FREE) / av if av > 0 else 0.0
    else:
        sd = av = sharpe = 0.0

    # Max drawdown
    peak = daily_equity[0]['equity']
    mdd  = 0.0
    for dv in daily_equity:
        eq = dv['equity']
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak
        if dd > mdd:
            mdd = dd

    # Calmar ratio
    calmar = cagr / mdd if mdd > 0 else float('inf')

    # Win rate
    wins     = sum(1 for t in trades if t['ret'] > 0)
    win_rate = wins / len(trades) if trades else 0.0

    # ---- Print ------------------------------------------------------------
    print(f"\n{'=' * 60}")
    print(f"  Risk-Adjusted Trend Strength Strategy -- Results")
    print(f"{'=' * 60}")
    print(f"  Total Return %:    {total_ret * 100:>10.2f}%")
    print(f"  Annual Return %:   {ann_ret * 100:>10.2f}%")
    print(f"  Sharpe Ratio:      {sharpe:>10.4f}")
    print(f"  Max Drawdown %:    {mdd * 100:>10.2f}%")
    print(f"  Calmar Ratio:      {calmar:>10.4f}")
    print(f"  # Trades:          {len(trades):>10d}")
    print(f"  Win Rate %:        {win_rate * 100:>10.2f}%")
    print(f"  CAGR:              {cagr * 100:>10.2f}%")
    print(f"{'=' * 60}")

    # ---- Exit breakdown ---------------------------------------------------
    exit_types = defaultdict(lambda: {'count': 0, 'rets': []})
    for t in trades:
        exit_types[t['exit']]['count'] += 1
        exit_types[t['exit']]['rets'].append(t['ret'])
    print(f"\n  Exit breakdown:")
    for ext in ['stop', 'trend', 'rebalance', 'final']:
        et = exit_types.get(ext)
        if et:
            avg_r = sum(et['rets']) / len(et['rets']) * 100
            wr    = sum(1 for r in et['rets'] if r > 0) / len(et['rets']) * 100
            print(f"    {ext:<12s}  count={et['count']:>3d}  avg_ret={avg_r:>+7.2f}%  "
                  f"wr={wr:>5.1f}%")

    # ---- Save equity curve ------------------------------------------------
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'strategy2_equity.csv')
    with open(out_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['date', 'equity'])
        for dv in daily_equity:
            writer.writerow([dv['date'], f"{dv['equity']:.2f}"])

    print(f"\n  Equity curve saved to  {out_path}")
    print(f"\nDone!")


if __name__ == '__main__':
    main()
