"""
ETF MA Deviation Priority Rotation Strategy - Aggressive Parameter Sweep
======================================================================
Purpose: Systematically test all buy/sell threshold combinations,
         with special focus on aggressive sell thresholds (>= 7%).

Strategy:
  - Priority ranking: KCBanDaoTi > KC50 > HS300 > Medical > Bank
  - Hold only 1 ETF at a time
  - Sell trigger -> immediately check if another ETF meets buy conditions
  - Buy highest priority ETF with buy signal
  - No buy signal -> stay in cash
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import urllib.request
import json
import math
import time
from collections import defaultdict

# ============================================================
# Config
# ============================================================
ETF_CONFIG = {
    'KCBanDaoTi':  {'code': 'sh588170', 'priority': 1},
    'KC50':    {'code': 'sh588150', 'priority': 2},
    'HS300': {'code': 'sh510300', 'priority': 3},
    'Medical':    {'code': 'sh512170', 'priority': 4},
    'Bank':    {'code': 'sh512800', 'priority': 5},
}

ETF_CN_NAMES = {
    'KCBanDaoTi': '科创半导体ETF华夏',
    'KC50': '科创50ETF南方',
    'HS300': '华泰柏瑞沪深300ETF',
    'Medical': '华宝中证医疗ETF',
    'Bank': '华宝中证银行ETF',
}

START_DATE = '2024-01-01'
END_DATE   = '2026-07-01'
RISK_FREE_RATE = 0.025
TRADING_DAYS   = 252

# ============================================================
# Data Fetching
# ============================================================
def fetch_tencent_kline(code, start, end):
    url = (f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
           f'?param={code},day,{start},{end},640,qfq')
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36',
        'Referer': 'https://gu.qq.com/',
    }
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except:
            time.sleep(1)
    return {}

def parse_kline_data(raw, code):
    try:
        days = None
        if 'data' in raw:
            for k in raw['data']:
                if isinstance(raw['data'][k], dict):
                    for field in ['qfqday', 'day']:
                        if field in raw['data'][k]:
                            days = raw['data'][k][field]
                            break
                    if days: break
        if not days: return []
        return [{'date': str(d[0]), 'close': float(d[2])}
                for d in days if len(d) >= 6]
    except:
        return []

def calc_ma(data, window=5):
    ma = []
    for i in range(len(data)):
        if i < window - 1:
            ma.append(float('nan'))
        else:
            ma.append(sum(data[i - window + 1:i + 1]) / window)
    return ma

def generate_signals(bars, buy_t, sell_t):
    closes = [b['close'] for b in bars]
    ma5 = calc_ma(closes, 5)
    for i, bar in enumerate(bars):
        bar['ma5'] = ma5[i]
        if math.isnan(ma5[i]) or ma5[i] == 0:
            bar['deviation'] = float('nan')
            bar['signal'] = 'hold'
        else:
            bar['deviation'] = (bar['close'] - ma5[i]) / abs(ma5[i])
            if bar['deviation'] < buy_t:
                bar['signal'] = 'buy'
            elif bar['deviation'] > sell_t:
                bar['signal'] = 'sell'
            else:
                bar['signal'] = 'hold'
    return bars

# ============================================================
# Priority Rotation Backtest
# ============================================================
def backtest_priority_rotation(all_signals, init_capital=1_000_000):
    date_maps = {}
    for name, bars in all_signals.items():
        date_maps[name] = {b['date']: b for b in bars}

    all_date_sets = [set(m.keys()) for m in date_maps.values()]
    common_dates = sorted(all_date_sets[0].intersection(*all_date_sets[1:]))

    if len(common_dates) < 2:
        return {
            'total_return': 0.0, 'annual_return': 0.0, 'volatility': 0.0,
            'sharpe': 0.0, 'calmar': 0.0, 'max_drawdown': 0.0,
            'n_trades': 0, 'n_pairs': 0, 'win_rate': 0.0,
            'trades': [], 'buy_sell_pairs': [], 'daily_values': [],
            'daily_returns': [], 'empty_days': 0, 'empty_pct': 0.0,
            'etf_stats': {}, 'holding_days': 0,
        }

    etfs_by_priority = sorted(all_signals.keys(),
                              key=lambda n: ETF_CONFIG[n]['priority'])

    cash = init_capital
    position = 0.0
    holding = None
    buy_price = 0.0
    trades = []
    daily_values = []
    etf_trades = defaultdict(list)
    etf_pnl = defaultdict(float)
    holding_days = 0

    for d in common_dates:
        # Step 1: Check sell
        if holding is not None:
            bar = date_maps[holding].get(d)
            if bar and bar['signal'] == 'sell':
                price = bar['close']
                cash = position * price
                pnl = cash - (position * buy_price)
                trades.append({
                    'date': d, 'action': 'sell', 'etf': holding,
                    'price': price, 'shares': position,
                    'value': cash, 'pnl': pnl,
                })
                etf_trades[holding].append({
                    'action': 'sell', 'date': d, 'price': price,
                    'shares': position, 'pnl': pnl,
                })
                etf_pnl[holding] += pnl
                position = 0.0
                holding = None
                buy_price = 0.0

        # Step 2: Check buy
        if holding is None:
            for name in etfs_by_priority:
                bar = date_maps[name].get(d)
                if bar and bar['signal'] == 'buy':
                    price = bar['close']
                    position = cash / price
                    buy_price = price
                    holding = name
                    trades.append({
                        'date': d, 'action': 'buy', 'etf': name,
                        'price': price, 'shares': position,
                        'value': cash,
                    })
                    etf_trades[name].append({
                        'action': 'buy', 'date': d, 'price': price,
                        'shares': position,
                    })
                    cash = 0.0
                    break

        # Daily value
        if holding is not None:
            bar = date_maps[holding].get(d)
            price = bar['close'] if bar else 0
            total = position * price
            holding_days += 1
        else:
            total = cash

        daily_values.append({
            'date': d, 'value': total, 'holding': holding,
        })

    # Final liquidation
    if holding is not None:
        last_date = common_dates[-1]
        bar = date_maps[holding].get(last_date)
        if bar:
            price = bar['close']
            cash = position * price
            pnl = cash - (position * buy_price)
            trades.append({
                'date': last_date, 'action': 'sell_final', 'etf': holding,
                'price': price, 'shares': position,
                'value': cash, 'pnl': pnl,
            })
            etf_pnl[holding] += pnl
            daily_values[-1]['value'] = cash
            daily_values[-1]['holding'] = None
        final_value = cash
    else:
        final_value = cash

    # Returns calculation
    returns = []
    for i in range(1, len(daily_values)):
        prev = daily_values[i-1]['value']
        curr = daily_values[i]['value']
        if prev > 0:
            returns.append((curr - prev) / prev)
        else:
            returns.append(0.0)

    # Max drawdown
    peak = daily_values[0]['value']
    max_dd = 0.0
    for dv in daily_values:
        if dv['value'] > peak:
            peak = dv['value']
        dd = (peak - dv['value']) / peak
        if dd > max_dd:
            max_dd = dd

    # Performance metrics
    total_return = (final_value - init_capital) / init_capital

    if len(returns) > 1:
        mean_ret = sum(returns) / len(returns)
        std_ret = (sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5
        annual_std = std_ret * math.sqrt(TRADING_DAYS)
        annual_ret = mean_ret * TRADING_DAYS
        sharpe = (annual_ret - RISK_FREE_RATE) / annual_std if annual_std > 0 else 0
    else:
        annual_std = mean_ret = sharpe = 0.0
        annual_ret = 0.0

    n_valid_days = max(len(returns), 1)
    if total_return > -1:
        annual_return = (1 + total_return) ** (TRADING_DAYS / n_valid_days) - 1
    else:
        annual_return = -1

    calmar = annual_return / max_dd if max_dd > 0 else float('inf')

    # Trade pairs
    buy_sell_pairs = []
    current_buy = None
    for t in trades:
        if t['action'] == 'buy':
            current_buy = t
        elif t['action'] in ('sell', 'sell_final') and current_buy:
            buy_sell_pairs.append({
                'etf': t['etf'],
                'buy_date': current_buy['date'],
                'sell_date': t['date'],
                'buy_price': current_buy['price'],
                'sell_price': t['price'],
                'return': (t['price'] - current_buy['price']) / current_buy['price'],
                'pnl': t.get('pnl', 0),
            })
            current_buy = None

    win_count = sum(1 for p in buy_sell_pairs if p['return'] > 0)
    win_rate = win_count / len(buy_sell_pairs) if buy_sell_pairs else 0

    # ETF stats
    etf_stats = {}
    for name in etfs_by_priority:
        n_b = len([t for t in etf_trades[name] if t['action'] == 'buy'])
        n_s = len([t for t in etf_trades[name] if t['action'] == 'sell'])
        etf_pairs = [p for p in buy_sell_pairs if p['etf'] == name]
        etf_wins = sum(1 for p in etf_pairs if p['return'] > 0)
        etf_stats[name] = {
            'n_buys': n_b, 'n_sells': n_s,
            'total_pnl': etf_pnl[name],
            'pnl_pct': etf_pnl[name] / init_capital,
            'win_rate': etf_wins / len(etf_pairs) if etf_pairs else 0,
            'n_pairs': len(etf_pairs),
        }

    empty_days = sum(1 for dv in daily_values if dv['holding'] is None)
    empty_pct = empty_days / len(daily_values) if daily_values else 0

    return {
        'total_return': total_return,
        'annual_return': annual_return,
        'volatility': annual_std,
        'sharpe': sharpe,
        'calmar': calmar,
        'max_drawdown': max_dd,
        'n_trades': len(trades),
        'n_pairs': len(buy_sell_pairs),
        'win_rate': win_rate,
        'empty_days': empty_days,
        'empty_pct': empty_pct,
        'holding_days': holding_days,
        'etf_stats': etf_stats,
        'trades': trades,
        'buy_sell_pairs': buy_sell_pairs,
        'daily_values': daily_values,
        'daily_returns': returns,
    }


# ============================================================
# Main
# ============================================================
def main():
    print('=' * 100)
    print('  ETF MA Deviation - Priority Rotation - Aggressive Parameter Sweep')
    print(f'  Period: {START_DATE} -> {END_DATE}')
    print('  Strategy: Priority Rotation (KCBanDaoTi > KC50 > HS300 > Medical > Bank)')
    print('=' * 100)

    # ---- Fetch Data ----
    print('\n[DATA] Fetching market data...')
    all_bars = {}
    for name, cfg in ETF_CONFIG.items():
        code = cfg['code']
        print(f'  -> {ETF_CN_NAMES[name]} ({code}) ...', end=' ', flush=True)
        raw = fetch_tencent_kline(code, START_DATE, END_DATE)
        bars = parse_kline_data(raw, code)
        if bars:
            all_bars[name] = bars
            print(f'OK {len(bars)} rows ({bars[0]["date"]} ~ {bars[-1]["date"]})')
        else:
            print(f'FAIL')
        time.sleep(0.2)

    etf_priority_order = sorted(all_bars.keys(),
                                key=lambda n: ETF_CONFIG[n]['priority'])

    # ---- Grid Search ----
    buy_candidates  = [-0.020, -0.025, -0.030, -0.035, -0.040, -0.045, -0.050, -0.055, -0.060, -0.070, -0.080]
    sell_candidates = [0.030, 0.035, 0.040, 0.045, 0.050, 0.055, 0.060, 0.065, 0.070, 0.075,
                       0.080, 0.085, 0.090, 0.095, 0.100, 0.110, 0.120, 0.130, 0.140]

    n_total = len(buy_candidates) * len(sell_candidates)
    print(f'\n[GRID] {len(buy_candidates)} buy x {len(sell_candidates)} sell = {n_total} combinations')
    print(f'   buy  range: {buy_candidates[0]:+.3f} ~ {buy_candidates[-1]:+.3f}')
    print(f'   sell range: {sell_candidates[0]:+.3f} ~ {sell_candidates[-1]:+.3f}')
    print()

    results = []
    count = 0

    for buy_val in buy_candidates:
        for sell_val in sell_candidates:
            count += 1

            signals = {}
            for name in etf_priority_order:
                bars_copy = [dict(b) for b in all_bars[name]]
                signals[name] = generate_signals(bars_copy, buy_val, sell_val)

            r = backtest_priority_rotation(signals)
            r['buy'] = buy_val
            r['sell'] = sell_val
            results.append(r)

            print(f'  [{count:>3d}/{n_total}] buy={buy_val:+.3f} sell={sell_val:+.3f}  '
                  f'S={r["sharpe"]:>7.3f}  Ret={r["total_return"]*100:>7.2f}%  '
                  f'DD={r["max_drawdown"]*100:>5.2f}%  Trades={r["n_pairs"]:>2d}  '
                  f'Win={r["win_rate"]*100:>4.0f}%  Cash={r["empty_pct"]*100:>4.0f}%  '
                  f'Hold={r["holding_days"]:>4d}d')

    # ---- Stability Score ----
    for r in results:
        dd_penalty = max(0, 1 - r['max_drawdown'] / 0.40)
        trade_bonus = min(math.sqrt(max(r['n_pairs'], 1)), 3.5)
        r['stability'] = r['sharpe'] * trade_bonus * (0.5 + 0.5 * dd_penalty)

    # ================================================================
    # Report A: Overall Stability Ranking TOP 30
    # ================================================================
    sorted_stable = sorted(results, key=lambda x: x['stability'], reverse=True)

    print('\n' + '=' * 100)
    print('  [A] OVERALL STABILITY RANKING - TOP 30')
    print('  (Sharpe x TradeFreq x DDPenalty)')
    print('=' * 100)

    header = (f'  {"Rank":<4s} {"buy":>7s} {"sell":>7s} '
              f'{"Sharpe":>7s} {"TotRet":>8s} {"AnnRet":>8s} '
              f'{"MaxDD":>7s} {"Calmar":>7s} {"Trd":>4s} '
              f'{"Win":>6s} {"Cash":>5s} {"HoldD":>6s} {"Stable":>8s}')
    print(header)
    print('  ' + '-' * 100)

    for rank, r in enumerate(sorted_stable[:30], 1):
        tag = ''
        if r['sell'] >= 0.100:
            tag = ' !!EXTREME'
        elif r['sell'] >= 0.085:
            tag = ' !AGGR'
        elif r['sell'] >= 0.075:
            tag = ' +'
        print(f'  {rank:<4d} {r["buy"]:>+7.3f} {r["sell"]:>+7.3f} '
              f'{r["sharpe"]:>7.3f} {r["total_return"]*100:>7.2f}% '
              f'{r["annual_return"]*100:>7.2f}% '
              f'{r["max_drawdown"]*100:>6.2f}% {r["calmar"]:>7.3f} '
              f'{r["n_pairs"]:>4d} {r["win_rate"]*100:>5.0f}% '
              f'{r["empty_pct"]*100:>4.0f}% {r["holding_days"]:>6d}{tag} '
              f'{r["stability"]:>8.4f}')

    # ================================================================
    # Report B: Pure Sharpe Ranking TOP 25
    # ================================================================
    sorted_sharpe = sorted(results, key=lambda x: x['sharpe'], reverse=True)

    print(f'\n\n  [B] PURE SHARPE RANKING - TOP 25')
    header = (f'  {"Rank":<4s} {"buy":>7s} {"sell":>7s} '
              f'{"Sharpe":>7s} {"TotRet":>8s} {"AnnRet":>8s} '
              f'{"MaxDD":>7s} {"Calmar":>7s} {"Trd":>4s} '
              f'{"Win":>6s} {"Cash":>5s}')
    print(header)
    print('  ' + '-' * 80)
    for rank, r in enumerate(sorted_sharpe[:25], 1):
        tag = ' !A' if r['sell'] >= 0.085 else (' +' if r['sell'] >= 0.075 else '')
        print(f'  {rank:<4d} {r["buy"]:>+7.3f} {r["sell"]:>+7.3f} '
              f'{r["sharpe"]:>7.3f} {r["total_return"]*100:>7.2f}% '
              f'{r["annual_return"]*100:>7.2f}% '
              f'{r["max_drawdown"]*100:>6.2f}% {r["calmar"]:>7.3f} '
              f'{r["n_pairs"]:>4d} {r["win_rate"]*100:>5.0f}% '
              f'{r["empty_pct"]*100:>4.0f}%{tag}')

    # ================================================================
    # Report C: Pure Return Ranking TOP 25
    # ================================================================
    sorted_return = sorted(results, key=lambda x: x['total_return'], reverse=True)

    print(f'\n\n  [C] PURE RETURN RANKING - TOP 25')
    header = (f'  {"Rank":<4s} {"buy":>7s} {"sell":>7s} '
              f'{"TotRet":>8s} {"AnnRet":>8s} {"Sharpe":>7s} '
              f'{"MaxDD":>7s} {"Calmar":>7s} {"Trd":>4s} '
              f'{"Win":>6s} {"Cash":>5s}')
    print(header)
    print('  ' + '-' * 80)
    for rank, r in enumerate(sorted_return[:25], 1):
        tag = ' !A' if r['sell'] >= 0.085 else (' +' if r['sell'] >= 0.075 else '')
        print(f'  {rank:<4d} {r["buy"]:>+7.3f} {r["sell"]:>+7.3f} '
              f'{r["total_return"]*100:>7.2f}% '
              f'{r["annual_return"]*100:>7.2f}% '
              f'{r["sharpe"]:>7.3f} {r["max_drawdown"]*100:>6.2f}% '
              f'{r["calmar"]:>7.3f} {r["n_pairs"]:>4d} '
              f'{r["win_rate"]*100:>5.0f}% '
              f'{r["empty_pct"]*100:>4.0f}%{tag}')

    # ================================================================
    # Report D: Aggressive Sell (>= 8%) Special
    # ================================================================
    aggressive = [r for r in results if r['sell'] >= 0.080]
    aggr_sorted = sorted(aggressive, key=lambda x: x['stability'], reverse=True)

    print('\n\n' + '=' * 100)
    print('  [D] AGGRESSIVE SELL (sell >= 8%) - Stability TOP 20')
    print('=' * 100)

    header = (f'  {"Rank":<4s} {"buy":>7s} {"sell":>7s} '
              f'{"Sharpe":>7s} {"TotRet":>8s} {"AnnRet":>8s} '
              f'{"MaxDD":>7s} {"Calmar":>7s} {"Trd":>4s} '
              f'{"Win":>6s} {"Cash":>5s} {"HoldD":>6s} {"Stable":>8s}')
    print(header)
    print('  ' + '-' * 100)
    for rank, r in enumerate(aggr_sorted[:20], 1):
        print(f'  {rank:<4d} {r["buy"]:>+7.3f} {r["sell"]:>+7.3f} '
              f'{r["sharpe"]:>7.3f} {r["total_return"]*100:>7.2f}% '
              f'{r["annual_return"]*100:>7.2f}% '
              f'{r["max_drawdown"]*100:>6.2f}% {r["calmar"]:>7.3f} '
              f'{r["n_pairs"]:>4d} {r["win_rate"]*100:>5.0f}% '
              f'{r["empty_pct"]*100:>4.0f}% {r["holding_days"]:>6d} '
              f'{r["stability"]:>8.4f}')

    # ================================================================
    # Report E: sell=10% deep dive
    # ================================================================
    sell_10 = [r for r in results if abs(r['sell'] - 0.100) < 0.001]
    sell_10_sorted = sorted(sell_10, key=lambda x: x['stability'], reverse=True)

    print('\n\n' + '=' * 100)
    print('  [E] SELL=10% DEEP DIVE - All buy thresholds')
    print('=' * 100)

    header = (f'  {"buy":>7s} {"sell":>7s} '
              f'{"Sharpe":>7s} {"TotRet":>8s} {"AnnRet":>8s} '
              f'{"MaxDD":>7s} {"Calmar":>7s} {"Trd":>4s} '
              f'{"Win":>6s} {"Cash":>5s} {"HoldD":>6s} {"Stable":>8s}')
    print(header)
    print('  ' + '-' * 85)
    for r in sell_10_sorted:
        print(f'  {r["buy"]:>+7.3f} {r["sell"]:>+7.3f} '
              f'{r["sharpe"]:>7.3f} {r["total_return"]*100:>7.2f}% '
              f'{r["annual_return"]*100:>7.2f}% '
              f'{r["max_drawdown"]*100:>6.2f}% {r["calmar"]:>7.3f} '
              f'{r["n_pairs"]:>4d} {r["win_rate"]*100:>5.0f}% '
              f'{r["empty_pct"]*100:>4.0f}% {r["holding_days"]:>6d} '
              f'{r["stability"]:>8.4f}')

    # ================================================================
    # Report F: Key Sell Thresholds comparison
    # ================================================================
    print('\n\n' + '=' * 100)
    print('  [F] KEY SELL THRESHOLDS - Best buy for each sell')
    print('=' * 100)

    key_sells = [0.045, 0.050, 0.060, 0.070, 0.075, 0.080, 0.085, 0.090, 0.100, 0.110, 0.120]
    header = (f'  {"Scenario":<24s} {"buy":>7s} {"sell":>7s} '
              f'{"Sharpe":>7s} {"TotRet":>8s} {"AnnRet":>8s} '
              f'{"MaxDD":>7s} {"Calmar":>7s} {"Trd":>4s} '
              f'{"Win":>6s} {"Cash":>5s} {"HoldD":>6s}')
    print(header)
    print('  ' + '-' * 100)

    for ks in key_sells:
        candidates = [r for r in results if abs(r['sell'] - ks) < 0.001]
        if not candidates:
            continue
        best = max(candidates, key=lambda x: x['stability'])
        label_map = {
            0.045: '[SAFE]  sell=4.5%',
            0.050: '        sell=5.0%',
            0.060: '        sell=6.0%',
            0.070: '[FIRE]  sell=7.0%',
            0.075: '        sell=7.5%',
            0.080: '[AGGR]  sell=8.0%',
            0.085: '[MONEY] sell=8.5%',
            0.090: '        sell=9.0%',
            0.100: '[FAST]  sell=10%',
            0.110: '[EXTRE] sell=11%',
            0.120: '[MAX]   sell=12%',
        }
        label = label_map.get(ks, f'sell={ks:+.1%}')
        r = best
        print(f'  {label:<24s} {r["buy"]:>+7.3f} {r["sell"]:>+7.3f} '
              f'{r["sharpe"]:>7.3f} {r["total_return"]*100:>7.2f}% '
              f'{r["annual_return"]*100:>7.2f}% '
              f'{r["max_drawdown"]*100:>6.2f}% {r["calmar"]:>7.3f} '
              f'{r["n_pairs"]:>4d} {r["win_rate"]*100:>5.0f}% '
              f'{r["empty_pct"]*100:>4.0f}% {r["holding_days"]:>6d}')

    # ================================================================
    # Report G: TOP 5 Detailed Trade Logs
    # ================================================================
    print('\n\n' + '=' * 100)
    print('  [G] TOP 5 - DETAILED TRADE LOGS')
    print('=' * 100)

    for rank, r in enumerate(sorted_stable[:5], 1):
        sell_tag = ''
        if r['sell'] >= 0.100:
            sell_tag = ' !! EXTREME AGGRESSIVE'
        elif r['sell'] >= 0.085:
            sell_tag = ' ! AGGRESSIVE'
        elif r['sell'] >= 0.075:
            sell_tag = ' + MODERATE'
        elif r['sell'] <= 0.050:
            sell_tag = ' - CONSERVATIVE'

        print(f'\n  +{"="*90}+')
        print(f'  |  #{rank}  buy={r["buy"]:+.3f}  sell={r["sell"]:+.3f}  '
              f'Sharpe={r["sharpe"]:.4f}  Stability={r["stability"]:.4f}{sell_tag}  |')
        print(f'  +{"="*90}+')

        print(f'\n    Performance: TotRet {r["total_return"]*100:.2f}% | '
              f'AnnRet {r["annual_return"]*100:.2f}% | '
              f'Vol {r["volatility"]*100:.2f}%')
        print(f'    Risk: MaxDD {r["max_drawdown"]*100:.2f}% | '
              f'Calmar {r["calmar"]:.3f}')
        print(f'    Trading: {r["n_pairs"]} round trips | '
              f'WinRate {r["win_rate"]*100:.1f}% | '
              f'Cash {r["empty_pct"]*100:.1f}% ({r["empty_days"]}d) | '
              f'Hold {r["holding_days"]}d')

        if r['buy_sell_pairs']:
            print(f'\n    Trade Timeline:')
            print(f'    {"#":<3s} {"BuyDate":<12s} {"SellDate":<12s} {"ETF":<12s} '
                  f'{"Buy@":>8s} {"Sell@":>8s} {"Return":>8s} {"P&L":>12s}')
            print(f'    {"-"*75}')
            total_pnl = 0
            for i, pair in enumerate(r['buy_sell_pairs'], 1):
                total_pnl += pair.get('pnl', 0)
                print(f'    {i:<3d} {pair["buy_date"]:<12s} {pair["sell_date"]:<12s} '
                      f'{pair["etf"]:<12s} '
                      f'{pair["buy_price"]:>8.4f} {pair["sell_price"]:>8.4f} '
                      f'{pair["return"]*100:>7.2f}% {pair.get("pnl",0):>11,.0f}')

        if r['etf_stats']:
            print(f'\n    ETF Contributions:')
            print(f'    {"ETF":<12s} {"Buys":>4s} {"Sells":>4s} '
                  f'{"P&L%":>8s} {"WinRate":>7s}')
            print(f'    {"-"*40}')
            for name in etf_priority_order:
                s = r['etf_stats'][name]
                if s['n_buys'] + s['n_sells'] > 0:
                    print(f'    {name:<12s} {s["n_buys"]:>4d} {s["n_sells"]:>4d} '
                          f'{s["pnl_pct"]*100:>7.2f}% {s["win_rate"]*100:>6.0f}%')

    # ================================================================
    # Report H: Heatmap - sell=7% vs 8.5% vs 10% across all buys
    # ================================================================
    print('\n\n' + '=' * 100)
    print('  [H] HEATMAP: sell=7% vs 8.5% vs 10% across buy thresholds')
    print('=' * 100)

    for target_sell in [0.070, 0.085, 0.100]:
        subset = [r for r in results if abs(r['sell'] - target_sell) < 0.001]
        subset.sort(key=lambda x: x['buy'])
        label = {0.070: '7%', 0.085: '8.5%', 0.100: '10%'}[target_sell]
        print(f'\n  sell={label}:')
        header = (f'  {"buy":>8s}  {"Sharpe":>7s}  {"Ret":>8s}  '
                  f'{"MaxDD":>7s}  {"Trd":>4s}  {"Win":>6s}  {"Cash":>5s}  {"Hold":>6s}')
        print(header)
        print(f'  {"-"*70}')
        for r in subset:
            print(f'  {r["buy"]:>+8.3f}  {r["sharpe"]:>7.3f}  {r["total_return"]*100:>7.2f}%  '
                  f'{r["max_drawdown"]*100:>6.2f}%  {r["n_pairs"]:>4d}  '
                  f'{r["win_rate"]*100:>5.0f}%  {r["empty_pct"]*100:>4.0f}%  '
                  f'{r["holding_days"]:>6d}')

    # ================================================================
    # Report I: Ultimate Summary
    # ================================================================
    best_sharpe_overall = sorted_sharpe[0]
    best_return_overall = sorted_return[0]
    best_stable_overall = sorted_stable[0]

    best_aggr_sharpe = max(aggressive, key=lambda x: x['sharpe'])
    best_aggr_return = max(aggressive, key=lambda x: x['total_return'])

    print('\n\n' + '=' * 100)
    print('  [I] ULTIMATE SUMMARY')
    print('=' * 100)

    sep = "+---------------------------------------------------------------------------+"
    print()
    print(sep)
    print("|                        ALL PARAMETER SPACE BEST                           |")
    print(sep)
    bs = best_stable_overall; bsh = best_sharpe_overall; br = best_return_overall
    print(f'|  [GOLD] Best Overall: buy={bs["buy"]:+.3f}  sell={bs["sell"]:+.3f}  '
          f'S={bs["sharpe"]:.4f}  Ret={bs["total_return"]*100:.2f}%  '
          f'DD={bs["max_drawdown"]*100:.2f}%  Trd={bs["n_pairs"]}  Win={bs["win_rate"]*100:.0f}%')
    print(f'|  Best Sharpe: buy={bsh["buy"]:+.3f}  sell={bsh["sell"]:+.3f}  '
          f'S={bsh["sharpe"]:.4f}  Ret={bsh["total_return"]*100:.2f}%  '
          f'DD={bsh["max_drawdown"]*100:.2f}%  Trd={bsh["n_pairs"]}  Win={bsh["win_rate"]*100:.0f}%')
    print(f'|  Best Return: buy={br["buy"]:+.3f}  sell={br["sell"]:+.3f}  '
          f'S={br["sharpe"]:.4f}  Ret={br["total_return"]*100:.2f}%  '
          f'DD={br["max_drawdown"]*100:.2f}%  Trd={br["n_pairs"]}  Win={br["win_rate"]*100:.0f}%')
    print(sep)
    print("|                        AGGRESSIVE ZONE (sell >= 8%)                       |")
    print(sep)
    bas = best_aggr_sharpe; bar2 = best_aggr_return
    print(f'|  Best Sharpe: buy={bas["buy"]:+.3f}  sell={bas["sell"]:+.3f}  '
          f'S={bas["sharpe"]:.4f}  Ret={bas["total_return"]*100:.2f}%  '
          f'DD={bas["max_drawdown"]*100:.2f}%  Trd={bas["n_pairs"]}  Win={bas["win_rate"]*100:.0f}%')
    print(f'|  Best Return: buy={bar2["buy"]:+.3f}  sell={bar2["sell"]:+.3f}  '
          f'S={bar2["sharpe"]:.4f}  Ret={bar2["total_return"]*100:.2f}%  '
          f'DD={bar2["max_drawdown"]*100:.2f}%  Trd={bar2["n_pairs"]}  Win={bar2["win_rate"]*100:.0f}%')
    print(sep)

    # ================================================================
    # Final Comparison Table
    # ================================================================
    print('=' * 100)
    print('  [TIP] FINAL RECOMMENDATION')
    print('=' * 100)

    sell7_best = max([r for r in results if abs(r['sell'] - 0.070) < 0.001],
                     key=lambda x: x['stability'])
    sell8_5_best = max([r for r in results if abs(r['sell'] - 0.085) < 0.001],
                        key=lambda x: x['stability'])
    sell10_best = max([r for r in results if abs(r['sell'] - 0.100) < 0.001],
                       key=lambda x: x['stability'])

    print()
    print(f'  Based on {n_total} parameter combinations tested:')
    print()
    print('  +----------+----------+----------+----------+----------+----------+----------+')
    print('  |                         SCENARIO COMPARISON                                |')
    print('  +----------+----------+----------+----------+----------+----------+----------+')
    print('  |  Sell    | Best Buy | Sharpe   | TotRet   | AnnRet   | MaxDD    | Trades   |')
    print('  +----------+----------+----------+----------+----------+----------+----------+')
    s7 = sell7_best; s85 = sell8_5_best; s10 = sell10_best
    print(f'  |  sell=7%  | {s7["buy"]:+.3f}    | {s7["sharpe"]:.4f}   | {s7["total_return"]*100:>6.1f}%   | {s7["annual_return"]*100:>6.1f}%  | {s7["max_drawdown"]*100:>6.1f}%  |  {s7["n_pairs"]:>5d}   |')
    print(f'  | sell=8.5% | {s85["buy"]:+.3f}    | {s85["sharpe"]:.4f}   | {s85["total_return"]*100:>6.1f}%   | {s85["annual_return"]*100:>6.1f}%  | {s85["max_drawdown"]*100:>6.1f}%  |  {s85["n_pairs"]:>5d}   |')
    print(f'  | sell=10%  | {s10["buy"]:+.3f}    | {s10["sharpe"]:.4f}   | {s10["total_return"]*100:>6.1f}%   | {s10["annual_return"]*100:>6.1f}%  | {s10["max_drawdown"]*100:>6.1f}%  |  {s10["n_pairs"]:>5d}   |')
    print('  +----------+----------+----------+----------+----------+----------+----------+')

    print('  [WARN] Notes:')
    print('  - Higher sell threshold = fewer trades but potentially bigger wins')
    print('  - sell > 8.5%: Sharpe typically decays as medium moves fail to trigger')
    print('  - The "optimal" depends on your DD tolerance and trade frequency preference')
    print('  - KCBanDaoTi only listed 2025-04, limited data (~1yr), high vol may skew results')

    print('\n  Sweep complete!')
    print(f'  Total: {n_total} parameter combinations tested')


if __name__ == '__main__':
    main()
