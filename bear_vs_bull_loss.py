"""Check: are losses clustered in bear markets?"""
import json,os,math
from collections import defaultdict
from datetime import datetime
DATA_DIR='data';START='2020-01-01';END='2026-07-29'
TRAIL=0.05;F_MA=6;S_MA=15;SL_MA=8

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
            dt=b['date']
            if len(dt)==8:dt=dt[:4]+'-'+dt[4:6]+'-'+dt[6:8]
            if START<=dt<=END:bars.append({'date':dt,'close':float(b['close'])})
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

def main():
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

    # Build HS300 slope regime AND HS300>MA60 regime
    bear_slope={};bear_below={};bear_both={}
    for c in codes:
        if c=='510300':
            cl=[b['close'] for b in etfs[c]['bars']]
            m60=ma(cl,60);sl=slp(m60,20);dts=[b['date'] for b in etfs[c]['bars']]
            for i in range(len(dts)):
                d=dts[i]
                bear_slope[d]=not math.isnan(sl[i]) and sl[i]<0
                bear_below[d]=not math.isnan(m60[i]) and cl[i]<m60[i]
                bear_both[d]=bear_slope[d] and bear_below[d]
            break

    dm={c:{b['date']:b for b in etfs[c]['bars']} for c in codes}
    fd={c:etfs[c]['first_date'] for c in codes}
    ad=set()
    for c in codes:
        for k in dm[c]:ad.add(k)
    all_dates=sorted(ad)

    # RUN: ETF>MA60 filter (best config)
    cash=int(1e7);pos_code=None;shares=0.0;bp=0.0;peak=0.0;entry_d='';trades=[];dvs=[]
    for d in all_dates:
        avail=[c for c in codes if fd[c]<=d]
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
                    # Tag with regime info at entry
                    is_bear_slope=bear_slope.get(entry_d,False)
                    is_bear_below=bear_below.get(entry_d,False)
                    is_bear_both=bear_both.get(entry_d,False)
                    trades.append({
                        'pnl':pnl,'r':(px-bp)/bp,'e':er,'c':pos_code,'b':entry_d,'s':d,
                        'bear_slope':is_bear_slope,'bear_below':is_bear_below,'bear_both':is_bear_both
                    })
                    cash=shares*px;pos_code=None;shares=0.0;bp=0.0;peak=0.0
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
                shares=cash/px;bp=px;peak=px;pos_code=c;entry_d=d;cash=0.0
        pos_val=shares*dm[pos_code].get(d,{}).get('close',0) if pos_code else 0
        dvs.append(cash+pos_val)

    if pos_code:
        bar=dm[pos_code].get(all_dates[-1])
        if bar:
            px=bar['close'];pnl=shares*px-shares*bp
            trades.append({'pnl':pnl,'r':(px-bp)/bp,'e':'final','c':pos_code,'b':entry_d,'s':all_dates[-1],
                           'bear_slope':bear_slope.get(entry_d,False),
                           'bear_below':bear_below.get(entry_d,False),
                           'bear_both':bear_both.get(entry_d,False)})

    st=[t for t in trades if t.get('e','') in('trail','off','final')]

    # ANALYZE: losses by market regime
    all_losses=[t for t in st if t['r']<0]
    total_losses=sum(t['pnl'] for t in all_losses)
    total_wins=sum(t['pnl'] for t in st if t['r']>0)

    print('='*80)
    print('  ARE LOSSES CLUSTERED IN BEAR MARKETS?')
    print('='*80)

    for regime_name,regime_key in[('HS300 MA60斜率<0','bear_slope'),('HS300 close<MA60','bear_below'),('两者都满足','bear_both')]:
        bear_losses=[t for t in all_losses if t.get(regime_key,False)]
        bull_losses=[t for t in all_losses if not t.get(regime_key,False)]
        bear_win=[t for t in st if t['r']>0 and t.get(regime_key,False)]
        bull_win=[t for t in st if t['r']>0 and not t.get(regime_key,False)]

        b_l=sum(t['pnl'] for t in bear_losses)
        b_w=sum(t['pnl'] for t in bear_win)
        bu_l=sum(t['pnl'] for t in bull_losses)
        bu_w=sum(t['pnl'] for t in bull_win)

        print('\n  %s:'%regime_name)
        print('    熊市交易: %dL/%dW  Net=%+.1f万 (Loss=%+.1f万 Win=%+.1f万)'%(
            len(bear_losses),len(bear_win),b_l/1e4+b_w/1e4,b_l/1e4,b_w/1e4))
        print('    牛市交易: %dL/%dW  Net=%+.1f万 (Loss=%+.1f万 Win=%+.1f万)'%(
            len(bull_losses),len(bull_win),bu_l/1e4+bu_w/1e4,bu_l/1e4,bu_w/1e4))
        b_pct=b_l/(b_l+bu_l)*100 if(b_l+bu_l)!=0 else 0
        print('    熊市亏损占总亏损: %.0f%% (=%.1f万 / %.1f万)'%(b_pct,b_l/1e4,(b_l+bu_l)/1e4))

    # Best performing: both filter
    print('\n\n  '+('='*80))
    print('  DETAIL: 熊牛双确认下的交易分布')
    print('  '+('='*80))

    for label,key in[('HS300斜率<0','bear_slope'),('HS300价<MA60','bear_below'),('两者都满足','bear_both')]:
        bear_tr=[t for t in st if t.get(key,False)]
        bull_tr=[t for t in st if not t.get(key,False)]
        b_l=[t for t in bear_tr if t['r']<0];b_w=[t for t in bear_tr if t['r']>0]
        bu_l=[t for t in bull_tr if t['r']<0];bu_w=[t for t in bull_tr if t['r']>0]

        b_l_pnl=sum(t['pnl'] for t in b_l);b_w_pnl=sum(t['pnl'] for t in b_w)
        bu_l_pnl=sum(t['pnl'] for t in bu_l);bu_w_pnl=sum(t['pnl'] for t in bu_w)

        b_days_all=sum(1 for d in all_dates if bear_slope.get(d,False)) if key=='bear_slope' else \
                    sum(1 for d in all_dates if bear_below.get(d,False)) if key=='bear_below' else \
                    sum(1 for d in all_dates if bear_both.get(d,False))
        total_days=len(all_dates)

        print('\n  %s (%.0f%% of days):'%(label,b_days_all/total_days*100))
        if bear_tr:
            b_wr=len(b_w)/len(bear_tr)*100 if bear_tr else 0
            print('    熊市: %d笔 (%dL/%dW=%.0f%%) Net=%+.1f万'%(
                len(bear_tr),len(b_l),len(b_w),b_wr,(b_l_pnl+b_w_pnl)/1e4))
        if bull_tr:
            bu_wr=len(bu_w)/len(bull_tr)*100 if bull_tr else 0
            print('    牛市: %d笔 (%dL/%dW=%.0f%%) Net=%+.1f万'%(
                len(bull_tr),len(bu_l),len(bu_w),bu_wr,(bu_l_pnl+bu_w_pnl)/1e4))

    print('\n  Done!')

if __name__=='__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
