"""
f>=0.8 过滤版 · 亏损深度分析
"""
import sys, io, os, math
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(r'C:\Users\26776\Desktop\strategy-backtest')
from data_loader import load_prices, calc_ma, get_common_dates

INIT = 10_000_000; RF = 0.025; TD = 252; MAX_POS = 5
SLIP = 0.003; B_FEE = 0.00025; S_FEE = 0.00025; STAX = 0.0005
K = 1.5; LB = 14; TRAIL = 0.30; REBAL = 21; MIN_F = 0.8

import csv
FUND_DIR = 'data/fundamentals_70stocks'
csvs = sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
sm = {}
with open(os.path.join(FUND_DIR, csvs[-1]), 'r', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): sm[r['code'].strip()] = r.get('sector', '').strip()

all_s = load_prices(stock_filter=None)
stocks = {c: i for c, i in all_s.items()
          if i['dates'] and i['dates'][0] <= '20200103' and len(i['dates']) >= 1500}
cd = get_common_dates(stocks)

factor = {}
for code, info in stocks.items():
    vols = info['volume']; dates = info['dates']; n = len(vols)
    ma_vol = calc_ma(vols, 20)
    vals = {}
    for i in range(n):
        if i < LB or math.isnan(ma_vol[i]): continue
        w = vols[i-19:i+1]; mu = sum(w) / 20
        var = sum((v - mu) ** 2 for v in w) / 20; std = var ** 0.5
        thr = ma_vol[i] + K * std
        ps = 0.0; rs = 0.0
        for j in range(max(0, i - LB + 1), i + 1):
            erupt = vols[j] >= thr
            if erupt:
                prev_erupt = (j > 0 and vols[j - 1] >= thr)
                if prev_erupt: rs += vols[j]
                else: ps += vols[j]
        vals[dates[i]] = ps / rs if rs > 0 else float('nan')
    factor[code] = vals

idx = {c: {d: i for i, d in enumerate(stocks[c]['dates'])} for c in stocks}

