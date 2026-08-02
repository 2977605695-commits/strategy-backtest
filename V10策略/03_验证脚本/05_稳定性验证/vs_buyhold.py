"""V10原版/R10策略 vs 各ETF买入持有涨幅对比.
重点 2024-2026 (AI大牛市), 以及全周期.
核心问题: 策略吃到了多少大牛市? 比直接持有最强ETF差多少? 代价(回撤)如何?
"""
import json, os, math
from collections import defaultdict
from datetime import datetime

DATA_DIR = r"C:\Users\home\Desktop\策略文件夹\data"
RF = 0.025; TD = 252; INIT = int(1e7)
F_MA = 6; S_MA = 15; SL_MA = 8
TRAIL_BULL = 0.03; TRAIL_BEAR = 0.06; BULL_MH = 0; BEAR_MH = 7; PULLBACK_MIN = 0.05
NEED_N = 7
ETF_CODES = ['159782','588380','588870','588080','588300','518800','589720','588890','588170',
             '588200','159995','512480','515880','515050','159819','159992','512010',
             '518880','159937','513180','513050','513100','159509','588000','588220',
             '510300','159915','510050','511010','511260','510880','512890','159301']
DEFENSIVE = ['518880','159937','518800','511010','511260','510880','512890','510050']
INDEX_CODES = {'510300':'沪深300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF','513100':'纳指ETF',
    '511260':'十年国债','512890':'红利低波','159992':'创新药ETF'}
NAME = {}

def load():
    etfs = {}
    for code in ETF_CODES:
        p = os.path.join(DATA_DIR, 'etf_'+code+'.json')
        if not os.path.exists(p): continue
        d = json.load(open(p, encoding='utf-8')); NAME[code] = d['name']; bars = []
        for b in d['bars']:
            dt = b['date']; px = float(b['close'])
            if len(dt) == 8: dt = dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            bars.append({'date': dt, 'close': px, 'high': float(b.get('high', px))})
        if bars: etfs[code] = {'name': d['name'], 'first_date': bars[0]['date'], 'bars': bars}
    return etfs

def ma(d, w):
    m = [float('nan')]*(w-1)
    for i in range(w-1, len(d)): m.append(sum(d[i-w+1:i+1])/w)
    return m
def slp(ms, lb):
    s = [float('nan')]*len(ms)
    for i in range(len(ms)):
        if i < lb: continue
        ys = ms[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys): continue
        n = len(ys); sx = sy = sxy = sxx = 0
        for j, y in enumerate(ys): sx += j; sy += y; sxy += j*y; sxx += j*j
        d_ = n*sxx - sx*sx
        if d_ > 0: s[i] = (n*sxy - sx*sy)/d_/ms[i] if ms[i] > 0 else 0
    return s

etfs = load(); codes = sorted(etfs.keys())
for c, n in INDEX_CODES.items():
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
        else: trnd[d] = False; rat[d] = 1.0
        abv[d] = not math.isnan(m60[i]) and cl[i] > m60[i]; hgh[d] = hi[i]
    all_trnd[c] = trnd; all_ratio[c] = rat; above_ma60[c] = abv; etf_highs[c] = hgh
index_slope = {}; index_above60 = {}
for code, name in INDEX_CODES.items():
    if code not in etfs: continue
    cl = [b['close'] for b in etfs[code]['bars']]; dts = [b['date'] for b in etfs[code]['bars']]
    m60 = ma(cl, 60); sl = slp(m60, 20)
    index_slope[code] = {dts[i]: not math.isnan(sl[i]) and sl[i] > 0 for i in range(len(dts))}
    index_above60[code] = {dts[i]: not math.isnan(m60[i]) and cl[i] > m60[i] for i in range(len(dts))}
dm = {c: {b['date']: b for b in etfs[c]['bars']} for c in codes}
fd = {c: etfs[c]['first_date'] for c in codes}
all_dates = sorted(set(d for c in codes for d in dm[c].keys()))
date_idx = {d: i for i, d in enumerate(all_dates)}

def n_pos_index(d): return sum(1 for c in INDEX_CODES if index_slope.get(c, {}).get(d, False))
def hs300_above60(d): return index_above60.get('510300', {}).get(d, False)
def hs300_slope_pos(d): return index_slope.get('510300', {}).get(d, False)

