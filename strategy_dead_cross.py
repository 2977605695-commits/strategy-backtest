"""
MA金叉+超跌过滤 · 全MA组合+乖离率网格搜索
===========================================
Buy: MA_fast/MA_slow golden cross (dev>=X%) AND N-day max drawdown >= D%
Sell: Trail stop (T%)
Pool: 70 stocks dynamic, 5-position rotation, no sector constraint

Grid:
  MA pairs: (3,7), (3,10), (5,10), (5,20), (7,20)
  Cross thr: 1%, 2%, 3%, 4%
  Oversold N: 5, 7, 10, 14
  Oversold D: 5%, 7%, 10%, 12%, 15%
  Trail: 10%, 15%, 20%, 25%, 30%
  = 5 × 4 × 4 × 5 × 5 = 2000 combos
"""

import sys, io, json, math, os, csv
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
START = '2020-01-01'; END = '2026-07-22'
RF = 0.025; TD = 252; INIT = 10_000_000; MAX_POS = 5
SLIPPAGE = 0.003; COMM = 0.00025; STAMP = 0.0005

MA_PAIRS = [(3,7), (3,10), (5,10), (5,20), (7,20)]
CROSS_THRS = [0.01, 0.02, 0.03, 0.04]
OS_Ns = [5, 7, 10, 14]
OS_Ds = [0.05, 0.07, 0.10, 0.12, 0.15]
TRAILS = [0.10, 0.15, 0.20, 0.25, 0.30]
TOTAL = len(MA_PAIRS) * len(CROSS_THRS) * len(OS_Ns) * len(OS_Ds) * len(TRAILS)


def load_stocks():
    stocks = {}
    for fn in sorted(os.listdir(DATA_DIR)):
        if not fn.endswith('.json') or fn.startswith('_'): continue
        d = json.load(open(os.path.join(DATA_DIR, fn), encoding='utf-8'))
        if len(d['bars']) < 64: continue
        bars = []
        for b in d['bars']:
            dt = b['date']
            if len(dt) == 8: dt = f'{dt[:4]}-{dt[4:6]}-{dt[6:8]}'
            if START <= dt <= END:
                bars.append({'date': dt, 'close': float(b['close']),
                             'high': float(b.get('high', b['close']))})
        if bars: stocks[d['code']] = {'name': d['name'], 'bars': bars, 'first_date': bars[0]['date']}
    return stocks


def calc_ma(data, w):
    ma = []; n = len(data)
    for i in range(n):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma


def generate_signals(bars, ma_fast, ma_slow, cross_thr, os_N, os_D):
    closes = [b['close'] for b in bars]; highs = [b['high'] for b in bars]
    ma_f = calc_ma(closes, ma_fast); ma_s = calc_ma(closes, ma_slow)
    n = len(bars)
    for i, b in enumerate(bars):
        b['ma_f'] = ma_f[i]; b['ma_s'] = ma_s[i]
        if math.isnan(ma_f[i]) or math.isnan(ma_s[i]) or ma_s[i]==0:
            b['dev'] = float('nan')
        else:
            b['dev'] = (ma_f[i]-ma_s[i])/abs(ma_s[i])
        if i < max(os_N, ma_slow):
            b['is_oversold'] = False; b['os_dd'] = 0.0
        else:
            peak = max(highs[i-os_N+1:i+1])
            dd = (peak - closes[i])/peak if peak > 0 else 0
            b['os_dd'] = dd; b['is_oversold'] = dd >= os_D
        b['signal_buy'] = (not math.isnan(b.get('dev', float('nan'))) and
                           b['dev'] >= cross_thr and b['is_oversold'])
    return bars


