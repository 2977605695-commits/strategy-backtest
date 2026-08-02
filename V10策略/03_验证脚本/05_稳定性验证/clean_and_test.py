"""清洗89池: 所有标的重新对齐到沪深300的1591天基准, 修复日期错位.
剔除内部空洞>10的标的. 然后重跑三池对比+89池扫参."""
import json, os, math
from collections import defaultdict
from datetime import datetime

RF=0.025; TD=252; INIT=int(1e7)
SL_MA=8; BULL_MH=0
SLIP=0.0003; COMM=0.00025; STAMP=0.0

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

# 基准日历
base=json.load(open('data/etf_510300.json',encoding='utf-8'))
base_dates=[b['date'] for b in base['bars']]
base_set=set(base_dates)
print('基准日历: %d 天' % len(base_dates))

def load_aligned(code):
    """加载并对齐到基准日历, 返回 {date:{close,high,valid}} 或 None(质量差)."""
    for dd in ['data_new','data']:
        p=os.path.join(dd,'etf_'+code+'.json')
        if os.path.exists(p):
            d=json.load(open(p,encoding='utf-8'))
            # 建立 date->bar 映射
            by_date={}
            for b in d['bars']:
                px=float(b['close'])
                by_date[b['date']]={'close':px,'high':float(b.get('high',px)) if px>0 else 0.0,'valid':px>0}
            # 对齐基准
            aligned={}
            seen_real=False; internal_holes=0
            for bd in base_dates:
                if bd in by_date and by_date[bd]['valid']:
                    aligned[bd]=by_date[bd]; seen_real=True
                elif bd in by_date and not by_date[bd]['valid']:
                    aligned[bd]={'close':0.0,'high':0.0,'valid':False}
                else:
                    # 日期不在: 如果还没上市, 补0; 如果已上市, 算空洞(用前值填充)
                    if not seen_real:
                        aligned[bd]={'close':0.0,'high':0.0,'valid':False}
                    else:
                        # 用前一个有效值填充
                        internal_holes+=1
                        aligned[bd]={'close':0.0,'high':0.0,'valid':False}  # 标记无效
            # 内部空洞用前值填充(修复停牌)
            prev_close=0.0; prev_high=0.0
            for bd in base_dates:
                if aligned[bd]['valid']:
                    prev_close=aligned[bd]['close']; prev_high=aligned[bd]['high']
                elif prev_close>0:  # 已上市后的停牌, 用前值
                    aligned[bd]={'close':prev_close,'high':prev_high,'valid':True}
            n_real=sum(1 for bd in base_dates if aligned[bd]['close']>0)
            return aligned, n_real, internal_holes, d.get('name','?')
    return None,0,999,'?'

# 加载所有标的
ETF_33=['159782','588380','588870','588080','588300','518800','589720','588890','588170','588200','159995','512480','515880','515050','159819','159992','512010','518880','159937','513180','513050','513100','159509','588000','588220','510300','159915','510050','511010','511260','510880','512890','159301']
NEW_56=sorted([f.replace('etf_','').replace('.json','') for f in os.listdir('data_new') if f.startswith('etf_')])
ALL=sorted(set(ETF_33+NEW_56))

print('清洗并对齐 %d 只标的...\n' % len(ALL))
all_data={}; names={}; stats=[]
clean_codes=[]
for code in ALL:
    aligned,n_real,holes,name=load_aligned(code)
    if aligned is None or n_real<100:
        print('  %-8s %-14s 剔除(数据不足)' % (code,name[:12])); continue
    if holes>15:
        print('  %-8s %-14s 剔除(内部空洞%d)' % (code,name[:12],holes)); continue
    all_data[code]=aligned; names[code]=name
    clean_codes.append(code)
    stats.append((code,name,n_real,holes))

print('清洗后保留 %d 只:' % len(clean_codes))
# 按真实数据量排序显示
stats.sort(key=lambda x:-x[2])
print('  %-8s %-14s %6s %6s' % ('代码','名称','真实','空洞'))
for code,name,nr,holes in stats:
    print('  %-8s %-14s %6d %5d' % (code,name[:12],nr,holes))

# 保存清洗后的标的清单
CLEAN_CODES=clean_codes
print('\n干净标的: %d 只' % len(CLEAN_CODES))

