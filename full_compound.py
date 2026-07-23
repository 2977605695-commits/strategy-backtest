"""
70只全股票复利回测 · 新股渐进加入
=========================================
- 2020-01-02 起，初始可用股票 ~44只
- 新股上市日自动加入池子
- 上市前赋 0 值（不参与）
- 复利：利润滚动再投资
- 对比稳健版 vs 进取版
"""
import json, os, math, csv
from collections import defaultdict
from datetime import datetime

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")
RISK_FREE = 0.025; TD = 252
MA_WIN = 5; MP = 5
BS = 0.003; SS = 0.003; BF = 0.00025; SF = 0.00075
WN = 0.50; WR_ = 0.37; WY = 0.13

# 两套配方
CONFIGS = [
    ("稳健版 F60d", -0.045, 0.45, 60),
    ("进取版 F90d", -0.050, 0.30, 90),
]

def calc_ma(d, w):
    m = []
    for i in range(len(d)):
        if i < w - 1: m.append(float('nan'))
        else: m.append(sum(d[i - w + 1:i + 1]) / w)
    return m

def load_fund():
    fd = defaultdict(list)
    for fn in sorted(os.listdir(FUND_DIR)):
        if not fn.endswith('.csv'): continue
        with open(os.path.join(FUND_DIR, fn), 'r', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                try:
                    fd[row['code'].strip()].append({
                        'pub_date': row['pub_date'].strip(),
                        'report_date': row['report_date'].strip(),
                        'roe': float(row['roe']),
                        'net_margin': float(row['net_margin']),
                        'rev_yoy': float(row['rev_yoy']),
                    })
                except:
                    pass
    for c in fd: fd[c].sort(key=lambda x: x['pub_date'])
    return fd

def get_latest(fd, ds):
    latest = {}
    for c, reps in fd.items():
        valid = [r for r in reps if r['pub_date'] <= ds]
        if valid: latest[c] = valid[-1]
    return latest

def zscores(lf):
    if len(lf) < 3: return {}
    mets = {'roe': [], 'net_margin': [], 'rev_yoy': []}
    codes = []
    for c, fund in lf.items():
        codes.append(c)
        mets['roe'].append(fund['roe'])
        mets['net_margin'].append(fund['net_margin'])
        mets['rev_yoy'].append(fund['rev_yoy'])
    stats = {}
    for k, vals in mets.items():
        mu = sum(vals) / len(vals)
        var = sum((v - mu) ** 2 for v in vals) / len(vals)
        stats[k] = (mu, math.sqrt(var) if var > 0 else 1.0)
    scores = {}
    for i, c in enumerate(codes):
        zr = (mets['roe'][i] - stats['roe'][0]) / stats['roe'][1]
        zn = (mets['net_margin'][i] - stats['net_margin'][0]) / stats['net_margin'][1]
        zy = (mets['rev_yoy'][i] - stats['rev_yoy'][0]) / stats['rev_yoy'][1]
        scores[c] = zn * WN + zr * WR_ + zy * WY
    return scores


def run(label, buy_thr, trail_pct, max_hold, all_stocks, fd):
    """复利回测：全量日期，新股渐进加入"""

    # --- 构建全量日期轴 ---
    all_dates = set()
    for s in all_stocks.values():
        for b in s['bars']:
            all_dates.add(b['date'])
    all_dates = sorted(all_dates)
    # Filter to 2020-01-02 to 2026-07-01
    all_dates = [d for d in all_dates if '2020-01-02' <= d <= '2026-07-01']

    # --- 预计算每只股票的 bars 按日期索引 ---
    # 为每只股票建立 date -> bar 的映射
    stock_maps = {}
    stock_first_date = {}
    for code, s in all_stocks.items():
        bar_map = {}
        for b in s['bars']:
            bar_map[b['date']] = b
        stock_maps[code] = bar_map
        stock_first_date[code] = s['bars'][0]['date']

    # --- 逐日遍历 ---
    INIT_CAP = 10_000_000
    cash = INIT_CAP
    total_cap = INIT_CAP  # 总资金池 = cash + holdings_value
    holdings = {}  # code -> {pos, buy_px, peak, buy_day, sector}
    trades = []
    daily_values = []
    current_scores = {}

    trl = 0; tim = 0; fin = 0

    for di, ds in enumerate(all_dates):
        # --- 确定当日可用的股票池 ---
        available = {c: s for c, s in all_stocks.items()
                     if stock_first_date[c] <= ds and c in stock_maps and ds in stock_maps[c]}

        # --- 基本面更新 ---
        nf = get_latest(fd, ds)
        if nf:
            # 只对当日有价格数据的股票计算得分
            nf_available = {c: v for c, v in nf.items() if c in available}
            ns = zscores(nf_available)
            if ns: current_scores = ns

        # --- Trail / Time 检查 ---
        sell_events = []
        for c, h in list(holdings.items()):
            if c not in stock_maps or ds not in stock_maps[c]:
                continue
            bar = stock_maps[c][ds]
            px = bar['close']
            ext = None
            if di > h['buy_day']:
                if px > h['peak']: h['peak'] = px
                if px <= h['peak'] * (1 - trail_pct):
                    ext = 'trail'
            if not ext and max_hold and di - h['buy_day'] >= max_hold:
                ext = 'time'
            if ext:
                sp = px * (1 - SS)
                gross = h['pos'] * sp
                nc = gross - gross * SF
                ret = (sp - h['buy_px']) / h['buy_px']
                trades.append({'name': all_stocks[c]['name'], 'ret': ret, 'exit': ext,
                               'days': di - h['buy_day'], 'date': ds})
                if ext == 'trail': trl += 1
                else: tim += 1
                cash += nc
                sell_events.append(c)

        for c in sell_events:
            del holdings[c]

        # --- 更新总资金池 ---
        holdings_value = 0
        for c, h in holdings.items():
            if c in stock_maps and ds in stock_maps[c]:
                holdings_value += h['pos'] * stock_maps[c][ds]['close']
        total_cap = cash + holdings_value

        # --- 动态仓位大小 ---
        per_stock = total_cap / MP

        # --- 买入 ---
        held_sectors = set(h['sector'] for h in holdings.values())
        held_codes = set(holdings.keys())
        eligible = []
        for c, sc in sorted(current_scores.items(), key=lambda x: x[1], reverse=True):
            if c in held_codes: continue
            if c not in available: continue
            sector = all_stocks[c]['sector']
            if sector in held_sectors: continue
            bar = stock_maps[c][ds]
            px = bar['close']
            ma5_vals = [stock_maps[c][d]['close'] for d in all_dates[max(0, di - 4):di + 1]
                       if c in stock_maps and d in stock_maps[c]]
            if len(ma5_vals) < MA_WIN: continue
            ma5 = sum(ma5_vals) / MA_WIN
            if ma5 == 0: continue
            dev = (px - ma5) / abs(ma5)
            if dev < buy_thr:
                eligible.append((c, sc, sector))

        while len(holdings) < MP and eligible and cash >= per_stock:
            c, sc, sector = eligible.pop(0)
            bar = stock_maps[c][ds]
            px = bar['close']
            bp = px * (1 + BS)
            fee = per_stock * BF
            pos = (per_stock - fee) / bp
            holdings[c] = {
                'pos': pos, 'buy_px': bp, 'peak': px,
                'buy_day': di, 'sector': sector,
            }
            cash -= per_stock
            held_sectors.add(sector)

        # --- 记录当日净值 ---
        hv = 0
        for c, h in holdings.items():
            if c in stock_maps and ds in stock_maps[c]:
                hv += h['pos'] * stock_maps[c][ds]['close']
        daily_values.append({
            'date': ds,
            'value': cash + hv,
            'positions': len(holdings),
            'available': len(available),
        })

    # --- 期末平仓 ---
    for c, h in list(holdings.items()):
        if c in stock_maps and all_dates[-1] in stock_maps[c]:
            fp = stock_maps[c][all_dates[-1]]['close']
        else:
            continue
        sp = fp * (1 - SS)
        gross = h['pos'] * sp
        nc = gross - gross * SF
        ret = (sp - h['buy_px']) / h['buy_px']
        trades.append({'name': all_stocks[c]['name'], 'ret': ret, 'exit': 'final',
                       'days': len(all_dates) - 1 - h['buy_day'], 'date': all_dates[-1]})
        fin += 1
        cash += nc
        del holdings[c]

    # --- 绩效计算 ---
    fv = daily_values[-1]['value']
    rets = []
    for i in range(1, len(daily_values)):
        p, c = daily_values[i - 1]['value'], daily_values[i]['value']
        if p > 0: rets.append((c - p) / p)

    pk = daily_values[0]['value']
    mdd = 0.0
    for dv in daily_values:
        if dv['value'] > pk: pk = dv['value']
        dd = (pk - dv['value']) / pk
        if dd > mdd: mdd = dd

    tr = (fv - INIT_CAP) / INIT_CAP
    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
        av = sd * math.sqrt(TD)
        ar_ = mu * TD
        sh = (ar_ - RISK_FREE) / av if av > 0 else 0
    else:
        av = sh = ar_ = 0.0
    cagr = (1 + tr) ** (TD / max(len(rets), 1)) - 1 if tr > -1 else -1
    wins = sum(1 for t in trades if t['ret'] > 0)
    wr = wins / len(trades) if trades else 0

    # --- 年度表现 ---
    yearly = defaultdict(lambda: {'start': None, 'end': None})
    for dv in daily_values:
        yr = dv['date'][:4]
        if yearly[yr]['start'] is None:
            yearly[yr]['start'] = dv['value']
        yearly[yr]['end'] = dv['value']

    return {
        'label': label, 'tr': tr, 'cagr': cagr, 'sh': sh, 'mdd': mdd,
        'np': len(trades), 'wr': wr,
        'trl': trl, 'tim': tim, 'fin': fin,
        'fv': fv, 'days': len(all_dates),
        'daily_values': daily_values,
        'yearly': yearly,
        'trades': trades,
    }


def main():
    print("=" * 90)
    print("  70只全股票复利回测 · 新股渐进加入 · 2020-01-02 → 2026-07-01")
    print("=" * 90)

    # Load all stocks
    all_stocks = {}
    for fn in sorted(os.listdir(DATA_DIR)):
        if not fn.endswith('.json') or fn.startswith('_'): continue
        with open(os.path.join(DATA_DIR, fn), 'r', encoding='utf-8') as f:
            d = json.load(f)
        all_stocks[d['code']] = {
            'name': d['name'],
            'sector': d.get('sector', '?'),
            'bars': d['bars'],
        }

    # IPO timeline
    ipos = []
    for code, s in all_stocks.items():
        ipos.append((s['bars'][0]['date'], code, s['name']))
    ipos.sort()

    print(f"\n  总股票数: {len(all_stocks)}")
    print(f"  初始可用 (2020-01-02): {sum(1 for d,c,n in ipos if d <= '2020-01-03')} 只")
    print(f"  新股上市节奏:")
    for yr in ['2020','2021','2022','2023','2024','2025','2026']:
        cnt = sum(1 for d,c,n in ipos if d[:4] == yr)
        if cnt:
            names = [n for d,c,n in ipos if d[:4] == yr]
            print(f"    {yr}: +{cnt}只 ({', '.join(names[:5])}{'...' if cnt>5 else ''})")

    fd = load_fund()

    # Run both configs
    results = []
    for label, buy_thr, trail_pct, max_hold in CONFIGS:
        print(f"\n  [RUNNING] {label}...")
        r = run(label, buy_thr, trail_pct, max_hold, all_stocks, fd)
        results.append(r)

    # ================================================================
    print(f"\n{'=' * 90}")
    print(f"  复利回测结果对比")
    print(f"{'=' * 90}")

    for r in results:
        print(f"\n  --- {r['label']} ---")
        print(f"  回测天数: {r['days']} 天")
        print(f"  夏普: {r['sh']:.4f} | 总收益: {r['tr']*100:.1f}% | 年化: {r['cagr']*100:.1f}%")
        print(f"  最大回撤: {r['mdd']*100:.1f}% | 交易: {r['np']}笔 | 胜率: {r['wr']*100:.0f}%")
        print(f"  Trail退出: {r['trl']} | 时限退出: {r['tim']} | 期末: {r['fin']}")

        print(f"\n  年度表现:")
        for yr in sorted(r['yearly'].keys()):
            y = r['yearly'][yr]
            if y['start'] and y['start'] > 0:
                yr_ret = (y['end'] / y['start'] - 1) * 100
                bar = '#' * max(1, int(abs(yr_ret) / 5))
                sign = '+' if yr_ret >= 0 else ''
                print(f"    {yr}: {sign}{yr_ret:.1f}%  {bar}")

        # Trade summary
        print(f"\n  最近10笔交易:")
        for t in r['trades'][-10:]:
            tag = 'LOSS' if t['ret'] < 0 else ''
            print(f"    {t.get('date','?')} {t['name']:<10s} {t['exit']:<6s} {t['ret']*100:>+7.1f}% {t['days']}d {tag}")

    # Comparison table
    print(f"\n\n{'=' * 90}")
    print(f"  对比汇总")
    print(f"{'=' * 90}")
    print(f"  {'指标':<16s} {'稳健版 F60d':>18s} {'进取版 F90d':>18s}")
    print(f"  {'-' * 54}")
    metrics = [
        ('夏普比率', 'sh', '.4f'),
        ('总收益率', 'tr', '.1%'),
        ('年化收益率', 'cagr', '.1%'),
        ('最大回撤', 'mdd', '.1%'),
        ('交易笔数', 'np', 'd'),
        ('胜率', 'wr', '.0%'),
        ('回测天数', 'days', 'd'),
    ]
    for name, key, fmt in metrics:
        v0 = results[0][key]
        v1 = results[1][key]
        if key in ('tr', 'cagr', 'mdd'):
            s0 = f"{v0*100:.1f}%"
            s1 = f"{v1*100:.1f}%"
        elif key == 'wr':
            s0 = f"{v0*100:.0f}%"
            s1 = f"{v1*100:.0f}%"
        elif key == 'sh':
            s0 = f"{v0:.4f}"
            s1 = f"{v1:.4f}"
        else:
            s0 = f"{int(v0)}"
            s1 = f"{int(v1)}"
        print(f"  {name:<16s} {s0:>18s} {s1:>18s}")

    print(f"\n  Done!")


if __name__ == '__main__':
    main()
