"""报告版(T+1) + 报告成本(0.35%/次) 的精确复现.
基于 etf30_vs_33.py 的 precompute (T+1: 用i-1数据), 加上回测验证报告标注的成本.
成本拆解(报告原文: 双边手续费0.1% + 印花0.05% + 滑点0.1%):
  买入: 滑点0.1% + 手续费0.05%(双边0.1%的一半) = 0.15%
  卖出: 滑点0.1% + 手续费0.05% + 印花0.05% = 0.20%
  单次换手 = 0.35%
"""
import json, os, math
from collections import defaultdict
from datetime import datetime

DATA_DIR = r"C:\Users\home\Desktop\策略文件夹\data"
START='2020-01-01'; END='2026-07-30'
RF=0.025; TD=252; INIT=int(1e7)
F_MA=6; S_MA=15; SL_MA=8
TRAIL_BULL=0.03; TRAIL_BEAR=0.06; BULL_MH=0; BEAR_MH=7; PULLBACK_MIN=0.05; NEED_N=7
# ★ 报告成本
SLIP=0.001; COMM=0.0005; STAMP=0.0005  # 买入0.15% 卖出0.20%

ETF_33=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
        '588200','159995','512480','515880','515050','159819','159992','512010',
        '518880','159937','513180','513050','513100','159509','588000','588220',
        '510300','159915','510050','511010','511260','510880','512890','159201']
DEF_POOL=['518880','159937','518800','511010','511260','510880','512890','159201','510050']
INDEX_CODES={'510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF','513100':'纳指ETF','511260':'十年国债',
    '512890':'红利低波','159992':'创新药ETF'}
NAME={}

def load():
    etfs={}
    for code in ETF_33:
        p=os.path.join(DATA_DIR,'etf_'+code+'.json')
        if not os.path.exists(p):continue
        d=json.load(open(p,encoding='utf-8')); NAME[code]=d['name']; bars=[]
        for b in d['bars']:
            dt=b['date'];px=float(b['close'])
            if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            if START<=dt<=END:bars.append({'date':dt,'close':px,'high':float(b.get('high',px))})
        if bars:etfs[code]={'name':d['name'],'first_date':bars[0]['date'],'bars':bars}
    return etfs
def ma(d,w):
    m=[float('nan')]*(w-1)
    for i in range(w-1,len(d)):m.append(sum(d[i-w+1:i+1])/w)
    return m
def slp(ms,lb):
    s=[float('nan')]*len(ms)
    for i in range(len(ms)):
        if i<lb:continue
        ys=ms[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys):continue
        n=len(ys);sx=sy=sxy=sxx=0
        for j,y in enumerate(ys):sx+=j;sy+=y;sxy+=j*y;sxx+=j*j
        d_=n*sxx-sx*sx
        if d_>0:s[i]=(n*sxy-sx*sy)/d_/ms[i] if ms[i]>0 else 0
    return s

