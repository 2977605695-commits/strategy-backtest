"""V10 2022 trade-by-trade attribution.
Runs the canonical V10 config (7/15 slope>0 DEF->ALL switch, expanded_index_switch.py logic)
and dumps every trade, then summarizes 2022 specifically.
"""
import json, os, math
from collections import defaultdict
from datetime import datetime

DATA_DIR = r"C:\Users\home\Desktop\策略文件夹\data"
START = '2020-01-01'; END = '2026-07-30'
RF = 0.025; TD = 252; INIT = int(1e7)
F_MA = 6; S_MA = 15; SL_MA = 8
TRAIL_BULL = 0.03; TRAIL_BEAR = 0.06
BULL_MH = 0; BEAR_MH = 7; PULLBACK_MIN = 0.05
NEED_N = 7  # V10 canonical

ETF_CODES = ['159782','588380','588870','588080','588300','518800','589720','588890','588170',
             '588200','159995','512480','515880','515050','159819','159992','512010',
             '518880','159937','513180','513050','513100','159509','588000','588220',
             '510300','159915','510050','511010','511260','510880','512890','159301']
DEFENSIVE = ['518880','159937','518800','511010','511260','510880','512890','510050']

INDEX_CODES = {
    '510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF','159819':'人工智能ETF','588200':'科创芯片ETF',
    '513100':'纳指ETF','513050':'中概互联ETF',
    '511260':'十年国债','512890':'红利低波','518800':'黄金ETF',
    '159992':'创新药ETF','512010':'医药ETF',
}
NAME = {}
def load():
    etfs = {}
    for code in ETF_CODES:
        p = os.path.join(DATA_DIR, 'etf_'+code+'.json')
        if not os.path.exists(p): continue
        d = json.load(open(p, encoding='utf-8'))
        NAME[code] = d['name']
        bars = []
        for b in d['bars']:
            dt = b['date']; px = float(b['close'])
            if len(dt) == 8: dt = dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            if START <= dt <= END:
                bars.append({'date': dt, 'close': px, 'high': float(b.get('high', px))})
        if bars: etfs[code] = {'name': d['name'], 'first_date': bars[0]['date'], 'bars': bars}
    return etfs

def ma(d, w):
    m = [float('nan')]*(w-1)
    for i in range(w-1, len(d)):
        m.append(sum(d[i-w+1:i+1])/w)
    return m

def slp(ms, lb):
    s = [float('nan')]*len(ms)
    for i in range(len(ms)):
        if i < lb: continue
        ys = ms[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys): continue
        n = len(ys); sx = sy = sxy = sxx = 0
        for j, y in enumerate(ys):
            sx += j; sy += y; sxy += j*y; sxx += j*j
        d_ = n*sxx - sx*sx
        if d_ > 0:
            s[i] = (n*sxy - sx*sy)/d_/ms[i] if ms[i] > 0 else 0
    return s

etfs = load(); codes = sorted(etfs.keys())
for c, n in INDEX_CODES.items():
    if c in NAME: continue
    if c in etfs: NAME[c] = etfs[c]['name']

