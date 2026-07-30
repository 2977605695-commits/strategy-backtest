"""
ETF V2 (MA7/14 slope14) · 自适应Trail阶梯扫参
============================================
Fixed strategy, sweep trail tiers only.
"""

import json, os, sys, io, math
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
START = '2020-01-01'; END = '2026-07-28'
RF = 0.025; TD = 252; INIT = 10_000_000; MAX_POS = 2
ETF_CODES = ['159782','588380','588870','588080','588300','518800','589720','588890','588170']

def load_etf(code):
    path = os.path.join(DATA_DIR, f'etf_{code}.json')
    if not os.path.exists(path): return None
    d = json.load(open(path, encoding='utf-8'))
    bars = []
    for b in d['bars']:
        dt = b['date']
        if len(dt) == 8: dt = dt[:4] + '-' + dt[4:6] + '-' + dt[6:8]
        if START <= dt <= END:
            bars.append({'date': dt, 'close': float(b['close'])})
    return {'name': d['name'], 'first_date': d['first_date'], 'bars': bars}

def calc_ma(data, w):
    ma = []; n = len(data)
    for i in range(n):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1]) / w)
    return ma

def calc_slope(ma_series, lb):
    slopes = [float('nan')] * len(ma_series)
    for i in range(len(ma_series)):
        if i < lb: continue
        ys = ma_series[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys): continue
        n = len(ys); sx = sy = sxy = sxx = 0
        for j, y in enumerate(ys): sx += j; sy += y; sxy += j*y; sxx += j*j
        denom = n*sxx - sx*sx
        if denom > 0: slopes[i] = (n*sxy - sx*sy) / denom / ma_series[i] if ma_series[i] > 0 else 0
    return slopes

# Pre-load and compute signals once
print('Loading...')
etfs = {}
for code in ETF_CODES:
    e = load_etf(code)
    if e: etfs[code] = e

all_sigs = {}
for code in ETF_CODES:
    if code not in etfs: continue
    bars = etfs[code]['bars']; closes = [b['close'] for b in bars]; n = len(bars)
    ma7 = calc_ma(closes, 7); ma14 = calc_ma(closes, 14); ma14_s = calc_ma(closes, 14)
    slopes = calc_slope(ma14_s, 7)
    dates = [b['date'] for b in bars]
    sigs = {'trend': {}, 'ratio': {}}
    for i in range(n):
        d = dates[i]
        if not math.isnan(ma7[i]) and not math.isnan(ma14[i]) and ma14[i] > 0:
            slope_ok = not math.isnan(slopes[i]) and slopes[i] > 0
            sigs['trend'][d] = ma7[i] > ma14[i] and slope_ok
            sigs['ratio'][d] = ma7[i] / ma14[i]
        else:
            sigs['trend'][d] = False; sigs['ratio'][d] = 1.0
    all_sigs[code] = sigs

codes = [c for c in ETF_CODES if c in etfs]
dm = {c: {b['date']: b for b in etfs[c]['bars']} for c in codes}
first_dates = {c: etfs[c]['first_date'] for c in codes}
all_dates = set()
for c in codes: all_dates.update(dm[c].keys())
all_dates = sorted(all_dates)

def get_trail(gain, tiers):
    """tiers = [(threshold, trail), ...] sorted ascending. Returns trail for current gain."""
    best = tiers[0][1]  # default = first tier's trail
    for thresh, trail in tiers:
        if gain >= thresh:
            best = trail
    return best

