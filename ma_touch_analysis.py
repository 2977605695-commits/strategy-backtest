"""
择时策略 · 63只全股票池回测
===========================
Buy: N=5 range-bound(Cap=10%) + MA3/MA7 >= 2%
Sell: Trail only (10%~30%) + Trail+MA7 touch
Equal weight across all qualifying stocks (>=200 bars, first <= 2024-06)

Period: 2024-01-01 to 2026-07-22 (common date range for all)
"""

import sys, io, json, math, os
from collections import defaultdict
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
START = '2024-01-01'; END = '2026-07-22'
RF = 0.025; TD = 252; INIT = 10_000_000  # 10M for better precision
MA_CROSS = 0.02; N = 5; CAP = 0.10


def load_stocks():
    """Load all qualifying stocks."""
    stocks = {}
    for f in sorted(os.listdir(DATA_DIR)):
        if not f.endswith('.json') or f.startswith('_'): continue
        d = json.load(open(os.path.join(DATA_DIR, f), encoding='utf-8'))
        if len(d['bars']) < 200: continue
        if d['first_date'] > '2024-06': continue
        bars = []
        for b in d['bars']:
            dt = b['date']
            if len(dt) == 8: dt = f'{dt[:4]}-{dt[4:6]}-{dt[6:8]}'
            if START <= dt <= END:
                bars.append({'date': dt, 'close': float(b['close'])})
        if bars:
            stocks[d['code']] = {'name': d['name'], 'bars': bars}
    return stocks


def calc_ma(data, w):
    ma = []; n = len(data)
    for i in range(n):
        if i < w-1: ma.append(float('nan'))
        else: ma.append(sum(data[i-w+1:i+1])/w)
    return ma


def generate_signals(bars, ma_sell_w=None):
    closes = [b['close'] for b in bars]
    ma3 = calc_ma(closes, 3); ma7 = calc_ma(closes, 7)
    if ma_sell_w: ma_sell = calc_ma(closes, ma_sell_w)
    for i, b in enumerate(bars):
        b['ma3'] = ma3[i]; b['ma7'] = ma7[i]
        if ma_sell_w: b['ma_sell'] = ma_sell[i]
        if math.isnan(ma3[i]) or math.isnan(ma7[i]) or ma7[i]==0:
            b['ma_cross_dev'] = float('nan')
        else:
            b['ma_cross_dev'] = (ma3[i]-ma7[i])/abs(ma7[i])
        if i < N: b['is_range_bound'] = False
        else:
            wc = closes[i-N+1:i+1]; cur = closes[i]
            b['is_range_bound'] = (max(wc)-cur)/cur <= CAP and (cur-min(wc))/min(wc) <= CAP
        b['signal_buy'] = (b['is_range_bound'] and
                           not math.isnan(b['ma_cross_dev']) and b['ma_cross_dev']>=MA_CROSS)
        if ma_sell_w:
            b['touches_ma'] = (not math.isnan(ma_sell[i]) and ma_sell[i]>0 and
                               b['close']<=ma_sell[i])
    return bars


