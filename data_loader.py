"""
Shared data loading for all backtest strategies.
Loads prices (JSON), fundamentals (CSV from fundamentals_70stocks/), and computes MA series.
"""
import json, os, csv, math
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FUND_DIR = os.path.join(DATA_DIR, "fundamentals_70stocks")


def calc_ma(data, window):
    """Simple moving average. Returns list same length as data, NaN for first window-1 elements."""
    ma = []
    for i in range(len(data)):
        if i < window - 1:
            ma.append(float('nan'))
        else:
            ma.append(sum(data[i - window + 1:i + 1]) / window)
    return ma


def load_prices(stock_filter=None):
    """
    Load all JSON price files from DATA_DIR.
    Args:
        stock_filter: 'old' for 44 pre-2020 stocks (first date <= 2020-01-03, >= 1500 bars),
                      'all64' for 64 stocks (incl STAR market, >= 100 bars),
                      None for all 70 stocks.
    Returns: dict code -> {'name': str, 'sector': str, 'dates': [str], 'close': [float],
                           'open': [float], 'high': [float], 'low': [float], 'volume': [float]}
    """
    stocks = {}
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'):
            continue
        with open(os.path.join(DATA_DIR, fname), 'r', encoding='utf-8') as f:
            d = json.load(f)

        code = d['code']
        dates = [b['date'] for b in d['bars']]
        closes = [b['close'] for b in d['bars']]
        opens = [b['open'] for b in d['bars']]
        highs = [b['high'] for b in d['bars']]
        lows = [b['low'] for b in d['bars']]
        volumes = [b['volume'] for b in d['bars']]

        if stock_filter == 'old':
            if dates[0] > '2020-01-03' or len(d['bars']) < 1500:
                continue
        elif stock_filter == 'all64':
            if len(d['bars']) < 100:
                continue

        stocks[code] = {
            'name': d['name'], 'sector': d.get('sector', ''),
            'dates': dates, 'close': closes, 'open': opens,
            'high': highs, 'low': lows, 'volume': volumes,
        }
    return stocks


def get_common_dates(stocks):
    """Find intersection of all trading dates across stocks. Returns sorted list of date strings."""
    sets = [set(s['dates']) for s in stocks.values()]
    return sorted(sets[0].intersection(*sets[1:]))


