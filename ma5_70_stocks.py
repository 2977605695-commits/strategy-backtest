"""
MA5 均线偏离策略 · 70只个股等权回测
=====================================
纯 MA5 偏离：DEV < -4.5% 买入，DEV > +7% 卖出
无止损、无止盈条件（取消所有止盈条件）
等权分配，独立交易
"""
import json, os, math
from collections import defaultdict

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
START_DATE = '2020-01-01'
END_DATE = '2026-07-01'
RISK_FREE = 0.025
TRADING_DAYS = 252
INIT_CAP = 10_000_000  # 1000万总资金
MA_WIN = 5
BUY_THR = -0.045   # DEV < -4.5% 买入
SELL_THR = 0.07    # DEV > +7% 卖出

def calc_ma(data, w):
    ma = []
    for i in range(len(data)):
        if i < w-1:
            ma.append(float('nan'))
        else:
            ma.append(sum(data[i-w+1:i+1]) / w)
    return ma

def backtest_one(name, bars, capital):
    """Pure MA5 deviation: buy at DEV < -4.5%, sell at DEV > +7%"""
    closes = [b['close'] for b in bars]
    ma5 = calc_ma(closes, MA_WIN)

    cash = capital
    pos = 0.0
    buy_px = 0.0
    holding = False
    trades = []
    daily_values = []

    for i, bar in enumerate(bars):
        px = bar['close']
        ma = ma5[i]

        if math.isnan(ma) or ma == 0:
            daily_values.append({'date': bar['date'], 'value': cash + (pos * px if holding else 0), 'holding': holding})
            continue

        dev = (px - ma) / abs(ma)

        # Sell check
        if holding and dev > SELL_THR:
            cash = pos * px
            pnl = cash - pos * buy_px
            trades.append({'buy_date': buy_date, 'sell_date': bar['date'],
                          'buy_px': buy_px, 'sell_px': px,
                          'ret': (px - buy_px) / buy_px, 'pnl': pnl})
            pos = 0.0
            buy_px = 0.0
            holding = False

        # Buy check
        if not holding and dev < BUY_THR and cash > 0:
            pos = cash / px
            buy_px = px
            buy_date = bar['date']
            holding = True
            cash = 0.0

        daily_values.append({'date': bar['date'], 'value': cash + (pos * px if holding else 0), 'holding': holding})

    # Final liquidation
    if holding:
        fp = bars[-1]['close']
        cash = pos * fp
        pnl = cash - pos * buy_px
        trades.append({'buy_date': buy_date, 'sell_date': bars[-1]['date'],
                      'buy_px': buy_px, 'sell_px': fp,
                      'ret': (fp - buy_px) / buy_px, 'pnl': pnl})
        daily_values[-1]['value'] = cash
        daily_values[-1]['holding'] = False

    fv = daily_values[-1]['value']

    # Calculate metrics
    rets = []
    for i in range(1, len(daily_values)):
        p, c = daily_values[i-1]['value'], daily_values[i]['value']
        if p > 0:
            rets.append((c - p) / p)

    # Max drawdown
    peak = daily_values[0]['value']
    mdd = 0.0
    for dv in daily_values:
        if dv['value'] > peak:
            peak = dv['value']
        dd = (peak - dv['value']) / peak
        if dd > mdd:
            mdd = dd

    tr = (fv - capital) / capital  # total return

    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu)**2 for r in rets) / (len(rets) - 1)) ** 0.5
        ann_vol = sd * math.sqrt(TRADING_DAYS)
        ann_ret = mu * TRADING_DAYS
        sharpe = (ann_ret - RISK_FREE) / ann_vol if ann_vol > 0 else 0
    else:
        ann_vol = sharpe = ann_ret = 0.0

    # Annualized return from total return
    ar = (1 + tr) ** (TRADING_DAYS / max(len(rets), 1)) - 1 if tr > -1 else -1
    calmar = ar / mdd if mdd > 0 else float('inf')

    wins = sum(1 for t in trades if t['ret'] > 0)
    win_rate = wins / len(trades) if trades else 0
    holding_days = sum(1 for dv in daily_values if dv['holding'])
    empty_days = len(daily_values) - holding_days

    return {
        'name': name,
        'total_return': tr,
        'ann_return': ar,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': mdd,
        'calmar': calmar,
        'num_trades': len(trades),
        'win_rate': win_rate,
        'holding_days': holding_days,
        'empty_days': empty_days,
        'empty_pct': empty_days / len(daily_values) if daily_values else 0,
        'final_value': fv,
        'trades': trades,
        'daily_values': daily_values,
    }