def run(start_d, end_d, use_cash=False):
    cash = INIT; pos = None; shares = 0.0; bp = 0.0; peak = 0.0
    entry_d = ''; entry_date = None; dvs = []
    pool_mode = 'all'; rolling_pnl = []
    seg_dates = [d for d in all_dates if start_d <= d <= end_d]
    for d in seg_dates:
        avail = [c for c in codes if fd[c] <= d]
        dt_obj = datetime.strptime(d, '%Y-%m-%d')
        is_bear = not hs300_slope_pos(d)
        cur_trail = TRAIL_BEAR if is_bear else TRAIL_BULL
        cur_mh = BEAR_MH if is_bear else BULL_MH
        if pos:
            bar = dm[pos].get(d)
            if bar:
                px = bar['close']
                if px > peak: peak = px
                ton = all_trnd[pos].get(d, False); er = None
                if px <= peak*(1-cur_trail): er = 'trail'
                elif not ton: er = 'off'
                if er:
                    pnl = shares*px - shares*bp
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl) > 5: rolling_pnl.pop(0)
                    cash = shares*px; pos = None; shares = 0.0; bp = 0.0; peak = 0.0; entry_date = None
                    if len(rolling_pnl) >= 5 and pool_mode == 'all' and sum(rolling_pnl) < -0.10*INIT:
                        pool_mode = 'defensive'
                    if pool_mode == 'defensive':
                        if n_pos_index(d) >= NEED_N: pool_mode = 'all'
        stay_cash = False
        if use_cash:
            stay_cash = ((not hs300_slope_pos(d)) and (not hs300_above60(d))) and n_pos_index(d) <= 4
        if not pos and cash > 0 and not stay_cash:
            cands = []
            for c in avail:
                if pool_mode == 'defensive' and c not in DEFENSIVE: continue
                if not all_trnd[c].get(d, False): continue
                if not above_ma60.get(c, {}).get(d, False): continue
                if PULLBACK_MIN > 0:
                    hi20 = 0
                    for lb in range(1, 21):
                        pdi = max(0, len(all_dates)-1-lb)
                        if 0 <= pdi < len(all_dates): hi20 = max(hi20, etf_highs[c].get(all_dates[pdi], 0))
                    if hi20 > 0 and (hi20-dm[c][d]['close'])/hi20 < PULLBACK_MIN: continue
                bar = dm[c].get(d)
                if bar: cands.append((c, all_ratio[c].get(d, 1.0), bar['close']))
            if cands:
                cands.sort(key=lambda x: x[1], reverse=True)
                c, ratio, px = cands[0]
                shares = cash/px; bp = px; peak = px; pos = c; entry_d = d; entry_date = dt_obj; cash = 0.0
        pos_val = shares*dm[pos].get(d, {}).get('close', 0) if pos else 0
        dvs.append(cash + pos_val)
    if pos:
        bar = dm[pos].get(seg_dates[-1])
        if bar: cash = shares*bar['close']
    return dvs

