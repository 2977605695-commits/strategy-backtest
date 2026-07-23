"""
MA5 偏离买入 + Trail Stop 精细扫描
===================================
纯 MA5 偏离，无其他止盈/过滤
Trail 从 3% 到 20%，密集测试最优区间
"""
import json, os, math
from collections import defaultdict

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
RISK_FREE = 0.025; TRADING_DAYS = 252; INIT_CAP = 10_000_000
MA_WIN = 5; BUY_THR = -0.045

def calc_ma(data, w):
    ma = []
    for i in range(len(data)):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma

def backtest_one(name, bars, capital, trail_pct):
    closes = [b['close'] for b in bars]
    ma5 = calc_ma(closes, MA_WIN)
    cash = capital; pos = 0.0; buy_px = 0.0; peak = 0.0
    holding = False; trades = []; daily_values = []; trail_stops = 0

    for i, bar in enumerate(bars):
        px = bar['close']; ma = ma5[i]
        if math.isnan(ma) or ma == 0:
            daily_values.append({'date': bar['date'], 'value': cash+(pos*px if holding else 0), 'holding': holding})
            continue

        if holding:
            if px > peak: peak = px
            if px <= peak*(1-trail_pct):
                cash = pos*px
                trades.append({'buy_date': buy_date, 'sell_date': bar['date'],
                              'buy_px': buy_px, 'sell_px': px,
                              'ret': (px-buy_px)/buy_px, 'pnl': cash-pos*buy_px,
                              'exit': 'trail', 'peak': peak})
                trail_stops += 1
                pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0

        if not holding and ma != 0 and (px-ma)/abs(ma) < BUY_THR and cash > 0:
            pos = cash/px; buy_px = px; peak = px
            buy_date = bar['date']; holding = True; cash = 0.0

        daily_values.append({'date': bar['date'], 'value': cash+(pos*px if holding else 0), 'holding': holding})

    if holding:
        fp = bars[-1]['close']; cash = pos*fp
        trades.append({'buy_date': buy_date, 'sell_date': bars[-1]['date'],
                      'buy_px': buy_px, 'sell_px': fp,
                      'ret': (fp-buy_px)/buy_px, 'pnl': cash-pos*buy_px, 'exit': 'final'})
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

    # Trail exit stats
    trail_trades = [t for t in trades if t.get('exit') == 'trail']
    trail_wins = [t for t in trail_trades if t['ret'] > 0]
    trail_loss = [t for t in trail_trades if t['ret'] <= 0]

    return {'name': name, 'tr': tr, 'ar': ar, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
            'np': len(trades), 'wr': wr, 'trail': trail_stops,
            'trail_avg_win': sum(t['ret'] for t in trail_wins)/len(trail_wins) if trail_wins else 0,
            'trail_avg_loss': sum(t['ret'] for t in trail_loss)/len(trail_loss) if trail_loss else 0,
            'trail_win_count': len(trail_wins), 'trail_loss_count': len(trail_loss),
            'fv': fv, 'trades': trades, 'daily_values': daily_values}

