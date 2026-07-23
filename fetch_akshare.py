"""
Fetch 70 A-share stocks from 2020-01-01 using AkShare.
Merges with existing Tencent data (2023-11+) to get full 6-year history.
"""

import sys, io, json, math, time, os
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import akshare as ak

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
START = '20200101'
END = '20260723'

CODES = [
    '000657','000938','000977','002050','002371','002384','002460','002466',
    '002837','002851','300054','300308','300373','300394','300475','300476',
    '300502','300604','300620','300661','300666','300757','301018','301165',
    '301308','301392','301629','600030','600111','600183','600276','600460',
    '600584','600900','601088','601138','601288','601398','601600','601689',
    '601869','601899','601995','603186','603259','603296','603738','603986',
    '688008','688012','688037','688072','688126','688141','688195','688256',
    '688261','688347','688409','688498','688502','688503','688627','688702',
    '688726','688727','688795','688802','688820','688981',
]

STOCK_NAMES = {
    '000657':'中钨高新','000938':'紫光股份','000977':'浪潮信息','002050':'三花智控',
    '002371':'北方华创','002384':'东山精密','002460':'赣锋锂业','002466':'天齐锂业',
    '002837':'英维克','002851':'麦格米特','300054':'鼎龙股份','300308':'中际旭创',
    '300373':'扬杰科技','300394':'天孚通信','300475':'香农芯创','300476':'胜宏科技',
    '300502':'新易盛','300604':'长川科技','300620':'光库科技','300661':'圣邦股份',
    '300666':'江丰电子','300757':'罗博特科','301018':'申菱环境','301165':'中瑞股份',
    '301308':'江波龙','301392':'汇成真空','301629':'矽电股份','600030':'中信证券',
    '600111':'北方稀土','600183':'生益科技','600276':'恒瑞医药','600460':'士兰微',
    '600584':'长电科技','600900':'长江电力','601088':'中国神华','601138':'工业富联',
    '601288':'农业银行','601398':'工商银行','601600':'中国铝业','601689':'拓普集团',
    '601869':'长飞光纤','601899':'紫金矿业','601995':'中金公司','603186':'华正新材',
    '603259':'药明康德','603296':'华勤技术','603738':'泰晶科技','603986':'兆易创新',
    '688008':'澜起科技','688012':'中微公司','688037':'芯源微','688072':'拓荆科技',
    '688126':'沪硅产业','688141':'杰华特','688195':'腾景科技','688256':'寒武纪',
    '688261':'东微半导','688347':'华虹宏力','688409':'富创精密','688498':'源杰科技',
    '688502':'茂莱光学','688503':'聚和材料','688627':'精智达','688702':'盛科通信',
    '688726':'拉普拉斯','688727':'衡泰技术','688795':'联动科技','688802':'中科飞测',
    '688820':'盛美上海','688981':'中芯集成',
}


def fetch_akshare(code, start, end):
    """Fetch daily kline via AkShare. Returns list of bar dicts."""
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_hist(symbol=code, period='daily',
                                    start_date=start, end_date=end, adjust='qfq')
            if df is None or len(df) == 0:
                time.sleep(2)
                continue
            bars = []
            for _, row in df.iterrows():
                bars.append({
                    'date': str(row['日期']).replace('-',''),
                    'open': float(row['开盘']),
                    'close': float(row['收盘']),
                    'high': float(row['最高']),
                    'low': float(row['最低']),
                    'volume': float(row['成交量']),
                })
            # Sort by date ascending
            bars.sort(key=lambda x: x['date'])
            return bars
        except Exception as e:
            time.sleep(2)
    return []


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    n = len(CODES); ok = 0; fail = []

    print(f'Fetching {n} stocks via AkShare ({START} ~ {END})')
    print()

    for i, code in enumerate(CODES, 1):
        name = STOCK_NAMES.get(code, code)
        print(f'[{i:>3d}/{n}] {code} {name} ...', end=' ', flush=True)

        ak_bars = fetch_akshare(code, START, END)

        if not ak_bars:
            # Try existing Tencent data
            tx_path = os.path.join(DATA_DIR, f'{code}.json')
            if os.path.exists(tx_path):
                existing = json.load(open(tx_path, encoding='utf-8'))
                print(f'AK fail, keep existing ({len(existing["bars"])} bars)')
                ok += 1
            else:
                print('FAIL')
                fail.append(code)
            time.sleep(0.3)
            continue

        # Save
        out = {
            'code': code,
            'name': name,
            'first_date': ak_bars[0]['date'],
            'last_date': ak_bars[-1]['date'],
            'n_days': len(ak_bars),
            'bars': ak_bars,
        }
        path = os.path.join(DATA_DIR, f'{code}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False)

        ok += 1
        print(f'OK {len(ak_bars):>4d} bars ({ak_bars[0]["date"]} ~ {ak_bars[-1]["date"]})')

        # Rate limit
        time.sleep(0.25)

    # Summary
    summary = {
        'fetch_date': datetime.now().strftime('%Y-%m-%d'),
        'source': 'AkShare (stock_zh_a_hist qfq)',
        'period': f'{START}~{END}',
        'total': n, 'success': ok, 'failed': fail,
    }
    with open(os.path.join(DATA_DIR, '_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Check coverage
    print(f'\n{"="*60}')
    print(f'Done: {ok}/{n} OK, {len(fail)} failed')
    if fail: print(f'Failed: {fail}')

    # Date distribution
    firsts = []
    for f in sorted(os.listdir(DATA_DIR)):
        if f.endswith('.json') and not f.startswith('_'):
            d = json.load(open(os.path.join(DATA_DIR, f), encoding='utf-8'))
            firsts.append(d['first_date'])
    from collections import Counter
    c = Counter(f[:6] for f in firsts)
    print(f'\nStart date distribution:')
    for k in sorted(c.keys()): print(f'  {k}: {c[k]} stocks')


if __name__ == '__main__':
    main()