def metrics(dvs):
    fv = dvs[-1]
    rets = [(dvs[i]-dvs[i-1])/dvs[i-1] for i in range(1, len(dvs)) if dvs[i-1] > 0]
    pk = dvs[0]; mdd = 0.0
    for v in dvs:
        if v > pk: pk = v
        dd = (pk-v)/pk
        if dd > mdd: mdd = dd
    tr = (fv-INIT)/INIT; mu = sum(rets)/len(rets) if rets else 0
    sd_ = (sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5 if len(rets) > 1 else 0.01
    av = sd_*math.sqrt(TD); sh = (mu*TD-RF)/av if av > 0 else 0
    return tr, mdd, sh

def buyhold(code, start_d, end_d):
    """单ETF买入持有区间收益 + 区间内最大回撤."""
    bars = [b for b in etfs[code]['bars'] if start_d <= b['date'] <= end_d]
    if len(bars) < 2: return None, None
    prices = [b['close'] for b in bars]
    ret = (prices[-1]-prices[0])/prices[0]
    pk = prices[0]; mdd = 0.0
    for p in prices:
        if p > pk: pk = p
        dd = (pk-p)/pk
        if dd > mdd: mdd = dd
    return ret, mdd

# ===== 2024-2026 对比 =====
S = '2024-01-01'; E = '2026-12-31'
print('='*120)
print('  2024-2026 (AI大牛市): 策略 vs 各ETF买入持有')
print('='*120)

dvs_orig = run(S, E, False); dvs_r10 = run(S, E, True)
tr_o, mdd_o, sh_o = metrics(dvs_orig)
tr_r, mdd_r, sh_r = metrics(dvs_r10)

# 各ETF buy&hold
bh = {}
for c in codes:
    r, m = buyhold(c, S, E)
    if r is not None: bh[c] = (r, m)
bh_sorted = sorted(bh.items(), key=lambda x: -x[1][0])

print('\n  各ETF买入持有涨幅排名 (2024-2026):')
print('  %-4s %-8s %-16s %10s %10s' % ('排名','代码','名称','涨幅','回撤'))
for i, (c, (r, m)) in enumerate(bh_sorted):
    tag = ''
    if c == bh_sorted[0][0]: tag = ' ← 最强'
    print('  %-4d %-8s %-16s %+9.0f%% %9.1f%%%s' % (i+1, c, NAME.get(c,c)[:14], r*100, m*100, tag))

# 等权33只ETF
eq_ret = sum(r for r, m in bh.values())/len(bh)

print('\n' + '='*100)
print('  ★ 核心对比 (2024-2026)')
print('='*100)
print('  %-30s %12s %12s %10s' % ('方案', '总收益', '最大回撤', '夏普'))
print('  ' + '-'*70)
print('  %-30s %+11.0f%% %11.1f%% %9.2f' % ('V10 原版策略', tr_o*100, mdd_o*100, sh_o))
print('  %-30s %+11.0f%% %11.1f%% %9.2f' % ('R10 空仓策略', tr_r*100, mdd_r*100, sh_r))
best_c, (best_r, best_m) = bh_sorted[0]
print('  %-30s %+11.0f%% %11.1f%% %9s' % ('持有最强ETF (%s)' % NAME.get(best_c,best_c)[:8], best_r*100, best_m*100, '—'))
print('  %-30s %+11.0f%% %11s %9s' % ('等权持有全部33只', eq_ret*100, '—', '—'))
# 等权回撤
eq_dvs = []
seg_dates = [d for d in all_dates if S <= d <= E]
for d in seg_dates:
    vals = []
    for c in codes:
        b = dm[c].get(d)
        if b: vals.append(b['close'])
    eq_dvs.append(sum(vals))
_, eq_mdd, _ = metrics(eq_dvs)
print('  %-30s %+11s %11.1f%% %9s' % ('', '', eq_mdd*100, ''))

print('\n  关键比率:')
print('    原版 / 最强ETF = %.0f%%  (策略吃到了最强ETF涨幅的 %.0f%%)' % (tr_o/best_r*100, tr_o/best_r*100))
print('    R10  / 最强ETF = %.0f%%  (策略吃到了最强ETF涨幅的 %.0f%%)' % (tr_r/best_r*100, tr_r/best_r*100))
print('    原版回撤 / 最强ETF回撤 = %.0f%%  (回撤只有它的 %.0f%%)' % (mdd_o/best_m*100, mdd_o/best_m*100))

# ===== 分年度: 策略 vs 最强ETF =====
print('\n' + '='*100)
print('  分年度: 策略 vs 当年最强ETF (事后诸葛, 看策略吃到多少)')
print('='*100)
print('  %-6s | %-16s %-10s | %-16s %-10s | %-16s %-10s | %s' % (
    '年份', '当年最强ETF', '涨幅', '次强ETF', '涨幅', 'V10原版', '涨幅', '原版/最强'))
print('  ' + '-'*100)
for y in ['2020','2021','2022','2023','2024','2025','2026']:
    ys = y+'-01-01'; ye = y+'-12-31'
    ybh = {}
    for c in codes:
        r, m = buyhold(c, ys, ye)
        if r is not None: ybh[c] = r
    yb_sorted = sorted(ybh.items(), key=lambda x: -x[1])
    dv = run(ys, ye, False)
    tr_y = (dv[-1]-INIT)/INIT
    best1 = yb_sorted[0]; best2 = yb_sorted[1] if len(yb_sorted)>1 else ('-',(0,))
    ratio = tr_y/best1[1]*100 if best1[1] != 0 else 0
    print('  %-6s | %-8s %-8s %+8.0f%% | %-8s %-8s %+8.0f%% | %-16s %+8.0f%% | %5.0f%%' % (
        y, best1[0], NAME.get(best1[0],'')[:6], best1[1]*100,
        best2[0], NAME.get(best2[0],'')[:6], best2[1]*100,
        'V10原版', tr_y*100, ratio))

# ===== 全周期 2020-2026 =====
print('\n' + '='*100)
print('  全周期 2020-2026: 策略 vs 最强ETF')
print('='*100)
S2='2020-01-01'; E2='2026-12-31'
dvs2 = run(S2, E2, False); tr2, mdd2, sh2 = metrics(dvs2)
bh2 = {}
for c in codes:
    r, m = buyhold(c, S2, E2)
    if r is not None: bh2[c] = (r, m)
bh2_sorted = sorted(bh2.items(), key=lambda x: -x[1][0])
best2_c, (best2_r, best2_m) = bh2_sorted[0]
print('  %-30s %12s %12s %10s' % ('方案', '总收益', '最大回撤', '夏普'))
print('  ' + '-'*70)
print('  %-30s %+11.0f%% %11.1f%% %9.2f' % ('V10 原版策略(全周期)', tr2*100, mdd2*100, sh2))
print('  %-30s %+11.0f%% %11.1f%% %9s' % ('持有最强ETF %s'%NAME.get(best2_c,best2_c)[:8], best2_r*100, best2_m*100, '—'))
print('\n  Top 5 ETF全周期涨幅:')
for i, (c, (r, m)) in enumerate(bh2_sorted[:5]):
    print('    %d. %-8s %-14s %+9.0f%% 回撤%.0f%%' % (i+1, c, NAME.get(c,c)[:12], r*100, m*100))
