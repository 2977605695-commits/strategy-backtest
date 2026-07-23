"""
MA5 偏离买入 + Trail 10% 止损 + 高点停滞止盈
==============================================
Buy: DEV(MA5) < -4.5%
Stop: Trail 10%（异常情况兜底）
Take-Profit: 创高点后 N 日未破新高 → 离场
Test: N ∈ {5, 7}
"""
import json, os, math
from collections import defaultdict

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
RISK_FREE = 0.025; TRADING_DAYS = 252; INIT_CAP = 10_000_000
MA_WIN = 5; BUY_THR = -0.045; TRAIL_PCT = 0.10

def calc_ma(data, w):
    ma = []
    for i in range(len(data)):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma

def backtest_one(name, bars, capital, stale_days=None):
    closes = [b['close'] for b in bars]
    ma5 = calc_ma(closes, MA_WIN)

    cash = capital; pos = 0.0; buy_px = 0.0; peak = 0.0; peak_day = 0
    holding = False; trades = []; daily_values = []
    trail_stops = 0; stale_exits = 0

    for i, bar in enumerate(bars):
        px = bar['close']
        ma = ma5[i]

        if math.isnan(ma) or ma == 0:
            daily_values.append({'date': bar['date'], 'value': cash + (pos*px if holding else 0), 'holding': holding})
            continue

        dev = (px - ma) / abs(ma)

        if holding:
            # Update peak
            if px > peak:
                peak = px
                peak_day = i

            # 1) Trail 10% emergency stop
            trail_px = peak * (1 - TRAIL_PCT)
            if px <= trail_px:
                cash = pos * px
                pnl = cash - pos * buy_px
                trades.append({'buy_date': buy_date, 'sell_date': bar['date'],
                              'buy_px': buy_px, 'sell_px': px,
                              'ret': (px-buy_px)/buy_px, 'pnl': pnl,
                              'exit': 'trail', 'days_held': i - buy_idx})
                trail_stops += 1
                pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0; peak_day = 0

            # 2) Stale peak: N days without new high
            elif stale_days and holding:
                days_since_peak = i - peak_day
                if days_since_peak >= stale_days:
                    cash = pos * px
                    pnl = cash - pos * buy_px
                    trades.append({'buy_date': buy_date, 'sell_date': bar['date'],
                                  'buy_px': buy_px, 'sell_px': px,
                                  'ret': (px-buy_px)/buy_px, 'pnl': pnl,
                                  'exit': 'stale', 'days_since_peak': days_since_peak,
                                  'days_held': i - buy_idx, 'peak': peak})
                    stale_exits += 1
                    pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0; peak_day = 0

        # Buy
        if not holding and dev < BUY_THR and cash > 0:
            pos = cash / px; buy_px = px; peak = px; peak_day = i
            buy_date = bar['date']; buy_idx = i
            holding = True; cash = 0.0

        daily_values.append({'date': bar['date'], 'value': cash + (pos*px if holding else 0), 'holding': holding})

    if holding:
        fp = bars[-1]['close']; cash = pos * fp
        pnl = cash - pos * buy_px
        trades.append({'buy_date': buy_date, 'sell_date': bars[-1]['date'],
                      'buy_px': buy_px, 'sell_px': fp,
                      'ret': (fp-buy_px)/buy_px, 'pnl': pnl, 'exit': 'final'})
        daily_values[-1]['value'] = cash; daily_values[-1]['holding'] = False

    fv = daily_values[-1]['value']
    rets = []
    for i in range(1, len(daily_values)):
        p, c = daily_values[i-1]['value'], daily_values[i]['value']
        if p > 0: rets.append((c-p)/p)

    peak_v = daily_values[0]['value']; mdd = 0.0
    for dv in daily_values:
        if dv['value'] > peak_v: peak_v = dv['value']
        dd = (peak_v - dv['value'])/peak_v
        if dd > mdd: mdd = dd

    tr = (fv-capital)/capital
    if len(rets) > 1:
        mu = sum(rets)/len(rets); sd = (sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av = sd*math.sqrt(TRADING_DAYS); ar_ = mu*TRADING_DAYS
        sh = (ar_-RISK_FREE)/av if av>0 else 0
    else: av = sh = ar_ = 0.0
    ar = (1+tr)**(TRADING_DAYS/max(len(rets),1))-1 if tr>-1 else -1
    cm = ar/mdd if mdd>0 else float('inf')

    wins = sum(1 for t in trades if t['ret']>0)
    wr = wins/len(trades) if trades else 0
    hd = sum(1 for dv in daily_values if dv['holding'])
    ed = len(daily_values)-hd

    # Stale stats
    stale_trades = [t for t in trades if t.get('exit') == 'stale']
    stale_wins = [t for t in stale_trades if t['ret'] > 0]
    stale_loss = [t for t in stale_trades if t['ret'] <= 0]

    return {'name': name, 'tr': tr, 'ar': ar, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
            'np': len(trades), 'wr': wr, 'trail': trail_stops, 'stale': stale_exits,
            'stale_avg_ret': sum(t['ret'] for t in stale_trades)/len(stale_trades) if stale_trades else 0,
            'stale_wins': len(stale_wins), 'stale_loss': len(stale_loss),
            'hd': hd, 'ed': ed, 'ep': ed/max(len(daily_values),1), 'fv': fv,
            'trades': trades, 'daily_values': daily_values}

def run_scenario(stale_days, label, qualified, per_stock):
    results = {}
    for code, info in qualified.items():
        r = backtest_one(info['name'], info['bars'], per_stock, stale_days)
        r['code'] = code; r['sector'] = info['sector']
        results[code] = r

    date_values = defaultdict(float)
    for code, r in results.items():
        for dv in r['daily_values']:
            date_values[dv['date']] += dv['value']

    pf = sorted([{'date': d, 'value': v} for d, v in date_values.items()], key=lambda x: x['date'])
    iv, fv = pf[0]['value'], pf[-1]['value']
    pf_rets = []
    for i in range(1, len(pf)):
        p, c = pf[i-1]['value'], pf[i]['value']
        if p > 0: pf_rets.append((c-p)/p)

    pk = iv; mdd = 0.0
    for dv in pf:
        if dv['value'] > pk: pk = dv['value']
        dd = (pk-dv['value'])/pk
        if dd > mdd: mdd = dd

    pf_tr = (fv-iv)/iv
    if len(pf_rets) > 1:
        mu = sum(pf_rets)/len(pf_rets); sd = (sum((r-mu)**2 for r in pf_rets)/(len(pf_rets)-1))**0.5
        av = sd*math.sqrt(TRADING_DAYS); ar_ = mu*TRADING_DAYS
        sh = (ar_-RISK_FREE)/av if av>0 else 0
    else: av = sh = ar_ = 0.0
    cagr = (1+pf_tr)**(TRADING_DAYS/max(len(pf_rets),1))-1 if pf_tr>-1 else -1
    cm = cagr/mdd if mdd>0 else float('inf')

    tt = sum(r['np'] for r in results.values())
    tw = sum(1 for r in results.values() for t in r['trades'] if t['ret']>0)
    ttr = sum(r['trail'] for r in results.values())
    tst = sum(r['stale'] for r in results.values())

    return {'label': label, 'stale_days': stale_days,
            'tr': pf_tr, 'ar': cagr, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
            'np': tt, 'wr': tw/tt if tt else 0, 'trail': ttr, 'stale': tst,
            'fv': fv, 'stock_r': results}

def main():
    print("=" * 80)
    print("  MA5 偏离 + Trail 10% 止损 + 高点停滞止盈")
    print(f"  Buy: DEV(MA5) < {BUY_THR:.1%}")
    print(f"  Hard Stop: Trail {TRAIL_PCT:.0%}")
    print(f"  Take-Profit: 创高点后 N 日不破新高 → 离场")
    print("=" * 80)

    print("\n[LOAD] Loading...")
    stocks = {}
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'): continue
        with open(os.path.join(DATA_DIR, fname), 'r', encoding='utf-8') as f:
            data = json.load(f)
        stocks[data['code']] = {'name': data['name'], 'sector': data['sector'], 'bars': data['bars']}

    qualified = {}
    skipped = []
    for code, info in stocks.items():
        if len(info['bars']) >= 500: qualified[code] = info
        else: skipped.append(f"{info['name']}({len(info['bars'])}b)")

    date_sets = [set(b['date'] for b in info['bars']) for info in qualified.values()]
    common = sorted(date_sets[0].intersection(*date_sets[1:]))
    for code in qualified:
        ds = set(common)
        qualified[code]['bars'] = [b for b in qualified[code]['bars'] if b['date'] in ds]

    per_stock = INIT_CAP / len(qualified)
    print(f"  {len(qualified)} stocks, {len(common)} days, {per_stock:,.0f}/stock")

    # Baseline: Trail 10% only (stale_days = None)
    base = run_scenario(None, 'BASELINE (仅 Trail 10%)', qualified, per_stock)

    # Test stale 5d + Trail 10%
    s5 = run_scenario(5, 'Stale 5d + Trail 10%', qualified, per_stock)

    # Test stale 7d + Trail 10%
    s7 = run_scenario(7, 'Stale 7d + Trail 10%', qualified, per_stock)

    all_r = [base, s5, s7]
    sorted_all = sorted(all_r, key=lambda r: r['sh'], reverse=True)

    # ================================================================
    print(f"\n{'='*100}")
    print(f"  组合对比")
    print(f"{'='*100}")
    print(f"  {'策略':<28s} {'夏普':>7s} {'总收益':>9s} {'年化':>7s} "
          f"{'回撤':>7s} {'卡玛':>7s} {'交易':>5s} {'胜率':>6s} "
          f"{'Trail#':>6s} {'Stale#':>6s}")
    print(f"  {'-'*94}")

    for rank, r in enumerate(sorted_all, 1):
        tag = ' << BEST' if rank == 1 else ''
        print(f"  {r['label']:<28s} {r['sh']:>7.4f} {r['tr']*100:>8.2f}% "
              f"{r['ar']*100:>6.2f}% {r['mdd']*100:>6.2f}% {r['cm']:>7.3f} "
              f"{r['np']:>5d} {r['wr']*100:>5.1f}% {r['trail']:>6d} {r['stale']:>6d}{tag}")

    # Delta
    print(f"\n  --- vs Baseline ---")
    for r in sorted_all:
        if r is base: continue
        dsh = r['sh'] - base['sh']
        dr = (r['tr'] - base['tr']) * 100
        dd = (r['mdd'] - base['mdd']) * 100
        print(f"  {r['label']:<28s} dSharpe={dsh:+.4f}  dRet={dr:+.1f}%  dDD={dd:+.2f}%  "
              f"Stale exits={r['stale']}")

    # Best: per-stock ranking
    best = sorted_all[0]
    print(f"\n\n{'='*100}")
    print(f"  BEST: {best['label']} — 个股排名")
    print(f"{'='*100}")
    sorted_stocks = sorted(best['stock_r'].values(), key=lambda r: r['sh'], reverse=True)

    print(f"  {'#':<3s} {'股票':<12s} {'赛道':<18s} {'夏普':>7s} {'收益':>9s} "
          f"{'回撤':>7s} {'交易':>4s} {'胜率':>6s} {'Trail':>5s} {'Stale':>5s} "
          f"{'S均盈':>6s}")
    print(f"  {'-'*100}")

    for rank, r in enumerate(sorted_stocks, 1):
        savg = r['stale_avg_ret'] * 100
        print(f"  {rank:<3d} {r['name']:<12s} {r['sector']:<18s} "
              f"{r['sh']:>7.3f} {r['tr']*100:>8.2f}% {r['mdd']*100:>6.2f}% "
              f"{r['np']:>4d} {r['wr']*100:>5.0f}% {r['trail']:>5d} {r['stale']:>5d} "
              f"{savg:>5.0f}%")

    # Bottom 5
    print(f"\n  --- 倒数 5 名 ---")
    for rank, r in enumerate(sorted_stocks[-5:], len(sorted_stocks)-4):
        savg = r['stale_avg_ret'] * 100
        print(f"  {rank:<3d} {r['name']:<12s} {r['sector']:<18s} "
              f"{r['sh']:>7.3f} {r['tr']*100:>8.2f}% {r['mdd']*100:>6.2f}% "
              f"{r['np']:>4d} {r['wr']*100:>5.0f}% {r['trail']:>5d} {r['stale']:>5d} "
              f"{savg:>5.0f}%")

    # Exit breakdown for best
    print(f"\n\n  --- 退出方式分布 (BEST: {best['label']}) ---")
    all_trades = []
    for r in best['stock_r'].values():
        for t in r['trades']:
            all_trades.append({'stock': r['name'], **t})

    from collections import Counter
    exit_counts = Counter(t.get('exit', '?') for t in all_trades)
    for exit_type, count in exit_counts.most_common():
        exit_trades = [t for t in all_trades if t.get('exit') == exit_type]
        avg_ret = sum(t['ret'] for t in exit_trades) / len(exit_trades) * 100
        wins = sum(1 for t in exit_trades if t['ret'] > 0)
        print(f"  {exit_type:<12s} {count:>5d}笔  均收益={avg_ret:>6.1f}%  胜率={wins/len(exit_trades)*100:>4.0f}%")

    print(f"\n  Backtest complete!")

if __name__ == '__main__':
    main()
