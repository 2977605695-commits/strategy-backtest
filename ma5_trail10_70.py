"""
MA5 偏离买入 + Trail 10% 移动止损 · 70只个股等权回测
=====================================================
Buy: DEV(MA5) < -4.5%
Sell: 从持仓最高点回撤 10%（Trail Stop）
无止盈条件，等权分配
"""
import json, os, math
from collections import defaultdict

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
RISK_FREE = 0.025
TRADING_DAYS = 252
INIT_CAP = 10_000_000
MA_WIN = 5
BUY_THR = -0.045
TRAIL_PCT = 0.10

def calc_ma(data, w):
    ma = []
    for i in range(len(data)):
        if i < w-1:
            ma.append(float('nan'))
        else:
            ma.append(sum(data[i-w+1:i+1]) / w)
    return ma

def backtest_one(name, bars, capital):
    closes = [b['close'] for b in bars]
    ma5 = calc_ma(closes, MA_WIN)

    cash = capital
    pos = 0.0
    buy_px = 0.0
    peak = 0.0
    holding = False
    trades = []
    daily_values = []
    trail_stops = 0

    for i, bar in enumerate(bars):
        px = bar['close']
        ma = ma5[i]

        if math.isnan(ma) or ma == 0:
            daily_values.append({'date': bar['date'], 'value': cash + (pos * px if holding else 0), 'holding': holding})
            continue

        dev = (px - ma) / abs(ma)

        if holding:
            if px > peak:
                peak = px
            stop_px = peak * (1 - TRAIL_PCT)
            if px <= stop_px:
                cash = pos * px
                pnl = cash - pos * buy_px
                trades.append({'buy_date': buy_date, 'sell_date': bar['date'],
                              'buy_px': buy_px, 'sell_px': px,
                              'ret': (px - buy_px) / buy_px, 'pnl': pnl,
                              'exit': 'trail_stop', 'peak': peak})
                trail_stops += 1
                pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0

        if not holding and dev < BUY_THR and cash > 0:
            pos = cash / px
            buy_px = px
            peak = px
            buy_date = bar['date']
            holding = True
            cash = 0.0

        daily_values.append({'date': bar['date'], 'value': cash + (pos * px if holding else 0), 'holding': holding})

    if holding:
        fp = bars[-1]['close']
        cash = pos * fp
        pnl = cash - pos * buy_px
        trades.append({'buy_date': buy_date, 'sell_date': bars[-1]['date'],
                      'buy_px': buy_px, 'sell_px': fp,
                      'ret': (fp - buy_px) / buy_px, 'pnl': pnl, 'exit': 'final'})
        daily_values[-1]['value'] = cash
        daily_values[-1]['holding'] = False

    fv = daily_values[-1]['value']

    rets = []
    for i in range(1, len(daily_values)):
        p, c = daily_values[i-1]['value'], daily_values[i]['value']
        if p > 0: rets.append((c-p)/p)

    peak_val = daily_values[0]['value']; mdd = 0.0
    for dv in daily_values:
        if dv['value'] > peak_val: peak_val = dv['value']
        dd = (peak_val - dv['value']) / peak_val
        if dd > mdd: mdd = dd

    tr = (fv - capital) / capital
    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = (sum((r-mu)**2 for r in rets) / (len(rets)-1)) ** 0.5
        ann_vol = sd * math.sqrt(TRADING_DAYS)
        ann_ret = mu * TRADING_DAYS
        sharpe = (ann_ret - RISK_FREE) / ann_vol if ann_vol > 0 else 0
    else:
        ann_vol = sharpe = ann_ret = 0.0

    ar = (1+tr) ** (TRADING_DAYS / max(len(rets),1)) - 1 if tr > -1 else -1
    calmar = ar / mdd if mdd > 0 else float('inf')

    wins = sum(1 for t in trades if t['ret'] > 0)
    wr = wins / len(trades) if trades else 0
    holding_days = sum(1 for dv in daily_values if dv['holding'])
    empty_days = len(daily_values) - holding_days

    # Trail stop stats
    trail_trades = [t for t in trades if t.get('exit') == 'trail_stop']
    trail_wins = [t for t in trail_trades if t['ret'] > 0]
    trail_losses = [t for t in trail_trades if t['ret'] <= 0]
    avg_win = sum(t['ret'] for t in trail_wins) / len(trail_wins) if trail_wins else 0
    avg_loss = sum(t['ret'] for t in trail_losses) / len(trail_losses) if trail_losses else 0

    return {
        'name': name, 'total_return': tr, 'ann_return': ar, 'ann_vol': ann_vol,
        'sharpe': sharpe, 'max_dd': mdd, 'calmar': calmar,
        'num_trades': len(trades), 'win_rate': wr,
        'holding_days': holding_days, 'empty_days': empty_days,
        'empty_pct': empty_days / len(daily_values) if daily_values else 0,
        'final_value': fv, 'trail_stops': trail_stops,
        'trail_win_avg': avg_win, 'trail_loss_avg': avg_loss,
        'trades': trades, 'daily_values': daily_values,
    }

