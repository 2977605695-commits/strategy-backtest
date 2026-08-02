"""超级组合回测: 89只按行业分层随机抽30只×10组, 每组全量扫参.
目的: 消除标的精选偏差, 得到参数的统计分布, 找出真正稳健的参数值.
费率: 真实ETF(万2.5佣金+0.03%滑点, 免印花).
"""
import json, os, math, random
from collections import defaultdict, Counter
from datetime import datetime

random.seed(42)  # 可复现
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

# 加载并清洗对齐(复用clean_and_test逻辑)
base=json.load(open('data/etf_510300.json',encoding='utf-8'))
base_dates=[b['date'] for b in base['bars']]

def load_aligned(code):
    for dd in ['data_new','data']:
        p=os.path.join(dd,'etf_'+code+'.json')
        if os.path.exists(p):
            d=json.load(open(p,encoding='utf-8'))
            by_date={}
            for b in d['bars']:
                px=float(b['close'])
                by_date[b['date']]={'close':px,'high':float(b.get('high',px)) if px>0 else 0.0,'valid':px>0}
            aligned={}; seen_real=False; prev_close=0.0; prev_high=0.0
            for bd in base_dates:
                if bd in by_date and by_date[bd]['valid']:
                    aligned[bd]=by_date[bd]; seen_real=True; prev_close=by_date[bd]['close']; prev_high=by_date[bd]['high']
                elif bd in by_date and prev_close>0:
                    aligned[bd]={'close':prev_close,'high':prev_high,'valid':True}
                elif seen_real and prev_close>0:
                    aligned[bd]={'close':prev_close,'high':prev_high,'valid':True}
                else:
                    aligned[bd]={'close':0.0,'high':0.0,'valid':False}
            n_real=sum(1 for bd in base_dates if aligned[bd]['close']>0)
            return aligned,n_real,d.get('name','?')
    return None,0,'?'

# 89只清单
ETF_33=['159782','588380','588870','588080','588300','518800','589720','588890','588170','588200','159995','512480','515880','515050','159819','159992','512010','518880','159937','513180','513050','513100','159509','588000','588220','510300','159915','510050','511010','511260','510880','512890','159301']
NEW_56=sorted([f.replace('etf_','').replace('.json','') for f in os.listdir('data_new') if f.startswith('etf_')])
ALL=sorted(set(ETF_33+NEW_56))

# 加载全部
all_data={}; names={}; n_real_map={}
for code in ALL:
    aligned,nr,name=load_aligned(code)
    if aligned and nr>=100:
        all_data[code]=aligned; names[code]=name; n_real_map[code]=nr

# ===== 行业分类 =====
SECTOR={
    # 科创板
    '科创': ['159782','588000','588080','588090','588150','588160','588190','588250','588260','588290','588380','588220','588280','588690','588870','588270','588380'],
    '科创芯片': ['588170','588200','588300','588210','588800','588900'],
    # 半导体/芯片
    '半导体': ['512480','512760','589720','561980','516620','159516','159565','562500'],
    # 通信/5G/数字
    '通信': ['515880','515050','159994','516950','588030'],
    'AI': ['159819','515070','515980','159825'],
    # 新能源
    '新能源': ['515790','516160'],
    # 海外/港股
    '海外': ['513100','159509','513500','513030','159920'],
    '港股科技': ['513180','513010','159740','513050','513080','159605','513330','513060'],
    # 黄金/商品
    '黄金': ['518880','159937','518800','518850','159934','159981','518860','562990'],
    '商品': ['159996'],
    # 债
    '债': ['511010','511260','511220','511380','159972','511030'],
    # 红利/价值
    '红利': ['510880','512890','515080','515450','159545'],
    # 宽基
    '宽基': ['510300','510310','159915','510050','159901','512100','562300'],
    # 医药
    '医药': ['159992','512010','512170','159883'],
    # 其他
    '传媒': ['512980'],
    '现金流': ['159301','159201'],
}
# 反查code->sector
code_sector={}
for sec,clist in SECTOR.items():
    for c in clist:
        if c in all_data: code_sector[c]=sec
# 补漏
for c in all_data:
    if c not in code_sector: code_sector[c]='其他'

# 显示行业分布
sec_count=Counter(code_sector.values())
print('行业分布(共%d只):' % len(all_data))
for sec,n in sec_count.most_common():
    print('  %-8s %d只' % (sec,n))

# ===== 分层抽样: 每组30只, 按行业比例 =====
def stratified_sample(n_target=30):
    """按行业比例分层抽样, 510300必选(牛熊判断基准)."""
    pool=[c for c in all_data if c!='510300']
    # 各行业按比例分配名额
    total=len(pool)
    picks=['510300']  # 必选
    remaining=n_target-1
    for sec,sec_codes_list in [(s,[c for c in cs if c in pool]) for s,cs in SECTOR.items()]:
        sec_codes=[c for c in sec_codes_list if c in pool]
        if not sec_codes: continue
        # 按比例分配, 至少0, 四舍五入
        quota=round(remaining*len(sec_codes)/total)
        quota=min(quota,len(sec_codes),remaining)
        if quota>0:
            chosen=random.sample(sec_codes,min(quota,len(sec_codes)))
            picks.extend(chosen)
            remaining-=len(chosen)
            pool=[c for c in pool if c not in chosen]
    # 不足30只, 从剩余补
    if remaining>0 and pool:
        picks.extend(random.sample(pool,min(remaining,len(pool))))
    return picks[:n_target]

