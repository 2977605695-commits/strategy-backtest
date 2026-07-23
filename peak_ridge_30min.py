"""
峰岭成交比因子 · 30分钟线精确版
================================================
Proper cal_flag: 按同一时刻(t-21..t)计算阈值 → 喷发判定 → 孤立/连续分类
Daily factor: SUM(21日峰量) / SUM(21日岭量)
Backtest: 因子截面排名 Top5 + 赛道去重 + Trail 20% + 21日重排
对比: 日线近似版 vs 30min精确版
"""
import sys, io, os, math, json
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

INIT = 10_000_000; RF = 0.025; TD = 252
SLIP = 0.003; B_FEE = 0.00025; S_FEE = 0.00025; STAX = 0.0005
TRAIL = 0.20; MAX_POS = 5; REBAL = 21
LOOKBACK = 21  # 21 trading days for peak/ridge calc
MIN30_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_30min")
FUND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fundamentals_70stocks")

def load_sector_map():
    import csv
    csvs = sorted([f for f in os.listdir(FUND_DIR) if f.endswith('.csv')])
    sm = {}
    with open(os.path.join(FUND_DIR, csvs[-1]), 'r', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f): sm[r['code'].strip()] = r.get('sector','').strip()
    return sm

# ================================================================
# 30-min Peak-Ridge Classification
# ================================================================
def load_30min_data():
    """Load all 30-min data into {code: {date: [{datetime, open, close, high, low, volume}]}}"""
    data = {}
    for fname in sorted(os.listdir(MIN30_DIR)):
        if not fname.endswith('.json'): continue
        with open(os.path.join(MIN30_DIR, fname), 'r', encoding='utf-8') as f:
            d = json.load(f)
        # Group bars by date
        by_date = defaultdict(list)
        for bar in d['bars']:
            dt = bar['datetime']
            date = dt[:10]  # YYYY-MM-DD
            # Extract time slot (HH:MM)
            time_slot = dt[11:16]
            bar['time_slot'] = time_slot
            by_date[date].append(bar)
        data[d['code']] = dict(by_date)
    return data

