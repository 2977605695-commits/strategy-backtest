"""
峰岭因子 vs 市场环境: 熊市能自然减仓吗?
"""
import sys, io, os, math, json
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices, calc_ma, get_common_dates

K = 1.5; LB = 14; MIN_F = 0.8; REBAL = 21

all_s = load_prices(stock_filter=None)
stocks = {c: i for c, i in all_s.items()
          if i['dates'] and i['dates'][0] <= '20200103' and len(i['dates']) >= 1500}
cd = get_common_dates(stocks)

factor = {}
for code, info in stocks.items():
    vols = info['volume']; dates = info['dates']; n = len(vols)
    ma_vol = calc_ma(vols, 20); vals = {}
    for i in range(n):
        if i < LB or math.isnan(ma_vol[i]): continue
        w = vols[i-19:i+1]; mu = sum(w) / 20
        var = sum((v - mu) ** 2 for v in w) / 20; std = var ** 0.5
        thr = ma_vol[i] + K * std; ps = 0.0; rs = 0.0
        for j in range(max(0, i - LB + 1), i + 1):
            erupt = vols[j] >= thr
            if erupt:
                prev_erupt = (j > 0 and vols[j - 1] >= thr)
                if prev_erupt: rs += vols[j]
                else: ps += vols[j]
        vals[dates[i]] = ps / rs if rs > 0 else float('nan')
    factor[code] = vals

with open('benchmarks/sh000300.json', 'r', encoding='utf-8') as f:
    hs300 = json.load(f)
hs_map = {b['date'].replace('-', ''): b['close'] for b in hs300['bars']}

def get_px(code, dt):
    if code not in stocks: return None
    im = {d: i for i, d in enumerate(stocks[code]['dates'])}
    si = im.get(dt)
    if si is None: return None
    return si, stocks[code]['close'][si]

rebal_dates = cd[::REBAL]
regime_stats = defaultdict(lambda: {
    'avg_factor': [], 'n_candidates': [], 'n_above_08': [],
    'top5_fwd': [], 'n_dates': 0})

for idx_r, dt in enumerate(rebal_dates):
    if idx_r < 4: continue
    si_now = None
    for j, d in enumerate(cd):
        if d == dt: si_now = j; break
    if si_now is None: continue

    hs_now = hs_map.get(dt)
    hs_past = hs_map.get(cd[max(0, si_now - 63)])
    if not hs_now or not hs_past or hs_past <= 0: continue
    past_ret = (hs_now - hs_past) / hs_past

    if past_ret < -0.10: regime = 'deep_bear'
    elif past_ret < -0.05: regime = 'bear'
    elif past_ret > 0.10: regime = 'bull'
    elif past_ret > 0.05: regime = 'recovery'
    else: regime = 'sideways'

    rs = regime_stats[regime]; rs['n_dates'] += 1

    fvs = []
    for code in stocks:
        fv = factor.get(code, {}).get(dt, float('nan'))
        if not math.isnan(fv): fvs.append(fv)
    if fvs:
        rs['avg_factor'].append(sum(fvs) / len(fvs))
        rs['n_candidates'].append(len(fvs))
        rs['n_above_08'].append(sum(1 for f in fvs if f >= MIN_F))

    # Top5 21d forward return
    cand = [(c, factor.get(c, {}).get(dt, float('nan'))) for c in stocks]
    cand = [(c, s) for c, s in cand if not math.isnan(s) and s >= MIN_F]
    cand.sort(key=lambda x: x[1], reverse=True)
    top5_codes = [c for c, _ in cand[:5]]
    top5_fwds = []
    for c in top5_codes:
        res = get_px(c, dt)
        if res is None: continue
        si2, px_now = res
        si_fwd = min(si2 + 21, len(stocks[c]['dates']) - 1)
        px_fwd = stocks[c]['close'][si_fwd]
        if px_now > 0: top5_fwds.append((px_fwd - px_now) / px_now)
    if top5_fwds: rs['top5_fwd'].append(sum(top5_fwds) / len(top5_fwds))

print('=' * 85)
print('  峰岭因子 vs 市场环境')
print('  分类标准: 过去3个月HS300涨跌幅')
print('  deep_bear: <-10% | bear: -5%~-10% | sideways: -5%~+5%')
print('  recovery: +5%~+10% | bull: >+10%')
print('=' * 85)

