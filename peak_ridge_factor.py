"""
峰岭成交比因子 · 日线近似版 · 截面排名选股
================================================
因子: 峰岭成交比 = SUM(21日峰日成交额) / SUM(21日岭日成交额)
喷发: 日成交量 > MA20(成交量) + 1×std(成交量)
峰:   当天喷发 且 前一天不喷发（孤立喷发）
岭:   当天喷发 且 前一天也喷发（连续喷发）

策略: 每21交易日因子重排 → Top5 等权 + 多头排列过滤 + 赛道去重
卖出: Trail 20%
对比: 乖离率排序 vs 因子排序
"""
import sys, io, os, math
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from data_loader import load_prices, calc_ma, get_common_dates

INIT = 10_000_000; RF = 0.025; TD = 252
SLIP = 0.003; B_FEE = 0.00025; S_FEE = 0.00025; STAX = 0.0005
TRAIL = 0.20; MAX_POS = 5; REBAL = 21
FUND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fundamentals_70stocks")

def load_sector_map():
    csvs = sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
    sm = {}
    import csv
    with open(os.path.join(FUND_DIR, csvs[-1]), 'r', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f): sm[r['code'].strip()] = r.get('sector','').strip()
    return sm

def calc_peak_ridge(stocks):
    """日线近似: 峰岭成交比 = 21日峰日成交量之和 / 21日岭日成交量之和"""
    factor = {}
    for code, info in stocks.items():
        vols = info['volume']; dates = info['dates']; n = len(vols)
        ma_vol = calc_ma(vols, 20)
        vals = []
        for i in range(n):
            if i < 20 or math.isnan(ma_vol[i]):
                vals.append(float('nan')); continue
            win = vols[i-19:i+1]; mu=sum(win)/20
            var = sum((v-mu)**2 for v in win)/20; std = var**0.5
            thr = ma_vol[i] + std
            peak_s=0.0; ridge_s=0.0
            for j in range(max(0,i-20), i+1):
                erupt = (vols[j] >= thr)
                if erupt:
                    prev_erupt = (j>0 and vols[j-1] >= thr)
                    if prev_erupt: ridge_s += vols[j]
                    else: peak_s += vols[j]
            vals.append(peak_s/ridge_s if ridge_s>0 else float('nan'))
        factor[code] = {dates[i]:v for i,v in enumerate(vals) if not math.isnan(v)}
    return factor

def gen_filter(stocks):
    """MA5>MA10>MA20 + MA3/MA7乖离率≥3%"""
    flt = {}
    for code, info in stocks.items():
        c=info['close']; dates=info['dates']
        ma3=calc_ma(c,3); ma5=calc_ma(c,5); ma7=calc_ma(c,7)
        ma10=calc_ma(c,10); ma20=calc_ma(c,20)
        sig = {}
        for i in range(len(c)):
            ok = (i>=20 and not math.isnan(ma5[i]) and not math.isnan(ma10[i])
                  and not math.isnan(ma20[i]) and ma5[i]>ma10[i]>ma20[i])
            if ok:
                if math.isnan(ma3[i]) or math.isnan(ma7[i]) or ma7[i]==0:
                    ok=False
                else:
                    ok = ((ma3[i]-ma7[i])/abs(ma7[i]) >= 0.03)
            sig[dates[i]] = ok
        flt[code] = sig
    return flt

