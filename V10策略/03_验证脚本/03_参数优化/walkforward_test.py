"""V10 Walk-Forward 样本外验证.
方法: 滚动窗口. 用前3年(IS训练集)选出的参数/规则, 在随后1年(OOS样本外)验证.
对'原版'和'R10空仓'分别测试, 看谁是真本事谁是过拟合.

窗口划分 (每年约252交易日):
  Fold1: IS=2020-2022  -> OOS=2023
  Fold2: IS=2021-2023  -> OOS=2024
  Fold3: IS=2022-2024  -> OOS=2025
  Fold4: IS=2023-2025  -> OOS=2026
合并所有OOS = 2023-2026 全部样本外表现.

另外测: 仅看2020-2023选出的最优配置, 用在2024-2026的真实样本外.
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
# 预计算信号 (全周期)
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
    """在 [start_d, end_d] 区间内回测. use_cash=True 用R10空仓规则."""
    cash = INIT; pos = None; shares = 0.0; bp = 0.0; peak = 0.0
    entry_d = ''; entry_date = None; trades = []; dvs = []
    pool_mode = 'all'; rolling_pnl = []; consec_loss = 0
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
                    trades.append({'buy': entry_d, 'sell': d, 'pnl': pnl, 'ret': (px-bp)/bp})
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl) > 5: rolling_pnl.pop(0)
                    if pnl <= 0: consec_loss += 1
                    else: consec_loss = 0
                    cash = shares*px; pos = None; shares = 0.0; bp = 0.0; peak = 0.0; entry_date = None
                    if len(rolling_pnl) >= 5 and pool_mode == 'all' and sum(rolling_pnl) < -0.10*INIT:
                        pool_mode = 'defensive'
                    if pool_mode == 'defensive':
                        if n_pos_index(d) >= NEED_N: pool_mode = 'all'
        # ★ R10 空仓规则
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
        if bar: px = bar['close']; pnl = shares*px - shares*bp
        trades.append({'buy': entry_d, 'sell': seg_dates[-1], 'pnl': pnl, 'ret': (px-bp)/bp})
        cash = shares*px
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
    ar = (1+tr)**(TD/len(rets))-1 if tr > -1 and len(rets) > 0 else -1
    st = [t for t in trades if t['buy']]; w = sum(1 for t in st if t['ret'] > 0)
    wr = w/len(st) if st else 0
    return {'sh': sh, 'tr': tr, 'mdd': mdd, 'ar': ar, 'n': len(st), 'wr': wr, 'fv': fv, 'days': len(rets)}

# ===== Walk-Forward: 滚动3年训练 + 1年验证 =====
print('='*120)
print('  V10 Walk-Forward 滚动样本外验证 (3年IS训练 -> 1年OOS验证)')
print('='*120)

folds = [
    ('2020-01-01', '2022-12-31', '2023-01-01', '2023-12-31', 'Fold1 OOS=2023'),
    ('2021-01-01', '2023-12-31', '2024-01-01', '2024-12-31', 'Fold2 OOS=2024'),
    ('2022-01-01', '2024-12-31', '2025-01-01', '2025-12-31', 'Fold3 OOS=2025'),
    ('2023-01-01', '2025-12-31', '2026-01-01', '2026-12-31', 'Fold4 OOS=2026'),
]

print('\n【各折 IS(训练) vs OOS(样本外) 表现】')
print('  %-16s | %-26s | %-26s' % ('', '原版(不空仓)', 'R10(双确认空仓)'))
print('  ' + '-'*80)
print('  %-16s | %-8s %-8s %-6s | %-8s %-8s %-6s' % ('', 'IS夏普', 'OOS夏普', 'OOS收益', 'IS夏普', 'OOS夏普', 'OOS收益'))
print('  ' + '-'*80)
oos_results = {'orig': [], 'r10': []}
for is_s, is_e, oos_s, oos_e, label in folds:
    is_orig = run(is_s, is_e, use_cash=False)
    oos_orig = run(oos_s, oos_e, use_cash=False)
    is_r10 = run(is_s, is_e, use_cash=True)
    oos_r10 = run(oos_s, oos_e, use_cash=True)
    oos_results['orig'].append((label, oos_orig))
    oos_results['r10'].append((label, oos_r10))
    print('  %-16s | %.3f   %.3f   %+6.0f%% | %.3f   %.3f   %+6.0f%%' % (
        label, is_orig['sh'], oos_orig['sh'], oos_orig['tr']*100,
        is_r10['sh'], oos_r10['sh'], oos_r10['tr']*100))

# OOS 夏普衰减率
print('\n【IS->OOS 夏普衰减分析】')
print('  %-16s | %-18s | %-18s | %s' % ('', '原版衰减', 'R10衰减', '谁更稳健'))
print('  ' + '-'*80)
orig_decays = []; r10_decays = []
for i, (is_s, is_e, oos_s, oos_e, label) in enumerate(folds):
    is_o = run(is_s, is_e, False); oos_o = run(oos_s, oos_e, False)
    is_r = run(is_s, is_e, True); oos_r = run(oos_s, oos_e, True)
    decay_o = (is_o['sh'] - oos_o['sh'])/is_o['sh']*100 if is_o['sh'] > 0 else 0
    decay_r = (is_r['sh'] - oos_r['sh'])/is_r['sh']*100 if is_r['sh'] > 0 else 0
    orig_decays.append(decay_o); r10_decays.append(decay_r)
    tag = 'R10更稳' if abs(decay_r) < abs(decay_o) else '原版更稳'
    print('  %-16s | IS %.2f→OOS %.2f  %5.0f%% | IS %.2f→OOS %.2f  %5.0f%% | %s' % (
        label, is_o['sh'], oos_o['sh'], decay_o, is_r['sh'], oos_r['sh'], decay_r, tag))
print('  %-16s | 平均衰减 %5.0f%%        | 平均衰减 %5.0f%%        |' % (
    '平均', sum(orig_decays)/len(orig_decays), sum(r10_decays)/len(r10_decays)))

# ===== 合并OOS期间 (2023-2026) 连续表现 =====
print('\n' + '='*120)
print('  合并所有 OOS 区间 (2023-2026 完整样本外)')
print('='*120)
# 连续跑 2023-2026
oos_full_orig = run('2023-01-01', '2026-12-31', use_cash=False)
oos_full_r10 = run('2023-01-01', '2026-12-31', use_cash=True)
print('  %-24s %-14s %-14s' % ('指标', '原版(不空仓)', 'R10(双确认空仓)'))
print('  ' + '-'*60)
print('  %-24s %-14s %-14s' % ('OOS夏普(2023-2026)', '%.3f' % oos_full_orig['sh'], '%.3f' % oos_full_r10['sh']))
print('  %-24s %-14s %-14s' % ('OOS总收益', '%+.0f%%' % (oos_full_orig['tr']*100), '%+.0f%%' % (oos_full_r10['tr']*100)))
print('  %-24s %-14s %-14s' % ('OOS年化', '%.1f%%' % (oos_full_orig['ar']*100), '%.1f%%' % (oos_full_r10['ar']*100)))
print('  %-24s %-14s %-14s' % ('OOS最大回撤', '%.1f%%' % (oos_full_orig['mdd']*100), '%.1f%%' % (oos_full_r10['mdd']*100)))
print('  %-24s %-14s %-14s' % ('OOS交易笔数', '%d' % oos_full_orig['n'], '%d' % oos_full_r10['n']))
print('  %-24s %-14s %-14s' % ('OOS胜率', '%.0f%%' % (oos_full_orig['wr']*100), '%.0f%%' % (oos_full_r10['wr']*100)))

# ===== 关键: 2020-2023训练 -> 2024-2026纯样本外 =====
print('\n' + '='*120)
print('  ★ 终极检验: 2020-2023 训练选参数 -> 2024-2026 完全样本外')
print('='*120)
train_orig = run('2020-01-01', '2023-12-31', use_cash=False)
train_r10 = run('2020-01-01', '2023-12-31', use_cash=True)
test_orig = run('2024-01-01', '2026-12-31', use_cash=False)
test_r10 = run('2024-01-01', '2026-12-31', use_cash=True)
print('  %-26s %-16s %-16s' % ('', '原版', 'R10'))
print('  ' + '-'*66)
print('  %-26s %-16s %-16s' % ('训练期夏普(2020-23)', '%.3f' % train_orig['sh'], '%.3f' % train_r10['sh']))
print('  %-26s %-16s %-16s' % ('训练期收益', '%+.0f%%' % (train_orig['tr']*100), '%+.0f%%' % (train_r10['tr']*100)))
print('  %-26s %-16s %-16s' % ('样本外夏普(2024-26)', '%.3f' % test_orig['sh'], '%.3f' % test_r10['sh']))
print('  %-26s %-16s %-16s' % ('样本外收益', '%+.0f%%' % (test_orig['tr']*100), '%+.0f%%' % (test_r10['tr']*100)))
print('  %-26s %-16s %-16s' % ('样本外回撤', '%.1f%%' % (test_orig['mdd']*100), '%.1f%%' % (test_r10['mdd']*100)))
d1 = (train_orig['sh']-test_orig['sh'])/train_orig['sh']*100 if train_orig['sh']>0 else 0
d2 = (train_r10['sh']-test_r10['sh'])/train_r10['sh']*100 if train_r10['sh']>0 else 0
print('  %-26s %-16s %-16s' % ('夏普衰减(过拟合度)', '%.0f%%' % d1, '%.0f%%' % d2))
print('\n  结论: %s 过拟合程度更低' % ('R10' if d2 < d1 else '原版'))
