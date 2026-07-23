"""
完整6年回测(2020-2026): 稳重型 vs 进攻型
只使用2020年前上市的老股票
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

STRATEGIES = [
    ('CONSERVATIVE', -0.035, 0.35),
    ('OFFENSIVE', -0.055, 0.45),
]

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
    daily_values = []; current_scores = {}; events = []

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
                    events.append({'date': date_str, 'type': 'SELL', 'stock': pc['name'],
                                  'buy_px': h['buy_px'], 'sell_px': sell_px, 'ret': ret,
                                  'buy_date': h['buy_date'], 'peak': h['peak'],
                                  'days': day_idx - h['buy_day'], 'sector': h['sector']})
                    trades.append({'name': pc['name'], 'ret': ret, 'exit': 'trail'})
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
            events.append({'date': date_str, 'type': 'BUY', 'stock': pc['name'],
                          'price': buy_px, 'dev': precomputed[code]['devs'][day_idx],
                          'score': score, 'sector': sector})

        pv = cash_pool
        for code, h in holdings.items():
            pv += h['pos'] * precomputed[code]['bars'][day_idx]['close']
        daily_values.append({'date': date_str, 'value': pv, 'positions': len(holdings)})

    for code, h in list(holdings.items()):
        pc = precomputed[code]; fp = pc['bars'][-1]['close']
        sell_px = fp * (1 - SELL_SLIPPAGE)
        gross = h['pos'] * sell_px; net_cash = gross - gross * SELL_FEE
        ret = (sell_px - h['buy_px']) / h['buy_px']
        events.append({'date': common_dates[-1], 'type': 'FINAL', 'stock': pc['name'],
                      'buy_px': h['buy_px'], 'sell_px': sell_px, 'ret': ret,
                      'buy_date': h['buy_date'], 'days': len(common_dates)-1-h['buy_day'],
                      'sector': h['sector']})
        trades.append({'name': pc['name'], 'ret': ret, 'exit': 'final'})
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

    return {'tr': tr, 'ar': cagr, 'av': av, 'sh': sh, 'mdd': mdd, 'cm': cm,
            'np': len(trades), 'wr': wr, 'avg_positions': avg_pos, 'fv': fv,
            'trades': trades, 'events': events, 'daily_values': daily_values}

def main():
    fund_data = load_fundamentals()
    stocks = {}
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'): continue
        with open(os.path.join(DATA_DIR, fname), 'r', encoding='utf-8') as f:
            data = json.load(f)
        stocks[data['code']] = {'name': data['name'], 'sector': data['sector'], 'bars': data['bars']}

    # Only stocks with data from 2020-01-02 AND fundamentals
    qualified = {}
    for code, info in stocks.items():
        if code in fund_data and len(info['bars']) >= 1500:
            if info['bars'][0]['date'] <= '2020-01-03':
                qualified[code] = info

    date_sets = [set(b['date'] for b in s['bars']) for s in qualified.values()]
    common_dates = sorted(date_sets[0].intersection(*date_sets[1:]))
    print(f"{len(qualified)} stocks from 2020, {len(common_dates)} trading days")
    print(f"Period: {common_dates[0]} -> {common_dates[-1]}\n")

    for sname, buy_thr, trail_pct in STRATEGIES:
        print(f"{'='*90}")
        print(f"  {sname}: DEV<{buy_thr:.1%} Trail{trail_pct:.0%}")
        print(f"{'='*90}")

        r = backtest(buy_thr, trail_pct, qualified, fund_data, common_dates)

        print(f"  绩效: {INIT_CAP:,.0f} -> {r['fv']:,.0f} | +{r['tr']*100:.1f}% | "
              f"年化{r['ar']*100:.1f}% | 夏普{r['sh']:.4f} | 回撤{r['mdd']*100:.1f}%")
        print(f"  交易{r['np']}笔 | 胜率{r['wr']*100:.0f}% | 均持仓{r['avg_positions']:.1f}只")

        # Timeline
        print(f"\n  --- 完整时间线 ({len(r['events'])}个事件) ---")
        for ev in r['events']:
            if ev['type'] == 'BUY':
                print(f"  {ev['date']} [买入] {ev['stock']:<10s} {ev['sector']:<16s} "
                      f"@{ev['price']:>8.2f}  DEV={ev['dev']:.1%}  得分={ev['score']:.3f}")
            elif ev['type'] == 'SELL':
                print(f"  {ev['date']} [卖出] {ev['stock']:<10s} "
                      f"买@{ev['buy_px']:.2f} -> 卖@{ev['sell_px']:.2f}  "
                      f"收益={ev['ret']:.1%}  持{ev['days']}天  高点@{ev['peak']:.2f}")
            elif ev['type'] == 'FINAL':
                print(f"  {ev['date']} [期末] {ev['stock']:<10s} "
                      f"买@{ev['buy_px']:.2f} -> 卖@{ev['sell_px']:.2f}  "
                      f"收益={ev['ret']:.1%}  持{ev['days']}天")

        # Annual performance
        print(f"\n  --- 年度表现 ---")
        for year in [2020, 2021, 2022, 2023, 2024, 2025, 2026]:
            year_start = f"{year}-01-01"
            year_end = f"{year}-12-31"
            year_dvs = [dv for dv in r['daily_values'] if year_start <= dv['date'] <= year_end]
            if len(year_dvs) >= 2:
                y_ret = (year_dvs[-1]['value'] / year_dvs[0]['value'] - 1) * 100
                bar = '+' * max(0, int(y_ret/5)) + '-' * max(0, int(-y_ret/5))
                print(f"  {year}: {y_ret:>+7.1f}%  {bar}")
            elif year_dvs:
                print(f"  {year}: (partial period)")

        # Trade summary
        print(f"\n  --- 交易汇总 ---")
        for i, t in enumerate(r['trades']):
            tag = " << LOSS" if t['ret'] < 0 else ""
            print(f"  {i+1:>2d}. {t['name']:<12s} {t['exit']:<6s} {t['ret']*100:>+8.1f}%{tag}")

        print()

if __name__ == '__main__':
    main()
