"""
5只ETF 均线偏离策略 最优化计算
==============================================
策略:
  买入信号: (c - ma5) / |ma5| < -0.08   (价格低于MA5超过8%)
  卖出信号: (c - ma5) / |ma5| >  0.08   (价格高于MA5超过8%)

ETF池:
  1. 科创50ETF南方     588150 (SH)
  2. 科创半导体ETF华夏   588170 (SH)
  3. 华宝中证银行ETF     512800 (SH)
  4. 华宝中证医疗ETF     512170 (SH)
  5. 华泰柏瑞沪深300ETF  510300 (SH)

数据源: 腾讯财经
日期范围: 2024-01-01 → 2026-07-01

最优化目标:
  - 枚举全部 2^5-1=31 种组合
  - 等权 & 最优权重 (Max Sharpe)
  - 排序输出最优解
"""

import sys
import io
# 强制 UTF-8 输出，解决 Windows GBK 编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import urllib.request
import json
import math
import itertools
import time
from datetime import datetime, timedelta
from collections import defaultdict

# ============================================================
# 配置
# ============================================================
ETF_CONFIG = {
    '科创50ETF南方':    {'code': 'sh588150', 'name': '科创50ETF南方'},
    '科创半导体ETF华夏':  {'code': 'sh588170', 'name': '科创半导体ETF华夏'},
    '华宝中证银行ETF':    {'code': 'sh512800', 'name': '华宝中证银行ETF'},
    '华宝中证医疗ETF':    {'code': 'sh512170', 'name': '华宝中证医疗ETF'},
    '华泰柏瑞沪深300ETF': {'code': 'sh510300', 'name': '华泰柏瑞沪深300ETF'},
}

START_DATE = '2024-01-01'
END_DATE   = '2026-07-01'

BUY_THRESHOLD  = -0.04   # (c-ma5)/|ma5| < -0.04 → 买入
SELL_THRESHOLD =  0.05   # (c-ma5)/|ma5| >  0.05 → 卖出

RISK_FREE_RATE = 0.025   # 无风险利率 (假设2.5%)
TRADING_DAYS   = 252

# ============================================================
# 1. 数据获取 - 腾讯财经 API
# ============================================================
def fetch_tencent_kline(code: str, start: str, end: str) -> dict:
    """
    腾讯财经 K线数据接口
    URL: http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?
         param=<code>,day,<start>,<end>,<count>,qfq
    返回前复权日线数据
    """
    url = (
        f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
        f'?param={code},day,{start},{end},640,qfq'
    )
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://gu.qq.com/',
    }

    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            return data
        except Exception as e:
            print(f'  ⚠ 第{attempt+1}次请求失败: {e}')
            time.sleep(1)
    return {}


def parse_kline_data(raw: dict, code: str) -> list[dict]:
    """解析腾讯财经返回的K线数据"""
    try:
        if code.startswith('sh'):
            key = code  # sh510300
        else:
            key = code  # sz159780

        # 尝试多种路径
        days = None
        if 'data' in raw and key in raw['data']:
            stock_data = raw['data'][key]
            if 'qfqday' in stock_data:
                days = stock_data['qfqday']
            elif 'day' in stock_data:
                days = stock_data['day']
        elif 'data' in raw:
            # 尝试第一个key
            for k in raw['data']:
                if isinstance(raw['data'][k], dict):
                    if 'qfqday' in raw['data'][k]:
                        days = raw['data'][k]['qfqday']
                        break
                    elif 'day' in raw['data'][k]:
                        days = raw['data'][k]['day']
                        break

        if not days:
            return []

        result = []
        for d in days:
            # 格式: [日期, 开盘, 收盘, 最高, 最低, 成交量]
            if len(d) >= 6:
                result.append({
                    'date':   str(d[0]),
                    'open':   float(d[1]),
                    'close':  float(d[2]),
                    'high':   float(d[3]),
                    'low':    float(d[4]),
                    'volume': float(d[5]),
                })
        return result
    except Exception as e:
        print(f'  ❌ 解析数据失败: {e}')
        return []


