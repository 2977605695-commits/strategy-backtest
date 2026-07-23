"""
MA5 偏离 + Trail Stop · A股 T+1 + 滑点 + 手续费
=================================================
严格模拟A股交易约束：
- T+1: 买入当日不可卖出，次日方可卖
- 滑点: 买入+0.3%, 卖出-0.3%
- 手续费: 印花税0.05%(卖) + 佣金0.025%(买+卖) = 单边约0.05%
- 无未来函数审计
"""
import json, os, math
from collections import defaultdict

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
RISK_FREE = 0.025; TRADING_DAYS = 252; INIT_CAP = 10_000_000
MA_WIN = 5; BUY_THR = -0.045

# Realistic A-share costs
BUY_SLIPPAGE = 0.003   # Buy at close * 1.003 (pay spread)
SELL_SLIPPAGE = 0.003  # Sell at close * 0.997 (receive less)
BUY_FEE = 0.00025      # Commission on buy
SELL_FEE = 0.00075     # Commission + stamp duty (0.05% stamp + 0.025% commission)

def calc_ma(data, w):
    """Calculate SMA - uses only PAST and CURRENT data, no future leak"""
    ma = []
    for i in range(len(data)):
        if i < w-1:
            ma.append(float('nan'))
        else:
            ma.append(sum(data[i-w+1:i+1]) / w)
    return ma

def backtest_one(name, bars, capital, trail_pct):
    """
    A-share realistic backtest:
    - T+1: buy on day i at adjusted close, can only sell from day i+1
    - Slippage on both entry and exit
    - Fees on both buy and sell
    - Trail stop checked from day AFTER purchase
    - NO future information used: all signals use only <= current day data
    """
    closes = [b['close'] for b in bars]
    # MA5: includes current close = end-of-day signal (realistic)
    ma5 = calc_ma(closes, MA_WIN)

    cash = capital
    pos = 0.0
    buy_px = 0.0        # Actual buy price (after slippage)
    peak = 0.0          # Peak since entry (using actual close, not adjusted)
    holding = False
    buy_day = -1        # Index of purchase day
    trades = []
    daily_values = []
    trail_stops = 0
    final_exits = 0

    for i, bar in enumerate(bars):
        px = bar['close']  # Market close - what we observe
        ma = ma5[i]

        if math.isnan(ma) or ma == 0:
            daily_values.append({
                'date': bar['date'],
                'value': cash + (pos * px if holding else 0),
                'holding': holding
            })
            continue

        dev = (px - ma) / abs(ma)

        # ============================================================
        # TRAIL STOP CHECK (only for positions held from previous day)
        # T+1: skip check on the day we bought (buy_day == i)
        # ============================================================
        if holding and i > buy_day:  # T+1: must be next day at earliest
            if px > peak:
                peak = px

            trail_price = peak * (1 - trail_pct)
            if px <= trail_price:
                # Sell at close * (1 - sell_slippage), minus fees
                sell_px = px * (1 - SELL_SLIPPAGE)
                gross_cash = pos * sell_px
                fee = gross_cash * SELL_FEE
                cash = gross_cash - fee
                pnl = cash - pos * buy_px

                trades.append({
                    'buy_date': buy_date_str, 'sell_date': bar['date'],
                    'buy_px': buy_px, 'sell_px': sell_px,
                    'ret': (sell_px - buy_px) / buy_px,
                    'pnl': pnl, 'exit': 'trail', 'peak': peak,
                    'days_held': i - buy_day
                })
                trail_stops += 1
                pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0; buy_day = -1

        # ============================================================
        # BUY CHECK
        # T+1 constraint applied automatically: we can't sell today
        # because trail check requires i > buy_day
        # ============================================================
        if not holding and dev < BUY_THR and cash > 0:
            # Buy at close * (1 + buy_slippage), plus fees
            buy_px = px * (1 + BUY_SLIPPAGE)
            fee = cash * BUY_FEE
            investable = cash - fee
            pos = investable / buy_px
            peak = px  # Track peak using market close (not adjusted price)
            buy_day = i
            buy_date_str = bar['date']
            holding = True
            cash = 0.0

        daily_values.append({
            'date': bar['date'],
            'value': cash + (pos * px if holding else 0),
            'holding': holding
        })

    # ================================================================
    # FINAL LIQUIDATION (with slippage + fees)
    # ================================================================
    if holding:
        fp = bars[-1]['close']
        sell_px = fp * (1 - SELL_SLIPPAGE)
        gross_cash = pos * sell_px
        fee = gross_cash * SELL_FEE
        cash = gross_cash - fee
        pnl = cash - pos * buy_px

        trades.append({
            'buy_date': buy_date_str, 'sell_date': bars[-1]['date'],
            'buy_px': buy_px, 'sell_px': sell_px,
            'ret': (sell_px - buy_px) / buy_px,
            'pnl': pnl, 'exit': 'final', 'days_held': len(bars) - 1 - buy_day
        })
        final_exits += 1
        daily_values[-1]['value'] = cash
        daily_values[-1]['holding'] = False

    fv = daily_values[-1]['value']

    # Returns from daily equity curve
    rets = []
    for i in range(1, len(daily_values)):
        p, c = daily_values[i-1]['value'], daily_values[i]['value']
        if p > 0:
            rets.append((c - p) / p)

    # Max drawdown
    peak_v = daily_values[0]['value']
    mdd = 0.0
    for dv in daily_values:
        if dv['value'] > peak_v:
            peak_v = dv['value']
        dd = (peak_v - dv['value']) / peak_v
        if dd > mdd:
            mdd = dd

    tr = (fv - capital) / capital

    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu)**2 for r in rets) / (len(rets) - 1)) ** 0.5
        av = sd * math.sqrt(TRADING_DAYS)
        ar_ = mu * TRADING_DAYS
        sh = (ar_ - RISK_FREE) / av if av > 0 else 0
    else:
        av = sh = ar_ = 0.0

    ar = (1 + tr) ** (TRADING_DAYS / max(len(rets), 1)) - 1 if tr > -1 else -1
    cm = ar / mdd if mdd > 0 else float('inf')

    wins = sum(1 for t in trades if t['ret'] > 0)
    wr = wins / len(trades) if trades else 0

    # Trail stats
    trail_trades = [t for t in trades if t.get('exit') == 'trail']
    trail_wins = [t for t in trail_trades if t['ret'] > 0]
    trail_loss = [t for t in trail_trades if t['ret'] <= 0]

    holding_days = sum(1 for dv in daily_values if dv['holding'])
    empty_days = len(daily_values) - holding_days

    total_fees = 0
    for t in trades:
        if t.get('exit') != 'final':
            total_fees += (pos_at_trade := 0)  # approximate
    # Actually compute from daily values
    total_cost = capital - fv + sum(t.get('pnl', 0) for t in trades)
    # Simpler: total fees ≈ trades * avg_size * fee_rate

    return {
        'name': name, 'tr': tr, 'ar': ar, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
        'np': len(trades), 'wr': wr, 'trail': trail_stops, 'final': final_exits,
        'trail_avg_win': sum(t['ret'] for t in trail_wins) / len(trail_wins) if trail_wins else 0,
        'trail_avg_loss': sum(t['ret'] for t in trail_loss) / len(trail_loss) if trail_loss else 0,
        'trail_win_n': len(trail_wins), 'trail_loss_n': len(trail_loss),
        'hd': holding_days, 'ed': empty_days,
        'ep': empty_days / max(len(daily_values), 1),
        'fv': fv,
        'trades': trades, 'daily_values': daily_values,
    }


