"""
MA5 偏离买入 + Trail 10% · 期末延期测试
==========================================
正常交易截止日提前 N 天，让持仓有 N 天缓冲期自然退出
减少"期末强平"的人为干扰
Test: N ∈ {0, 5, 7, 10, 15}
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

def backtest_one(name, bars, capital, buy_cutoff_days=0):
    """
    buy_cutoff_days: stop entering new positions N days before end
    This gives existing positions time to exit naturally via Trail Stop
    """
    closes = [b['close'] for b in bars]
    ma5 = calc_ma(closes, MA_WIN)

    last_buy_day = len(bars) - buy_cutoff_days

    cash = capital; pos = 0.0; buy_px = 0.0; peak = 0.0
    holding = False; trades = []; daily_values = []; trail_stops = 0
    final_exits = 0; skipped_buys = 0

    for i, bar in enumerate(bars):
        px = bar['close']
        ma = ma5[i]

        if math.isnan(ma) or ma == 0:
            daily_values.append({'date': bar['date'], 'value': cash + (pos*px if holding else 0), 'holding': holding})
            continue

        dev = (px - ma) / abs(ma)

        # Trail stop (always active)
        if holding:
            if px > peak: peak = px
            if px <= peak * (1 - TRAIL_PCT):
                cash = pos * px
                pnl = cash - pos * buy_px
                trades.append({'buy_date': buy_date, 'sell_date': bar['date'],
                              'buy_px': buy_px, 'sell_px': px,
                              'ret': (px-buy_px)/buy_px, 'pnl': pnl, 'exit': 'trail'})
                trail_stops += 1
                pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0

        # Buy (only if before cutoff)
        if not holding and dev < BUY_THR and cash > 0:
            if i < last_buy_day:
                pos = cash / px; buy_px = px; peak = px
                buy_date = bar['date']; holding = True; cash = 0.0
            else:
                skipped_buys += 1

        daily_values.append({'date': bar['date'], 'value': cash + (pos*px if holding else 0), 'holding': holding})

    # Final liquidation (only for positions that survived to the end)
    if holding:
        fp = bars[-1]['close']; cash = pos * fp
        pnl = cash - pos * buy_px
        trades.append({'buy_date': buy_date, 'sell_date': bars[-1]['date'],
                      'buy_px': buy_px, 'sell_px': fp,
                      'ret': (fp-buy_px)/buy_px, 'pnl': pnl, 'exit': 'final'})
        final_exits += 1
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

    return {'name': name, 'tr': tr, 'ar': ar, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
            'np': len(trades), 'wr': wr, 'trail': trail_stops, 'final': final_exits,
            'skipped': skipped_buys, 'hd': hd, 'ed': ed,
            'ep': ed/max(len(daily_values),1), 'fv': fv,
            'trades': trades, 'daily_values': daily_values}

def run_scenario(cutoff_days, label, qualified, per_stock):
    results = {}
    for code, info in qualified.items():
        r = backtest_one(info['name'], info['bars'], per_stock, cutoff_days)
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
    tfin = sum(r['final'] for r in results.values())
    tskip = sum(r['skipped'] for r in results.values())
    thd = sum(r['hd'] for r in results.values())

    # Average holding days for final exits
    final_trades = []
    for r in results.values():
        for t in r['trades']:
            if t.get('exit') == 'final':
                final_trades.append(t)

    return {'label': label, 'cutoff': cutoff_days,
            'tr': pf_tr, 'ar': cagr, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
            'np': tt, 'wr': tw/tt if tt else 0, 'trail': ttr, 'final': tfin,
            'skipped': tskip, 'total_hold_days': thd,
            'fv': fv, 'stock_r': results}

def main():
    print("=" * 80)
    print("  MA5 偏离 + Trail 10% · 期末买入截止日延期测试")
    print(f"  买入截止日 = 数据结束日 - N 天，给持仓缓冲期自然退出")
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
    n_days = len(common)
    print(f"  {len(qualified)} stocks, {n_days} trading days, {per_stock:,.0f}/stock")

    # Test scenarios
    cutoffs = [0, 5, 7, 10, 15]
    all_r = []
    for c in cutoffs:
        label = f'截止前{c}d停买' if c > 0 else 'BASELINE (期末强平)'
        r = run_scenario(c, label, qualified, per_stock)
        all_r.append(r)
        print(f"  {label:<26s} Sharpe={r['sh']:>7.4f} Ret={r['tr']*100:>7.2f}% "
              f"DD={r['mdd']*100:>5.2f}% Trd={r['np']:>4d} "
              f"Trail={r['trail']:>4d} Final={r['final']:>3d} Skip={r['skipped']:>4d}")

    sorted_all = sorted(all_r, key=lambda r: r['sh'], reverse=True)

    # ================================================================
    print(f"\n{'='*100}")
    print(f"  排名")
    print(f"{'='*100}")
    print(f"  {'#':<3s} {'策略':<26s} {'夏普':>7s} {'总收益':>8s} {'年化':>7s} "
          f"{'回撤':>7s} {'卡玛':>7s} {'交易':>5s} {'胜率':>6s} "
          f"{'Trail':>5s} {'Final':>5s} {'Skip':>5s} {'持仓天':>6s}")
    print(f"  {'-'*100}")

    for rank, r in enumerate(sorted_all, 1):
        tag = ' << BEST' if rank == 1 else ''
        print(f"  {rank:<3d} {r['label']:<26s} {r['sh']:>7.4f} {r['tr']*100:>7.2f}% "
              f"{r['ar']*100:>6.2f}% {r['mdd']*100:>6.2f}% {r['cm']:>7.3f} "
              f"{r['np']:>5d} {r['wr']*100:>5.1f}% {r['trail']:>5d} {r['final']:>5d} "
              f"{r['skipped']:>5d} {r['total_hold_days']:>6d}{tag}")

    # Delta
    base = all_r[0]
    print(f"\n  --- vs Baseline ---")
    for r in sorted_all:
        if r is base: continue
        dsh = r['sh'] - base['sh']
        dr = (r['tr'] - base['tr']) * 100
        dd = (r['mdd'] - base['mdd']) * 100
        df = r['final'] - base['final']
        print(f"  {r['label']:<26s} dSharpe={dsh:+.4f}  dRet={dr:+.1f}%  "
              f"dDD={dd:+.2f}%  dFinal={df:+d}  dSkip={r['skipped']}")

    # Best per-stock
    best = sorted_all[0]
    print(f"\n\n  BEST: {best['label']} — 个股 TOP 10 & BOTTOM 5")
    ss = sorted(best['stock_r'].values(), key=lambda r: r['sh'], reverse=True)
    print(f"  {'股票':<12s} {'夏普':>7s} {'收益':>9s} {'回撤':>7s} {'交易':>4s} {'Trail':>5s} {'Final':>5s} {'Skip':>5s}")
    for r in ss[:10]:
        print(f"  {r['name']:<12s} {r['sh']:>7.3f} {r['tr']*100:>8.2f}% {r['mdd']*100:>6.2f}% "
              f"{r['np']:>4d} {r['trail']:>5d} {r['final']:>5d} {r['skipped']:>5d}")
    print(f"  ...")
    for r in ss[-5:]:
        print(f"  {r['name']:<12s} {r['sh']:>7.3f} {r['tr']*100:>8.2f}% {r['mdd']*100:>6.2f}% "
              f"{r['np']:>4d} {r['trail']:>5d} {r['final']:>5d} {r['skipped']:>5d}")

    print(f"\n  Backtest complete!")

if __name__ == '__main__':
    main()
