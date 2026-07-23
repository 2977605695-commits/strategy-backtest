"""
进攻型: DEV<-5.5% Trail45% 完整持仓流程
"""
import json, os, math, csv
from collections import defaultdict

DATA_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")
RISK_FREE = 0.025; TRADING_DAYS = 252; INIT_CAP = 10_000_000
MA_WIN = 5; MAX_POSITIONS = 5
BUY_THR = -0.055; TRAIL_PCT = 0.45
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

def main():
    print("=" * 100)
    print(f"  进攻型: DEV<{BUY_THR:.1%}  Trail{TRAIL_PCT:.0%}  完整持仓流程")
    print(f"  得分 = Z(净利)x0.50 + Z(ROE)x0.37 + Z(营收YoY)x0.13")
    print(f"  T+1 + 滑点0.3% + 手续费 | 5只持仓, 赛道不重复")
    print("=" * 100)

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
        precomputed[code] = {'bars': bars, 'closes': closes, 'devs': devs,
                            'sector': info['sector'], 'name': info['name']}

    per_stock_cap = INIT_CAP / MAX_POSITIONS
    holdings = {}; cash_pool = INIT_CAP; trades = []; daily_values = []; current_scores = {}
    events = []; trail_count = 0; final_count = 0

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
                if px <= h['peak'] * (1 - TRAIL_PCT):
                    sell_px = px * (1 - SELL_SLIPPAGE)
                    gross = h['pos'] * sell_px
                    net_cash = gross - gross * SELL_FEE
                    ret = (sell_px - h['buy_px']) / h['buy_px']
                    events.append({'date': date_str, 'type': 'SELL', 'stock': pc['name'],
                                  'buy_px': h['buy_px'], 'sell_px': sell_px, 'ret': ret,
                                  'buy_date': h['buy_date'], 'peak': h['peak'],
                                  'days': day_idx - h['buy_day'], 'sector': h['sector']})
                    trades.append({'code': code, 'name': pc['name'], 'ret': ret, 'exit': 'trail'})
                    trail_count += 1; sell_events.append((code, net_cash))

        for code, cr in sell_events: cash_pool += cr; del holdings[code]

        held_sectors = set(h['sector'] for h in holdings.values())
        held_codes = set(holdings.keys())
        eligible = []
        for code, sc in sorted(current_scores.items(), key=lambda x: x[1]['score'], reverse=True):
            if code in held_codes or sc['sector'] in held_sectors: continue
            if code not in precomputed: continue
            dev = precomputed[code]['devs'][day_idx]
            if not math.isnan(dev) and dev < BUY_THR:
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
        trades.append({'code': code, 'name': pc['name'], 'ret': ret, 'exit': 'final'})
        final_count += 1; cash_pool += net_cash; del holdings[code]

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
    tr = (fv-INIT_CAP)/INIT_CAP
    if len(rets)>1:
        mu = sum(rets)/len(rets); sd = (sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av = sd*math.sqrt(TRADING_DAYS); ar_ = mu*TRADING_DAYS
        sh = (ar_-RISK_FREE)/av if av>0 else 0
    else: av = sh = ar_ = 0.0
    cagr = (1+tr)**(TRADING_DAYS/max(len(rets),1))-1 if tr>-1 else -1

    print(f"\n  组合绩效: {INIT_CAP:,.0f} -> {fv:,.0f} | +{tr*100:.1f}% | 年化{cagr*100:.1f}%")
    print(f"  夏普 {sh:.4f} | 回撤 {mdd*100:.1f}% | 交易 {len(trades)}笔")
    print(f"  Trail退出 {trail_count}笔 | 期末 {final_count}笔")

    # Timeline
    print(f"\n\n  {'='*90}")
    print(f"  完整时间线 ({len(events)} 个事件)")
    print(f"  {'='*90}")

    current_held = set()
    for ev in events:
        if ev['type'] == 'BUY':
            current_held.add(ev['stock'])
        elif ev['type'] in ('SELL', 'FINAL'):
            current_held.discard(ev['stock'])

        held_names = list(current_held)

        if ev['type'] == 'BUY':
            print(f"\n  {ev['date']} [买入] {ev['stock']} ({ev['sector']})")
            print(f"    买入价={ev['price']:.2f}  DEV={ev['dev']:.1%}  得分={ev['score']:.3f}")
            print(f"    当前持仓({len(held_names)}只): {', '.join(held_names)}")

        elif ev['type'] == 'SELL':
            print(f"\n  {ev['date']} [卖出] {ev['stock']} ({ev['sector']})")
            print(f"    买入@{ev['buy_px']:.2f} -> 卖出@{ev['sell_px']:.2f}  收益={ev['ret']:.1%}")
            print(f"    持{ev['days']}天  高点@{ev['peak']:.2f}")
            print(f"    剩余持仓({len(held_names)}只): {', '.join(held_names) if held_names else '(空仓)'}")

        elif ev['type'] == 'FINAL':
            print(f"\n  {ev['date']} [期末] {ev['stock']} ({ev['sector']})")
            print(f"    买入@{ev['buy_px']:.2f} -> 卖出@{ev['sell_px']:.2f}  收益={ev['ret']:.1%}  持{ev['days']}天")

    # Trade summary
    print(f"\n\n  {'='*80}")
    print(f"  交易汇总")
    print(f"  {'='*80}")
    total_ret = 0
    for i, t in enumerate(trades):
        tag = " << LOSS!" if t['ret'] < 0 else ""
        total_ret += t['ret']*100
        print(f"  {i+1:>2d}. {t['name']:<12s} {t['exit']:<6s} {t['ret']*100:>+7.1f}%{tag}")

    wins = [t for t in trades if t['ret']>0]; losses = [t for t in trades if t['ret']<=0]
    print(f"\n  胜率: {len(wins)/len(trades)*100:.0f}% | "
          f"均盈: +{sum(t['ret'] for t in wins)/max(len(wins),1)*100:.1f}% | "
          f"均亏: {sum(t['ret'] for t in losses)/max(len(losses),1)*100:.1f}%")

    # vs 稳重型
    print(f"\n\n  {'='*80}")
    print(f"  进攻型 vs 稳重型 对比")
    print(f"  {'='*80}")
    print(f"  {'指标':<18s} {'稳重型 (DEV-3.5% Tr35%)':>24s} {'进攻型 (DEV-5.5% Tr45%)':>24s}")
    print(f"  {'-'*68}")
    print(f"  {'夏普':<18s} {'2.372':>24s} {f'{sh:.3f}':>24s}")
    print(f"  {'总收益':<18s} {'345%':>24s} {f'{tr*100:.0f}%':>24s}")
    print(f"  {'最大回撤':<18s} {'25.5%':>24s} {f'{mdd*100:.1f}%':>24s}")
    print(f"  {'交易笔数':<18s} {'9':>24s} {f'{len(trades)}':>24s}")
    print(f"  {'Trail退出':<18s} {'4':>24s} {f'{trail_count}':>24s}")

    print(f"\n  Done!")

if __name__ == '__main__':
    main()