# ===== 用清洗后的数据跑引擎 =====
def precompute(codes, f_ma, s_ma, sl_lb):
    all_trnd={};all_ratio={};above_ma60={};etf_highs={}
    for c in codes:
        cl=[all_data[c][bd]['close'] if all_data[c][bd]['valid'] else float('nan') for bd in base_dates]
        hi=[all_data[c][bd]['high'] if all_data[c][bd]['valid'] else 0.0 for bd in base_dates]
        mf=ma(cl,f_ma);ms_=ma(cl,s_ma);msl=ma(cl,SL_MA);slo_=slp(msl,sl_lb)
        m60=ma(cl,60)
        trnd={};rat={};abv={}
        for i in range(len(base_dates)):
            d=base_dates[i];valid=all_data[c][d]['valid']
            if valid and not math.isnan(mf[i]) and not math.isnan(ms_[i]) and ms_[i]>0:
                sk=not math.isnan(slo_[i]) and slo_[i]>0
                trnd[d]=mf[i-1]>ms_[i-1] and sk if i>0 else False
                rat[d]=mf[i-1]/ms_[i-1] if i>0 and ms_[i-1]>0 else 1.0
            else:trnd[d]=False;rat[d]=1.0
            abv[d]=valid and (not math.isnan(m60[i-1]) and cl[i-1]>m60[i-1]) if i>0 else False
        all_trnd[c]=trnd;all_ratio[c]=rat;above_ma60[c]=abv;etf_highs[c]={bd:all_data[c][bd]['high'] for bd in base_dates}
    return all_trnd,all_ratio,above_ma60,etf_highs

INDEX_CODES={'510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF','513100':'纳指ETF','511260':'十年国债',
    '512890':'红利低波','159992':'创新药ETF'}
# 指数斜率 (用清洗数据)
index_slope={}
for code in INDEX_CODES:
    if code not in all_data:continue
    cl=[all_data[code][bd]['close'] if all_data[code][bd]['valid'] else float('nan') for bd in base_dates]
    m60=ma(cl,60);sl=slp(m60,20)
    index_slope[code]={base_dates[i]:(not math.isnan(sl[i-1]) and sl[i-1]>0) if i>0 else False for i in range(len(base_dates))}

def run(codes, def_codes, sig, p):
    all_trnd,all_ratio,above_ma60,etf_highs=sig
    dm={c:all_data[c] for c in codes}
    fd={c:next((bd for bd in base_dates if all_data[c][bd]['valid']),'9999-12-31') for c in codes}
    trail_bull=p.get('trail_bull',0.03);trail_bear=p.get('trail_bear',0.06)
    mh_bear=p.get('mh_bear',7);pullback=p.get('pullback',0.05);need_n=p.get('need_n',7)
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;dvs=[]
    pool_mode='all';rolling_pnl=[];n_trades=0
    for d in base_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=not index_slope.get('510300',{}).get(d,False)
        cur_trail=trail_bear if is_bear else trail_bull;cur_mh=mh_bear if is_bear else BULL_MH
        if pos:
            bar=dm[pos].get(d)
            if bar and bar['valid']:
                px_raw=bar['close'];px_sell=px_raw*(1-SLIP-COMM-STAMP)
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
                    if pool_mode=='defensive' and sum(1 for c2 in INDEX_CODES if index_slope.get(c2,{}).get(d,False))>=need_n:pool_mode='all'
        if not pos and cash>0:
            cands=[]
            for c in avail:
                if pool_mode=='defensive' and c not in def_codes:continue
                if not all_trnd.get(c,{}).get(d,False):continue
                if not above_ma60.get(c,{}).get(d,False):continue
                if pullback>0:
                    hi20=0
                    di=base_dates.index(d)
                    for lb in range(1,21):
                        pdi=di-lb
                        if 0<=pdi<len(base_dates):hi20=max(hi20,etf_highs[c].get(base_dates[pdi],0))
                    if hi20>0 and (hi20-dm[c][d]['close'])/hi20<pullback:continue
                bar=dm[c].get(d)
                if bar and bar['valid']:cands.append((c,all_ratio[c].get(d,1.0),bar['close']))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px_raw=cands[0];bp=px_raw*(1+SLIP+COMM)
                shares=cash/bp;peak=px_raw;pos=c;entry_d=d;entry_date=dt_obj;cash=0.0
        pos_val=shares*dm[pos].get(d,{}).get('close',0) if pos else 0
        dvs.append(cash+pos_val)
    if pos:
        bar=dm[pos].get(base_dates[-1])
        if bar and bar['valid']:cash=shares*bar['close']*(1-SLIP-COMM-STAMP)
    fv=dvs[-1];rets=[(dvs[i]-dvs[i-1])/dvs[i-1] for i in range(1,len(dvs)) if dvs[i-1]>0]
    pk=dvs[0];mdd=0.0
    for v in dvs:
        if v>pk:pk=v
        dd=(pk-v)/pk
        if dd>mdd:mdd=dd
    tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    yr={}
    date_idx={d:i for i,d in enumerate(base_dates)}
    for y in ['2020','2021','2022','2023','2024','2025','2026']:
        ld=max((dd for dd in base_dates if dd[:4]==y),default=base_dates[-1])
        sd0=min((dd for dd in base_dates if dd[:4]==y),default=base_dates[0])
        s_v=dvs[date_idx[sd0]-1] if date_idx[sd0]>0 else INIT
        e_v=dvs[date_idx[ld]]
        yr[y]=(e_v-s_v)/s_v if s_v>0 else 0
    return {'sh':sh,'tr':tr,'mdd':mdd,'n_trades':n_trades,'yr':yr}