def backtest(name, bars, init_cap, trail_pct, use_ma):
    cash=init_cap; pos=0.0; bp=0.0; peak=0.0; trades=[]; dvs=[]; holding=False
    trail_n=0; ma_n=0; fin_n=0
    for b in bars:
        px = b['close']
        if holding:
            if px>peak: peak=px
            if px <= peak*(1-trail_pct):
                cash=pos*px; pnl=cash-pos*bp
                trades.append({'d':b['date'],'a':'trail','px':px,'pnl':pnl})
                trail_n+=1; pos=0.0; bp=0.0; holding=False; peak=0.0
            elif use_ma and b.get('touches_ma',False):
                cash=pos*px; pnl=cash-pos*bp
                trades.append({'d':b['date'],'a':'ma','px':px,'pnl':pnl})
                ma_n+=1; pos=0.0; bp=0.0; holding=False; peak=0.0
        if not holding and b.get('signal_buy',False) and cash>0:
            pos=cash/px; bp=px; peak=px; holding=True
            trades.append({'d':b['date'],'a':'buy','px':px})
            cash=0.0
        dvs.append({'d':b['date'],'v':cash+(pos*px if holding else 0),'h':holding})
    if holding:
        fp=bars[-1]['close']; cash=pos*fp; pnl=cash-pos*bp
        trades.append({'d':bars[-1]['date'],'a':'final','px':fp,'pnl':pnl})
        fin_n+=1; dvs[-1]['v']=cash; dvs[-1]['h']=False
    fv=cash; rets=[]
    for i in range(1,len(dvs)):
        p,c=dvs[i-1]['v'],dvs[i]['v']
        if p>0:rets.append((c-p)/p)
    if not rets:rets=[0.0]
    pkv=dvs[0]['v'];mdd=0.0
    for dv in dvs:
        if dv['v']>pkv:pkv=dv['v']
        dd=(pkv-dv['v'])/pkv
        if dd>mdd:mdd=dd
    tr=(fv-init_cap)/init_cap
    if len(rets)>1:
        mu=sum(rets)/len(rets);sd=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
        av=sd*math.sqrt(TD);ar_=mu*TD;sh=(ar_-RF)/av if av>0 else 0
    else:av=sh=ar_=0.0
    ar=(1+tr)**(TD/max(len(rets),1))-1 if tr>-1 else -1;cm=ar/mdd if mdd>0 else float('inf')
    pairs=[];cb=None
    for t in trades:
        if t['a']=='buy':cb=t
        elif t['a'] in('trail','ma','final') and cb:
            pairs.append({'bd':cb['d'],'sd':t['d'],'bp':cb['px'],'sp':t['px'],
                          'ret':(t['px']-cb['px'])/cb['px'],'pnl':t.get('pnl',0),'exit':t['a']})
            cb=None
    wins=sum(1 for p in pairs if p['ret']>0);wr=wins/len(pairs) if pairs else 0
    hd=sum(1 for dv in dvs if dv['h'])
    return {'name':name,'tr':tr,'ar':ar,'vol':av,'sh':sh,'cm':cm,'mdd':mdd,
            'np':len(pairs),'wr':wr,'trail_n':trail_n,'ma_n':ma_n,'fin_n':fin_n,
            'hd':hd,'dvs':dvs,'pairs':pairs,'fv':fv}