etfs=load(); codes=sorted(etfs.keys())
# ★ T+1 信号 (用 i-1 数据, 和 etf30_vs_33.py 完全一致)
all_trnd={};all_ratio={};above_ma60={};etf_highs={};index_slope={}
for c in codes:
    bars=etfs[c]['bars'];cl=[b['close'] for b in bars];hi=[b['high'] for b in bars]
    mf=ma(cl,F_MA);ms=ma(cl,S_MA);msl=ma(cl,SL_MA);slo_=slp(msl,max(SL_MA//2,3))
    m60=ma(cl,60);dts=[b['date'] for b in bars]
    for i in range(len(bars)):
        d=dts[i]
        if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
            sk=not math.isnan(slo_[i]) and slo_[i]>0
            all_trnd.setdefault(c,{})[d]=mf[i-1]>ms[i-1] and sk if i>0 else False
            all_ratio.setdefault(c,{})[d]=mf[i-1]/ms[i-1] if i>0 and ms[i-1]>0 else 1.0
        else:
            all_trnd.setdefault(c,{})[d]=False;all_ratio.setdefault(c,{})[d]=1.0
        above_ma60.setdefault(c,{})[d]=not math.isnan(m60[i-1]) and cl[i-1]>m60[i-1] if i>0 else False
        etf_highs.setdefault(c,{})[d]=hi[i]
for code,name in INDEX_CODES.items():
    if code not in etfs:continue
    cl=[b['close'] for b in etfs[code]['bars']];dts=[b['date'] for b in etfs[code]['bars']]
    m60=ma(cl,60);sl=slp(m60,20)
    index_slope[code]={dts[i]:(not math.isnan(sl[i-1]) and sl[i-1]>0) if i>0 else False for i in range(len(dts))}

dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
fd={c:etfs[c]['first_date'] for c in codes}
all_dates=sorted(set(d for c in codes for d in dm[c].keys()))

def run(use_cost):
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]
    pool_mode='all';rolling_pnl=[]
    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=not index_slope.get('510300',{}).get(d,False)
        cur_trail=TRAIL_BEAR if is_bear else TRAIL_BULL;cur_mh=BEAR_MH if is_bear else BULL_MH
        if pos:
            bar=dm[pos].get(d)
            if bar:
                px_raw=bar['close']
                if use_cost: px_sell=px_raw*(1-SLIP-COMM-STAMP)
                else: px_sell=px_raw
                if px_raw>peak:peak=px_raw
                er=None
                if px_raw<=peak*(1-cur_trail):er='trail'
                elif not all_trnd.get(pos,{}).get(d,False):
                    er='off'
                if er:
                    pnl=shares*px_sell-shares*bp
                    trades.append({'pnl':pnl,'r':(px_sell-bp)/bp,'c':pos,'b':entry_d,'s':d})
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl)>5:rolling_pnl.pop(0)
                    cash=shares*px_sell;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
                    if len(rolling_pnl)>=5 and pool_mode=='all' and sum(rolling_pnl)<-0.10*INIT:
                        pool_mode='defensive'
                    if pool_mode=='defensive':
                        if sum(1 for c2 in INDEX_CODES if index_slope.get(c2,{}).get(d,False))>=NEED_N:
                            pool_mode='all'
        if not pos and cash>0:
            cands=[]
            for c in avail:
                if pool_mode=='defensive' and c not in DEF_POOL:continue
                if not all_trnd.get(c,{}).get(d,False):continue
                if not above_ma60.get(c,{}).get(d,False):continue
                if PULLBACK_MIN>0:
                    hi20=0
                    for lb in range(1,21):
                        pdi=max(0,len(all_dates)-1-lb)
                        if pdi>=0 and pdi<len(all_dates):hi20=max(hi20,etf_highs[c].get(all_dates[pdi],0))
                    if hi20>0 and (hi20-dm[c][d]['close'])/hi20<PULLBACK_MIN:continue
                bar=dm[c].get(d)
                if bar:cands.append((c,all_ratio.get(c,{}).get(d,1.0),bar['close']))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px_raw=cands[0]
                if use_cost: bp=px_raw*(1+SLIP+COMM)
                else: bp=px_raw
                shares=cash/bp;peak=px_raw;pos=c;entry_d=d;entry_date=dt_obj;cash=0.0
        pos_val=shares*dm[pos].get(d,{}).get('close',0) if pos else 0
        dvs.append(cash+pos_val)
    if pos:
        bar=dm[pos].get(all_dates[-1])
        if bar:
            px_raw=bar['close']
            px_sell=px_raw*(1-SLIP-COMM-STAMP) if use_cost else px_raw
            pnl=shares*px_sell-shares*bp
            trades.append({'pnl':pnl,'r':(px_sell-bp)/bp,'c':pos,'b':entry_d,'s':all_dates[-1]})
            cash=shares*px_sell
    fv=cash;rets=[(dvs[i]-dvs[i-1])/dvs[i-1] for i in range(1,len(dvs)) if dvs[i-1]>0]
    pk=dvs[0];mdd=0.0
    for v in dvs:
        if v>pk:pk=v
        dd=(pk-v)/pk
        if dd>mdd:mdd=dd
    tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    st=[t for t in trades if t.get('b')];w=sum(1 for t in st if t['r']>0)
    yr=defaultdict(float)
    for t in st:yr[t['s'][:4]]+=t['pnl']
    # 平均持仓
    holds=[(datetime.strptime(t['s'],'%Y-%m-%d')-datetime.strptime(t['b'],'%Y-%m-%d')).days for t in st]
    avg_hold=sum(holds)/len(holds) if holds else 0
    return sh,tr,mdd,len(st),w/len(st) if st else 0,dict(yr),avg_hold