def run_scenario(trail_pct, qualified, per_stock):
    label = f'Trail {trail_pct:.0%}'
    results = {}
    for code, info in qualified.items():
        r = backtest_one(info['name'], info['bars'], per_stock, trail_pct)
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
    ttrail = sum(r['trail'] for r in results.values())

    # Aggregate trail stats
    all_trail = []
    for r in results.values():
        for t in r['trades']:
            if t.get('exit') == 'trail':
                all_trail.append(t['ret'])
    avg_trail_win = sum(r for r in all_trail if r > 0)/max(sum(1 for r in all_trail if r > 0), 1)
    avg_trail_loss = sum(r for r in all_trail if r <= 0)/max(sum(1 for r in all_trail if r <= 0), 1)

    # Per-stock sharpe distribution
    stock_sharpes = sorted([r['sh'] for r in results.values()])
    n = len(stock_sharpes)
    sh_median = stock_sharpes[n//2]
    sh_q25 = stock_sharpes[n//4]
    sh_q75 = stock_sharpes[3*n//4]
    pos_count = sum(1 for s in stock_sharpes if s > 0)
    neg_count = sum(1 for s in stock_sharpes if s <= 0)

    return {'label': label, 'trail_pct': trail_pct,
            'tr': pf_tr, 'ar': cagr, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
            'np': tt, 'wr': tw/tt if tt else 0, 'trail_count': ttrail,
            'avg_trail_win': avg_trail_win, 'avg_trail_loss': avg_trail_loss,
            'sh_median': sh_median, 'sh_q25': sh_q25, 'sh_q75': sh_q75,
            'pos_stocks': pos_count, 'neg_stocks': neg_count,
            'fv': fv, 'stock_r': results}

def main():
    print("=" * 80)
    print("  MA5 偏离 + Trail Stop 精细扫描")
    print(f"  Trail 范围: 3% ~ 20%，密集搜索最优区间")
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

    # Dense grid: 3% to 20%
    # Extra dense around 8-12% where the sweet spot likely is
    trail_values = [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13, 0.14, 0.15, 0.17, 0.20]

    print(f"\n[SWEEP] {len(trail_values)} Trail values...\n")
    all_r = []
    for tp in trail_values:
        label = f'Trail {tp:.0%}'
        r = run_scenario(tp, qualified, per_stock)
        all_r.append(r)
        print(f"  {label:>10s} | Sharpe={r['sh']:>7.4f} | Ret={r['tr']*100:>7.2f}% | "
              f"DD={r['mdd']*100:>5.2f}% | Calmar={r['cm']:>7.3f} | "
              f"Trd={r['np']:>5d} | Win={r['wr']*100:>5.1f}% | "
              f"+{r['avg_trail_win']*100:>5.1f}% / {r['avg_trail_loss']*100:>5.1f}% | "
              f"Pos={r['pos_stocks']:>2d} Neg={r['neg_stocks']:>2d}")

    sorted_all = sorted(all_r, key=lambda r: r['sh'], reverse=True)

    # ================================================================
    print(f"\n\n{'='*115}")
    print(f"  Trail 值排名（按夏普）")
    print(f"{'='*115}")
    print(f"  {'#':<3s} {'Trail':>8s} {'夏普':>7s} {'总收益':>8s} {'年化':>7s} "
          f"{'回撤':>7s} {'卡玛':>7s} {'交易':>5s} {'胜率':>6s} {'盈均':>6s} {'亏均':>6s} "
          f"{'盈利股':>6s} {'亏损股':>6s}")
    print(f"  {'-'*111}")

    for rank, r in enumerate(sorted_all, 1):
        tag = ' << BEST' if rank == 1 else ''
        print(f"  {rank:<3d} {r['trail_pct']:>7.0%} {r['sh']:>7.4f} {r['tr']*100:>7.2f}% "
              f"{r['ar']*100:>6.2f}% {r['mdd']*100:>6.2f}% {r['cm']:>7.3f} "
              f"{r['np']:>5d} {r['wr']*100:>5.1f}% {r['avg_trail_win']*100:>5.1f}% "
              f"{r['avg_trail_loss']*100:>5.1f}% {r['pos_stocks']:>5d} {r['neg_stocks']:>5d}{tag}")

    # Sweet spot analysis
    print(f"\n\n{'='*80}")
    print(f"  最优区间分析")
    print(f"{'='*80}")

    # Best 3
    top3 = sorted_all[:3]
    print(f"\n  --- TOP 3 个股夏普分布 ---")
    for r in top3:
        print(f"  Trail {r['trail_pct']:.0%}: 中位数夏普={r['sh_median']:.3f}  "
              f"Q25={r['sh_q25']:.3f}  Q75={r['sh_q75']:.3f}  "
              f"盈利股={r['pos_stocks']} 亏损股={r['neg_stocks']}")

    # Sharpe vs Trail curve (text-based)
    print(f"\n  --- 夏普 vs Trail 曲线 ---")
    max_sh = max(r['sh'] for r in all_r)
    for r in sorted(all_r, key=lambda r: r['trail_pct']):
        bar_len = int(r['sh'] / max_sh * 50)
        bar = '█' * bar_len
        star = ' ★' if r['sh'] == max_sh else ''
        print(f"  {r['trail_pct']:>5.0%}  {r['sh']:.4f}  {bar}{star}")

    # Return vs DD trade-off
    print(f"\n  --- 收益 vs 回撤 散点 ---")
    for r in sorted(all_r, key=lambda r: r['trail_pct']):
        print(f"  {r['trail_pct']:>5.0%}  Ret={r['tr']*100:>6.1f}%  DD={r['mdd']*100:>5.2f}%  "
              f"比值={r['tr']/(r['mdd']+0.001):.1f}")

    # Best: per-stock TOP 15
    best = sorted_all[0]
    print(f"\n\n  BEST: Trail {best['trail_pct']:.0%} — 个股 TOP 15")
    ss = sorted(best['stock_r'].values(), key=lambda r: r['sh'], reverse=True)
    print(f"  {'股票':<12s} {'赛道':<18s} {'夏普':>7s} {'收益':>9s} {'回撤':>7s} "
          f"{'交易':>4s} {'胜率':>6s} {'Trail#':>6s}")
    for r in ss[:15]:
        print(f"  {r['name']:<12s} {r['sector']:<18s} {r['sh']:>7.3f} "
              f"{r['tr']*100:>8.2f}% {r['mdd']*100:>6.2f}% "
              f"{r['np']:>4d} {r['wr']*100:>5.0f}% {r['trail']:>6d}")

    # Robustness check
    print(f"\n\n  --- 稳健性：盈利股数 vs Trail ---")
    for r in sorted(all_r, key=lambda r: r['trail_pct']):
        bar_len = r['pos_stocks']
        bar = '#' * bar_len
        print(f"  {r['trail_pct']:>5.0%}  Pos={r['pos_stocks']:>2d}  Neg={r['neg_stocks']:>2d}  {bar}")

    print(f"\n  Backtest complete!")

if __name__ == '__main__':
    main()
