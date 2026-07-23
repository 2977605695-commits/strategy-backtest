"""
Range-Bound MA3/MA7 Crossover + Trail Stop Only
================================================
Buy (ALL must be met):
  1. Range-bound: past N days, max_up <= Cap% AND max_down <= Cap%
  2. MA3/MA7 deviation >= +2% (MA3 crosses 2% above MA7)

Sell: Trail stop ONLY (no MA touch)

Grid: N ∈ {5,7,10,14}  Cap ∈ {5%,7%,10%}  Trail ∈ {10%,15%,20%,25%,30%}
      180 combos × 5 stocks

Stocks: 北方华创 士兰微 澜起科技 长电科技 长川科技 (equal weight 20%)
Period: 2024-01-01 to 2026-07-22
"""

import sys, io, urllib.request, json, math, time
from collections import defaultdict
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

STOCKS = {
    '北方华创': 'sz002371', '士兰微': 'sh600460', '澜起科技': 'sh688008',
    '长电科技': 'sh600584', '长川科技': 'sz300604',
}
START = '2024-01-01'; END = '2026-07-22'
RF = 0.025; TD = 252; INIT = 1_000_000
MA_CROSS_THRESHOLD = 0.02

Ns = [5, 7, 10, 14]
CAPS = [0.05, 0.07, 0.10]
TRAILS = [0.10, 0.15, 0.20, 0.25, 0.30]

def fetch(code, s, e):
    url = f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,{s},{e},640,qfq'
    h = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'}
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=15) as r: return json.loads(r.read().decode('utf-8'))
        except: time.sleep(1)
    return {}

def parse(raw, code):
    try:
        days = None
        if 'data' in raw:
            for k in raw['data']:
                if isinstance(raw['data'][k], dict):
                    for f in ['qfqday', 'day']:
                        if f in raw['data'][k]: days = raw['data'][k][f]; break
                    if days: break
        if not days: return []
        return [{'date': str(d[0]), 'close': float(d[2])}
                for d in days if len(d) >= 6]
    except: return []

def calc_ma(data, w):
    ma = []
    for i in range(len(data)):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma

def generate_signals(bars, N, cap):
    closes = [b['close'] for b in bars]
    ma3 = calc_ma(closes, 3)
    ma7 = calc_ma(closes, 7)
    n = len(bars)
    for i, bar in enumerate(bars):
        bar['ma3'] = ma3[i]; bar['ma7'] = ma7[i]
        if math.isnan(ma3[i]) or math.isnan(ma7[i]) or ma7[i] == 0:
            bar['ma_cross_dev'] = float('nan')
        else:
            bar['ma_cross_dev'] = (ma3[i] - ma7[i]) / abs(ma7[i])
        if i < N:
            bar['is_range_bound'] = False
        else:
            window = closes[i-N+1:i+1]; cur = closes[i]
            mx_up = (max(window) - cur) / cur
            mx_dn = (cur - min(window)) / min(window)
            bar['is_range_bound'] = (mx_up <= cap and mx_dn <= cap)
        bar['signal_buy'] = (bar.get('is_range_bound', False) and
                             not math.isnan(bar.get('ma_cross_dev', float('nan'))) and
                             bar['ma_cross_dev'] >= MA_CROSS_THRESHOLD)
    return bars


