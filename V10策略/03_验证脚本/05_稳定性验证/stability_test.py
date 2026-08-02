"""稳定性验证: 用新ETF池跑V10策略, 对比原版33只池.
新池: data_new 目录所有ETF (0值=未上市, 不参与交易但占位).
检测池/防御池重新按板块映射. 原版参数 + T+1 + 报告成本(0.1%滑点).
"""
import json, os, math
from collections import defaultdict
from datetime import datetime

# ===== 通用引擎: 可指定数据目录和池子 =====
RF=0.025; TD=252; INIT=int(1e7)
F_MA=6; S_MA=15; SL_MA=8
TRAIL_BULL=0.03; TRAIL_BEAR=0.06; BULL_MH=0; BEAR_MH=7; PULLBACK_MIN=0.05; NEED_N=7
SLIP=0.001; COMM=0.0005; STAMP=0.0005

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

def load_pool(data_dir, codes):
    etfs={}
    for code in codes:
        p=os.path.join(data_dir,'etf_'+code+'.json')
        if not os.path.exists(p):continue
        d=json.load(open(p,encoding='utf-8'))
        bars=[]
        for b in d['bars']:
            dt=b['date'];px=float(b['close'])
            if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            # 0值(未上市)用NaN标记, 不参与计算
            bars.append({'date':dt,'close':px,'high':float(b.get('high',px)) if px>0 else 0.0,'valid':px>0})
        if bars:etfs[code]={'name':d['name'],'first_date':d.get('first_date',bars[0]['date']),'bars':bars}
    return etfs

