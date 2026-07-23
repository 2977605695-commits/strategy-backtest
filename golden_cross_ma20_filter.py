"""
MA3/MA7金叉 · 均线多头趋势过滤 · 多方案对比
==================================================
Buy: MA3/MA7乖离率≥3% AND 均线过滤条件, Top5追强, 赛道不重复
Sell: Trail 20%
测试N种均线过滤方案
"""
import sys, io, os, math, csv
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from data_loader import load_prices, calc_ma, get_common_dates

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

def gen_signals(stocks, filter_type):
    """
    filter_type:
      'none'          → no MA filter
      'ma20_up'       → today MA20 > yesterday MA20
      'ma5>ma10'      → MA5 > MA10
      'ma10>ma20'     → MA10 > MA20
      'ma5>ma20'      → MA5 > MA20
      'ma5>ma10>ma20' → full 多头排列
    """
    sigs = {}
    for code, info in stocks.items():
        c = info['close']; dates = info['dates']
        ma3 = calc_ma(c,3); ma7 = calc_ma(c,7)
        sig = {}
        # 按需算 MA
        need_ma20 = ('ma20_up' in filter_type or 'ma10>ma20' in filter_type or
                     'ma5>ma20' in filter_type or '>ma20' in filter_type)
        need_ma5 = ('ma5>' in filter_type)
        need_ma10 = ('ma10>' in filter_type or 'ma5>ma10' in filter_type)
        ma5 = calc_ma(c,5) if need_ma5 else None
        ma10 = calc_ma(c,10) if need_ma10 else None
        ma20 = calc_ma(c,20) if need_ma20 else None

        for i in range(len(c)):
            # 乖离率
            if math.isnan(ma3[i]) or math.isnan(ma7[i]) or ma7[i]==0:
                dev = float('nan')
            else:
                dev = (ma3[i]-ma7[i])/abs(ma7[i])
            ok = not math.isnan(dev) and dev >= THR

            if ok and filter_type != 'none':
                # 所有 MA 需要足够数据
                min_i = 20  # 至少 20 根才能判断
                if i < min_i:
                    ok = False
                else:
                    def v(ma, idx):
                        return ma[idx] if ma and not math.isnan(ma[idx]) else float('nan')

                    if filter_type == 'ma20_up':
                        ma20_today = v(ma20, i)
                        ma20_yest = v(ma20, i-1)
                        ok = (not math.isnan(ma20_today) and not math.isnan(ma20_yest)
                              and ma20_today > ma20_yest)
                    elif filter_type == 'ma5>ma10':
                        m5 = v(ma5, i); m10 = v(ma10, i)
                        ok = not math.isnan(m5) and not math.isnan(m10) and m5 > m10
                    elif filter_type == 'ma10>ma20':
                        m10 = v(ma10, i); m20 = v(ma20, i)
                        ok = not math.isnan(m10) and not math.isnan(m20) and m10 > m20
                    elif filter_type == 'ma5>ma20':
                        m5 = v(ma5, i); m20 = v(ma20, i)
                        ok = not math.isnan(m5) and not math.isnan(m20) and m5 > m20
                    elif filter_type == 'ma5>ma10>ma20':
                        m5 = v(ma5, i); m10 = v(ma10, i); m20 = v(ma20, i)
                        ok = (not math.isnan(m5) and not math.isnan(m10)
                              and not math.isnan(m20) and m5 > m10 > m20)
            sig[dates[i]] = {'buy': ok, 'dev': dev}
        sigs[code] = sig
    return sigs

