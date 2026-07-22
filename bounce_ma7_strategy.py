"""
ETF MA5 on Stocks + High Turnover Take Profit + Trail 10% Stop
==============================================================
- MA5, Buy DEV < -4.5%
- Trail Stop: 10% from peak
- Take Profit: price gain >= X% from entry AND volume >= Y * avg_volume(20d)
  "高位高换手": unusually high volume at elevated prices = distribution signal
- Test grid: gain threshold 5%, 10%, 15% x volume multiplier 1.5x, 2.0x, 2.5x, 3.0x
- Equal weight 20% each

Stocks: 北方华创 士兰微 澜起科技 长电科技 长川科技
"""

import sys, io, urllib.request, json, math, time
from collections import defaultdict
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

STOCKS = {
    '北方华创': 'sz002371', '士兰微': 'sh600460', '澜起科技': 'sh688008',
    '长电科技': 'sh600584', '长川科技': 'sz300604',
}
START_DATE = '2024-01-01'; END_DATE = '2026-07-22'
RISK_FREE = 0.025; TRADING_DAYS = 252; INIT_CAP = 1_000_000
MA_WIN = 5; BUY_THR = -0.045; TRAIL_PCT = 0.10
VOL_MA_PERIOD = 20

def fetch(code, start, end):
    url = f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,{start},{end},640,qfq'
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

def generate_signals(bars, gain_thr, vol_mult):
    closes = [b['close'] for b in bars]
    volumes = [b['volume'] for b in bars]
    ma = calc_ma(closes, MA_WIN)
    vol_ma = calc_ma(volumes, VOL_MA_PERIOD)

    for i, bar in enumerate(bars):
        bar['ma'] = ma[i]; bar['vol_ma'] = vol_ma[i]
        if math.isnan(ma[i]) or ma[i] == 0:
            bar['deviation'] = float('nan'); bar['signal'] = 'hold'
        else:
            bar['deviation'] = (bar['close'] - ma[i]) / abs(ma[i])
            bar['signal'] = 'buy' if bar['deviation'] < BUY_THR else 'hold'
        # volume ratio
        if not math.isnan(vol_ma[i]) and vol_ma[i] > 0:
            bar['vol_ratio'] = bar['volume'] / vol_ma[i]
        else:
            bar['vol_ratio'] = float('nan')

    return bars