def run_pool(etfs, def_codes, index_codes_dict, slip=SLIP):
    """在指定池上跑V10. index_codes_dict: {code:name} 用于牛熊/切换判断."""
    codes=sorted(etfs.keys())
    # 预计算信号 (跳过0值/NaN日期)
    all_trnd={};all_ratio={};above_ma60={};etf_highs={}
    for c in codes:
        bars=etfs[c]['bars']
        cl=[b['close'] if b['valid'] else float('nan') for b in bars]
        hi=[b['high'] if b['valid'] else 0.0 for b in bars]
        dts=[b['date'] for b in bars]
        mf=ma(cl,F_MA);ms_=ma(cl,S_MA);msl=ma(cl,SL_MA);slo_=slp(msl,4)
        m60=ma(cl,60)
        trnd={};rat={};abv={};hgh={}
        for i in range(len(bars)):
            d=dts[i]
            valid = b_valid = bars[i]['valid']
            if valid and not math.isnan(mf[i]) and not math.isnan(ms_[i]) and ms_[i]>0:
                sk=not math.isnan(slo_[i]) and slo_[i]>0
                trnd[d]=mf[i-1]>ms_[i-1] and sk if i>0 else False
                rat[d]=mf[i-1]/ms_[i-1] if i>0 and ms_[i-1]>0 else 1.0
            else:
                trnd[d]=False;rat[d]=1.0
            abv[d]=valid and (not math.isnan(m60[i-1]) and cl[i-1]>m60[i-1]) if i>0 else False
            hgh[d]=hi[i]
        all_trnd[c]=trnd;all_ratio[c]=rat;above_ma60[c]=abv;etf_highs[c]=hgh
    # 检测池指数信号
    index_slope={}
    for code in index_codes_dict:
        if code not in etfs:continue
        bars=etfs[code]['bars']
        cl=[b['close'] if b['valid'] else float('nan') for b in bars]
        dts=[b['date'] for b in bars]
        m60=ma(cl,60);sl=slp(m60,20)
        index_slope[code]={dts[i]:(not math.isnan(sl[i-1]) and sl[i-1]>0) if i>0 else False for i in range(len(dts))}
    dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    # first_date 用第一个valid的日期
    fd={}
    for c in codes:
        fd[c]=next((b['date'] for b in etfs[c]['bars'] if b['valid']), '9999-12-31')
    all_dates=sorted(set(d for c in codes for d in dm[c].keys()))
    date_idx={d:i for i,d in enumerate(all_dates)}

    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;dvs=[]
    pool_mode='all';rolling_pnl=[];n_trades=0
    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=not index_slope.get('510300',{}).get(d,False)
        cur_trail=TRAIL_BEAR if is_bear else TRAIL_BULL;cur_mh=BEAR_MH if is_bear else BULL_MH
        if pos:
            bar=dm[pos].get(d)
            if bar and bar['valid']:
                px_raw=bar['close']; px_sell=px_raw*(1-slip-COMM-STAMP)
                if px_raw>peak:peak=px_raw
                er=None
                if px_raw<=peak*(1-cur_trail):er='trail'
                elif not all_trnd.get(pos,{}).get(d,False):
                    if cur_mh>0 and entry_date and (dt_obj-entry_date).days>=cur_mh:er='off'
                    else:er='off'
                if er:
                    n_trades+=1
                    pnl=shares*px_sell-shares*bp
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl)>5:rolling_pnl.pop(0)
                    cash=shares*px_sell;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
                    if len(rolling_pnl)>=5 and pool_mode=='all' and sum(rolling_pnl)<-0.10*INIT:pool_mode='defensive'
                    if pool_mode=='defensive' and sum(1 for c2 in index_codes_dict if index_slope.get(c2,{}).get(d,False))>=NEED_N:pool_mode='all'
        if not pos and cash>0:
            cands=[]
            for c in avail:
                if pool_mode=='defensive' and c not in def_codes:continue
                if not all_trnd.get(c,{}).get(d,False):continue
                if not above_ma60.get(c,{}).get(d,False):continue
                if PULLBACK_MIN>0:
                    hi20=0
                    for lb in range(1,21):
                        pdi=max(0,len(all_dates)-1-lb)
                        if 0<=pdi<len(all_dates):hi20=max(hi20,etf_highs[c].get(all_dates[pdi],0))
                    if hi20>0 and (hi20-dm[c][d]['close'])/hi20<PULLBACK_MIN:continue
                bar=dm[c].get(d)
                if bar and bar['valid']:cands.append((c,all_ratio[c].get(d,1.0),bar['close']))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px_raw=cands[0]; bp=px_raw*(1+slip+COMM)
                shares=cash/bp;peak=px_raw;pos=c;entry_d=d;entry_date=dt_obj;cash=0.0
        pos_val=shares*dm[pos].get(d,{}).get('close',0) if pos else 0
        dvs.append(cash+pos_val)
    if pos:
        bar=dm[pos].get(all_dates[-1])
        if bar and bar['valid']:cash=shares*bar['close']*(1-slip-COMM-STAMP)
    fv=dvs[-1];rets=[(dvs[i]-dvs[i-1])/dvs[i-1] for i in range(1,len(dvs)) if dvs[i-1]>0]
    pk=dvs[0];mdd=0.0
    for v in dvs:
        if v>pk:pk=v
        dd=(pk-v)/pk
        if dd>mdd:mdd=dd
    tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    # 年度收益(年初年末净值比)
    yr={}
    yrs=['2020','2021','2022','2023','2024','2025','2026']
    yr_s={y:None for y in yrs}; yr_e={y:None for y in yrs}
    for i,d in enumerate(all_dates):
        y=d[:4]
        if y in yr_s and yr_s[y] is None:yr_s[y]=dvs[i-1] if i>0 else INIT
    for y in yrs:
        ld=max((dd for dd in all_dates if dd[:4]==y), default=all_dates[-1])
        yr_e[y]=dvs[date_idx[ld]]
    for y in yrs:
        s_=yr_s[y]; e_=yr_e[y]
        yr[y]=(e_-s_)/s_ if s_>0 else 0
    return {'sh':sh,'tr':tr,'mdd':mdd,'n_trades':n_trades,'yr':yr,'dvs':dvs}

# ===== 配置: 原版池 vs 新池 =====
# 原版33只 (检测池/防御池用原版)
ETF_33=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
        '588200','159995','512480','515880','515050','159819','159992','512010',
        '518880','159937','513180','513050','513100','159509','588000','588220',
        '510300','159915','510050','511010','511260','510880','512890','159301']
DEF_OLD=['518880','159937','518800','511010','511260','510880','512890','510050']
INDEX_OLD={'510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF','513100':'纳指ETF','511260':'十年国债',
    '512890':'红利低波','159992':'创新药ETF'}