cash = INIT; pos = {}; trades = []; eq = []
for di, dt in enumerate(cd):
    for code, p in list(pos.items()):
        if code not in idx or dt not in idx[code]: continue
        px = stocks[code]['close'][idx[code][dt]]
        if px > p['peak']: p['peak'] = px
        if px <= p['peak'] * (1 - TRAIL):
            sp = px * (1 - SLIP - S_FEE - STAX); cash += p['shares'] * sp
            ret = (sp - p['bp']) / p['bp'] if p['bp'] > 0 else 0
            dd_path = []
            for k in range(p['bi'], di + 1):
                dk = cd[k]; pxk = stocks[code]['close'][idx[code][dk]]
                dd_path.append((pxk - p['bp']) / p['bp'] * 100)
            trades.append({
                'code': code, 'name': stocks[code]['name'],
                'bd': p['bd'], 'sd': dt, 'ret': ret, 'exit': 'trail',
                'sector': sm.get(code, ''), 'entry_factor': p.get('entry_factor', float('nan')),
                'peak_ret': (p['peak'] - p['bp']) / p['bp'] * 100 if p['bp'] > 0 else 0,
                'hold': di - p['bi'], 'dd_path': dd_path,
                'nav_at_entry': p.get('nav_at_entry', INIT)})
            del pos[code]

    if di % REBAL == 0:
        cand = [(c, factor.get(c, {}).get(dt, float('nan'))) for c in stocks]
        cand = [(c, s) for c, s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
        # KEY: f>=0.8 filter
        cand = [(c, s) for c, s in cand if s >= MIN_F]
        cand.sort(key=lambda x: x[1], reverse=True)

        selected = []; sel_secs = set()
        for c, s in cand:
            sec = sm.get(c, '')
            if sec and sec in sel_secs: continue
            if len(selected) >= MAX_POS: break
            selected.append((c, s)); sel_secs.add(sec)
        top_codes = set(c for c, _ in selected)

        pv_curr = sum(p['shares'] * stocks[c]['close'][idx[c][dt]]
                      for c, p in pos.items() if c in idx and dt in idx[c])
        total_nav = cash + pv_curr

        for code in list(pos.keys()):
            if code not in top_codes:
                px = stocks[code]['close'][idx[code][dt]]; sp = px * (1 - SLIP - S_FEE - STAX)
                cash += pos[code]['shares'] * sp
                ret = (sp - pos[code]['bp']) / pos[code]['bp'] if pos[code]['bp'] > 0 else 0
                trades.append({
                    'code': code, 'name': stocks[code]['name'],
                    'bd': pos[code]['bd'], 'sd': dt, 'ret': ret, 'exit': 'rebal',
                    'sector': sm.get(code, ''), 'entry_factor': pos[code].get('entry_factor', float('nan')),
                    'peak_ret': (pos[code]['peak'] - pos[code]['bp']) / pos[code]['bp'] * 100 if pos[code]['bp'] > 0 else 0,
                    'hold': di - pos[code]['bi'],
                    'nav_at_entry': pos[code].get('nav_at_entry', INIT)})
                del pos[code]

        pv_curr = sum(p['shares'] * stocks[c]['close'][idx[c][dt]]
                      for c, p in pos.items() if c in idx and dt in idx[c])
        total_nav = cash + pv_curr
        target_val = total_nav / len(selected) if selected else 0

        for code, score in selected:
            if code in pos: continue
            if cash < target_val * 0.99: break
            buy_val = min(target_val, cash)
            if buy_val <= 0: break
            raw = stocks[code]['close'][idx[code][dt]]; bp = raw * (1 + SLIP + B_FEE)
            if bp > 0 and buy_val > bp * 0.01:
                sh = buy_val / bp; cash -= buy_val
                pos[code] = {'shares': sh, 'bp': bp, 'peak': raw, 'bd': dt, 'bi': di,
                             'nav_at_entry': total_nav, 'entry_factor': score, 'peak_day': di}

    cash *= (1 + RF / TD)
    pv2 = sum(p['shares'] * stocks[c]['close'][idx[c][dt]] for c, p in pos.items() if c in idx and dt in idx[c])
    eq.append(cash + pv2)

ld = cd[-1]
for code, p in list(pos.items()):
    if code in idx and ld in idx[code]:
        px = stocks[code]['close'][idx[code][ld]]; sp = px * (1 - SLIP - S_FEE - STAX)
        cash += p['shares'] * sp
        ret = (sp - p['bp']) / p['bp'] if p['bp'] > 0 else 0
        trades.append({
            'code': code, 'name': stocks[code]['name'],
            'bd': p['bd'], 'sd': ld, 'ret': ret, 'exit': 'final',
            'sector': sm.get(code, ''), 'entry_factor': p.get('entry_factor', float('nan')),
            'peak_ret': (p['peak'] - p['bp']) / p['bp'] * 100 if p['bp'] > 0 else 0,
            'hold': len(cd) - 1 - p['bi'],
            'nav_at_entry': p.get('nav_at_entry', INIT)})

losers = [t for t in trades if t['ret'] < 0]
winners = [t for t in trades if t['ret'] > 0]

print('=' * 85)
print(' f>=%.1f 过滤版 · 亏损深度分析' % MIN_F)
print('=' * 85)
print(' Total: %d trades | Losers: %d (%.0f%%) | Winners: %d (%.0f%%)' % (
    len(trades), len(losers), len(losers) / len(trades) * 100,
    len(winners), len(winners) / len(trades) * 100))
print(' Final: %.1f%% | MaxDD: %.1f%%' % (
    (eq[-1] - eq[0]) / eq[0] * 100,
    max((max(eq[:i+1]) - eq[i]) / max(eq[:i+1]) * 100 for i in range(len(eq)))))

# 1. 亏损分布
print('\n' + '-' * 85)
print(' 1. 亏损大小分布（vs 无过滤版对比）')
print('-' * 85)
losses_pct = sorted([t['ret'] * 100 for t in losers])
buckets = [(-100, -25), (-25, -20), (-20, -15), (-15, -10), (-10, -5), (-5, 0)]
for lo, hi in buckets:
    cnt = sum(1 for l in losses_pct if l >= lo and l < hi)
    bar = '█' * cnt if cnt > 0 else ''
    if cnt > 0:
        avg = sum(l for l in losses_pct if l >= lo and l < hi) / cnt
        print('  [%+3d%%,%+3d%%): %3d trades  avg=%+.1f%%  %s' % (lo, hi, cnt, avg, bar))
print('  Worst loss: %.1f%%' % losses_pct[0])

# 2. 时间分布
print('\n' + '-' * 85)
print(' 2. 亏损按年度')
print('-' * 85)
for y in ['2020', '2021', '2022', '2023', '2024', '2025', '2026']:
    sub = [t for t in losers if t['bd'][:4] == y]
    sub_w = [t for t in winners if t['bd'][:4] == y]
    if sub:
        avg = sum(t['ret'] for t in sub) / len(sub) * 100
        worst_r = min(t['ret'] for t in sub) * 100
        print('  %s: %2d losses / %2d wins  avg=%.1f%%  worst=%.1f%%  loss_rate=%.0f%%' % (
            y, len(sub), len(sub_w), avg, worst_r,
            len(sub) / (len(sub) + len(sub_w)) * 100))

# 3. 退出方式
print('\n' + '-' * 85)
print(' 3. 亏损按退出方式')
print('-' * 85)
for exit_type in sorted(set(t['exit'] for t in losers)):
    sub = [t for t in losers if t['exit'] == exit_type]
    sub_w = [t for t in winners if t['exit'] == exit_type]
    avg = sum(t['ret'] for t in sub) / len(sub) * 100
    print('  %-12s: %3d losses / %3d wins  avg=%.1f%%  worst=%.1f%%  loss_rate=%.0f%%' % (
        exit_type, len(sub), len(sub_w), avg, min(t['ret'] for t in sub) * 100,
        len(sub) / (len(sub) + len(sub_w)) * 100 if (len(sub) + len(sub_w)) > 0 else 0))

# 4. 赛道
print('\n' + '-' * 85)
print(' 4. 亏损集中的赛道（>=3笔）')
print('-' * 85)
sec_stats = defaultdict(lambda: {'loss': 0, 'win': 0, 'total_loss': 0.0})
for t in trades:
    s = t['sector']
    if t['ret'] < 0:
        sec_stats[s]['loss'] += 1; sec_stats[s]['total_loss'] += t['ret']
    else:
        sec_stats[s]['win'] += 1
for s in sorted(sec_stats, key=lambda x: sec_stats[x]['loss'] * sec_stats[x]['total_loss'], reverse=True):
    d = sec_stats[s]
    if d['loss'] >= 3:
        total = d['loss'] + d['win']; loss_rate = d['loss'] / total * 100
        avg = d['total_loss'] / d['loss'] * 100
        print('  %-30s %3dL/%2dW  loss_rate=%.0f%%  avg_loss=%.1f%%  total_loss=%.1f%%' % (
            s, d['loss'], d['win'], loss_rate, avg, d['total_loss'] * 100))

# 5. 曾经盈利
print('\n' + '-' * 85)
print(' 5. 曾经浮盈但最终亏损')
print('-' * 85)
crashed = [t for t in losers if t.get('peak_ret', 0) > 0]
print('  %d/%d losing trades (%.0f%%) ever went positive' % (
    len(crashed), len(losers), len(crashed) / len(losers) * 100))
for lo, hi in [(0, 5), (5, 10), (10, 20), (20, 50), (50, 200)]:
    sub = [t for t in crashed if lo <= t.get('peak_ret', 0) < hi]
    if sub:
        avg_final = sum(t['ret'] for t in sub) / len(sub) * 100
        avg_peak = sum(t.get('peak_ret', 0) for t in sub) / len(sub)
        print('  Peak +(%2d~%3d)%%: %2d trades  avg peak=%.1f%%  avg final=%.1f%%  drop=%.1f%%' % (
            lo, hi, len(sub), avg_peak, avg_final, avg_peak - avg_final))

# 6. 持仓天数
print('\n' + '-' * 85)
print(' 6. 亏损按持仓天数')
print('-' * 85)
for lo, hi in [(0, 10), (10, 21), (21, 42), (42, 100)]:
    sub = [t for t in losers if lo <= t['hold'] < hi]
    sub_w = [t for t in winners if lo <= t['hold'] < hi]
    if sub:
        avg = sum(t['ret'] for t in sub) / len(sub) * 100
        loss_rate = len(sub) / (len(sub) + len(sub_w)) * 100 if (len(sub) + len(sub_w)) > 0 else 0
        print('  %2d~%3dd: %3d losses / %3d wins  avg=%.1f%%  loss_rate=%.0f%%' % (
            lo, hi, len(sub), len(sub_w), avg, loss_rate))

# 7. 同日多亏
print('\n' + '-' * 85)
print(' 7. 同日多笔亏损')
print('-' * 85)
losers.sort(key=lambda x: x['bd'])
date_losses = defaultdict(list)
for t in losers: date_losses[t['bd']].append(t)
clusters = [(d, tl) for d, tl in date_losses.items() if len(tl) >= 2]
clusters.sort(key=lambda x: len(x[1]), reverse=True)
print('  %d dates with >=2 simultaneous losing exits' % len(clusters))
for d, cl in clusters[:8]:
    total = sum(t['ret'] for t in cl) * 100
    print('  %s: %d trades  total_loss=%.1f%%  %s' % (
        d, len(cl), total,
        ', '.join('%s(%.1f%%)' % (t['name'][:6], t['ret'] * 100) for t in cl)))

# 8. 入场因子值分布
print('\n' + '-' * 85)
print(' 8. 入场因子值（亏损 vs 盈利）')
print('-' * 85)
for tag, data in [('Losers', losers), ('Winners', winners)]:
    fvs = [t['entry_factor'] for t in data if not math.isnan(t.get('entry_factor', float('nan')))]
    if fvs:
        fvs.sort(); n = len(fvs)
        print('  %-8s: min=%.2f p10=%.2f med=%.2f p90=%.2f max=%.2f' % (
            tag, fvs[0], fvs[n // 10], fvs[n // 2], fvs[n * 9 // 10], fvs[-1]))

# 9. 被过滤掉的最惨亏损（如果无过滤，这些也会进）
# Can't show without comparison - but show what we know
print('\n' + '-' * 85)
print(' 9. f>=0.8 过滤效果: 排除了因子值<0.8的弱信号交易')
print('-' * 85)
# Count how many trades had entry_factor between 0 and 0.8 in the no-filter version
# We can approximate: losers with low factor
low_f_losers = [t for t in losers if t.get('entry_factor', 99) < 0.9]
print('  当前loser中因子<0.9的有: %d 笔（它们侥幸通过了0.8的阈值）' % len(low_f_losers))
if low_f_losers:
    avg = sum(t['ret'] for t in low_f_losers) / len(low_f_losers) * 100
    print('  avg loss: %.1f%%' % avg)

# 10. 全部亏损列表
print('\n' + '-' * 85)
print(' 10. 全部亏损交易（最严重30笔）')
print('-' * 85)
losers.sort(key=lambda x: x['ret'])
print('  %3s %-10s %-20s %-12s %-12s %7s %7s %4s %s' % (
    '#', 'Stock', 'Sector', 'Buy', 'Sell', 'Ret', 'Peak', 'Hold', 'Exit'))
print('  ' + '-' * 78)
for i, t in enumerate(losers[:30], 1):
    print('  %3d %-10s %-20s %-12s %-12s %+6.1f%% %+6.1f%% %4dd %s' % (
        i, t['name'][:10], t['sector'][:20], t['bd'], t['sd'],
        t['ret'] * 100, t.get('peak_ret', 0), t['hold'], t['exit']))

print('\nDone!')