def backtest_single(name, bars, init_cap, trail_pct):
    cash = init_cap; pos = 0.0; buy_px = 0.0; peak = 0.0
    trades = []; dvs = []; holding = False; trail_stops = 0

    for bar in bars:
        px = bar['close']
        if holding:
            if px > peak: peak = px
            stop_px = peak * (1 - trail_pct)
            if px <= stop_px:
                cash = pos * px; pnl = cash - pos * buy_px
                trades.append({'date': bar['date'], 'action': 'trail_stop', 'price': px,
                               'shares': pos, 'value': cash, 'pnl': pnl, 'peak': peak})
                trail_stops += 1; pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0
        if not holding and bar.get('signal_buy', False) and cash > 0:
            pos = cash / px; buy_px = px; peak = px; holding = True
            trades.append({'date': bar['date'], 'action': 'buy', 'price': px,
                           'shares': pos, 'value': cash})
            cash = 0.0
        dvs.append({'date': bar['date'],
                     'value': cash + (pos*px if holding else 0), 'holding': holding})

    if holding:
        fp = bars[-1]['close']; cash = pos * fp; pnl = cash - pos * buy_px
        trades.append({'date': bars[-1]['date'], 'action': 'sell_final', 'price': fp,
                       'shares': pos, 'value': cash, 'pnl': pnl})
        dvs[-1]['value'] = cash; dvs[-1]['holding'] = False

    fv = cash; rets = []
    for i in range(1, len(dvs)):
        p, c = dvs[i-1]['value'], dvs[i]['value']
        if p > 0: rets.append((c-p)/p)
    if not rets: rets = [0.0]
    pkv = dvs[0]['value']; mdd = 0.0
    for dv in dvs:
        if dv['value'] > pkv: pkv = dv['value']
        dd = (pkv-dv['value'])/pkv
        if dd > mdd: mdd = dd
    tr = (fv-init_cap)/init_cap
    if len(rets) > 1:
        mu = sum(rets)/len(rets)
        sd = (sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av = sd*math.sqrt(TD); ar_ = mu*TD
        sh = (ar_-RF)/av if av>0 else 0
    else: av = sh = ar_ = 0.0
    ar = (1+tr)**(TD/max(len(rets),1))-1 if tr>-1 else -1
    cm = ar/mdd if mdd>0 else float('inf')
    pairs = []; cb = None
    for t in trades:
        if t['action']=='buy': cb = t
        elif t['action'] in ('trail_stop','sell_final') and cb:
            pairs.append({'buy_d': cb['date'], 'sell_d': t['date'],
                          'buy_px': cb['price'], 'sell_px': t['price'],
                          'ret': (t['price']-cb['price'])/cb['price'],
                          'pnl': t.get('pnl',0), 'exit': t['action']})
            cb = None
    wins = sum(1 for p in pairs if p['ret']>0)
    wr = wins/len(pairs) if pairs else 0
    hd = sum(1 for dv in dvs if dv['holding']); ed = len(dvs)-hd
    return {'name': name, 'tr': tr, 'ar': ar, 'vol': av, 'sh': sh, 'cm': cm,
            'mdd': mdd, 'np': len(pairs), 'wr': wr, 'trail': trail_stops,
            'hd': hd, 'ed': ed, 'ep': ed/max(len(dvs),1), 'dvs': dvs, 'pairs': pairs, 'fv': fv}


def main():
    print('='*100)
    print('  Range-Bound MA3/MA7 Crossover + Trail Stop ONLY')
    print(f'  Buy: N-day range-bound(Cap) + MA3/MA7 >= {MA_CROSS_THRESHOLD:.0%}')
    print(f'  Sell: Trail Stop ONLY')
    print(f'  Grid: N∈{Ns} Cap∈{[f"{c:.0%}" for c in CAPS]} Trail∈{[f"{t:.0%}" for t in TRAILS]}')
    print('='*100)

    print('\n[DATA] Fetching...')
    all_raw = {}
    for name, code in STOCKS.items():
        print(f'  -> {name} ({code}) ...', end=' ', flush=True)
        raw = fetch(code, START, END); bars = parse(raw, code)
        if bars: all_raw[name] = bars; print(f'OK {len(bars)}')
        else: print('FAIL')
        time.sleep(0.2)
    ds = [set(b['date'] for b in bars) for bars in all_raw.values()]
    common = sorted(ds[0].intersection(*ds[1:]))
    print(f'  Common days: {len(common)}')
    per_s = INIT / len(all_raw)

    TOTAL = len(Ns) * len(CAPS) * len(TRAILS)
    print(f'\n[GRID] {len(Ns)}x{len(CAPS)}x{len(TRAILS)} = {TOTAL} combos')

    signal_cache = {}
    def get_signals(N, cap):
        key = (N, cap)
        if key not in signal_cache:
            sigs = {}
            for name in all_raw:
                bars = [b for b in all_raw[name] if b['date'] in common]
                sigs[name] = generate_signals(bars, N, cap)
            signal_cache[key] = sigs
        return signal_cache[key]

    results = []; count = 0
    print('\n[BACKTEST] Running...')
    for N in Ns:
        for cap in CAPS:
            sigs = get_signals(N, cap)
            for trail in TRAILS:
                count += 1
                label = f'N={N} Cap={cap:.0%} Trail={trail:.0%}'
                sr = {}
                for name in all_raw:
                    sr[name] = backtest_single(name, sigs[name], per_s, trail)
                # Portfolio combine
                vm = {n: {dv['date']: dv['value']/per_s for dv in r['dvs']} for n, r in sr.items()}
                pd_ = [{'date': d, 'value': sum(vm[n][d] for n in STOCKS)} for d in common]
                iv, fv = pd_[0]['value'], pd_[-1]['value']
                pr = []
                for i in range(1, len(pd_)):
                    p, c = pd_[i-1]['value'], pd_[i]['value']
                    if p>0: pr.append((c-p)/p)
                if not pr: pr = [0.0]
                pk = iv; mdd = 0.0
                for dv in pd_:
                    if dv['value']>pk: pk = dv['value']
                    dd = (pk-dv['value'])/pk
                    if dd>mdd: mdd = dd
                tr = (fv-iv)/iv
                if len(pr)>1:
                    mu = sum(pr)/len(pr); va = sum((r-mu)**2 for r in pr)/(len(pr)-1)
                    av = va**0.5*math.sqrt(TD); sh = (mu*TD-RF)/av if av>0 else 0
                else: av = sh = 0.0
                ar = (1+tr)**(TD/max(len(pr),1))-1 if tr>-1 else -1
                cm = ar/mdd if mdd>0 else float('inf')
                all_p = []
                for n, r in sr.items():
                    for p in r['pairs']: all_p.append({**p, 'stock': n})
                all_p.sort(key=lambda x: x['buy_d'])
                wins = sum(1 for p in all_p if p['ret']>0)
                wr = wins/len(all_p) if all_p else 0
                tt = sum(r['trail'] for r in sr.values())
                th = sum(r['hd'] for r in sr.values())
                results.append({'label': label, 'N': N, 'cap': cap, 'trail': trail,
                                'tr': tr, 'ar': ar, 'av': av, 'sh': sh, 'cm': cm,
                                'mdd': mdd, 'np': len(all_p), 'wr': wr, 'trail_n': tt,
                                'th': th, 'stock_r': sr, 'all_p': all_p})
                if count % 15 == 1 or count == TOTAL:
                    print(f'  [{count:>3d}/{TOTAL}] {label:<25s} '
                          f'S={sh:>7.3f} Ret={tr*100:>7.2f}% DD={mdd*100:>5.2f}% '
                          f'Trd={len(all_p):>3d} Win={wr*100:>4.0f}% Trail={tt:>3d} Hold={th:>4d}d')

    # ================================================================
    results.sort(key=lambda x: x['sh'], reverse=True)

    print('\n\n' + '='*100)
    print('  TOP 30 by Sharpe (Trail Stop ONLY)')
    print('='*100)
    print(f'  {"Rank":<4s} {"N":>3s} {"Cap":>5s} {"Trail":>6s} '
          f'{"Sharpe":>7s} {"TotRet":>9s} {"AnnRet":>8s} {"MaxDD":>7s} '
          f'{"Calmar":>7s} {"Trd":>4s} {"Win":>5s} {"Trail#":>6s} {"HoldD":>6s}')
    print(f'  {"-"*90}')
    for rank, r in enumerate(results[:30], 1):
        print(f'  {rank:<4d} {r["N"]:>3d} {r["cap"]:>5.0%} {r["trail"]:>6.0%} '
              f'{r["sh"]:>7.3f} {r["tr"]*100:>8.2f}% {r["ar"]*100:>7.2f}% '
              f'{r["mdd"]*100:>6.2f}% {r["cm"]:>7.3f} '
              f'{r["np"]:>4d} {r["wr"]*100:>4.0f}% {r["trail_n"]:>6d} {r["th"]:>6d}')

    # Bottom 5
    print(f'\n  BOTTOM 5:')
    for rank, r in enumerate(results[-5:], len(results)-4):
        print(f'  {rank:<4d} {r["label"]:<25s} S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% '
              f'DD={r["mdd"]*100:>5.2f}% Trd={r["np"]:>3d}')

    # ================================================================
    # Best 3 detail
    print('\n\n' + '='*100)
    print('  TOP 3 - Per Stock Detail')
    print('='*100)
    for rank, r in enumerate(results[:3], 1):
        print(f'\n  #{rank} {r["label"]}: S={r["sh"]:.4f} Ret={r["tr"]*100:.2f}% '
              f'Ann={r["ar"]*100:.2f}% DD={r["mdd"]*100:.2f}% '
              f'Calmar={r["cm"]:.3f} Trd={r["np"]} Win={r["wr"]*100:.0f}%')
        print(f'    Trail stops: {r["trail_n"]}')
        for name in STOCKS:
            sr = r['stock_r'][name]
            print(f'    {name:<8s}  Ret={sr["tr"]*100:>7.2f}%  S={sr["sh"]:>6.3f}  '
                  f'DD={sr["mdd"]*100:>5.2f}%  Trd={sr["np"]:>2d}  '
                  f'Win={sr["wr"]*100:>4.0f}%  Trail={sr["trail"]:>3d}  Hold={sr["hd"]:>4d}d')

    # Trade log for best
    best = results[0]
    print(f'\n\n  BEST: {best["label"]} - Trade Log (all stocks):')
    print(f'  {"Stk":<8s} {"Buy":<12s} {"Sell":<12s} {"Buy@":>8s} {"Sell@":>8s} '
          f'{"Ret":>8s} {"Exit":>12s} {"Days":>5s}')
    print(f'  {"-"*75}')
    for p in best['all_p']:
        try:
            bd = datetime.strptime(p['buy_d'],'%Y-%m-%d'); sd = datetime.strptime(p['sell_d'],'%Y-%m-%d')
            hd = (sd-bd).days
        except: hd = '?'
        el = {'trail_stop': 'TRAIL', 'sell_final': 'FINAL'}.get(p['exit'], p['exit'])
        print(f'  {p["stock"]:<8s} {p["buy_d"]:<12s} {p["sell_d"]:<12s} '
              f'{p["buy_px"]:>8.3f} {p["sell_px"]:>8.3f} {p["ret"]*100:>7.2f}% '
              f'{el:>12s} {str(hd):>5s}')

    # ================================================================
    # Parameter sensitivity
    print('\n\n' + '='*100)
    print('  PARAMETER SENSITIVITY (avg Sharpe)')
    print('='*100)
    for pname, pkey in [('N (lookback days)', 'N'), ('Cap (range bound)', 'cap'),
                          ('Trail (stop loss)', 'trail')]:
        levels = {}
        for r in results:
            levels.setdefault(r[pkey], []).append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(levels.keys()):
            avg_s = sum(levels[v])/len(levels[v])
            bar_len = max(int(avg_s * 40), 1) if avg_s > 0 else int(abs(avg_s) * 40)
            bar = '█'*bar_len if avg_s > 0 else '░'*bar_len
            print(f'    {v:>6}  avg Sharpe={avg_s:>7.3f}  {bar}')

    # Signal stats
    print(f'\n\n  SIGNAL STATS (best params N={best["N"]} Cap={best["cap"]:.0%}):')
    sigs = get_signals(best['N'], best['cap'])
    for name in STOCKS:
        buy_n = sum(1 for b in sigs[name] if b.get('signal_buy', False))
        rb_n = sum(1 for b in sigs[name] if b.get('is_range_bound', False))
        cross_n = sum(1 for b in sigs[name]
                       if not math.isnan(b.get('ma_cross_dev', float('nan')))
                       and b['ma_cross_dev'] >= MA_CROSS_THRESHOLD)
        print(f'    {name:<8s}  Buy={buy_n:>4d}  Range-bound={rb_n:>4d}  MA3>MA7+2%={cross_n:>4d}')

    print('\n  Done!')


if __name__ == '__main__':
    main()