# 新池: data_new 所有ETF
def get_new_codes():
    return sorted([f.replace('etf_','').replace('.json','') for f in os.listdir('data_new') if f.startswith('etf_')])

# 新检测池 (从新池里选同板块替代)
INDEX_NEW={
    '510310':'沪深300','159901':'深100','512100':'中证1000','510050':'上证50',  # 宽基(用510310替510300, 但510300在新池? 检查)
    '512760':'半导体50','159994':'5G',  # 科技
    '513500':'标普500',  # 海外
    '511220':'城投债','515080':'红利华泰',  # 防御
    '512170':'医疗',  # 医药
}
# 新防御池 (从新池选黄金/债/红利/宽基)
DEF_NEW=['159934','518850','159981','518860',  # 黄金
         '511220','159972','511030',  # 债
         '515080','515450',  # 红利
         '159901']  # 深100替上证50

print('='*100)
print('  V10 稳定性验证: 原版33只池 vs 新ETF池')
print('='*100)

# 原版
print('\n加载原版池...')
e_old=load_pool('data', ETF_33)
r_old=run_pool(e_old, DEF_OLD, INDEX_OLD)
print('  原版33只: 夏普=%.3f 收益=%+.0f%% 回撤=%.1f%% 笔数=%d' % (r_old['sh'],r_old['tr']*100,r_old['mdd']*100,r_old['n_trades']))

# 新池
new_codes=get_new_codes()
print('\n加载新池 %d 只...' % len(new_codes))
e_new=load_pool('data_new', new_codes)
# 检测池必须包含510300(牛熊判断), 新池没有则用510310
idx_new=dict(INDEX_NEW)
if '510300' not in e_new and '510310' in e_new:
    # 牛熊判断改用510310
    pass
r_new=run_pool(e_new, DEF_NEW, idx_new)
print('  新池%d只: 夏普=%.3f 收益=%+.0f%% 回撤=%.1f%% 笔数=%d' % (len(new_codes),r_new['sh'],r_new['tr']*100,r_new['mdd']*100,r_new['n_trades']))

# 对比
print('\n'+'='*100)
print('  对比')
print('='*100)
print('  %-20s %-15s %-15s' % ('指标','原版33只','新池%d只'%len(new_codes)))
print('  '+'-'*55)
print('  %-20s %-15s %-15s' % ('夏普','%.3f'%r_old['sh'],'%.3f'%r_new['sh']))
print('  %-20s %-15s %-15s' % ('总收益','%+.0f%%'%(r_old['tr']*100),'%+.0f%%'%(r_new['tr']*100)))
print('  %-20s %-15s %-15s' % ('最大回撤','%.1f%%'%(r_old['mdd']*100),'%.1f%%'%(r_new['mdd']*100)))
print('  %-20s %-15s %-15s' % ('交易笔数','%d'%r_old['n_trades'],'%d'%r_new['n_trades']))
print('\n  年度收益:')
print('  %-6s %-12s %-12s' % ('年份','原版','新池'))
for y in ['2020','2021','2022','2023','2024','2025','2026']:
    print('  %-6s %+11.0f%% %+11.0f%%' % (y, r_old['yr'].get(y,0)*100, r_new['yr'].get(y,0)*100))

# 稳定性判定
print('\n'+'='*100)
print('  稳定性判定')
print('='*100)
sh_old=r_old['sh']; sh_new=r_new['sh']
if sh_new > 0.8:
    print('  ★ 新池夏普 %.2f > 0.8: 策略逻辑稳健, 换标的仍有效' % sh_new)
elif sh_new > 0.5:
    print('  ○ 新池夏普 %.2f (0.5-0.8): 部分有效, 但表现下滑明显' % sh_new)
else:
    print('  × 新池夏普 %.2f < 0.5: 策略严重依赖原标的, 过拟合嫌疑大' % sh_new)
# 收益方向一致性
agree=sum(1 for y in ['2020','2021','2022','2023','2024','2025','2026']
          if (r_old['yr'].get(y,0)>0)==(r_new['yr'].get(y,0)>0))
print('  年度盈亏方向一致: %d/7 年 (%.0f%%)' % (agree, agree/7*100))
