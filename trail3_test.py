"""Trail=3% Full Test · MA6/15 s8 · ETF>MA60 filter"""
import json,os,math
from collections import defaultdict
from datetime import datetime
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=int(1e7);TRAIL=0.03;F_MA=6;S_MA=15;SL_MA=8

ETF_CODES=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
           '588200','159995','512480','515880','515050','159819','159992','512010',
           '518880','159937','513180','513050','513100','159509','588000','588220',
           '510300','159915','510050','511010','511260','510880','512890','159301']

def load():
    etfs={}
    for code in ETF_CODES:
        path=os.path.join(DATA_DIR,'etf_'+code+'.json')
        if not os.path.exists(path):continue
        d=json.load(open(path,encoding='utf-8'))
        bars=[]
        for b in d['bars']:
            dt=b['date'];px=float(b['close'])
            if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            if START<=dt<=END:bars.append({'date':dt,'close':px})
        if bars:etfs[code]={'name':d['name'],'first_date':bars[0]['date'],'bars':bars}
    return etfs

def ma(data,w):
    m=[];n=len(data)
    for i in range(n):
        if i<w-1:m.append(float('nan'))
        else:m.append(sum(data[i-w+1:i+1])/w)
    return m
def slp(ms,lb):
    s=[float('nan')]*len(ms)
    for i in range(len(ms)):
        if i<lb:continue
        ys=ms[i-lb+1:i+1]
        if any(math.isnan(y) for y in ys):continue
        n=len(ys);sx=sy=sxy=sxx=0
        for j,y in enumerate(ys):sx+=j;sy+=y;sxy+=j*y;sxx+=j*j
        d=n*sxx-sx*sx
        if d>0:s[i]=(n*sxy-sx*sy)/d/ms[i] if ms[i]>0 else 0
    return s

etfs=load();codes=sorted(etfs.keys())
# Precompute
all_trnd={};all_ratio={};above_ma60={}
for c in codes:
    bars=etfs[c]['bars'];cl=[b['close'] for b in bars];n=len(bars)
    mf=ma(cl,F_MA);ms=ma(cl,S_MA);msl=ma(cl,SL_MA);slo_=slp(msl,max(SL_MA//2,3))
    m60=ma(cl,60);dts=[b['date'] for b in bars]
    trnd={};rat={};abv={}
    for i in range(n):
        d=dts[i]
        if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
            sk=not math.isnan(slo_[i]) and slo_[i]>0
            trnd[d]=mf[i]>ms[i] and sk;rat[d]=mf[i]/ms[i]
        else:trnd[d]=False;rat[d]=1.0
        abv[d]=not math.isnan(m60[i]) and cl[i]>m60[i]
    all_trnd[c]=trnd;all_ratio[c]=rat;above_ma60[c]=abv

dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
fd={c:etfs[c]['first_date'] for c in codes}
ad=set()
for c in codes:
    for k in dm[c]:ad.add(k)
all_dates=sorted(ad)

# Full backtest
cash=INIT;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]

for d in all_dates:
    avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
    if pos_code:
        bar=dm[pos_code].get(d)
        if bar:
            px=bar['close']
            if px>peak:peak=px
            ton=all_trnd[pos_code].get(d,False);er=None
            if px<=peak*(1-TRAIL):er='trail'
            elif not ton:er='off'
            if er:
                pnl=shares*px-shares*bp
                trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':er,'c':pos_code,'b':entry_d,'s':d,
                               'days':(dt_obj-entry_date).days if entry_date else 0})
                cash=shares*px;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
    if not pos_code and cash>0:
        cands=[]
        for c in avail:
            ton=all_trnd[c].get(d,False)
            if not ton:continue
            if not above_ma60.get(c,{}).get(d,False):continue
            bar=dm[c].get(d)
            if bar:cands.append((c,all_ratio[c].get(d,1.0),bar['close']))
        if cands:
            cands.sort(key=lambda x:x[1],reverse=True)
            c,ratio,px=cands[0]
            shares=cash/px;bp=px;peak=px;pos_code=c;entry_d=d;entry_date=dt_obj;cash=0.0
    pos_val=shares*dm[pos_code].get(d,{}).get('close',0) if pos_code else 0
    dvs.append(cash+pos_val)

if pos_code:
    bar=dm[pos_code].get(all_dates[-1])
    if bar:
        px=bar['close'];pnl=shares*px-shares*bp
        dt_last=datetime.strptime(all_dates[-1],'%Y-%m-%d')
        trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':'final','c':pos_code,'b':entry_d,'s':all_dates[-1],
                       'days':(dt_last-entry_date).days if entry_date else 0})
        cash=shares*px

fv=cash;rets=[]
for i in range(1,len(dvs)):
    if dvs[i-1]>0:rets.append((dvs[i]-dvs[i-1])/dvs[i-1])
if not rets:rets=[0.0]

# Metrics
pk=dvs[0];mdd=0.0;dd_trough_idx=0
for i,(v,d) in enumerate(zip(dvs,all_dates)):
    if v>pk:pk=v
    dd=(pk-v)/pk
    if dd>mdd:mdd=dd;dd_trough_idx=i
dd_peak_idx=0
for i in range(dd_trough_idx,-1,-1):
    if dvs[i]==max(dvs[:dd_trough_idx+1]):dd_peak_idx=i;break
dd_start=all_dates[dd_peak_idx];dd_end=all_dates[dd_trough_idx]

tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5 if len(rets)>1 else 0.01
av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0;ar=(1+tr)**(TD/len(rets))-1 if tr>-1 else -1
cm=ar/mdd if mdd>0 else 0

st=[t for t in trades if t['e'] in('trail','off','final')]
w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0

