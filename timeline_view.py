"""Print trade timeline for best ETF rotation strategy."""
import json, os, sys, io, math
from collections import defaultdict, OrderedDict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
START = '2020-01-01'; END = '2026-07-28'
RF = 0.025; TD = 252; INIT = 10_000_000; MAX_POS = 2
ETF_CODES = ['159782','588380','588870','588080','588300','518800','589720','588890','588170']
TRAIL = 0.05; STRAT = 'MA5>MA20+MA20斜率正'

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

def calc_slope(ma_series, lb=5):
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

# Load
etfs = {}
for code in ETF_CODES:
    e = load_etf(code)
    if e: etfs[code] = e

# Compute signals
all_sigs = {}
for code in ETF_CODES:
    if code not in etfs: continue
    bars = etfs[code]['bars']; closes = [b['close'] for b in bars]; n = len(bars)
    mas = {w: calc_ma(closes, w) for w in [5, 20]}
    ma20_slope = calc_slope(mas[20], 10)
    dates = [b['date'] for b in bars]
    sigs = {'trend': {}, 'ratio': {}, 'px': {}}
    for i in range(n):
        d = dates[i]; px = closes[i]
        ms20_ok = not math.isnan(ma20_slope[i]) and ma20_slope[i] > 0
        if not math.isnan(mas[5][i]) and not math.isnan(mas[20][i]) and mas[20][i] > 0:
            trend = mas[5][i] > mas[20][i] and ms20_ok
            ratio = mas[5][i] / mas[20][i]
        else:
            trend = False; ratio = 1.0
        sigs['trend'][d] = trend; sigs['ratio'][d] = ratio; sigs['px'][d] = px
    all_sigs[code] = sigs

# Backtest
codes = sorted([c for c in ETF_CODES if c in etfs])
dm = {c: {b['date']: b for b in etfs[c]['bars']} for c in codes}
first_dates = {c: etfs[c]['first_date'] for c in codes}
all_dates_set = set()
for c in codes: all_dates_set.update(dm[c].keys())
all_dates = sorted(all_dates_set)

cash = INIT; positions = {}; trades = []; dvs = []; per_slot = INIT / MAX_POS

for d in all_dates:
    available = [c for c in codes if first_dates[c] <= d]

    # Check exits
    for c in list(positions.keys()):
        bar = dm[c].get(d)
        if not bar: continue
        px = bar['close']; pos = positions[c]
        if px > pos['peak']: pos['peak'] = px
        trend_on = all_sigs[c]['trend'].get(d, False)
        exit_reason = None
        if px <= pos['peak'] * (1 - TRAIL): exit_reason = 'trail'
        elif not trend_on: exit_reason = 'trend_off'
        if exit_reason:
            sell_val = pos['shares'] * px; pnl = sell_val - pos['shares'] * pos['bp']
            trades.append({
                'code': c, 'bd': pos['entry_d'], 'sd': d,
                'bp': pos['bp'], 'sp': px,
                'ret': (px - pos['bp']) / pos['bp'],
                'pnl': pnl, 'exit': exit_reason,
            })
            cash += sell_val; del positions[c]

    # Fill slots
    slots = MAX_POS - len(positions)
    if slots > 0 and cash > 0:
        candidates = []
        for c in available:
            if c in positions: continue
            trend_on = all_sigs[c]['trend'].get(d, False)
            if trend_on:
                candidates.append((c, all_sigs[c]['ratio'].get(d, 1.0), all_sigs[c]['px'].get(d, 0)))
        candidates.sort(key=lambda x: x[1], reverse=True)
        for c, ratio, px in candidates:
            if len(positions) >= MAX_POS or cash <= 0: break
            invest = min(cash, per_slot)
            if invest <= 0: continue
            shares = invest / px
            positions[c] = {'shares': shares, 'bp': px, 'peak': px, 'entry_d': d, 'ratio': ratio}
            cash -= invest

    pos_val = sum(pos['shares'] * dm[c].get(d, {}).get('close', 0) for c, pos in positions.items() if dm[c].get(d))
    dvs.append({'date': d, 'value': cash + pos_val, 'n_pos': len(positions), 'holding': list(positions.keys())})

# Final
ld = all_dates[-1]
for c in list(positions.keys()):
    bar = dm[c].get(ld)
    if bar:
        px = bar['close']; sell_val = positions[c]['shares'] * px
        pnl = sell_val - positions[c]['shares'] * positions[c]['bp']
        trades.append({
            'code': c, 'bd': positions[c]['entry_d'], 'sd': ld,
            'bp': positions[c]['bp'], 'sp': px,
            'ret': (px - positions[c]['bp']) / positions[c]['bp'],
            'pnl': pnl, 'exit': 'final',
        })
        cash += sell_val; del positions[c]

# ================================================================
trades.sort(key=lambda x: x['bd'])
from datetime import datetime

