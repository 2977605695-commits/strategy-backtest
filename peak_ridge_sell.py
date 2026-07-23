"""
峰岭因子卖出方案对比
====================
因子逻辑不变(日线近似), 测试多种卖出方案
"""
import sys, io, os, math
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from data_loader import load_prices, calc_ma, get_common_dates

INIT = 10_000_000; RF = 0.025; TD = 252
SLIP = 0.003; B_FEE = 0.00025; S_FEE = 0.00025; STAX = 0.0005
MAX_POS = 5; REBAL = 21
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
            if i < 20 or math.isnan(ma_vol[i]): continue
            win = vols[i-19:i+1]; mu = sum(win)/20
            var = sum((v-mu)**2 for v in win)/20; std = var**0.5
            thr = ma_vol[i] + std
            peak_s = 0.0; ridge_s = 0.0
            for j in range(max(0,i-20), i+1):
                erupt = vols[j] >= thr
                if erupt:
                    prev_erupt = (j>0 and vols[j-1] >= thr)
                    if prev_erupt: ridge_s += vols[j]
                    else: peak_s += vols[j]
            vals[dates[i]] = peak_s/ridge_s if ridge_s > 0 else float('nan')
        factor[code] = vals
    return factor

def backtest(stocks, factor, sm, dates, sell_config):
    """
    sell_config = {
        'trail': float or None,       # Trail stop %
        'rebalance': bool,            # Rebalance at 21-day
        'factor_trail': float or None, # Factor from peak drops X%
        'factor_threshold': float or None, # Absolute factor threshold
        'factor_cross': bool,         # Factor crosses below entry value
    }
    """
    cash = INIT; slot = INIT/MAX_POS; pos = {}; eq = []; trades = []
    idx = {c: {d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    # Track factor values per stock
    factor_series = defaultdict(dict)  # code -> date -> value

    for di, dt in enumerate(dates):
        # Check all exit conditions
        for code, p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px = stocks[code]['close'][idx[code][dt]]
            fv = factor.get(code, {}).get(dt, float('nan'))

            if px > p['peak']: p['peak'] = px
            if not math.isnan(fv) and fv > p.get('fpeak', -float('inf')):
                p['fpeak'] = fv

            sell = False; reason = ''

            # 1) Price Trail stop
            cfg_trail = sell_config.get('trail')
            if cfg_trail and px <= p['peak'] * (1 - cfg_trail):
                sell = True; reason = 'trail'

            # 2) Factor Trail: factor drops from peak by X%
            cfg_ftrail = sell_config.get('factor_trail')
            if not sell and cfg_ftrail and not math.isnan(fv):
                fp = p.get('fpeak', fv)
                if fp > 0 and fv <= fp * (1 - cfg_ftrail):
                    sell = True; reason = f'f_trail{cfg_ftrail:.0%}'

            # 3) Factor absolute threshold
            cfg_fthr = sell_config.get('factor_threshold')
            if not sell and cfg_fthr is not None and not math.isnan(fv):
                if fv < cfg_fthr:
                    sell = True; reason = f'f_below_{cfg_fthr:.2f}'

            # 4) Factor crosses below entry value
            cfg_fcross = sell_config.get('factor_cross')
            if not sell and cfg_fcross and not math.isnan(fv):
                entry_fv = p.get('entry_factor', float('nan'))
                if not math.isnan(entry_fv) and fv < entry_fv:
                    sell = True; reason = 'f_cross_entry'

            if sell:
                sp = px * (1 - SLIP - S_FEE - STAX)
                cash += p['shares'] * sp
                trades.append({
                    'code': code, 'name': stocks[code]['name'],
                    'bd': p['bd'], 'sd': dt,
                    'ret': (sp - p['bp'])/p['bp'] if p['bp'] > 0 else 0,
                    'exit': reason, 'hold': di - p['bi'],
                    'entry_fv': p.get('entry_factor', float('nan')),
                    'exit_fv': fv})
                del pos[code]

        # Rebalance
        if sell_config.get('rebalance', True) and di % REBAL == 0:
            cand = [(c, factor.get(c, {}).get(dt, float('nan'))) for c in stocks]
            cand = [(c, s) for c, s in cand if not math.isnan(s)
                    and c in idx and dt in idx[c]]
            cand.sort(key=lambda x: x[1], reverse=True)
            top_codes = set(c for c, _ in cand[:MAX_POS])

            for code in list(pos.keys()):
                if code not in top_codes:
                    px = stocks[code]['close'][idx[code][dt]]
                    sp = px * (1 - SLIP - S_FEE - STAX)
                    cash += pos[code]['shares'] * sp
                    trades.append({
                        'code': code, 'name': stocks[code]['name'],
                        'bd': pos[code]['bd'], 'sd': dt,
                        'ret': (sp - pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp'] > 0 else 0,
                        'exit': 'rebalance', 'hold': di - pos[code]['bi'],
                        'entry_fv': pos[code].get('entry_factor', float('nan')),
                        'exit_fv': factor.get(code, {}).get(dt, float('nan'))})
                    del pos[code]

            hc = set(pos.keys()); hs = {sm.get(c, '') for c in hc}
            for code, sc in cand:
                if len(pos) >= MAX_POS: break
                if code in hc: continue
                s = sm.get(code, '')
                if s and s in hs: continue
                if cash < slot * 0.99: break
                raw = stocks[code]['close'][idx[code][dt]]
                bp = raw * (1 + SLIP + B_FEE); sh = slot / bp; cash -= slot
                entry_fv = factor.get(code, {}).get(dt, float('nan'))
                pos[code] = {'shares': sh, 'bp': bp, 'peak': raw, 'bd': dt, 'bi': di,
                             'fpeak': entry_fv if not math.isnan(entry_fv) else 0,
                             'entry_factor': entry_fv}
                hc.add(code); hs.add(s)

        cash *= (1 + RF / TD)
        pv = sum(p['shares'] * stocks[c]['close'][idx[c][dt]]
                 for c, p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date': dt, 'equity': cash + pv, 'pos': len(pos)})

    ld = dates[-1]
    for code, p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px = stocks[code]['close'][idx[code][ld]]
            sp = px * (1 - SLIP - S_FEE - STAX); cash += p['shares'] * sp
            trades.append({
                'code': code, 'name': stocks[code]['name'],
                'bd': p['bd'], 'sd': ld,
                'ret': (sp - p['bp'])/p['bp'] if p['bp'] > 0 else 0,
                'exit': 'final', 'hold': len(dates) - 1 - p['bi'],
                'entry_fv': p.get('entry_factor', float('nan')),
                'exit_fv': factor.get(code, {}).get(ld, float('nan'))})
    pos.clear()
    if eq: eq[-1]['equity'] = cash; eq[-1]['pos'] = 0

    v = [d['equity'] for d in eq]
    tr = (v[-1] - v[0]) / v[0]
    rs = [(v[i] - v[i-1]) / v[i-1] for i in range(1, len(v)) if v[i-1] > 0]
    y = len(rs) / TD; cagr = (v[-1]/v[0]) ** (1/y) - 1 if y > 0 else 0
    mu = sum(rs) / len(rs) if rs else 0
    sd = (sum((r - mu) ** 2 for r in rs) / len(rs)) ** 0.5 if rs else 0
    sh = (mu * TD - RF) / (sd * (TD ** 0.5)) if sd > 0 else 0
    pk = v[0]; mdd = 0.0
    for x in v:
        if x > pk: pk = x
        dd = (pk - x) / pk
        if dd > mdd: mdd = dd
    cm = cagr / mdd if mdd > 0 else float('inf')
    w = sum(1 for t in trades if t['ret'] > 0)

    exits = {}
    for e in set(t['exit'] for t in trades):
        sub = [t for t in trades if t['exit'] == e]
        exits[e] = {'cnt': len(sub), 'avg_ret': sum(t['ret'] for t in sub) / len(sub) * 100}

    return {'equity': eq, 'trades': trades, 'tr': tr, 'cagr': cagr, 'sh': sh,
            'mdd': mdd, 'calmar': cm, 'nt': len(trades),
            'wr': w / len(trades) if trades else 0,
            'hp': sum(1 for d in eq if d['pos'] > 0) / len(eq), 'exits': exits}

def annual(eq):
    yr = defaultdict(lambda: {'s': None, 'e': None})
    for d in eq:
        yk = d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s'] = d['equity']
        yr[yk]['e'] = d['equity']
    return {y: (v['e'] - v['s']) / v['s'] * 100 for y, v in yr.items()
            if v['s'] and v['e'] and v['s'] > 0}

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sm = load_sector_map()
    all_s = load_prices(stock_filter=None)
    stocks = {c: i for c, i in all_s.items()
              if i['dates'] and i['dates'][0] <= '20200103' and len(i['dates']) >= 1500}
    from data_loader import get_common_dates
    cd = get_common_dates(stocks)
    print(f'[DATA] {len(stocks)} stocks, {len(cd)} days ({cd[0]}~{cd[-1]}, {len(cd)/252:.1f}yr)')

    print(f'\n[FACTOR] Computing...')
    factor = calc_factor(stocks)
    nv = sum(len(v) for v in factor.values())
    print(f'  {nv} valid values')

    # ============================================================
    # 8 sell configs
    # ============================================================
    configs = [
        # (label, sell_config)
        ('#0 Baseline: Trail20% + Rebalance',
         {'trail': 0.20, 'rebalance': True}),

        ('#1 Trail20% + Rebalance + Factor Trail 50%',
         {'trail': 0.20, 'rebalance': True, 'factor_trail': 0.50}),

        ('#2 Trail20% + Rebalance + Factor Trail 75%',
         {'trail': 0.20, 'rebalance': True, 'factor_trail': 0.75}),

        ('#3 Trail20% + Rebalance + Factor Cross Entry',
         {'trail': 0.20, 'rebalance': True, 'factor_cross': True}),

        ('#4 Trail20% + Rebalance + Factor Below 0.5',
         {'trail': 0.20, 'rebalance': True, 'factor_threshold': 0.5}),

        ('#5 Factor Trail 50% ONLY (no price trail)',
         {'rebalance': True, 'factor_trail': 0.50}),

        ('#6 Factor Trail 75% ONLY (no price trail)',
         {'rebalance': True, 'factor_trail': 0.75}),

        ('#7 Trail20% + Factor Trail 50% (no rebalance)',
         {'trail': 0.20, 'rebalance': False, 'factor_trail': 0.50}),
    ]

    print(f'\n{"="*90}')
    print(f'  峰岭因子卖出方案 · 日线版 · {len(cd)/252:.1f}年全周期')
    print(f'{"="*90}')

    results = {}
    for label, cfg in configs:
        bt = backtest(stocks, factor, sm, cd, cfg)
        results[label] = bt
        s = bt
        print(f'\n  [{label}]')
        print(f'    S={s["sh"]:.4f}  R={s["tr"]*100:.1f}%  DD={s["mdd"]*100:.1f}%  '
              f'CM={s["calmar"]:.3f}  Trd={s["nt"]}  Win={s["wr"]*100:.0f}%  '
              f'Hold={s["hp"]*100:.1f}%')
        for e, d in s['exits'].items():
            print(f'    {e:<15s} {d["cnt"]:>4d}笔  均收益={d["avg_ret"]:>+6.1f}%')

    # ============================================================
    # Compare with baseline
    # ============================================================
    base = results['#0 Baseline: Trail20% + Rebalance']
    print(f'\n{"─"*90}')
    print(f'  vs Baseline (#0) 对比')
    print(f'{"─"*90}')
    print(f'  {"Config":<55s} {"Sharpe":>7s} {"Ret":>8s} {"MDD":>7s} {"Calmar":>7s}')
    print(f'  {"─"*85}')
    for label, bt in results.items():
        s = bt
        b_s = base['sh']; s_s = s['sh']
        ds = s_s - b_s
        sign = '+' if ds > 0 else ''
        best = ' ✨' if s_s == max(r['sh'] for r in results.values()) else ''
        print(f'  {label:<55s} {s["sh"]:>7.3f} {s["tr"]*100:>7.1f}% '
              f'{s["mdd"]*100:>6.1f}% {s["calmar"]:>7.3f}{best}')

    # ============================================================
    # Best config annual
    # ============================================================
    best_label = max(results, key=lambda x: results[x]['sh'])
    best = results[best_label]
    print(f'\n{"─"*90}')
    print(f'  🏆 Best: {best_label}')
    yr = annual(best['equity'])
    for y, r in yr.items():
        print(f'    {y}: {r:+.1f}%')
    print(f'\n  Factor exit analysis:')
    ft_trades = [t for t in best['trades'] if 'f_' in t['exit']]
    if ft_trades:
        print(f'    {len(ft_trades)} factor-exit trades')
        for t in sorted(ft_trades, key=lambda x: x['ret'], reverse=True)[:5]:
            print(f'    {t["name"]:<10s} {t["bd"]}→{t["sd"]}  {t["ret"]*100:+.1f}%  '
                  f'fv:{t["entry_fv"]:.2f}→{t["exit_fv"]:.2f}  {t["exit"]}')

    print(f'\n{"="*90}\n  Done!\n{"="*90}')

if __name__ == '__main__':
    main()