def backtest_single(name, bars, init_cap, gain_thr, vol_mult):
    cash = init_cap; pos = 0.0; buy_px = 0.0; peak = 0.0
    trades = []; dvs = []; holding = False
    trail_stops = 0; turnover_sells = 0

    for bar in bars:
        px = bar['close']

        if holding:
            if px > peak: peak = px
            # Trail stop
            stop_px = peak * (1 - TRAIL_PCT)
            if px <= stop_px:
                cash = pos * px; pnl = cash - pos * buy_px
                trades.append({'date': bar['date'], 'action': 'trail_stop', 'price': px,
                               'shares': pos, 'value': cash, 'pnl': pnl, 'peak': peak})
                trail_stops += 1; pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0

            # High-turnover take profit
            elif holding:
                gain_pct = (px - buy_px) / buy_px
                vr = bar.get('vol_ratio', 0)
                high_vol = (not math.isnan(vr)) and vr >= vol_mult
                high_gain = gain_pct >= gain_thr
                if high_gain and high_vol:
                    cash = pos * px; pnl = cash - pos * buy_px
                    trades.append({'date': bar['date'], 'action': 'turnover_sell', 'price': px,
                                   'shares': pos, 'value': cash, 'pnl': pnl,
                                   'gain': gain_pct, 'vol_ratio': vr})
                    turnover_sells += 1; pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0

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
    peak_v = dvs[0]['value']; mdd = 0.0
    for dv in dvs:
        if dv['value'] > peak_v: peak_v = dv['value']
        dd = (peak_v-dv['value'])/peak_v
        if dd > mdd: mdd = dd
    tr = (fv-init_cap)/init_cap
    if len(rets) > 1:
        mu = sum(rets)/len(rets)
        sd = (sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av = sd * math.sqrt(TRADING_DAYS); ar_ = mu * TRADING_DAYS
        sh = (ar_-RISK_FREE)/av if av > 0 else 0
    else: av = sh = ar_ = 0.0
    ar = (1+tr)**(TRADING_DAYS/max(len(rets),1))-1 if tr > -1 else -1
    cm = ar/mdd if mdd > 0 else float('inf')

    pairs = []; cb = None
    for t in trades:
        if t['action'] == 'buy': cb = t
        elif t['action'] in ('trail_stop','turnover_sell','sell_final') and cb:
            pairs.append({'buy_date': cb['date'], 'sell_date': t['date'],
                          'buy_px': cb['price'], 'sell_px': t['price'],
                          'ret': (t['price']-cb['price'])/cb['price'],
                          'pnl': t.get('pnl',0), 'exit': t['action'],
                          'gain': t.get('gain',0), 'vr': t.get('vol_ratio',0)})
            cb = None
    wins = sum(1 for p in pairs if p['ret'] > 0)
    wr = wins/len(pairs) if pairs else 0
    hd = sum(1 for dv in dvs if dv['holding']); ed = len(dvs)-hd
    return {'name': name, 'tr': tr, 'ar': ar, 'vol': av, 'sh': sh, 'cm': cm,
            'mdd': mdd, 'np': len(pairs), 'wr': wr, 'trail': trail_stops,
            'turnover': turnover_sells, 'hd': hd, 'ed': ed,
            'ep': ed/max(len(dvs),1), 'trades': trades, 'pairs': pairs,
            'dvs': dvs, 'fv': fv}


def run_scenario(gain_thr, vol_mult, label, all_sigs, common_dates, per_stock):
    sr = {}
    for name in all_sigs:
        sr[name] = backtest_single(name, all_sigs[name], per_stock, gain_thr, vol_mult)
    vm = {n: {dv['date']: dv['value']/per_stock for dv in r['dvs']} for n, r in sr.items()}
    pd_ = [{'date': d, 'value': sum(vm[n][d] for n in STOCKS)} for d in common_dates]
    iv, fv = pd_[0]['value'], pd_[-1]['value']
    pr = []
    for i in range(1, len(pd_)):
        p, c = pd_[i-1]['value'], pd_[i]['value']
        if p > 0: pr.append((c-p)/p)
    pk = iv; mdd = 0.0
    for dv in pd_:
        if dv['value'] > pk: pk = dv['value']
        dd = (pk-dv['value'])/pk
        if dd > mdd: mdd = dd
    tr = (fv-iv)/iv
    if len(pr) > 1:
        mu = sum(pr)/len(pr); va = sum((r-mu)**2 for r in pr)/(len(pr)-1)
        av = va**0.5*math.sqrt(TRADING_DAYS); sh = (mu*TRADING_DAYS-RISK_FREE)/av if av>0 else 0
    else: av = sh = 0.0
    ar = (1+tr)**(TRADING_DAYS/max(len(pr),1))-1 if tr>-1 else -1
    cm = ar/mdd if mdd>0 else float('inf')
    all_p = []
    for n, r in sr.items():
        for p in r['pairs']: all_p.append({**p, 'stock': n})
    all_p.sort(key=lambda x: x['buy_date'])
    wins = sum(1 for p in all_p if p['ret']>0)
    wr = wins/len(all_p) if all_p else 0
    th = sum(r['hd'] for r in sr.values())
    tt = sum(r['trail'] for r in sr.values())
    to = sum(r['turnover'] for r in sr.values())
    return {'label': label, 'gain_thr': gain_thr, 'vol_mult': vol_mult,
            'tr': tr, 'ar': ar, 'av': av, 'sh': sh, 'cm': cm, 'mdd': mdd,
            'np': len(all_p), 'wr': wr, 'trail': tt, 'turnover': to,
            'th': th, 'stock_r': sr, 'all_p': all_p}


def main():
    print('='*100)
    print('  MA5 on Stocks + High-Turnover Take Profit + Trail 10% Stop')
    print(f'  Buy < {BUY_THR:.1%} | Trail Stop {TRAIL_PCT:.0%} | Take Profit = Gain+X% & Vol>Y*MA20(Vol)')
    print('='*100)

    print('\n[DATA] Fetching...')
    all_raw = {}
    for name, code in STOCKS.items():
        print(f'  -> {name} ({code}) ...', end=' ', flush=True)
        raw = fetch(code, START_DATE, END_DATE)
        bars = parse(raw, code)
        if bars: all_raw[name] = bars; print(f'OK {len(bars)}')
        else: print('FAIL')
        time.sleep(0.2)
    ds = [set(b['date'] for b in bars) for bars in all_raw.values()]
    common = sorted(ds[0].intersection(*ds[1:]))
    print(f'  Common days: {len(common)}')
    per_s = INIT_CAP / len(all_raw)

    # Test grid
    gains = [0.05, 0.10, 0.15]
    vols  = [1.5, 2.0, 2.5, 3.0]
    # Also baseline: DEV>7% sell + Trail 10%
    baselines = [('DEV>7%', None, None)]

    print('\n[BACKTEST]')
    results = {}

    # --- Baseline: DEV>7% profit + Trail 10% ---
    print('\n  --- Baseline: DEV>7% take profit + Trail 10% ---')
    # Reuse generate_signals but add sell signal for DEV>7%
    baseline_sigs = {}
    for name in all_raw:
        bars = [b for b in all_raw[name] if b['date'] in common]
        baseline_sigs[name] = generate_signals(bars, 0.07, 2.0)  # gain_thr unused here
        # Override: add DEV>7% sell signal
        for bar in baseline_sigs[name]:
            if not math.isnan(bar['deviation']) and bar['deviation'] > 0.07:
                bar['signal'] = 'sell'
    # Run with DEV>7% logic (use a special backtest)
    base_r = run_scenario_dev(0.07, baseline_sigs, common, per_s)
    results['DEV>7%'] = base_r

    # --- Grid: Turnover-based take profit ---
    for gain_thr in gains:
        for vol_mult in vols:
            label = f'Gain{gain_thr:.0%}_Vol{vol_mult:.1f}x'
            print(f'\n  --- {label} ---')
            sigs = {}
            for name in all_raw:
                bars = [b for b in all_raw[name] if b['date'] in common]
                sigs[name] = generate_signals(bars, gain_thr, vol_mult)
            r = run_scenario(gain_thr, vol_mult, label, sigs, common, per_s)
            results[label] = r
            for name in STOCKS:
                sr = r['stock_r'][name]
                print(f'    {name:<8s}  Ret={sr["tr"]*100:>7.2f}%  S={sr["sh"]:>6.3f}  '
                      f'DD={sr["mdd"]*100:>5.2f}%  Trd={sr["np"]:>2d}  Win={sr["wr"]*100:>4.0f}%  '
                      f'Trail={sr["trail"]}  TOSell={sr["turnover"]}')

    # ================================================================
    print('\n\n' + '='*100)
    print('  SUMMARY: HIGH-TURNOVER TAKE PROFIT vs DEV>7% BASELINE')
    print('='*100)
    print(f'\n  {"Scenario":<22s} | {"Sharpe":>7s} | {"TotRet":>9s} | {"AnnRet":>8s} | '
          f'{"MaxDD":>7s} | {"Calmar":>7s} | {"Trd":>4s} | {"Win":>5s} | '
          f'{"Trail#":>6s} | {"TOSell#":>7s} |')
    print(f'  {"-"*102}')

    # Sort by sharpe
    sorted_keys = sorted(results.keys(), key=lambda k: results[k]['sh'], reverse=True)
    for k in sorted_keys:
        r = results[k]
        tag = ' <-- BASELINE' if 'DEV' in k else ''
        print(f'  {k:<22s} | {r["sh"]:>7.3f} | {r["tr"]*100:>8.2f}% | {r["ar"]*100:>7.2f}% | '
              f'{r["mdd"]*100:>6.2f}% | {r["cm"]:>7.3f} | {r["np"]:>4d} | {r["wr"]*100:>4.0f}% | '
              f'{r["trail"]:>6d} | {r["turnover"]:>7d} |{tag}')

    # Best 3 detail
    print(f'\n\n  BEST 3 SCENARIOS - Per Stock:')
    for rank, k in enumerate(sorted_keys[:3], 1):
        r = results[k]
        print(f'\n  #{rank} {k}: Sharpe={r["sh"]:.4f} TotRet={r["tr"]*100:.2f}% MaxDD={r["mdd"]*100:.2f}%')
        for name in STOCKS:
            sr = r['stock_r'][name]
            print(f'    {name:<8s}  Ret={sr["tr"]*100:>7.2f}%  S={sr["sh"]:>6.3f}  '
                  f'DD={sr["mdd"]*100:>5.2f}%  Trd={sr["np"]:>2d}  Trail={sr["trail"]}  TO={sr["turnover"]}')

    # Trade log for best
    best_k = sorted_keys[0]
    best = results[best_k]
    print(f'\n\n  BEST: {best_k} - Trade Log:')
    print(f'  {"Stock":<8s} {"Buy":<12s} {"Sell":<12s} {"Buy@":>8s} {"Sell@":>8s} {"Ret":>8s} {"Exit":>14s} {"Gain":>7s} {"VolR":>6s} {"Days":>5s}')
    print(f'  {"-"*100}')
    for p in best['all_p']:
        try:
            bd = datetime.strptime(p['buy_date'],'%Y-%m-%d'); sd = datetime.strptime(p['sell_date'],'%Y-%m-%d')
            hd = (sd-bd).days
        except: hd = '?'
        el = {'trail_stop': 'TRAIL_STOP', 'turnover_sell': 'TURNOVER_SELL', 'sell_final': 'FINAL'}.get(p['exit'], p['exit'])
        g = p.get('gain',0); vr = p.get('vr',0)
        print(f'  {p["stock"]:<8s} {p["buy_date"]:<12s} {p["sell_date"]:<12s} '
              f'{p["buy_px"]:>8.3f} {p["sell_px"]:>8.3f} {p["ret"]*100:>7.2f}% '
              f'{el:>14s} {g*100:>6.1f}% {vr:>5.1f}x {str(hd):>5s}')

    print('\n  Backtest complete!')


def run_scenario_dev(sell_thr, all_sigs, common_dates, per_stock):
    """Special: use DEV > sell_thr as take profit, + trail 10% stop"""
    # We need to inject sell signal into bars
    for name, bars in all_sigs.items():
        for bar in bars:
            if not math.isnan(bar['deviation']) and bar['deviation'] > sell_thr:
                bar['signal_sell_dev'] = True
            else:
                bar['signal_sell_dev'] = False

    sr = {}
    for name in all_sigs:
        bars = all_sigs[name]
        cash = per_stock; pos = 0.0; buy_px = 0.0; peak = 0.0
        trades = []; dvs = []; holding = False; trail = 0; to_sell = 0

        for bar in bars:
            px = bar['close']
            if holding:
                if px > peak: peak = px
                sp = peak*(1-TRAIL_PCT)
                if px <= sp:
                    cash = pos*px; pnl = cash-pos*buy_px
                    trades.append({'date': bar['date'], 'action': 'trail_stop', 'price': px,
                                   'shares': pos, 'value': cash, 'pnl': pnl})
                    trail += 1; pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0
                elif holding and bar.get('signal_sell_dev'):
                    cash = pos*px; pnl = cash-pos*buy_px
                    trades.append({'date': bar['date'], 'action': 'dev_sell', 'price': px,
                                   'shares': pos, 'value': cash, 'pnl': pnl,
                                   'gain': (px-buy_px)/buy_px})
                    to_sell += 1; pos = 0.0; buy_px = 0.0; holding = False; peak = 0.0
            if not holding and bar.get('signal') == 'buy' and cash > 0:
                pos = cash/px; buy_px = px; peak = px; holding = True
                trades.append({'date': bar['date'], 'action': 'buy', 'price': px,
                               'shares': pos, 'value': cash})
                cash = 0.0
            dvs.append({'date': bar['date'], 'value': cash+(pos*px if holding else 0), 'holding': holding})
        if holding:
            fp = bars[-1]['close']; cash = pos*fp; pnl = cash-pos*buy_px
            trades.append({'date': bars[-1]['date'], 'action': 'sell_final', 'price': fp,
                           'shares': pos, 'value': cash, 'pnl': pnl})
            dvs[-1]['value'] = cash; dvs[-1]['holding'] = False
        fv = cash; rets = []
        for i in range(1,len(dvs)):
            p,c = dvs[i-1]['value'], dvs[i]['value']
            if p>0: rets.append((c-p)/p)
        pkv = dvs[0]['value']; mdd = 0.0
        for dv in dvs:
            if dv['value']>pkv: pkv = dv['value']
            dd = (pkv-dv['value'])/pkv
            if dd>mdd: mdd = dd
        tr = (fv-per_stock)/per_stock
        if len(rets)>1:
            mu = sum(rets)/len(rets)
            sd = (sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
            av = sd*math.sqrt(TRADING_DAYS); ar_ = mu*TRADING_DAYS
            sh = (ar_-RISK_FREE)/av if av>0 else 0
        else: av = sh = ar_ = 0.0
        ar = (1+tr)**(TRADING_DAYS/max(len(rets),1))-1 if tr>-1 else -1
        cm = ar/mdd if mdd>0 else float('inf')
        pairs = []; cb = None
        for t in trades:
            if t['action']=='buy': cb = t
            elif t['action'] in ('trail_stop','dev_sell','sell_final') and cb:
                pairs.append({'buy_date': cb['date'], 'sell_date': t['date'],
                              'buy_px': cb['price'], 'sell_px': t['price'],
                              'ret': (t['price']-cb['price'])/cb['price'],
                              'pnl': t.get('pnl',0), 'exit': t['action'],
                              'gain': t.get('gain',0), 'vr': 0})
                cb = None
        wins = sum(1 for p in pairs if p['ret']>0)
        wr = wins/len(pairs) if pairs else 0
        hd = sum(1 for dv in dvs if dv['holding']); ed = len(dvs)-hd
        sr[name] = {'tr': tr, 'ar': ar, 'vol': av, 'sh': sh, 'cm': cm, 'mdd': mdd,
                     'np': len(pairs), 'wr': wr, 'trail': trail, 'turnover': to_sell,
                     'hd': hd, 'ed': ed, 'dvs': dvs, 'pairs': pairs}

    vm = {n: {dv['date']: dv['value']/per_stock for dv in r['dvs']} for n, r in sr.items()}
    pd_ = [{'date': d, 'value': sum(vm[n][d] for n in STOCKS)} for d in common_dates]
    iv, fv = pd_[0]['value'], pd_[-1]['value']
    pr = []
    for i in range(1,len(pd_)):
        p,c = pd_[i-1]['value'], pd_[i]['value']
        if p>0: pr.append((c-p)/p)
    pk = iv; mdd = 0.0
    for dv in pd_:
        if dv['value']>pk: pk = dv['value']
        dd = (pk-dv['value'])/pk
        if dd>mdd: mdd = dd
    tr = (fv-iv)/iv
    if len(pr)>1:
        mu = sum(pr)/len(pr); va = sum((r-mu)**2 for r in pr)/(len(pr)-1)
        av = va**0.5*math.sqrt(TRADING_DAYS); sh = (mu*TRADING_DAYS-RISK_FREE)/av if av>0 else 0
    else: av = sh = 0.0
    ar = (1+tr)**(TRADING_DAYS/max(len(pr),1))-1 if tr>-1 else -1
    cm = ar/mdd if mdd>0 else float('inf')
    all_p = []
    for n, r in sr.items():
        for p in r['pairs']: all_p.append({**p, 'stock': n})
    all_p.sort(key=lambda x: x['buy_date'])
    wins = sum(1 for p in all_p if p['ret']>0)
    wr = wins/len(all_p) if all_p else 0
    th = sum(r['hd'] for r in sr.values())
    tt = sum(r['trail'] for r in sr.values())
    to = sum(r['turnover'] for r in sr.values())
    return {'label': 'DEV>7%', 'gain_thr': 0.07, 'vol_mult': 0,
            'tr': tr, 'ar': ar, 'av': av, 'sh': sh, 'cm': cm, 'mdd': mdd,
            'np': len(all_p), 'wr': wr, 'trail': tt, 'turnover': to,
            'th': th, 'stock_r': sr, 'all_p': all_p}


if __name__ == '__main__':
    main()