def calc_peak_ridge_30min(min_data):
    """
    Proper peak/ridge factor using 30-min bars.
    For each trading day, for each time slot:
      - Get volumes for same time slot over past LOOKBACK trading days
      - Threshold = mean + std
      - Current bar volume >= threshold → eruption (flag=1 preliminary)
    Then: consecutive eruptions → ridge(2), isolated eruption → peak(1), non → valley(0)
    Factor = sum(peak volumes over 21d) / sum(ridge volumes over 21d)
    """
    factor_daily = {}

    for code, date_bars in min_data.items():
        dates = sorted(date_bars.keys())

        # Pre-collect all time slot volumes in a 2D structure
        # all_vols[date_index][time_slot] = volume
        time_slots = ['09:30', '10:00', '10:30', '11:00',
                      '11:30', '13:00', '13:30', '14:00', '14:30', '15:00']
        # Map actual time slots to our standard list
        all_vols = []  # list of lists per date
        valid_dates = []
        for date in dates:
            bars = date_bars[date]
            # Build time_slot → volume map
            vol_map = {}
            for b in bars:
                ts = b['time_slot']
                vol_map[ts] = b['volume']
            # Only include dates with complete data (at least 6 bars)
            if len(vol_map) >= 6:
                all_vols.append(vol_map)
                valid_dates.append(date)

        if len(all_vols) < LOOKBACK + 1:
            continue

        # Get all unique time slots present
        all_slots = set()
        for vm in all_vols:
            all_slots.update(vm.keys())
        all_slots = sorted(all_slots)

        # For each day t (>= LOOKBACK), compute flags and factor
        result_vals = {}  # date → factor value
        for t in range(LOOKBACK, len(all_vols)):
            date_t = valid_dates[t]
            window_indices = range(t - LOOKBACK, t + 1)  # t-20 .. t

            # Per time slot: compute eruption flags for all days in window
            # slot_flags[date_rel_idx][time_slot] = bool (eruption?)
            slot_flags = []
            for rel_idx in window_indices:
                day_flags = {}
                vm = all_vols[rel_idx]
                for ts in all_slots:
                    cur_vol = vm.get(ts, 0)
                    # Get volumes for this time slot across the 21-day window
                    window_vols = []
                    for wi in window_indices:
                        v = all_vols[wi].get(ts, 0)
                        if v > 0:
                            window_vols.append(v)
                    if len(window_vols) >= 5:
                        mu = sum(window_vols) / len(window_vols)
                        var = sum((v - mu) ** 2 for v in window_vols) / len(window_vols)
                        std = var ** 0.5
                        threshold = mu + std
                        day_flags[ts] = (cur_vol >= threshold)
                    else:
                        day_flags[ts] = False
                slot_flags.append(day_flags)

            # Now: convert eruption flags to peak/ridge/valley
            # For each day in window, for each time slot:
            #   if eruption and (prev_slot eruption or next_slot eruption) → ridge(2)
            #   elif eruption → peak(1)
            #   else → valley(0)
            peak_vol_sum = 0.0
            ridge_vol_sum = 0.0

            for rel_idx in range(len(window_indices)):
                abs_idx = window_indices[rel_idx]
                vm = all_vols[abs_idx]
                flags = slot_flags[rel_idx]

                for i, ts in enumerate(all_slots):
                    if not flags.get(ts, False):
                        continue  # valley, not counted
                    vol = vm.get(ts, 0)

                    # Check if adjacent slots also erupted (continuous)
                    prev_erupt = (i > 0 and flags.get(all_slots[i-1], False))
                    next_erupt = (i < len(all_slots) - 1 and flags.get(all_slots[i+1], False))

                    if prev_erupt or next_erupt:
                        ridge_vol_sum += vol
                    else:
                        peak_vol_sum += vol

            if ridge_vol_sum > 0:
                result_vals[date_t] = peak_vol_sum / ridge_vol_sum
            else:
                result_vals[date_t] = float('nan')

        factor_daily[code] = result_vals

    return factor_daily

