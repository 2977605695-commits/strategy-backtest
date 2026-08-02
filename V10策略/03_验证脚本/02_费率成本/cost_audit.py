"""成本审计: 仔细分析单次换手成本到底多少, 为什么累积这么高.
关键问题: 0.7%/次换手, 6.5年累积到底吃掉多少?
拆解: 每笔交易的实际成本, 年化换手率, 复利效应.
"""
import json, os, math
from collections import defaultdict
from datetime import datetime

DATA_DIR = r"C:\Users\home\Desktop\策略文件夹\data"
RF = 0.025; TD = 252; INIT = int(1e7)
F_MA = 6; S_MA = 15; SL_MA = 8
TRAIL_BULL = 0.03; TRAIL_BEAR = 0.06; BULL_MH = 0; BEAR_MH = 7; PULLBACK_MIN = 0.05
NEED_N = 7
SLIPPAGE = 0.003; BUY_FEE = 0.00025; SELL_FEE = 0.00025; STAMP_TAX = 0.0005

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
index_slope = {}
for code, name in INDEX_CODES.items():
    if code not in etfs: continue
    cl = [b['close'] for b in etfs[code]['bars']]; dts = [b['date'] for b in etfs[code]['bars']]
    m60 = ma(cl, 60); sl = slp(m60, 20)
    index_slope[code] = {dts[i]: not math.isnan(sl[i]) and sl[i] > 0 for i in range(len(dts))}
dm = {c: {b['date']: b for b in etfs[c]['bars']} for c in codes}
fd = {c: etfs[c]['first_date'] for c in codes}
all_dates = sorted(set(d for c in codes for d in dm[c].keys()))
def n_pos_index(d): return sum(1 for c in INDEX_CODES if index_slope.get(c, {}).get(d, False))
def hs300_slope_pos(d): return index_slope.get('510300', {}).get(d, False)

def run(start_d, end_d, use_real_cost=True):
    """记录每笔交易的明细成本."""
    cash = INIT; pos = None; shares = 0.0; bp = 0.0; peak = 0.0
    entry_d = ''; entry_date = None; dvs = []
    pool_mode = 'all'; rolling_pnl = []
    seg_dates = [d for d in all_dates if start_d <= d <= end_d]
    trades = []  # 每笔记录成本
    total_buy_cost = 0.0  # 累计买入成本(元)
    total_sell_cost = 0.0  # 累计卖出成本(元)
    for d in seg_dates:
        avail = [c for c in codes if fd[c] <= d]
        dt_obj = datetime.strptime(d, '%Y-%m-%d')
        is_bear = not hs300_slope_pos(d)
        cur_trail = TRAIL_BEAR if is_bear else TRAIL_BULL
        cur_mh = BEAR_MH if is_bear else BULL_MH
        if pos:
            bar = dm[pos].get(d)
            if bar:
                px_raw = bar['close']
                if use_real_cost:
                    px_sell = px_raw * (1 - SLIPPAGE - SELL_FEE - STAMP_TAX)
                    sell_cost = shares * px_raw * (SLIPPAGE + SELL_FEE + STAMP_TAX)
                else:
                    px_sell = px_raw; sell_cost = 0
                if px_raw > peak: peak = px_raw
                ton = all_trnd[pos].get(d, False); er = None
                if px_raw <= peak*(1-cur_trail): er = 'trail'
                elif not ton: er = 'off'
                if er:
                    gross_pnl = shares*px_raw - shares*bp_gross  # bp_gross 是不含成本的买入价
                    trades.append({'date': d, 'gross_pnl': gross_pnl, 'sell_cost': sell_cost})
                    total_sell_cost += sell_cost
                    pnl = shares*px_sell - shares*bp
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl) > 5: rolling_pnl.pop(0)
                    cash = shares*px_sell; pos = None; shares = 0.0; bp = 0.0; peak = 0.0; entry_date = None
                    if len(rolling_pnl) >= 5 and pool_mode == 'all' and sum(rolling_pnl) < -0.10*INIT:
                        pool_mode = 'defensive'
                    if pool_mode == 'defensive':
                        if n_pos_index(d) >= NEED_N: pool_mode = 'all'
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
                        if 0 <= pdi < len(all_dates): hi20 = max(hi20, etf_highs[c].get(all_dates[pdi], 0))
                    if hi20 > 0 and (hi20-dm[c][d]['close'])/hi20 < PULLBACK_MIN: continue
                bar = dm[c].get(d)
                if bar: cands.append((c, all_ratio[c].get(d, 1.0), bar['close']))
            if cands:
                cands.sort(key=lambda x: x[1], reverse=True)
                c, ratio, px_raw = cands[0]
                if use_real_cost:
                    bp = px_raw * (1 + SLIPPAGE + BUY_FEE)
                    buy_cost = cash * (SLIPPAGE + BUY_FEE)
                    total_buy_cost += buy_cost
                else:
                    bp = px_raw; buy_cost = 0
                bp_gross = px_raw
                shares = cash/bp; peak = px_raw; pos = c; entry_d = d; entry_date = dt_obj; cash = 0.0
        pos_val = shares*dm[pos].get(d, {}).get('close', 0) if pos else 0
        dvs.append(cash + pos_val)
    if pos:
        bar = dm[pos].get(seg_dates[-1])
        if bar:
            px_raw = bar['close']
            px_sell = px_raw * (1 - SLIPPAGE - SELL_FEE - STAMP_TAX) if use_real_cost else px_raw
            if use_real_cost:
                total_sell_cost += shares * px_raw * (SLIPPAGE + SELL_FEE + STAMP_TAX)
            cash = shares*px_sell
    return dvs, trades, total_buy_cost, total_sell_cost, len([t for t in trades])

