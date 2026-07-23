"""
峰岭因子策略 · 标准版输出 + 沪深300/中证500 基准对比
========================================================
Strategy A: Trail 22%
Strategy B: Trail 30%
因子: peak/ridge ratio, K=1.5, LB=14d
"""
import sys, io, os, math, json
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from data_loader import load_prices, calc_ma, get_common_dates

INIT = 10_000_000; RF = 0.025; TD = 252; MAX_POS = 5
SLIP = 0.003; B_FEE = 0.00025; S_FEE = 0.00025; STAX = 0.0005
FUND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fundamentals_70stocks")

def load_sector_map():
    import csv
    csvs = sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
    sm = {}
    with open(os.path.join(FUND_DIR, csvs[-1]), 'r', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f): sm[r['code'].strip()] = r.get('sector','').strip()
    return sm

def calc_factor(stocks):
    factor = {}
    for code, info in stocks.items():
        vols = info['volume']; dates = info['dates']; n = len(vols)
        ma_vol = calc_ma(vols, 20)
        vals = {}
        for i in range(n):
            if i < 14 or math.isnan(ma_vol[i]): continue
            w = vols[i-19:i+1]; mu = sum(w)/20
            var = sum((v-mu)**2 for v in w)/20; std = var**0.5
            thr = ma_vol[i] + 1.5*std
            ps = 0.0; rs = 0.0
            for j in range(max(0, i-13), i+1):
                erupt = vols[j] >= thr
                if erupt:
                    prev_erupt = (j > 0 and vols[j-1] >= thr)
                    if prev_erupt: rs += vols[j]
                    else: ps += vols[j]
            vals[dates[i]] = ps/rs if rs > 0 else float('nan')
        factor[code] = vals
    return factor

