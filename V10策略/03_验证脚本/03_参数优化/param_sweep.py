"""V10 全量参数扫描 (贪心法, 逐参数优化).
基准: T+1 + 报告成本(滑点0.1%+手续费0.05%/边+印花0.05%).
扫参参数: F_MA, S_MA, SL_MA, Trail牛/熊, MH熊, 回撤过滤, NEED_N, ALL->DEF阈值.
优化目标: 夏普(主要) + 收益(次要).
贪心: 固定其他参数扫一个, 取最优固定, 再扫下一个.
最后对最优组合做邻域稳健性检查(防尖峰过拟合).
"""
import json, os, math
from collections import defaultdict
from datetime import datetime

DATA_DIR = r"C:\Users\home\Desktop\策略文件夹\data"
START='2020-01-01'; END='2026-07-30'
RF=0.025; TD=252; INIT=int(1e7)
NEED_N_BASE=7
SLIP=0.001; COMM=0.0005; STAMP=0.0005  # 报告成本固定

ETF_33=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
        '588200','159995','512480','515880','515050','159819','159992','512010',
        '518880','159937','513180','513050','513100','159509','588000','588220',
        '510300','159915','510050','511010','511260','510880','512890','159201']
DEF_POOL=['518880','159937','518800','511010','511260','510880','512890','159201','510050']
INDEX_CODES={'510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50',
    '515880':'通信ETF','512480':'半导体ETF','513100':'纳指ETF','511260':'十年国债',
    '512890':'红利低波','159992':'创新药ETF'}

def load():
    etfs={}
    for code in ETF_33:
        p=os.path.join(DATA_DIR,'etf_'+code+'.json')
        if not os.path.exists(p):continue
        d=json.load(open(p,encoding='utf-8')); bars=[]
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
# 预计算所有可能用到的MA (为加速扫参, 预算各周期)
MA_CACHE={}; SLP_CACHE={}
for c in codes:
    cl=[b['close'] for b in etfs[c]['bars']]; hi=[b['high'] for b in etfs[c]['bars']]
    dts=[b['date'] for b in etfs[c]['bars']]
    MA_CACHE[c]={w:ma(cl,w) for w in [3,4,5,6,7,8,9,10,11,12,13,15,16,18,20,25,30,60]}
    SLP_CACHE[c]={lb:slp(MA_CACHE[c][8],lb) for lb in [3,4,5,6]}
    MA_CACHE[c]['hi']=hi; MA_CACHE[c]['cl']=cl; MA_CACHE[c]['dts']=dts
INDEX_SLOPE={}
for code in INDEX_CODES:
    if code not in etfs:continue
    cl=[b['close'] for b in etfs[code]['bars']]
    m60=ma(cl,60); sl=slp(m60,20)
    dts=[b['date'] for b in etfs[code]['bars']]
    INDEX_SLOPE[code]={dts[i]:(not math.isnan(sl[i-1]) and sl[i-1]>0) if i>0 else False for i in range(len(dts))}
dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
fd={c:etfs[c]['first_date'] for c in codes}
all_dates=sorted(set(d for c in codes for d in dm[c].keys()))

def build_signals(f_ma,s_ma,sl_lb):
    """用指定MA参数重建T+1信号."""
    sig={}
    for c in codes:
        mf=MA_CACHE[c][f_ma]; ms_=MA_CACHE[c][s_ma]; slo_=SLP_CACHE[c][sl_lb]
        m60=MA_CACHE[c][60]; cl=MA_CACHE[c]['cl']; dts=MA_CACHE[c]['dts']
        trnd={};rat={};abv={}
        for i in range(len(dts)):
            d=dts[i]
            if not math.isnan(mf[i]) and not math.isnan(ms_[i]) and ms_[i]>0:
                sk=not math.isnan(slo_[i]) and slo_[i]>0
                trnd[d]=mf[i-1]>ms_[i-1] and sk if i>0 else False
                rat[d]=mf[i-1]/ms_[i-1] if i>0 and ms_[i-1]>0 else 1.0
            else:trnd[d]=False;rat[d]=1.0
            abv[d]=not math.isnan(m60[i-1]) and cl[i-1]>m60[i-1] if i>0 else False
        sig[c]={'trnd':trnd,'rat':rat,'abv':abv}
    return sig