def backtest(stocks, sigs, sm, dates, trail):
    cash = INIT; slot = INIT/5; pos = {}; eq = []; trades = []
    idx = {c: {d:i for i,d in enumerate(stocks[c]['dates'])} for c in stocks}
    for di, dt in enumerate(dates):
        for code, p in list(pos.items()):
            if code not in idx or dt not in idx[code]: continue
            px = stocks[code]['close'][idx[code][dt]]
            if px > p['peak']: p['peak'] = px
            if px <= p['peak']*(1-trail):
                sp = px*(1-SLIP-S_FEE-STAX)
                cash += p['shares']*sp
                trades.append({'code':code,'name':stocks[code]['name'],
                    'bd':p['bd'],'sd':dt,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
                    'exit':'trail','hold':di-p['bi']})
                del pos[code]
        if len(pos) < 5 and cash >= slot*0.99:
            hc = set(pos.keys()); hs = {sm.get(c,'') for c in hc}
            cand = []
            for code in stocks:
                if code in hc: continue
                s = sm.get(code,'')
                if s and s in hs: continue
                sg = sigs.get(code,{}).get(dt,{})
                if sg.get('buy'): cand.append((code, sg['dev']))
            cand.sort(key=lambda x: x[1], reverse=True)
            for code, dev in cand[:5-len(pos)]:
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
            trades.append({'code':code,'name':stocks[code]['name'],
                'bd':p['bd'],'sd':ld,'ret':(sp-p['bp'])/p['bp'] if p['bp']>0 else 0,
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
    return {'equity':eq,'trades':trades,
        'tr':tr,'cagr':cagr,'sh':sh,'mdd':mdd,'calmar':cm,
        'nt':len(trades),'wr':w/len(trades) if trades else 0,
        'hp':sum(1 for d in eq if d['pos']>0)/len(eq)}

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
    all_s = load_prices(stock_filter=None)
    stocks = {c:i for c,i in all_s.items()
              if i['dates'] and i['dates'][0]<='20200103' and len(i['dates'])>=1500}
    cd = get_common_dates(stocks)
    print(f'[DATA] {len(stocks)} stocks, {len(cd)} days ({cd[0]}~{cd[-1]}, {len(cd)/252:.1f}yr)')

    filters = [
        ('none',           '无过滤'),
        ('ma20_up',        'MA20↑ (今日>昨日)'),
        ('ma5>ma10',       'MA5 > MA10'),
        ('ma10>ma20',      'MA10 > MA20'),
        ('ma5>ma20',       'MA5 > MA20'),
        ('ma5>ma10>ma20',  'MA5 > MA10 > MA20 (多头排列)'),
    ]

    print(f'\n{"="*95}')
    print(f'  MA3/MA7金叉(≥3%) + Trail=20% + Top5追强 + 赛道去重')
    print(f'  测试 {len(filters)} 种均线过滤方案')
    print(f'{"="*95}')

    results = {}
    for ft, label in filters:
        sigs = gen_signals(stocks, ft)
        n_sig = sum(sum(1 for s in sigs[c].values() if s['buy']) for c in sigs)
        n_day = len(set(d for c in sigs for d,s in sigs[c].items() if s['buy']))
        bt = backtest(stocks, sigs, sm, cd, TRAIL)
        results[label] = bt
        print(f'  [{label:<28s}] sig={n_sig:>5d} days={n_day:>4d}  '
              f'S={bt["sh"]:.3f}  Ret={bt["tr"]*100:>6.1f}%  MDD={bt["mdd"]*100:>5.1f}%  '
              f'Calmar={bt["calmar"]:.3f}  Trd={bt["nt"]:>3d}  Win={bt["wr"]*100:>3.0f}%')

    # ========================================
    # 全维度对比表
    # ========================================
    print(f'\n{"─"*95}')
    print(f'  全维度对比 (最佳值高亮 ✨)')
    print(f'{"─"*95}')

    baseline = results['无过滤']
    for metric, key, fmt, better in [
        ('Sharpe', 'sh', '.3f', 'high'),
        ('Total Ret', 'tr', '.1%', 'high'),
        ('Max DD', 'mdd', '.1%', 'low'),
        ('Calmar', 'calmar', '.3f', 'high'),
        ('Trades', 'nt', 'd', 'high'),
        ('Win Rate', 'wr', '.0%', 'high'),
    ]:
        vals = [(label, r[key]) for label, r in results.items()]
        if better == 'high': best_v = max(v[1] for v in vals)
        else: best_v = min(v[1] for v in vals)

        print(f'\n  ── {metric} ──')
        for label, r in results.items():
            v = r[key]
            if fmt == '.1%': v_disp = v*100; bv2 = baseline[key]*100
            elif fmt == '.0%': v_disp = v*100; bv2 = baseline[key]*100
            else: v_disp = v; bv2 = baseline[key]

            # Δ vs baseline
            dv = v_disp - bv2

            # Bar
            all_v = [r2[key] for r2 in results.values()]
            if better == 'high':
                pct = (v - min(all_v)) / (max(all_v) - min(all_v)) if max(all_v) != min(all_v) else 0.5
            else:
                pct = 1 - (v - min(all_v)) / (max(all_v) - min(all_v)) if max(all_v) != min(all_v) else 0.5
            bar_len = int(pct * 20)
            bar = '█' * bar_len

            # Tag
            tag = ' ✨BEST' if v == best_v else ''
            sign = '+' if dv > 0 else ''
            print(f'    {label:<30s} {v_disp:{fmt}}  {sign}{dv:+.2f}  {bar}{tag}')

    # ========================================
    # 年度收益热力图
    # ========================================
    print(f'\n{"─"*95}')
    print(f'  年度收益细表 (绿=正, 红=负)')
    print(f'{"─"*95}')

    all_years = sorted(set(
        y for ft, lbl in filters
        for y in annual(results[lbl]['equity']).keys()
    ))

    # Header
    hdr = f'  {"Filter":<30s}'
    for y in all_years:
        hdr += f' {y:>8s}'
    hdr += f' {"Avg":>8s} {"MaxDD":>7s}'
    print(hdr)
    print(f'  {"─"*75}')

    for ft, label in filters:
        bt = results[label]
        yr = annual(bt['equity'])
        row = f'  {label:<30s}'
        pos_sum = 0
        for y in all_years:
            r = yr.get(y, 0)
            if r > 0: pos_sum += 1
            row += f' {r:>+7.1f}%'
        avg_r = sum(yr.get(y,0) for y in all_years) / len(all_years)
        row += f' {avg_r:>+7.1f}%'
        row += f' {bt["mdd"]*100:>6.1f}%'
        print(row)
        if ft == 'none':
            print(f'  {"─"*75}')

    # ========================================
    # OOS for best filter
    # ========================================
    best_label = max(results, key=lambda x: results[x]['calmar'])
    best = results[best_label]
    best_ft = [ft for ft, label in filters if label == best_label][0]

    print(f'\n{"─"*95}')
    print(f'  🏆 最优方案: {best_label}')
    print(f'     Sharpe={best["sh"]:.4f}  Ret={best["tr"]*100:.2f}%  '
          f'MDD={best["mdd"]*100:.2f}%  Calmar={best["calmar"]:.3f}  '
          f'Trd={best["nt"]}  Win={best["wr"]*100:.0f}%')
    print(f'     vs 无过滤: ΔSharpe={best["sh"]-baseline["sh"]:+.3f}  '
          f'ΔMDD={(best["mdd"]-baseline["mdd"])*100:+.1f}%  '
          f'ΔCalmar={best["calmar"]-baseline["calmar"]:+.3f}')

    print(f'\n  OOS Validation:')
    print(f'  {"Period":<22s} {"Sharpe":>7s} {"Ret":>9s} {"MDD":>7s} {"Trd":>5s} {"Win":>5s}')
    print(f'  {"─"*60}')
    sigs_best = gen_signals(stocks, best_ft)
    for name,ds,de in [
        ('Full Period', None, None),
        ('2020-2022 (Train)', None, '20221231'),
        ('2023-2024 (Valid)', '20230101', '20241231'),
        ('2025-2026 (Test)',  '20250101', None),
    ]:
        dd = [d for d in cd if (not ds or d>=ds) and (not de or d<=de)]
        bt = backtest(stocks, sigs_best, sm, dd, TRAIL)
        print(f'  {name:<22s} {bt["sh"]:>7.3f} {bt["tr"]*100:>8.2f}% '
              f'{bt["mdd"]*100:>6.2f}% {bt["nt"]:>5d} {bt["wr"]*100:>4.0f}%')

    # ========================================
    # 叠加方案: 宽松(MA5>MA20) vs 严格(MA5>MA10>MA20)
    # ========================================
    print(f'\n{"─"*95}')
    print(f'  🎯 策略建议')
    print(f'{"─"*95}')

    # Find best balance (highest Sharpe with MDD reduction)
    best_sharpe = max(results, key=lambda x: results[x]['sh'])
    sharpe_r = results[best_sharpe]
    print(f'  最高夏普:  {best_sharpe} → Sharpe={sharpe_r["sh"]:.3f} '
          f'MDD={sharpe_r["mdd"]*100:.1f}%')

    best_calmar = max(results, key=lambda x: results[x]['calmar'])
    cal_r = results[best_calmar]
    print(f'  最高卡玛:  {best_calmar} → Calmar={cal_r["calmar"]:.3f} '
          f'MDD={cal_r["mdd"]*100:.1f}%')

    best_mdd = min(results, key=lambda x: results[x]['mdd'])
    mdd_r = results[best_mdd]
    print(f'  最低回撤:  {best_mdd} → MDD={mdd_r["mdd"]*100:.1f}%')

    print(f'\n  推荐: 用 {best_calmar} — 因为它的风险调整收益最高。')
    print(f'  如果更看重绝对收益 -> {best_sharpe}')
    print(f'  如果最怕回撤 -> {best_mdd}')

    print(f'\n{"="*95}\n  Done!\n{"="*95}')

if __name__ == '__main__':
    main()