def load_fundamentals():
    """
    Load quarterly fundamental data from FUND_DIR.
    Returns: dict code -> list of dicts [{pub_date, report_date, roe, net_margin, rev_yoy}, ...]
             sorted by pub_date per stock.
    Handles float conversion errors gracefully (skips bad rows).
    """
    fd = defaultdict(list)
    for fname in sorted(os.listdir(FUND_DIR)):
        if not fname.endswith('.csv'):
            continue
        with open(os.path.join(FUND_DIR, fname), 'r', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                code = row['code'].strip()
                try:
                    fd[code].append({
                        'pub_date': row['pub_date'].strip(),
                        'report_date': row['report_date'].strip(),
                        'roe': float(row['roe']) if row.get('roe') and row['roe'].strip() else float('nan'),
                        'net_margin': float(row['net_margin']) if row.get('net_margin') and row['net_margin'].strip() else float('nan'),
                        'rev_yoy': float(row['rev_yoy']) if row.get('rev_yoy') and row['rev_yoy'].strip() else float('nan'),
                    })
                except (ValueError, KeyError, TypeError):
                    pass
    for c in fd:
        fd[c].sort(key=lambda x: x['pub_date'])
    return fd


def get_latest_fundamentals(fd, date_str):
    """
    Get latest published fundamental data for each stock as of date_str.
    Returns: dict code -> {pub_date, report_date, roe, net_margin, rev_yoy}
    Only includes reports with pub_date <= date_str.
    """
    latest = {}
    for code, reports in fd.items():
        valid = [r for r in reports if r['pub_date'] <= date_str]
        if valid:
            latest[code] = valid[-1]
    return latest


def zscore_fundamentals(latest_dict):
    """
    Z-score standardize 3 fundamental metrics across stocks in latest_dict.
    Computes combined score with weights: NetMargin=0.50, ROE=0.37, RevYoY=0.13.
    Returns: dict code -> {roe_z, net_margin_z, rev_yoy_z, score}
    Returns empty dict if fewer than 3 valid stocks.
    """
    if len(latest_dict) < 3:
        return {}

    codes = list(latest_dict.keys())
    metrics = {'roe': [], 'net_margin': [], 'rev_yoy': []}
    valid_codes = []

    for c in codes:
        fund = latest_dict[c]
        try:
            roe_v = fund['roe']
            nm_v = fund['net_margin']
            ry_v = fund['rev_yoy']
        except (KeyError, TypeError):
            continue
        # Skip stocks with NaN fundamentals to avoid poisoning cross-sectional stats
        if math.isnan(roe_v) or math.isnan(nm_v) or math.isnan(ry_v):
            continue
        metrics['roe'].append(roe_v)
        metrics['net_margin'].append(nm_v)
        metrics['rev_yoy'].append(ry_v)
        valid_codes.append(c)

    if len(valid_codes) < 3:
        return {}

    stats = {}
    for k, vals in metrics.items():
        mu = sum(vals) / len(vals)
        var = sum((v - mu) ** 2 for v in vals) / len(vals)
        stats[k] = (mu, var ** 0.5 if var > 0 else 1.0)

    scores = {}
    for i, c in enumerate(valid_codes):
        zr = (metrics['roe'][i] - stats['roe'][0]) / stats['roe'][1]
        zn = (metrics['net_margin'][i] - stats['net_margin'][0]) / stats['net_margin'][1]
        zy = (metrics['rev_yoy'][i] - stats['rev_yoy'][0]) / stats['rev_yoy'][1]
        scores[c] = {
            'roe_z': zr, 'net_margin_z': zn, 'rev_yoy_z': zy,
            'score': zn * 0.50 + zr * 0.37 + zy * 0.13,
        }
    return scores


def compute_momentum_signals(stocks, common_dates):
    """
    Pre-compute 3-timeframe momentum for all stocks.
    M_short (21d):  close[t-1] / close[t-22] - 1
    M_mid (63d):    close[t-1] / close[t-64] - 1
    M_long (126d):  close[t-22] / close[t-127] - 1  (skip most recent month)

    Returns: dict code -> dict date -> {'m_short': float, 'm_mid': float, 'm_long': float}
    Only dates from index 126 onward have valid signals.
    """
    signals = {}
    for code, info in stocks.items():
        c = info['close']
        sig = {}
        for i in range(126, len(c)):
            date = info['dates'][i]
            m_short = (c[i-1] / c[i-22] - 1) if c[i-22] and c[i-22] != 0 else float('nan')
            m_mid = (c[i-1] / c[i-64] - 1) if c[i-64] and c[i-64] != 0 else float('nan')
            m_long = (c[i-22] / c[i-127] - 1) if c[i-127] and c[i-127] != 0 else float('nan')
            sig[date] = {'m_short': m_short, 'm_mid': m_mid, 'm_long': m_long}
        signals[code] = sig
    return signals


def compute_trend_signals(stocks, common_dates):
    """
    Pre-compute MA crossover trend scores and volatility.
    trend_raw = (MA5 > MA20 ? 1 : 0) + (MA10 > MA50 ? 1 : 0) + (MA20 > MA100 ? 1 : 0)  (0-3)
    vol = std(close, 20d) / close  (daily volatility)

    Returns: dict code -> dict date -> {'trend_raw': int, 'vol': float}
    Only dates from index 100 onward have valid signals (MA100 needs 100 bars).
    """
    signals = {}
    for code, info in stocks.items():
        c = info['close']
        ma5 = calc_ma(c, 5)
        ma10 = calc_ma(c, 10)
        ma20 = calc_ma(c, 20)
        ma50 = calc_ma(c, 50)
        ma100 = calc_ma(c, 100)

        sig = {}
        for i in range(100, len(c)):
            date = info['dates'][i]
            mas = [ma5[i], ma10[i], ma20[i], ma50[i], ma100[i]]
            if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in mas):
                continue

            t1 = 1 if ma5[i] > ma20[i] else 0
            t2 = 1 if ma10[i] > ma50[i] else 0
            t3 = 1 if ma20[i] > ma100[i] else 0
            trend_raw = t1 + t2 + t3

            window = c[i-19:i+1]
            mu = sum(window) / 20
            var = sum((v - mu) ** 2 for v in window) / 20
            vol = (var ** 0.5) / c[i] if c[i] > 0 else 0

            sig[date] = {'trend_raw': trend_raw, 'vol': vol}
        signals[code] = sig
    return signals
