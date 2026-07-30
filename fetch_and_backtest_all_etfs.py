"""Fetch new ETFs + merge existing 9 + run V4 backtest on full pool."""
import json, time, os, sys, io, math, re
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import urllib.request
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
START_DT = '2020-01-01'; END_DT = '2026-07-29'
os.makedirs(DATA_DIR, exist_ok=True)

# All ETFs from the table + our existing 9
ALL_ETFS = {
    # From the table (25 ETFs)
    '588200': '科创芯片ETF', '159995': '芯片ETF', '512480': '半导体ETF',
    '515880': '通信ETF', '515050': '5G通信ETF', '159819': '人工智能ETF',
    '159992': '创新药ETF', '512010': '医药ETF', '518880': '华安黄金ETF',
    '159937': '博时黄金ETF', '513180': '恒生科技ETF', '513050': '中概互联ETF',
    '513100': '纳指ETF', '159509': '纳指科技ETF', '588000': '华夏科创50ETF',
    '588080': '易方达科创50ETF', '588220': '科创100ETF',
    '510300': '沪深300ETF', '159915': '创业板ETF', '510050': '上证50ETF',
    '511010': '国债ETF', '511260': '十年国债ETF', '510880': '红利ETF',
    '512890': '中证红利低波ETF', '159301': '自由现金流ETF',
    # Our existing 9 (some overlap like 588080)
    '159782': '科创50ETF', '588380': '科创100ETF', '588870': '科创200ETF',
    '588300': '科创芯片ETF', '518800': '黄金ETF', '589720': '半导体设备ETF',
    '588890': '科创AIETF', '588170': '科创半导体ETF',
}
# Remove duplicates - 588080 is in both
# Total unique ETFs:
unique_etfs = {}
for code, name in ALL_ETFS.items():
    if code not in unique_etfs:
        unique_etfs[code] = name

n_existing = sum(1 for c in unique_etfs if os.path.exists(os.path.join(DATA_DIR, f'etf_{c}.json')))
n_fetch = sum(1 for c in unique_etfs if not os.path.exists(os.path.join(DATA_DIR, f'etf_{c}.json')))
print(f'Total unique ETFs: {len(unique_etfs)}')
print(f'Existing: {n_existing}')
print(f'To fetch: {n_fetch}')

# ===== Step 1: Fetch missing =====
def fetch_chunk(code, s, e):
    prefix = 'sz' if code.startswith(('1','3')) else 'sh'
    url = f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,{s},{e},640,qfq'
    h = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'}
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = json.loads(r.read().decode('utf-8'))
                days = None
                if 'data' in raw:
                    for k in raw['data']:
                        if isinstance(raw['data'][k], dict):
                            for f in ['qfqday', 'day']:
                                if f in raw['data'][k]: days = raw['data'][k][f]; break
                            if days: break
                if days:
                    return [{'date': str(d[0]), 'open': float(d[1]), 'close': float(d[2]),
                             'high': float(d[3]), 'low': float(d[4]), 'volume': float(d[5])}
                            for d in days if len(d) >= 6]
        except: time.sleep(1)
    return []

to_fetch = [c for c in unique_etfs if not os.path.exists(os.path.join(DATA_DIR, f'etf_{c}.json'))]
print(f'\n[FETCH] {len(to_fetch)} new ETFs...')
for i, code in enumerate(to_fetch, 1):
    name = unique_etfs[code]
    print(f'  [{i:>2d}/{len(to_fetch)}] {code} {name} ...', end=' ', flush=True)
    all_bars = []
    cs = datetime(2020, 1, 1)
    ce = datetime(2026, 7, 29)
    while cs < ce:
        chunk_end = min(cs + timedelta(days=450), ce)
        bars = fetch_chunk(code, cs.strftime('%Y-%m-%d'), chunk_end.strftime('%Y-%m-%d'))
        for b in bars:
            if not all_bars or b['date'] != all_bars[-1]['date']:
                all_bars.append(b)
        cs = chunk_end + timedelta(days=1)
        time.sleep(0.08)
    if all_bars:
        d = {'code': code, 'name': name, 'first_date': all_bars[0]['date'],
             'last_date': all_bars[-1]['date'], 'n_days': len(all_bars), 'bars': all_bars}
        with open(os.path.join(DATA_DIR, f'etf_{code}.json'), 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
        print(f'OK {len(all_bars)} bars ({all_bars[0]["date"]}~{all_bars[-1]["date"]})')
    else:
        print('FAIL')

# ===== Step 2: Build merged table CSV =====
print(f'\n[TABLE] Building merged ETF table...')
table_rows = []
for code in sorted(unique_etfs.keys()):
    name = unique_etfs[code]
    path = os.path.join(DATA_DIR, f'etf_{code}.json')
    if os.path.exists(path):
        d = json.load(open(path, encoding='utf-8'))
        table_rows.append([code, name, d['first_date'], d['last_date'], str(d['n_days'])])
    else:
        table_rows.append([code, name, 'N/A', 'N/A', '0'])

csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ETF池_完整列表.csv')
with open(csv_path, 'w', encoding='utf-8-sig') as f:
    f.write('代码,名称,最早日期,最晚日期,数据条数\n')
    for row in table_rows:
        f.write(','.join(row) + '\n')
print(f'  Saved: {csv_path} ({len(table_rows)} ETFs)')
print(f'  Data ready: {sum(1 for r in table_rows if r[4] != "0")}/{len(table_rows)}')

# ===== Step 3: Run V4 backtest on full pool =====
print(f'\n[BACKTEST] V4 MA9/17 slp17 Trail=5% on {len(unique_etfs)} ETFs...')
ETF_CODES = sorted(unique_etfs.keys())
START = '2020-01-01'; END = '2026-07-29'
RF = 0.025; TD = 252; INIT = 10_000_000; MAX_POS = 2; TRAIL = 0.05
F_MA = 9; S_MA = 17; SL_MA = 17

def calc_ma(data, w):
    ma = []; n = len(data)
    for i in range(n):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1]) / w)
    return ma