def backtest(stocks, rank_fn, sm, dates, trail, max_pos, rebal):
    """rank_fn(code, date) → score or None. Higher score = higher priority."""
    cash=INIT; slot=INIT/max_pos; pos={}; eq=[]; trades=[]
    idx = {c:{d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    for di, dt in enumerate(dates):
        # Trail exits
        for code, p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px = stocks[code]['close'][idx[code][dt]]
            if px>p['peak']: p['peak']=px
            if px <= p['peak']*(1-trail):
                sp = px*(1-SLIP-S_FEE-STAX); cash += p['shares']*sp
                trades.append({'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'exit':'trail','hold':di-p['bi']})
                del pos[code]
        # Rebalance
        if di % rebal == 0:
            cand = [(c, rank_fn(c, dt)) for c in stocks]
            cand = [(c,s) for c,s in cand if s is not None]
            cand.sort(key=lambda x: x[1], reverse=True)
            top_codes = set(c for c,_ in cand[:max_pos])
            # Sell non-top
            for code in list(pos.keys()):
                if code not in top_codes:
                    if code in idx and dt in idx[code]:
                        px = stocks[code]['close'][idx[code][dt]]
                        sp = px*(1-SLIP-S_FEE-STAX); cash += pos[code]['shares']*sp
                        trades.append({'code':code,'name':stocks[code]['name'],
                            'bd':pos[code]['bd'],'sd':dt,
                            'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                            'exit':'rebalance','hold':di-pos[code]['bi']})
                        del pos[code]
            # Buy new
            hc=set(pos.keys()); hs={sm.get(c,'') for c in hc}
            for code, sc in cand:
                if len(pos)>=max_pos: break
                if code in hc: continue
                s=sm.get(code,'')
                if s and s in hs: continue
                if cash<slot*0.99: break
                si=idx[code][dt]; raw=stocks[code]['close'][si]
                bp=raw*(1+SLIP+B_FEE); sh=slot/bp; cash-=slot
                pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di,'score':sc}
                hc.add(code); hs.add(s)
        cash *= (1+RF/TD)
        pv = sum(p['shares']*stocks[c]['close'][idx[c][dt]]
                 for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date':dt,'equity':cash+pv,'pos':len(pos)})
    ld = dates[-1]
    for code,p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px=stocks[code]['close'][idx[code][ld]]
            sp=px*(1-SLIP-S_FEE-STAX); cash+=p['shares']*sp
            trades.append({'code':code,'name':stocks[code]['name'],
                'bd':p['bd'],'sd':ld,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                'exit':'final','hold':len(dates)-1-p['bi']})
    pos.clear()
    if eq: eq[-1]['equity']=cash; eq[-1]['pos']=0
    v=[d['equity'] for d in eq]
    tr=(v[-1]-v[0])/v[0]; rs=[(v[i]-v[i-1])/v[i-1] for i in range(1,len(v)) if v[i-1]>0]
    y=len(rs)/TD; cagr=(v[-1]/v[0])**(1/y)-1 if y>0 else 0
    mu=sum(rs)/len(rs) if rs else 0
    sd=(sum((r-mu)**2 for r in rs)/len(rs))**0.5 if rs else 0
    sh=(mu*TD-RF)/(sd*(TD**0.5)) if sd>0 else 0
    pk=v[0]; mdd=0.0
    for x in v:
        if x>pk: pk=x
        dd=(pk-x)/pk
        if dd>mdd: mdd=dd
    cm=cagr/mdd if mdd>0 else float('inf')
    w=sum(1 for t in trades if t['ret']>0)
    exits = {}
    for e in set(t['exit'] for t in trades):
        subset = [t for t in trades if t['exit']==e]
        exits[e] = {'cnt':len(subset), 'avg_ret':sum(t['ret'] for t in subset)/len(subset)*100}
    return {'equity':eq,'trades':trades,'tr':tr,'cagr':cagr,'sh':sh,
        'mdd':mdd,'calmar':cm,'nt':len(trades),
        'wr':w/len(trades) if trades else 0,
        'hp':sum(1 for d in eq if d['pos']>0)/len(eq),
        'exits':exits}

def annual(eq):
    yr=defaultdict(lambda:{'s':None,'e':None})
    for d in eq:
        yk=d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s']=d['equity']
        yr[yk]['e']=d['equity']
    return {y:(v['e']-v['s'])/v['s']*100 for y,v in yr.items() if v['s'] and v['e'] and v['s']>0}

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sm = load_sector_map()
    all_s = load_prices(stock_filter=None)
    stocks = {c:i for c,i in all_s.items()
              if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
    cd = get_common_dates(stocks)
    print(f'[DATA] {len(stocks)}stk {len(cd)}d ({cd[0]}~{cd[-1]}, {len(cd)/252:.1f}yr)')

    # Calc factor
    print(f'\n[FACTOR] 峰岭成交比 (日线近似)...')
    factor = calc_peak_ridge(stocks)
    nv = sum(1 for c in factor for _ in factor[c])
    print(f'  Valid values: {nv}')

    # Calc MA filter
    print(f'[FILTER] MA多头+金叉...')
    flt = gen_filter(stocks)
    nf = sum(1 for c in flt for d,v in flt[c].items() if v)
    print(f'  Passing: {nf} stock-days')

    # ==============================
    # Rank functions
    # ==============================
    def rank_golden_cross(code, dt):
        """乖离率排序 (baseline)."""
        if not flt.get(code, {}).get(dt, False): return None
        # Get 乖离率
        info = stocks[code]; dates = info['dates']
        if dt not in dates: return None
        i = dates.index(dt)
        if i < 3: return None
        c = info['close']; ma3 = calc_ma(c,3); ma7 = calc_ma(c,7)
        if math.isnan(ma3[i]) or math.isnan(ma7[i]) or ma7[i]==0: return None
        return (ma3[i]-ma7[i])/abs(ma7[i])

    def rank_factor(code, dt):
        """因子排序."""
        if not flt.get(code, {}).get(dt, False): return None
        fv = factor.get(code, {}).get(dt, float('nan'))
        return None if math.isnan(fv) else fv

    def rank_factor_only(code, dt):
        """因子排序(无MA过滤)."""
        fv = factor.get(code, {}).get(dt, float('nan'))
        return None if math.isnan(fv) else fv

    # ==============================
    # Run all variants
    # ==============================
    print(f'\n{"="*90}')
    print(f'  峰岭成交比因子 · 截面排名 · 每{REBAL}日重排')
    print(f'  Trail 20% | Top{MAX_POS} | 赛道去重 | 多头+金叉过滤')
    print(f'{"="*90}')

    configs = [
        ('乖离率降序 (baseline)', rank_golden_cross, False),
        ('因子降序 (峰多=强)', rank_factor, False),
        ('因子升序 (岭多=强)', rank_factor, True),
        ('因子降序 无MA过滤', rank_factor_only, False),
    ]

    results = {}
    for label, rfn, reverse in configs:
        rfn_orig = rfn
        if reverse:
            def _rev(c,d, orig=rfn_orig):
                v = orig(c,d)
                return -v if v is not None else None
            rfn = _rev
        bt = backtest(stocks, rfn, sm, cd, TRAIL, MAX_POS, REBAL)
        results[label] = bt
        s = bt
        print(f'  [{label:<30s}] S={s["sh"]:>7.3f} R={s["tr"]*100:>7.1f}% '
              f'DD={s["mdd"]*100:>5.1f}% CM={s["calmar"]:>6.3f} '
              f'Trd={s["nt"]:>4d} Win={s["wr"]*100:>3.0f}% Hold={s["hp"]*100:>4.1f}%')

    # ==============================
    # Detail comparison
    # ==============================
    baseline = results['乖离率降序 (baseline)']
    best_f = results['因子降序 (峰多=强)']

    print(f'\n{"─"*90}')
    print(f'  vs Baseline 对比')
    print(f'{"─"*90}')
    for m, k, fmt in [('Sharpe','sh','.3f'),('Total Ret','tr','.1%'),('CAGR','cagr','.1%'),
                       ('Max DD','mdd','.1%'),('Calmar','calmar','.3f'),
                       ('Trades','nt','d'),('Win Rate','wr','.0%')]:
        bv=baseline[k]; fv=best_f[k]
        if fmt=='.1%': bv*=100; fv*=100
        elif fmt=='.0%': bv*=100; fv*=100
        d = fv-bv; sign='+' if d>0 else ''
        print(f'  {m:<15s} {bv:{fmt}} → {fv:{fmt}} ({sign}{d:{fmt}})')

    # Annual
    print(f'\n{"─"*90}')
    print(f'  年度收益对比')
    yr_b = annual(baseline['equity'])
    yr_f = annual(best_f['equity'])
    print(f'  {"Year":<6s} {"Baseline":>10s} {"因子降序":>10s} {"Δ":>10s}')
    print(f'  {"─"*38}')
    for y in sorted(set(list(yr_b.keys())+list(yr_f.keys()))):
        rb=yr_b.get(y,0); rf=yr_f.get(y,0)
        print(f'  {y:<6s} {rb:>+9.1f}% {rf:>+9.1f}% {rf-rb:>+9.1f}%')

    # Exit analysis
    print(f'\n{"─"*90}')
    print(f'  退出方式分布')
    for label in ['乖离率降序 (baseline)', '因子降序 (峰多=强)']:
        bt = results[label]
        print(f'\n  {label}:')
        for e, d in bt['exits'].items():
            print(f'    {e:<12s} {d["cnt"]:>4d}笔  均收益={d["avg_ret"]:>+6.1f}%')

    # Factor distribution in trades
    print(f'\n{"─"*90}')
    print(f'  因子降序策略 — Trade Factor Analysis')
    ts = sorted(best_f['trades'], key=lambda x: x['ret'], reverse=True)
    for tag, subset in [('Top 5', ts[:5]), ('Bottom 5', ts[-5:])]:
        fvs = [t.get('score', float('nan')) for t in subset]
        avg_f = sum(v for v in fvs if not math.isnan(v))/max(1,sum(1 for v in fvs if not math.isnan(v)))
        print(f'  {tag}: avg factor={avg_f:.4f}')
        for t in subset:
            print(f'    {t["name"]:<12s} {t["bd"]}→{t["sd"]}  {t["ret"]*100:>+7.1f}%  f={t.get("score",float("nan")):.4f}')

    print(f'\n{"="*90}\n  Done!\n{"="*90}')

if __name__ == '__main__':
    main()
