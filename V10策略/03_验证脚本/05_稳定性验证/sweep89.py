"""89池(原33+新56) 全量扫参, 用原版检测池(含510300)."""
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
def load_pool(codes):
    etfs={}
    for code in codes:
        for dd in ['data_new','data']:
            p=os.path.join(dd,'etf_'+code+'.json')
            if os.path.exists(p):
                d=json.load(open(p,encoding='utf-8'))
                bars=[]
                for b in d['bars']:
                    dt=b['date'];px=float(b['close'])
                    if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
                    bars.append({'date':dt,'close':px,'high':float(b.get('high',px)) if px>0 else 0.0,'valid':px>0})
                if bars:etfs[code]={'name':d['name'],'bars':bars};break
    return etfs

ETF_33=['159782','588380','588870','588080','588300','518800','589720','588890','588170','588200','159995','512480','515880','515050','159819','159992','512010','518880','159937','513180','513050','513100','159509','588000','588220','510300','159915','510050','511010','511260','510880','512890','159301']
NEW_56=sorted([f.replace('etf_','').replace('.json','') for f in os.listdir('data_new') if f.startswith('etf_')])
ETF_89=sorted(set(ETF_33+NEW_56))
DEF_89=['518880','159937','518800','511010','511260','510880','512890','510050','159934','518850','159981','518860','511220','159972','511030','515080','515450']
INDEX_89={'510300':'HS300','159915':'创业板','588000':'科创50','510050':'上证50','515880':'通信ETF','512480':'半导体ETF','513100':'纳指ETF','511260':'十年国债','512890':'红利低波','159992':'创新药ETF'}
BEAR_CODE='510300'

def precompute(etfs, f_ma, s_ma, sl_lb):
    all_trnd={};all_ratio={};above_ma60={};etf_highs={};index_slope={}
    for c in etfs:
        bars=etfs[c]['bars']
        cl=[b['close'] if b['valid'] else float('nan') for b in bars]
        hi=[b['high'] if b['valid'] else 0.0 for b in bars]
        dts=[b['date'] for b in bars]
        mf=ma(cl,f_ma);ms_=ma(cl,s_ma);msl=ma(cl,SL_MA);slo_=slp(msl,sl_lb)
        m60=ma(cl,60)
        trnd={};rat={};abv={}
        for i in range(len(bars)):
            d=dts[i];valid=bars[i]['valid']
            if valid and not math.isnan(mf[i]) and not math.isnan(ms_[i]) and ms_[i]>0:
                sk=not math.isnan(slo_[i]) and slo_[i]>0
                trnd[d]=mf[i-1]>ms_[i-1] and sk if i>0 else False
                rat[d]=mf[i-1]/ms_[i-1] if i>0 and ms_[i-1]>0 else 1.0
            else:trnd[d]=False;rat[d]=1.0
            abv[d]=valid and (not math.isnan(m60[i-1]) and cl[i-1]>m60[i-1]) if i>0 else False
        all_trnd[c]=trnd;all_ratio[c]=rat;above_ma60[c]=abv;etf_highs[c]={b['date']:b['high'] for b in bars}
    for code in INDEX_89:
        if code not in etfs:continue
        bars=etfs[code]['bars']
        cl=[b['close'] if b['valid'] else float('nan') for b in bars]
        dts=[b['date'] for b in bars]
        m60=ma(cl,60);sl=slp(m60,20)
        index_slope[code]={dts[i]:(not math.isnan(sl[i-1]) and sl[i-1]>0) if i>0 else False for i in range(len(dts))}
    return all_trnd,all_ratio,above_ma60,etf_highs,index_slope

