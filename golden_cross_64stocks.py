"""
64只全股 · MA3/MA7金叉+多头排列 · 等权回测
"""
import sys, io, os, math
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from data_loader import load_prices, calc_ma, get_common_dates
import csv

INIT = 10_000_000; RF = 0.025; TD = 252
SLIP = 0.003; B_FEE = 0.00025; S_FEE = 0.00025; STAX = 0.0005
THR = 0.03; TRAIL = 0.20
FUND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fundamentals_70stocks")

def load_sector_map():
    csvs = sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
    sm = {}
    with open(os.path.join(FUND_DIR, csvs[-1]), 'r', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            sm[r['code'].strip()] = r.get('sector','').strip()
    return sm

def gen_signals(stocks):
    sigs = {}
    for code, info in stocks.items():
        c = info['close']; dates = info['dates']
        ma3=calc_ma(c,3); ma7=calc_ma(c,7); ma5=calc_ma(c,5)
        ma10=calc_ma(c,10); ma20=calc_ma(c,20)
        sig = {}
        for i in range(len(c)):
            if math.isnan(ma3[i]) or math.isnan(ma7[i]) or ma7[i]==0:
                dev = float('nan')
            else:
                dev = (ma3[i]-ma7[i])/abs(ma7[i])
            ok = not math.isnan(dev) and dev >= THR
            if ok and i>=20:
                m5 = ma5[i] if not math.isnan(ma5[i]) else float('nan')
                m10 = ma10[i] if not math.isnan(ma10[i]) else float('nan')
                m20 = ma20[i] if not math.isnan(ma20[i]) else float('nan')
                ok = (not math.isnan(m5) and not math.isnan(m10)
                      and not math.isnan(m20) and m5 > m10 > m20)
            elif ok and i<20:
                ok = False
            sig[dates[i]] = {'buy': ok, 'dev': dev}
        sigs[code] = sig
    return sigs

def backtest(stocks, sigs, sm, dates, trail, max_pos=5):
    cash = INIT; slot = INIT/max_pos; pos = {}; eq = []; trades = []
    idx = {c: {d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    for di, dt in enumerate(dates):
        for code, p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px = stocks[code]['close'][idx[code][dt]]
            if px > p['peak']: p['peak'] = px
            if px <= p['peak']*(1-trail):
                sp = px*(1-SLIP-S_FEE-STAX)
                cash += p['shares']*sp
                trades.append({
                    'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,
                    'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'exit':'trail','hold':di-p['bi']})
                del pos[code]
        if len(pos) < max_pos and cash >= slot*0.99:
            hc = set(pos.keys()); hs = {sm.get(c,'') for c in hc}
            cand = []
            for code in stocks:
                if code in hc: continue
                s = sm.get(code,'')
                if s and s in hs: continue
                sg = sigs.get(code,{}).get(dt,{})
                if sg.get('buy'): cand.append((code, sg['dev']))
            cand.sort(key=lambda x: x[1], reverse=True)
            for code, dev in cand[:max_pos-len(pos)]:
                if cash < slot*0.99: break
                si = idx[code][dt]; raw = stocks[code]['close'][si]
                bp = raw*(1+SLIP+B_FEE); sh = slot/bp; cash -= slot
                pos[code] = {'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}
                hc.add(code); hs.add(sm.get(code,''))
        cash *= (1+RF/TD)
        pv = sum(p['shares']*stocks[c]['close'][idx[c][dt]]
                 for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date':dt,'equity':cash+pv,'pos':len(pos),'cash':cash})
    ld = dates[-1]
    for code, p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px = stocks[code]['close'][idx[code][ld]]
            sp = px*(1-SLIP-S_FEE-STAX); cash += p['shares']*sp
            trades.append({
                'code':code,'name':stocks[code]['name'],
                'bd':p['bd'],'sd':ld,
                'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                'exit':'final','hold':len(dates)-1-p['bi']})
    pos.clear()
    if eq: eq[-1]['equity']=cash; eq[-1]['pos']=0
    v = [d['equity'] for d in eq]
    tr = (v[-1]-v[0])/v[0]; rs = [(v[i]-v[i-1])/v[i-1] for i in range(1,len(v)) if v[i-1]>0]
    y = len(rs)/TD; cagr = (v[-1]/v[0])**(1/y)-1 if y>0 else 0
    mu = sum(rs)/len(rs) if rs else 0
    sd = (sum((r-mu)**2 for r in rs)/len(rs))**0.5 if rs else 0
    sh = (mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk=v[0]; mdd=0.0
    for x in v:
        if x>pk: pk=x
        dd=(pk-x)/pk
        if dd>mdd: mdd=dd
    cm = cagr/mdd if mdd>0 else float('inf')
    w = sum(1 for t in trades if t['ret']>0)
    return {
        'equity':eq,'trades':trades,'tr':tr,'cagr':cagr,'sh':sh,
        'mdd':mdd,'calmar':cm,
        'nt':len(trades),'wr':w/len(trades) if trades else 0,
        'hp':sum(1 for d in eq if d['pos']>0)/len(eq),
    }

def annual(eq):
    yr = defaultdict(lambda:{'s':None,'e':None})
    for d in eq:
        yk = d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s']=d['equity']
        yr[yk]['e']=d['equity']
    return {y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sm = load_sector_map()

    # Load both pools
    all_stocks = load_prices(stock_filter=None)
    stocks_44 = {c:i for c,i in all_stocks.items()
                 if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
    stocks_64 = {c:i for c,i in all_stocks.items() if len(i['dates'])>=100}

    # For 64-all: restrict to stocks with >=200 bars (skip very new ones)
    stocks_64_filtered = {c:i for c,i in stocks_64.items() if len(i['dates'])>=200}
    print(f'[DATA] 44-old: {len(stocks_44)} | 64-all(≥200bars): {len(stocks_64_filtered)}')

    # Run 44-old
    cd44 = get_common_dates(stocks_44)
    sigs44 = gen_signals(stocks_44)
    bt44 = backtest(stocks_44, sigs44, sm, cd44, TRAIL)
    print(f'[44-old] {len(cd44)}d  S={bt44["sh"]:.4f} R={bt44["tr"]*100:.1f}% DD={bt44["mdd"]*100:.1f}% CM={bt44["calmar"]:.3f} Trd={bt44["nt"]}')

    # Run 64-all
    cd64 = get_common_dates(stocks_64_filtered)
    cd64 = [d for d in cd64 if d >= '20200121']  # MA20 needs 20 prior bars
    sigs64 = gen_signals(stocks_64_filtered)
    bt64 = backtest(stocks_64_filtered, sigs64, sm, cd64, TRAIL)
    print(f'[64-all] {len(stocks_64_filtered)}stk/{len(cd64)}d  S={bt64["sh"]:.4f} R={bt64["tr"]*100:.1f}% DD={bt64["mdd"]*100:.1f}% CM={bt64["calmar"]:.3f} Trd={bt64["nt"]}')

    # Also run 44-old on same date range as 64 for fair comparison
    cd44_common = [d for d in cd44 if d in cd64]
    bt44_fair = backtest(stocks_44, sigs44, sm, cd44_common, TRAIL)
    print(f'[44-old fair] {len(cd44_common)}d  S={bt44_fair["sh"]:.4f} R={bt44_fair["tr"]*100:.1f}% DD={bt44_fair["mdd"]*100:.1f}% Trd={bt44_fair["nt"]}')

    # ========================================
    # Side-by-side
    # ========================================
    print(f'\n{"="*95}')
    print(f'  MA3/MA7金叉(≥3%) + 多头排列(MA5>MA10>MA20) + Trail=20%')
    print(f'  44-old vs 64-all 等权对比')
    print(f'{"="*95}')
    print(f'  {"Pool":<18s} {"Stk":>4s} {"Days":>6s} {"Sharpe":>7s} {"Ret":>9s} {"MDD":>7s} {"Calmar":>7s} {"Trd":>5s} {"Win":>5s} {"Hold":>6s}')
    print(f'  {"─"*80}')
    for label, bt, n, nd in [
        ('44-old (full)', bt44, len(stocks_44), len(cd44)),
        ('44-old (fair)', bt44_fair, len(stocks_44), len(cd44_common)),
        ('64-all (≥200b)', bt64, len(stocks_64_filtered), len(cd64)),
    ]:
        print(f'  {label:<18s} {n:>4d} {nd:>6d} {bt["sh"]:>7.3f} {bt["tr"]*100:>8.2f}% {bt["mdd"]*100:>6.2f}% {bt["calmar"]:>7.3f} {bt["nt"]:>5d} {bt["wr"]*100:>4.0f}% {bt["hp"]*100:>5.1f}%')

    # Annual comparison
    print(f'\n{"─"*95}')
    print(f'  年度收益对比')
    print(f'  {"Year":<6s} {"44-old(full)":>13s} {"44-old(fair)":>13s} {"64-all":>13s}')
    print(f'  {"─"*48}')
    yr44 = annual(bt44['equity'])
    yr44f = annual(bt44_fair['equity'])
    yr64 = annual(bt64['equity'])
    for y in sorted(set(list(yr44.keys())+list(yr64.keys()))):
        r44 = yr44.get(y,0); r44f = yr44f.get(y,0); r64 = yr64.get(y,0)
        print(f'  {y:<6s} {r44:>+12.1f}% {r44f:>+12.1f}% {r64:>+12.1f}%')

    # New stocks impact
    new_codes = set(stocks_64_filtered.keys()) - set(stocks_44.keys())
    print(f'\n{"─"*95}')
    print(f'  64-all 新增 {len(new_codes)} 只科创新股贡献:')
    new_trades = [t for t in bt64['trades'] if t['code'] in new_codes]
    if new_trades:
        avg_ret = sum(t['ret'] for t in new_trades)/len(new_trades)*100
        wins = sum(1 for t in new_trades if t['ret']>0)
        print(f'  {len(new_trades)} trades, avg ret={avg_ret:+.1f}%, win={wins}/{len(new_trades)}')
        print(f'  Top 5:')
        for t in sorted(new_trades, key=lambda x: x['ret'], reverse=True)[:5]:
            print(f'    {t["name"]:<12s} {t["bd"]}→{t["sd"]}  {t["ret"]*100:>+7.1f}%  {t["hold"]:>4d}d')
        print(f'  Worst 3:')
        for t in sorted(new_trades, key=lambda x: x['ret'])[:3]:
            print(f'    {t["name"]:<12s} {t["bd"]}→{t["sd"]}  {t["ret"]*100:>+7.1f}%  {t["hold"]:>4d}d')
    else:
        print(f'  (no trades from new stocks)')

    # Sector diversity
    for label, bt in [('44-old', bt44), ('64-all', bt64)]:
        secs = defaultdict(int)
        for t in bt['trades']:
            secs[sm.get(t['code'],'?')] += 1
        print(f'\n  {label} sectors: {len(secs)} unique / {bt["nt"]} trades')

    # Signal stats
    n64_sig = sum(sum(1 for s in sigs64[c].values() if s['buy']) for c in sigs64)
    n44_sig = sum(sum(1 for s in sigs44[c].values() if s['buy']) for c in sigs44)
    print(f'\n  Signal density: 44-old={n44_sig} ({n44_sig/len(stocks_44):.0f}/stk) | 64-all={n64_sig} ({n64_sig/len(stocks_64_filtered):.0f}/stk)')

    print(f'\n{"="*95}\n  Done!\n{"="*95}')

if __name__ == '__main__':
    main()
