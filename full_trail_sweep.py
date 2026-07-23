"""Full Trail sweep: 8% to 50%"""
import json, os, math, csv
from collections import defaultdict

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")
RISK_FREE = 0.025; TRADING_DAYS = 252; INIT_CAP = 10_000_000
MA_WIN = 5; MAX_POSITIONS = 5
BUY_SLIPPAGE = 0.003; SELL_SLIPPAGE = 0.003
BUY_FEE = 0.00025; SELL_FEE = 0.00075
W_NET_MARGIN = 0.50; W_ROE = 0.37; W_REV_YOY = 0.13

def calc_ma(data, w):
    ma = []
    for i in range(len(data)):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma

def load_fundamentals():
    fund_data = defaultdict(list)
    for fname in sorted(os.listdir(FUND_DIR)):
        if not fname.endswith('.csv'): continue
        with open(os.path.join(FUND_DIR, fname), 'r', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                try:
                    fund_data[row['code'].strip()].append({
                        'pub_date': row['pub_date'].strip(),
                        'report_date': row['report_date'].strip(),
                        'roe': float(row['roe']), 'net_margin': float(row['net_margin']),
                        'rev_yoy': float(row['rev_yoy']), 'sector': row['sector'].strip(),
                    })
                except: pass
    for code in fund_data: fund_data[code].sort(key=lambda x: x['pub_date'])
    return fund_data

def get_latest(fund_data, date_str):
    latest = {}
    for code, reports in fund_data.items():
        valid = [r for r in reports if r['pub_date'] <= date_str]
        if valid: latest[code] = valid[-1]
    return latest

def compute_zscores(latest_fund):
    if len(latest_fund) < 3: return {}
    metrics = {'roe': [], 'net_margin': [], 'rev_yoy': []}
    codes = []
    for code, fund in latest_fund.items():
        codes.append(code)
        metrics['roe'].append(fund['roe']); metrics['net_margin'].append(fund['net_margin'])
        metrics['rev_yoy'].append(fund['rev_yoy'])
    stats = {}
    for key, vals in metrics.items():
        mu = sum(vals)/len(vals); var = sum((v-mu)**2 for v in vals)/len(vals)
        stats[key] = (mu, math.sqrt(var) if var > 0 else 1.0)
    scores = {}
    for i, code in enumerate(codes):
        z_roe = (metrics['roe'][i]-stats['roe'][0])/stats['roe'][1]
        z_nm = (metrics['net_margin'][i]-stats['net_margin'][0])/stats['net_margin'][1]
        z_ry = (metrics['rev_yoy'][i]-stats['rev_yoy'][0])/stats['rev_yoy'][1]
        scores[code] = {'score': z_nm*W_NET_MARGIN + z_roe*W_ROE + z_ry*W_REV_YOY,
                       'sector': latest_fund[code]['sector']}
    return scores

def backtest(buy_thr, trail_pct, stocks_data, fund_data, common_dates):
    common_set = set(common_dates)
    precomputed = {}
    for code, info in stocks_data.items():
        bars = [b for b in info['bars'] if b['date'] in common_set]
        closes = [b['close'] for b in bars]
        ma5 = calc_ma(closes, MA_WIN)
        devs = []
        for i, bar in enumerate(bars):
            ma = ma5[i]
            if math.isnan(ma) or ma == 0: devs.append(float('nan'))
            else: devs.append((bar['close']-ma)/abs(ma))
        precomputed[code] = {'bars': bars, 'closes': closes, 'devs': devs,
                            'sector': info['sector'], 'name': info['name']}

    per_stock_cap = INIT_CAP / MAX_POSITIONS
    holdings = {}; cash_pool = INIT_CAP; trades = []
    daily_values = []; current_scores = {}

    for day_idx, date_str in enumerate(common_dates):
        new_fund = get_latest(fund_data, date_str)
        if new_fund:
            new_scores = compute_zscores(new_fund)
            if new_scores: current_scores = new_scores

        sell_events = []
        for code, h in list(holdings.items()):
            pc = precomputed[code]; px = pc['bars'][day_idx]['close']
            if day_idx > h['buy_day']:
                if px > h['peak']: h['peak'] = px
                if px <= h['peak'] * (1 - trail_pct):
                    sell_px = px * (1 - SELL_SLIPPAGE)
                    gross = h['pos'] * sell_px
                    net_cash = gross - gross * SELL_FEE
                    ret = (sell_px - h['buy_px']) / h['buy_px']
                    trades.append({'code': code, 'ret': ret, 'exit': 'trail'})
                    sell_events.append((code, net_cash))

        for code, cr in sell_events:
            cash_pool += cr; del holdings[code]

        held_sectors = set(h['sector'] for h in holdings.values())
        held_codes = set(holdings.keys())

        eligible = []
        for code, sc in sorted(current_scores.items(), key=lambda x: x[1]['score'], reverse=True):
            if code in held_codes or sc['sector'] in held_sectors: continue
            if code not in precomputed: continue
            dev = precomputed[code]['devs'][day_idx]
            if not math.isnan(dev) and dev < buy_thr:
                eligible.append((code, sc['sector']))

        while len(holdings) < MAX_POSITIONS and eligible and cash_pool >= per_stock_cap:
            code, sector = eligible.pop(0)
            pc = precomputed[code]; px = pc['bars'][day_idx]['close']
            buy_px = px * (1 + BUY_SLIPPAGE)
            fee = per_stock_cap * BUY_FEE
            pos = (per_stock_cap - fee) / buy_px
            holdings[code] = {'pos': pos, 'buy_px': buy_px, 'peak': px,
                             'buy_day': day_idx, 'buy_date': date_str, 'sector': sector}
            cash_pool -= per_stock_cap; held_sectors.add(sector)

        pv = cash_pool
        for code, h in holdings.items():
            pv += h['pos'] * precomputed[code]['bars'][day_idx]['close']
        daily_values.append({'value': pv, 'positions': len(holdings)})

    for code, h in list(holdings.items()):
        pc = precomputed[code]; fp = pc['bars'][-1]['close']
        sell_px = fp * (1 - SELL_SLIPPAGE)
        gross = h['pos'] * sell_px
        net_cash = gross - gross * SELL_FEE
        ret = (sell_px - h['buy_px']) / h['buy_px']
        trades.append({'code': code, 'ret': ret, 'exit': 'final'})
        cash_pool += net_cash; del holdings[code]

    fv = daily_values[-1]['value']
    rets = []
    for i in range(1, len(daily_values)):
        p, c = daily_values[i-1]['value'], daily_values[i]['value']
        if p > 0: rets.append((c-p)/p)
    peak_v = daily_values[0]['value']; mdd = 0.0
    for dv in daily_values:
        if dv['value'] > peak_v: peak_v = dv['value']
        dd = (peak_v-dv['value'])/peak_v
        if dd > mdd: mdd = dd
    tr = (fv-INIT_CAP)/INIT_CAP
    if len(rets) > 1:
        mu = sum(rets)/len(rets); sd = (sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av = sd*math.sqrt(TRADING_DAYS); ar_ = mu*TRADING_DAYS
        sh = (ar_-RISK_FREE)/av if av>0 else 0
    else: av = sh = ar_ = 0.0
    cagr = (1+tr)**(TRADING_DAYS/max(len(rets),1))-1 if tr>-1 else -1
    cm = cagr/mdd if mdd>0 else float('inf')
    wins = sum(1 for t in trades if t['ret']>0); wr = wins/len(trades) if trades else 0
    avg_pos = sum(dv['positions'] for dv in daily_values)/len(daily_values)

    return {'buy_thr': buy_thr, 'trail_pct': trail_pct, 'tr': tr, 'ar': cagr, 'av': av,
            'sh': sh, 'mdd': mdd, 'cm': cm, 'np': len(trades), 'wr': wr,
            'avg_positions': avg_pos, 'fv': fv}

def main():
    # Load
    fund_data = load_fundamentals()
    stocks = {}
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'): continue
        with open(os.path.join(DATA_DIR, fname), 'r', encoding='utf-8') as f:
            data = json.load(f)
        stocks[data['code']] = {'name': data['name'], 'sector': data['sector'], 'bars': data['bars']}

    qualified = {c: s for c, s in stocks.items() if c in fund_data and len(s['bars']) >= 500}
    date_sets = [set(b['date'] for b in s['bars']) for s in qualified.values()]
    common_dates = sorted(date_sets[0].intersection(*date_sets[1:]))
    print(f"{len(qualified)} stocks, {len(common_dates)} days")

    buy_thresholds = [-0.030, -0.035, -0.040, -0.045, -0.050, -0.055]
    trail_pcts = [0.08, 0.10, 0.12, 0.15, 0.17, 0.20, 0.22, 0.25, 0.27, 0.30, 0.35, 0.40, 0.45, 0.50]

    total = len(buy_thresholds)*len(trail_pcts)
    print(f"Grid: {len(buy_thresholds)}x{len(trail_pcts)} = {total} scenarios\n")

    all_results = []; done = 0
    for buy_thr in buy_thresholds:
        for trail_pct in trail_pcts:
            done += 1
            r = backtest(buy_thr, trail_pct, qualified, fund_data, common_dates)
            all_results.append(r)
            print(f"[{done:>3d}/{total}] DEV<{buy_thr:.1%} Tr{trail_pct:.0%} | "
                  f"Sh={r['sh']:>7.4f} | Ret={r['tr']*100:>7.1f}% | "
                  f"DD={r['mdd']*100:>5.1f}% | Trd={r['np']:>3d} | "
                  f"Win={r['wr']*100:>4.0f}% | Pos={r['avg_positions']:.1f}")

    sorted_all = sorted(all_results, key=lambda r: r['sh'], reverse=True)
    global_max = max(r['sh'] for r in all_results)

    print(f"\n{'='*110}")
    print(f"  TOP 20 + BOTTOM 5")
    print(f"{'='*110}")
    print(f"  {'#':<3s} {'策略':<26s} {'夏普':>7s} {'总收益':>8s} {'年化':>7s} "
          f"{'回撤':>7s} {'卡玛':>7s} {'交易':>5s} {'胜率':>6s} {'均持仓':>6s}")
    print(f"  {'-'*100}")

    for rank, r in enumerate(sorted_all[:20], 1):
        tag = " << GLOBAL MAX" if r['sh'] == global_max else ""
        print(f"  {rank:<3d} DEV<{r['buy_thr']:.1%} Trail{r['trail_pct']:.0%}     "
              f"{r['sh']:>7.4f} {r['tr']*100:>7.1f}% {r['ar']*100:>6.1f}% "
              f"{r['mdd']*100:>6.2f}% {r['cm']:>7.3f} "
              f"{r['np']:>5d} {r['wr']*100:>5.1f}% {r['avg_positions']:>5.1f}{tag}")
    print(f"  ...")
    for rank, r in enumerate(sorted_all[-5:], len(sorted_all)-4):
        print(f"  {rank:<3d} DEV<{r['buy_thr']:.1%} Trail{r['trail_pct']:.0%}     "
              f"{r['sh']:>7.4f} {r['tr']*100:>7.1f}% {r['ar']*100:>6.1f}% "
              f"{r['mdd']*100:>6.2f}% {r['cm']:>7.3f} "
              f"{r['np']:>5d} {r['wr']*100:>5.1f}% {r['avg_positions']:>5.1f}")

    # Heatmap
    print(f"\n\n  --- 夏普热力图 (8% -> 50%) ---")
    header = "           "
    for t in trail_pcts: header += f" T{int(t*100):<4d}"
    print(f"  {header}")
    for buy_thr in buy_thresholds:
        row = f"  DEV<{buy_thr:.1%}"
        for trail_pct in trail_pcts:
            matches = [x for x in all_results if abs(x['buy_thr']-buy_thr)<0.001 and abs(x['trail_pct']-trail_pct)<0.001]
            if matches:
                sh = matches[0]['sh']
                if sh == global_max: v = f" *{sh:.2f}*"
                elif sh >= 2.0: v = f"  {sh:.2f} "
                elif sh >= 1.5: v = f"  {sh:.2f} "
                else: v = f"  {sh:.2f} "
                row += v
            else:
                row += "   ??? "
        print(row)

    # Trail curve
    print(f"\n\n  --- 均夏普 vs Trail（6个DEV均值）---")
    for trail_pct in trail_pcts:
        trs = [r for r in all_results if abs(r['trail_pct']-trail_pct)<0.001]
        avg_sh = sum(r['sh'] for r in trs)/len(trs)
        max_sh = max(r['sh'] for r in trs)
        avg_ret = sum(r['tr'] for r in trs)/len(trs)*100
        avg_trd = sum(r['np'] for r in trs)/len(trs)
        bar = '#' * int(avg_sh * 25)
        pk = ' <-- MAX' if max_sh == global_max else ''
        print(f"  T{trail_pct:>3.0%}  meanSh={avg_sh:.4f}  maxSh={max_sh:.4f}  "
              f"meanRet={avg_ret:.1f}%  meanTrd={avg_trd:.0f}  {bar}{pk}")

    # Best DEV analysis
    print(f"\n\n  --- 按DEV阈值 ---")
    for buy_thr in buy_thresholds:
        trs = [r for r in all_results if abs(r['buy_thr']-buy_thr)<0.001]
        avg_sh = sum(r['sh'] for r in trs)/len(trs)
        max_sh = max(r['sh'] for r in trs)
        best_trail = [r for r in trs if r['sh']==max_sh][0]
        print(f"  DEV<{buy_thr:.1%}  meanSh={avg_sh:.4f}  maxSh={max_sh:.4f}  "
              f"@Trail{best_trail['trail_pct']:.0%}")

    print(f"\n  Done!")

if __name__ == '__main__':
    main()
