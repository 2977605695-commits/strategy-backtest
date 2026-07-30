"""Trail=3% Bear Market Loss Deep Dive"""
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

# Build bear regime
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

# Full backtest with detailed trade tracking
cash=INIT;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[]

for d in all_dates:
    avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
    is_bear=bear_slope.get(d,False)
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
                trades.append({
                    'pnl':pnl,'r':(px-bp)/bp,'e':er,'c':pos_code,'b':entry_d,'s':d,
                    'days':(dt_obj-entry_date).days if entry_date else 0,
                    'is_bear_entry':bear_slope.get(entry_d,False),
                    'peak_r':(peak-bp)/bp if bp>0 else 0
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
                       'is_bear_entry':bear_slope.get(entry_d,False),'peak_r':0})

st=[t for t in trades if t['e'] in('trail','off','final')]

# ===== ANALYZE 2022 SPECIFICALLY =====
print('='*100)
print('  TRAIL=3%% · 2022 BEAR MARKET LOSS ANALYSIS')
print('='*100)

yr2022=[t for t in st if t.get('b','')[:4]=='2022']
losses_22=[t for t in yr2022 if t['r']<0]
wins_22=[t for t in yr2022 if t['r']>0]
total_l_22=sum(t['pnl'] for t in losses_22);total_w_22=sum(t['pnl'] for t in wins_22)

print('\n  2022 SUMMARY: %d trades (%dL/%dW) Net=%+.0f (Loss=%+.0f Win=%+.0f)'%(
    len(yr2022),len(losses_22),len(wins_22),total_l_22+total_w_22,total_l_22,total_w_22))

# Break down by entry regime
bear_entry=[t for t in yr2022 if t.get('is_bear_entry',False)]
bull_entry=[t for t in yr2022 if not t.get('is_bear_entry',False)]
b_l=[t for t in bear_entry if t['r']<0];b_w=[t for t in bear_entry if t['r']>0]
bu_l=[t for t in bull_entry if t['r']<0];bu_w=[t for t in bull_entry if t['r']>0]
print('  Entered during HS300 slope<0: %d trades (%dL/%dW) Net=%.0f'%(
    len(bear_entry),len(b_l),len(b_w),sum(t['pnl'] for t in b_l)+sum(t['pnl'] for t in b_w)))
print('  Entered during HS300 slope>=0: %d trades (%dL/%dW) Net=%.0f'%(
    len(bull_entry),len(bu_l),len(bu_w),sum(t['pnl'] for t in bu_l)+sum(t['pnl'] for t in bu_w)))

# ===== ANALYZE: what happens to bear-market trades? =====
print('\n\n  ALL 2022 LOSSES BY CAUSE:')
# Exit type breakdown
trail_l22=[t for t in losses_22 if t['e']=='trail']
off_l22=[t for t in losses_22 if t['e']=='off']
print('  Trail止损: %d trades avg=%.2f%%'%(len(trail_l22),sum(t['r'] for t in trail_l22)/len(trail_l22) if trail_l22 else 0))
print('  趋势转空: %d trades avg=%.2f%%'%(len(off_l22),sum(t['r'] for t in off_l22)/len(off_l22) if off_l22 else 0))

# Was peak reached before exit?
print('\n  TRAIL LOSS DETAILS: did they ever show profit?')
for t in trail_l22:
    name=etfs[t['c']]['name'];peak_r=t.get('peak_r',0)
    print('  %s %s: entry=%.4f peak_r=%.2f%% exit_r=%.2f%% %dd %s'%(
        t['c'],name,t.get('bp',0),peak_r*100,t['r']*100,t['days'],'bear' if t['is_bear_entry'] else 'bull'))

# ===== ETF-level: which ETFs caused the most damage in 2022? =====
print('\n\n  ETF DAMAGE IN 2022:')
etf_22=defaultdict(lambda:{'loss':0,'win':0,'n':0})
for t in yr2022:
    etf_22[t['c']]['n']+=1
    if t['r']<0:etf_22[t['c']]['loss']+=t['pnl']
    else:etf_22[t['c']]['win']+=t['pnl']
for c,stats in sorted(etf_22.items(),key=lambda x:x[1]['loss']):
    name=etfs[c]['name'];net=stats['loss']+stats['win']
    print('  %s %s: L=%.0f W=%.0f Net=%.0f N=%d'%(c,name,stats['loss'],stats['win'],net,stats['n']))

# ===== COMPARE: Trail=3% vs Trail=5% for 2022 =====
print('\n\n  TRAIL=3%% vs TRAIL=5%% (2022 comparison)')
print('  Trail=5%%: 31 trades, -4.4%% annual, max DD 30.7%%')
# Simulate: what if Trail=5% for the same entries?
# (simplified: Trail=3% losses that would NOT have triggered at Trai=5%)
saved_by_5pct=0
for t in trail_l22:
    # Trail=3% triggered when px <= peak*0.97
    # Would Trail=5% have triggered? When px <= peak*0.95
    # If the loss was between 3% and 5%, Trail=5% would NOT have triggered
    if abs(t['r'])<0.05 and t['r']<0:
        saved_by_5pct+=abs(t['pnl'])
        name=etfs[t['c']]['name']
        print('  Would have survived Trail=5%%: %s %s %.2f%% %dd'%(t['c'],name,t['r']*100,t['days']))
print('  Total saved by Trail=5%% in 2022: %.0f'%saved_by_5pct)

# Sequential loss analysis
print('\n\n  SEQUENTIAL LOSSES IN 2022:')
yr2022_sorted=sorted(yr2022,key=lambda x:x.get('b',''))
streak=0;max_seq=0;seq_losses=[]
for t in yr2022_sorted:
    if t['r']<0:
        streak+=1;seq_losses.append(t)
        max_seq=max(max_seq,streak)
    else:
        if streak>=3:
            total_seq=sum(t2['pnl'] for t2 in seq_losses[-streak:])
            print('  %d consecutive losses, total=%.0f'%(streak,total_seq))
            for t2 in seq_losses[-streak:]:
                name=etfs[t2['c']]['name']
                print('    %s %s %s->%s %.2f%% %s'%(t2['c'],name,t2['b'],t2['s'],t2['r']*100,t2['e']))
        streak=0
print('  Max consecutive losses: %d'%max_seq)

print('\n  Done!')