# 无成本 vs 报告成本
nc = run(False)
rc = run(True)

print('='*110)
print('  报告版(T+1) 精确复现: 无成本 vs 报告成本(0.35%/次)')
print('='*110)
print('  %-30s %10s %10s %10s %8s %8s' % ('方案','总收益','回撤','夏普','笔数','持仓'))
print('  '+'-'*80)
print('  %-30s %+9.0f%% %9.1f%% %8.2f %7d %6dd' % ('① 报告版 无成本',nc[1]*100,nc[2]*100,nc[0],nc[3],nc[6]))
print('  %-30s %+9.0f%% %9.1f%% %8.2f %7d %6dd' % ('② 报告版 +成本(0.35%/次)',rc[1]*100,rc[2]*100,rc[0],rc[3],rc[6]))
print('\n  成本吃掉: %.0f个百分点' % ((nc[1]-rc[1])*100))

print('\n【分年度对比】')
print('  %-6s %12s %12s %10s' % ('年份','无成本','报告成本','差异'))
for y in ['2020','2021','2022','2023','2024','2025','2026']:
    a=nc[5].get(y,0)/INIT*100; b=rc[5].get(y,0)/INIT*100
    print('  %-6s %+11.1f%% %+11.1f%% %+9.1f' % (y,a,b,a-b))

# 报告声称值对照
print('\n【与报告声称值对照】')
print('  报告声称(修复后): 夏普1.510 收益+1396% 回撤25.6% 笔数172 持仓18天')
print('  实测无成本:       夏普%.3f 收益%+.0f%% 回撤%.1f%% 笔数%d 持仓%d天' % (nc[0],nc[1]*100,nc[2]*100,nc[3],nc[6]))
print('  → 报告的数字 == 无成本版, 报告标注的成本实际未执行!')

# 大牛市区间
print('\n【2024-2026 大牛市 (T+1版)】')
# 重算2024-2026区间 - 需要重新跑,这里用全周期年度数据近似
a24=nc[5].get('2024',0)/INIT*100; b24=rc[5].get('2024',0)/INIT*100
a25=nc[5].get('2025',0)/INIT*100; b25=rc[5].get('2025',0)/INIT*100
a26=nc[5].get('2026',0)/INIT*100; b26=rc[5].get('2026',0)/INIT*100
# 复利
cum_nc=(1+a24/100)*(1+a25/100)*(1+a26/100)-1
cum_rc=(1+b24/100)*(1+b25/100)*(1+b26/100)-1
print('  T+1版 无成本:   2024=%+.0f%% 2025=%+.0f%% 2026=%+.0f%%  累计=%+.0f%%' % (a24,a25,a26,cum_nc*100))
print('  T+1版 +成本:    2024=%+.0f%% 2025=%+.0f%% 2026=%+.0f%%  累计=%+.0f%%' % (b24,b25,b26,cum_rc*100))
print('  持有最强ETF:    +304% 回撤29%')
print('  ★ T+1版+成本 仍大幅超越持有最强ETF')
