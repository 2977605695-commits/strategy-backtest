"""
Strategy 1: Pure Dual Momentum (纯双动量选股)
===============================================
Three timeframes: M_short (21d), M_mid (63d), M_long (126d, skip 1 month)
Score = Z(m_short)*0.2 + Z(m_mid)*0.3 + Z(m_long)*0.5
Filter: M_long > 0 (absolute momentum -- only buy stocks whose 6-month trend is up)
Monthly rebalance, top 5, equal weight, hard stop -15%, T+1, round lots.
"""
import math, os, csv
from collections import defaultdict
from data_loader import (
    DATA_DIR, load_prices, get_common_dates,
    compute_momentum_signals,
)

# ----- Constants -----
RISK_FREE  = 0.025
TD         = 252
INIT_CAP   = 10_000_000
MAX_POS    = 5
SLIP       = 0.003
BUY_FEE    = 0.00025
SELL_FEE   = 0.00075      # includes stamp tax
W_SHORT    = 0.2
W_MID      = 0.3
W_LONG     = 0.5
HARD_STOP  = -0.15

ROUND_LOT  = 100           # A-share round-lot rule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_first_trading_day(date_str, common_dates):
    """Return True if *date_str* is the first common date of its calendar month."""
    month_key = date_str[:7]               # "YYYY-MM"
    for d in common_dates:
        if d[:7] == month_key:
            return d == date_str
    return False


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

    print("Computing momentum signals...")
    mom = compute_momentum_signals(stocks, common_dates)

    # 2. Find trading start (first date >= 2021-01-02) ----------------------
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

    # 3. State variables ----------------------------------------------------
    cash         = float(INIT_CAP)
    holdings     = {}          # code -> {shares, cost, buy_date, buy_day_idx}
    daily_equity = []          # [{date, equity}, ...]
    trades       = []          # [{code, name, ret, buy_date, sell_date, exit}, ...]

    risk_free_daily = (1 + RISK_FREE) ** (1 / TD) - 1

    # Record flat equity for warm-up dates (before start_idx)
    for i in range(start_idx):
        daily_equity.append({
            'date': common_dates[i],
            'equity': INIT_CAP,
        })

    # 4. Day-by-day loop ----------------------------------------------------
    for day_idx in range(start_idx, len(common_dates)):
        date_str = common_dates[day_idx]

        # --- 4a. Risk-free interest on cash ---------------------------------
        cash *= (1 + risk_free_daily)

        # --- 4b. Hard-stop check (any day) ----------------------------------
        stop_codes = []
        for code, h in list(holdings.items()):
            # T+1: cannot sell on the same day bought
            if date_str == h['buy_date']:
                continue

            close_px = stocks[code]['close'][
                stocks[code]['dates'].index(date_str)]

            loss_pct = (close_px - h['cost']) / h['cost']
            if loss_pct <= HARD_STOP:
                stop_codes.append(code)

        for code in stop_codes:
            h = holdings[code]
            close_px = stocks[code]['close'][
                stocks[code]['dates'].index(date_str)]
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

        # --- 4c. Monthly rebalance (first trading day of the month) ---------
        if is_first_trading_day(date_str, common_dates):
            # Score every valid stock (M_long > 0)
            valid = {}
            for code in stocks:
                sig = mom.get(code, {}).get(date_str)
                if sig is None:
                    continue
                ms, mm, ml = sig['m_short'], sig['m_mid'], sig['m_long']
                if any(math.isnan(x) for x in (ms, mm, ml)):
                    continue
                if ml <= 0:                    # absolute momentum filter
                    continue
                valid[code] = {'m_short': ms, 'm_mid': mm, 'm_long': ml}

            if len(valid) >= 2:
                # Cross-sectional z-scores
                z_s = cross_sectional_z({c: v['m_short'] for c, v in valid.items()})
                z_m = cross_sectional_z({c: v['m_mid']   for c, v in valid.items()})
                z_l = cross_sectional_z({c: v['m_long']  for c, v in valid.items()})

                scores = {}
                for code in valid:
                    scores[code] = (
                        z_s.get(code, 0.0) * W_SHORT +
                        z_m.get(code, 0.0) * W_MID   +
                        z_l.get(code, 0.0) * W_LONG
                    )

                # Rank: top MAX_POS
                top5 = [code for code, _ in
                        sorted(scores.items(), key=lambda x: x[1], reverse=True)[:MAX_POS]]

                # --- Sell stocks no longer in Top 5 -------------------------
                for code in list(holdings.keys()):
                    if code not in top5 and date_str != holdings[code]['buy_date']:
                        h = holdings[code]
                        close_px  = stocks[code]['close'][
                            stocks[code]['dates'].index(date_str)]
                        sell_px   = close_px * (1 - SLIP - SELL_FEE)
                        proceeds  = h['shares'] * sell_px
                        cash     += proceeds

                        ret = (sell_px - h['cost']) / h['cost']
                        trades.append({
                            'code': code, 'name': stocks[code]['name'],
                            'ret': ret, 'buy_date': h['buy_date'],
                            'sell_date': date_str, 'exit': 'rebalance',
                        })
                        del holdings[code]

                # --- Buy new Top 5 stocks (equal weight) --------------------
                to_buy = [c for c in top5 if c not in holdings]
                if to_buy:
                    # current total equity (cash + MTM of holdings)
                    total_eq = cash
                    for code, h in holdings.items():
                        px = stocks[code]['close'][
                            stocks[code]['dates'].index(date_str)]
                        total_eq += h['shares'] * px

                    target_per_pos = total_eq / MAX_POS

                    for code in to_buy:
                        close_px = stocks[code]['close'][
                            stocks[code]['dates'].index(date_str)]
                        buy_px = close_px * (1 + SLIP + BUY_FEE)

                        if buy_px <= 0:
                            continue

                        raw_shares = target_per_pos / buy_px
                        lots = int(raw_shares // ROUND_LOT) * ROUND_LOT

                        if lots >= ROUND_LOT:
                            cost = lots * buy_px
                            if cost <= cash:
                                cash -= cost
                                holdings[code] = {
                                    'shares':      lots,
                                    'cost':        buy_px,
                                    'buy_date':    date_str,
                                    'buy_day_idx': day_idx,
                                }

        # --- 4d. Mark-to-market equity --------------------------------------
        equity = cash
        for code, h in holdings.items():
            close_px = stocks[code]['close'][
                stocks[code]['dates'].index(date_str)]
            equity += h['shares'] * close_px

        daily_equity.append({'date': date_str, 'equity': equity})

    # 5. Force-close on last day --------------------------------------------
    last_date = common_dates[-1]
    for code, h in list(holdings.items()):
        close_px = stocks[code]['close'][
            stocks[code]['dates'].index(last_date)]
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

    # 6. Statistics ----------------------------------------------------------
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
    print(f"  Pure Dual Momentum Strategy -- Results")
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
    for ext in ['stop', 'rebalance', 'final']:
        et = exit_types.get(ext)
        if et:
            avg_r = sum(et['rets']) / len(et['rets']) * 100
            wr    = sum(1 for r in et['rets'] if r > 0) / len(et['rets']) * 100
            print(f"    {ext:<12s}  count={et['count']:>3d}  avg_ret={avg_r:>+7.2f}%  "
                  f"wr={wr:>5.1f}%")

    # ---- Save equity curve ------------------------------------------------
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'strategy1_equity.csv')
    with open(out_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['date', 'equity'])
        for dv in daily_equity:
            writer.writerow([dv['date'], f"{dv['equity']:.2f}"])

    print(f"\n  Equity curve saved to  {out_path}")
    print(f"\nDone!")


if __name__ == '__main__':
    main()