def main():
    print("=" * 80)
    print("  MA5 偏离买入 + Trail 10% 移动止损 · 70只个股等权回测")
    print(f"  Buy: DEV(MA5) < {BUY_THR:.1%}  |  Stop: Trail {TRAIL_PCT:.0%}")
    print(f"  无止盈条件 · 无放量止盈 · 等权分配")
    print("=" * 80)

    print("\n[LOAD] Loading data...")
    stocks = {}
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'):
            continue
        with open(os.path.join(DATA_DIR, fname), 'r', encoding='utf-8') as f:
            data = json.load(f)
        stocks[data['code']] = {'name': data['name'], 'sector': data['sector'], 'bars': data['bars']}
    print(f"  Loaded {len(stocks)} stocks")

    qualified = {}
    skipped = []
    for code, info in stocks.items():
        if len(info['bars']) >= 500:
            qualified[code] = info
        else:
            skipped.append(f"{info['name']}({len(info['bars'])}b)")

    print(f"  Qualified (>=500 bars): {len(qualified)}")
    if skipped:
        print(f"  Skipped: {', '.join(skipped)}")

    # Common dates
    date_sets = [set(b['date'] for b in info['bars']) for info in qualified.values()]
    common_dates = sorted(date_sets[0].intersection(*date_sets[1:]))
    print(f"  Common trading days: {len(common_dates)}")

    for code in qualified:
        ds = set(common_dates)
        qualified[code]['bars'] = [b for b in qualified[code]['bars'] if b['date'] in ds]

    per_stock_cap = INIT_CAP / len(qualified)
    print(f"\n[BACKTEST] {len(qualified)} stocks, {per_stock_cap:,.0f}/stock\n")

    results = {}
    for code, info in qualified.items():
        name = info['name']
        r = backtest_one(name, info['bars'], per_stock_cap)
        r['code'] = code; r['sector'] = info['sector']
        results[code] = r

    # Portfolio
    date_values = defaultdict(float)
    for code, r in results.items():
        for dv in r['daily_values']:
            date_values[dv['date']] += dv['value']

    pf_dvs = sorted([{'date': d, 'value': v} for d, v in date_values.items()],
                    key=lambda x: x['date'])

    iv = pf_dvs[0]['value']; fv = pf_dvs[-1]['value']
    pf_rets = []
    for i in range(1, len(pf_dvs)):
        p, c = pf_dvs[i-1]['value'], pf_dvs[i]['value']
        if p > 0: pf_rets.append((c-p)/p)

    peak_v = iv; mdd = 0.0
    for dv in pf_dvs:
        if dv['value'] > peak_v: peak_v = dv['value']
        dd = (peak_v - dv['value']) / peak_v
        if dd > mdd: mdd = dd

    pf_tr = (fv - iv) / iv
    if len(pf_rets) > 1:
        mu = sum(pf_rets) / len(pf_rets)
        sd = (sum((r-mu)**2 for r in pf_rets) / (len(pf_rets)-1)) ** 0.5
        pf_av = sd * math.sqrt(TRADING_DAYS)
        pf_ar = mu * TRADING_DAYS
        pf_sh = (pf_ar - RISK_FREE) / pf_av if pf_av > 0 else 0
    else:
        pf_av = pf_sh = pf_ar = 0.0
    pf_cagr = (1+pf_tr) ** (TRADING_DAYS / max(len(pf_rets),1)) - 1 if pf_tr > -1 else -1
    pf_cm = pf_cagr / mdd if mdd > 0 else float('inf')

    total_trades = sum(r['num_trades'] for r in results.values())
    total_wins = sum(sum(1 for t in r['trades'] if t['ret'] > 0) for r in results.values())
    total_trail = sum(r['trail_stops'] for r in results.values())

    # ================================================================
    print("\n" + "=" * 80)
    print("  组合级别汇总")
    print("=" * 80)
    print(f"  回测区间: {pf_dvs[0]['date']} → {pf_dvs[-1]['date']}")
    print(f"  交易日数: {len(pf_dvs)}")
    print(f"  股票数量: {len(qualified)}")
    print(f"  初始资金: {INIT_CAP:,.0f}  →  最终资金: {fv:,.0f}")
    print()
    print(f"  {'总收益率':<14s} | {pf_tr*100:>10.2f}%")
    print(f"  {'年化收益率':<14s} | {pf_cagr*100:>10.2f}%")
    print(f"  {'年化波动率':<14s} | {pf_av*100:>10.2f}%")
    print(f"  {'夏普比率':<14s} | {pf_sh:>10.4f}")
    print(f"  {'最大回撤':<14s} | {mdd*100:>10.2f}%")
    print(f"  {'卡玛比率':<14s} | {pf_cm:>10.4f}")
    print(f"  {'总交易笔数':<14s} | {total_trades:>10d}")
    print(f"  {'Trail止损次数':<14s} | {total_trail:>10d}")
    print(f"  {'胜率':<14s} | {total_wins/total_trades*100:>10.1f}%" if total_trades else "")

    # Per-stock ranking
    sorted_stocks = sorted(results.values(), key=lambda r: r['sharpe'], reverse=True)

    print(f"\n{'='*110}")
    print(f"  个股排名（按夏普）")
    print(f"{'='*110}")
    print(f"  {'#':<3s} {'股票':<12s} {'赛道':<18s} {'夏普':>7s} {'总收益':>9s} "
          f"{'年化':>8s} {'回撤':>7s} {'卡玛':>7s} {'交易':>4s} {'胜率':>6s} "
          f"{'Trail#':>6s} {'+均盈':>6s} {'-均亏':>6s}")
    print(f"  {'-'*106}")

    for rank, r in enumerate(sorted_stocks, 1):
        tw = r['trail_win_avg']*100
        tl = r['trail_loss_avg']*100
        print(f"  {rank:<3d} {r['name']:<12s} {r['sector']:<18s} "
              f"{r['sharpe']:>7.3f} {r['total_return']*100:>8.2f}% "
              f"{r['ann_return']*100:>7.2f}% {r['max_dd']*100:>6.2f}% "
              f"{r['calmar']:>7.3f} {r['num_trades']:>4d} {r['win_rate']*100:>5.0f}% "
              f"{r['trail_stops']:>6d} {tw:>5.0f}% {tl:>5.0f}%")

    # Bottom 5
    print(f"\n  --- 倒数 5 名 ---")
    for rank, r in enumerate(sorted_stocks[-5:], len(sorted_stocks)-4):
        tw = r['trail_win_avg']*100
        tl = r['trail_loss_avg']*100
        print(f"  {rank:<3d} {r['name']:<12s} {r['sector']:<18s} "
              f"{r['sharpe']:>7.3f} {r['total_return']*100:>8.2f}% "
              f"{r['ann_return']*100:>7.2f}% {r['max_dd']*100:>6.2f}% "
              f"{r['calmar']:>7.3f} {r['num_trades']:>4d} {r['win_rate']*100:>5.0f}% "
              f"{r['trail_stops']:>6d} {tw:>5.0f}% {tl:>5.0f}%")

    # Comparison with previous run
    print(f"\n\n{'='*60}")
    print(f"  与 DEV>7% 止盈版对比")
    print(f"{'='*60}")
    print(f"  {'指标':<16s} {'DEV>7% 止盈':>14s} {'Trail 10%':>14s}")
    print(f"  {'-'*46}")
    # From previous run: Sharpe 1.91, return 115.47%, DD 15.19%
    print(f"  {'夏普比率':<16s} {'1.9098':>14s} {pf_sh:>14.4f}")
    print(f"  {'总收益率':<16s} {'115.47%':>14s} {pf_tr*100:>13.2f}%")
    print(f"  {'最大回撤':<16s} {'15.19%':>14s} {mdd*100:>13.2f}%")
    print(f"  {'总交易笔数':<16s} {'514':>14s} {total_trades:>14d}")

    print(f"\n  Backtest complete!")

if __name__ == '__main__':
    main()
