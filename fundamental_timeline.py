"""
最优策略 DEV<-3.5% Trail35% 完整持仓流程输出
"""
import json, os, math, csv
from collections import defaultdict

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")
RISK_FREE = 0.025; TRADING_DAYS = 252; INIT_CAP = 10_000_000
MA_WIN = 5; MAX_POSITIONS = 5
BUY_THR = -0.035; TRAIL_PCT = 0.35
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
        metrics['roe'].append(fund['roe'])
        metrics['net_margin'].append(fund['net_margin'])
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
        scores[code] = {
            'score': z_nm*W_NET_MARGIN + z_roe*W_ROE + z_ry*W_REV_YOY,
            'sector': latest_fund[code]['sector'],
            'roe': metrics['roe'][i], 'net_margin': metrics['net_margin'][i],
            'rev_yoy': metrics['rev_yoy'][i],
        }
    return scores

def main():
    print("=" * 100)
    print(f"  BEST: DEV<{BUY_THR:.1%}  Trail{TRAIL_PCT:.0%}  完整持仓流程")
    print(f"  得分 = Z(净利)×{W_NET_MARGIN:.2f} + Z(ROE)×{W_ROE:.2f} + Z(营收YoY)×{W_REV_YOY:.2f}")
    print(f"  T+1 + 滑点0.3% + 手续费 | {MAX_POSITIONS}只持仓, 赛道不重复 | 本金 {INIT_CAP/1e6:.0f}00万")
    print("=" * 100)

    # Load data
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
    common_set = set(common_dates)

    # Precompute
    precomputed = {}
    for code, info in qualified.items():
        bars = [b for b in info['bars'] if b['date'] in common_set]
        closes = [b['close'] for b in bars]
        ma5 = calc_ma(closes, MA_WIN)
        devs = []
        for i, bar in enumerate(bars):
            ma = ma5[i]
            if math.isnan(ma) or ma == 0: devs.append(float('nan'))
            else: devs.append((bar['close']-ma)/abs(ma))
        precomputed[code] = {'bars': bars, 'closes': closes, 'ma5': ma5, 'devs': devs,
                            'sector': info['sector'], 'name': info['name']}

    per_stock_cap = INIT_CAP / MAX_POSITIONS

    # Run backtest with detailed logging
    holdings = {}  # code -> {pos, buy_px, peak, buy_day, buy_date, sector}
    cash_pool = INIT_CAP
    trades = []
    daily_values = []
    current_scores = {}
    events = []  # chronological events
    # Track portfolio state at each event
    portfolio_snapshots = []

    for day_idx, date_str in enumerate(common_dates):
        # Fund update
        new_fund = get_latest(fund_data, date_str)
        if new_fund:
            new_scores = compute_zscores(new_fund)
            if new_scores and new_scores != current_scores:
                current_scores = new_scores

        # Trail check
        sell_events = []
        for code, h in list(holdings.items()):
            pc = precomputed[code]
            px = pc['bars'][day_idx]['close']
            if day_idx > h['buy_day']:
                if px > h['peak']: h['peak'] = px
                if px <= h['peak'] * (1 - TRAIL_PCT):
                    sell_px = px * (1 - SELL_SLIPPAGE)
                    gross = h['pos'] * sell_px
                    net_cash = gross - gross * SELL_FEE
                    ret = (sell_px - h['buy_px']) / h['buy_px']

                    events.append({
                        'date': date_str, 'type': 'SELL',
                        'stock': pc['name'], 'code': code, 'sector': h['sector'],
                        'buy_px': h['buy_px'], 'sell_px': sell_px, 'ret': ret,
                        'buy_date': h['buy_date'], 'peak': h['peak'],
                        'days': day_idx - h['buy_day'],
                    })
                    trades.append({'code': code, 'name': pc['name'], 'ret': ret, 'exit': 'trail'})
                    sell_events.append((code, net_cash))

        for code, cr in sell_events:
            cash_pool += cr
            del holdings[code]

        # Record state before buys
        held_before = {c: precomputed[c]['name'] for c in holdings}

        # Try to buy
        held_sectors = set(h['sector'] for h in holdings.values())
        held_codes = set(holdings.keys())
        eligible = []
        for code, sc in sorted(current_scores.items(), key=lambda x: x[1]['score'], reverse=True):
            if code in held_codes or sc['sector'] in held_sectors: continue
            if code not in precomputed: continue
            dev = precomputed[code]['devs'][day_idx]
            if not math.isnan(dev) and dev < BUY_THR:
                eligible.append((code, sc['score'], sc['sector'], sc['roe'], sc['net_margin'], sc['rev_yoy'], dev))

        while len(holdings) < MAX_POSITIONS and eligible and cash_pool >= per_stock_cap:
            code, score, sector, roe, nm, ry, dev = eligible.pop(0)
            pc = precomputed[code]
            px = pc['bars'][day_idx]['close']
            buy_px = px * (1 + BUY_SLIPPAGE)
            fee = per_stock_cap * BUY_FEE
            pos = (per_stock_cap - fee) / buy_px
            holdings[code] = {'pos': pos, 'buy_px': buy_px, 'peak': px,
                             'buy_day': day_idx, 'buy_date': date_str, 'sector': sector}
            cash_pool -= per_stock_cap
            held_sectors.add(sector)

            events.append({
                'date': date_str, 'type': 'BUY',
                'stock': pc['name'], 'code': code, 'sector': sector,
                'price': buy_px, 'dev': dev,
                'score': score, 'roe': roe, 'net_margin': nm, 'rev_yoy': ry,
            })

        # Portfolio value
        pv = cash_pool
        for code, h in holdings.items():
            pv += h['pos'] * precomputed[code]['bars'][day_idx]['close']
        daily_values.append({'date': date_str, 'value': pv, 'cash': cash_pool, 'positions': len(holdings)})

        # Snapshot at events or month-end
        is_event = any(e['date'] == date_str for e in events[-3:])  # last 3 events
        is_month_end = date_str.endswith(common_dates[-1]) or (
            day_idx < len(common_dates)-1 and common_dates[day_idx+1][:7] != date_str[:7]
        )
        if is_event or is_month_end:
            held_now = [(c, precomputed[c]['name'], h['buy_date'])
                       for c, h in holdings.items()]
            portfolio_snapshots.append({
                'date': date_str, 'value': pv, 'positions': len(holdings),
                'held': held_now, 'cash': cash_pool,
            })

    # Final liquidation
    for code, h in list(holdings.items()):
        pc = precomputed[code]
        fp = pc['bars'][-1]['close']
        sell_px = fp * (1 - SELL_SLIPPAGE)
        gross = h['pos'] * sell_px
        net_cash = gross - gross * SELL_FEE
        ret = (sell_px - h['buy_px']) / h['buy_px']
        events.append({
            'date': common_dates[-1], 'type': 'FINAL',
            'stock': pc['name'], 'code': code, 'sector': h['sector'],
            'buy_px': h['buy_px'], 'sell_px': sell_px, 'ret': ret,
            'buy_date': h['buy_date'], 'days': len(common_dates) - 1 - h['buy_day'],
        })
        cash_pool += net_cash
        del holdings[code]

    # Metrics
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
    tr = (fv - INIT_CAP)/INIT_CAP
    if len(rets) > 1:
        mu = sum(rets)/len(rets); sd = (sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av = sd*math.sqrt(TRADING_DAYS); ar_ = mu*TRADING_DAYS
        sh = (ar_-RISK_FREE)/av if av>0 else 0
    else: av = sh = ar_ = 0.0
    cagr = (1+tr)**(TRADING_DAYS/max(len(rets),1))-1 if tr>-1 else -1

    # ================================================================
    print(f"\n  {'='*60}")
    print(f"  组合绩效")
    print(f"  {'='*60}")
    print(f"  {INIT_CAP:,.0f} → {fv:,.0f}  |  +{tr*100:.1f}%  |  年化 {cagr*100:.1f}%")
    print(f"  夏普 {sh:.4f}  |  回撤 {mdd*100:.1f}%  |  交易 {len(trades)}笔")
    print(f"  Trail退出 {sum(1 for t in trades if t['exit']=='trail')}笔  |  "
          f"期末 {sum(1 for t in trades if t['exit']=='final')}笔")

    # ================================================================
    print(f"\n\n  {'='*100}")
    print(f"  完整持仓时间线（共 {len(events)} 个事件）")
    print(f"  {'='*100}")

    # Count holdings at each event
    current_held = set()
    for ev in events:
        if ev['type'] == 'BUY':
            current_held.add(ev['code'])
        elif ev['type'] in ('SELL', 'FINAL'):
            current_held.discard(ev['code'])

        held_names = [precomputed[c]['name'] for c in current_held]

        if ev['type'] == 'BUY':
            print(f"\n  {ev['date']} [买入] {ev['stock']} ({ev['sector']})")
            print(f"    买入价={ev['price']:.2f}  DEV={ev['dev']:.1%}  "
                  f"得分={ev['score']:.3f}  ROE={ev['roe']:.1f}%  "
                  f"净利={ev['net_margin']:.1f}%  营收YoY={ev['rev_yoy']:.1f}%")
            print(f"    当前持仓({len(held_names)}): {', '.join(held_names)}")

        elif ev['type'] == 'SELL':
            print(f"\n  {ev['date']} [卖出] {ev['stock']} ({ev['sector']})")
            print(f"    买入@{ev['buy_px']:.2f} → 卖出@{ev['sell_px']:.2f}  "
                  f"收益={ev['ret']:.1%}  "
                  f"持{ev['days']}天  高点@{ev['peak']:.2f}")
            print(f"    剩余持仓({len(held_names)}): {', '.join(held_names) if held_names else '(空仓)'}")

        elif ev['type'] == 'FINAL':
            print(f"\n  {ev['date']} [期末] {ev['stock']} ({ev['sector']})")
            print(f"    买入@{ev['buy_px']:.2f} → 卖出@{ev['sell_px']:.2f}  "
                  f"收益={ev['ret']:.1%}  持{ev['days']}天")

    # ================================================================
    print(f"\n\n  {'='*100}")
    print(f"  持仓净值快照（月末 + 事件日）")
    print(f"  {'='*100}")
    print(f"  {'日期':<12s} {'持仓':>4s} {'净值':>14s} {'收益率':>9s} {'持有股票'}")
    print(f"  {'-'*100}")

    for ps in portfolio_snapshots:
        tr_pct = (ps['value']/INIT_CAP - 1)*100
        held_names = [name for _, name, _ in ps['held']]
        bars = '#' * ps['positions'] + '.' * (MAX_POSITIONS - ps['positions'])
        print(f"  {ps['date']:<12s} {ps['positions']:>2}只 {bars} "
              f"{ps['value']:>12,.0f} {tr_pct:>+7.1f}%  "
              f"{', '.join(held_names) if held_names else '(空仓)'}")

    # ================================================================
    print(f"\n\n  {'='*80}")
    print(f"  交易汇总")
    print(f"  {'='*80}")
    total_pnl = 0
    for i, t in enumerate(trades):
        stock_ret = t['ret']*100
        tag = "← 亏损!" if stock_ret < 0 else ""
        total_pnl += stock_ret
        print(f"  {i+1:>2d}. {t['name']:<10s} {t['exit']:<6s}  {stock_ret:>+7.1f}%  {tag}")

    avg_win = sum(t['ret'] for t in trades if t['ret']>0)/max(sum(1 for t in trades if t['ret']>0),1)*100
    avg_loss = sum(t['ret'] for t in trades if t['ret']<=0)/max(sum(1 for t in trades if t['ret']<=0),1)*100
    win_r = sum(1 for t in trades if t['ret']>0)/len(trades)*100
    print(f"\n  胜率: {win_r:.0f}%  |  均盈: +{avg_win:.1f}%  |  均亏: {avg_loss:.1f}%")

    print(f"\n  Done!")

if __name__ == '__main__':
    main()