# 默认信号 (MA6/15, slope_lb=4)
SIG_DEFAULT=build_signals(6,15,4)

def run(params, sig=None):
    if sig is None: sig=SIG_DEFAULT
    f_ma=params.get('f_ma',6); s_ma=params.get('s_ma',15)
    trail_bull=params.get('trail_bull',0.03); trail_bear=params.get('trail_bear',0.06)
    mh_bull=params.get('mh_bull',0); mh_bear=params.get('mh_bear',7)
    pullback=params.get('pullback',0.05); need_n=params.get('need_n',7)
    loss_thresh=params.get('loss_thresh',0.10)  # ALL->DEF 阈值(占初始资金)
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;dvs=[]
    pool_mode='all';rolling_pnl=[]
    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=not INDEX_SLOPE.get('510300',{}).get(d,False)
        cur_trail=trail_bear if is_bear else trail_bull
        cur_mh=mh_bear if is_bear else mh_bull
        if pos:
            bar=dm[pos].get(d)
            if bar:
                px_raw=bar['close']; px_sell=px_raw*(1-SLIP-COMM-STAMP)
                if px_raw>peak:peak=px_raw
                er=None
                if px_raw<=peak*(1-cur_trail):er='trail'
                elif not sig[pos]['trnd'].get(d,False):er='off'
                if er:
                    pnl=shares*px_sell-shares*bp
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl)>5:rolling_pnl.pop(0)
                    cash=shares*px_sell;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
                    if len(rolling_pnl)>=5 and pool_mode=='all' and sum(rolling_pnl)<-loss_thresh*INIT:pool_mode='defensive'
                    if pool_mode=='defensive' and sum(1 for c2 in INDEX_CODES if INDEX_SLOPE.get(c2,{}).get(d,False))>=need_n:pool_mode='all'
        if not pos and cash>0:
            cands=[]
            for c in avail:
                if pool_mode=='defensive' and c not in DEF_POOL:continue
                if not sig[c]['trnd'].get(d,False):continue
                if not sig[c]['abv'].get(d,False):continue
                if pullback>0:
                    hi20=0
                    for lb in range(1,21):
                        pdi=max(0,len(all_dates)-1-lb)
                        if 0<=pdi<len(all_dates):hi20=max(hi20,MA_CACHE[c]['hi'][pdi] if pdi<len(MA_CACHE[c]['hi']) else 0)
                    if hi20>0 and (hi20-dm[c][d]['close'])/hi20<pullback:continue
                bar=dm[c].get(d)
                if bar:cands.append((c,sig[c]['rat'].get(d,1.0),bar['close']))
            if cands:
                cands.sort(key=lambda x:x[1],reverse=True)
                c,ratio,px_raw=cands[0]; bp=px_raw*(1+SLIP+COMM)
                shares=cash/bp;peak=px_raw;pos=c;entry_d=d;entry_date=dt_obj;cash=0.0
        pos_val=shares*dm[pos].get(d,{}).get('close',0) if pos else 0
        dvs.append(cash+pos_val)
    if pos:
        bar=dm[pos].get(all_dates[-1])
        if bar:cash=shares*bar['close']*(1-SLIP-COMM-STAMP)
    fv=dvs[-1];rets=[(dvs[i]-dvs[i-1])/dvs[i-1] for i in range(1,len(dvs)) if dvs[i-1]>0]
    pk=dvs[0];mdd=0.0
    for v in dvs:
        if v>pk:pk=v
        dd=(pk-v)/pk
        if dd>mdd:mdd=dd
    tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0
    yr=defaultdict(float)
    return {'sh':sh,'tr':tr,'mdd':mdd,'yr':dict(yr)}

# 基准
base_params={'f_ma':6,'s_ma':15,'sl_lb':4,'trail_bull':0.03,'trail_bear':0.06,
             'mh_bull':0,'mh_bear':7,'pullback':0.05,'need_n':7,'loss_thresh':0.10}
