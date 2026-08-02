"""用回测验证报告.html 里的真实成本重算.
报告标注成本: 双边手续费0.1% + 印税0.05% + 滑点0.1% = 单次换手0.25%.
对比我之前误用的 0.7%/次.
"""
import json, os, math
from collections import defaultdict
from datetime import datetime

DATA_DIR = r"C:\Users\home\Desktop\策略文件夹\data"
RF = 0.025; TD = 252; INIT = int(1e7)
F_MA = 6; S_MA = 15; SL_MA = 8
TRAIL_BULL = 0.03; TRAIL_BEAR = 0.06; BULL_MH = 0; BEAR_MH = 7; PULLBACK_MIN = 0.05
NEED_N = 7
# ★ 报告里的真实成本 (单次换手 0.25%)
# 拆解: 滑点0.1%(单边) + 手续费0.1%(双边=0.05%/边) + 印花0.05%(卖单)
# 买入: 滑点0.1% + 手续费0.05% = 0.15%
# 卖出: 滑点0.1% + 手续费0.05% + 印花0.05% = 0.20%
# 单次换手 = 0.35%  ... 但报告说"双边0.1%+印0.05%+滑0.1%"合计0.25%
# 取报告字面: 双边手续费0.1%(已含买卖) + 印0.05% + 滑0.1% = 0.25% per round trip
# 更可能: 滑点0.1%是单边, 印花单边, 手续费双边0.1% => 卖出0.1%+0.05%+0.05%=0.2%, 买入0.1%+0.05%=0.15% => 0.35%
# 报告原文"双边手续费0.1%+印税0.05%+滑点0.1%" 字面合计 0.25%, 按此执行
SLIP = 0.001       # 0.1% 滑点(单边)
COMM_BUY = 0.0005  # 手续费(买入, 双边0.1%的一半)
COMM_SELL = 0.0005 # 手续费(卖出, 双边0.1%的一半)
STAMP = 0.0005     # 印花税(卖出)

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

def run(start_d, end_d, slip, comm_buy, comm_sell, stamp):
    cash = INIT; pos = None; shares = 0.0; bp = 0.0; peak = 0.0
    entry_d = ''; entry_date = None; dvs = []; n_trades = 0
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
                px_raw = bar['close']
                px_sell = px_raw * (1 - slip - comm_sell - stamp)
                if px_raw > peak: peak = px_raw
                ton = all_trnd[pos].get(d, False); er = None
                if px_raw <= peak*(1-cur_trail): er = 'trail'
                elif not ton: er = 'off'
                if er:
                    n_trades += 1
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
                bp = px_raw * (1 + slip + comm_buy)
                shares = cash/bp; peak = px_raw; pos = c; entry_d = d; entry_date = dt_obj; cash = 0.0
        pos_val = shares*dm[pos].get(d, {}).get('close', 0) if pos else 0
        dvs.append(cash + pos_val)
    if pos:
        bar = dm[pos].get(seg_dates[-1])
        if bar:
            px_raw = bar['close']
            px_sell = px_raw * (1 - slip - comm_sell - stamp)
            cash = shares*px_sell
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
    return tr, mdd, sh, n_trades

print('='*110)
print('  成本敏感性: 无成本 vs 报告成本(0.25%/次) vs 我误用成本(0.7%/次)')
print('='*110)
print('  报告标注成本: 双边手续费0.1% + 印花0.05% + 滑点0.1%')
# 报告成本拆解执行
# 无成本
# 报告成本: slip=0.001, comm_buy=0.0005, comm_sell=0.0005, stamp=0.0005
#   买入0.15% 卖出0.2% 单次0.35% (报告字面0.25%可能滑点只算单边)
# 我误用: slip=0.003, comm=0.00025, stamp=0.0005 => 单次0.7%
print('  %-30s %10s %10s %10s %8s' % ('方案', '总收益', '回撤', '夏普', '笔数'))
print('  ' + '-'*75)

# 全周期
tr0,m0,s0,n0 = run('2020-01-01','2026-12-31', 0, 0, 0, 0)
tr1,m1,s1,n1 = run('2020-01-01','2026-12-31', 0.001, 0.0005, 0.0005, 0.0005)
tr2,m2,s2,n2 = run('2020-01-01','2026-12-31', 0.003, 0.00025, 0.00025, 0.0005)
print('  【全周期 2020-2026】')
print('  %-30s %+9.0f%% %9.1f%% %8.2f %7d' % ('无成本 (理想)', tr0*100, m0*100, s0, n0))
print('  %-30s %+9.0f%% %9.1f%% %8.2f %7d' % ('报告成本 0.35%/次', tr1*100, m1*100, s1, n1))
print('  %-30s %+9.0f%% %9.1f%% %8.2f %7d' % ('我误用 0.7%/次', tr2*100, m2*100, s2, n2))
print('  报告成本吃掉: %.0f个百分点 (vs 无成本)' % ((tr0-tr1)*100))
print('  我多扣的: %.0f个百分点 (报告成本 vs 我误用)' % ((tr1-tr2)*100))

print('\n  【2024-2026 大牛市】')
tr0,m0,s0,n0 = run('2024-01-01','2026-12-31', 0, 0, 0, 0)
tr1,m1,s1,n1 = run('2024-01-01','2026-12-31', 0.001, 0.0005, 0.0005, 0.0005)
tr2,m2,s2,n2 = run('2024-01-01','2026-12-31', 0.003, 0.00025, 0.00025, 0.0005)
print('  %-30s %+9.0f%% %9.1f%% %8.2f %7d' % ('无成本 (理想)', tr0*100, m0*100, s0, n0))
print('  %-30s %+9.0f%% %9.1f%% %8.2f %7d' % ('报告成本 0.35%/次', tr1*100, m1*100, s1, n1))
print('  %-30s %+9.0f%% %9.1f%% %8.2f %7d' % ('我误用 0.7%/次', tr2*100, m2*100, s2, n2))
print('  持有最强ETF (事后): +304% 回撤29%')

print('\n  【分年度: 报告成本 vs 无成本】')
print('  %-6s %12s %12s %10s' % ('年份', '无成本', '报告成本', '差异'))
for y in ['2020','2021','2022','2023','2024','2025','2026']:
    tr0,_,_,_ = run(y+'-01-01', y+'-12-31', 0,0,0,0)
    tr1,_,_,_ = run(y+'-01-01', y+'-12-31', 0.001,0.0005,0.0005,0.0005)
    print('  %-6s %+11.0f%% %+11.0f%% %+9.0f' % (y, tr0*100, tr1*100, (tr0-tr1)*100))