def run(etfs, sig, def_codes, p):
    all_trnd,all_ratio,above_ma60,etf_highs,index_slope=sig
    codes=sorted(etfs.keys())
    dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    fd={c:next((b['date'] for b in etfs[c]['bars'] if b['valid']),'9999-12-31') for c in codes}
    all_dates=sorted(set(d for c in codes for d in dm[c].keys()))
    trail_bull=p.get('trail_bull',0.03);trail_bear=p.get('trail_bear',0.06)
    mh_bear=p.get('mh_bear',7);pullback=p.get('pullback',0.05);need_n=p.get('need_n',7)
    cash=INIT;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;dvs=[]
    pool_mode='all';rolling_pnl=[]
    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        is_bear=not index_slope.get(BEAR_CODE,{}).get(d,False)
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
                    pnl=shares*px_sell-shares*bp
                    rolling_pnl.append(pnl)
                    if len(rolling_pnl)>5:rolling_pnl.pop(0)
                    cash=shares*px_sell;pos=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
                    if len(rolling_pnl)>=5 and pool_mode=='all' and sum(rolling_pnl)<-0.10*INIT:pool_mode='defensive'
                    if pool_mode=='defensive' and sum(1 for c2 in INDEX_89 if index_slope.get(c2,{}).get(d,False))>=need_n:pool_mode='all'
        if not pos and cash>0:
            cands=[]
            for c in avail:
                if pool_mode=='defensive' and c not in def_codes:continue
                if not all_trnd.get(c,{}).get(d,False):continue
                if not above_ma60.get(c,{}).get(d,False):continue
                if pullback>0:
                    hi20=0
                    for lb in range(1,21):
                        pdi=max(0,len(all_dates)-1-lb)
                        if 0<=pdi<len(all_dates):hi20=max(hi20,etf_highs[c].get(all_dates[pdi],0))
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
        bar=dm[pos].get(all_dates[-1])
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
    return {'sh':sh,'tr':tr,'mdd':mdd}

etfs=load_pool(ETF_89)
base_p={'f_ma':6,'s_ma':15,'sl_lb':4,'trail_bull':0.03,'trail_bear':0.06,'mh_bear':7,'pullback':0.05,'need_n':7}
cur=dict(base_p)
SIG_DEFAULT=precompute(etfs,6,15,4)
base=run(etfs,SIG_DEFAULT,DEF_89,base_p)
print('='*100)
print('  89池(原33+新56) 全量扫参 | 真实ETF费率')
print('='*100)
print('  基准(原版参数): 夏普=%.3f 收益=%+.0f%% 回撤=%.1f%%\n' % (base['sh'],base['tr']*100,base['mdd']*100))
print('  %-16s %10s %10s %10s' % ('参数','最优值','夏普','收益'))
print('  '+'-'*55)

def sweep(name, key, vals, rb):
    global SIG_DEFAULT
    results=[]
    for v in vals:
        p=dict(cur);p[key]=v
        sig=precompute(etfs,p.get('f_ma',6),p.get('s_ma',15),p.get('sl_lb',4)) if rb else SIG_DEFAULT
        r=run(etfs,sig,DEF_89,p)
        results.append((v,r['sh'],r['tr'],r['mdd']))
    results.sort(key=lambda x:-x[1])
    cur[key]=results[0][0]
    if rb: SIG_DEFAULT=precompute(etfs,cur['f_ma'],cur['s_ma'],cur['sl_lb'])
    return results[0]

for name,key,vals,rb in [
    ('快线MA','f_ma',[4,5,6,7,8],True),('慢线MA','s_ma',[10,12,15,18,20],True),
    ('斜率回看','sl_lb',[3,4,5,6],True),('牛市Trail','trail_bull',[0.02,0.025,0.03,0.035,0.04],False),
    ('熊市Trail','trail_bear',[0.04,0.05,0.06,0.08,0.10],False),('熊市MH','mh_bear',[0,3,5,7,10],False),
    ('回撤过滤','pullback',[0,0.03,0.05,0.08,0.10],False),('DEF->ALL','need_n',[5,6,7,8,9],False),
]:
    best=sweep(name,key,vals,rb)
    mark=' ★改进' if best[1]>base['sh']+0.01 else (' =基准' if abs(best[1]-base['sh'])<0.01 else ' 下降')
    print('  %-16s %10s %10.3f %+9.0f%%%s' % (name,cur[key],best[1],best[2]*100,mark))

final_sig=precompute(etfs,cur['f_ma'],cur['s_ma'],cur['sl_lb'])
final=run(etfs,final_sig,DEF_89,cur)
print('\n  最终优化: 夏普=%.3f→%.3f 收益=%+.0f%%→%+.0f%% 回撤=%.1f%%→%.1f%%' % (
    base['sh'],final['sh'],base['tr']*100,final['tr']*100,base['mdd']*100,final['mdd']*100))
changed=[k for k in base_p if base_p[k]!=cur[k]]
if changed:
    print('  调整参数: %s' % ', '.join('%s:%s→%s'%(k,base_p[k],cur[k]) for k in changed))
else:
    print('  ★ 原版参数已是最优')
