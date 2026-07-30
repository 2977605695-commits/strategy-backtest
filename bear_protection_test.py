"""Bear Market Protection Strategies · 6 Approaches Tested"""
import json,os,sys,io,math
from collections import defaultdict
from datetime import datetime
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
RF=0.025;TD=252;INIT=10_000_000;TRAIL=0.05;F_MA=6;S_MA=15;SL_MA=8

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

def build_regime(method,ma_w=None):
    """Build market regime dict. method: 'slope' | 'close_below' | 'both' | None"""
    for code in load_all():
        if code=='510300':
            c=[b['close'] for b in load_all()[code]['bars']]
            m=ma(c,ma_w or 60);dates=[b['date'] for b in load_all()[code]['bars']]
            sl=slp(m,(ma_w or 60)//3) if ma_w else slp(m,20)
            res={}
            for i in range(len(dates)):
                d=dates[i]
                if method=='slope':
                    res[d]=not math.isnan(sl[i]) and sl[i]<0
                elif method=='close_below':
                    res[d]=not math.isnan(m[i]) and c[i]<m[i]
                elif method=='both':
                    res[d]=(not math.isnan(sl[i]) and sl[i]<0) and (not math.isnan(m[i]) and c[i]<m[i])
                elif method=='close_below_slope_neg':
                    res[d]=(not math.isnan(sl[i]) and sl[i]<0) or (not math.isnan(m[i]) and c[i]<m[i])
                elif method=='never':
                    res[d]=False
                else:
                    res[d]=False
            return res
    return {}

def run(etfs,all_sigs,protect_mode,protect_param):
    """protect_mode: 'none'|'mh'|'noentry'|'halfsize'|'etf_ma60'|'defensive'|'vol_skew'"""
    codes=sorted(etfs.keys());dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    fd={c:etfs[c]['first_date'] for c in codes}
    ad=set()
    for c in codes:ad.update(dm[c].keys())
    all_dates=sorted(ad)
    cash=INIT;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';entry_date=None;trades=[];dvs=[];tn=0;trn=0
    # Build regime signals
    if 'regime_' in protect_mode:
        # protect_mode = 'regime_method_maw'
        pass

    regime_maps={}
    for key,cfg in [
        ('slope60', ('regime_slope',60)),
        ('close60', ('regime_close_below',60)),
        ('both60', ('regime_both',60)),
    ]:
        regime_maps[key]=build_regime(cfg[0].replace('regime_',''),cfg[1])

    # Also compute each ETF's own MA60 for ETF-level filter
    etf_ma60={}
    for code in etfs:
        c=[b['close'] for b in etfs[code]['bars']]
        m60=ma(c,60);dates=[b['date'] for b in etfs[code]['bars']]
        etf_ma60[code]={dates[i]:not math.isnan(m60[i]) and c[i]>m60[i] for i in range(len(dates))}

    def is_bear(d):
        if protect_mode=='none':return False
        # ALL protection modes use HS300 slope60 as bear detection
        return regime_maps['slope60'].get(d,False)

    # Precompute bear_days for stats
    bear_d_count=sum(1 for d in all_dates if is_bear(d))

    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d];dt_obj=datetime.strptime(d,'%Y-%m-%d')
        bear=is_bear(d)
        if protect_mode=='mh':
            cur_mh=protect_param if bear else 0
        elif protect_mode in('close60','both60'):
            bear2=regime_maps.get(protect_mode,{}).get(d,False)
            cur_mh=protect_param if bear2 else 0
        else:
            cur_mh=0

        if pos_code:
            bar=dm[pos_code].get(d)
            if bar:
                px=bar['close']
                if px>peak:peak=px
                ton=all_sigs[pos_code]['trend'].get(d,False);er=None
                if px<=peak*(1-TRAIL):er='trail';trn+=1
                elif not ton:
                    if cur_mh>0 and entry_date:
                        if (dt_obj-entry_date).days>=cur_mh:er='off';tn+=1
                    else:er='off';tn+=1
                if er:
                    sell_val=shares*px;pnl=sell_val-shares*bp
                    trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':er,'bear':bear})
                    cash+=sell_val;pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_date=None

        # Entry logic
        if not pos_code and cash>0:
            # Check if allowed to enter
            can_enter=True;pos_frac=1.0
            if protect_mode=='noentry' and bear:can_enter=False
            elif protect_mode=='halfsize' and bear:pos_frac=0.5
            elif protect_mode=='quartersize' and bear:pos_frac=0.25

            if protect_mode=='etf_ma60':
                # Individual ETF must be above its MA60
                pass  # handled per-candidate below

            if can_enter:
                cands=[]
                for c in avail:
                    ton=all_sigs[c]['trend'].get(d,False)
                    if not ton:continue
                    # ETF-level filter: only buy if ETF > MA60 (in bear)
                    if protect_mode=='etf_ma60' and bear:
                        if not etf_ma60[c].get(d,True):continue
                    if protect_mode=='etf_ma60_always':
                        if not etf_ma60[c].get(d,True):continue
                    bar=dm[c].get(d);cands.append((c,all_sigs[c]['ratio'].get(d,1.0),bar['close'] if bar else 0))
                if cands:
                    cands.sort(key=lambda x:x[1],reverse=True)
                    c,ratio,px=cands[0]
                    invest=cash*pos_frac
                    shares=invest/px;bp=px;peak=px;pos_code=c;entry_d=d;entry_date=dt_obj;cash-=invest

        pos_val=shares*dm[pos_code].get(d,{}).get('close',0) if pos_code else 0
        dvs.append(cash+pos_val)

    if pos_code:
        bar=dm[pos_code].get(all_dates[-1])
        if bar:
            px=bar['close'];sell_val=shares*px;pnl=sell_val-shares*bp
            trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':'final','bear':is_bear(all_dates[-1])});cash+=sell_val

    fv=cash;rets=[]
    for i in range(1,len(dvs)):
        p,c=dvs[i-1],dvs[i]
        if p>0:rets.append((c-p)/p)
    if not rets:rets=[0.0]
    pkv=dvs[0];md=0.0
    for v in dvs:
        if v>pkv:pkv=v
        dd=(pkv-v)/pkv
        if dd>md:md=dd
    tr=(fv-INIT)/INIT;mu=sum(rets)/len(rets)
    sd_=(sum((r-mu)**2 for r in rets)/(len(rets)-1))**0.5 if len(rets)>1 else 0.01
    av=sd_*math.sqrt(TD);sh=(mu*TD-RF)/av if av>0 else 0;ar=(1+tr)**(TD/len(rets))-1 if tr>-1 else -1
    cm=ar/md if md>0 else 0
    st=[t for t in trades if t['e'] in('trail','off','final')]
    w=sum(1 for t in st if t['r']>0);wr=w/len(st) if st else 0
    bear_trades=[t for t in st if t['bear']];bull_trades=[t for t in st if not t['bear']]
    bear_pnl=sum(t['pnl'] for t in bear_trades);bull_pnl=sum(t['pnl'] for t in bull_trades)
    return{'sh':sh,'tr':tr,'ar':ar,'mdd':md,'cm':cm,'np':len(st),'wr':wr,'tn':tn,'trn':trn,
           'bear_pnl':bear_pnl,'bull_pnl':bull_pnl,'bear_days':bear_d_count,
           'bear_trd':len(bear_trades),'bull_trd':len(bull_trades),'trades':trades,'dvs':dvs}