def main():
    print('='*100)
    print('  择时策略 · 63只全股票池回测')
    print(f'  Buy: N={N} Cap={CAP:.0%} MA3/MA7>={MA_CROSS:.0%}')
    print(f'  Sell: Trail (10~30%) + Trail+MA7 touch')
    print('='*100)

    # Load
    print('\n[DATA] Loading...')
    stocks = load_stocks()
    print(f'  Loaded {len(stocks)} stocks')
    codes = sorted(stocks.keys())

    # Align dates
    date_sets = [set(b['date'] for b in stocks[c]['bars']) for c in codes]
    common = sorted(date_sets[0].intersection(*date_sets[1:]))
    print(f'  Common dates: {len(common)}')
    per_stock = INIT / len(codes)

    # Pre-generate signals (one set for Trail-only, one for Trail+MA7)
    print('\n[SIGNALS] Generating...')
    sigs_no_ma = {}
    sigs_ma7 = {}
    for code in codes:
        bars = [b for b in stocks[code]['bars'] if b['date'] in common]
        sigs_no_ma[code] = generate_signals(bars, ma_sell_w=None)
        sigs_ma7[code] = generate_signals(bars, ma_sell_w=7)
        buy_n = sum(1 for b in sigs_no_ma[code] if b.get('signal_buy',False))
        print(f'  {code} {stocks[code]["name"]:<10s} {len(bars):>4d} bars  buy_signals={buy_n}')

    # Scenarios
    trails = [0.10, 0.15, 0.20, 0.25, 0.30]
    scenarios = []
    # Trail-only
    for t in trails:
        scenarios.append((f'Trail={t:.0%} only', t, sigs_no_ma, False))
    # Trail + MA7 touch
    for t in trails:
        scenarios.append((f'Trail={t:.0%}+MA7', t, sigs_ma7, True))

    print(f'\n[BACKTEST] {len(scenarios)} scenarios x {len(codes)} stocks...')

    results = []
    for label, trail, sigs, use_ma in scenarios:
        print(f'\n  --- {label} ---')
        sr = {}
        for code in codes:
            sr[code] = backtest(stocks[code]['name'], sigs[code], per_stock, trail, use_ma)

        # Portfolio aggregate
        vm = {c: {dv['d']: dv['v']/per_stock for dv in sr[c]['dvs']} for c in codes}
        pd_ = [{'d': d, 'v': sum(vm[c].get(d, 1.0) for c in codes)} for d in common]
        iv, fv = pd_[0]['v'], pd_[-1]['v']
        pr = []
        for i in range(1, len(pd_)):
            p, c = pd_[i-1]['v'], pd_[i]['v']
            if p>0: pr.append((c-p)/p)
        if not pr: pr = [0.0]
        pk = iv; mdd = 0.0
        for dv in pd_:
            if dv['v']>pk: pk = dv['v']
            dd = (pk-dv['v'])/pk
            if dd>mdd: mdd = dd
        tr = (fv-iv)/iv
        if len(pr)>1:
            mu=sum(pr)/len(pr);va=sum((r-mu)**2 for r in pr)/(len(pr)-1)
            av=va**0.5*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
        else: av=sh=0.0
        ar=(1+tr)**(TD/max(len(pr),1))-1 if tr>-1 else -1
        cm=ar/mdd if mdd>0 else float('inf')

        all_p = []
        for c in codes:
            for p in sr[c]['pairs']: all_p.append({**p, 'stock': f'{c} {stocks[c]["name"]}'})
        all_p.sort(key=lambda x: x['bd'])
        wins=sum(1 for p in all_p if p['ret']>0)
        wr=wins/len(all_p) if all_p else 0
        tt=sum(r['trail_n'] for r in sr.values())
        tma=sum(r['ma_n'] for r in sr.values())

        # Top/bottom performers
        stock_rets = [(c, sr[c]['tr'], sr[c]['sh'], sr[c]['np']) for c in codes]
        stock_rets.sort(key=lambda x: x[1], reverse=True)

        results.append({
            'label': label, 'trail': trail, 'use_ma': use_ma,
            'tr': tr, 'ar': ar, 'av': av, 'sh': sh, 'cm': cm, 'mdd': mdd,
            'np': len(all_p), 'wr': wr, 'trail_n': tt, 'ma_n': tma,
            'best5': stock_rets[:5], 'worst5': stock_rets[-5:],
            'all_p': all_p, 'sr': sr,
        })

        print(f'    Portfolio: S={sh:.4f} Ret={tr*100:.2f}% Ann={ar*100:.2f}% DD={mdd*100:.2f}% '
              f'Calmar={cm:.3f} Trd={len(all_p)} Win={wr*100:.0f}% Trail={tt} MA={tma}')
        print(f'    Top 5 stocks:')
        for c, ret, s, np in stock_rets[:5]:
            print(f'      {c} {stocks[c]["name"]:<10s} Ret={ret*100:>7.2f}% S={s:>6.3f} Trd={np}')
        print(f'    Bottom 5:')
        for c, ret, s, np in stock_rets[-5:]:
            print(f'      {c} {stocks[c]["name"]:<10s} Ret={ret*100:>7.2f}% S={s:>6.3f} Trd={np}')

    # ================================================================
    print('\n\n' + '='*100)
    print('  FINAL RANKING (Sorted by Sharpe)')
    print('='*100)
    results.sort(key=lambda x: x['sh'], reverse=True)
    print(f'  {"Rank":<4s} {"Scenario":<20s} {"Sharpe":>7s} {"TotRet":>9s} {"AnnRet":>8s} '
          f'{"MaxDD":>7s} {"Calmar":>7s} {"Trd":>5s} {"Win":>5s} {"Trail#":>6s} {"MA#":>5s}')
    print(f'  {"-"*92}')
    for rank, r in enumerate(results, 1):
        tag = ' <-- BEST' if rank==1 else ''
        print(f'  {rank:<4d} {r["label"]:<20s} {r["sh"]:>7.3f} {r["tr"]*100:>8.2f}% '
              f'{r["ar"]*100:>7.2f}% {r["mdd"]*100:>6.2f}% {r["cm"]:>7.3f} '
              f'{r["np"]:>5d} {r["wr"]*100:>4.0f}% {r["trail_n"]:>6d} {r["ma_n"]:>5d}{tag}')

    # Best detail
    best = results[0]
    print(f'\n\n  BEST: {best["label"]}')
    print(f'  Portfolio: S={best["sh"]:.4f} Ret={best["tr"]*100:.2f}% DD={best["mdd"]*100:.2f}%')
    print(f'\n  Top 10 stocks:')
    for c, ret, s, np in best['best5'][:5]:
        name = stocks[c]['name']
        print(f'    {c} {name:<10s} Ret={ret*100:>7.2f}% S={s:>6.3f} Trd={np}')
    for c, ret, s, np in best['worst5'][-1:-6:-1][:5]:
        name = stocks[c]['name']
        print(f'    {c} {name:<10s} Ret={ret*100:>7.2f}% S={s:>6.3f} Trd={np}')

    # Best 10 trades
    best['all_p'].sort(key=lambda x: x['ret'], reverse=True)
    print(f'\n  Best 10 trades:')
    for p in best['all_p'][:10]:
        print(f'    {p["stock"]:<20s} {p["bd"]} -> {p["sd"]}  {p["ret"]*100:>7.2f}%  {p["exit"]}')

    print('\n  Done!')


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