# ===== 全周期成本审计 =====
print('='*100)
print('  成本审计: 单次换手成本 + 累积成本拆解')
print('='*100)

# 1. 理论单次成本
print('\n【1. 单次换手理论成本】')
buy_unit = SLIPPAGE + BUY_FEE
sell_unit = SLIPPAGE + SELL_FEE + STAMP_TAX
single = buy_unit + sell_unit
print('  买入方: 滑点%.3f%% + 佣金%.4f%% = %.4f%%' % (SLIPPAGE*100, BUY_FEE*100, buy_unit*100))
print('  卖出方: 滑点%.3f%% + 佣金%.4f%% + 印花%.3f%% = %.4f%%' % (SLIPPAGE*100, SELL_FEE*100, STAMP_TAX*100, sell_unit*100))
print('  单次完整换手(卖+买): %.4f%%' % (single*100))
print('  → 10万仓位单次成本: %.0f元' % (100000*single))

# 2. 实际交易统计
print('\n【2. 全周期交易统计 2020-2026】')
dvs, trades, tbc, tsc, n_tr = run('2020-01-01', '2026-12-31', use_real_cost=True)
n_trades = n_tr
years = 6.5
print('  总交易笔数(卖出): %d 笔' % n_trades)
print('  年均交易笔数: %.1f 笔/年' % (n_trades/years))
print('  累计买入成本: %.0f 元 (%.2f%% of 初始资金)' % (tbc, tbc/INIT*100))
print('  累计卖出成本: %.0f 元 (%.2f%% of 初始资金)' % (tsc, tsc/INIT*100))
print('  累计总成本: %.0f 元 (%.2f%% of 初始资金)' % (tbc+tsc, (tbc+tsc)/INIT*100))

# 3. 关键: 成本是按"当时仓位金额"还是"初始资金"
print('\n【3. 成本基数分析 - 为什么累积这么高】')
print('  初始资金: %.0f元' % INIT)
print('  期末仓位金额(后期满仓): %.0f元 (收益放大后)' % dvs[-1])
print('  → 后期单次换手成本 = 仓位 × 0.7%%')
print('  → 例: 期末1.5亿仓位, 单次换手 = %.0f元' % (1.5e8*single))
print('  → 这就是为什么成本随收益"滚雪球"放大')

# 4. 复利视角: 每次换手成本占当时净值的比例
print('\n【4. 成本的复利侵蚀 (关键)】')
# 假设每次换手成本是当时净值的 0.7%
# N次换手后, 净值变成 (1-0.007)^N
print('  每次换手吃掉当时净值的 0.7%%')
for n in [20, 50, 100, 150]:
    remaining = (1-single)**n
    print('  %d次换手后, 净值剩 %.4f (成本吃掉 %.1f%%)' % (n, remaining, (1-remaining)*100))
print('  全周期 %d 次换手: 净值剩 %.4f (成本吃掉 %.1f%%)' % (n_trades, (1-single)**n_trades, (1-(1-single)**n_trades)*100))
print('  ★ 这就是为什么 +1446%% → +397%%: 成本不是线性累加, 而是复利侵蚀!')

# 5. 分年度成本占比
print('\n【5. 分年度: 成本占当年初始资金的比例】')
print('  %-6s %8s %12s %12s %10s' % ('年份', '笔数', '买入成本', '卖出成本', '占初始%'))
for y in ['2020','2021','2022','2023','2024','2025','2026']:
    ys = y+'-01-01'; ye = y+'-12-31'
    dv, tr, bc, sc, nt = run(ys, ye, use_real_cost=True)
    print('  %-6s %8d %12.0f %12.0f %9.1f%%' % (y, nt, bc, sc, (bc+sc)/INIT*100))

# 6. 真实vs理想 对照(全周期)
print('\n【6. 理想 vs 真实成本 全周期】')
dvs_ideal, _, _, _, _ = run('2020-01-01', '2026-12-31', use_real_cost=False)
tr_i = (dvs_ideal[-1]-INIT)/INIT
tr_r = (dvs[-1]-INIT)/INIT
print('  理想: %+,.0f%%' % tr_i)
print('  真实: %+,.0f%%' % tr_r)
print('  成本吃掉: %.0f个百分点' % ((tr_i-tr_r)*100))
