"""
MA5 on Stocks + 'Blow-Off Top' Take Profit + Trail 10% Stop
============================================================
Take Profit = 天量滞涨（冲顶止盈）— 3 conditions ALL met:
  1. VOL > k * MA(VOL, 20)         — extreme volume
  2. close == HHV(close, 20)        — new 20-day high
  3. (high-close)/(high-low) > θ    — long upper shadow (selling pressure)

Grid search: k ∈ {2.0, 2.5, 3.0}, θ ∈ {0.5, 0.6, 0.7}
+ Trail 10% stop + Baseline comparison
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
MA_WIN = 5; BUY_THR = -0.045; TRAIL = 0.10
N_VOL = 20; N_HHV = 20


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
        return [{'date': str(d[0]), 'open': float(d[1]), 'close': float(d[2]),
                 'high': float(d[3]), 'low': float(d[4]), 'volume': float(d[5])}
                for d in days if len(d) >= 6]
    except: return []

def calc_ma(data, w):
    ma = []
    for i in range(len(data)):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma

def calc_hhv(data, w):
    """Rolling max over window w, value at index i = max of [i-w+1, i]"""
    hhv = []
    for i in range(len(data)):
        if i < w-1:
            hhv.append(float('nan'))
        else:
            hhv.append(max(data[i-w+1:i+1]))
    return hhv


def generate_signals(bars):
    closes = [b['close'] for b in bars]
    highs  = [b['high'] for b in bars]
    lows   = [b['low'] for b in bars]
    vols   = [b['volume'] for b in bars]

    ma = calc_ma(closes, MA_WIN)
    vol_ma = calc_ma(vols, N_VOL)
    hhv_close = calc_hhv(closes, N_HHV)

    for i, bar in enumerate(bars):
        bar['ma'] = ma[i]
        bar['vol_ma'] = vol_ma[i]
        bar['hhv_close'] = hhv_close[i]

        # MA5 deviation & buy signal
        if math.isnan(ma[i]) or ma[i] == 0:
            bar['deviation'] = float('nan'); bar['signal'] = 'hold'
        else:
            bar['deviation'] = (bar['close'] - ma[i]) / abs(ma[i])
            bar['signal'] = 'buy' if bar['deviation'] < BUY_THR else 'hold'

        # Volume ratio
        if not math.isnan(vol_ma[i]) and vol_ma[i] > 0:
            bar['vol_ratio'] = bar['volume'] / vol_ma[i]
        else:
            bar['vol_ratio'] = float('nan')

        # Upper shadow ratio
        hl_range = bar['high'] - bar['low']
        if hl_range > 0:
            bar['upper_shadow'] = (bar['high'] - bar['close']) / hl_range
        else:
            bar['upper_shadow'] = 0.0

        # New high check
        if not math.isnan(hhv_close[i]):
            bar['is_new_high'] = (bar['close'] >= hhv_close[i] * 0.999)
        else:
            bar['is_new_high'] = False

    return bars


def backtest_single(name, bars, init_cap, k_vol, theta):
    cash = init_cap; pos = 0.0; buy_px = 0.0; peak = 0.0
    trades = []; dvs = []; holding = False
    trail_stops = 0; blowoff_sells = 0

    for bar in bars:
        px = bar['close']

        if holding:
            if px > peak: peak = px

            # Trail stop
            stop_px = peak * (1 - TRAIL)
            if px <= stop_px:
                cash = pos * px; pnl = cash - pos * buy_px
                trades.append({'date': bar['date'], 'action': 'trail_stop', 'price': px,
                               'shares': pos, 'value': cash, 'pnl': pnl, 'peak': peak})
                trail_stops += 1; pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0

            # Blow-off top take profit
            elif holding:
                vr = bar.get('vol_ratio', 0)
                us = bar.get('upper_shadow', 0)
                nh = bar.get('is_new_high', False)

                cond_vol = (not math.isnan(vr)) and vr >= k_vol
                cond_high = nh
                cond_shadow = us > theta

                if cond_vol and cond_high and cond_shadow:
                    cash = pos * px; pnl = cash - pos * buy_px
                    gain_pct = (px - buy_px) / buy_px
                    trades.append({'date': bar['date'], 'action': 'blowoff_sell', 'price': px,
                                   'shares': pos, 'value': cash, 'pnl': pnl,
                                   'gain': gain_pct, 'vol_ratio': vr, 'shadow': us})
                    blowoff_sells += 1; pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0

        # Buy
        if not holding and bar['signal'] == 'buy' and cash > 0:
            pos = cash / px; buy_px = px; peak = px; holding = True
            trades.append({'date': bar['date'], 'action': 'buy', 'price': px,
                           'shares': pos, 'value': cash})
            cash = 0.0

        dvs.append({'date': bar['date'], 'value': cash + (pos*px if holding else 0), 'holding': holding})

    # Final
    if holding:
        fp = bars[-1]['close']; cash = pos * fp; pnl = cash - pos * buy_px
        trades.append({'date': bars[-1]['date'], 'action': 'sell_final', 'price': fp,
                       'shares': pos, 'value': cash, 'pnl': pnl})
        dvs[-1]['value'] = cash; dvs[-1]['holding'] = False

    fv = cash; rets = []
    for i in range(1, len(dvs)):
        p, c = dvs[i-1]['value'], dvs[i]['value']
        if p > 0: rets.append((c-p)/p)
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
        elif t['action'] in ('trail_stop','blowoff_sell','sell_final') and cb:
            pairs.append({'buy_d': cb['date'], 'sell_d': t['date'],
                          'buy_px': cb['price'], 'sell_px': t['price'],
                          'ret': (t['price']-cb['price'])/cb['price'],
                          'pnl': t.get('pnl',0), 'exit': t['action'],
                          'gain': t.get('gain',0), 'vr': t.get('vol_ratio',0),
                          'shadow': t.get('shadow',0)})
            cb = None
    wins = sum(1 for p in pairs if p['ret']>0)
    wr = wins/len(pairs) if pairs else 0
    hd = sum(1 for dv in dvs if dv['holding']); ed = len(dvs)-hd
    return {'name': name, 'tr': tr, 'ar': ar, 'vol': av, 'sh': sh, 'cm': cm,
            'mdd': mdd, 'np': len(pairs), 'wr': wr, 'trail': trail_stops,
            'blowoff': blowoff_sells, 'hd': hd, 'ed': ed,
            'ep': ed/max(len(dvs),1), 'dvs': dvs, 'pairs': pairs, 'fv': fv}


def run_scenario(k_vol, theta, label, all_sigs, common, per_stock):
    sr = {}
    for name in all_sigs:
        sr[name] = backtest_single(name, all_sigs[name], per_stock, k_vol, theta)
    vm = {n: {dv['date']: dv['value']/per_stock for dv in r['dvs']} for n, r in sr.items()}
    pd_ = [{'date': d, 'value': sum(vm[n][d] for n in STOCKS)} for d in common]
    iv, fv = pd_[0]['value'], pd_[-1]['value']
    pr = []
    for i in range(1, len(pd_)):
        p, c = pd_[i-1]['value'], pd_[i]['value']
        if p>0: pr.append((c-p)/p)
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
    th = sum(r['hd'] for r in sr.values())
    tt = sum(r['trail'] for r in sr.values())
    tb = sum(r['blowoff'] for r in sr.values())
    return {'label': label, 'k_vol': k_vol, 'theta': theta,
            'tr': tr, 'ar': ar, 'av': av, 'sh': sh, 'cm': cm, 'mdd': mdd,
            'np': len(all_p), 'wr': wr, 'trail': tt, 'blowoff': tb,
            'th': th, 'stock_r': sr, 'all_p': all_p}


def main():
    print('='*100)
    print('  MA5 + Blow-Off Top Take Profit + Trail 10% Stop')
    print(f'  Buy < {BUY_THR:.1%} | Trail Stop {TRAIL:.0%}')
    print(f'  Take Profit: VOL > k*MA20(VOL) & Close=HHV(20) & UpperShadow > theta')
    print('='*100)

    # ---- Fetch ----
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

    all_sigs = {}
    for name in all_raw:
        bars = [b for b in all_raw[name] if b['date'] in common]
        all_sigs[name] = generate_signals(bars)

    # ---- Blow-off statistics ----
    print(f'\n  Blow-Off Top Event Statistics (N1=N2={N_VOL}):')
    for name in STOCKS:
        bars = all_sigs[name]
        for k in [2.0, 2.5, 3.0]:
            for th in [0.5, 0.6, 0.7]:
                cnt = 0
                for b in bars:
                    vr = b.get('vol_ratio', 0)
                    us = b.get('upper_shadow', 0)
                    nh = b.get('is_new_high', False)
                    if (not math.isnan(vr)) and vr >= k and nh and us > th:
                        cnt += 1
        # just show one combo
        break
    # Per-stock counts for k=2.5, theta=0.6
    print(f'    k=2.5, theta=0.6:')
    for name in STOCKS:
        bars = all_sigs[name]
        cnt = sum(1 for b in bars
                  if (not math.isnan(b.get('vol_ratio', 0))) and b['vol_ratio'] >= 2.5
                  and b.get('is_new_high', False) and b.get('upper_shadow', 0) > 0.6)
        print(f'      {name:<8s}  {cnt} events')

    # ---- Run grid ----
    print('\n[BACKTEST]')
    results = {}

    # Baseline: Trail 10% only
    print('\n  --- Baseline: Trail 10% only ---')
    for name in all_sigs:
        r0 = backtest_single(name, all_sigs[name], per_s, k_vol=999, theta=0)  # never trigger
        results['Baseline_Trail10'] = {'_dummy': r0}

    # Actually run properly
    print('\n  --- Pure Trail 10% (no take profit) ---')
    r0 = run_scenario(999, 0, 'Pure Trail 10%', all_sigs, common, per_s)
    results['Pure Trail 10%'] = r0
    for name in STOCKS:
        sr = r0['stock_r'][name]
        print(f'    {name:<8s}  Ret={sr["tr"]*100:>7.2f}%  S={sr["sh"]:>6.3f}  '
              f'DD={sr["mdd"]*100:>5.2f}%  Trd={sr["np"]:>2d}  '
              f'Trail={sr["trail"]}  BO={sr["blowoff"]}')

    # Recalculate without dummy
    del results['Baseline_Trail10']

    # Grid
    ks = [2.0, 2.5, 3.0]
    thetas = [0.5, 0.6, 0.7]
    for k in ks:
        for th in thetas:
            label = f'k={k}_th={th}'
            print(f'\n  --- {label} ---')
            r = run_scenario(k, th, label, all_sigs, common, per_s)
            results[label] = r
            for name in STOCKS:
                sr = r['stock_r'][name]
                print(f'    {name:<8s}  Ret={sr["tr"]*100:>7.2f}%  S={sr["sh"]:>6.3f}  '
                      f'DD={sr["mdd"]*100:>5.2f}%  Trd={sr["np"]:>2d}  '
                      f'Trail={sr["trail"]}  BO={sr["blowoff"]}')

    # ================================================================
    print('\n\n' + '='*100)
    print('  RANKING: Blow-Off Top Take Profit vs Pure Trail 10%')
    print('='*100)
    sorted_k = sorted(results.keys(), key=lambda x: results[x]['sh'], reverse=True)

    print(f'\n  {"Scenario":<22s} | {"Sharpe":>7s} | {"TotRet":>9s} | {"AnnRet":>8s} | '
          f'{"MaxDD":>7s} | {"Calmar":>7s} | {"Trd":>4s} | {"Win":>5s} | '
          f'{"Trail#":>6s} | {"BOSell#":>7s} |')
    print(f'  {"-"*100}')
    for k in sorted_k:
        r = results[k]
        tag = ' <-- BASELINE' if 'Pure' in k else ''
        print(f'  {k:<22s} | {r["sh"]:>7.3f} | {r["tr"]*100:>8.2f}% | {r["ar"]*100:>7.2f}% | '
              f'{r["mdd"]*100:>6.2f}% | {r["cm"]:>7.3f} | {r["np"]:>4d} | {r["wr"]*100:>4.0f}% | '
              f'{r["trail"]:>6d} | {r["blowoff"]:>7d} |{tag}')

    # Per-stock best
    print(f'\n\n  BEST 3 - Per Stock:')
    for rank, k in enumerate(sorted_k[:3], 1):
        r = results[k]
        print(f'\n  #{rank} {k}: S={r["sh"]:.4f} Ret={r["tr"]*100:.2f}% DD={r["mdd"]*100:.2f}% '
              f'Trail={r["trail"]} BO={r["blowoff"]}')
        for name in STOCKS:
            sr = r['stock_r'][name]
            print(f'    {name:<8s}  Ret={sr["tr"]*100:>7.2f}%  S={sr["sh"]:>6.3f}  '
                  f'DD={sr["mdd"]*100:>5.2f}%  Trd={sr["np"]:>2d}  Trail={sr["trail"]}  BO={sr["blowoff"]}')

    # Best trade log
    best_k = sorted_k[0]
    best = results[best_k]
    print(f'\n\n  BEST: {best_k} - Full Trade Log:')
    print(f'  {"Stock":<8s} {"Buy":<12s} {"Sell":<12s} {"Buy@":>8s} {"Sell@":>8s} {"Ret":>8s} '
          f'{"Exit":>14s} {"Gain":>7s} {"VolR":>6s} {"Shdw":>6s} {"Days":>5s}')
    print(f'  {"-"*100}')
    for p in best['all_p']:
        try:
            bd = datetime.strptime(p['buy_d'],'%Y-%m-%d'); sd = datetime.strptime(p['sell_d'],'%Y-%m-%d')
            hd = (sd-bd).days
        except: hd = '?'
        el = {'trail_stop': 'TRAIL_STOP', 'blowoff_sell': 'BLOWOFF_TOP', 'sell_final': 'FINAL'}.get(p['exit'], p['exit'])
        g = p.get('gain',0); vr = p.get('vr',0); us = p.get('shadow',0)
        print(f'  {p["stock"]:<8s} {p["buy_d"]:<12s} {p["sell_d"]:<12s} '
              f'{p["buy_px"]:>8.3f} {p["sell_px"]:>8.3f} {p["ret"]*100:>7.2f}% '
              f'{el:>14s} {g*100:>6.1f}% {vr:>5.1f}x {us:>5.2f} {str(hd):>5s}')

    # Blow-off events analysis
    print(f'\n\n  Blow-Off Top Event Analysis (k=2.5, theta=0.6):')
    # Find this scenario
    bo_key = 'k=2.5_th=0.6'
    if bo_key in results:
        r_bo = results[bo_key]
        bo_pairs = [p for p in r_bo['all_p'] if p.get('exit') == 'blowoff_sell']
        win_bo = [p for p in bo_pairs if p['ret']>0]
        lose_bo = [p for p in bo_pairs if p['ret']<=0]
        avg_win = sum(p["ret"] for p in win_bo)/len(win_bo)*100 if win_bo else 0
        avg_loss = sum(p["ret"] for p in lose_bo)/len(lose_bo)*100 if lose_bo else 0
        avg_vr = sum(p.get("vr",0) for p in bo_pairs)/len(bo_pairs) if bo_pairs else 0
        avg_us = sum(p.get("shadow",0) for p in bo_pairs)/len(bo_pairs) if bo_pairs else 0
        print(f'    Total blow-off exits: {len(bo_pairs)}')
        print(f'    Winning: {len(win_bo)} (avg ret {avg_win:+.2f}%)')
        print(f'    Losing:  {len(lose_bo)} (avg ret {avg_loss:+.2f}%)')
        print(f'    Avg volume ratio: {avg_vr:.2f}x')
        print(f'    Avg shadow ratio: {avg_us:.2f}')

    # Final comparison with all previous methods
    print(f'\n\n  +{"-"*85}+')
    print(f'  | {"ALL METHODS COMPARISON":<83s} |')
    print(f'  +{"-"*85}+')
    print(f'  | {"Method":<24s} | {"Sharpe":>7s} | {"TotRet":>9s} | {"MaxDD":>8s} | '
          f'{"Calmar":>7s} | {"Trd":>4s} |')
    print(f'  |{"-"*83}|')

    # Known baselines from previous runs
    baselines = [
        ('No Stop (ETF strat)', 1.260, 149.72, 23.72, 1.937, 46),
        ('Trail 10% (prev best)', 1.580, 279.48, 26.89, 2.733, 99),
        ('High-TO + Trail 10%', 1.613, 273.66, 26.51, 2.731, 100),
        ('MACD Stop', 0.622, 43.84, 23.70, 0.684, 83),
        ('ATR 3x', 1.206, 124.06, 23.20, 1.705, 74),
    ]
    for label, sh, tr, dd, cm, np in baselines:
        print(f'  | {label:<24s} | {sh:>7.3f} | {tr:>8.2f}% | {dd:>7.2f}% | '
              f'{cm:>7.3f} | {np:>4d} |')

    # Add current best blow-off
    r_best = results[sorted_k[0]]
    print(f'  | {"Blow-Off + Trail 10%":<24s} | {r_best["sh"]:>7.3f} | {r_best["tr"]*100:>8.2f}% | '
          f'{r_best["mdd"]*100:>7.2f}% | {r_best["cm"]:>7.3f} | {r_best["np"]:>4d} | <-- NEW')
    print(f'  +{"-"*85}+')

    print('\n  Backtest complete!')


if __name__ == '__main__':
    main()
