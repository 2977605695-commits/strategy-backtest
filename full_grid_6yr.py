"""
完整6年(2020-2026) DEV×Trail 网格搜索 + 持仓对比
44只老股票, 1451个交易日
"""
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
                        'pub_date': row['pub_date'].strip(), 'report_date': row['report_date'].strip(),
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
    buys = []  # Track all buy events for comparison

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
                    gross = h['pos'] * sell_px; net_cash = gross - gross * SELL_FEE
                    ret = (sell_px - h['buy_px']) / h['buy_px']
                    trades.append({'name': pc['name'], 'ret': ret, 'exit': 'trail',
                                  'buy_date': h['buy_date'], 'sell_date': date_str})
                    sell_events.append((code, net_cash))

        for code, cr in sell_events: cash_pool += cr; del holdings[code]

        held_sectors = set(h['sector'] for h in holdings.values())
        held_codes = set(holdings.keys())
        eligible = []
        for code, sc in sorted(current_scores.items(), key=lambda x: x[1]['score'], reverse=True):
            if code in held_codes or sc['sector'] in held_sectors: continue
            if code not in precomputed: continue
            dev = precomputed[code]['devs'][day_idx]
            if not math.isnan(dev) and dev < buy_thr:
                eligible.append((code, sc['score'], sc['sector']))

        while len(holdings) < MAX_POSITIONS and eligible and cash_pool >= per_stock_cap:
            code, score, sector = eligible.pop(0)
            pc = precomputed[code]; px = pc['bars'][day_idx]['close']
            buy_px = px * (1 + BUY_SLIPPAGE)
            fee = per_stock_cap * BUY_FEE; pos = (per_stock_cap - fee) / buy_px
            holdings[code] = {'pos': pos, 'buy_px': buy_px, 'peak': px,
                             'buy_day': day_idx, 'buy_date': date_str, 'sector': sector}
            cash_pool -= per_stock_cap; held_sectors.add(sector)
            buys.append({'date': date_str, 'name': pc['name'], 'sector': sector,
                        'dev': precomputed[code]['devs'][day_idx], 'score': score})

        pv = cash_pool
        for code, h in holdings.items():
            pv += h['pos'] * precomputed[code]['bars'][day_idx]['close']
        daily_values.append({'date': date_str, 'value': pv, 'positions': len(holdings)})

    for code, h in list(holdings.items()):
        pc = precomputed[code]; fp = pc['bars'][-1]['close']
        sell_px = fp * (1 - SELL_SLIPPAGE)
        gross = h['pos'] * sell_px; net_cash = gross - gross * SELL_FEE
        ret = (sell_px - h['buy_px']) / h['buy_px']
        trades.append({'name': pc['name'], 'ret': ret, 'exit': 'final',
                      'buy_date': h['buy_date'], 'sell_date': common_dates[-1]})
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
    if len(rets)>1:
        mu = sum(rets)/len(rets); sd = (sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av = sd*math.sqrt(TRADING_DAYS); ar_ = mu*TRADING_DAYS
        sh = (ar_-RISK_FREE)/av if av>0 else 0
    else: av = sh = ar_ = 0.0
    cagr = (1+tr)**(TRADING_DAYS/max(len(rets),1))-1 if tr>-1 else -1
    cm = cagr/mdd if mdd>0 else float('inf')
    wins = sum(1 for t in trades if t['ret']>0); wr = wins/len(trades) if trades else 0
    avg_pos = sum(dv['positions'] for dv in daily_values)/len(daily_values)

    # Aggregate stocks held
    all_stocks_bought = list(set(b['name'] for b in buys))

    return {'buy_thr': buy_thr, 'trail_pct': trail_pct, 'tr': tr, 'ar': cagr, 'av': av,
            'sh': sh, 'mdd': mdd, 'cm': cm, 'np': len(trades), 'wr': wr,
            'avg_positions': avg_pos, 'fv': fv, 'trades': trades, 'buys': buys,
            'stocks_bought': sorted(all_stocks_bought)}

def main():
    fund_data = load_fundamentals()
    stocks = {}
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'): continue
        with open(os.path.join(DATA_DIR, fname), 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data['bars'][0]['date'] <= '2020-01-03' and len(data['bars']) >= 1500:
            stocks[data['code']] = {'name': data['name'], 'sector': data['sector'], 'bars': data['bars']}

    qualified = {c: s for c, s in stocks.items() if c in fund_data}
    date_sets = [set(b['date'] for b in s['bars']) for s in qualified.values()]
    common_dates = sorted(date_sets[0].intersection(*date_sets[1:]))

    print(f"{len(qualified)} stocks, {len(common_dates)} days ({common_dates[0]} -> {common_dates[-1]})")

    buy_thresholds = [-0.030, -0.035, -0.040, -0.045, -0.050, -0.055]
    trail_pcts = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]

    total = len(buy_thresholds)*len(trail_pcts)
    print(f"Grid: {len(buy_thresholds)}x{len(trail_pcts)} = {total} scenarios\n")

    all_results = []; done = 0
    for buy_thr in buy_thresholds:
        for trail_pct in trail_pcts:
            done += 1
            r = backtest(buy_thr, trail_pct, qualified, fund_data, common_dates)
            all_results.append(r)
            print(f"[{done:>2d}/{total}] DEV<{buy_thr:.1%} Tr{trail_pct:.0%} | "
                  f"Sh={r['sh']:>7.4f} Ret={r['tr']*100:>6.0f}% DD={r['mdd']*100:>5.1f}% "
                  f"Trd={r['np']:>3d} Win={r['wr']*100:>4.0f}% Pos={r['avg_positions']:.1f} "
                  f"Stocks={len(r['stocks_bought'])}")

    sorted_all = sorted(all_results, key=lambda r: r['sh'], reverse=True)
    global_max = max(r['sh'] for r in all_results)

    # ================================================================
    print(f"\n{'='*110}")
    print(f"  全排名 (44股 x 1451天 x 6年)")
    print(f"{'='*110}")
    print(f"  {'#':<3s} {'策略':<26s} {'夏普':>7s} {'总收益':>7s} {'年化':>6s} "
          f"{'回撤':>6s} {'卡玛':>6s} {'交易':>4s} {'胜率':>5s} {'持仓':>4s} {'股票数':>5s}")
    print(f"  {'-'*98}")

    for rank, r in enumerate(sorted_all, 1):
        tag = " << MAX" if r['sh'] == global_max else ""
        print(f"  {rank:<3d} DEV<{r['buy_thr']:.1%} Trail{r['trail_pct']:.0%}     "
              f"{r['sh']:>7.4f} {r['tr']*100:>6.0f}% {r['ar']*100:>5.1f}% "
              f"{r['mdd']*100:>5.2f}% {r['cm']:>6.3f} "
              f"{r['np']:>4d} {r['wr']*100:>4.0f}% {r['avg_positions']:>4.1f} "
              f"{len(r['stocks_bought']):>5d}{tag}")

    # Heatmap
    print(f"\n\n  --- 夏普热力图 (6年数据) ---")
    header = "           "
    for t in trail_pcts: header += f" T{int(t*100):<4d}"
    print(f"  {header}")
    for buy_thr in buy_thresholds:
        row = f"  DEV<{buy_thr:.1%}"
        for trail_pct in trail_pcts:
            matches = [x for x in all_results if abs(x['buy_thr']-buy_thr)<0.001 and abs(x['trail_pct']-trail_pct)<0.001]
            if matches:
                sh = matches[0]['sh']
                if sh == global_max: row += f" *{sh:.2f}*"
                elif sh >= 1.0: row += f"  {sh:.2f} "
                else: row += f"  {sh:.2f} "
        print(row)

    # Best 3: stock comparison
    print(f"\n\n{'='*90}")
    print(f"  TOP 3 策略持仓对比")
    print(f"{'='*90}")

    for rank, r in enumerate(sorted_all[:3], 1):
        print(f"\n  #{rank} DEV<{r['buy_thr']:.1%} Trail{r['trail_pct']:.0%} "
              f"Sharpe={r['sh']:.4f} Ret={r['tr']*100:.0f}% "
              f"买入{len(r['buys'])}次, 涉及{len(r['stocks_bought'])}只股票")

        # Show buys by year
        buys_by_year = defaultdict(list)
        for b in r['buys']:
            yr = b['date'][:4]
            buys_by_year[yr].append(b)

        for yr in sorted(buys_by_year):
            stocks_yr = buys_by_year[yr]
            names = [f"{b['name']}(DEV={b['dev']:.1%})" for b in stocks_yr]
            print(f"    {yr}: {', '.join(names)}")

        # Hold duration stats
        trail_days = [t.get('days_held',0) for t in r.get('trades',[]) if t.get('exit')=='trail']
        final_days = [t.get('days_held',0) for t in r.get('trades',[]) if t.get('exit')=='final']
        print(f"    Trail退出均持{sum(trail_days)/max(len(trail_days),1):.0f}天, "
              f"Final均持{sum(final_days)/max(len(final_days),1):.0f}天")

    # Compare stock overlap between top strategies
    print(f"\n\n{'='*90}")
    print(f"  TOP 3 持股重叠分析")
    print(f"{'='*90}")
    for i in range(3):
        for j in range(i+1, 3):
            set_i = set(sorted_all[i]['stocks_bought'])
            set_j = set(sorted_all[j]['stocks_bought'])
            overlap = set_i & set_j
            only_i = set_i - set_j
            only_j = set_j - set_i
            print(f"\n  #{i+1} vs #{j+1}: 重叠{len(overlap)}只")
            if overlap:
                print(f"    共同持有: {', '.join(sorted(overlap)[:10])}")
            if only_i:
                print(f"    仅#{i+1}: {', '.join(sorted(only_i)[:10])}")
            if only_j:
                print(f"    仅#{j+1}: {', '.join(sorted(only_j)[:10])}")

    print(f"\n  Done!")

if __name__ == '__main__':
    main()