# 防御池: 选清洗后池里的黄金/债/红利/宽基
DEF=['518880','159937','518800','511010','511260','510880','512890','510050','159934','518850','159981','511220','515080']

# ===== 清洗后89池(实际数量) 基准 + 扫参 =====
codes_pool=[c for c in CLEAN_CODES]
base_p={'f_ma':6,'s_ma':15,'sl_lb':4,'trail_bull':0.03,'trail_bear':0.06,'mh_bear':7,'pullback':0.05,'need_n':7}
SIG=precompute(codes_pool,6,15,4)
base_r=run(codes_pool,DEF,SIG,base_p)
print('\n'+'='*100)
print('  清洗后大池(%d只) 基准表现' % len(codes_pool))
print('='*100)
print('  原版参数: 夏普=%.3f 收益=%+.0f%% 回撤=%.1f%% 笔数=%d' % (
    base_r['sh'],base_r['tr']*100,base_r['mdd']*100,base_r['n_trades']))

# 扫参
cur=dict(base_p)
def sweep(name,key,vals,rb):
    global SIG
    results=[]
    for v in vals:
        p=dict(cur);p[key]=v
        sig=precompute(codes_pool,p.get('f_ma',6),p.get('s_ma',15),p.get('sl_lb',4)) if rb else SIG
        r=run(codes_pool,DEF,sig,p)
        results.append((v,r['sh'],r['tr'],r['mdd']))
    results.sort(key=lambda x:-x[1])
    cur[key]=results[0][0]
    if rb: SIG=precompute(codes_pool,cur['f_ma'],cur['s_ma'],cur['sl_lb'])
    return results[0]

print('\n【全量扫参】')
print('  %-16s %10s %10s %10s' % ('参数','最优值','夏普','收益'))
print('  '+'-'*55)
for name,key,vals,rb in [
    ('快线MA','f_ma',[4,5,6,7,8],True),('慢线MA','s_ma',[10,12,15,18,20],True),
    ('斜率回看','sl_lb',[3,4,5,6],True),('牛市Trail','trail_bull',[0.02,0.025,0.03,0.035,0.04],False),
    ('熊市Trail','trail_bear',[0.04,0.05,0.06,0.08,0.10],False),('熊市MH','mh_bear',[0,3,5,7,10],False),
    ('回撤过滤','pullback',[0,0.03,0.05,0.08,0.10],False),('DEF->ALL','need_n',[5,6,7,8,9],False),
]:
    best=sweep(name,key,vals,rb)
    mark=' ★改进' if best[1]>base_r['sh']+0.01 else (' =基准' if abs(best[1]-base_r['sh'])<0.01 else ' 下降')
    print('  %-16s %10s %10.3f %+9.0f%%%s' % (name,cur[key],best[1],best[2]*100,mark))

final_sig=precompute(codes_pool,cur['f_ma'],cur['s_ma'],cur['sl_lb'])
final_r=run(codes_pool,DEF,final_sig,cur)
print('\n  最终优化: 夏普=%.3f→%.3f 收益=%+.0f%%→%+.0f%% 回撤=%.1f%%→%.1f%%' % (
    base_r['sh'],final_r['sh'],base_r['tr']*100,final_r['tr']*100,base_r['mdd']*100,final_r['mdd']*100))
changed=[k for k in base_p if base_p[k]!=cur[k]]
print('  调整参数: %s' % ', '.join('%s:%s→%s'%(k,base_p[k],cur[k]) for k in changed) if changed else '(原版最优)')

print('\n【分年度: 原版参数 vs 优化参数】')
print('  %-6s %12s %12s' % ('年份','原版参数','优化参数'))
for y in ['2020','2021','2022','2023','2024','2025','2026']:
    print('  %-6s %+11.0f%% %+11.0f%%' % (y,base_r['yr'].get(y,0)*100,final_r['yr'].get(y,0)*100))
