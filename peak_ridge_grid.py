"""
峰岭因子参数网格搜索
====================
可调参数:
  ① 喷发倍数: mean + K×std   (K ∈ 0.5, 1.0, 1.5, 2.0)
  ② 回溯窗口: LOOKBACK 天    (10, 14, 21, 30)
  ③ 因子形态: peak/ridge | peak_only | ridge_only | peak/(peak+ridge)
  ④ Trail: 止损比例          (15%, 20%, 25%)
  ⑤ 重排周期: 每N天           (10, 21, 42)
"""
import sys, io, os, math
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from data_loader import load_prices, calc_ma, get_common_dates

INIT = 10_000_000; RF = 0.025; TD = 252
SLIP = 0.003; B_FEE = 0.00025; S_FEE = 0.00025; STAX = 0.0005
MAX_POS = 5
FUND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fundamentals_70stocks")

def load_sector_map():
    import csv
    csvs = sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
    sm = {}
    with open(os.path.join(FUND_DIR, csvs[-1]), 'r', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f): sm[r['code'].strip()] = r.get('sector','').strip()
    return sm

def calc_factor(stocks, lookback=21, k=1.0, ftype='ratio'):
    """
    lookback: 回溯天数
    k: 喷发倍数 (mean + k*std)
    ftype: 'ratio'=peak/ridge, 'peak'=peak only, 'ridge'=ridge only,
           'peak_frac'=peak/(peak+ridge)
    """
    factor = {}
    for code, info in stocks.items():
        vols = info['volume']; dates = info['dates']; n = len(vols)
        ma_vol = calc_ma(vols, max(lookback, 20))
        vals = {}
        for i in range(n):
            if i < lookback or math.isnan(ma_vol[i]): continue
            # Rolling window for threshold
            win_len = min(20, i+1)
            win = vols[i-win_len+1:i+1]
            mu = sum(win)/win_len
            var = sum((v-mu)**2 for v in win)/win_len; std = var**0.5
            thr = ma_vol[i] + k * std

            peak_s = 0.0; ridge_s = 0.0
            for j in range(max(0, i-lookback+1), i+1):
                erupt = vols[j] >= thr
                if erupt:
                    prev_erupt = (j>0 and vols[j-1] >= thr)
                    if prev_erupt: ridge_s += vols[j]
                    else: peak_s += vols[j]

            if ftype == 'peak':
                vals[dates[i]] = peak_s
            elif ftype == 'ridge':
                vals[dates[i]] = ridge_s
            elif ftype == 'peak_frac':
                total = peak_s + ridge_s
                vals[dates[i]] = peak_s/total if total > 0 else float('nan')
            else:  # ratio
                vals[dates[i]] = peak_s/ridge_s if ridge_s > 0 else float('nan')
        factor[code] = vals
    return factor

