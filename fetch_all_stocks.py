"""
Fetch all stocks from 表格_20260722.csv from Tencent Finance
Period: 2020-01-01 → 2026-07-01
Handles sector header rows and paginates API calls (max 640 bars each).
"""
import csv, json, urllib.request, time, os, re
from collections import OrderedDict, Counter

CSV_PATH = r"C:\Users\home\Desktop\表格_20260722.csv"
OUT_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
START = '2020-01-01'
END = '2026-07-01'

def code_to_tencent(code):
    return f'sh{code}' if code.startswith('6') else f'sz{code}'

def is_valid_code(s):
    """Check if string is a valid 6-digit stock code"""
    return bool(re.match(r'^\d{6}$', s))

def fetch_raw(tc_code, start, end):
    """Fetch one chunk from Tencent Finance"""
    url = f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc_code},day,{start},{end},640,qfq'
    h = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'}
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
    return None

def parse(raw):
    """Parse raw API response into bar list"""
    try:
        days = None
        if 'data' in raw:
            for k in raw['data']:
                if isinstance(raw['data'][k], dict):
                    for f in ['qfqday', 'day']:
                        if f in raw['data'][k]:
                            days = raw['data'][k][f]
                            break
                    if days:
                        break
        if not days:
            return []
        result = []
        for d in days:
            if len(d) >= 6:
                result.append({
                    'date': str(d[0]),
                    'open': float(d[1]),
                    'close': float(d[2]),
                    'high': float(d[3]),
                    'low': float(d[4]),
                    'volume': float(d[5])
                })
        return result
    except:
        return []

def fetch_all_chunks(tc_code, start, end):
    """Fetch all data by chunking into 500-day windows and merging"""
    from datetime import datetime, timedelta

    start_dt = datetime.strptime(start, '%Y-%m-%d')
    end_dt = datetime.strptime(end, '%Y-%m-%d')

    all_bars = []
    chunk_start_dt = start_dt

    while chunk_start_dt < end_dt:
        chunk_end_dt = min(chunk_start_dt + timedelta(days=500), end_dt)
        chunk_start = chunk_start_dt.strftime('%Y-%m-%d')
        chunk_end = chunk_end_dt.strftime('%Y-%m-%d')

        raw = fetch_raw(tc_code, chunk_start, chunk_end)
        bars = parse(raw)

        if bars:
            # Deduplicate by date
            for bar in bars:
                if not all_bars or bar['date'] != all_bars[-1]['date']:
                    all_bars.append(bar)

        chunk_start_dt = chunk_end_dt + timedelta(days=1)
        time.sleep(0.05)

    return all_bars

def main():
    # Read CSV - handle sector headers
    stocks = OrderedDict()
    current_sector = ""

    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader)  # skip: 股票代码,股票名称,细分赛道
        for row in reader:
            if len(row) < 1:
                continue
            col0 = row[0].strip()
            col1 = row[1].strip() if len(row) > 1 else ''
            col2 = row[2].strip() if len(row) > 2 else ''

            # Sector header: col0 is text (not numeric code), col1 and col2 are empty
            if not is_valid_code(col0):
                # This is a sector header row
                if col0 and col0 != '——':
                    current_sector = col0
                continue

            # Valid stock row
            name = col1
            if col2:
                current_sector = col2  # use sub-sector from col2
            stocks[col0] = {'name': name, 'sector': current_sector}

    print(f"Total valid stocks: {len(stocks)}")
    print(f"Period: {START} → {END}")
    print(f"Fetching in 500-day chunks to cover full date range\n")

    os.makedirs(OUT_DIR, exist_ok=True)

    results = []
    success = fail = 0

    for code, info in stocks.items():
        tc = code_to_tencent(code)
        name = info['name']
        sector = info['sector']
        print(f"  [{sector}] {name} ({tc}) ...", end=' ', flush=True)

        bars = fetch_all_chunks(tc, START, END)

        if not bars:
            print("FAIL (no data)")
            fail += 1
            continue

        # Save individual file
        out_file = os.path.join(OUT_DIR, f'{code}_{name}.json')
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump({
                'code': code, 'name': name, 'sector': sector,
                'start': bars[0]['date'], 'end': bars[-1]['date'],
                'count': len(bars), 'bars': bars
            }, f, ensure_ascii=False)

        results.append({
            'code': code, 'name': name, 'sector': sector,
            'days': len(bars), 'start': bars[0]['date'], 'end': bars[-1]['date']
        })
        print(f"OK {len(bars)} bars ({bars[0]['date']} ~ {bars[-1]['date']})")
        success += 1

    # Save summary
    summary = {
        'period': f'{START} → {END}',
        'total_stocks': len(stocks),
        'success': success, 'fail': fail,
        'stocks': results
    }
    with open(os.path.join(OUT_DIR, '_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"  Done: {success} OK, {fail} FAIL, {len(stocks)} total")
    print(f"  Data saved to: {OUT_DIR}")

    print(f"\n  --- Sector Summary ---")
    sector_counts = Counter(r['sector'] for r in results)
    for s, c in sector_counts.items():
        print(f"  {s}: {c} stocks")

if __name__ == '__main__':
    main()