# ================================================================
# Backtest engine (same as before)
# ================================================================
def backtest(daily_data, factor, sm, stock_names, dates, trail, max_pos, rebal):
    """daily_data: {code: {date: close}}  factor: {code: {date: value}}"""
    cash = INIT; slot = INIT / max_pos; pos = {}; eq = []; trades = []

    for di, dt in enumerate(dates):
        # Trail exits
        for code, p in list(pos.items()):
            px = daily_data[code].get(dt)
            if px is None: continue
            if px > p['peak']: p['peak'] = px
            if px <= p['peak'] * (1 - trail):
                sp = px * (1 - SLIP - S_FEE - STAX)
                cash += p['shares'] * sp
                trades.append({
                    'code': code, 'name': p['name'], 'bd': p['bd'], 'sd': dt,
                    'ret': (sp - p['bp']) / p['bp'] if p['bp'] > 0 else 0,
                    'exit': 'trail', 'hold': di - p['bi']})
                del pos[code]

        # Rebalance
        if di % rebal == 0:
            cand = [(c, factor.get(c, {}).get(dt, float('nan')))
                    for c in daily_data if c in factor]
            cand = [(c, s) for c, s in cand if not math.isnan(s) and daily_data[c].get(dt)]
            cand.sort(key=lambda x: x[1], reverse=True)
            top_codes = set(c for c, _ in cand[:max_pos])

            # Sell non-top
            for code in list(pos.keys()):
                if code not in top_codes:
                    px = daily_data[code].get(dt)
                    if px:
                        sp = px * (1 - SLIP - S_FEE - STAX)
                        cash += pos[code]['shares'] * sp
                        trades.append({
                            'code': code, 'name': pos[code]['name'],
                            'bd': pos[code]['bd'], 'sd': dt,
                            'ret': (sp - pos[code]['bp']) / pos[code]['bp'] if pos[code]['bp'] > 0 else 0,
                            'exit': 'rebalance', 'hold': di - pos[code]['bi']})
                        del pos[code]

            # Buy new
            hc = set(pos.keys()); hs = {sm.get(c, '') for c in hc}
            for code, sc in cand:
                if len(pos) >= max_pos: break
                if code in hc: continue
                s = sm.get(code, '')
                if s and s in hs: continue
                if cash < slot * 0.99: break
                raw = daily_data[code][dt]
                bp = raw * (1 + SLIP + B_FEE); sh = slot / bp; cash -= slot
                pos[code] = {'shares': sh, 'bp': bp, 'peak': raw, 'bd': dt, 'bi': di,
                             'name': stock_names.get(code, code), 'score': sc}
                hc.add(code); hs.add(s)

        cash *= (1 + RF / TD)
        pv = sum(p['shares'] * daily_data[c].get(dt, 0) for c, p in pos.items())
        eq.append({'date': dt, 'equity': cash + pv, 'pos': len(pos)})

    # Final
    ld = dates[-1]
    for code, p in list(pos.items()):
        px = daily_data[code].get(ld)
        if px:
            sp = px * (1 - SLIP - S_FEE - STAX); cash += p['shares'] * sp
            trades.append({
                'code': code, 'name': p['name'], 'bd': p['bd'], 'sd': ld,
                'ret': (sp - p['bp']) / p['bp'] if p['bp'] > 0 else 0,
                'exit': 'final', 'hold': len(dates) - 1 - p['bi']})
    pos.clear()
    if eq: eq[-1]['equity'] = cash; eq[-1]['pos'] = 0

    v = [d['equity'] for d in eq]
    tr = (v[-1] - v[0]) / v[0]
    rs = [(v[i] - v[i-1]) / v[i-1] for i in range(1, len(v)) if v[i-1] > 0]
    y = len(rs) / TD; cagr = (v[-1] / v[0]) ** (1 / y) - 1 if y > 0 else 0
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

    return {
        'equity': eq, 'trades': trades, 'tr': tr, 'cagr': cagr, 'sh': sh,
        'mdd': mdd, 'calmar': cm, 'nt': len(trades),
        'wr': w / len(trades) if trades else 0,
        'hp': sum(1 for d in eq if d['pos'] > 0) / len(eq), 'exits': exits
    }

def annual(eq):
    yr = defaultdict(lambda: {'s': None, 'e': None})
    for d in eq:
        yk = d['date'][:4]
        if yr[yk]['s'] is None: yr[yk]['s'] = d['equity']
        yr[yk]['e'] = d['equity']
    return {y: (v['e'] - v['s']) / v['s'] * 100 for y, v in yr.items()
            if v['s'] and v['e'] and v['s'] > 0}

# ================================================================
# Daily approximation (for comparison)
# ================================================================
def calc_peak_ridge_daily(daily_data):
    """Daily approximation: same logic but using daily volume bars."""
    from data_loader import calc_ma
    factor = {}
    for code, info in daily_data.items():
        vols = info['volumes']; dates = info['dates']; n = len(vols)
        ma_vol = calc_ma(vols, 20)
        vals = {}
        for i in range(n):
            if i < 20 or math.isnan(ma_vol[i]): continue
            win = vols[i-19:i+1]; mu = sum(win) / 20
            var = sum((v-mu)**2 for v in win) / 20; std = var ** 0.5
            thr = ma_vol[i] + std
            peak_s = 0.0; ridge_s = 0.0
            for j in range(max(0, i-20), i+1):
                erupt = vols[j] >= thr
                if erupt:
                    prev_erupt = (j > 0 and vols[j-1] >= thr)
                    if prev_erupt: ridge_s += vols[j]
                    else: peak_s += vols[j]
            vals[dates[i]] = peak_s / ridge_s if ridge_s > 0 else float('nan')
        factor[code] = vals
    return factor