# 生成10组
print('\n生成10组分层随机样本(每组30只):')
groups=[]
for i in range(10):
    g=stratified_sample(30)
    groups.append(g)
    secs=Counter(code_sector[c] for c in g)
    print('  组%02d: %s' % (i+1, dict(secs)))

# ===== 引擎 =====
INDEX_CODES={'510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF','513100':'纳指ETF','511260':'十年国债',
    '512890':'红利低波','159992':'创新药ETF'}
# 指数斜率(全局)
index_slope={}
for code in INDEX_CODES:
    if code not in all_data:continue
    cl=[all_data[code][bd]['close'] if all_data[code][bd]['valid'] else float('nan') for bd in base_dates]
    m60=ma(cl,60);sl=slp(m60,20)
    index_slope[code]={base_dates[i]:(not math.isnan(sl[i-1]) and sl[i-1]>0) if i>0 else False for i in range(len(base_dates))}

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
                    hi20=0;di=base_dates.index(d)
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
    return sh,tr,mdd

# 防御池(从89池选)
DEF=['518880','159937','518800','511010','511260','510880','512890','510050','159934','518850','511220','515080']

# ===== 每组全量扫参 =====
base_p={'f_ma':6,'s_ma':15,'sl_lb':4,'trail_bull':0.03,'trail_bear':0.06,'mh_bear':7,'pullback':0.05,'need_n':7}
print('\n'+'='*100)
print('  超级组合: 10组随机样本 × 全量扫参')
print('='*100)

all_optimals=[]  # 每组的最优参数
all_baselines=[]  # 每组的基准夏普
for gi, codes in enumerate(groups):
    # 确保防御池/检测池在组内
    def_codes=[c for c in DEF if c in codes]
    if len(def_codes)<5:  # 防御池不足, 补
        for c in DEF:
            if c not in codes and len(def_codes)<8:
                codes.append(c); def_codes.append(c)
    cur=dict(base_p)
    def sweep(name,key,vals,rb,sig):
        results=[]
        for v in vals:
            p=dict(cur);p[key]=v
            s=precompute(codes,p.get('f_ma',6),p.get('s_ma',15),p.get('sl_lb',4)) if rb else sig
            sh,tr,mdd=run(codes,def_codes,s,p)
            results.append((v,sh,tr))
        results.sort(key=lambda x:-x[1])
        cur[key]=results[0][0]
        return results[0]
    sig0=precompute(codes,6,15,4)
    base_sh,base_tr,base_mdd=run(codes,def_codes,sig0,base_p)
    for name,key,vals,rb in [
        ('f_ma','f_ma',[4,5,6,7,8],True),('s_ma','s_ma',[10,12,15,18,20],True),
        ('sl_lb','sl_lb',[3,4,5,6],True),('trail_bull','trail_bull',[0.02,0.025,0.03,0.035,0.04],False),
        ('trail_bear','trail_bear',[0.04,0.05,0.06,0.08,0.10],False),('mh_bear','mh_bear',[0,3,5,7,10],False),
        ('pullback','pullback',[0,0.03,0.05,0.08,0.10],False),('need_n','need_n',[5,6,7,8,9],False),
    ]:
        sweep(name,key,vals,rb,sig0)
        sig0=precompute(codes,cur['f_ma'],cur['s_ma'],cur['sl_lb'])
    sigf=precompute(codes,cur['f_ma'],cur['s_ma'],cur['sl_lb'])
    fsh,ftr,fmdd=run(codes,def_codes,sigf,cur)
    all_optimals.append(dict(cur)); all_baselines.append((base_sh,fsh))
    print('  组%02d 基准夏普%.2f→优化%.2f  收益%+.0f%%→%+.0f%%  参数:MA%d/%d Trail%.1f/%.0f MH%d pull%.2f n%d' % (
        gi+1,base_sh,fsh,base_tr*100,ftr*100,cur['f_ma'],cur['s_ma'],cur['trail_bull']*100,cur['trail_bear']*100,cur['mh_bear'],cur['pullback'],cur['need_n']))

# ===== 统计参数分布 =====
print('\n'+'='*100)
print('  10组最优参数分布统计')
print('='*100)
for key,label in [('f_ma','快线MA'),('s_ma','慢线MA'),('sl_lb','斜率回看'),
                  ('trail_bull','牛市Trail'),('trail_bear','熊市Trail'),
                  ('mh_bear','熊市MH'),('pullback','回撤过滤'),('need_n','DEF->ALL')]:
    vals=[opt[key] for opt in all_optimals]
    cnt=Counter(vals)
    most=cnt.most_common(1)[0]
    mean=sum(vals)/len(vals)
    print('  %-12s 分布: %s' % (label, dict(cnt)))
    print('  %-12s 众数=%s(出现%d次) 均值=%.2f' % ('',most[0],most[1],mean))

# 基准vs优化汇总
print('\n【基准vs优化 夏普汇总】')
bs=[b[0] for b in all_baselines]; os_=[b[1] for b in all_baselines]
print('  基准(原版参数): 均值%.2f 中位%.2f 范围%.2f~%.2f' % (sum(bs)/len(bs),sorted(bs)[5],min(bs),max(bs)))
print('  优化(各自最优): 均值%.2f 中位%.2f 范围%.2f~%.2f' % (sum(os_)/len(os_),sorted(os_)[5],min(os_),max(os_)))