def backtest(tiers):
    cash = INIT; positions = {}; trades = []; dvs = []; per_slot = INIT / MAX_POS
    trail_n = 0; trend_n = 0; final_n = 0
    for d in all_dates:
        available = [c for c in codes if first_dates[c] <= d]
        for c in list(positions.keys()):
            bar = dm[c].get(d)
            if not bar: continue
            px = bar['close']; pos = positions[c]
            if px > pos['peak']: pos['peak'] = px
            gain = (px - pos['bp']) / pos['bp'] if pos['bp'] > 0 else 0
            cur_trail = get_trail(gain, tiers)
            trend_on = all_sigs[c]['trend'].get(d, False)
            exit_reason = None
            if px <= pos['peak'] * (1 - cur_trail):
                exit_reason = 'trail'; trail_n += 1
            elif not trend_on:
                exit_reason = 'trend_off'; trend_n += 1
            if exit_reason:
                sell_val = pos['shares'] * px
                pnl = sell_val - pos['shares'] * pos['bp']
                trades.append({'code': c, 'bd': pos['entry_d'], 'sd': d,
                               'bp': pos['bp'], 'sp': px,
                               'ret': (px - pos['bp']) / pos['bp'],
                               'pnl': pnl, 'exit': exit_reason})
                cash += sell_val; del positions[c]
        slots = MAX_POS - len(positions)
        if slots > 0 and cash > 0:
            candidates = []
            for c in available:
                if c in positions: continue
                trend_on = all_sigs[c]['trend'].get(d, False)
                if trend_on:
                    bar = dm[c].get(d)
                    candidates.append((c, all_sigs[c]['ratio'].get(d, 1.0), bar['close'] if bar else 0))
            candidates.sort(key=lambda x: x[1], reverse=True)
            for c, ratio, px in candidates:
                if len(positions) >= MAX_POS or cash <= 0: break
                invest = min(cash, per_slot)
                if invest <= 0: continue
                shares = invest / px
                positions[c] = {'shares': shares, 'bp': px, 'peak': px, 'entry_d': d}
                cash -= invest
        pos_val = sum(pos['shares'] * dm[c].get(d, {}).get('close', 0)
                      for c, pos in positions.items() if dm[c].get(d))
        dvs.append({'date': d, 'value': cash + pos_val})
    ld = all_dates[-1]
    for c in list(positions.keys()):
        bar = dm[c].get(ld)
        if bar:
            px = bar['close']
            sell_val = positions[c]['shares'] * px
            pnl = sell_val - positions[c]['shares'] * positions[c]['bp']
            trades.append({'code': c, 'bd': positions[c]['entry_d'], 'sd': ld,
                           'bp': positions[c]['bp'], 'sp': px,
                           'ret': (px - positions[c]['bp']) / positions[c]['bp'],
                           'pnl': pnl, 'exit': 'final'})
            final_n += 1; cash += sell_val
        del positions[c]
    fv = cash; rets = []
    for i in range(1, len(dvs)):
        p, c = dvs[i-1]['value'], dvs[i]['value']
        if p > 0: rets.append((c - p) / p)
    if not rets: rets = [0.0]
    pkv = dvs[0]['value']; mdd = 0.0
    for dv in dvs:
        if dv['value'] > pkv: pkv = dv['value']
        dd = (pkv - dv['value']) / pkv
        if dd > mdd: mdd = dd
    tr = (fv - INIT) / INIT
    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu)**2 for r in rets) / (len(rets) - 1))**0.5
        av = sd * math.sqrt(TD); ar_ = mu * TD
        sh = (ar_ - RF) / av if av > 0 else 0
    else: av = sh = ar_ = 0.0
    sell_tr = [t for t in trades if t['exit'] in ('trail', 'trend_off', 'final')]
    wins = sum(1 for t in sell_tr if t['ret'] > 0)
    wr = wins / len(sell_tr) if sell_tr else 0
    return {'sh': sh, 'tr': tr, 'mdd': mdd, 'np': len(sell_tr), 'wr': wr,
            'trail_n': trail_n, 'trend_n': trend_n}

