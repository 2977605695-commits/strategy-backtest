"""V10 标准配方完整回测 (按文档: 10指数检测池, 7/10 slope>0).
验证文档声称的核心指标: 夏普1.52 / 收益+1446% / 年化54.3% / 回撤22.3% / 胜率54% / 2022=-5.7%.
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
NEED_N = 7  # V10 文档: 7/10 slope>0

ETF_CODES = ['159782','588380','588870','588080','588300','518800','589720','588890','588170',
             '588200','159995','512480','515880','515050','159819','159992','512010',
             '518880','159937','513180','513050','513100','159509','588000','588220',
             '510300','159915','510050','511010','511260','510880','512890','159301']
DEFENSIVE = ['518880','159937','518800','511010','511260','510880','512890','510050']

# ===== V10 文档第三节: 10指数检测池 =====
INDEX_CODES = {
    # 宽基 (4)
    '510300': '沪深300', '159915': '创业板', '588000': '科创50', '510050': '上证50',
    # 科技 (2)
    '515880': '通信ETF', '512480': '半导体ETF',
    # 海外 (1)
    '513100': '纳指ETF',
    # 防御 (2)
    '511260': '十年国债', '512890': '红利低波',
    # 医药 (1)
    '159992': '创新药ETF',
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
    for i in range(w-1, len(d)): m.append(sum(d[i-w+1:i+1])/w)
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
date_idx = {d: i for i, d in enumerate(all_dates)}

print('V10 标准配方 | 检测池=%d指数, DEF->ALL门槛=%d/%d slope>0' % (len(INDEX_CODES), NEED_N, len(INDEX_CODES)))
print('交易池 %d ETF, 防御池 %d ETF\n' % (len(codes), len(DEFENSIVE)))

def run():
    cash = INIT; pos = None; shares = 0.0; bp = 0.0; peak = 0.0
    entry_d = ''; entry_date = None; trades = []; dvs = []
    pool_mode = 'all'; rolling_pnl = []; switched_date = ''
    pool_history = []

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
                elif not ton:
                    if cur_mh > 0 and entry_date and (dt_obj-entry_date).days >= cur_mh: er = 'off'
                    else: er = 'off'
                if er:
                    pnl = shares*px - shares*bp
                    trades.append({
                        'code': pos, 'name': NAME.get(pos, pos),
                        'buy': entry_d, 'sell': d, 'pnl': pnl, 'ret': (px-bp)/bp,
                    })
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl) > 5: rolling_pnl.pop(0)
                    cash = shares*px; pos = None; shares = 0.0
                    bp = 0.0; peak = 0.0; entry_date = None

                    if len(rolling_pnl) >= 5 and pool_mode == 'all' and sum(rolling_pnl) < -0.10*INIT:
                        pool_mode = 'defensive'; switched_date = d
                        pool_history.append(('ALL->DEF', d))

                    if pool_mode == 'defensive':
                        n_pos = sum(1 for c in INDEX_CODES if index_slope.get(c, {}).get(d, False))
                        if n_pos >= NEED_N:
                            pool_mode = 'all'; switched_date = ''
                            pool_history.append(('DEF->ALL', d, n_pos))

        if not pos and cash > 0:
            cands = []
            for c in avail:
                if pool_mode == 'defensive' and c not in DEFENSIVE: continue
                if not all_trnd[c].get(d, False): continue
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

    if pos:
        bar = dm[pos].get(all_dates[-1])
        if bar: px = bar['close']; pnl = shares*px - shares*bp
        trades.append({'code': pos, 'name': NAME.get(pos, pos), 'buy': entry_d,
                       'sell': all_dates[-1], 'pnl': pnl, 'ret': (px-bp)/bp})
        cash = shares*px
    return trades, dvs, pool_history

trades, dvs, pool_history = run()

# ===== 指标 =====
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

yr_pnl = defaultdict(float)
for t in st: yr_pnl[t['sell'][:4]] += t['pnl']

print('='*90)
print('  V10 标准配方回测结果 vs 文档声称值')
print('='*90)
print('  %-22s %-15s %-15s %s' % ('指标', '本文档值', '文档声称', '是否一致'))
print('  ' + '-'*80)

def cmp(label, mine, claim, tol_pct=5):
    diff = abs(mine - claim) / (abs(claim) if claim else 1) * 100
    ok = '✓' if diff < tol_pct else '✗ 差异%.0f%%' % diff
    print('  %-22s %-15s %-15s %s' % (label, mine, claim, ok))

cmp('夏普', round(sh, 2), 1.52)
cmp('总收益%', round(tr*100), 1446)
cmp('年化%', round(ar*100, 1), 54.3)
cmp('最大回撤%', round(mdd*100, 1), 22.3)
cmp('胜率%', round(wr*100), 54)
cmp('交易笔数', len(st, ), None) if False else None

print('\n  %-22s %s' % ('交易笔数', len(st)))
print('  %-22s %s' % ('终值(初始1千万)', '%+.0f' % fv))

print('\n【年度收益对比】')
print('  %-6s %-12s %-12s %s' % ('年份', '本文档值', '文档声称', '说明'))
print('  ' + '-'*70)
doc_year = {'2020': 17.1, '2021': 30.0, '2022': -5.7, '2023': 39.5,
            '2024': 80.7, '2025': 550.6, '2026': 734.3}
for y in ['2020','2021','2022','2023','2024','2025','2026']:
    mine = yr_pnl.get(y, 0)/INIT*100
    claim = doc_year.get(y, 0)
    diff = mine - claim
    tag = ''
    if abs(diff) > 3: tag = ('偏差%+.1f' % diff)
    print('  %-6s %+11.1f%%  %+11.1f%%  %s' % (y, mine, claim, tag))

print('\n【池子切换记录】')
for sw in pool_history:
    if len(sw) == 3:
        print('  %s  %s  (%d/10 斜率>0)' % sw)
    else:
        print('  %s  %s' % sw)

# 2022 detail
t2022 = [t for t in st if t['sell'][:4] == '2022']
w22 = sum(1 for t in t2022 if t['ret'] > 0)
print('\n【2022 年明细】 笔数=%d 胜率=%.0f%% 净盈亏=%+d (%.1f%%)' % (
    len(t2022), w22/len(t2022)*100 if t2022 else 0,
    sum(t['pnl'] for t in t2022), sum(t['pnl'] for t in t2022)/(
        dvs[date_idx[[d for d in all_dates if d[:4]=='2021'][-1]]])*100))
