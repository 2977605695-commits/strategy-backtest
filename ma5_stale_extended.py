"""
MA5 偏离 + Trail 10% 止损 + 高点停滞止盈 · 扩展测试
====================================================
对比 纯Trail vs Stale{5,7,10,15}d + Trail 10%
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
        px = bar['close']; ma = ma5[i]
        if math.isnan(ma) or ma == 0:
            daily_values.append({'date': bar['date'], 'value': cash+(pos*px if holding else 0), 'holding': holding})
            continue

        if holding:
            if px > peak: peak = px; peak_day = i

            # Trail 10%
            if px <= peak*(1-TRAIL_PCT):
                cash = pos*px
                trades.append({'buy_date': buy_date, 'sell_date': bar['date'], 'buy_px': buy_px,
                              'sell_px': px, 'ret': (px-buy_px)/buy_px, 'pnl': cash-pos*buy_px,
                              'exit': 'trail', 'days_held': i-buy_idx})
                trail_stops += 1
                pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0; peak_day = 0

            # Stale peak exit
            elif stale_days and holding:
                days_since = i - peak_day
                if days_since >= stale_days:
                    cash = pos*px
                    trades.append({'buy_date': buy_date, 'sell_date': bar['date'], 'buy_px': buy_px,
                                  'sell_px': px, 'ret': (px-buy_px)/buy_px, 'pnl': cash-pos*buy_px,
                                  'exit': 'stale', 'days_since_peak': days_since,
                                  'days_held': i-buy_idx, 'peak': peak})
                    stale_exits += 1
                    pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0; peak_day = 0

        if not holding and ma != 0 and (px-ma)/abs(ma) < BUY_THR and cash > 0:
            pos = cash/px; buy_px = px; peak = px; peak_day = i
            buy_date = bar['date']; buy_idx = i; holding = True; cash = 0.0

        daily_values.append({'date': bar['date'], 'value': cash+(pos*px if holding else 0), 'holding': holding})

    if holding:
        fp = bars[-1]['close']; cash = pos*fp
        trades.append({'buy_date': buy_date, 'sell_date': bars[-1]['date'], 'buy_px': buy_px,
                      'sell_px': fp, 'ret': (fp-buy_px)/buy_px, 'pnl': cash-pos*buy_px, 'exit': 'final'})
        daily_values[-1]['value'] = cash; daily_values[-1]['holding'] = False

    fv = daily_values[-1]['value']
    rets = []
    for i in range(1, len(daily_values)):
        p, c = daily_values[i-1]['value'], daily_values[i]['value']
        if p > 0: rets.append((c-p)/p)

    peak_v = daily_values[0]['value']; mdd = 0.0
    for dv in daily_values:
        if dv['value'] > peak_v: peak_v = dv['value']
        dd = (peak_v-dv['value'])/peak_v
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

    # Exit breakdown
    exit_stats = {}
    for ext in ['trail', 'stale', 'final']:
        et = [t for t in trades if t.get('exit') == ext]
        if et:
            avg_r = sum(t['ret'] for t in et)/len(et)
            w = sum(1 for t in et if t['ret'] > 0)
            exit_stats[ext] = {'count': len(et), 'avg_ret': avg_r, 'win_rate': w/len(et)}

    return {'name': name, 'tr': tr, 'ar': ar, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
            'np': len(trades), 'wr': wr, 'trail': trail_stops, 'stale': stale_exits,
            'hd': hd, 'ed': len(daily_values)-hd,
            'ep': (len(daily_values)-hd)/max(len(daily_values),1),
            'fv': fv, 'exit_stats': exit_stats,
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
        mu = sum(pf_rets)/len(pf_rets)
        sd = (sum((r-mu)**2 for r in pf_rets)/(len(pf_rets)-1))**0.5
        av = sd*math.sqrt(TRADING_DAYS); ar_ = mu*TRADING_DAYS
        sh = (ar_-RISK_FREE)/av if av>0 else 0
    else: av = sh = ar_ = 0.0
    cagr = (1+pf_tr)**(TRADING_DAYS/max(len(pf_rets),1))-1 if pf_tr>-1 else -1
    cm = cagr/mdd if mdd>0 else float('inf')

    tt = sum(r['np'] for r in results.values())
    tw = sum(1 for r in results.values() for t in r['trades'] if t['ret']>0)
    ttr = sum(r['trail'] for r in results.values())
    tst = sum(r['stale'] for r in results.values())

    # Aggregate exit stats
    all_trades = []
    for r in results.values():
        for t in r['trades']:
            all_trades.append({'stock': r['name'], **t})

    agg_exit = {}
    for ext in ['trail', 'stale', 'final']:
        et = [t for t in all_trades if t.get('exit') == ext]
        if et:
            avg_r = sum(t['ret'] for t in et)/len(et)
            w = sum(1 for t in et if t['ret'] > 0)
            agg_exit[ext] = (len(et), avg_r*100, w/len(et)*100)

    return {'label': label, 'stale_days': stale_days,
            'tr': pf_tr, 'ar': cagr, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
            'np': tt, 'wr': tw/tt if tt else 0, 'trail': ttr, 'stale_exits': tst,
            'fv': fv, 'exit_stats': agg_exit, 'stock_r': results}

def main():
    print("=" * 80)
    print("  MA5 + Trail 10% vs + Stale Exit 对比")
    print(f"  纯 Trail vs Stale{{5,7,10,15}}d + Trail 10%")
    print("=" * 80)

    print("\n[LOAD] Loading...")
    stocks = {}
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'): continue
        with open(os.path.join(DATA_DIR, fname), 'r', encoding='utf-8') as f:
            data = json.load(f)
        stocks[data['code']] = {'name': data['name'], 'sector': data['sector'], 'bars': data['bars']}

    qualified = {}
    for code, info in stocks.items():
        if len(info['bars']) >= 500: qualified[code] = info

    date_sets = [set(b['date'] for b in info['bars']) for info in qualified.values()]
    common = sorted(date_sets[0].intersection(*date_sets[1:]))
    for code in qualified:
        ds = set(common)
        qualified[code]['bars'] = [b for b in qualified[code]['bars'] if b['date'] in ds]

    per_stock = INIT_CAP / len(qualified)
    print(f"  {len(qualified)} stocks, {len(common)} days")

    # Scenarios
    configs = [
        (None, '纯 Trail 10%（无止盈）'),
        (5, 'Stale 5d + Trail 10%'),
        (7, 'Stale 7d + Trail 10%'),
        (10, 'Stale 10d + Trail 10%'),
        (15, 'Stale 15d + Trail 10%'),
    ]

    all_r = []
    for stale_days, label in configs:
        r = run_scenario(stale_days, label, qualified, per_stock)
        all_r.append(r)
        print(f"  {label:<30s} Sharpe={r['sh']:>7.4f} Ret={r['tr']*100:>7.2f}% "
              f"DD={r['mdd']*100:>5.2f}% Trd={r['np']:>4d} Trail={r['trail']} Stale={r['stale_exits']}")

    # ================================================================
    print(f"\n{'='*110}")
    print(f"  组合对比")
    print(f"{'='*110}")
    print(f"  {'策略':<30s} {'夏普':>7s} {'收益':>8s} {'年化':>7s} "
          f"{'回撤':>7s} {'卡玛':>7s} {'交易':>5s} {'胜率':>6s} "
          f"{'Trail':>6s} {'Stale':>6s} {'持仓%':>6s}")
    print(f"  {'-'*108}")

    sorted_all = sorted(all_r, key=lambda r: r['sh'], reverse=True)
    for rank, r in enumerate(sorted_all, 1):
        tag = ' << BEST' if rank == 1 else ''
        avg_hold = sum(r2['hd'] for r2 in r['stock_r'].values()) / max(sum(r2['np'] for r2 in r['stock_r'].values()), 1)
        print(f"  {r['label']:<30s} {r['sh']:>7.4f} {r['tr']*100:>7.2f}% "
              f"{r['ar']*100:>6.2f}% {r['mdd']*100:>6.2f}% {r['cm']:>7.3f} "
              f"{r['np']:>5d} {r['wr']*100:>5.1f}% {r['trail']:>6d} {r['stale_exits']:>6d} "
              f"{r['stock_r'][list(r['stock_r'].keys())[0]]['ep']*100:>5.0f}% {tag}")

    # vs Baseline delta
    base = all_r[0]
    print(f"\n  --- vs 纯 Trail 10% ---")
    print(f"  {'策略':<30s} {'d夏普':>8s} {'d收益':>8s} {'d回撤':>8s} {'Stale笔数':>8s}")
    for r in sorted_all:
        if r is base: continue
        dsh = r['sh'] - base['sh']
        dr = (r['tr'] - base['tr'])*100
        dd = (r['mdd'] - base['mdd'])*100
        print(f"  {r['label']:<30s} {dsh:>+8.4f} {dr:>+7.1f}% {dd:>+7.2f}% {r['stale_exits']:>8d}")

    # Exit breakdown
    print(f"\n\n  {'='*90}")
    print(f"  退出方式分布对比")
    print(f"  {'='*90}")
    for r in sorted_all:
        print(f"\n  --- {r['label']} ---")
        es = r['exit_stats']
        for ext in ['trail', 'stale', 'final']:
            if ext in es:
                cnt, avg_r, wr = es[ext]
                print(f"    {ext:<8s} {cnt:>5d}笔  均收益={avg_r:>6.1f}%  胜率={wr:>5.1f}%")

    # TOP 10 stocks from BEST
    best = sorted_all[0]
    print(f"\n\n  BEST: {best['label']} — TOP 10 个股")
    ss = sorted(best['stock_r'].values(), key=lambda r: r['sh'], reverse=True)
    print(f"  {'股票':<12s} {'夏普':>7s} {'收益':>9s} {'回撤':>7s} {'交易':>4s} {'Trail':>5s} {'Stale':>5s}")
    for r in ss[:10]:
        print(f"  {r['name']:<12s} {r['sh']:>7.3f} {r['tr']*100:>8.2f}% {r['mdd']*100:>6.2f}% "
              f"{r['np']:>4d} {r['trail']:>5d} {r['stale']:>5d}")

    print(f"\n  Backtest complete!")

if __name__ == '__main__':
    main()