print('\n  %-12s %5s %8s %8s %8s %8s %8s' % (
    'Regime', 'N', 'AvgF', '#Cand', '#>=0.8', 'Ratio%', 'Top5Fwd'))
print('  ' + '-' * 75)
for r_name in ['deep_bear', 'bear', 'sideways', 'recovery', 'bull']:
    s = regime_stats[r_name]
    if s['n_dates'] == 0: continue
    af = sum(s['avg_factor']) / len(s['avg_factor']) if s['avg_factor'] else 0
    ac = sum(s['n_candidates']) / len(s['n_candidates']) if s['n_candidates'] else 0
    a8 = sum(s['n_above_08']) / len(s['n_above_08']) if s['n_above_08'] else 0
    ratio = a8 / ac * 100 if ac > 0 else 0
    tf = sum(s['top5_fwd']) / len(s['top5_fwd']) * 100 if s['top5_fwd'] else 0
    print('  %-12s %4d  %7.3f %7.1f %7.1f %7.1f%% %7.2f%%' % (
        r_name, s['n_dates'], af, ac, a8, ratio, tf))

# KEY INSIGHT
print('\n' + '=' * 85)
print('  核心发现: f>=%.1f 通过率 vs 市场环境' % MIN_F)
print('=' * 85)
for r_name in ['deep_bear', 'bear', 'sideways', 'recovery', 'bull']:
    s = regime_stats[r_name]
    if s['n_dates'] == 0: continue
    ratios = [n8 / nc * 100 if nc > 0 else 0
              for n8, nc in zip(s['n_above_08'], s['n_candidates'])]
    avg_ratio = sum(ratios) / len(ratios)
    mn, mx = min(ratios), max(ratios)
    bar = '#' * int(avg_ratio / 3)
    print('  %-12s: avg=%.0f%% [%.0f~%.0f%%]  %s' % (r_name, avg_ratio, mn, mx, bar))

# Count shortage dates
print('\n' + '=' * 85)
print('  候选股不足5只的调仓日')
print('=' * 85)
low_dates = {'deep_bear': 0, 'bear': 0, 'sideways': 0, 'recovery': 0, 'bull': 0}
for r_name in regime_stats:
    s = regime_stats[r_name]
    for n8 in s['n_above_08']:
        if n8 < 5: low_dates[r_name] += 1
for r_name in ['deep_bear', 'bear', 'sideways', 'recovery', 'bull']:
    n_low = low_dates.get(r_name, 0)
    total = regime_stats[r_name]['n_dates']
    if total > 0:
        print('  %-12s: %d/%d dates (%.0f%%) 无法满仓' % (r_name, n_low, total, n_low / total * 100))

# 2020 bear market replay
print('\n' + '=' * 85)
print('  2020年COVID熊市回放')
print('=' * 85)
print('  %-12s %8s %8s %8s %s' % ('Date', 'HS300_3M', 'f>=0.8', 'Top5', 'Top5_Names'))
for idx_r, dt in enumerate(rebal_dates):
    if not dt.startswith('2020'): continue
    if idx_r < 4: continue
    si_now = next((j for j, d in enumerate(cd) if d == dt), None)
    if si_now is None: continue
    hs_now = hs_map.get(dt)
    hs_past = hs_map.get(cd[max(0, si_now - 63)])
    if not hs_now or not hs_past or hs_past <= 0: continue
    past_ret = (hs_now - hs_past) / hs_past * 100
    fvs = {c: factor.get(c, {}).get(dt, float('nan')) for c in stocks}
    above_08 = sum(1 for f in fvs.values() if not math.isnan(f) and f >= MIN_F)
    cand = [(c, fvs[c]) for c in fvs if not math.isnan(fvs[c]) and fvs[c] >= MIN_F]
    cand.sort(key=lambda x: x[1], reverse=True)
    top5_str = ','.join('%s(%.2f)' % (stocks[c]['name'][:4], s) for c, s in cand[:5])
    print('  %-12s %+7.1f%% %7d  %5d  %s' % (dt, past_ret, above_08, len(cand[:5]), top5_str[:80]))

print('\nDone!')
