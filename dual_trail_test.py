"""Dual-Trail: Bull=3% Bear=5%+MH=7 · ETF>MA60 filter · Single rotation"""
import json,os,math
from collections import defaultdict
from datetime import datetime
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=int(1e7);F_MA=6;S_MA=15;SL_MA=8
TRAIL_BULL=0.03;TRAIL_BEAR=0.05;BEAR_MH=7;BULL_MH=0

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

# Bear regime
bear_slope={}
for c in codes:
    if c=='510300':
        cl=[b['close'] for b in etfs[c]['bars']]
        m60=ma(cl,60);sl=slp(m60,20);dts=[b['date'] for b in etfs[c]['bars']]
        for i in range(len(dts)):bear_slope[dts[i]]=not math.isnan(sl[i]) and sl[i]<0
        break

dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
fd={c:etfs[c]['first_date'] for c in codes}
ad=set()
for c in codes:
    for k in dm[c]:ad.add(k)
all_dates=sorted(ad)

# Run backtest
cash=INIT;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]

for d in all_dates:
    avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
    is_bear=bear_slope.get(d,False)
    cur_trail=TRAIL_BEAR if is_bear else TRAIL_BULL
    cur_mh=BEAR_MH if is_bear else BULL_MH

    if pos_code:
        bar=dm[pos_code].get(d)
        if bar:
            px=bar['close']
            if px>peak:peak=px
            ton=all_trnd[pos_code].get(d,False);er=None
            if px<=peak*(1-cur_trail):er='trail'
            elif not ton:
                if cur_mh>0 and entry_date:
                    if (dt_obj-entry_date).days>=cur_mh:er='off'
                else:er='off'
            if er:
                pnl=shares*px-shares*bp
                trades.append({
                    'pnl':pnl,'r':(px-bp)/bp,'e':er,'c':pos_code,'b':entry_d,'s':d,
                    'days':(dt_obj-entry_date).days if entry_date else 0,
                    'is_bear_entry':bear_slope.get(entry_d,False)
                })
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
                       'days':(dt_last-entry_date).days if entry_date else 0,
                       'is_bear_entry':bear_slope.get(entry_d,False)})
        cash=shares*px

fv=cash;rets=[]
for i in range(1,len(dvs)):
    if dvs[i-1]>0:rets.append((dvs[i]-dvs[i-1])/dvs[i-1])
if not rets:rets=[0.0]

pk=dvs[0];mdd=0.0
for v in dvs:
    if v>pk:pk=v
    dd=(pk-v)/pk
    if dd>mdd:mdd=dd
tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5 if len(rets)>1 else 0.01
av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0;ar=(1+tr)**(TD/len(rets))-1 if tr>-1 else -1
cm=ar/mdd if mdd>0 else 0
st=[t for t in trades if t['e'] in('trail','off','final')]
w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0

# Annual
yr_pnl=defaultdict(float);yr_trd=defaultdict(int);yr_wr=defaultdict(list)
for t in trades:
    if t['e'] in('trail','off','final'):
        yr=t.get('b','')[:4] if t.get('b') else '?'
        yr_pnl[yr]+=t['pnl'];yr_trd[yr]+=1;yr_wr[yr].append(t['r']>0)

# Bear vs bull stats
bear_trades=[t for t in st if t.get('is_bear_entry',False)]
bull_trades=[t for t in st if not t.get('is_bear_entry',False)]

print('='*100)
print('  DUAL-TRAIL: Bull=3%% Bear=5%%+MH=7d  ETF>MA60  Single Rotation')
print('='*100)
print()
print('  PERFORMANCE:')
print('  Sharpe: %.3f  |  Ret: %.1f%%  |  Ann: %.1f%%  |  DD: %.1f%%  |  Calmar: %.3f'%(sh,tr*100,ar*100,mdd*100,cm))
print('  Trades: %d  |  Win: %.0f%% (%d/%d)'%(len(st),wr*100,w,len(st)))
print()

print('  ANNUAL:')
print('  %-6s %9s %7s %5s %5s'%('Year','Ret','DD','Trd','Win'))
for y in sorted(yr_pnl):
    wrs=sum(yr_wr[y])/len(yr_wr[y])*100 if yr_wr[y] else 0
    print('  %-6s %+8.1f%% %7s %5d %4.0f%%'%(y,yr_pnl[y]/INIT*100,'',yr_trd[y],wrs))

print()
print('  BEAR vs BULL TRADE STATS:')
for label,ts in[('Bear (5%+MH7)',bear_trades),('Bull (3%)',bull_trades)]:
    if ts:
        n=len(ts);l=[t for t in ts if t['r']<0];w_=[t for t in ts if t['r']>0]
        avg_d=sum(t['days'] for t in ts)/n if ts else 0
        tp=sum(t['pnl'] for t in l);tw=sum(t['pnl'] for t in w_)
        print('  %s: %d trades(%dL/%dW=%.0f%%) avg=%.1fd Net=%.0f Loss=%.0f Win=%.0f'%(
            label,n,len(l),len(w_),len(w_)/n*100,avg_d,tp+tw,tp,tw))

print()
print('  HOLDING DAYS:')
wl=[t for t in st if t['r']>0];ll=[t for t in st if t['r']<=0]
print('  Wins: avg=%.0fd median=%dd  Losses: avg=%.0fd median=%dd'%(
    sum(t['days'] for t in wl)/len(wl) if wl else 0,sorted([t['days'] for t in wl])[len(wl)//2] if wl else 0,
    sum(t['days'] for t in ll)/len(ll) if ll else 0,sorted([t['days'] for t in ll])[len(ll)//2] if ll else 0))

print()
print('  WIN/LOSS RATIO:')
aw=sum(t['r'] for t in wl)/len(wl) if wl else 0;al_=sum(t['r'] for t in ll)/len(ll) if ll else 0
print('  Wins: avg=%.2f%%  Losses: avg=%.2f%%  Ratio=%.2f'%(aw*100,al_*100,abs(aw/al_) if al_!=0 else 99))

# COMPARISON TABLE
print()
print('  '+'='*80)
print('  VERSION COMPARISON (annual returns)')
print('  '+'='*80)
print('  %-25s %6s %6s %6s %6s %6s %6s %6s %6s %6s'%('Version','2020','2021','2022','2023','2024','2025','2026','S','DD'))
print('  '+'-'*95)
baselines=[
    ('Trail=5% + ETF>MA60',[33.6,14.7,-4.4,12.6,35.8,162.4,73.1],1.048,38.8),
    ('Trail=3% + ETF>MA60',[31.5,45.7,-35.9,11.8,55.8,372.8,368.0],0.440,100.0),
    ('Dual-Trail (this)',[yr_pnl.get(str(y),0)/INIT*100 for y in range(2020,2027)],sh,mdd*100),
]
for label,rets_,s_,dd_ in baselines:
    yr_str=' '.join('%+5.1f%%'%r for r in rets_)
    print('  %-25s %s %7.3f %5.1f%%'%(label,yr_str,s_,dd_))

# Formula
print()
print('  FORMULA:')
print('''
  Dual-Trail Trend Rotation

  Entry: MA6 > MA15 AND MA8 4d-slope > 0 AND ETF > MA60
  Exit:  Trail OR trend_off
  Select: Top 1 by MA6/MA15 ratio, full capital

  Bull (HS300 slope>=0): Trail=3%%, no MH
  Bear (HS300 slope<0):  Trail=5%%, MH=7d

  Sharpe %.3f | Ret %.1f%% | DD %.1f%% | %d trades | Win %.0f%%
'''%(sh,tr*100,mdd*100,len(st),wr*100))

print('  Done!')