def backtest(stocks, factor, sm, dates, trail, max_pos, rebal):
    cash = INIT; slot = INIT/max_pos; pos = {}; eq = []; trades = []
    idx = {c: {d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    for di, dt in enumerate(dates):
        for code, p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px = stocks[code]['close'][idx[code][dt]]
            if px > p['peak']: p['peak'] = px
            if px <= p['peak']*(1-trail):
                sp = px*(1-SLIP-S_FEE-STAX); cash += p['shares']*sp
                trades.append({'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,
                    'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'exit':'trail','hold':di-p['bi']})
                del pos[code]
        if di % rebal == 0:
            cand = [(c, factor.get(c,{}).get(dt, float('nan'))) for c in stocks]
            cand = [(c,s) for c,s in cand if not math.isnan(s) and c in idx and dt in idx[c]]
            # All factor types: higher is better
            cand.sort(key=lambda x: x[1], reverse=True)
            top_codes = set(c for c,_ in cand[:max_pos])
            for code in list(pos.keys()):
                if code not in top_codes:
                    px = stocks[code]['close'][idx[code][dt]]; sp = px*(1-SLIP-S_FEE-STAX)
                    cash += pos[code]['shares']*sp
                    trades.append({'code':code,'name':stocks[code]['name'],
                        'bd':pos[code]['bd'],'sd':dt,
                        'ret':(sp-pos[code]['bp'])/pos[code]['bp'] if pos[code]['bp']>0 else 0,
                        'exit':'rebalance','hold':di-pos[code]['bi']})
                    del pos[code]
            hc=set(pos.keys()); hs={sm.get(c,'') for c in hc}
            for code, sc in cand:
                if len(pos)>=max_pos: break
                if code in hc: continue
                s=sm.get(code,'')
                if s and s in hs: continue
                if cash<slot*0.99: break
                raw = stocks[code]['close'][idx[code][dt]]
                bp=raw*(1+SLIP+B_FEE); sh=slot/bp; cash-=slot
                pos[code]={'shares':sh,'bp':bp,'peak':raw,'bd':dt,'bi':di}
                hc.add(code); hs.add(s)
        cash *= (1+RF/TD)
        pv = sum(p['shares']*stocks[c]['close'][idx[c][dt]]
                 for c,p in pos.items() if c in idx and dt in idx[c])
        eq.append({'date':dt,'equity':cash+pv,'pos':len(pos)})
    ld = dates[-1]
    for code,p in list(pos.items()):
        if code in idx and ld in idx[code]:
            px = stocks[code]['close'][idx[code][ld]]; sp = px*(1-SLIP-S_FEE-STAX)
            cash += p['shares']*sp
            trades.append({'code':code,'name':stocks[code]['name'],
                'bd':p['bd'],'sd':ld,
                'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
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
    return {'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,
        'nt':len(trades),'wr':w/len(trades) if trades else 0,
        'hp':sum(1 for d in eq if d['pos']>0)/len(eq)}

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sm = load_sector_map()
    all_s = load_prices(stock_filter=None)
    stocks = {c:i for c,i in all_s.items()
              if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
    cd = get_common_dates(stocks)
    print(f'[DATA] {len(stocks)}stk {len(cd)}d ({cd[0]}~{cd[-1]}, {len(cd)/252:.1f}yr)')

    # ============================================================
    # Baseline
    # ============================================================
    ks =       [0.5, 1.0, 1.5, 2.0]
    lookbacks = [10, 14, 21, 30]
    ftypes =   [('ratio', '峰/岭比'), ('peak', '峰量'), ('ridge', '岭量'), ('peak_frac', '峰占比')]
    trails =   [0.15, 0.20, 0.25]
    rebals =   [10, 21, 42]

    DEFAULT = {'k':1.0, 'lookback':21, 'ftype':'ratio', 'trail':0.20, 'rebal':21}

    # Strategy: test one param at a time holding others at default
    to_test = [
        ('喷发倍数 K (mean + K*std)', 'k', ks),
        ('回溯窗口 (days)', 'lookback', lookbacks),
        ('因子形态', 'ftype', [('ratio','峰/岭比'), ('peak','峰量'), ('ridge','岭量'), ('peak_frac','峰占比')]),
        ('Trail 止损', 'trail', trails),
        ('重排周期 (days)', 'rebal', rebals),
    ]

    print(f'\n{"="*90}')
    print(f'  峰岭因子参数网格 · 单变量敏感度分析')
    print(f'  Baseline: K=1.0, lookback=21, ratio, Trail=20%, rebal=21')
    print(f'{"="*90}')

    # Pre-compute all factor variants
    factor_cache = {}
    print(f'\n  Pre-computing factors...')
    for lb in lookbacks:
        for k in ks:
            for ft, _ in ftypes:
                key = (lb, k, ft)
                if key not in factor_cache:
                    fac = calc_factor(stocks, lookback=lb, k=k, ftype=ft)
                    nv = sum(len(v) for v in fac.values())
                    factor_cache[key] = fac
                    print(f'    lb={lb} k={k} {ft}: {nv} vals')

    # For each parameter, test all values with DEFAULT for others
    for title, param, values in to_test:
        print(f'\n{"─"*90}')
        print(f'  {title}')
        print(f'  {"─"*90}')
        print(f'  {"Value":<18s} {"Sharpe":>7s} {"Ret":>9s} {"MDD":>7s} '
              f'{"Calmar":>7s} {"Trd":>5s} {"Win":>5s} {"Hold":>6s}')
        print(f'  {"─"*62}')

        for raw_val in values:
            if param == 'ftype':
                ft, ft_label = raw_val
            else:
                ft = DEFAULT['ftype']
                ft_label = 'ratio'

            cfg = {**DEFAULT}
            if param == 'ftype':
                cfg['ftype'] = ft
                cfg['label'] = ft_label
            else:
                cfg[param] = raw_val
                cfg['label'] = str(raw_val)

            fac_key = (cfg['lookback'], cfg['k'], cfg['ftype'])
            fac = factor_cache.get(fac_key)
            if fac is None:
                fac = calc_factor(stocks, lookback=cfg['lookback'], k=cfg['k'], ftype=cfg['ftype'])

            bt = backtest(stocks, fac, sm, cd, cfg['trail'], MAX_POS, cfg['rebal'])
            s = bt
            label = cfg['label']
            if param == 'ftype': label = ft_label
            elif param == 'k': label = f'K={raw_val}'
            elif param == 'lookback': label = f'{raw_val}d'
            elif param == 'trail': label = f'{raw_val:.0%}'
            elif param == 'rebal': label = f'{raw_val}d'

            # Mark default
            is_default = (cfg['k']==DEFAULT['k'] and cfg['lookback']==DEFAULT['lookback']
                          and cfg['ftype']==DEFAULT['ftype'] and cfg['trail']==DEFAULT['trail']
                          and cfg['rebal']==DEFAULT['rebal'])
            tag = ' ← BASELINE' if is_default else ''
            best_tag = ''
            print(f'  {label:<18s} {s["sh"]:>7.3f} {s["tr"]*100:>8.2f}% '
                  f'{s["mdd"]*100:>6.2f}% {s["calmar"]:>7.3f} '
                  f'{s["nt"]:>5d} {s["wr"]*100:>4.0f}% {s["hp"]*100:>5.1f}%{tag}{best_tag}')

    # ============================================================
    # Top combinations from best per-category
    # ============================================================
    print(f'\n{"─"*90}')
    print(f'  综合最优组合')

    # Best K from first test, etc. - actually let's just try a few promising combos
    combos = [
        ('Baseline', 1.0, 21, 'ratio', 0.20, 21),
        ('Best K=1.5', 1.5, 21, 'ratio', 0.20, 21),
        ('Best K=2.0', 2.0, 21, 'ratio', 0.20, 21),
        ('K=1.5 lb=14', 1.5, 14, 'ratio', 0.20, 21),
        ('K=1.5 lb=30', 1.5, 30, 'ratio', 0.20, 21),
        ('peak_only K=1.5', 1.5, 21, 'peak', 0.20, 21),
        ('peak_frac K=1.5', 1.5, 21, 'peak_frac', 0.20, 21),
        ('K=1.5 trail=15%', 1.5, 21, 'ratio', 0.15, 21),
        ('K=1.5 rebal=10', 1.5, 21, 'ratio', 0.20, 10),
    ]

    print(f'  {"Combo":<25s} {"Sharpe":>7s} {"Ret":>9s} {"MDD":>7s} '
          f'{"Calmar":>7s} {"Trd":>5s} {"Win":>5s}')
    print(f'  {"─"*65}')
    for name, k, lb, ft, tr, rb in combos:
        fac = calc_factor(stocks, lookback=lb, k=k, ftype=ft)
        bt = backtest(stocks, fac, sm, cd, tr, MAX_POS, rb)
        s = bt; best = ' ✨' if bt['sh'] == max(bt['sh'] for _, *_, bt2 in
            [(name, k, lb, ft, tr, rb,
              (lambda: backtest(stocks, calc_factor(stocks,lookback=lb,k=k,ftype=ft), sm, cd, tr, MAX_POS, rb))())
             for name, k, lb, ft, tr, rb in combos]) else ''
        print(f'  {name:<25s} {s["sh"]:>7.3f} {s["tr"]*100:>8.2f}% '
              f'{s["mdd"]*100:>6.2f}% {s["calmar"]:>7.3f} '
              f'{s["nt"]:>5d} {s["wr"]*100:>4.0f}%')

    print(f'\n{"="*90}\n  Done!\n{"="*90}')

if __name__ == '__main__':
    main()