def backtest(stocks, factor, sm, dates, trail_pct):
    cash = INIT; slot = INIT/MAX_POS; pos = {}; eq = []; trades = []
    idx = {c: {d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    for di, dt in enumerate(dates):
        # Trail exits
        for code, p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px = stocks[code]['close'][idx[code][dt]]
            if px > p['peak']: p['peak'] = px
            if px <= p['peak']*(1-trail_pct):
                sp = px*(1-SLIP-S_FEE-STAX)
                cash += p['shares']*sp
                trades.append({'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'exit':'trail','hold':di-p['bi']})
                del pos[code]
        # Rebalance
        if di % 21 == 0:
            cand = [(c, factor.get(c,{}).get(dt, float('nan'))) for c in stocks]
            cand = [(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
            cand.sort(key=lambda x: x[1], reverse=True)
            top = set(c for c,_ in cand[:MAX_POS])
            for code in list(pos.keys()):
                if code not in top:
                    px = stocks[code]['close'][idx[code][dt]]
                    sp = px*(1-SLIP-S_FEE-STAX); cash += pos[code]['shares']*sp
                    trades.append({'code':code,'name':stocks[code]['name'],
                        'bd':pos[code]['bd'],'sd':dt,
                        'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                        'exit':'rebalance','hold':di-pos[code]['bi']})
                    del pos[code]
            hc = set(pos.keys()); hs = {sm.get(c,'') for c in hc}
            for code, sc in cand:
                if len(pos) >= MAX_POS: break
                if code in hc: continue
                s = sm.get(code,'')
                if s and s in hs: continue
                if cash < slot*0.99: break
                raw = stocks[code]['close'][idx[code][dt]]
                bp = raw*(1+SLIP+B_FEE); sh = slot/bp; cash -= slot
                pos[code] = {'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}
                hc.add(code); hs.add(s)
        cash *= (1+RF/TD)
        pv = sum(p['shares']*stocks[c]['close'][idx[c][dt]] for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date':dt, 'equity':cash+pv, 'pos':len(pos), 'cash':cash})
    # Final
    ld = dates[-1]
    for code, p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px = stocks[code]['close'][idx[code][ld]]
            sp = px*(1-SLIP-S_FEE-STAX); cash += p['shares']*sp
            trades.append({'code':code,'name':stocks[code]['name'],
                'bd':p['bd'],'sd':ld,
                'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                'exit':'final','hold':len(dates)-1-p['bi']})
    pos.clear()
    if eq: eq[-1]['equity'] = cash; eq[-1]['pos'] = 0
    # Stats
    v = [d['equity'] for d in eq]
    tr = (v[-1]-v[0])/v[0]; rs = [(v[i]-v[i-1])/v[i-1] for i in range(1,len(v)) if v[i-1]>0]
    y = len(rs)/TD; cagr = (v[-1]/v[0])**(1/y)-1 if y>0 else 0
    mu = sum(rs)/len(rs) if rs else 0
    sd = (sum((r-mu)**2 for r in rs)/len(rs))**0.5 if rs else 0
    sh = (mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk = v[0]; mdd = 0.0
    for x in v:
        if x > pk: pk = x
        dd = (pk-x)/pk
        if dd > mdd: mdd = dd
    cm = cagr/mdd if mdd > 0 else float('inf')
    w = sum(1 for t in trades if t['ret'] > 0)
    # Exit types
    exits = {}
    for e in set(t['exit'] for t in trades):
        sub = [t for t in trades if t['exit'] == e]
        exits[e] = {'cnt':len(sub), 'avg_ret':sum(t['ret'] for t in sub)/len(sub)*100}
    return {
        'equity':eq, 'trades':trades,
        'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,
        'nt':len(trades), 'wr':w/len(trades) if trades else 0,
        'hp':sum(1 for d in eq if d['pos']>0)/len(eq),
        'exits':exits
    }

def annual_returns(equity):
    yr = defaultdict(lambda:{'s':None,'e':None})
    for d in equity:
        yk = d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s'] = d['equity']
        yr[yk]['e'] = d['equity']
    return {y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}

def load_index(path):
    with open(path, 'r', encoding='utf-8') as f:
        d = json.load(f)
    # Convert YYYY-MM-DD → YYYYMMDD to match stock data format
    dates = []
    closes = []
    date_close = {}
    for b in d['bars']:
        dt = b['date'].replace('-', '')
        dates.append(dt)
        closes.append(b['close'])
        date_close[dt] = b['close']
    return {'name':d['name'],'code':d['code'],'dates':dates,'close':closes,'map':date_close}

def benchmark_stats(index_data, common_dates):
    """Calculate buy-and-hold stats for index."""
    vals = []
    first = None
    for dt in common_dates:
        px = index_data['map'].get(dt)
        if px:
            if first is None: first = px
            vals.append(px)
    if not vals or first is None or first <= 0:
        return {}
    final = vals[-1]
    tr = (final - first)/first
    rets = [(vals[i]-vals[i-1])/vals[i-1] for i in range(1,len(vals)) if vals[i-1]>0]
    y = len(rets)/TD; cagr = (final/first)**(1/y)-1 if y>0 else 0
    mu = sum(rets)/len(rets) if rets else 0
    sd = (sum((r-mu)**2 for r in rets)/len(rets))**0.5 if rets else 0
    sh = (mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk = vals[0]; mdd = 0.0
    for v in vals:
        if v > pk: pk = v
        dd = (pk-v)/pk
        if dd > mdd: mdd = dd
    cm = cagr/mdd if mdd > 0 else float('inf')
    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm}

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sm = load_sector_map()
    all_s = load_prices(stock_filter=None)
    stocks = {c:i for c,i in all_s.items()
              if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
    cd = get_common_dates(stocks)

    # Calc factor
    print('[FACTOR] Computing peak/ridge ratio (K=1.5, LB=14d)...')
    factor = calc_factor(stocks)
    print(f'  {sum(len(v) for v in factor.values())} valid values')

    # Load benchmarks
    print('[BENCH] Loading index data...')
    base = os.path.dirname(os.path.abspath(__file__))
    hs300 = load_index(os.path.join(base, 'benchmarks', 'sh000300.json'))
    zz500 = load_index(os.path.join(base, 'benchmarks', 'sh000905.json'))
    print(f'  沪深300: {len(hs300["dates"])} bars')
    print(f'  中证500: {len(zz500["dates"])} bars')

    # Common dates with benchmarks
    bench_dates = set(hs300['map'].keys()) & set(zz500['map'].keys())
    common_all = sorted(set(cd) & bench_dates)
    print(f'  Common dates: {len(common_all)} ({common_all[0]} ~ {common_all[-1]})')

    # ================================================================
    # Strategy A: Trail 22%
    # Strategy B: Trail 30%
    # ================================================================
    configs = [
        ('A·Trail=22%', 0.22),
        ('B·Trail=30%', 0.30),
    ]

    print(f'\n{"="*90}')
    print(f'  峰岭因子策略 · 标准版')
    print(f'  Factor: peak/ridge ratio | K=1.5 | LB=14d | rebal=21d | Top5 | 赛道去重')
    print(f'{"="*90}')

    results = {}
    for label, trail in configs:
        bt = backtest(stocks, factor, sm, cd, trail)
        results[label] = bt
        s = bt
        yr = annual_returns(bt['equity'])
        print(f'\n  ╔{"═"*75}╗')
        print(f'  ║  STRATEGY {label:<63s}║')
        print(f'  ╠{"═"*75}╣')
        print(f'  ║  Sharpe={s["sh"]:.4f}  |  Ret={s["tr"]*100:.1f}%  |  CAGR={s["cagr"]*100:.2f}%  |  MDD={s["mdd"]*100:.1f}%  |  Calmar={s["calmar"]:.3f}  ║')
        print(f'  ║  Trades={s["nt"]}  |  Win={s["wr"]*100:.0f}%  |  Hold={s["hp"]*100:.1f}%  ║')
        print(f'  ╚{"═"*75}╝')
        for e,d in s['exits'].items():
            print(f'    {e:<12s}: {d["cnt"]:>4d}笔  均收益={d["avg_ret"]:>+6.1f}%')

        print(f'\n    Annual Returns:')
        for y, r in yr.items():
            bar = '█'*max(1,int(abs(r)/3)) if r>0 else '░'*max(1,int(abs(r)/3))
            print(f'      {y}: {r:>+7.1f}%  {bar}')

    # ================================================================
    # Benchmark comparison
    # ================================================================
    print(f'\n{"="*90}')
    print(f'  BENCHMARK COMPARISON: 策略 vs 沪深300 vs 中证500')
    print(f'  Period: {common_all[0]} ~ {common_all[-1]} ({len(common_all)/252:.1f} years)')
    print(f'{"="*90}')

    # Calculate benchmark stats
    bench_stats = {}
    for idx_data in [hs300, zz500]:
        s = benchmark_stats(idx_data, common_all)
        bench_stats[idx_data['name']] = s

    # Align strategy equity to benchmark dates
    print(f'\n  {"Metric":<18s} {"策略A 22%":>11s} {"策略B 30%":>11s} {"沪深300":>11s} {"中证500":>11s}')
    print(f'  {"─"*62}')

    for metric, key, fmt in [
        ('Sharpe', 'sh', '.3f'),
        ('Total Return', 'tr', '.1%'),
        ('CAGR', 'cagr', '.1%'),
        ('Max Drawdown', 'mdd', '.1%'),
        ('Calmar', 'calmar', '.3f'),
        ('Win/Lose Ratio', 'wr', '.0%'),
    ]:
        row = f'  {metric:<18s}'
        for label in ['A·Trail=22%', 'B·Trail=30%']:
            v = results[label][key]
            if fmt == '.1%': v *= 100
            elif fmt == '.0%': v *= 100
            row += f' {v:>10{fmt}}'
        for bm_name in ['沪深300', '中证500']:
            v = bench_stats[bm_name].get(key, 0)
            if fmt == '.1%': v *= 100
            elif fmt == '.0%': v *= 100
            row += f' {v:>10{fmt}}'
        print(row)

    # Annual returns comparison
    print(f'\n  {"Year":<6s} {"策略A 22%":>10s} {"策略B 30%":>10s} {"沪深300":>10s} {"中证500":>10s}')
    print(f'  {"─"*47}')

    # Build benchmark annual returns
    yr_hs300 = {}
    yr_zz500 = {}
    hs_first = {}
    zz_first = {}
    for dt in common_all:
        y = dt[:4]
        px_hs = hs300['map'].get(dt)
        px_zz = zz500['map'].get(dt)
        if px_hs and y not in hs_first: hs_first[y] = px_hs
        if px_zz and y not in zz_first: zz_first[y] = px_zz
        if px_hs: yr_hs300[y] = px_hs
        if px_zz: yr_zz500[y] = px_zz

    yr_a = annual_returns(results['A·Trail=22%']['equity'])
    yr_b = annual_returns(results['B·Trail=30%']['equity'])

    for y in sorted(set(list(yr_a.keys()) + list(yr_hs300.keys()))):
        ra = yr_a.get(y, 0)
        rb = yr_b.get(y, 0)
        s_hs = hs_first.get(y)
        e_hs = yr_hs300.get(y)
        rh = (e_hs-s_hs)/s_hs*100 if s_hs and e_hs and s_hs>0 else 0
        s_zz = zz_first.get(y)
        e_zz = yr_zz500.get(y)
        rz = (e_zz-s_zz)/s_zz*100 if s_zz and e_zz and s_zz>0 else 0
        print(f'  {y:<6s} {ra:>+9.1f}% {rb:>+9.1f}% {rh:>+9.1f}% {rz:>+9.1f}%')

    # ================================================================
    # Excess return analysis
    # ================================================================
    print(f'\n{"="*90}')
    print(f'  EXCESS RETURN vs 沪深300 (benchmark)')
    print(f'{"="*90}')

    for label in ['A·Trail=22%', 'B·Trail=30%']:
        bt = results[label]
        eq_map = {d['date']: d['equity'] for d in bt['equity']}
        # Calculate excess daily returns
        excess = []
        for i in range(1, len(common_all)):
            d = common_all[i]; d_prev = common_all[i-1]
            sv = eq_map.get(d_prev); ev = eq_map.get(d)
            bv = hs300['map'].get(d_prev); bve = hs300['map'].get(d)
            if sv and ev and bv and bve and sv>0 and bv>0:
                excess.append((ev/sv - 1) - (bve/bv - 1))
        if excess:
            mu = sum(excess)/len(excess)
            sd = (sum((r-mu)**2 for r in excess)/len(excess))**0.5
            ir = mu/sd*(TD**0.5) if sd>0 else 0
            # Alpha estimate
            alpha = mu*TD*100
            print(f'  {label}: Information Ratio={ir:.3f}  Annual Alpha={alpha:.2f}%  Tracking Error={sd*(TD**0.5)*100:.1f}%')

    # ================================================================
    # Top trades for Strategy A
    # ================================================================
    print(f'\n{"="*90}')
    print(f'  Strategy A (Trail=22%) — Top & Bottom Trades')
    print(f'{"="*90}')
    ts = sorted(results['A·Trail=22%']['trades'], key=lambda x: x['ret'], reverse=True)
    for tag, subset in [('🏆 Top 10', ts[:10]), ('💀 Bottom 10', ts[-10:])]:
        print(f'\n  {tag}:')
        print(f'  {"Stock":<12s} {"Buy":<12s} {"Sell":<12s} {"Ret":>8s} {"Hold":>5s} {"Exit":>10s}')
        print(f'  {"─"*55}')
        for t in subset:
            print(f'  {t["name"]:<12s} {t["bd"]:<12s} {t["sd"]:<12s} {t["ret"]*100:>7.1f}% {t["hold"]:>5d} {t["exit"]:>10s}')

    # ================================================================
    # Sector exposure
    # ================================================================
    print(f'\n{"="*90}')
    print(f'  Strategy A — Sector Exposure')
    print(f'{"="*90}')
    sector_pnl = defaultdict(lambda: {'cnt':0, 'total_ret':0.0, 'trades':[]})
    for t in results['A·Trail=22%']['trades']:
        sec = sm.get(t['code'], '?')
        sector_pnl[sec]['cnt'] += 1
        sector_pnl[sec]['total_ret'] += t['ret']
    print(f'  {"Sector":<30s} {"Trd":>5s} {"AvgRet":>8s} {"Total":>8s}')
    print(f'  {"─"*53}')
    for sec in sorted(sector_pnl, key=lambda x: sector_pnl[x]['total_ret']/sector_pnl[x]['cnt'] if sector_pnl[x]['cnt'] else 0, reverse=True):
        d = sector_pnl[sec]
        avg = d['total_ret']/d['cnt']*100 if d['cnt']>0 else 0
        total = d['total_ret']*100
        if d['cnt'] >= 3:
            print(f'  {sec:<30s} {d["cnt"]:>5d} {avg:>7.1f}% {total:>7.1f}%')

    # ================================================================
    # Export equity curves
    # ================================================================
    import csv
    base = os.path.dirname(os.path.abspath(__file__))
    for label, bt in [('A_trail22', results['A·Trail=22%']), ('B_trail30', results['B·Trail=30%'])]:
        with open(os.path.join(base, f'peakridge_{label}_equity.csv'), 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['date', 'equity', 'positions', 'cash'])
            for d in bt['equity']:
                w.writerow([d['date'], f'{d["equity"]:.2f}', d['pos'], f'{d["cash"]:.2f}'])

    with open(os.path.join(base, f'peakridge_trades.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['code','name','buy_date','sell_date','ret','exit','hold_days'])
        for t in results['A·Trail=22%']['trades']:
            w.writerow([t['code'],t['name'],t['bd'],t['sd'],f'{t["ret"]:.4f}',t['exit'],t['hold']])

    print(f'\n{"="*90}')
    print(f'  EXPORTED:')
    print(f'    peakridge_A_trail22_equity.csv')
    print(f'    peakridge_B_trail30_equity.csv')
    print(f'    peakridge_trades.csv (Strategy A)')
    print(f'{"="*90}')
    print(f'\n  Done!')

if __name__ == '__main__':
    main()