all_trnd = {}; all_ratio = {}; above_ma60 = {}; etf_highs = {}
for c in codes:
    bars = etfs[c]['bars']; cl = [b['close'] for b in bars]; hi = [b['high'] for b in bars]
    mf = ma(cl, F_MA); ms = ma(cl, S_MA); msl = ma(cl, SL_MA); slo_ = slp(msl, max(SL_MA//2, 3))
    m60 = ma(cl, 60); dts = [b['date'] for b in bars]
    trnd = {}; rat = {}; abv = {}; hgh = {}
    for i in range(len(bars)):
        d = dts[i]
        if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i] > 0:
            sk = not math.isnan(slo_[i]) and slo_[i] > 0
            trnd[d] = mf[i] > ms[i] and sk; rat[d] = mf[i]/ms[i]
        else:
            trnd[d] = False; rat[d] = 1.0
        abv[d] = not math.isnan(m60[i]) and cl[i] > m60[i]; hgh[d] = hi[i]
    all_trnd[c] = trnd; all_ratio[c] = rat; above_ma60[c] = abv; etf_highs[c] = hgh

index_slope = {}
for code, name in INDEX_CODES.items():
    if code not in etfs: continue
    cl = [b['close'] for b in etfs[code]['bars']]; dts = [b['date'] for b in etfs[code]['bars']]
    m60 = ma(cl, 60); sl = slp(m60, 20)
    index_slope[code] = {dts[i]: not math.isnan(sl[i]) and sl[i] > 0 for i in range(len(dts))}

dm = {c: {b['date']: b for b in etfs[c]['bars']} for c in codes}
fd = {c: etfs[c]['first_date'] for c in codes}
ad = set()
for c in codes: ad.update(dm[c].keys())
all_dates = sorted(ad)

def run():
    cash = INIT; pos = None; shares = 0.0; bp = 0.0; peak = 0.0
    entry_d = ''; entry_date = None; trades = []; dvs = []
    pool_mode = 'all'; rolling_pnl = []; switched_date = ''
    pool_history = []  # track mode switches

    for d in all_dates:
        avail = [c for c in codes if fd[c] <= d]
        dt_obj = datetime.strptime(d, '%Y-%m-%d')
        is_bear = not index_slope.get('510300', {}).get(d, False)
        cur_trail = TRAIL_BEAR if is_bear else TRAIL_BULL
        cur_mh = BEAR_MH if is_bear else BULL_MH

        if pos:
            bar = dm[pos].get(d)
            if bar:
                px = bar['close']
                if px > peak: peak = px
                ton = all_trnd[pos].get(d, False); er = None; reason = ''
                if px <= peak*(1-cur_trail):
                    er = 'trail'; reason = 'Trail止损%.0f%%' % (cur_trail*100)
                elif not ton:
                    if cur_mh > 0 and entry_date and (dt_obj-entry_date).days >= cur_mh:
                        er = 'off'; reason = '趋势转空(持仓>=%d天)' % cur_mh
                    else:
                        er = 'off'; reason = '趋势转空'
                if er:
                    pnl = shares*px - shares*bp
                    trades.append({
                        'code': pos, 'name': NAME.get(pos, pos),
                        'buy': entry_d, 'sell': d,
                        'bp': bp, 'sp': px, 'shares': shares,
                        'pnl': pnl, 'ret': (px-bp)/bp,
                        'reason': reason, 'trail_used': cur_trail,
                        'hold_days': (dt_obj-entry_date).days,
                        'mode_at_sell': pool_mode,
                    })
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl) > 5: rolling_pnl.pop(0)
                    cash = shares*px; pos = None; shares = 0.0
                    bp = 0.0; peak = 0.0; entry_date = None

                    if len(rolling_pnl) >= 5 and pool_mode == 'all' and sum(rolling_pnl) < -0.10*INIT:
                        pool_mode = 'defensive'; switched_date = d
                        pool_history.append(('ALL->DEF', d, '5笔累计亏损<-100万'))

                    if pool_mode == 'defensive':
                        n_pos = sum(1 for c in INDEX_CODES if index_slope.get(c, {}).get(d, False))
                        if n_pos >= NEED_N:
                            pool_mode = 'all'; switched_date = ''
                            pool_history.append(('DEF->ALL', d, '%d/15指数斜率>0' % n_pos))

        if not pos and cash > 0:
            cands = []
            for c in avail:
                if pool_mode == 'defensive' and c not in DEFENSIVE: continue
                ton = all_trnd[c].get(d, False)
                if not ton: continue
                if not above_ma60.get(c, {}).get(d, False): continue
                if PULLBACK_MIN > 0:
                    hi20 = 0
                    for lb in range(1, 21):
                        pdi = max(0, len(all_dates)-1-lb)
                        if 0 <= pdi < len(all_dates):
                            hi20 = max(hi20, etf_highs[c].get(all_dates[pdi], 0))
                    if hi20 > 0 and (hi20-dm[c][d]['close'])/hi20 < PULLBACK_MIN: continue
                bar = dm[c].get(d)
                if bar: cands.append((c, all_ratio[c].get(d, 1.0), bar['close']))
            if cands:
                cands.sort(key=lambda x: x[1], reverse=True)
                c, ratio, px = cands[0]
                shares = cash/px; bp = px; peak = px; pos = c
                entry_d = d; entry_date = dt_obj; cash = 0.0
        pos_val = shares*dm[pos].get(d, {}).get('close', 0) if pos else 0
        dvs.append(cash + pos_val)
    return trades, dvs, pool_history

trades, dvs, pool_history = run()

# ===== Overall =====
print('='*100)
print('  V10 2022 亏损归因分析 (7/15 slope>0 配方)')
print('='*100)

yr_pnl = defaultdict(list)
for t in trades:
    yr_pnl[t['sell'][:4]].append(t)

print('\n【年度交易汇总】')
print('  %-6s %5s %6s %10s %10s %8s' % ('年份', '笔数', '胜率', '盈利', '亏损', '净盈亏'))
for y in sorted(yr_pnl.keys()):
    ts = yr_pnl[y]
    wins = [t for t in ts if t['pnl'] > 0]
    losses = [t for t in ts if t['pnl'] <= 0]
    wsum = sum(t['pnl'] for t in wins); lsum = sum(t['pnl'] for t in losses)
    print('  %-6s %5d %5.0f%% %+10.0f %+10.0f %+9.0f' % (
        y, len(ts), len(wins)/len(ts)*100 if ts else 0, wsum, lsum, wsum+lsum))

# ===== 2022 detail =====
t2022 = [t for t in trades if t['sell'][:4] == '2022']
print('\n' + '='*100)
print('  2022 年逐笔交易明细 (共 %d 笔)' % len(t2022))
print('='*100)
print('  %-4s %-12s %-10s %-10s %8s %8s %8s %5s %-20s %s' % (
    '#', '标的', '买入', '卖出', '买价', '卖价', '收益%', '持仓', '模式', '退出原因'))