def calc_slope(ma_series, lb):
    slopes = [float('nan')] * len(ma_series)
    for i in range(len(ma_series)):
        if i < lb: continue
        ys = ma_series[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys): continue
        n = len(ys); sx = sy = sxy = sxx = 0
        for j, y in enumerate(ys): sx += j; sy += y; sxy += j*y; sxx += j*j
        denom = n*sxx - sx*sx
        if denom > 0: slopes[i] = (n*sxy - sx*sy) / denom / ma_series[i] if ma_series[i] > 0 else 0
    return slopes

# Load ETFs
etfs = {}
for code in ETF_CODES:
    path = os.path.join(DATA_DIR, f'etf_{code}.json')
    if not os.path.exists(path): continue
    d = json.load(open(path, encoding='utf-8'))
    bars = []
    for b in d['bars']:
        dt = b['date']
        if len(dt) == 8: dt = dt[:4] + '-' + dt[4:6] + '-' + dt[6:8]
        if START <= dt <= END:
            bars.append({'date': dt, 'close': float(b['close'])})
    if bars: etfs[code] = {'name': d['name'], 'first_date': bars[0]['date'], 'bars': bars}

print(f'  Loaded {len(etfs)} ETFs for backtest')

# Precompute signals
all_sigs = {}
for code in ETF_CODES:
    if code not in etfs: continue
    bars = etfs[code]['bars']; closes = [b['close'] for b in bars]; n = len(bars)
    ma_f = calc_ma(closes, F_MA); ma_s = calc_ma(closes, S_MA)
    ma_sl = calc_ma(closes, SL_MA)
    slopes = calc_slope(ma_sl, SL_MA // 2)
    dates = [b['date'] for b in bars]
    sigs = {'trend': {}, 'ratio': {}}
    for i in range(n):
        d = dates[i]
        if not math.isnan(ma_f[i]) and not math.isnan(ma_s[i]) and ma_s[i] > 0:
            slope_ok = not math.isnan(slopes[i]) and slopes[i] > 0
            sigs['trend'][d] = ma_f[i] > ma_s[i] and slope_ok
            sigs['ratio'][d] = ma_f[i] / ma_s[i]
        else:
            sigs['trend'][d] = False; sigs['ratio'][d] = 1.0
    all_sigs[code] = sigs

# Backtest
codes = [c for c in ETF_CODES if c in etfs]
dm = {c: {b['date']: b for b in etfs[c]['bars']} for c in codes}
first_dates = {c: etfs[c]['first_date'] for c in codes}
all_dates = sorted(set.union(*[set(dm[c].keys()) for c in codes]))

cash = INIT; positions = {}; trades = []; dvs = []; per_slot = INIT / MAX_POS
tn = 0; trn = 0; fn = 0

for d in all_dates:
    available = [c for c in codes if first_dates[c] <= d]
    for c in list(positions.keys()):
        bar = dm[c].get(d)
        if not bar: continue
        px = bar['close']; pos = positions[c]
        if px > pos['peak']: pos['peak'] = px
        trend_on = all_sigs[c]['trend'].get(d, False)
        exit_reason = None
        if px <= pos['peak'] * (1 - TRAIL): exit_reason = 'trail'; trn += 1
        elif not trend_on: exit_reason = 'trend_off'; tn += 1
        if exit_reason:
            sell_val = pos['shares'] * px
            pnl = sell_val - pos['shares'] * pos['bp']
            trades.append({'code': c, 'bd': pos['entry_d'], 'sd': d, 'bp': pos['bp'], 'sp': px,
                           'ret': (px - pos['bp']) / pos['bp'], 'pnl': pnl, 'exit': exit_reason})
            cash += sell_val; del positions[c]
    slots = MAX_POS - len(positions)
    if slots > 0 and cash > 0:
        candidates = []
        for c in available:
            if c in positions: continue
            trend_on = all_sigs[c]['trend'].get(d, False)
            if trend_on:
                bar = dm[c].get(d)
                candidates.append((c, all_sigs[c]['ratio'].get(d, 1.0), bar['close'] if bar else 0))
        candidates.sort(key=lambda x: x[1], reverse=True)
        for c, ratio, px in candidates:
            if len(positions) >= MAX_POS or cash <= 0: break
            invest = min(cash, per_slot)
            if invest <= 0: continue
            shares = invest / px
            positions[c] = {'shares': shares, 'bp': px, 'peak': px, 'entry_d': d}
            cash -= invest
    pos_val = sum(pos['shares'] * dm[c].get(d, {}).get('close', 0) for c, pos in positions.items() if dm[c].get(d))
    dvs.append({'date': d, 'value': cash + pos_val})

ld = all_dates[-1]
for c in list(positions.keys()):
    bar = dm[c].get(ld)
    if bar:
        px = bar['close']; sell_val = positions[c]['shares'] * px
        pnl = sell_val - positions[c]['shares'] * positions[c]['bp']
        trades.append({'code': c, 'bd': positions[c]['entry_d'], 'sd': ld, 'bp': positions[c]['bp'], 'sp': px,
                       'ret': (px - positions[c]['bp']) / positions[c]['bp'], 'pnl': pnl, 'exit': 'final'})
        fn += 1; cash += sell_val
    del positions[c]

fv = cash; rets = []
for i in range(1, len(dvs)):
    p, c = dvs[i-1]['value'], dvs[i]['value']
    if p > 0: rets.append((c - p) / p)
if not rets: rets = [0.0]
pkv = dvs[0]['value']; mdd = 0.0
for dv in dvs:
    if dv['value'] > pkv: pkv = dv['value']
    dd = (pkv - dv['value']) / pkv
    if dd > mdd: mdd = dd
tr = (fv - INIT) / INIT
if len(rets) > 1:
    mu = sum(rets) / len(rets)
    sd = (sum((r - mu)**2 for r in rets) / (len(rets) - 1))**0.5
    av = sd * math.sqrt(TD); ar_ = mu * TD
    sh = (ar_ - RF) / av if av > 0 else 0
else: av = sh = ar_ = 0.0
ar = (1 + tr)**(TD / len(rets)) - 1 if tr > -1 else -1

sell_tr = [t for t in trades if t['exit'] in ('trail', 'trend_off', 'final')]
wins = sum(1 for t in sell_tr if t['ret'] > 0)
wr = wins / len(sell_tr) if sell_tr else 0

# ================================================================
print(f'\n\n{"="*80}')
print(f'  V4 BACKTEST on {len(etfs)} ETFs: MA{F_MA}/{S_MA} slp{SL_MA} Trail={TRAIL:.0%}')
print(f'{"="*80}')
print(f'  Sharpe:       {sh:.4f}')
print(f'  Total Return: {tr*100:.2f}%')
print(f'  Annual Return:{ar*100:.2f}%')
print(f'  Max DD:       {mdd*100:.2f}%')
print(f'  Total Trades: {len(sell_tr)}')
print(f'  Win Rate:     {wr*100:.1f}% ({wins}/{len(sell_tr)})')
print(f'  Trail exits:  {trn}')
print(f'  TrendOff:      {tn}')
print(f'  Final:        {fn}')

# Per ETF
ep = defaultdict(float); etr = defaultdict(int)
for t in sell_tr: ep[t['code']] += t['pnl']; etr[t['code']] += 1
top_etfs = sorted(ep.items(), key=lambda x: x[1], reverse=True)
print(f'\n  Top 10 ETFs by PnL:')
for c, pnl in top_etfs[:10]:
    name = etfs[c]['name'] if c in etfs else '?'
    print(f'    {c} {name:<15s} PnL={pnl:>12,.0f} Trd={etr[c]}')
print(f'\n  Bottom 5:')
for c, pnl in top_etfs[-5:]:
    name = etfs[c]['name'] if c in etfs else '?'
    print(f'    {c} {name:<15s} PnL={pnl:>12,.0f} Trd={etr[c]}')

# Best trades
sell_tr.sort(key=lambda x: x['ret'], reverse=True)
print(f'\n  Best 10 trades:')
for t in sell_tr[:10]:
    name = etfs[t['code']]['name'] if t['code'] in etfs else '?'
    print(f'    {t["code"]} {name:<15s} {t["bd"]}->{t["sd"]} {t["ret"]*100:>7.2f}% {t["exit"]}')

# Compare vs 9 ETF pool
print(f'\n  COMPARISON:')
print(f'  {"Pool":<20s} {"S":>7s} {"Ret":>8s} {"DD":>7s} {"Trd":>5s}')
print(f'  {"-"*50}')
print(f'  {"9 ETFs (V4 best)":<20s} {"0.879":>7s} {"138.0%":>8s} {"13.8%":>7s} {"155":>5s}')
pool_nm = str(len(etfs)) + ' ETFs (this)'
print(f'{pool_nm:<20s} {sh:>7.3f} {tr*100:>7.2f}% {mdd*100:>6.2f}% {len(sell_tr):>5d}')

print('\n  Done!')
