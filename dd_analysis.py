"""Drawdown Analysis for Final Strategy"""
import json,os,sys,io,math
from collections import defaultdict
from datetime import datetime
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')

DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=10_000_000;MAX_POS=1;TRAIL=0.05;F_MA=6;S_MA=15;SL_MA=8;BEAR_MH=7;BULL_MH=0

ETF_CODES=['159782','588380','588870','588080','588300','518800','589720','588890','588170',
           '588200','159995','512480','515880','515050','159819','159992','512010',
           '518880','159937','513180','513050','513100','159509','588000','588220',
           '510300','159915','510050','511010','511260','510880','512890','159301']

def load_all():
    etfs={}
    for code in ETF_CODES:
        path=os.path.join(DATA_DIR,'etf_'+code+'.json')
        if not os.path.exists(path):continue
        d=json.load(open(path,encoding='utf-8'))
        bars=[]
        for b in d['bars']:
            dt=b['date']
            if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            bars.append({'date':dt,'close':float(b['close'])})
        etfs[code]={'name':d['name'],'first_date':bars[0]['date'],'bars':bars}
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

def gen_sigs(bars):
    c=[b['close'] for b in bars];n=len(bars)
    mf=ma(c,F_MA);ms=ma(c,S_MA);msl=ma(c,SL_MA);slo_=slp(msl,max(SL_MA//2,3))
    dates=[b['date'] for b in bars];trnd={};rat={}
    for i in range(n):
        d=dates[i]
        if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i]>0:
            sok=not math.isnan(slo_[i]) and slo_[i]>0
            trnd[d]=mf[i]>ms[i] and sok;rat[d]=mf[i]/ms[i]
        else:trnd[d]=False;rat[d]=1.0
    return{'trend':trnd,'ratio':rat}

def build_regime(etfs):
    for code in etfs:
        if code=='510300':
            c=[b['close'] for b in etfs[code]['bars']]
            m60=ma(c,60);sl=slp(m60,20);dates=[b['date'] for b in etfs[code]['bars']]
            return {dates[i]:not math.isnan(sl[i]) and sl[i]<0 for i in range(len(dates))}
    return {}

etfs_all=load_all()
all_sigs={}
for code in etfs_all:all_sigs[code]=gen_sigs(etfs_all[code]['bars'])
mkt=build_regime(etfs_all)

codes=sorted(etfs_all.keys())
dm={c:{b['date']:b for b in etfs_all[c]['bars']} for c in codes}
fd={c:etfs_all[c]['first_date'] for c in codes}
ad=set()
for c in codes:ad.update(dm[c].keys())
all_dates=sorted(ad)
cash=INIT;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[];holdings=[]

for d in all_dates:
    avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
    is_bear=mkt.get(d,False);cur_mh=BEAR_MH if is_bear else BULL_MH
    if pos_code:
        bar=dm[pos_code].get(d)
        if bar:
            px=bar['close']
            if px>peak:peak=px
            ton=all_sigs[pos_code]['trend'].get(d,False);er=None
            if px<=peak*(1-TRAIL):er='trail'
            elif not ton:
                if cur_mh>0 and entry_date:
                    if (dt_obj-entry_date).days>=cur_mh:er='off'
                else:er='off'
            if er:
                sell_val=shares*px;pnl=sell_val-shares*bp
                trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':er,'c':pos_code,'b':entry_d,'s':d})
                cash=sell_val;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None
    if not pos_code and cash>0:
        cands=[]
        for c in avail:
            ton=all_sigs[c]['trend'].get(d,False)
            if ton:
                bar=dm[c].get(d);cands.append((c,all_sigs[c]['ratio'].get(d,1.0),bar['close'] if bar else 0))
        if cands:
            cands.sort(key=lambda x:x[1],reverse=True)
            c,ratio,px=cands[0];shares=cash/px;bp=px;peak=px;pos_code=c;entry_d=d;entry_date=dt_obj;cash=0.0
    pos_val=shares*dm[pos_code].get(d,{}).get('close',0) if pos_code else 0
    holdings.append((d,pos_code,all_sigs[pos_code]['trend'].get(d,False) if pos_code else None))
    dvs.append(cash+pos_val)

if pos_code:
    bar=dm[pos_code].get(all_dates[-1])
    if bar:
        px=bar['close'];sell_val=shares*px;pnl=sell_val-shares*bp
        trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':'final','c':pos_code,'b':entry_d,'s':all_dates[-1]})

# Find ALL DD events (>5%)
pk=dvs[0];dd_events=[];in_dd=False;dd_start='';dd_start_idx=0;local_max_dd=0
for i,(d,v) in enumerate(zip(all_dates,dvs)):
    if v>pk:pk=v
    dd=(pk-v)/pk
    if dd>0.05 and not in_dd:
        in_dd=True;dd_start=d;dd_start_idx=i;local_max_dd=dd
    if in_dd:
        if dd>local_max_dd:local_max_dd=dd
        if dd<0.03:
            if local_max_dd>0.08:
                dd_events.append((dd_start,d,local_max_dd,dd_start_idx,i))
            in_dd=False;local_max_dd=0
if in_dd and local_max_dd>0.08:
    dd_events.append((dd_start,all_dates[-1],local_max_dd,dd_start_idx,len(dvs)-1))

print('='*90)
print('  MAJOR DRAWDOWN EVENTS (>8% depth)')
print('='*90)
for idx,(start,end,maxdd,si,ei) in enumerate(dd_events):
    period_trades=[]
    for t in trades:
        ts=t.get('b','');te=t.get('s','')
        if (start<=ts<=end) or (start<=te<=end):period_trades.append(t)
    losses=[t for t in period_trades if t['r']<0];wins=[t for t in period_trades if t['r']>0]
    # Holding during DD
    holdings_in_dd=set()
    for d,h,_ in holdings[si:ei+1]:
        if h:holdings_in_dd.add(h)
    h_names=','.join(etfs_all[c]['name'] for c in holdings_in_dd if c in etfs_all)
    total_loss=sum(t['pnl'] for t in losses);total_win=sum(t['pnl'] for t in wins)
    sd=datetime.strptime(start,'%Y-%m-%d');ed=datetime.strptime(end,'%Y-%m-%d')
    nd=(ed-sd).days
    print('\n  DD #%d: %.1f%%  %s->%s (%dd)  Holding: %s'%(idx+1,maxdd*100,start,end,nd,h_names[:50]))
    print('  %d trades (%dL/%dW) NetPnL=%s  LossTotal=%s  WinTotal=%s'%(
        len(period_trades),len(losses),len(wins),
        str(int(total_loss+total_win)),str(int(total_loss)),str(int(total_win))))
    for t in sorted(losses,key=lambda x:x['r']):
        name=etfs_all[t['c']]['name'] if t['c'] in etfs_all else '?'
        print('    L %s %s %s %+.2f%% %s'%(t['c'],name,t.get('b','?'),t['r']*100,t['e']))

# === MAX DD DETAIL ===
print('\n\n'+'='*70)
print('  DEEPEST DRAWDOWN ANALYSIS')
print('='*70)
pk=dvs[0];max_dd=0;max_dd_start='';max_dd_end='';max_dd_idx=0
for i,(v,d) in enumerate(zip(dvs,all_dates)):
    if v>pk:pk=v
    dd=(pk-v)/pk
    if dd>max_dd:max_dd=dd;max_dd_end=d;max_dd_idx=i

# Find when this DD started (peak before trough)
pk_before=0;pk_before_idx=0
for i in range(max_dd_idx):
    if dvs[i]>pk_before:pk_before=dvs[i];pk_before_idx=i
max_dd_start=all_dates[pk_before_idx]

sd=datetime.strptime(max_dd_start,'%Y-%m-%d');ed=datetime.strptime(max_dd_end,'%Y-%m-%d')
print('  DD: %.1f%%  %s -> %s (%dd)'%(max_dd*100,max_dd_start,max_dd_end,(ed-sd).days))
print('  Peak NAV: %s  Trough NAV: %s'%(str(int(pk_before)),str(int(dvs[max_dd_idx]))))

# All trades in this period
period_trades=[]
for t in trades:
    ts=t.get('b','');te=t.get('s','')
    if (max_dd_start<=ts<=max_dd_end) or (max_dd_start<=te<=max_dd_end):
        period_trades.append(t)
losses=[t for t in period_trades if t['r']<0];wins=[t for t in period_trades if t['r']>0]
print('\n  ALL TRADES IN DD WINDOW (%d total):'%len(period_trades))
for t in sorted(period_trades,key=lambda x:x['r']):
    name=etfs_all[t['c']]['name'] if t['c'] in etfs_all else '?'
    tag='L' if t['r']<0 else 'W'
    print('  %s %s %s %s->%s %+7.2f%% %-8s PnL=%s'%(
        tag,t['c'],name,t.get('b','?'),t.get('s','?'),t['r']*100,t['e'],str(int(t['pnl']))))

# Root cause summary
print('\n\n  ROOT CAUSE:')
total_loss_in_dd=sum(t['pnl'] for t in losses)
total_win_in_dd=sum(t['pnl'] for t in wins)
print('  Losses: %d trades, total=%s'%(len(losses),str(int(total_loss_in_dd))))
print('  Wins:   %d trades, total=%s'%(len(wins),str(int(total_win_in_dd))))
print('  Net:    %s'%(str(int(total_loss_in_dd+total_win_in_dd))))

# Check: was DD driven by a single big loss or many small ones?
losses_by_pnl=sorted(losses,key=lambda x:x['pnl'])
cum=0;n_for_80=0
for t in losses_by_pnl:
    cum+=t['pnl'];n_for_80+=1
    if abs(cum)>abs(total_loss_in_dd)*0.8:break
print('\n  Top %d losses contribute 80%% of total loss'%n_for_80)
for t in losses_by_pnl[:n_for_80]:
    name=etfs_all[t['c']]['name'] if t['c'] in etfs_all else '?'
    print('    %s %s %+.2f%% %s %s'%(t['c'],name,t['r']*100,t['e'],str(int(t['pnl']))))

print('\n  Done!')