# Main
etfs_all=load_all()
all_sigs={}
for code in etfs_all:all_sigs[code]=gen_sigs(etfs_all[code]['bars'])

print('='*110)
print('  BEAR MARKET PROTECTION STRATEGIES COMPARED')
print('  Base: MA6/15 s8 Trail=5% BearMH=7 BullMH=0 HS300-slope60')
print('='*110)

tests=[
    ('BASELINE (no protect)', 'none', 0),
    ('MH=7 (current best)', 'slope60', 7),
    ('MH=14 bear', 'slope60', 14),
    ('MH=21 bear', 'slope60', 21),
    ('NO ENTRY in bear', 'noentry', 0),
    ('HALF SIZE in bear', 'halfsize', 0),
    ('QUARTER SIZE in bear', 'quartersize', 0),
    ('ETF>MA60 in bear', 'etf_ma60', 7),
    ('ETF>MA60 ALWAYS', 'etf_ma60_always', 7),
    ('MH=7 + close<MA60', 'close60', 7),
    ('MH=7 + both filter', 'both60', 7),
]

# Fix: noentry/halfsize/quartersize/etf_ma60* need regime signal
# For these, use slope60 as bear signal but modify behavior
print('  %-30s %7s %9s %7s %7s %7s %5s %5s %10s %10s %8s'%(
    'Protection','S','Ret','DD','Calmar','Ann','Trd','Win','BearPnL','BullPnL','Yr22'))
print('  '+'-'*110)

all_res=[]
for label,mode,param in tests:
    r=run(etfs_all,all_sigs,mode,param)
    r['label']=label;all_res.append(r)
    yr_pnl=defaultdict(float)
    for t in r['trades']:
        if t['e'] in('trail','off','final'):yr_pnl[t.get('b','')[:4] if t.get('b') else '?' ]+=t['pnl']
    yr22=yr_pnl.get('2022',0)/INIT*100
    print('  %-30s %7.3f %8.2f%% %6.2f%% %7.3f %6.2f%% %5d %4.0f%% %+10s %+10s %+7.1f%%'%(
        label,r['sh'],r['tr']*100,r['mdd']*100,r['cm'],r['ar']*100,
        r['np'],r['wr']*100,str(int(r['bear_pnl'])),str(int(r['bull_pnl'])),yr22))

all_res.sort(key=lambda x:x['sh'],reverse=True)
print('\n\n  RANKING:')
for i,r in enumerate(all_res):
    yr_pnl=defaultdict(float)
    for t in r['trades']:
        if t['e'] in('trail','off','final'):yr_pnl[t.get('b','')[:4] if t.get('b') else '?']+=t['pnl']
    print('  %2d. %-30s S=%.3f Ret=%.1f%% DD=%.1f%% 2022=%+.1f%%'%(
        i+1,r['label'],r['sh'],r['tr']*100,r['mdd']*100,yr_pnl.get('2022',0)/INIT*100))

print('\n  Done!')