# Define tier configurations to test
TIER_CONFIGS = [
    # (label, [(gain_threshold, trail_pct), ...])

    # === Flat baselines ===
    ('Flat5%',  [(0.0, 0.05)]),
    ('Flat10%', [(0.0, 0.10)]),
    ('Flat15%', [(0.0, 0.15)]),
    ('Flat20%', [(0.0, 0.20)]),

    # === Original V2 tiers ===
    ('V2原版',   [(0.0,0.15), (0.15,0.05), (0.30,0.10), (0.50,0.15), (1.00,0.20), (1.50,0.10)]),

    # === Wide start, tighten as profit grows ===
    ('宽→紧 A',  [(0.0,0.20), (0.20,0.15), (0.50,0.10), (1.00,0.05)]),
    ('宽→紧 B',  [(0.0,0.25), (0.20,0.15), (0.50,0.10), (1.00,0.05)]),
    ('宽→紧 C',  [(0.0,0.30), (0.30,0.20), (0.60,0.10), (1.00,0.05)]),

    # === Tight always ===
    ('紧→更紧',  [(0.0,0.10), (0.15,0.07), (0.30,0.05), (0.50,0.03)]),

    # === Moderate tiers ===
    ('中等 A',   [(0.0,0.15), (0.30,0.10), (0.60,0.07), (1.00,0.05)]),
    ('中等 B',   [(0.0,0.15), (0.20,0.12), (0.40,0.10), (0.80,0.08), (1.20,0.05)]),
    ('中等 C',   [(0.0,0.15), (0.25,0.12), (0.50,0.10), (0.80,0.07), (1.20,0.05)]),

    # === Wide always ===
    ('宽→不变',  [(0.0,0.20), (0.30,0.20), (0.60,0.15), (1.00,0.15)]),
    ('超宽',     [(0.0,0.30), (0.50,0.25), (1.00,0.20)]),

    # === Reverse: tight start, widen for moonshots ===
    ('紧→宽',    [(0.0,0.10), (0.30,0.15), (0.60,0.20), (1.00,0.25)]),

    # === Few-step ===
    ('两步 A',   [(0.0,0.15), (0.50,0.10)]),
    ('两步 B',   [(0.0,0.20), (0.50,0.10)]),
    ('三步 A',   [(0.0,0.20), (0.30,0.12), (0.60,0.07)]),
    ('三步 B',   [(0.0,0.15), (0.40,0.10), (0.80,0.05)]),
]

print(f'\nTesting {len(TIER_CONFIGS)} tier configs...')
results = []

for label, tiers in TIER_CONFIGS:
    r = backtest(tiers)
    r.update({'label': label, 'tiers': tiers})
    results.append(r)
    tier_str = ' | '.join(f'{t:.0%}→{v:.0%}' for t,v in tiers)
    print(f'  {label:<12s} [{tier_str:<50s}] '
          f'S={r["sh"]:>7.3f} Ret={r["tr"]*100:>7.2f}% DD={r["mdd"]*100:>5.2f}% '
          f'Trd={r["np"]:>4d} Win={r["wr"]*100:>4.0f}% T={r["trail_n"]:>3d} TO={r["trend_n"]:>3d}')

results.sort(key=lambda x: x['sh'], reverse=True)

print('\n\n' + '='*120)
print('  RANKING: 自适应Trail阶梯扫参 (MA7/14 slope14)')
print('='*120)
print(f'  {"Rk":<3s} {"配置":<12s} {"阶梯规则":<50s} '
      f'{"S":>7s} {"Ret":>8s} {"DD":>6s} {"Trd":>4s} {"Win":>5s} {"T":>4s} {"TO":>4s}')
print(f'  {"-"*100}')
for rank, r in enumerate(results, 1):
    tier_str = ' | '.join(f'{t:.0%}→{v:.0%}' for t,v in r['tiers'])
    print(f'  {rank:<3d} {r["label"]:<12s} {tier_str:<50s} '
          f'{r["sh"]:>7.3f} {r["tr"]*100:>7.2f}% {r["mdd"]*100:>5.2f}% '
          f'{r["np"]:>4d} {r["wr"]*100:>4.0f}% {r["trail_n"]:>4d} {r["trend_n"]:>4d}')

print('\n  Done!')