print('  ' + '-'*120)
for i, t in enumerate(t2022):
    print('  %-4d %s(%s) %-10s %-10s %8.3f %8.3f %+7.1f%% %4dd %-10s %-20s' % (
        i+1, t['code'], t['name'][:4], t['buy'], t['sell'], t['bp'], t['sp'],
        t['ret']*100, t['hold_days'], t['mode_at_sell'], t['reason']))

# ===== Attribution =====
print('\n' + '='*100)
print('  2022 亏损归因')
print('='*100)

# by reason
by_reason = defaultdict(lambda: {'n': 0, 'pnl': 0.0, 'wins': 0})
for t in t2022:
    key = t['reason']
    by_reason[key]['n'] += 1
    by_reason[key]['pnl'] += t['pnl']
    if t['pnl'] > 0: by_reason[key]['wins'] += 1
print('\n  按退出原因:')
print('  %-25s %5s %6s %12s' % ('退出原因', '笔数', '胜率', '净盈亏'))
for k, v in sorted(by_reason.items(), key=lambda x: x[1]['pnl']):
    print('  %-25s %5d %5.0f%% %+12.0f' % (k, v['n'], v['wins']/v['n']*100 if v['n'] else 0, v['pnl']))

# by stock
by_stock = defaultdict(lambda: {'n': 0, 'pnl': 0.0, 'wins': 0})
for t in t2022:
    key = '%s %s' % (t['code'], t['name'])
    by_stock[key]['n'] += 1
    by_stock[key]['pnl'] += t['pnl']
    if t['pnl'] > 0: by_stock[key]['wins'] += 1
print('\n  按标的:')
print('  %-22s %5s %6s %12s' % ('标的', '笔数', '胜率', '净盈亏'))
for k, v in sorted(by_stock.items(), key=lambda x: x[1]['pnl']):
    print('  %-22s %5d %5.0f%% %+12.0f' % (k, v['n'], v['wins']/v['n']*100 if v['n'] else 0, v['pnl']))

# by mode at sell
by_mode = defaultdict(lambda: {'n': 0, 'pnl': 0.0, 'wins': 0})
for t in t2022:
    key = t['mode_at_sell']
    by_mode[key]['n'] += 1
    by_mode[key]['pnl'] += t['pnl']
    if t['pnl'] > 0: by_mode[key]['wins'] += 1
print('\n  按卖出时所在池子模式:')
print('  %-15s %5s %6s %12s' % ('池子模式', '笔数', '胜率', '净盈亏'))
for k, v in sorted(by_mode.items(), key=lambda x: x[1]['pnl']):
    print('  %-15s %5d %5.0f%% %+12.0f' % (k, v['n'], v['wins']/v['n']*100 if v['n'] else 0, v['pnl']))

# biggest losers
print('\n  2022 年最大 5 笔亏损:')
for t in sorted(t2022, key=lambda x: x['pnl'])[:5]:
    print('    %s %s %s→%s %+.1f%% (%s)' % (
        t['code'], t['name'][:4], t['buy'], t['sell'], t['ret']*100, t['reason']))

# pool switch history in 2022
print('\n  2022 年池子切换记录:')
for sw in pool_history:
    if sw[1][:4] == '2022':
        print('    %s  %s  %s' % sw)
if not any(sw[1][:4] == '2022' for sw in pool_history):
    print('    (2022 年内无切换记录)')

# ===== Monthly equity in 2022 =====
print('\n' + '='*100)
print('  2022 年月度净值曲线')
print('='*100)
# Reconstruct month-end equity
# Map all_dates index to dvs
date_idx = {d: i for i, d in enumerate(all_dates)}
months = sorted(set(d[:7] for d in all_dates if d[:4] == '2022'))
prev_eq = None
print('  %-8s %12s %10s' % ('月份', '月末净值', '月收益%'))
for m in months:
    # find last trading date in month
    last_d = max(d for d in all_dates if d[:7] == m)
    eq = dvs[date_idx[last_d]]
    if prev_eq is None:
        # find end of 2021
        prev_candidates = [d for d in all_dates if d < m]
        prev_eq = dvs[date_idx[prev_candidates[-1]]] if prev_candidates else INIT
    ret = (eq-prev_eq)/prev_eq*100
    print('  %-8s %+12.0f %+9.1f%%' % (m, eq, ret))
    prev_eq = eq

# Net 2022 result
eq_start_2022 = None
eq_end_2022 = None
d2021 = [d for d in all_dates if d[:4] == '2021']
d2022 = [d for d in all_dates if d[:4] == '2022']
if d2021 and d2022:
    eq_start_2022 = dvs[date_idx[d2021[-1]]]
    eq_end_2022 = dvs[date_idx[d2022[-1]]]
    print('\n  2022 全年: %+.1f%%' % ((eq_end_2022-eq_start_2022)/eq_start_2022*100))