# ================================================================
# Main
# ================================================================
def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sm = load_sector_map()

    # Load daily data (for price info + daily factor comparison)
    from data_loader import load_prices, get_common_dates
    all_stocks = load_prices(stock_filter=None)
    stocks_daily = {c: i for c, i in all_stocks.items()
                    if i['dates'] and i['dates'][0] <= '20200103' and len(i['dates']) >= 1500}

    print('=' * 90)
    print('  峰岭成交比因子 · 30分钟线精确版 vs 日线近似版')
    print('=' * 90)

    # Load 30-min data
    print('\n[1] Loading 30-min data...')
    min30 = load_30min_data()
    print(f'  Loaded {len(min30)} stocks')

    # Get daily close prices for same stocks (convert dates to YYYY-MM-DD)
    daily_close = {}
    for code in min30:
        if code in stocks_daily:
            info = stocks_daily[code]
            # Convert YYYYMMDD → YYYY-MM-DD
            daily_close[code] = {}
            for d, c in zip(info['dates'], info['close']):
                d2 = d[:4] + '-' + d[4:6] + '-' + d[6:8]
                daily_close[code][d2] = c
    print(f'  {len(daily_close)} stocks have matching daily data')

    # Calc 30-min factor
    print('\n[2] Computing 30-min peak-ridge factor...')
    factor_30min = calc_peak_ridge_30min(min30)
    n_vals = sum(len(v) for v in factor_30min.values())
    print(f'  {n_vals} valid daily factor values across {len(factor_30min)} stocks')

    # Factor date range
    all_f_dates = set()
    for fv in factor_30min.values():
        all_f_dates.update(fv.keys())
    f_dates = sorted(all_f_dates)
    print(f'  Date range: {f_dates[0]} ~ {f_dates[-1]} ({len(f_dates)} days)')

    # Calc daily approximation factor (same date range for fair comparison)
    print('\n[3] Computing daily-approx factor (for comparison)...')
    daily_vol_data = {}
    for code in daily_close:
        if code in stocks_daily:
            info = stocks_daily[code]
            # Convert dates: YYYYMMDD → YYYY-MM-DD for consistency
            dates_dash = [d[:4] + '-' + d[4:6] + '-' + d[6:8] for d in info['dates']]
            daily_vol_data[code] = {'dates': dates_dash, 'volumes': info['volume']}
    factor_daily_approx = calc_peak_ridge_daily(daily_vol_data)

    # ============================================================
    # Find common date range where most stocks have data
    # ============================================================
    print('\n[4] Running backtests on common dates...')
    f_dates_all = []
    for code in factor_30min:
        f_dates_all.append(set(factor_30min[code].keys()))
    common_f = sorted(f_dates_all[0].intersection(*f_dates_all[1:]))
    print(f'  Common factor dates (all stocks): {len(common_f)}')

    # Use dates where at least 80% of stocks have both factor and close data
    common_dates = []
    for d in f_dates:
        cnt = sum(1 for c in factor_30min
                  if d in factor_30min[c] and d in daily_close.get(c, {}))
        if cnt >= len(factor_30min) * 0.8:
            common_dates.append(d)
    if not common_dates:
        common_dates = f_dates  # fallback: all factor dates
    print(f'  Common backtest dates: {len(common_dates)} ({common_dates[0]} ~ {common_dates[-1]})')
    print(f'  Period: {len(common_dates)/252:.1f} years')

    # ============================================================
    # Run: 30min factor vs daily-approx factor
    # ============================================================
    # Prepare daily-approx factor map for same codes
    factor_daily_map = {}
    for code in daily_close:
        if code in factor_daily_approx:
            factor_daily_map[code] = factor_daily_approx[code]

    # Build stock name map
    stock_names = {}
    for code in daily_close:
        if code in stocks_daily:
            stock_names[code] = stocks_daily[code].get('name', code)

    configs = [
        ('30min精确版', factor_30min),
        ('日线近似版', factor_daily_map),
    ]

    results = {}
    for label, fac in configs:
        bt = backtest(daily_close, fac, sm, stock_names, common_dates, TRAIL, MAX_POS, REBAL)
        results[label] = bt
        s = bt
        print(f'\n  [{label}]')
        print(f'    S={s["sh"]:.4f}  R={s["tr"]*100:.1f}%  DD={s["mdd"]*100:.1f}%  '
              f'CM={s["calmar"]:.3f}  Trd={s["nt"]}  Win={s["wr"]*100:.0f}%  '
              f'Hold={s["hp"]*100:.1f}%')
        print(f'    Exits: {s["exits"]}')

    # ============================================================
    # Then run 30min on FULL available range (just the factor itself)
    # ============================================================
    print(f'\n{"─"*90}')
    print(f'  30min精确版 · Full Range (no daily-approx alignment)')
    # Use all dates available, not just common intersection
    full_dates = sorted(f_dates)
    # Filter to dates where at least half of stocks have factor+close
    full_valid = []
    for d in full_dates:
        cnt = sum(1 for c in factor_30min if d in factor_30min[c] and d in daily_close.get(c, {}))
        if cnt >= max(10, len(factor_30min) // 2):
            full_valid.append(d)
    print(f'  Full range dates (≥50% stocks): {len(full_valid)} ({full_valid[0]} ~ {full_valid[-1]})')

    bt_full = backtest(daily_close, factor_30min, sm, stock_names, full_valid, TRAIL, MAX_POS, REBAL)
    s = bt_full
    print(f'  S={s["sh"]:.4f}  R={s["tr"]*100:.1f}%  DD={s["mdd"]*100:.1f}%  '
          f'CM={s["calmar"]:.3f}  Trd={s["nt"]}  Win={s["wr"]*100:.0f}%')
    print(f'  Exits: {s["exits"]}')

    # Annual
    yr = annual(bt_full['equity'])
    print(f'\n  年度收益:')
    for y, r in yr.items():
        print(f'    {y}: {r:+.1f}%')

    # ============================================================
    # Correlation: 30min factor vs daily-approx factor
    # ============================================================
    print(f'\n{"─"*90}')
    print(f'  因子相关性: 30min精确版 vs 日线近似版')
    paired = []
    for code in factor_30min:
        if code not in factor_daily_map: continue
        f30 = factor_30min[code]
        fd = factor_daily_map[code]
        for d in common_f:
            v30 = f30.get(d, float('nan'))
            vd = fd.get(d, float('nan'))
            if not math.isnan(v30) and not math.isnan(vd):
                paired.append((v30, vd))
    if paired:
        x = [p[0] for p in paired]; y_ = [p[1] for p in paired]
        mx = sum(x)/len(x); my = sum(y_)/len(y_)
        sx = (sum((v-mx)**2 for v in x)/len(x))**0.5
        sy = (sum((v-my)**2 for v in y_)/len(y_))**0.5
        corr = sum((x[i]-mx)*(y_[i]-my) for i in range(len(x)))/(len(x)*sx*sy) if sx*sy>0 else 0
        print(f'  {len(paired)} paired values, Pearson r = {corr:.4f}')
        if abs(corr) < 0.3:
            print(f'  ⚠️  Low correlation → 30min version captures different information!')

    # Top/bottom trades
    ts = sorted(bt_full['trades'], key=lambda x: x['ret'], reverse=True)
    print(f'\n  Top 5 trades:')
    for t in ts[:5]:
        print(f'    {t.get("name","?"):<12s} {t["bd"]}→{t["sd"]}  {t["ret"]*100:>+7.1f}%  {t["exit"]}  {t["hold"]}d')
    print(f'\n  Bottom 5:')
    for t in ts[-5:]:
        print(f'    {t.get("name","?"):<12s} {t["bd"]}→{t["sd"]}  {t["ret"]*100:>+7.1f}%  {t["exit"]}  {t["hold"]}d')

    print(f'\n{"="*90}\n  Done!\n{"="*90}')

if __name__ == '__main__':
    main()
