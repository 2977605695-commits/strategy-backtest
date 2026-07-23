"""Fetch 50 OOS stocks for overfitting test"""
import os, json, time, urllib.request, random

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_oos")
os.makedirs(OOS_DIR, exist_ok=True)

existing = set()
for f in os.listdir(DATA_DIR):
    if f.endswith('.json') and not f.startswith('_'):
        with open(os.path.join(DATA_DIR, f), encoding='utf-8') as fh:
            existing.add(json.load(fh)['code'])

CANDIDATES = {
    '000001':'平安银行','000002':'万科A','000063':'中兴通讯','000100':'TCL科技',
    '000157':'中联重科','000333':'美的集团','000338':'潍柴动力','000408':'藏格矿业',
    '000425':'徐工机械','000538':'云南白药','000568':'泸州老窖','000596':'古井贡酒',
    '000625':'长安汽车','000651':'格力电器','000661':'长春高新','000708':'中信特钢',
    '000725':'京东方A','000768':'中航西飞','000776':'广发证券','000786':'北新建材',
    '000792':'盐湖股份','000800':'一汽解放','000830':'鲁西化工','000858':'五粮液',
    '000876':'新希望','000895':'双汇发展','000963':'华东医药','000988':'华工科技',
    '000999':'华润三九','002007':'华兰生物','002008':'大族激光','002027':'分众传媒',
    '002049':'紫光国微','002074':'国轩高科','002129':'TCL中环','002142':'宁波银行',
    '002179':'中航光电','002230':'科大讯飞','002241':'歌尔股份','002271':'东方雨虹',
    '002304':'洋河股份','002311':'海大集团','002352':'顺丰控股','002410':'广联达',
    '002415':'海康威视','002475':'立讯精密','002594':'比亚迪','002601':'龙佰集团',
    '002603':'以岭药业','002648':'卫星化学','002709':'天赐材料','002714':'牧原股份',
    '002736':'国信证券','002812':'恩捷股份','002916':'深南电路','002920':'德赛西威',
    '300003':'乐普医疗','300014':'亿纬锂能','300015':'爱尔眼科','300033':'同花顺',
    '300059':'东方财富','300122':'智飞生物','300124':'汇川技术','300142':'沃森生物',
    '300274':'阳光电源','300285':'国瓷材料','300316':'晶盛机电','300347':'泰格医药',
    '300408':'三环集团','300413':'芒果超媒','300433':'蓝思科技','300450':'先导智能',
    '300498':'温氏股份','300529':'健帆生物','300601':'康泰生物','300628':'亿联网络',
    '300677':'英科医疗','300750':'宁德时代','300760':'迈瑞医疗','300896':'爱美客',
    '600000':'浦发银行','600009':'上海机场','600016':'民生银行','600019':'宝钢股份',
    '600028':'中国石化','600031':'三一重工','600036':'招商银行','600048':'保利发展',
    '600050':'中国联通','600085':'同仁堂','600104':'上汽集团','600132':'重庆啤酒',
    '600150':'中国船舶','600161':'天坛生物','600176':'中国巨石','600196':'复星医药',
    '600309':'万华化学','600346':'恒力石化','600406':'国电南瑞','600436':'片仔癀',
    '600438':'通威股份','600519':'贵州茅台','600547':'山东黄金','600570':'恒生电子',
    '600585':'海螺水泥','600588':'用友网络','600600':'青岛啤酒','600690':'海尔智家',
    '600703':'三安光电','600745':'闻泰科技','600809':'山西汾酒','600837':'海通证券',
    '600875':'东方电气','600886':'国投电力','600887':'伊利股份','600893':'航发动力',
    '600919':'江苏银行','600941':'中国移动','600989':'宝丰能源',
    '601006':'大秦铁路','601012':'隆基绿能','601100':'恒立液压','601117':'中国化学',
    '601166':'兴业银行','601211':'国泰君安','601225':'陕西煤业','601318':'中国平安',
    '601328':'交通银行','601336':'新华保险','601360':'三六零','601390':'中国中铁',
    '601601':'中国太保','601607':'上海医药','601615':'明阳智能','601628':'中国人寿',
    '601633':'长城汽车','601668':'中国建筑','601669':'中国电建','601688':'华泰证券',
    '601699':'潞安环能','601728':'中国电信','601766':'中国中车','601788':'光大证券',
    '601800':'中国交建','601808':'中海油服','601857':'中国石油','601888':'中国中免',
    '601919':'中远海控','601939':'建设银行','601985':'中国核电',
    '603160':'汇顶科技','603288':'海天味业','603369':'今世缘','603501':'韦尔股份',
    '603605':'珀莱雅','603799':'华友钴业','603806':'福斯特','603833':'欧派家居',
    '603899':'晨光股份','603993':'洛阳钼业','605117':'德业股份',
    '688005':'容百科技','688036':'传音控股','688065':'凯赛生物','688111':'金山办公',
    '688122':'西部超导','688139':'海尔生物','688166':'博瑞医药','688169':'石头科技',
    '688180':'君实生物','688188':'柏楚电子','688200':'华峰测控','688202':'美迪西',
    '688223':'晶科能源','688271':'联影医疗','688295':'中复神鹰','688303':'大全能源',
    '688363':'华熙生物','688390':'固德威','688599':'天合光能',
}

new_stocks = {c: n for c, n in CANDIDATES.items() if c not in existing}
print(f'{len(new_stocks)} candidates not in existing pool')

random.seed(42)
selected = random.sample(list(new_stocks.items()), 50)
selected.sort(key=lambda x: x[0])

success = 0
for code, name in selected:
    out_path = os.path.join(OOS_DIR, f'{code}.json')
    if os.path.exists(out_path):
        with open(out_path, encoding='utf-8') as f:
            d = json.load(f)
        print(f'  {code} {name} -> SKIP ({len(d.get(\"bars\",[]))} bars)')
        success += 1; continue

    market = 'sh' if code.startswith('6') else 'sz'
    sc = f'{market}{code}'
    url = f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sc},day,2020-01-01,2026-07-23,640,qfq'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'})

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            bars = None
            for k in data.get('data', {}):
                if isinstance(data['data'][k], dict):
                    for f in ['qfqday', 'day']:
                        if f in data['data'][k]: bars = data['data'][k][f]; break
                    if bars: break
            if bars and len(bars) > 100:
                records = []
                for d in bars:
                    if len(d) >= 6:
                        dt = str(d[0]).replace('-','')
                        records.append({
                            'date': dt, 'open': float(d[1]), 'close': float(d[2]),
                            'high': float(d[3]), 'low': float(d[4]), 'volume': float(d[5])})
                with open(out_path, 'w', encoding='utf-8') as f:
                    json.dump({'code': code, 'name': name,
                               'first_date': records[0]['date'], 'last_date': records[-1]['date'],
                               'n_days': len(records), 'bars': records}, f, ensure_ascii=False)
                print(f'  {code} {name} -> {len(records)} bars [{records[0][\"date\"]}~{records[-1][\"date\"]}]')
                success += 1
            else:
                if attempt == 2: print(f'  {code} {name} -> FAIL (only {len(bars) if bars else 0} bars)')
            break
        except Exception as e:
            if attempt == 2: print(f'  {code} {name} -> FAIL: {e}')
            time.sleep(2)
    time.sleep(0.5)

print(f'\nDone: {success}/{len(selected)} fetched')