def backtest(stocks_dict, signal_dict, trail_pct):
    codes = sorted(stocks_dict.keys())
    per_pos_cap = INIT / MAX_POS
    date_maps = {c: {b['date']: b for b in signal_dict[c]} for c in codes}
    all_dates = sorted(set.union(*[set(m.keys()) for m in date_maps.values()]))
    first_dates = {c: stocks_dict[c]['bars'][0]['date'] for c in codes}
    positions = {}; cash = INIT; trades = []; dvs = []; trail_n = 0

    for d in all_dates:
        available = [c for c in codes if first_dates[c] <= d]
        # Step 1: Check trail stops
        for code in list(positions.keys()):
            bar = date_maps[code].get(d)
            if not bar: continue
            px = bar['close']; pos = positions[code]
            if px > pos['peak']: pos['peak'] = px
            if px <= pos['peak'] * (1 - trail_pct):
                sell_cash = pos['shares'] * px * (1 - STAMP - SLIPPAGE)
                pnl = sell_cash - pos['shares'] * pos['buy_px']
                trades.append({'code': code, 'buy_d': pos['entry_date'], 'sell_d': d,
                               'bp': pos['buy_px'], 'sp': px,
                               'ret': (px - pos['buy_px'])/pos['buy_px'],
                               'pnl': pnl, 'exit': 'trail'})
                trail_n += 1; cash += sell_cash; del positions[code]

        # Step 2: Fill slots
        slots = MAX_POS - len(positions)
        if slots > 0:
            candidates = []
            for code in available:
                if code in positions: continue
                bar = date_maps[code].get(d)
                if bar and bar.get('signal_buy', False) and not math.isnan(bar['dev']):
                    candidates.append((code, bar['dev']))
            candidates.sort(key=lambda x: x[1], reverse=True)
            bought = 0
            for code, dev in candidates:
                if bought >= slots or cash <= 0: break
                bar = date_maps[code][d]; px = bar['close']
                invest = min(cash/(slots-bought), per_pos_cap)
                shares = invest/(px*(1+SLIPPAGE+COMM))
                positions[code] = {'shares': shares, 'buy_px': px, 'peak': px, 'entry_date': d}
                cash -= shares*px*(1+SLIPPAGE+COMM); bought += 1

        pos_value = sum(pos['shares']*date_maps[c].get(d,{}).get('close',0)*(1-STAMP-SLIPPAGE)
                        for c, pos in positions.items() if date_maps[c].get(d))
        dvs.append({'date': d, 'value': cash+pos_value, 'n_pos': len(positions)})

    # Final
    last_d = all_dates[-1]; fin_n = 0
    for code in list(positions.keys()):
        bar = date_maps[code].get(last_d)
        if bar:
            px = bar['close']; sell_cash = positions[code]['shares']*px*(1-STAMP-SLIPPAGE)
            pnl = sell_cash-positions[code]['shares']*positions[code]['buy_px']
            trades.append({'code': code, 'buy_d': positions[code]['entry_date'], 'sell_d': last_d,
                           'bp': positions[code]['buy_px'], 'sp': px,
                           'ret': (px-positions[code]['buy_px'])/positions[code]['buy_px'],
                           'pnl': pnl, 'exit': 'final'}); fin_n += 1
            cash += sell_cash
        del positions[code]

    fv = cash; rets = []
    for i in range(1, len(dvs)):
        p, c = dvs[i-1]['value'], dvs[i]['value']
        if p>0: rets.append((c-p)/p)
    if not rets: rets = [0.0]
    pkv = dvs[0]['value']; mdd = 0.0
    for dv in dvs:
        if dv['value']>pkv: pkv=dv['value']
        dd = (pkv-dv['value'])/pkv
        if dd>mdd: mdd=dd
    tr = (fv-INIT)/INIT
    if len(rets)>1:
        mu = sum(rets)/len(rets); sd = (sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av = sd*math.sqrt(TD); ar_ = mu*TD
        sh = (ar_-RF)/av if av>0 else 0
    else: av=sh=ar_=0.0
    ar = (1+tr)**(TD/max(len(rets),1))-1 if tr>-1 else -1
    cm = ar/mdd if mdd>0 else float('inf')
    wins = sum(1 for t in trades if t['ret']>0)
    wr = wins/len(trades) if trades else 0
    return {'tr': tr, 'ar': ar, 'vol': av, 'sh': sh, 'cm': cm, 'mdd': mdd,
            'np': len(trades), 'wr': wr, 'trail_n': trail_n, 'fin_n': fin_n,
            'dvs': dvs, 'trades': trades, 'fv': fv}


def main():
    print('='*100)
    print(f'  MA金叉+超跌过滤 · 全MA组合+乖离率 · {TOTAL} 组网格')
    print(f'  MA: {MA_PAIRS}  Thr: {CROSS_THRS}  OS_N: {OS_Ns}  OS_D: {OS_Ds}  Trail: {TRAILS}')
    print('='*100)

    print('\n[DATA] Loading...')
    stocks = load_stocks()
    codes = sorted(stocks.keys())
    print(f'  {len(codes)} stocks loaded')
    per_s = INIT / MAX_POS

    # Pre-compute signals lazily
    signal_cache = {}
    def get_signals(ma_f, ma_s, thr, n_val, d_val):
        key = (ma_f, ma_s, thr, n_val, d_val)
        if key not in signal_cache:
            sigs = {}
            for c in codes:
                sigs[c] = generate_signals([dict(b) for b in stocks[c]['bars']],
                                           ma_f, ma_s, thr, n_val, d_val)
            signal_cache[key] = sigs
        return signal_cache[key]

    results = []; count = 0; best_sh = -999
    print(f'\n[GRID] Running {TOTAL} combos...')

    for ma_f, ma_s in MA_PAIRS:
        for thr in CROSS_THRS:
            for os_n in OS_Ns:
                for os_d in OS_Ds:
                    sigs = get_signals(ma_f, ma_s, thr, os_n, os_d)
                    for trail in TRAILS:
                        count += 1
                        label = f'MA{ma_f}/{ma_s} thr={thr:.0%} N={os_n} D={os_d:.0%} T={trail:.0%}'
                        r = backtest(stocks, sigs, trail)
                        r.update({'ma_f': ma_f, 'ma_s': ma_s, 'thr': thr,
                                  'os_n': os_n, 'os_d': os_d, 'trail': trail, 'label': label})
                        results.append(r)
                        if r['sh'] > best_sh: best_sh = r['sh']
                        if count % 100 == 1 or count == TOTAL:
                            print(f'  [{count:>5d}/{TOTAL}] {label:<40s} '
                                  f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% '
                                  f'DD={r["mdd"]*100:>5.2f}% Trd={r["np"]:>4d} '
                                  f'Win={r["wr"]*100:>4.0f}% BestSoFar={best_sh:.4f}')

    # ================================================================
    results.sort(key=lambda x: x['sh'], reverse=True)
    print('\n\n' + '='*120)
    print('  TOP 30 BY SHARPE')
    print('='*120)
    hdr = (f'  {"Rk":<3s} {"MA":>7s} {"Thr":>4s} {"N":>3s} {"D":>4s} {"Trail":>5s} '
           f'{"S":>7s} {"Ret":>9s} {"Ann":>7s} {"DD":>6s} {"Calmar":>7s} '
           f'{"Trd":>4s} {"Win":>5s} {"Trail#":>6s}')
    print(hdr); print(f'  {"-"*90}')
    for rank, r in enumerate(results[:30], 1):
        print(f'  {rank:<3d} MA{r["ma_f"]}/{r["ma_s"]:<3d} {r["thr"]:>4.0%} {r["os_n"]:>3d} {r["os_d"]:>4.0%} '
              f'{r["trail"]:>5.0%} {r["sh"]:>7.3f} {r["tr"]*100:>8.2f}% {r["ar"]*100:>6.2f}% '
              f'{r["mdd"]*100:>5.2f}% {r["cm"]:>7.3f} {r["np"]:>4d} {r["wr"]*100:>4.0f}% '
              f'{r["trail_n"]:>6d}')

    # ================================================================
    # Parameter sensitivity
    print('\n\n  PARAMETER SENSITIVITY (avg Sharpe):')
    for pname, pkey in [
        ('MA Pair', 'ma_f'), ('Cross Threshold', 'thr'),
        ('Oversold N', 'os_n'), ('Oversold D', 'os_d'), ('Trail', 'trail')]:
        levels = defaultdict(list)
        for r in results:
            if pkey == 'ma_f':
                v = f'{r["ma_f"]}/{r["ma_s"]}'
            else:
                v = r[pkey]
            levels[v].append(r['sh'])
        print(f'\n  {pname}:')
        for v in sorted(levels.keys(), key=lambda x: (x if isinstance(x,(int,float)) else 0)):
            avg = sum(levels[v])/len(levels[v])
            bar = '#'*max(int(avg*40),1) if avg>0 else '·'*max(int(abs(avg)*40),1)
            lbl = f'{v:.0%}' if isinstance(v, float) else str(v)
            print(f'    {lbl:>8s}  avg S={avg:>7.3f} n={len(levels[v]):>4d}  {bar}')

    # Top MA pair detail
    print(f'\n\n  MA PAIR BREAKDOWN (best Sharpe per pair):')
    for ma_f, ma_s in MA_PAIRS:
        subset = [r for r in results if r['ma_f']==ma_f and r['ma_s']==ma_s]
        if not subset: continue
        best = max(subset, key=lambda x: x['sh'])
        avg_s = sum(r['sh'] for r in subset)/len(subset)
        best_idx = results.index(best)+1
        print(f'    MA{ma_f}/{ma_s}: best S={best["sh"]:.4f} (rank #{best_idx}) '
              f'avg S={avg_s:.4f}  best params: thr={best["thr"]:.0%} '
              f'N={best["os_n"]} D={best["os_d"]:.0%} T={best["trail"]:.0%} '
              f'Ret={best["tr"]*100:.1f}% Trd={best["np"]}')

    # Signal size vs Sharpe
    print(f'\n\n  SIGNAL SIZE vs SHARPE:')
    for ma_f, ma_s in MA_PAIRS:
        for thr in CROSS_THRS:
            for os_n in OS_Ns:
                for os_d in OS_Ds:
                    sigs = signal_cache.get((ma_f, ma_s, thr, os_n, os_d))
                    if sigs:
                        buy_n = sum(sum(1 for b in sigs[c] if b.get('signal_buy',False)) for c in codes)
                        subset = [r for r in results
                                  if r['ma_f']==ma_f and r['ma_s']==ma_s and r['thr']==thr
                                  and r['os_n']==os_n and r['os_d']==os_d]
                        if subset:
                            best_s = max(r['sh'] for r in subset)
                            if best_s > 0.8:
                                print(f'    MA{ma_f}/{ma_s} thr={thr:.0%} N={os_n} D={os_d:.0%} '
                                      f'signals={buy_n:>5d} best_S={best_s:.4f}')

    # ================================================================
    # BEST detail
    best = results[0]
    print(f'\n\n  {"="*80}')
    print(f'  BEST: {best["label"]}')
    print(f'  S={best["sh"]:.4f} Ret={best["tr"]*100:.2f}% Ann={best["ar"]*100:.2f}% '
          f'DD={best["mdd"]*100:.2f}% Calmar={best["cm"]:.3f}')
    print(f'  Trades={best["np"]} Win={best["wr"]*100:.0f}% '
          f'Trail={best["trail_n"]} Final={best["fin_n"]}')

    # Top stocks
    code_pnl = defaultdict(float)
    for t in best['trades']: code_pnl[t['code']] += t['pnl']
    top = sorted(code_pnl.items(), key=lambda x: x[1], reverse=True)
    print(f'\n  Top 10 stocks:')
    for c, pnl in top[:10]:
        print(f'    {c} {stocks[c]["name"]:<10s} PnL={pnl:>12,.0f}')
    print(f'\n  Bottom 5:')
    for c, pnl in top[-5:]:
        print(f'    {c} {stocks[c]["name"]:<10s} PnL={pnl:>12,.0f}')

    # Best trades
    best['trades'].sort(key=lambda x: x['ret'], reverse=True)
    print(f'\n  Best 10 trades:')
    for t in best['trades'][:10]:
        print(f'    {t["code"]} {stocks[t["code"]]["name"]:<10s} '
              f'{t["buy_d"]}->{t["sell_d"]} {t["ret"]*100:>7.2f}% {t["exit"]}')

    # Signal count for best params
    sigs_best = signal_cache.get((best['ma_f'], best['ma_s'], best['thr'], best['os_n'], best['os_d']))
    if sigs_best:
        n_sig = sum(sum(1 for b in sigs_best[c] if b.get('signal_buy',False)) for c in codes)
        print(f'\n  Signal count: {n_sig}')

    print('\n  Done!')


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