# ============================================================
# 2. 信号计算
# ============================================================
def calc_ma(data: list[float], window: int = 5) -> list[float]:
    """计算移动平均线 (不足window返回NaN)"""
    ma = []
    for i in range(len(data)):
        if i < window - 1:
            ma.append(float('nan'))
        else:
            ma.append(sum(data[i - window + 1:i + 1]) / window)
    return ma


def generate_signals(bars: list[dict]) -> list[dict]:
    """
    为每根K线生成买卖信号
    信号: (close - ma5) / |ma5|
    """
    closes = [b['close'] for b in bars]
    ma5 = calc_ma(closes, 5)

    for i, bar in enumerate(bars):
        bar['ma5'] = ma5[i]
        if math.isnan(ma5[i]) or ma5[i] == 0:
            bar['deviation'] = float('nan')
            bar['signal'] = 'hold'
        else:
            bar['deviation'] = (bar['close'] - ma5[i]) / abs(ma5[i])
            if bar['deviation'] < BUY_THRESHOLD:
                bar['signal'] = 'buy'
            elif bar['deviation'] > SELL_THRESHOLD:
                bar['signal'] = 'sell'
            else:
                bar['signal'] = 'hold'
    return bars


# ============================================================
# 3. 单ETF回测
# ============================================================
def backtest_single(bars: list[dict], name: str, init_capital: float = 1_000_000
                    ) -> dict:
    """
    对单只ETF回测均线偏离策略
    逻辑:
      - buy 信号 + 空仓 → 全仓买入 (按收盘价)
      - sell 信号 + 持仓 → 全仓卖出 (按收盘价)
      - 连续买入信号只执行第一次，卖出后等待下一次买入
    """
    position = 0.0       # 持仓份额
    cash = init_capital  # 现金
    trades = []          # 交易记录
    daily_values = []    # 每日总资产

    for i, bar in enumerate(bars):
        signal = bar['signal']
        price = bar['close']

        if signal == 'buy' and position == 0 and cash > 0:
            # 全仓买入
            position = cash / price
            cash = 0.0
            trades.append({
                'date': bar['date'],
                'action': 'buy',
                'price': price,
                'shares': position,
                'value': position * price,
            })

        elif signal == 'sell' and position > 0:
            # 全仓卖出
            cash = position * price
            trades.append({
                'date': bar['date'],
                'action': 'sell',
                'price': price,
                'shares': position,
                'value': cash,
            })
            position = 0.0

        # 每日总资产
        total_value = cash + position * price
        daily_values.append({
            'date': bar['date'],
            'value': total_value,
            'price': price,
            'position': position,
        })

    # 最后如果还持仓，按最后价格平仓
    if position > 0:
        final_price = bars[-1]['close']
        cash = position * final_price
        trades.append({
            'date': bars[-1]['date'],
            'action': 'sell_final',
            'price': final_price,
            'shares': position,
            'value': cash,
        })
        position = 0.0

    final_value = cash

    # 计算日收益率序列
    returns = []
    for i in range(1, len(daily_values)):
        r = (daily_values[i]['value'] - daily_values[i-1]['value']
             ) / daily_values[i-1]['value']
        returns.append(r)

    # 计算回撤序列
    peak = daily_values[0]['value']
    max_dd = 0.0
    dd_dates = ('', '')
    for dv in daily_values:
        if dv['value'] > peak:
            peak = dv['value']
        dd = (peak - dv['value']) / peak
        if dd > max_dd:
            max_dd = dd
            dd_dates = (dv['date'], dv['date'])

    # 绩效指标
    total_return = (final_value - init_capital) / init_capital
    annual_return = (1 + total_return) ** (TRADING_DAYS / max(len(returns), 1)) - 1

    if len(returns) > 1:
        mean_ret = sum(returns) / len(returns)
        std_ret = (sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5
        sharpe = ((mean_ret * TRADING_DAYS - RISK_FREE_RATE)
                   / (std_ret * math.sqrt(TRADING_DAYS))) if std_ret > 0 else 0
        # 索提诺比率 (只考虑下行风险)
        downside_returns = [r for r in returns if r < 0]
        if downside_returns:
            downside_std = (sum((r - mean_ret) ** 2 for r in downside_returns)
                            / len(downside_returns)) ** 0.5
            sortino = ((mean_ret * TRADING_DAYS - RISK_FREE_RATE)
                        / (downside_std * math.sqrt(TRADING_DAYS))) if downside_std > 0 else 0
        else:
            sortino = float('inf')
    else:
        std_ret = mean_ret = sharpe = sortino = 0.0

    calmar = annual_return / max_dd if max_dd > 0 else float('inf')

    # 胜率
    wins = sum(1 for t in trades if t['action'] in ('sell', 'sell_final')
               and t['value'] > 0)
    win_rate = 0
    sell_trades = [t for t in trades if t['action'] in ('sell', 'sell_final')]
    buy_trades  = [t for t in trades if t['action'] == 'buy']
    if len(sell_trades) > 0 and len(buy_trades) > 0:
        # 比较每对买卖
        pairs = min(len(buy_trades), len(sell_trades))
        win_count = 0
        for j in range(pairs):
            if sell_trades[j]['price'] > buy_trades[j]['price']:
                win_count += 1
        win_rate = win_count / pairs

    # 交易次数
    n_trades = len([t for t in trades if t['action'] in ('buy', 'sell')])

    return {
        'name': name,
        'init_capital': init_capital,
        'final_value': final_value,
        'total_return': total_return,
        'annual_return': annual_return,
        'volatility': std_ret * math.sqrt(TRADING_DAYS),
        'sharpe': sharpe,
        'sortino': sortino,
        'calmar': calmar,
        'max_drawdown': max_dd,
        'win_rate': win_rate,
        'n_trades': n_trades,
        'trades': trades,
        'daily_values': daily_values,
        'daily_returns': returns,
    }


# ============================================================
# 4. 组合回测 (多ETF等权)
# ============================================================
def backtest_portfolio(etf_results: list[dict], weights: list[float] = None
                       ) -> dict:
    """
    对多只ETF的组合进行回测
    每只ETF独立执行策略，组合按权重分配资金

    关键修正: 截断到所有ETF共同的日期范围，避免不同上市日期
    导致的回撤计算失真
    """
    n = len(etf_results)
    if weights is None:
        weights = [1.0 / n] * n

    # ---- 找出共同日期范围 ----
    # 所有ETF的日期集合
    all_date_sets = []
    for res in etf_results:
        dates = set(dv['date'] for dv in res['daily_values'])
        all_date_sets.append(dates)

    # 交集 = 所有ETF都有数据的日期
    common_dates = all_date_sets[0]
    for ds in all_date_sets[1:]:
        common_dates = common_dates & ds

    common_dates = sorted(common_dates)
    if len(common_dates) < 2:
        return {
            'total_return': 0, 'annual_return': 0, 'volatility': 0,
            'sharpe': 0, 'calmar': 0, 'max_drawdown': 0,
            'weights': weights, 'n_etfs': n,
        }

    # ---- 构建每只ETF在共同日期上的归一化价值序列 ----
    # 提前建立日期→价值的映射
    value_maps = []
    for res in etf_results:
        vmap = {dv['date']: dv['value'] / res['init_capital']
                for dv in res['daily_values']}
        value_maps.append(vmap)

    # ---- 计算组合每日归一化价值 ----
    portfolio_values = []
    for d in common_dates:
        total = sum(w * vmap.get(d, vmap[d]) for w, vmap in zip(weights, value_maps))
        portfolio_values.append({'date': d, 'value': total})

    # ---- 计算收益与风险指标 ----
    init_value = portfolio_values[0]['value']
    final_value = portfolio_values[-1]['value']

    # 在截断的日期范围内重新计算收益率
    total_return = (final_value - init_value) / init_value

    returns = []
    for i in range(1, len(portfolio_values)):
        prev = portfolio_values[i-1]['value']
        curr = portfolio_values[i]['value']
        if prev > 0:
            returns.append((curr - prev) / prev)
        else:
            returns.append(0.0)

    peak = init_value
    max_dd = 0.0
    for pv in portfolio_values:
        if pv['value'] > peak:
            peak = pv['value']
        dd = (peak - pv['value']) / peak
        if dd > max_dd:
            max_dd = dd

    n_days = len(returns)
    annual_return = ((final_value / init_value) ** (TRADING_DAYS / max(n_days, 1)) - 1
                     if init_value > 0 else 0)

    if n_days > 1:
        mean_ret = sum(returns) / n_days
        variance = sum((r - mean_ret) ** 2 for r in returns) / (n_days - 1)
        std_ret = variance ** 0.5
        annual_std = std_ret * math.sqrt(TRADING_DAYS)
        sharpe = ((mean_ret * TRADING_DAYS - RISK_FREE_RATE) / annual_std
                  if annual_std > 0 else 0)
    else:
        annual_std = mean_ret = sharpe = 0.0

    calmar = annual_return / max_dd if max_dd > 0 else float('inf')

    return {
        'total_return': total_return,
        'annual_return': annual_return,
        'volatility': annual_std,
        'sharpe': sharpe,
        'calmar': calmar,
        'max_drawdown': max_dd,
        'weights': weights,
        'n_etfs': n,
    }


# ============================================================
# 5. 权重最优化 (Max Sharpe, scipy)
# ============================================================
def optimize_weights(etf_results: list[dict]) -> dict:
    """
    使用二次规划优化组合权重，最大化夏普比率
    """
    try:
        from scipy.optimize import minimize
    except ImportError:
        return {'weights': None, 'sharpe': 0, 'note': 'scipy未安装'}

    n = len(etf_results)
    if n <= 1:
        return {'weights': [1.0], 'sharpe': etf_results[0]['sharpe']}

    # 提取对齐的日收益率
    returns_matrix = []
    min_len = min(len(r['daily_returns']) for r in etf_results)
    for i, res in enumerate(etf_results):
        returns_matrix.append(res['daily_returns'][-min_len:])

    # 年化收益率和协方差矩阵
    annual_returns = []
    for rets in returns_matrix:
        mean_daily = sum(rets) / len(rets) if rets else 0
        annual_returns.append(mean_daily * TRADING_DAYS)

    # 协方差矩阵
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                mean_i = sum(returns_matrix[i]) / len(returns_matrix[i])
                cov[i][i] = (sum((r - mean_i) ** 2 for r in returns_matrix[i])
                             / (len(returns_matrix[i]) - 1))
            else:
                mean_i = sum(returns_matrix[i]) / len(returns_matrix[i])
                mean_j = sum(returns_matrix[j]) / len(returns_matrix[j])
                cov[i][j] = (sum((returns_matrix[i][k] - mean_i)
                                 * (returns_matrix[j][k] - mean_j)
                                 for k in range(min_len))
                             / (min_len - 1))

    # 负夏普比率 (最小化)
    def neg_sharpe(w):
        port_ret = sum(w[i] * annual_returns[i] for i in range(n))
        port_var = sum(w[i] * w[j] * cov[i][j] * TRADING_DAYS
                       for i in range(n) for j in range(n))
        port_std = port_var ** 0.5
        if port_std < 1e-10:
            return 1e10
        return -(port_ret - RISK_FREE_RATE) / port_std

    # 约束: sum(w) = 1, w_i >= 0
    constraints = [{'type': 'eq', 'fun': lambda w: sum(w) - 1}]
    bounds = [(0, 1) for _ in range(n)]

    # 多个起点
    best_w = None
    best_val = 1e10
    starts = [
        [1.0 / n] * n,  # 等权
        [1.0] + [0.0] * (n - 1),  # 只买第1只
    ]
    for i in range(n):
        w = [0.0] * n
        w[i] = 1.0
        starts.append(w)

    for start in starts:
        result = minimize(neg_sharpe, start, method='SLSQP',
                          bounds=bounds, constraints=constraints,
                          options={'maxiter': 1000, 'ftol': 1e-12})
        if result.fun < best_val:
            best_val = result.fun
            best_w = result.x.tolist()

    # 过滤极小权重
    best_w = [w if w > 0.001 else 0.0 for w in best_w]
    # 重新归一化
    total = sum(best_w)
    if total > 0:
        best_w = [w / total for w in best_w]

    opt_sharpe = -best_val

    # 用最优权重回测
    port_result = backtest_portfolio(etf_results, best_w)

    return {
        'weights': best_w,
        'sharpe': opt_sharpe,
        'total_return': port_result['total_return'],
        'annual_return': port_result['annual_return'],
        'volatility': port_result['volatility'],
        'calmar': port_result['calmar'],
        'max_drawdown': port_result['max_drawdown'],
    }


# ============================================================
# 6. 主程序
# ============================================================
def main():
    print('=' * 72)
    print('  5 ETF 均线偏离策略 · 最优组合计算')
    print('  策略: (c-MA5)/|MA5| < -8% 买入, > +8% 卖出')
    print(f'  数据: 腾讯财经  |  {START_DATE} → {END_DATE}')
    print('=' * 72)

    # ---- Step 1: 获取数据 ----
    print('\n📡 [1/4] 获取行情数据...')
    all_bars = {}
    for name, cfg in ETF_CONFIG.items():
        code = cfg['code']
        print(f'  ↓ {name} ({code}) ...', end=' ', flush=True)
        raw = fetch_tencent_kline(code, START_DATE, END_DATE)
        bars = parse_kline_data(raw, code)
        if bars:
            bars = generate_signals(bars)
            all_bars[name] = bars
            print(f'✅ {len(bars)} 条K线  '
                  f'({bars[0]["date"]} ~ {bars[-1]["date"]})')
        else:
            print(f'❌ 无数据')
        time.sleep(0.3)

    if not all_bars:
        print('\n❌ 所有ETF数据获取失败，请检查网络或代码')
        return

    print(f'\n  成功获取 {len(all_bars)}/{len(ETF_CONFIG)} 只ETF数据')

    # ---- Step 2: 单ETF回测 ----
    print('\n📊 [2/4] 单ETF策略回测...')
    individual_results = {}
    for name, bars in all_bars.items():
        result = backtest_single(bars, name)
        individual_results[name] = result

    # 打印单ETF结果
    print(f'\n  {"ETF名称":<16s} {"总收益%":>8s} {"年化%":>8s} '
          f'{"波动率%":>8s} {"夏普":>7s} {"卡玛":>7s} '
          f'{"最大回撤%":>9s} {"交易次":>6s} {"胜率%":>7s}')
    print('  ' + '-' * 80)
    sorted_single = sorted(individual_results.values(),
                           key=lambda x: x['sharpe'], reverse=True)
    for r in sorted_single:
        print(f'  {r["name"]:<16s} '
              f'{r["total_return"]*100:>7.2f} '
              f'{r["annual_return"]*100:>7.2f} '
              f'{r["volatility"]*100:>7.2f} '
              f'{r["sharpe"]:>7.3f} '
              f'{r["calmar"]:>7.3f} '
              f'{r["max_drawdown"]*100:>8.2f} '
              f'{r["n_trades"]:>6d} '
              f'{r["win_rate"]*100:>6.1f}')

    # ---- 信号统计 ----
    print(f'\n  📶 信号触发统计')
    print(f'  {"ETF名称":<16s} {"买入信号":>8s} {"卖出信号":>8s} '
          f'{"平均偏离%":>10s} {"最小偏离%":>10s} {"最大偏离%":>10s}')
    print('  ' + '-' * 68)
    for name, bars in all_bars.items():
        buy_count = sum(1 for b in bars if b['signal'] == 'buy')
        sell_count = sum(1 for b in bars if b['signal'] == 'sell')
        deviations = [b['deviation'] for b in bars
                      if not math.isnan(b['deviation'])]
        if deviations:
            avg_dev = sum(deviations) / len(deviations)
            min_dev = min(deviations)
            max_dev = max(deviations)
        else:
            avg_dev = min_dev = max_dev = 0
        print(f'  {name:<16s} {buy_count:>8d} {sell_count:>8d} '
              f'{avg_dev*100:>9.2f}% {min_dev*100:>9.2f}% {max_dev*100:>9.2f}%')

    # ---- Step 3: 枚举全部组合 ----
    print('\n🔍 [3/4] 枚举全部 31 种组合...')
    etf_names = list(all_bars.keys())

    all_combos = []
    for k in range(1, len(etf_names) + 1):
        for combo in itertools.combinations(etf_names, k):
            # 等权组合
            etf_res = [individual_results[n] for n in combo]
            port = backtest_portfolio(etf_res)
            all_combos.append({
                'etfs': combo,
                'n': k,
                'type': 'equal_weight',
                **port,
            })

            # 最优权重组合
            opt = optimize_weights(etf_res)
            if opt['weights'] is not None:
                all_combos.append({
                    'etfs': combo,
                    'n': k,
                    'type': 'optimized',
                    'opt_weights': opt['weights'],
                    'total_return': opt['total_return'],
                    'annual_return': opt['annual_return'],
                    'volatility': opt['volatility'],
                    'sharpe': opt['sharpe'],
                    'calmar': opt['calmar'],
                    'max_drawdown': opt['max_drawdown'],
                })

    # ---- Step 4: 排序输出最优解 ----
    print('\n🏆 [4/4] 最优解排名')

    # 按夏普排序
    combos_by_sharpe = sorted(all_combos, key=lambda x: x['sharpe'], reverse=True)

    print(f'\n  ◆ 按夏普比率排名 (TOP 15)')
    print(f'  {"排名":<4s} {"组合":<40s} '
          f'{"权重":>8s} {"总收益%":>8s} {"夏普":>7s} '
          f'{"卡玛":>7s} {"回撤%":>7s}')
    print('  ' + '-' * 85)
    for rank, c in enumerate(combos_by_sharpe[:15], 1):
        etf_str = ' + '.join(c['etfs'])
        if len(etf_str) > 38:
            etf_str = etf_str[:35] + '...'
        weight_str = c.get('type', '')
        if c.get('type') == 'optimized':
            weight_str = '最优'
        else:
            weight_str = '等权'
        print(f'  {rank:<4d} {etf_str:<40s} '
              f'{weight_str:>8s} '
              f'{c["total_return"]*100:>7.2f} '
              f'{c["sharpe"]:>7.3f} '
              f'{c["calmar"]:>7.3f} '
              f'{c["max_drawdown"]*100:>7.2f}')

    # 按总收益排序
    combos_by_return = sorted(all_combos, key=lambda x: x['total_return'], reverse=True)

    print(f'\n  ◆ 按总收益率排名 (TOP 15)')
    print(f'  {"排名":<4s} {"组合":<40s} '
          f'{"权重":>8s} {"总收益%":>8s} {"夏普":>7s} '
          f'{"卡玛":>7s} {"回撤%":>7s}')
    print('  ' + '-' * 85)
    for rank, c in enumerate(combos_by_return[:15], 1):
        etf_str = ' + '.join(c['etfs'])
        if len(etf_str) > 38:
            etf_str = etf_str[:35] + '...'
        weight_str = '最优' if c.get('type') == 'optimized' else '等权'
        print(f'  {rank:<4d} {etf_str:<40s} '
              f'{weight_str:>8s} '
              f'{c["total_return"]*100:>7.2f} '
              f'{c["sharpe"]:>7.3f} '
              f'{c["calmar"]:>7.3f} '
              f'{c["max_drawdown"]*100:>7.2f}')

    # ---- 推荐最优解 ----
    print('\n' + '=' * 72)
    print('  🎯 最优推荐')
    print('=' * 72)

    best_sharpe = combos_by_sharpe[0]
    best_return = combos_by_return[0]

    print(f'\n  📈 最高夏普比率组合:')
    print(f'      ETF: {" + ".join(best_sharpe["etfs"])}')
    print(f'      权重: {best_sharpe.get("type", "等权")}')
    if best_sharpe.get('opt_weights'):
        tw = list(zip(best_sharpe['etfs'], best_sharpe['opt_weights']))
        for name, w in tw:
            if w > 0.001:
                print(f'        - {name}: {w*100:.1f}%')
    print(f'      夏普比率: {best_sharpe["sharpe"]:.4f}')
    print(f'      总收益率: {best_sharpe["total_return"]*100:.2f}%')
    print(f'      年化收益: {best_sharpe["annual_return"]*100:.2f}%')
    print(f'      最大回撤: {best_sharpe["max_drawdown"]*100:.2f}%')
    print(f'      卡玛比率: {best_sharpe["calmar"]:.4f}')

    print(f'\n  💰 最高总收益组合:')
    print(f'      ETF: {" + ".join(best_return["etfs"])}')
    print(f'      权重: {best_return.get("type", "等权")}')
    if best_return.get('opt_weights'):
        tw = list(zip(best_return['etfs'], best_return['opt_weights']))
        for name, w in tw:
            if w > 0.001:
                print(f'        - {name}: {w*100:.1f}%')
    print(f'      总收益率: {best_return["total_return"]*100:.2f}%')
    print(f'      年化收益: {best_return["annual_return"]*100:.2f}%')
    print(f'      夏普比率: {best_return["sharpe"]:.4f}')
    print(f'      最大回撤: {best_return["max_drawdown"]*100:.2f}%')

    # ---- Step 5: 输出详细的买入卖出信号时间表 ----
    print('\n' + '=' * 72)
    print('  📋 最优组合详细交易记录')
    print('=' * 72)

    # 取最优夏普组合的ETF，输出每只的交易记录
    for name in best_sharpe['etfs']:
        r = individual_results[name]
        print(f'\n  【{name}】')
        print(f'    总收益: {r["total_return"]*100:.2f}%  |  '
              f'夏普: {r["sharpe"]:.3f}  |  '
              f'交易{r["n_trades"]}次  |  '
              f'胜率{r["win_rate"]*100:.1f}%')
        print(f'    {"日期":<12s} {"操作":<6s} {"价格":>8s} '
              f'{"数量":>10s} {"金额":>12s}')
        print(f'    {"-"*50}')
        for t in r['trades']:
            action_cn = {'buy': '🟢 买入', 'sell': '🔴 卖出',
                         'sell_final': '⚪ 平仓'}.get(t['action'], t['action'])
            print(f'    {t["date"]:<12s} {action_cn:<6s} '
                  f'{t["price"]:>8.3f} {t["shares"]:>10.0f} '
                  f'{t["value"]:>12.0f}')

    print('\n' + '=' * 72)
    print('  计算完成 ✅')
    print('=' * 72)


if __name__ == '__main__':
    main()
