"""Show which indices trigger 7/10 slope>0 at key switching dates"""
import json,os,math
DATA_DIR='data';START='2020-01-01';END='2026-07-30'

INDEX_CODES={
    '510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF',
    '513100':'纳指ETF','511260':'十年国债','512890':'红利低波','159992':'创新药ETF',
}

def load():
    raw={}
    for code in INDEX_CODES:
        p=os.path.join(DATA_DIR,'etf_'+code+'.json')
        if not os.path.exists(p):continue
        d=json.load(open(p,encoding='utf-8'))
        bars=[]
        for b in d['bars']:
            dt=b['date']
            if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            if START<=dt<=END:bars.append({'date':dt,'close':float(b['close'])})
        raw[code]=bars
    return raw

def ma(data,w):
    m=[float('nan')]*(w-1)
    for i in range(w-1,len(data)):m.append(sum(data[i-w+1:i+1])/w)
    return m

def slope(ms,lb):
    s=[float('nan')]*len(ms)
    for i in range(len(ms)):
        if i<lb:continue
        ys=ms[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys):continue
        n=len(ys);sx=sy=sxy=sxx=0
        for j,y in enumerate(ys):sx+=j;sy+=y;sxy+=j*y;sxx+=j*j
        denom=n*sxx-sx*sx
        if denom>0:s[i]=(n*sxy-sx*sy)/denom/ms[i] if ms[i]>0 else 0
    return s

bars=load()

# Build per-index slope signals
sig={}
for code,name in INDEX_CODES.items():
    if code not in bars:continue
    cl=[b['close'] for b in bars[code]];dts=[b['date'] for b in bars[code]]
    m60=ma(cl,60);sl=slope(m60,20)
    # Create date->bool map
    dmap={}
    for i in range(len(dts)):
        if not math.isnan(sl[i]):
            dmap[dts[i]]=sl[i]>0
    sig[code]=name,dmap

# Monthly summary
print('='*100)
print('  MONTHLY SLOPE>0 COUNT (10 indices)')
print('='*100)
print('  %-8s %4s %s'%('Month','N','Which indices slope>0'))
print('  '+'-'*80)

# For each month, count unique indices with slope>0 on the LAST day of the month
# (snapshot approach: use last trading day of each month)
all_dates=sorted(set().union(*[set(dmap.keys()) for _,dmap in sig.values()]))
# Get last trading day per month
month_last={}
for d in all_dates:
    ym=d[:7];month_last[ym]=d

for ym in sorted(month_last.keys()):
    d=month_last[ym]
    pos_list=[]
    for code in INDEX_CODES:
        if code not in sig:continue
        name,dmap=sig[code]
        if dmap.get(d,False):pos_list.append(name)
    n=len(pos_list)
    tag=' <- 7/10!' if n>=7 else ''
    bar='+'*n+'-'*(10-n)
    print('  %-8s %2d %s  %s%s'%(ym,n,bar,','.join(pos_list),tag))

# Key dates: when did ALL->DEF happen and when did DEF->ALL trigger
print('\n\n  '+('='*100))
print('  FIRST 7/10 REACHED EACH YEAR')
print('  '+('='*100))
for year in['2020','2021','2022','2023','2024','2025','2026']:
    first_d=None;first_list=[]
    for ym in sorted(month_last.keys()):
        if not ym.startswith(year):continue
        d=month_last[ym]
        pos_list=[sig[c][0] for c in INDEX_CODES if c in sig and sig[c][1].get(d,False)]
        if len(pos_list)>=7:
            first_d=ym;first_list=pos_list;break
    if first_d:
        print('  %s: %s (%d/10: %s)'%(year,first_d,len(first_list),','.join(first_list)))
    else:
        print('  %s: NEVER reached 7/10'%year)

# 2026 daily detail for Q1
print('\n\n  '+('='*100))
print('  2026 Q1 DAILY SLOPE COUNT (when does 7/10 first trigger?)')
print('  '+('='*100))
last_n=-1
for d in all_dates:
    if '2026-01-01'<=d<='2026-04-01':
        pos=[sig[c][0] for c in INDEX_CODES if c in sig and sig[c][1].get(d,False)]
        n=len(pos)
        if n!=last_n:
            tag=' <-- SWITCH!' if n>=7 else ''
            print('  %s  %2d/10  +:%s%s'%(d,n,(','.join(pos) if pos else 'NONE'),tag))
            last_n=n

print('\nDone!')
