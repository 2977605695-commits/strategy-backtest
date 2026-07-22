"""
Three-strategy comparison: correlation matrix and equal-weight portfolio.
Loads strategy equity curves from CSV files.
"""
import csv, math, os

def load_equity(path):
    dates, vals = [], []
    with open(path, 'r') as f:
        for row in csv.DictReader(f):
            dates.append(row['date'])
            vals.append(float(row['equity']))
    return dates, vals

def daily_returns(equity_vals):
    return [(equity_vals[i] - equity_vals[i-1]) / equity_vals[i-1]
            for i in range(1, len(equity_vals))]

def align_to_common(dates1, vals1, dates2, vals2):
    """Align two equity series to common dates. Returns paired daily returns."""
    d1 = dict(zip(dates1, vals1))
    d2 = dict(zip(dates2, vals2))
    common = sorted(set(d1.keys()) & set(d2.keys()))
    rets1, rets2 = [], []
    for i in range(1, len(common)):
        prev_d, cur_d = common[i-1], common[i]
        if prev_d in d1 and cur_d in d1 and d1[prev_d] > 0:
            rets1.append(d1[cur_d] / d1[prev_d] - 1)
        else:
            rets1.append(0.0)
        if prev_d in d2 and cur_d in d2 and d2[prev_d] > 0:
            rets2.append(d2[cur_d] / d2[prev_d] - 1)
        else:
            rets2.append(0.0)
    return rets1, rets2

def pearson_corr(x, y):
    n = len(x)
    if n < 2: return 0
    mx = sum(x)/n; my = sum(y)/n
    sx = (sum((v-mx)**2 for v in x)/n)**0.5
    sy = (sum((v-my)**2 for v in y)/n)**0.5
    if sx == 0 or sy == 0: return 0
    return sum((x[i]-mx)*(y[i]-my) for i in range(n)) / (n*sx*sy)

def compute_stats(vals):
    """Compute key stats from equity values."""
    INIT = vals[0]
    final = vals[-1]
    total_ret = (final - INIT) / INIT * 100
    rets = daily_returns(vals)
    if not rets: return {}
    mu = sum(rets)/len(rets)
    sd = (sum((r-mu)**2 for r in rets)/len(rets))**0.5
    years = len(rets)/252
    ann_ret = mu * 252 * 100
    cagr = ((final/INIT)**(1/years)-1)*100 if years > 0 else 0
    sharpe = (mu - 0.025/252) / sd * (252**0.5) if sd > 0 else 0
    peak = vals[0]; mdd = 0
    for v in vals:
        if v > peak: peak = v
        dd = (peak - v) / peak
        if dd > mdd: mdd = dd
    calmar = cagr / (mdd*100) if mdd > 0 else 0
    return {'total_ret': total_ret, 'ann_ret': ann_ret, 'cagr': cagr,
            'sharpe': sharpe, 'mdd': mdd*100, 'calmar': calmar,
            'years': years}

def main():
    BASE = r"C:\Users\home\Desktop\strategy-backtest"

    strategies = {}
    for name, fname in [
        ('S1_DualMomentum', 'strategy1_equity.csv'),
        ('S2_RiskTrend', 'strategy2_equity.csv'),
        ('S3_Fundamental', 'fundamental_equity.csv'),
    ]:
        path = os.path.join(BASE, fname)
        if os.path.exists(path):
            dates, vals = load_equity(path)
            strategies[name] = (dates, vals)
            print(f"Loaded {name}: {len(dates)} days, final={vals[-1]:,.0f}")
        else:
            print(f"MISSING: {fname} - skipping {name}")

    if len(strategies) < 2:
        print("\nNeed at least 2 equity curves for comparison. Exiting.")
        return

    # Stats table
    print(f"\n{'='*80}")
    print(f"  Individual Strategy Performance")
    print(f"{'='*80}")
    print(f"  {'Strategy':<22s} {'Sharpe':>7s} {'Return%':>9s} {'CAGR%':>7s} {'MDD%':>7s} {'Calmar':>7s}")
    print(f"  {'-'*60}")
    for name, (dates, vals) in strategies.items():
        s = compute_stats(vals)
        print(f"  {name:<22s} {s['sharpe']:>7.2f} {s['total_ret']:>9.1f} {s['cagr']:>7.1f} {s['mdd']:>7.1f} {s['calmar']:>7.2f}")

    # Correlation matrix
    names = list(strategies.keys())
    print(f"\n{'='*80}")
    print(f"  Daily Return Correlation Matrix")
    print(f"{'='*80}")
    header = f"  {'':<22s}"
    for n in names:
        header += f"{n:>18s}"
    print(header)
    for n1 in names:
        row = f"  {n1:<22s}"
        for n2 in names:
            r1, r2 = align_to_common(strategies[n1][0], strategies[n1][1],
                                      strategies[n2][0], strategies[n2][1])
            corr = pearson_corr(r1, r2)
            row += f"{corr:>18.4f}"
        print(row)

    # Equal-weight portfolio
    print(f"\n{'='*80}")
    print(f"  Equal-Weight Combined Portfolio")
    print(f"{'='*80}")

    # Normalize all to start at INIT_CAP = 10,000,000
    INIT = 10_000_000
    # Find common dates across all strategies
    all_date_sets = [set(s[0]) for s in strategies.values()]
    common_dates = sorted(set.intersection(*all_date_sets))

    # Build normalized equity for each strategy on common dates
    norm_eq = {name: [] for name in names}
    for name in names:
        dates, vals = strategies[name]
        date_val = dict(zip(dates, vals))
        # Scale to INIT
        scale = INIT / vals[0]
        norm_eq[name] = [date_val.get(d, 0) * scale for d in common_dates]

    # Equal weight portfolio
    portfolio = []
    for i in range(len(common_dates)):
        total = sum(norm_eq[name][i] for name in names) / len(names)
        portfolio.append(total)

    ps = compute_stats(portfolio)
    print(f"  Combined Sharpe:  {ps['sharpe']:.2f}")
    print(f"  Combined Return:  {ps['total_ret']:.1f}%")
    print(f"  Combined CAGR:    {ps['cagr']:.1f}%")
    print(f"  Combined MDD:     {ps['mdd']:.1f}%")
    print(f"  Combined Calmar:  {ps['calmar']:.2f}")

    # Save
    out_path = os.path.join(BASE, 'portfolio_combined.csv')
    with open(out_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['date', 'equity'])
        for i, d in enumerate(common_dates):
            w.writerow([d, f'{portfolio[i]:.2f}'])
    print(f"\n  Portfolio equity saved to portfolio_combined.csv")

if __name__ == '__main__':
    main()
