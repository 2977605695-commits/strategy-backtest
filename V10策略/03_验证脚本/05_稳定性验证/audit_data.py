"""审计89池每只ETF的数据质量, 找出有问题的标的.
问题类型: 1.内部0值空洞(上市后中间断层) 2.日期不齐 3.数据过短."""
import json, os

ETF_33=['159782','588380','588870','588080','588300','518800','589720','588890','588170','588200','159995','512480','515880','515050','159819','159992','512010','518880','159937','513180','513050','513100','159509','588000','588220','510300','159915','510050','511010','511260','510880','512890','159301']
NEW_56=sorted([f.replace('etf_','').replace('.json','') for f in os.listdir('data_new') if f.startswith('etf_')])
ALL_CODES=sorted(set(ETF_33+NEW_56))

# 基准日期 (沪深300)
base=json.load(open('data/etf_510300.json',encoding='utf-8'))
base_dates=[b['date'] for b in base['bars']]
base_set=set(base_dates)
print('基准交易日: %d 天 (%s ~ %s)\n' % (len(base_dates), base_dates[0], base_dates[-1]))

print('='*100)
print('  89池数据质量审计')
print('='*100)
print('  %-8s %-16s %6s %6s %8s %8s %s' % ('代码','名称','天数','真实','前置0','内部空洞','来源'))
print('  '+'-'*95)

clean=[]; problematic=[]
for code in ALL_CODES:
    src='data_new' if os.path.exists('data_new/etf_'+code+'.json') else 'data'
    p=os.path.join(src,'etf_'+code+'.json')
    if not os.path.exists(p):
        print('  %-8s %-16s %6s %6s %8s %8s 缺失!' % (code,'?','-','-','-','-')); continue
    d=json.load(open(p,encoding='utf-8'))
    bars=d['bars']
    name=d.get('name','?')[:14]
    # 检查日期是否与基准对齐
    bar_dates=[b['date'] for b in bars]
    n_total=len(bars)
    # 真实数据(非0)
    real=[b for b in bars if float(b['close'])>0]
    n_real=len(real)
    # 前置0(未上市)
    first_real_idx=next((i for i,b in enumerate(bars) if float(b['close'])>0), n_total)
    n_prefix0=first_real_idx
    # 内部空洞: 上市后出现close=0
    internal_holes=0
    seen_real=False
    for b in bars[first_real_idx:]:
        if float(b['close'])>0: seen_real=True
        elif seen_real: internal_holes+=1
    # 日期对齐检查
    date_misalign=0
    if n_total==len(base_dates):
        for i,bd in enumerate(bar_dates):
            if bd!=base_dates[i]: date_misalign+=1
    else:
        date_misalign=abs(n_total-len(base_dates))

    issues=[]
    if internal_holes>0: issues.append('内部空洞%d'%internal_holes)
    if date_misalign>5: issues.append('日期偏差%d'%date_misalign)
    if n_real<200: issues.append('数据过短')

    status='OK' if not issues else 'X '+';'.join(issues)
    if issues: problematic.append((code,name,issues))
    else: clean.append(code)
    print('  %-8s %-16s %6d %6d %8d %8d %s [%s]' % (
        code,name,n_total,n_real,n_prefix0,internal_holes,status,src))

print('\n'+'='*60)
print('  汇总: 干净%d只 | 有问题%d只' % (len(clean), len(problematic)))
print('='*60)
if problematic:
    print('\n  需剔除/修复的问题标的:')
    for code,name,issues in problematic:
        print('    %-8s %-14s %s' % (code,name,';'.join(issues)))
print('\n  干净标的清单(%d只):' % len(clean))
print('  '+' '.join(clean))
