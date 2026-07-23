"""
MA5 偏离买入 + 前置波动率过滤 + Trail 10% 移动止损
====================================================
Buy: DEV(MA5) < -4.5% AND 近期波动率 < 阈值
Stop: Trail 10%
Grid search over: vol_window ∈ {3, 5}, vol_threshold ∈ {25%, 30%, 35%, 40%, 45%}
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

def calc_hist_vol(bars, window, annualize=True):
    """Calculate historical volatility over `window` days"""
    if len(bars) < window + 1:
        return [float('nan')] * len(bars)

    closes = [b['close'] for b in bars]
    vol = [float('nan')] * len(bars)

    for i in range(window, len(bars)):
        # Daily log returns over the window
        rets = []
        for j in range(i - window + 1, i + 1):
            if closes[j-1] > 0:
                rets.append(math.log(closes[j] / closes[j-1]))
        if len(rets) > 1:
            mu = sum(rets) / len(rets)
            var = sum((r - mu)**2 for r in rets) / (len(rets) - 1)
            sd = math.sqrt(var)
            vol[i] = sd * math.sqrt(TRADING_DAYS) if annualize else sd
        else:
            vol[i] = float('nan')

    return vol

def backtest_one(name, bars, capital, vol_window, vol_threshold, use_filter=True):
    """Buy: DEV < -4.5% AND hist_vol < vol_threshold. Stop: Trail 10%"""
    closes = [b['close'] for b in bars]
    ma5 = calc_ma(closes, MA_WIN)
    hist_vol = calc_hist_vol(bars, vol_window)

    cash = capital; pos = 0.0; buy_px = 0.0; peak = 0.0
    holding = False; trades = []; daily_values = []; trail_stops = 0
    buys_blocked = 0

    for i, bar in enumerate(bars):
        px = bar['close']
        ma = ma5[i]
        hv = hist_vol[i]

        if math.isnan(ma) or ma == 0:
            daily_values.append({'date': bar['date'], 'value': cash + (pos*px if holding else 0), 'holding': holding})
            continue

        dev = (px - ma) / abs(ma)

        # Trail stop
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

        # Buy with vol filter
        if not holding and dev < BUY_THR and cash > 0:
            if not use_filter:
                pos = cash / px; buy_px = px; peak = px
                buy_date = bar['date']; holding = True; cash = 0.0
            elif not math.isnan(hv) and hv <= vol_threshold:
                pos = cash / px; buy_px = px; peak = px
                buy_date = bar['date']; holding = True; cash = 0.0
            else:
                buys_blocked += 1

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
        dd = (peak_v - dv['value']) / peak_v
        if dd > mdd: mdd = dd

    tr = (fv - capital) / capital
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
            'np': len(trades), 'wr': wr, 'trail': trail_stops,
            'buy_blocked': buys_blocked, 'hd': hd, 'ed': ed,
            'ep': ed/max(len(daily_values),1), 'fv': fv,
            'trades': trades, 'daily_values': daily_values}

def run_scenario(vol_window, vol_threshold, label, qualified, per_stock, use_filter=True):
    results = {}
    for code, info in qualified.items():
        r = backtest_one(info['name'], info['bars'], per_stock, vol_window, vol_threshold, use_filter)
        r['code'] = code; r['sector'] = info['sector']
        results[code] = r

    # Portfolio
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
        av = sd*math.sqrt(TRADING_DAYS)
        ar_ = mu*TRADING_DAYS
        sh = (ar_-RISK_FREE)/av if av>0 else 0
    else: av = sh = ar_ = 0.0
    cagr = (1+pf_tr)**(TRADING_DAYS/max(len(pf_rets),1))-1 if pf_tr>-1 else -1
    cm = cagr/mdd if mdd>0 else float('inf')

    tt = sum(r['np'] for r in results.values())
    tw = sum(1 for r in results.values() for t in r['trades'] if t['ret']>0)
    ttl = sum(r['trail'] for r in results.values())
    tb = sum(r['buy_blocked'] for r in results.values())

    return {'label': label, 'vol_window': vol_window, 'vol_threshold': vol_threshold,
            'tr': pf_tr, 'ar': cagr, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
            'np': tt, 'wr': tw/tt if tt else 0, 'trail': ttl, 'buy_blocked': tb,
            'fv': fv, 'stock_r': results}

def main():
    print("=" * 80)
    print("  MA5 偏离 + 前置波动率过滤 + Trail 10% · Grid Search")
    print(f"  Buy: DEV(MA5) < {BUY_THR:.1%} AND HistVol < threshold")
    print(f"  Stop: Trail {TRAIL_PCT:.0%}")
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

    # Grid
    windows = [3, 5]
    thresholds = [0.25, 0.30, 0.35, 0.40, 0.45]  # annualized

    print(f"\n[GRID] {len(windows)*len(thresholds)} scenarios + baseline (no filter)\n")

    # Baseline: no vol filter (threshold = inf)
    base = run_scenario(3, 0.40, 'BASELINE (无过滤)', qualified, per_stock, use_filter=False)
    all_results = [base]
    print(f"  {'BASELINE':<24s} | Sharpe={base['sh']:>7.4f} | Ret={base['tr']*100:>8.2f}% | "
          f"DD={base['mdd']*100:>6.2f}% | Trd={base['np']:>5d} | Win={base['wr']*100:>5.1f}% | "
          f"Blocked=0")

    for w in windows:
        for th in thresholds:
            label = f"Vol{w}d < {th:.0%}"
            r = run_scenario(w, th, label, qualified, per_stock)
            all_results.append(r)
            print(f"  {label:<24s} | Sharpe={r['sh']:>7.4f} | Ret={r['tr']*100:>8.2f}% | "
                  f"DD={r['mdd']*100:>6.2f}% | Trd={r['np']:>5d} | Win={r['wr']*100:>5.1f}% | "
                  f"Blocked={r['buy_blocked']}")

    # Sort and rank
    sorted_all = sorted(all_results, key=lambda r: r['sh'], reverse=True)

    print(f"\n\n{'='*90}")
    print(f"  排名（按组合夏普）")
    print(f"{'='*90}")
    print(f"  {'#':<3s} {'策略':<26s} {'夏普':>7s} {'总收益':>9s} {'年化':>7s} "
          f"{'回撤':>7s} {'卡玛':>7s} {'交易':>5s} {'胜率':>6s} {'阻塞':>6s}")
    print(f"  {'-'*86}")

    for rank, r in enumerate(sorted_all, 1):
        tag = ' << BEST' if rank == 1 else ''
        print(f"  {rank:<3d} {r['label']:<26s} {r['sh']:>7.4f} {r['tr']*100:>8.2f}% "
              f"{r['ar']*100:>6.2f}% {r['mdd']*100:>6.2f}% {r['cm']:>7.3f} "
              f"{r['np']:>5d} {r['wr']*100:>5.1f}% {r['buy_blocked']:>6d}{tag}")

    # Best scenario per-stock detail
    best = sorted_all[0]
    print(f"\n\n  BEST: {best['label']} — 个股 TOP 15")
    print(f"  {'股票':<12s} {'赛道':<18s} {'夏普':>7s} {'收益':>9s} {'回撤':>7s} "
          f"{'交易':>4s} {'胜率':>6s} {'Trail':>5s} {'阻塞':>5s}")
    print(f"  {'-'*85}")

    sorted_stocks = sorted(best['stock_r'].values(), key=lambda r: r['sh'], reverse=True)
    for rank, r in enumerate(sorted_stocks[:15], 1):
        print(f"  {r['name']:<12s} {r['sector']:<18s} {r['sh']:>7.3f} "
              f"{r['tr']*100:>8.2f}% {r['mdd']*100:>6.2f}% "
              f"{r['np']:>4d} {r['wr']*100:>5.0f}% {r['trail']:>5d} {r['buy_blocked']:>5d}")

    # Key insight
    print(f"\n\n  --- 波动率过滤效果分析 ---")
    for r in sorted_all[:6]:
        delta_sh = r['sh'] - base['sh']
        delta_ret = r['tr'] - base['tr']
        delta_dd = r['mdd'] - base['mdd']
        print(f"  {r['label']:<26s} Δ夏普={delta_sh:+.4f}  Δ收益={delta_ret*100:+.1f}%  "
              f"Δ回撤={delta_dd*100:+.2f}%  阻塞={r['buy_blocked']}笔买入")

    print(f"\n  Backtest complete!")

if __name__ == '__main__':
    main()