print('=' * 130)
print('  ETF趋势轮动 · MA5>MA20+MA20斜率正 · Trail=5% · 完整操作流程')
print('=' * 130)
print(f'  {"#":<4s} {"买入日":<12s} {"卖出日":<12s} {"代码":<8s} {"ETF":<18s} {"买入@":>8s} {"卖出@":>8s} {"收益":>8s} {"退出":>10s} {"天":>4s} {"累计PnL":>12s}')
print(f'  {"-" * 115}')

cum_pnl = 0
for i, t in enumerate(trades, 1):
    bd_d = datetime.strptime(t['bd'], '%Y-%m-%d')
    sd_d = datetime.strptime(t['sd'], '%Y-%m-%d')
    hd = (sd_d - bd_d).days
    cum_pnl += t['pnl']
    name = etfs[t['code']]['name'] if t['code'] in etfs else '?'
    exit_ch = {'trail': 'Trail止损', 'trend_off': '趋势转空', 'final': '期末平仓'}.get(t['exit'], t['exit'])
    print(f'  {i:<4d} {t["bd"]:<12s} {t["sd"]:<12s} {t["code"]:<8s} {name:<18s} '
          f'{t["bp"]:>8.4f} {t["sp"]:>8.4f} {t["ret"]*100:>7.2f}% {exit_ch:<10s} {hd:>4d} {cum_pnl:>11,.0f}')

# Summary
rets = []
for i in range(1, len(dvs)):
    p, c = dvs[i-1]['value'], dvs[i]['value']
    if p > 0: rets.append((c - p) / p)
tr = (dvs[-1]['value'] - INIT) / INIT
mu = sum(rets) / len(rets); sd = (sum((r - mu)**2 for r in rets) / (len(rets) - 1))**0.5
av = sd * math.sqrt(TD); sh = (mu * TD - RF) / av if av > 0 else 0
ar = (1 + tr)**(TD / len(rets)) - 1
pkv = dvs[0]['value']; mdd = 0.0
for dv in dvs:
    if dv['value'] > pkv: pkv = dv['value']
    dd = (pkv - dv['value']) / pkv
    if dd > mdd: mdd = dd

sell_tr = [t for t in trades if t['exit'] in ('trail', 'trend_off', 'final')]
wins = sum(1 for t in sell_tr if t['ret'] > 0)

print(f'\n\n{"=" * 60}')
print(f'  绩效汇总')
print(f'{"=" * 60}')
print(f'  夏普={sh:.4f}  总收益={tr*100:.2f}%  年化={ar*100:.2f}%  最大回撤={mdd*100:.2f}%')
print(f'  交易={len(sell_tr)}笔  胜率={wins}/{len(sell_tr)}={wins/len(sell_tr)*100:.1f}%')
print(f'  Trail退出={sum(1 for t in sell_tr if t["exit"]=="trail")}  '
      f'趋势转空={sum(1 for t in sell_tr if t["exit"]=="trend_off")}  '
      f'期末={sum(1 for t in sell_tr if t["exit"]=="final")}')

# Yearly
print(f'\n  年度表现:')
from collections import defaultdict
yr = defaultdict(lambda: {'trades': 0, 'pnl': 0.0})
for t in trades:
    y = t['bd'][:4]
    yr[y]['trades'] += 1
    yr[y]['pnl'] += t['pnl']
for y in sorted(yr):
    print(f'    {y}: {yr[y]["trades"]:>3d}笔  净PnL={yr[y]["pnl"]/INIT*100:>6.2f}%')

# Operation flow summary
print(f'\n\n{"=" * 60}')
print(f'  操作流程总结')
print(f'{"=" * 60}')
print(f'''
  每日流程:
  1. 检查持仓是否触发 Trail 5% 止损 → 是则卖出
  2. 检查持仓趋势是否转空 (MA5<=MA20 或 MA20斜率<=0) → 是则卖出
  3. 卖出后的资金 + 剩余现金 → 选 MA5/MA20 比值最高的 2 只 (趋势需满足)
  4. 等权买入, 各 50%

  触发条件:
  - 买入:   MA5 > MA20 且 MA20 10日斜率 > 0
  - 卖出1:  价格 <= 持仓最高价 × 0.95
  - 卖出2:  趋势条件不满足
  - 选股:   MA5/MA20 比值降序 (买趋势最强的)

  首次建仓: 2021-05-18  买入 科创50易方达 + 黄金ETF
  最终清仓: 2026-07-28  (期末平仓)
''')

# Holding timeline summary
print(f'\n  持仓切换时间线:')
prev = set()
switches = []
for dv in dvs:
    cur = tuple(sorted(dv['holding']))
    if cur != prev:
        names = [etfs[c]['name'] if c in etfs else c for c in dv['holding']]
        switches.append((dv['date'], names, dv['value']))
        prev = cur

for i, (date, names, val) in enumerate(switches):
    if i > 0:
        prev_val = switches[i-1][2]
        note = f' (NAV变化: {(val-prev_val)/prev_val*100:+.2f}%)'
    else:
        note = f' (NAV={val/INIT*100:.1f}%)'
    print(f'  {date}: [{", ".join(names) if names else "空仓"}]{note}')
