"""
Fetch 30-min K-line data from Sina for all 44 old stocks.
"""
import os, json, time, urllib.request

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_30min")
os.makedirs(OUT_DIR, exist_ok=True)

def load_stock_list():
    codes = []
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'):
            continue
        with open(os.path.join(DATA_DIR, fname), 'r', encoding='utf-8') as f:
            d = json.load(f)
        if d.get('first_date', '') <= '20200103' and d.get('n_days', 0) >= 1500:
            codes.append((d['code'], d['name']))
    return codes

def fetch_30min(code):
    if code.startswith('6'):
        symbol = 'sh' + code
    else:
        symbol = 'sz' + code
    url = ('http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           'CN_MarketData.getKLineData?symbol=' + symbol + '&scale=30&ma=no&datalen=20000')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode('gb2312', errors='replace')
                data = json.loads(text)
                bars = []
                for row in data:
                    bars.append({
                        'datetime': row['day'],
                        'open': float(row['open']),
                        'close': float(row['close']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'volume': float(row['volume']),
                    })
                return bars
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)
    return []

def main():
    stocks = load_stock_list()
    print('Fetching 30-min data for', len(stocks), 'stocks...')
    success, fail = 0, 0
    for code, name in stocks:
        out_path = os.path.join(OUT_DIR, code + '.json')
        if os.path.exists(out_path):
            print(' ', code, name.ljust(12), '-> SKIP (exists)')
            success += 1
            continue
        try:
            bars = fetch_30min(code)
            if bars:
                with open(out_path, 'w', encoding='utf-8') as f:
                    json.dump({
                        'code': code, 'name': name,
                        'first_dt': bars[0]['datetime'],
                        'last_dt': bars[-1]['datetime'],
                        'n_bars': len(bars),
                        'bars': bars,
                    }, f, ensure_ascii=False)
                date_first = bars[0]['datetime'][:10]
                date_last = bars[-1]['datetime'][:10]
                print(' ', code, name.ljust(12), '->', len(bars), 'bars [', date_first, '~', date_last, ']')
                success += 1
            else:
                print(' ', code, name.ljust(12), '-> FAIL (empty)')
                fail += 1
        except Exception as e:
            print(' ', code, name.ljust(12), '-> FAIL (' + str(e)[:80] + ')')
            fail += 1
        time.sleep(2)
    print('Done!', success, 'success,', fail, 'failed')

if __name__ == '__main__':
    main()