print('='*100)
print('  TRAIL=3%% · FULL STRATEGY REPORT')
print('  MA%d/%d s%d · ETF>MA60 filter · Single-position rotation'%(F_MA,S_MA,SL_MA))
print('='*100)
print()
print('  PERFORMANCE:')
print('  Sharpe:                %.4f'%sh)
print('  Total Return:          %.2f%%'%(tr*100))
print('  Annual Return:         %.2f%%'%(ar*100))
print('  Annual Volatility:     %.2f%%'%(av*100))
print('  Max Drawdown:          %.2f%%'%(mdd*100))
print('  Calmar Ratio:          %.4f'%cm)
print('  Total Trades:          %d'%len(st))
print('  Win Rate:              %.1f%% (%d/%d)'%(wr*100,w,len(st)))
print()

# Annual breakdown
print('  ANNUAL BREAKDOWN:')
yr_pnl=defaultdict(float);yr_trd=defaultdict(int);yr_wr=defaultdict(list)
for t in trades:
    if t['e'] in('trail','off','final'):
        yr=t.get('b','')[:4] if t.get('b') else '?'
        yr_pnl[yr]+=t['pnl'];yr_trd[yr]+=1;yr_wr[yr].append(t['r']>0)
print('  %-6s %9s %7s %5s %5s'%('Year','Ret%','DD%','Trd','Win%'))
for y in sorted(yr_pnl):
    wrs=sum(yr_wr[y])/len(yr_wr[y])*100 if yr_wr[y] else 0
    print('  %-6s %+8.1f%% %7s %5d %4.0f%%'%(y,yr_pnl[y]/INIT*100,'',yr_trd[y],wrs))

# Holding days stats
win_days=[t['days'] for t in st if t['r']>0];loss_days=[t['days'] for t in st if t['r']<=0]
print()
print('  HOLDING DAYS:')
print('  Wins: avg=%.0fd median=%dd  Losses: avg=%.0fd median=%dd'%(
    sum(win_days)/len(win_days) if win_days else 0, sorted(win_days)[len(win_days)//2] if win_days else 0,
    sum(loss_days)/len(loss_days) if loss_days else 0, sorted(loss_days)[len(loss_days)//2] if loss_days else 0))

# Win/loss ratio
losses=[t for t in st if t['r']<=0];wins=[t for t in st if t['r']>0]
al=sum(t['r'] for t in losses)/len(losses) if losses else 0
aw=sum(t['r'] for t in wins)/len(wins) if wins else 0
print()
print('  WIN/LOSS PROFILE:')
print('  Wins: %d trades avg=%.2f%%  Losses: %d trades avg=%.2f%%  Ratio=%.2f'%(
    len(wins),aw*100,len(losses),al*100,abs(aw/al) if al!=0 else 99))

# Top/bottom ETFs
etf_pnl=defaultdict(float);etf_trd=defaultdict(int);etf_ret=defaultdict(list)
for t in st:etf_pnl[t['c']]+=t['pnl'];etf_trd[t['c']]+=1;etf_ret[t['c']].append(t['r'])
top10=sorted(etf_pnl.items(),key=lambda x:x[1],reverse=True)[:10]
print()
print('  TOP 10 ETFs:')
for c,pnl in top10:
    name=etfs[c]['name'];wr_=sum(1 for r in etf_ret[c] if r>0)/len(etf_ret[c])*100
    print('  %s %s: PnL=%+.0f Trd=%d Win=%.0f%%'%(c,name,pnl,etf_trd[c],wr_))
bottom5=sorted(etf_pnl.items(),key=lambda x:x[1])[:5]
print()
print('  BOTTOM 5 ETFs:')
for c,pnl in bottom5:
    name=etfs[c]['name']
    print('  %s %s: PnL=%+.0f Trd=%d'%(c,name,pnl,etf_trd[c]))

# DEEPEST DD detail
print()
print('  DEEPEST DD: %.1f%%  %s->%s (%dd)'%(
    mdd*100,dd_start,dd_end,(datetime.strptime(dd_end,'%Y-%m-%d')-datetime.strptime(dd_start,'%Y-%m-%d')).days))
st_dd=[t for t in st if dd_start<=t.get('b','')<=dd_end or dd_start<=t.get('s','')<=dd_end]
dd_losses=[t for t in st_dd if t['r']<0];dd_wins=[t for t in st_dd if t['r']>0]
tl=sum(t['pnl'] for t in dd_losses);tw=sum(t['pnl'] for t in dd_wins)
pp_=dvs[dd_peak_idx];tt=dvs[dd_trough_idx]
trade_loss=tl+tw;float_loss=(tt-pp_)-trade_loss
print('  %d trades (%dL/%dW) TradePnL=%+.0f FloatPnL=%+.0f'%(
    len(st_dd),len(dd_losses),len(dd_wins),trade_loss,float_loss))
for t in sorted(dd_losses,key=lambda x:x['r']):
    name=etfs[t['c']]['name']
    print('    L %s %s %+.2f%% %s'%(t['c'],name,t['r']*100,t['e']))

# Formula
print('\n\n  '+('='*80))
print('  FORMULA')
print('  '+('='*80))
print('''
  Trail=3%% · ETF趋势轮动 · 最终版

  买入: MA6 > MA15 AND MA8 4日斜率 > 0 AND ETF价格 > MA60
  卖出: Trail 3%% 或 趋势转空
  选股: MA6/MA15 比值最高 1 只, 满仓轮动

  Sharpe %.3f | Ret %.1f%% | DD %.1f%% | %d trades | Win %.0f%%
'''%(sh,tr*100,mdd*100,len(st),wr*100))

print('  Done!')