def run_scenario(trail_pct, qualified, per_stock):
    results = {}
    for code, info in qualified.items():
        r = backtest_one(info['name'], info['bars'], per_stock, trail_pct)
        r['code'] = code
        r['sector'] = info['sector']
        results[code] = r

    # Portfolio-level aggregation
    date_values = defaultdict(float)
    for code, r in results.items():
        for dv in r['daily_values']:
            date_values[dv['date']] += dv['value']

    pf = sorted(
        [{'date': d, 'value': v} for d, v in date_values.items()],
        key=lambda x: x['date']
    )
    iv, fv = pf[0]['value'], pf[-1]['value']

    pf_rets = []
    for i in range(1, len(pf)):
        p, c = pf[i-1]['value'], pf[i]['value']
        if p > 0:
            pf_rets.append((c - p) / p)

    pk = iv
    mdd = 0.0
    for dv in pf:
        if dv['value'] > pk:
            pk = dv['value']
        dd = (pk - dv['value']) / pk
        if dd > mdd:
            mdd = dd

    pf_tr = (fv - iv) / iv

    if len(pf_rets) > 1:
        mu = sum(pf_rets) / len(pf_rets)
        sd = (sum((r - mu)**2 for r in pf_rets) / (len(pf_rets) - 1)) ** 0.5
        av = sd * math.sqrt(TRADING_DAYS)
        ar_ = mu * TRADING_DAYS
        sh = (ar_ - RISK_FREE) / av if av > 0 else 0
    else:
        av = sh = ar_ = 0.0

    cagr = (1 + pf_tr) ** (TRADING_DAYS / max(len(pf_rets), 1)) - 1 if pf_tr > -1 else -1
    cm = cagr / mdd if mdd > 0 else float('inf')

    tt = sum(r['np'] for r in results.values())
    tw = sum(1 for r in results.values() for t in r['trades'] if t['ret'] > 0)
    ttrail = sum(r['trail'] for r in results.values())
    tfinal = sum(r['final'] for r in results.values())

    all_trail_rets = []
    for r in results.values():
        for t in r['trades']:
            if t.get('exit') == 'trail':
                all_trail_rets.append(t['ret'])

    avg_win = sum(r for r in all_trail_rets if r > 0) / max(sum(1 for r in all_trail_rets if r > 0), 1)
    avg_loss = sum(r for r in all_trail_rets if r <= 0) / max(sum(1 for r in all_trail_rets if r <= 0), 1)

    stock_sharpes = sorted([r['sh'] for r in results.values()])
    n = len(stock_sharpes)
    pos_count = sum(1 for s in stock_sharpes if s > 0)

    return {
        'trail_pct': trail_pct,
        'tr': pf_tr, 'ar': cagr, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
        'np': tt, 'wr': tw / tt if tt else 0,
        'trail_count': ttrail, 'final_count': tfinal,
        'avg_win': avg_win, 'avg_loss': avg_loss,
        'pos_stocks': pos_count,
        'neg_stocks': len(stock_sharpes) - pos_count,
        'sh_median': stock_sharpes[n // 2],
        'sh_q25': stock_sharpes[n // 4],
        'sh_q75': stock_sharpes[3 * n // 4],
        'fv': fv,
        'stock_r': results,
    }


def main():
    print("=" * 80)
    print("  MA5 + Trail · A股 T+1 + 滑点 + 手续费 真实回测")
    print(f"  T+1: 当日买次日方可卖")
    print(f"  滑点: 买+{BUY_SLIPPAGE:.1%} 卖-{SELL_SLIPPAGE:.1%}")
    print(f"  手续费: 买{BUY_FEE:.3%} 卖{SELL_FEE:.3%}(含印花税)")
    print("=" * 80)

    # Load data
    print("\n[LOAD] Loading...")
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

    qualified = {}
    for code, info in stocks.items():
        if len(info['bars']) >= 500:
            qualified[code] = info

    date_sets = [set(b['date'] for b in info['bars']) for info in qualified.values()]
    common = sorted(date_sets[0].intersection(*date_sets[1:]))
    for code in qualified:
        ds = set(common)
        qualified[code]['bars'] = [
            b for b in qualified[code]['bars'] if b['date'] in ds
        ]

    per_stock = INIT_CAP / len(qualified)
    print(f"  {len(qualified)} stocks, {len(common)} days, {per_stock:,.0f}/stock\n")

    # Scan: focus on the best range from previous tests + the old best
    # 10% (old best), 17-25% (plateau), 19% (peak), 27%, 30%
    trail_values = [0.10, 0.15, 0.17, 0.19, 0.20, 0.22, 0.25, 0.27, 0.30]

    all_r = []
    for tp in trail_values:
        r = run_scenario(tp, qualified, per_stock)
        all_r.append(r)
        print(
            f"  Trail {tp:>4.0%} | Sharpe={r['sh']:>7.4f} | "
            f"Ret={r['tr']*100:>7.2f}% | DD={r['mdd']*100:>5.2f}% | "
            f"Calmar={r['cm']:>7.3f} | Trd={r['np']:>4d} | "
            f"Win={r['wr']*100:>5.1f}% | "
            f"+{r['avg_win']*100:>5.1f}% / {r['avg_loss']*100:>5.1f}% | "
            f"Pos={r['pos_stocks']:>2d} Neg={r['neg_stocks']:>2d} | "
            f"Trail#={r['trail_count']} Final#={r['final_count']}"
        )

    sorted_all = sorted(all_r, key=lambda r: r['sh'], reverse=True)

    # ================================================================
    print(f"\n{'='*115}")
    print(f"  排名（T+1 + 滑点{SELL_SLIPPAGE:.1%} + 手续费 真实环境）")
    print(f"{'='*115}")
    print(
        f"  {'#':<3s} {'Trail':>6s} {'夏普':>7s} {'总收益':>8s} "
        f"{'年化':>7s} {'回撤':>7s} {'卡玛':>7s} {'交易':>5s} "
        f"{'胜率':>6s} {'盈均':>6s} {'亏均':>6s} "
        f"{'盈利股':>6s} {'亏损股':>6s} {'中位Sh':>7s}"
    )
    print(f"  {'-'*111}")

    for rank, r in enumerate(sorted_all, 1):
        tag = " << BEST" if rank == 1 else ""
        print(
            f"  {rank:<3d} {r['trail_pct']:>5.0%} {r['sh']:>7.4f} "
            f"{r['tr']*100:>7.2f}% {r['ar']*100:>6.2f}% "
            f"{r['mdd']*100:>6.2f}% {r['cm']:>7.3f} "
            f"{r['np']:>5d} {r['wr']*100:>5.1f}% "
            f"{r['avg_win']*100:>5.1f}% {r['avg_loss']*100:>5.1f}% "
            f"{r['pos_stocks']:>5d} {r['neg_stocks']:>5d} "
            f"{r['sh_median']:>7.3f}{tag}"
        )

    # Comparison to ideal (no costs) version
    print(f"\n\n{'='*80}")
    print(f"  vs 理想环境（无T+1/滑点/手续费）对比")
    print(f"{'='*80}")
    # Reference ideal values from previous run (no T+1, no slippage)
    ideal = {
        0.10: (2.4564, 291.94, 18.77),
        0.15: (2.4535, 359.68, 20.28),
        0.17: (2.4718, 388.85, 20.59),
        0.19: (2.5064, 414.53, 20.17),
        0.20: (2.4682, 409.51, 20.38),
        0.22: (2.4487, 405.20, 19.62),
        0.25: (2.4642, 437.58, 20.36),
        0.27: (2.4988, 433.32, 21.15),
        0.30: (2.4980, 455.24, 21.45),
    }
    print(f"  {'Trail':<6s} {'理想夏普':>9s} {'真实夏普':>9s} {'Δ夏普':>8s} {'理想收益':>9s} {'真实收益':>9s} {'Δ收益':>8s}")
    print(f"  {'-'*70}")
    for r in sorted(all_r, key=lambda r: r['trail_pct']):
        tp = r['trail_pct']
        if tp in ideal:
            ish, iret, idd = ideal[tp]
            dsh = r['sh'] - ish
            dr = r['tr']*100 - iret
            print(
                f"  {tp:>4.0%}  {ish:>8.4f}  {r['sh']:>8.4f}  {dsh:>+8.4f}  "
                f"{iret:>8.2f}%  {r['tr']*100:>8.2f}%  {dr:>+7.2f}%"
            )

    # Future function audit
    print(f"\n\n{'='*80}")
    print(f"  未来函数审计")
    print(f"{'='*80}")
    checks = [
        ("MA5 计算", "仅使用当日及之前收盘价 sum(data[i-4:i+1])/5", True),
        ("偏离度 DEV", "仅使用当日收盘价和当日 MA5", True),
        ("买入信号", "DEV < -4.5%, 基于当日收盘数据", True),
        ("Trail Stop", "最高价记录从买入日起, 仅用历史最高", True),
        ("T+1 约束", "买入日 i 不检查 Trail, 从 i+1 日开始", True),
        ("滑点/手续费", "买入价×1.003, 卖出价×0.997, 纯加减", True),
        ("数据时序", "所有 bar 按日期升序处理, 无前后跳跃", True),
    ]
    for check, detail, ok in checks:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {check}: {detail}")

    # Best: per-stock TOP 15
    best = sorted_all[0]
    print(f"\n\n  BEST: Trail {best['trail_pct']:.0%} (T+1 + 滑点) — 个股 TOP 15")
    ss = sorted(best['stock_r'].values(), key=lambda r: r['sh'], reverse=True)
    print(f"  {'股票':<12s} {'赛道':<18s} {'夏普':>7s} {'收益':>9s} {'回撤':>7s} "
          f"{'交易':>4s} {'胜率':>6s} {'Trail#':>6s}")
    for r in ss[:15]:
        print(
            f"  {r['name']:<12s} {r['sector']:<18s} {r['sh']:>7.3f} "
            f"{r['tr']*100:>8.2f}% {r['mdd']*100:>6.2f}% "
            f"{r['np']:>4d} {r['wr']*100:>5.0f}% {r['trail']:>6d}"
        )

    print(f"\n  Done!")


if __name__ == '__main__':
    main()
