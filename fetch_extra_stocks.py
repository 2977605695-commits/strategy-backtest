"""Fetch 2 additional stocks"""
import json, urllib.request, time, os, re
from datetime import datetime, timedelta

OUT_DIR = r"C:\Users\home\Desktop\strategy-backtest\data"
START = '2020-01-01'
END = '2026-07-01'

EXTRA = {
    '688503': {'name': '聚和材料', 'sector': '半导体高分子材料'},
    '688727': {'name': '恒坤新材', 'sector': '半导体工艺材料'},
}

def fetch_raw(tc_code, start, end):
    url = f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc_code},day,{start},{end},640,qfq'
    h = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'}
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode('utf-8'))
        except:
            if attempt < 2: time.sleep(1)
    return None

def parse(raw):
    try:
        days = None
        if 'data' in raw:
            for k in raw['data']:
                if isinstance(raw['data'][k], dict):
                    for f in ['qfqday', 'day']:
                        if f in raw['data'][k]:
                            days = raw['data'][k][f]; break
                    if days: break
        if not days: return []
        return [{'date': str(d[0]), 'open': float(d[1]), 'close': float(d[2]),
                 'high': float(d[3]), 'low': float(d[4]), 'volume': float(d[5])}
                for d in days if len(d) >= 6]
    except: return []

def fetch_all(tc_code, start, end):
    start_dt = datetime.strptime(start, '%Y-%m-%d')
    end_dt = datetime.strptime(end, '%Y-%m-%d')
    all_bars = []
    chunk_start = start_dt
    while chunk_start < end_dt:
        chunk_end = min(chunk_start + timedelta(days=500), end_dt)
        raw = fetch_raw(tc_code, chunk_start.strftime('%Y-%m-%d'), chunk_end.strftime('%Y-%m-%d'))
        bars = parse(raw)
        if bars:
            for bar in bars:
                if not all_bars or bar['date'] != all_bars[-1]['date']:
                    all_bars.append(bar)
        chunk_start = chunk_end + timedelta(days=1)
        time.sleep(0.05)
    return all_bars

def main():
    for code, info in EXTRA.items():
        tc = f'sh{code}'
        name = info['name']
        sector = info['sector']
        print(f"  [{sector}] {name} ({tc}) ...", end=' ', flush=True)

        bars = fetch_all(tc, START, END)
        if not bars:
            print("FAIL")
            continue

        out_file = os.path.join(OUT_DIR, f'{code}_{name}.json')
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump({
                'code': code, 'name': name, 'sector': sector,
                'start': bars[0]['date'], 'end': bars[-1]['date'],
                'count': len(bars), 'bars': bars
            }, f, ensure_ascii=False)

        print(f"OK {len(bars)} bars ({bars[0]['date']} ~ {bars[-1]['date']})")

    # Update summary
    summary_path = os.path.join(OUT_DIR, '_summary.json')
    with open(summary_path, 'r', encoding='utf-8') as f:
        summary = json.load(f)

    for code, info in EXTRA.items():
        # check if already in summary
        already = any(s['code'] == code for s in summary['stocks'])
        if not already:
            fpath = os.path.join(OUT_DIR, f'{code}_{info["name"]}.json')
            with open(fpath, 'r', encoding='utf-8') as f:
                d = json.load(f)
            summary['stocks'].append({
                'code': code, 'name': info['name'], 'sector': info['sector'],
                'days': d['count'], 'start': d['start'], 'end': d['end']
            })
            summary['total_stocks'] = len(summary['stocks'])
            summary['success'] = len(summary['stocks'])

    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n  Summary updated: {summary['total_stocks']} stocks total")

if __name__ == '__main__':
    main()
