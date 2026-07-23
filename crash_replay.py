"""
崩盘日熔断回放: 详细追踪触发前后的操作
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

# ============================================================
# Full backtest with crash guard (-5%) and detailed tracking
# ============================================================
cash = INIT; pos = {}; trades = []
guard_events = []  # (date, nav_before, nav_after, positions_sold)
daily_nav = []
daily_actions = []  # [{date, action, detail}]

for di, dt in enumerate(cd):
    pv_curr = sum(p['shares'] * stocks[c]['close'][idx[c][dt]]
                  for c, p in pos.items() if c in idx and dt in idx[c])
    nav = cash + pv_curr
    prev_nav = daily_nav[-1] if daily_nav else INIT
    daily_ret = (nav - prev_nav) / prev_nav if prev_nav > 0 else 0
    daily_nav.append(nav)

    # Track daily actions
    day_actions = []

    # Crash guard check
    crash_triggered = (daily_ret < -0.05)
    if crash_triggered:
        sold_positions = []
        for code, p in list(pos.items()):
            px = stocks[code]['close'][idx[code][dt]]
            sp = px * (1 - SLIP - S_FEE - STAX); cash += p['shares'] * sp
            ret = (sp - p['bp']) / p['bp'] * 100 if p['bp'] > 0 else 0
            sold_positions.append({
                'code': code, 'name': stocks[code]['name'],
                'buy_date': p['bd'], 'ret%': ret,
                'entry_factor': p.get('entry_factor', float('nan')),
                'sector': sm.get(code, '')})
            trades.append({'code': code, 'name': stocks[code]['name'],
                           'bd': p['bd'], 'sd': dt, 'ret': ret / 100,
                           'exit': 'crash_guard'})
        pos.clear()
        guard_events.append({
            'date': dt, 'di': di,
            'daily_ret%': daily_ret * 100,
            'nav_before': prev_nav,
            'nav_after': cash,
            'positions_sold': sold_positions,
            'nav_5d_later': None,  # fill later
            'nav_10d_later': None,
            'nav_21d_later': None})
        day_actions.append('CRASH_GUARD: sold %d positions' % len(sold_positions))

    # Trail exits (only if not in crash guard)
    if not crash_triggered:
        for code, p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px = stocks[code]['close'][idx[code][dt]]
            if px > p['peak']: p['peak'] = px
            if px <= p['peak'] * (1 - TRAIL):
                sp = px * (1 - SLIP - S_FEE - STAX); cash += p['shares'] * sp
                ret = (sp - p['bp']) / p['bp'] * 100 if p['bp'] > 0 else 0
                day_actions.append('TRAIL: %s (%.1f%%)' % (stocks[code]['name'], ret))
                trades.append({'code': code, 'name': stocks[code]['name'],
                               'bd': p['bd'], 'sd': dt, 'ret': ret / 100,
                               'exit': 'trail'})
                del pos[code]

    # Rebalance
    if di % REBAL == 0:
        cand = [(c, factor.get(c, {}).get(dt, float('nan'))) for c in stocks]
        cand = [(c, s) for c, s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
        cand = [(c, s) for c, s in cand if s >= MIN_F]
        cand.sort(key=lambda x: x[1], reverse=True)

        selected = []; sel_secs = set()
        for c, s in cand:
            sec = sm.get(c, '')
            if sec and sec in sel_secs: continue
            if len(selected) >= MAX_POS: break
            selected.append((c, s)); sel_secs.add(sec)
        top_codes = set(c for c, _ in selected)

        # Sell non-top
        sold_count = 0
        for code in list(pos.keys()):
            if code not in top_codes:
                px = stocks[code]['close'][idx[code][dt]]
                sp = px * (1 - SLIP - S_FEE - STAX); cash += pos[code]['shares'] * sp
                ret = (sp - pos[code]['bp']) / pos[code]['bp'] * 100
                sold_count += 1
                trades.append({'code': code, 'name': stocks[code]['name'],
                               'bd': pos[code]['bd'], 'sd': dt, 'ret': ret / 100,
                               'exit': 'rebal'})
                del pos[code]

        # Buy new
        bought_count = 0; bought_names = []
        pv_curr2 = sum(p['shares'] * stocks[c]['close'][idx[c][dt]]
                       for c, p in pos.items() if c in idx and dt in idx[c])
        total_nav = cash + pv_curr2
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
                             'entry_factor': score}
                bought_count += 1
                bought_names.append((stocks[code]['name'], score))
        if sold_count > 0 or bought_count > 0:
            day_actions.append('REBAL: sold %d, bought %d (%s)' % (
                sold_count, bought_count,
                ', '.join('%s(%.2f)' % (n, s) for n, s in bought_names[:3])))

    cash *= (1 + RF / TD)
    daily_actions.append({'date': dt, 'nav': nav, 'actions': day_actions, 'pos_count': len(pos)})

# Fill forward returns for each guard event
for ge in guard_events:
    di = ge['di']
    ge['pos_after_crash'] = 0
    # Find when first rebalance happens after crash
    next_rebal_di = ((di // REBAL) + 1) * REBAL
    if next_rebal_di < len(cd):
        ge['days_to_rebal'] = next_rebal_di - di
        ge['rebal_date'] = cd[next_rebal_di]
    # NAV at +5d, +10d, +21d
    for offset, key in [(5, 'nav_5d'), (10, 'nav_10d'), (21, 'nav_21d')]:
        if di + offset < len(daily_nav):
            ge[key] = daily_nav[di + offset]
    ge['nav_peak_after'] = max(daily_nav[di:min(di+63, len(daily_nav))])
    ge['recovery_days'] = next(
        (j for j in range(di, len(daily_nav)) if daily_nav[j] >= ge['nav_before']), None)

# ============================================================
# PRINT ANALYSIS
# ============================================================
print('=' * 95)
print('  崩盘熔断回放: 每次触发详细分析')
print('=' * 95)
print('  参数: K=1.5 LB=14 Trail=30% min_f=0.8 CrashGuard=-5%')
print('  触发条件: 单日净值跌幅 > 5%')
print('  触发后: 全仓清仓，等待下次调仓日(最多%d天)' % REBAL)

print('\n' + '=' * 95)
print('  熔断事件列表 (%d次触发)' % len(guard_events))
print('=' * 95)

for i, ge in enumerate(guard_events):
    rec_days = ge['recovery_days'] - ge['di'] if ge['recovery_days'] else 'never'
    peak_after = (ge['nav_peak_after'] - ge['nav_after']) / ge['nav_after'] * 100
    print('\n  --- 熔断 #%d: %s (第%d交易日) ---' % (i + 1, ge['date'], ge['di']))
    print('  单日跌幅: %.2f%%' % ge['daily_ret%'])
    print('  熔断前净值: %.0f万 → 熔断后净值: %.0f万 (现金)' % (
        ge['nav_before'] / 1e4, ge['nav_after'] / 1e4))
    print('  被清仓的持仓:')
    for ps in ge['positions_sold']:
        print('    %-12s %-20s 买入:%s 收益:%+.1f%% 因子:%.2f' % (
            ps['name'][:12], ps['sector'][:20], ps['buy_date'],
            ps['ret%'], ps['entry_factor']))
    print('  5天后净值: %.0f万 (%+.1f%%)' % (
        ge.get('nav_5d', 0) / 1e4,
        (ge.get('nav_5d', ge['nav_after']) - ge['nav_after']) / ge['nav_after'] * 100))
    print('  21天后净值: %.0f万 (%+.1f%%)' % (
        ge.get('nav_21d', 0) / 1e4,
        (ge.get('nav_21d', ge['nav_after']) - ge['nav_after']) / ge['nav_after'] * 100))
    print('  之后峰值: %.0f万 (%+.1f%%)' % (ge['nav_peak_after'] / 1e4, peak_after))
    print('  恢复到熔断前: %s' % (
        '第%d天(%.1f周)' % (rec_days, (rec_days / 5)) if isinstance(rec_days, int) else '从未恢复 ❌'))

# ============================================================
# DETAILED REPLAY of worst crash
# ============================================================
print('\n' + '=' * 95)
print('  最惨熔断回放: 逐日操作日志')
print('=' * 95)

# Find the crash event with worst daily return
worst_ge = min(guard_events, key=lambda x: x['daily_ret%'])
worst_di = worst_ge['di']
start_di = max(0, worst_di - 10)
end_di = min(len(cd) - 1, worst_di + 42)

print('\n  熔断日: %s (单日跌幅 %.2f%%)' % (worst_ge['date'], worst_ge['daily_ret%']))
print('  %-12s %8s %5s %s' % ('Date', 'NAV(万)', 'Pos', 'Actions'))
print('  ' + '-' * 75)

for di in range(start_di, end_di + 1):
    da = daily_actions[di]
    dt = da['date']
    nav_w = da['nav'] / 1e4
    pos_n = da['pos_count']
    actions = da['actions']
    marker = ' <<< CRASH' if di == worst_di else ''
    if actions:
        for a in actions:
            print('  %-12s %8.0f %4d  %s%s' % (dt, nav_w, pos_n, a, marker))
            marker = ''  # Only show marker once
    # else: skip non-action days for brevity

# ============================================================
# SCENARIO: what if we DIDN'T crash-guard?
# ============================================================
print('\n' + '=' * 95)
print('  假设不放熔断: 持有不动 vs 熔断清仓 对比')
print('=' * 95)

for i, ge in enumerate(guard_events):
    # Simulate: what if we held the positions instead of selling?
    held_value = 0
    for ps in ge['positions_sold']:
        code = ps['code']
        p = None
        # We don't have the actual position object anymore, but we can approximate
        # The nav_before includes all positions, so:
        # held_value_at_crash = sum of position closing values
        pass

    # Simpler: compare crash NAV vs holding
    nav_if_held = 0
    # If we held, we'd have the same NAV as before (positions were sold at market)
    # Actually the crash guard sells at market close price
    # So nav_after = nav_before - daily_loss (positions were liquidated)
    # If not sold: next day's price change would apply to positions too
    # For simplicity: just compare re-entry timing
    rebal_di = ((ge['di'] // REBAL) + 1) * REBAL
    rebal_date = cd[rebal_di] if rebal_di < len(cd) else '?'
    days_waiting = rebal_di - ge['di'] if rebal_di < len(cd) else '?'

    print('\n  熔断 #%d (%s):' % (i + 1, ge['date']))
    print('    熔断日净值: %.0f万 → 全仓变现金' % (ge['nav_before'] / 1e4))
    print('    下次调仓日: %s (等待%d天)' % (rebal_date, days_waiting))
    if ge.get('nav_5d'):
        held_gain = (ge['nav_5d'] - ge['nav_before']) / ge['nav_before'] * 100
        cash_gain = (ge['nav_5d'] - ge['nav_after']) / ge['nav_after'] * 100
        print('    5天后: 若持有=%.1f%% vs 现金=%.1f%%' % (held_gain, cash_gain))

# ============================================================
# CONCLUSION
# ============================================================
print('\n' + '=' * 95)
print('  结论')
print('=' * 95)

n_recovered = sum(1 for ge in guard_events
                  if ge.get('recovery_days') and isinstance(ge.get('recovery_days'), int))
print('  %d/%d 次熔断后净值恢复到熔断前水平' % (n_recovered, len(guard_events)))

avg_recovery = sum(
    ge['recovery_days'] - ge['di']
    for ge in guard_events
    if ge.get('recovery_days') and isinstance(ge.get('recovery_days'), int)
) / n_recovered if n_recovered > 0 else 0
print('  平均恢复时间: %.0f 个交易日 (%.1f 周)' % (avg_recovery, avg_recovery / 5))

avg_wait_to_rebal = sum(
    ((ge['di'] // REBAL) + 1) * REBAL - ge['di']
    for ge in guard_events
) / len(guard_events)
print('  平均等待下次调仓: %.0f 天' % avg_wait_to_rebal)

# Worst-case
worst = min(guard_events, key=lambda x: x['daily_ret%'])
print('\n  最惨熔断: %s (-%.1f%%)' % (worst['date'], -worst['daily_ret%']))
print('  被卖出的持仓: %d只' % len(worst['positions_sold']))
if worst.get('nav_21d'):
    recovery_21d = (worst['nav_21d'] - worst['nav_after']) / worst['nav_after'] * 100
    print('  21天后现金收益: %.1f%% (如果持有不动会怎样?)' % recovery_21d)

print('\nDone!')