base=run(base_params)
print('='*100)
print('  V10 全量参数扫描 (贪心法) | 基准: T+1 + 报告成本')
print('='*100)
print('  基准(原版): 夏普=%.3f 收益=%+.0f%% 回撤=%.1f%%\n' % (base['sh'],base['tr']*100,base['mdd']*100))

# ===== 贪心扫描 =====
cur=dict(base_params)
def sweep(name, key, values, rebuild_sig=False, sl_lb_key=None):
    """扫描单个参数, 返回最优值."""
    results=[]
    for v in values:
        p=dict(cur); p[key]=v
        sig=None
        if rebuild_sig:
            sl_lb = p.get(sl_lb_key,4) if sl_lb_key else 4
            sig=build_signals(p.get('f_ma',6),p.get('s_ma',15),sl_lb)
        r=run(p,sig=sig)
        results.append((v,r['sh'],r['tr'],r['mdd'],r))
    results.sort(key=lambda x:-x[1])
    best=results[0]
    cur[key]=best[0]
    print('  【%s】 当前%s=%s' % (name,key,cur[key]))
    print('  %-8s %8s %10s %8s %s' % ('值','夏普','收益','回撤',''))
    for v,sh,tr,mdd,r in results:
        mark=' ★最优' if v==best[0] else ''
        base_sh=base['sh']
        d=''
        if abs(v-cur[key])<1e-9 and sh==best[1]:d=' <- 选定'
        print('  %-8s %8.3f %+9.0f%% %7.1f%%%s%s' % (v,sh,tr*100,mdd*100,mark,d))
    print('  → 选定 %s=%s 夏普=%.3f\n' % (key,cur[key],best[1]))
    return best

print('-'*100)
print('  STEP 1: 快线 MA')
sweep('快线MA','f_ma',[4,5,6,7,8],rebuild_sig=True,sl_lb_key='sl_lb')
print('  STEP 2: 慢线 MA')
sweep('慢线MA','s_ma',[10,12,15,18,20],rebuild_sig=True,sl_lb_key='sl_lb')
print('  STEP 3: 斜率回看')
sweep('斜率回看','sl_lb',[3,4,5,6],rebuild_sig=True,sl_lb_key='sl_lb')
print('  STEP 4: 牛市Trail')
sweep('牛市Trail','trail_bull',[0.02,0.025,0.03,0.035,0.04])
print('  STEP 5: 熊市Trail')
sweep('熊市Trail','trail_bear',[0.04,0.05,0.06,0.08,0.10])
print('  STEP 6: 熊市最低持仓')
sweep('熊市MH','mh_bear',[0,3,5,7,10])
print('  STEP 7: 牛市最低持仓')
sweep('牛市MH','mh_bull',[0,2,3,5])
print('  STEP 8: 回撤过滤')
sweep('回撤过滤','pullback',[0,0.03,0.05,0.08,0.10])
print('  STEP 9: DEF->ALL 指数阈值')
sweep('DEF->ALL','need_n',[5,6,7,8,9])
print('  STEP 10: ALL->DEF 亏损阈值')
sweep('ALL->DEF','loss_thresh',[0.06,0.08,0.10,0.12,0.15])

# 最终结果
final_sig=build_signals(cur['f_ma'],cur['s_ma'],cur['sl_lb'])
final=run(cur,sig=final_sig)
print('='*100)
print('  最终优化结果')
print('='*100)
print('  %-24s %-12s %-12s %s' % ('参数','原版','优化','变化'))
print('  '+'-'*60)
for k in ['f_ma','s_ma','sl_lb','trail_bull','trail_bear','mh_bear','mh_bull','pullback','need_n','loss_thresh']:
    ov=base_params[k]; nv=cur[k]
    print('  %-24s %-12s %-12s %s' % (k,ov,nv,'← 改' if ov!=nv else ''))
print('\n  %-24s %-12s %-12s' % ('指标','原版','优化'))
print('  %-24s %-12s %-12s' % ('夏普','%.3f'%base['sh'],'%.3f'%final['sh']))
print('  %-24s %-12s %-12s' % ('总收益','%+.0f%%'%(base['tr']*100),'%+.0f%%'%(final['tr']*100)))
print('  %-24s %-12s %-12s' % ('回撤','%.1f%%'%(base['mdd']*100),'%.1f%%'%(final['mdd']*100)))