def main():
    # Load all stocks
    print("=" * 80)
    print("  MA5 均线偏离策略 · 70只个股等权回测")
    print(f"  Buy: DEV(MA5) < {BUY_THR:.1%}  |  Sell: DEV(MA5) > {SELL_THR:.0%}")
    print(f"  无止损 · 无止盈条件 · 等权分配")
    print("=" * 80)

    print("\n[LOAD] Loading data...")
    stocks = {}
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'):
            continue
        fpath = os.path.join(DATA_DIR, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        code = data['code']
        name = data['name']
        sector = data['sector']
        bars = data['bars']
        stocks[code] = {'name': name, 'sector': sector, 'bars': bars}

    print(f"  Loaded {len(stocks)} stocks")

    # Find common date range: use 2024-01-01 to 2026-07-01 for fairness
    # (most stocks have data from 2020, but some are newer)
    # For max coverage, use stocks with >= 500 bars (~2 years)
    qualified = {}
    skipped = []
    for code, info in stocks.items():
        if len(info['bars']) >= 500:
            qualified[code] = info
        else:
            skipped.append(f"{info['name']}({len(info['bars'])} bars)")

    print(f"\n  Qualified (>=500 bars): {len(qualified)} stocks")
    if skipped:
        print(f"  Skipped (<500 bars): {', '.join(skipped)}")

    # Find common dates across qualified stocks
    date_sets = [set(b['date'] for b in info['bars']) for info in qualified.values()]
    common_dates = sorted(date_sets[0].intersection(*date_sets[1:]))
    print(f"  Common trading days: {len(common_dates)}")

    # Filter each stock to common dates
    for code in qualified:
        date_set = set(common_dates)
        qualified[code]['bars'] = [b for b in qualified[code]['bars'] if b['date'] in date_set]

    per_stock_cap = INIT_CAP / len(qualified)

    # Run backtest
    print(f"\n[BACKTEST] Running on {len(qualified)} stocks, "
          f"{per_stock_cap:,.0f} per stock...\n")

    results = {}
    for code, info in qualified.items():
        name = info['name']
        sector = info['sector']
        r = backtest_one(name, info['bars'], per_stock_cap)
        r['code'] = code
        r['sector'] = sector
        results[code] = r

    # Build portfolio daily values
    date_values = defaultdict(float)
    for code, r in results.items():
        for dv in r['daily_values']:
            date_values[dv['date']] += dv['value']

    portfolio_dvs = sorted([{'date': d, 'value': v} for d, v in date_values.items()],
                          key=lambda x: x['date'])

    # Portfolio metrics
    iv = portfolio_dvs[0]['value']
    fv = portfolio_dvs[-1]['value']
    port_rets = []
    for i in range(1, len(portfolio_dvs)):
        p, c = portfolio_dvs[i-1]['value'], portfolio_dvs[i]['value']
        if p > 0:
            port_rets.append((c - p) / p)

    peak = iv
    mdd = 0.0
    for dv in portfolio_dvs:
        if dv['value'] > peak:
            peak = dv['value']
        dd = (peak - dv['value']) / peak
        if dd > mdd:
            mdd = dd

    port_tr = (fv - iv) / iv
    if len(port_rets) > 1:
        mu = sum(port_rets) / len(port_rets)
        sd = (sum((r - mu)**2 for r in port_rets) / (len(port_rets) - 1)) ** 0.5
        port_av = sd * math.sqrt(TRADING_DAYS)
        port_ar = mu * TRADING_DAYS
        port_sh = (port_ar - RISK_FREE) / port_av if port_av > 0 else 0
    else:
        port_av = port_sh = port_ar = 0.0
    port_ar_cagr = (1 + port_tr) ** (TRADING_DAYS / max(len(port_rets), 1)) - 1 if port_tr > -1 else -1
    port_cm = port_ar_cagr / mdd if mdd > 0 else float('inf')

    total_trades = sum(r['num_trades'] for r in results.values())
    total_wins = sum(sum(1 for t in r['trades'] if t['ret'] > 0) for r in results.values())
    total_win_rate = total_wins / total_trades if total_trades else 0

    # ================================================================
    # OUTPUT
    # ================================================================
    print("\n" + "=" * 80)
    print("  组合级别汇总")
    print("=" * 80)
    print(f"  回测区间: {portfolio_dvs[0]['date']} → {portfolio_dvs[-1]['date']}")
    print(f"  交易日数: {len(portfolio_dvs)}")
    print(f"  股票数量: {len(qualified)}")
    print(f"  初始资金: {INIT_CAP:,.0f}")
    print(f"  最终资金: {fv:,.0f}")
    print()
    print(f"  {'总收益率':<16s} | {port_tr*100:>10.2f}%")
    print(f"  {'年化收益率':<16s} | {port_ar_cagr*100:>10.2f}%")
    print(f"  {'年化波动率':<16s} | {port_av*100:>10.2f}%")
    print(f"  {'夏普比率':<16s} | {port_sh:>10.4f}")
    print(f"  {'最大回撤':<16s} | {mdd*100:>10.2f}%")
    print(f"  {'卡玛比率':<16s} | {port_cm:>10.4f}")
    print(f"  {'总交易笔数':<16s} | {total_trades:>10d}")
    print(f"  {'胜率':<16s} | {total_win_rate*100:>10.1f}%")

    # ================================================================
    # Per-stock ranking by Sharpe
    # ================================================================
    sorted_stocks = sorted(results.values(), key=lambda r: r['sharpe'], reverse=True)

    print(f"\n\n{'='*100}")
    print(f"  个股排名（按夏普比率排序）")
    print(f"{'='*100}")
    print(f"  {'排名':<4s} {'股票':<12s} {'赛道':<16s} {'夏普':>7s} {'总收益':>9s} "
          f"{'年化':>8s} {'最大回撤':>7s} {'卡玛':>7s} {'交易':>4s} {'胜率':>6s} "
          f"{'空仓%':>6s}")
    print(f"  {'-'*96}")

    for rank, r in enumerate(sorted_stocks, 1):
        print(f"  {rank:<4d} {r['name']:<12s} {r['sector']:<16s} "
              f"{r['sharpe']:>7.3f} {r['total_return']*100:>8.2f}% "
              f"{r['ann_return']*100:>7.2f}% {r['max_dd']*100:>6.2f}% "
              f"{r['calmar']:>7.3f} {r['num_trades']:>4d} {r['win_rate']*100:>5.0f}% "
              f"{r['empty_pct']*100:>5.0f}%")

    # Bottom 5
    print(f"\n  --- 倒数 5 名 ---")
    for rank, r in enumerate(sorted_stocks[-5:], len(sorted_stocks)-4):
        print(f"  {rank:<4d} {r['name']:<12s} {r['sector']:<16s} "
              f"{r['sharpe']:>7.3f} {r['total_return']*100:>8.2f}% "
              f"{r['ann_return']*100:>7.2f}% {r['max_dd']*100:>6.2f}% "
              f"{r['calmar']:>7.3f} {r['num_trades']:>4d} {r['win_rate']*100:>5.0f}% "
              f"{r['empty_pct']*100:>5.0f}%")

    # Sector summary
    print(f"\n\n{'='*60}")
    print(f"  赛道汇总")
    print(f"{'='*60}")
    sector_stats = defaultdict(lambda: {'count': 0, 'sharpe_sum': 0, 'ret_sum': 0, 'dd_sum': 0})
    for r in results.values():
        s = r['sector']
        sector_stats[s]['count'] += 1
        sector_stats[s]['sharpe_sum'] += r['sharpe']
        sector_stats[s]['ret_sum'] += r['total_return']
        sector_stats[s]['dd_sum'] += r['max_dd']

    sorted_sectors = sorted(sector_stats.items(),
                           key=lambda x: x[1]['sharpe_sum']/x[1]['count'],
                           reverse=True)

    print(f"  {'赛道':<20s} {'数量':>4s} {'均夏普':>7s} {'均收益':>8s} {'均回撤':>7s}")
    print(f"  {'-'*50}")
    for sname, ss in sorted_sectors:
        n = ss['count']
        print(f"  {sname:<20s} {n:>4d} {ss['sharpe_sum']/n:>7.3f} "
              f"{ss['ret_sum']/n*100:>7.2f}% {ss['dd_sum']/n*100:>6.2f}%")

    print(f"\n  Backtest complete!")

if __name__ == '__main__':
    main()
