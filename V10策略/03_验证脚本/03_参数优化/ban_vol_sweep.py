"""V10 熊市禁高波动股测试.
熊市(HS300 MA60斜率<0)时, 把波动率最高的 K 只 ETF 踢出交易池.
牛市不动, 防御池机制保留, Trail/MH 保持原版 (牛市3%/熊市6%, MH7).
"""
import json, os, math
from collections import defaultdict
from datetime import datetime

DATA_DIR = r"C:\Users\home\Desktop\策略文件夹\data"
START = '2020-01-01'; END = '2026-07-30'
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
            if START <= dt <= END: bars.append({'date': dt, 'close': px, 'high': float(b.get('high', px))})
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

# ===== 计算每只 ETF 全周期年化波动率, 用于排序禁选 =====
vol_ranking = {}
for c in codes:
    cl = [b['close'] for b in etfs[c]['bars']]
    rts = [(cl[i]-cl[i-1])/cl[i-1] for i in range(1, len(cl)) if cl[i-1] > 0]
    if len(rts) > 30:
        mu = sum(rts)/len(rts)
        sd = (sum((r-mu)**2 for r in rts)/(len(rts)-1))**0.5
        vol_ranking[c] = sd*math.sqrt(TD)

sorted_by_vol = sorted(vol_ranking.items(), key=lambda x: -x[1])
print('='*110)
print('  33 只 ETF 年化波动率排名 (高→低)')
print('='*110)
print('  %-4s %-8s %-14s %8s   %-4s %-8s %-14s %8s' % ('排名','代码','名称','年化波动','','代码','名称','年化波动'))
for i in range(len(sorted_by_vol)):
    half = ''
    if i < 8:
        c1, v1 = sorted_by_vol[i]
        print('  %-4d %-8s %-14s %7.1f%%' % (i+1, c1, NAME.get(c1,c1)[:12], v1*100))
print('  ...')
# print middle and low
for i in [8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32]:
    if i < len(sorted_by_vol):
        c1, v1 = sorted_by_vol[i]
        print('  %-4d %-8s %-14s %7.1f%%' % (i+1, c1, NAME.get(c1,c1)[:12], v1*100))

def run(ban_k):
    """ban_k: 熊市禁选波动率最高的 K 只 (0=不禁)."""
    ban_set = set(c for c, v in sorted_by_vol[:ban_k]) if ban_k > 0 else set()
    cash = INIT; pos = None; shares = 0.0; bp = 0.0; peak = 0.0
    entry_d = ''; entry_date = None; trades = []; dvs = []
    pool_mode = 'all'; rolling_pnl = []
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
                ton = all_trnd[pos].get(d, False); er = None
                if px <= peak*(1-cur_trail): er = 'trail'
                elif not ton: er = 'off'
                if er:
                    pnl = shares*px - shares*bp
                    trades.append({'code':pos,'buy': entry_d, 'sell': d, 'pnl': pnl, 'ret': (px-bp)/bp})
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl) > 5: rolling_pnl.pop(0)
                    cash = shares*px; pos = None; shares = 0.0; bp = 0.0; peak = 0.0; entry_date = None
                    if len(rolling_pnl) >= 5 and pool_mode == 'all' and sum(rolling_pnl) < -0.10*INIT:
                        pool_mode = 'defensive'
                    if pool_mode == 'defensive':
                        n_pos = sum(1 for c in INDEX_CODES if index_slope.get(c, {}).get(d, False))
                        if n_pos >= NEED_N: pool_mode = 'all'
        if not pos and cash > 0:
            cands = []
            for c in avail:
                if pool_mode == 'defensive' and c not in DEFENSIVE: continue
                if not all_trnd[c].get(d, False): continue
                if not above_ma60.get(c, {}).get(d, False): continue
                # ★ 熊市禁高波动股
                if is_bear and c in ban_set: continue
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
        bar = dm[pos].get(all_dates[-1])
        if bar: px = bar['close']; pnl = shares*px - shares*bp
        trades.append({'code':pos,'buy': entry_d, 'sell': all_dates[-1], 'pnl': pnl, 'ret': (px-bp)/bp})
        cash = shares*px
    fv = dvs[-1]
    rets = [(dvs[i]-dvs[i-1])/dvs[i-1] for i in range(1, len(dvs)) if dvs[i-1] > 0]
    pk = dvs[0]; mdd = 0.0
    for v in dvs:
        if v > pk: pk = v
        dd = (pk-v)/pk
        if dd > mdd: mdd = dd
    tr = (fv-INIT)/INIT; mu = sum(rets)/len(rets)
    sd_ = (sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
    av = sd_*math.sqrt(TD); sh = (mu*TD-RF)/av if av > 0 else 0
    ar = (1+tr)**(TD/len(rets))-1 if tr > -1 else -1
    st = [t for t in trades if t['buy']]; w = sum(1 for t in st if t['ret'] > 0)
    wr = w/len(st) if st else 0
    yr = defaultdict(float)
    for t in st: yr[t['sell'][:4]] += t['pnl']
    return {'sh': sh, 'tr': tr, 'mdd': mdd, 'ar': ar, 'n': len(st), 'wr': wr, 'yr': dict(yr), 'trades': st, 'ban': ban_set}

# 扫描 K=0,2,3,4,5,6,8,10
ks = [0, 2, 3, 4, 5, 6, 8, 10]
results = {k: run(k) for k in ks}

print('\n' + '='*120)
print('  熊市禁高波动股扫描 (熊市踢出波动率最高的 K 只, 牛市不动)')
print('='*120)
print('  禁选K | 夏普   | 总收益%%  | 年化%% | 回撤%% | 胜率%% | 笔数 |  2020   2021   2022   2023   2024   2025   2026')
print('  ' + '-'*118)
for k in ks:
    r = results[k]
    yrs = '  '.join('%+6.1f%%' % (r['yr'].get(y, 0)/INIT*100) for y in ['2020','2021','2022','2023','2024','2025','2026'])
    tag = ' (原版)' if k == 0 else ''
    print('  K=%-3d | %.3f  | %+8.0f%% | %5.1f%% | %5.1f%% | %4.0f%% | %4d | %s%s' % (
        k, r['sh'], r['tr']*100, r['ar']*100, r['mdd']*100, r['wr']*100, r['n'], yrs, tag))

# 显示各档被禁标的
print('\n  各档禁选标的:')
for k in ks:
    if k == 0: continue
    names = [NAME.get(c, c) for c, v in sorted_by_vol[:k]]
    print('  K=%-3d 禁: %s' % (k, ' '.join(n[:6] for n in names)))

# 对比改善
base = results[0]
print('\n' + '='*100)
print('  相比原版(K=0)的改善')
print('='*100)
print('  禁选K | Δ夏普  | Δ总收益%% | Δ2022%%  | Δ回撤%% | 评价')
print('  ' + '-'*70)
for k in ks:
    r = results[k]
    dsh = r['sh'] - base['sh']; dtr = (r['tr'] - base['tr'])*100
    d22 = (r['yr'].get('2022',0) - base['yr'].get('2022',0))/INIT*100
    dmdd = (r['mdd'] - base['mdd'])*100
    if dsh > 0.02 and d22 > 0.5: tag = '★★ 双优'
    elif dsh > 0.02: tag = '★ 夏普提升'
    elif d22 > 0.5: tag = '· 2022改善'
    elif abs(d22) < 0.5 and abs(dsh) < 0.02: tag = '○ 持平'
    else: tag = '× 变差'
    print('  K=%-3d | %+.3f | %+8.0f | %+7.1f | %+6.1f | %s' % (k, dsh, dtr, d22, dmdd, tag))
