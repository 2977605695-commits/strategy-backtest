"""
V10 终极版 策略核心逻辑（平台无关）
=====================================
终极版改进：
  - 快线 MA4-6均值 / 慢线 MA14-16均值（消除尖峰）
  - MA7/8/9 三周期投票（消除MA8尖峰）
  - MA95 年线过滤（替代MA60，33+89池验证最优）
  - ATR 2.5倍自适应Trail（替代固定3%/6%）
  - 百分比池切换（净值8%，替代固定100万）+ 冷却3天
  - 熊市MH=0 / pullback=0（蒙特卡洛验证删除）
  - ETF免印花税（修正成本）

被 qmt_strategy.py / ptrade_strategy.py 引用。
"""

import math
from datetime import datetime
from typing import Dict, List, Tuple, Optional


def calc_ma(data: List[float], window: int) -> List[float]:
    """移动平均，右对齐，前 window-1 个为 NaN"""
    result = [float('nan')] * (window - 1)
    for i in range(window - 1, len(data)):
        result.append(sum(data[i - window + 1:i + 1]) / window)
    return result


def calc_slope(ma_series: List[float], lookback: int) -> List[float]:
    """计算 MA 系列的斜率（归一化）"""
    slopes = [float('nan')] * len(ma_series)
    for i in range(len(ma_series)):
        if i < lookback:
            continue
        ys = ma_series[i - lookback + 1:i + 1]
        if any(math.isnan(y) for y in ys):
            continue
        n = len(ys)
        sx = sy = sxy = sxx = 0
        for j, y in enumerate(ys):
            sx += j; sy += y; sxy += j * y; sxx += j * j
        denom = n * sxx - sx * sx
        if denom > 0 and ma_series[i] > 0:
            slopes[i] = (n * sxy - sx * sy) / denom / ma_series[i]
    return slopes


def calc_atr(highs: List[float], lows: List[float], closes: List[float], window: int = 14) -> List[float]:
    """计算 ATR (Average True Range)"""
    tr = [0.0]
    for i in range(1, len(closes)):
        if math.isnan(closes[i]) or math.isnan(closes[i - 1]):
            tr.append(0.0)
            continue
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))
    return calc_ma(tr, window)


def calc_ma_avg(closes: List[float], periods: List[int], idx: int) -> float:
    """计算多个MA周期的均值（区间均线），idx是当前索引"""
    vals = []
    for w in periods:
        ma_w = calc_ma(closes, w)
        if idx < len(ma_w) and not math.isnan(ma_w[idx]):
            vals.append(ma_w[idx])
    return sum(vals) / len(vals) if vals else float('nan')


class StrategyCore:
    """V10 终极版 策略核心逻辑，平台无关"""

    def __init__(self, config: dict):
        strat = config['strategy']
        # ★ 终极版参数
        self.fast_range = strat.get('fast_range', [4, 5, 6])      # 快线区间
        self.slow_range = strat.get('slow_range', [14, 15, 16])   # 慢线区间
        self.vote_ma = strat.get('vote_ma', [7, 8, 9])             # 动能投票周期
        self.slope_lb = strat.get('slope_lookback', 4)             # 斜率回看
        self.ma_filter = strat.get('ma_filter', 95)                # 年线过滤周期
        self.atr_mult = strat.get('atr_mult', 2.5)                 # ATR Trail倍数
        self.atr_w = strat.get('atr_window', 14)                   # ATR周期
        self.atr_min = strat.get('atr_min', 0.02)                  # Trail下限
        self.atr_max = strat.get('atr_max', 0.08)                  # Trail上限
        # 池切换
        self.loss_pct = strat.get('loss_pct', 0.08)                # 百分比阈值
        self.cooldown = strat.get('cooldown_days', 3)               # 冷却期
        self.need_n = strat.get('need_n_index', 7)
        # 删除的参数（保留兼容）
        self.pullback_min = 0.0   # 已删除
        self.bear_mh = 0          # 已删除
        self.risk_free = strat.get('risk_free', 0.025)

        self.full_pool = config['pool']['full']
        self.defensive_pool = config['pool']['defensive']
        self.index_codes = config['pool']['index_codes']

        # 成本（ETF免印花税）
        exe = config.get('execution', {})
        self.slippage = exe.get('slippage', 0.0003)
        self.buy_fee = exe.get('buy_fee', 0.00025)
        self.sell_fee = exe.get('sell_fee', 0.00025)
        self.stamp_tax = 0.0  # ETF免征

        # 运行时状态
        self.position = None
        self.pool_mode = 'all'
        self.rolling_pnl = []
        self.trade_count = 0
        self.last_trade_date = None
        self.last_switch_date = None  # 上次池切换日期（冷却期用）

    def compute_signals(self, bars: List[dict], code: str) -> dict:
        """
        计算单个ETF的信号数据（终极版：区间均值+投票+MA95）
        bars: [{'date': str, 'close': float, 'high': float, 'low': float}, ...]
        """
        closes = [b['close'] for b in bars]
        highs = [b['high'] for b in bars]
        lows = [b.get('low', b['close']) for b in bars]
        dates = [b['date'] for b in bars]
        n = len(bars)

        # 预计算所有MA
        ma_cache = {}
        for w in set(self.fast_range + self.slow_range + self.vote_ma + [self.ma_filter, 60]):
            ma_cache[w] = calc_ma(closes, w)

        # 预计算投票MA的斜率
        slo_cache = {}
        for sw in self.vote_ma:
            slo_cache[sw] = calc_slope(ma_cache[sw], self.slope_lb)

        # ATR
        atr_series = calc_atr(highs, lows, closes, self.atr_w)

        trend = {}
        ratio = {}
        above_filter = {}
        atr_val = {}
        etf_highs = {}

        for i in range(n):
            d = dates[i]
            # T-1 信号（用 i-1）
            if i > 0:
                # ★ 区间均值快线/慢线
                fv = [ma_cache[w][i - 1] for w in self.fast_range
                      if not math.isnan(ma_cache[w][i - 1])]
                sv = [ma_cache[w][i - 1] for w in self.slow_range
                      if not math.isnan(ma_cache[w][i - 1])]
                if fv and sv:
                    fast_line = sum(fv) / len(fv)
                    slow_line = sum(sv) / len(sv)
                    gold_cross = slow_line > 0 and fast_line > slow_line
                    ratio_val = fast_line / slow_line if slow_line > 0 else 1.0
                else:
                    gold_cross = False
                    ratio_val = 1.0

                # ★ MA投票
                votes = 0
                for sw in self.vote_ma:
                    sl = slo_cache[sw][i - 1]
                    if not math.isnan(sl) and sl > 0:
                        votes += 1
                momentum = votes >= 2  # 2/3多数

                trend[d] = gold_cross and momentum
                ratio[d] = ratio_val

                # ★ MA95过滤（替代MA60）
                mf = ma_cache[self.ma_filter][i - 1]
                above_filter[d] = (not math.isnan(mf)) and closes[i - 1] > mf
            else:
                trend[d] = False
                ratio[d] = 1.0
                above_filter[d] = False

            # ATR（当日值，用于Trail计算）
            atr_val[d] = atr_series[i] if i < len(atr_series) else 0
            if math.isnan(atr_val[d]):
                atr_val[d] = 0

            etf_highs[d] = highs[i]

        return {
            'trend': trend, 'ratio': ratio,
            'above_filter': above_filter,  # 替代 above_ma60
            'above_ma60': above_filter,    # 兼容旧代码
            'atr': atr_val, 'highs': etf_highs,
        }

    def compute_index_slope(self, bars: List[dict]) -> dict:
        """计算指数 MA60 斜率信号（不变）"""
        closes = [b['close'] for b in bars]
        dates = [b['date'] for b in bars]
        m60 = calc_ma(closes, 60)
        sl = calc_slope(m60, 20)
        result = {}
        for i in range(len(dates)):
            if i > 0 and not math.isnan(sl[i - 1]):
                result[dates[i]] = sl[i - 1] > 0
            else:
                result[dates[i]] = False
        return result

    def is_bear_market(self, index_slopes: dict, date_str: str) -> bool:
        return not index_slopes.get('510300', {}).get(date_str, False)

    def get_atr_trail(self, peak: float, atr_value: float) -> float:
        """★ ATR自适应Trail比例"""
        if peak > 0 and atr_value > 0:
            trail = self.atr_mult * atr_value / peak
            return max(min(trail, self.atr_max), self.atr_min)
        return self.atr_max  # 无ATR数据时用上限（保守）

    def check_exit(self, current_price: float, date_str: str, trend_on: bool,
                   is_bear: bool, atr_value: float = 0) -> Optional[str]:
        """
        检查是否应该卖出（终极版：ATR Trail）
        """
        if not self.position:
            return None

        pos = self.position
        if current_price > pos['peak']:
            pos['peak'] = current_price

        # ★ ATR自适应Trail（替代固定牛市3%/熊市6%）
        trail = self.get_atr_trail(pos['peak'], atr_value)

        # Trail 止损
        if current_price <= pos['peak'] * (1 - trail):
            return 'trail'

        # 趋势退出（MH=0，不锁仓）
        if not trend_on:
            return 'trend_off'

        return None

    def select_buy(self, date_str: str, available_codes: List[str],
                   signals: dict, bars_data: dict, recent_highs: dict) -> Optional[Tuple[str, float, float]]:
        """选股买入（终极版：无pullback）"""
        if self.position or not available_codes:
            return None

        cands = []
        for c in available_codes:
            if self.pool_mode == 'defensive' and c not in self.defensive_pool:
                continue
            # 趋势信号
            if not signals.get('trend', {}).get(c, {}).get(date_str, False):
                continue
            # ★ MA95过滤（替代MA60）
            if not signals.get('above_filter', {}).get(c, {}).get(date_str, False):
                continue
            # pullback已删除

            bar = bars_data.get(c, {}).get(date_str)
            if bar:
                ratio = signals.get('ratio', {}).get(c, {}).get(date_str, 1.0)
                cands.append((c, ratio, bar['close']))

        if cands:
            cands.sort(key=lambda x: x[1], reverse=True)
            return cands[0]
        return None

    def on_exit(self, exit_reason: str, sell_price: float, date_str: str) -> dict:
        """卖出回调（终极版：百分比池切换+冷却期）"""
        pos = self.position
        shares = pos['shares']
        buy_price = pos['buy_price']
        pnl = shares * sell_price - shares * buy_price

        self.rolling_pnl.append(pnl)
        if len(self.rolling_pnl) > 5:
            self.rolling_pnl.pop(0)

        # ★ B版池切换：百分比阈值 + 冷却期
        prev_mode = self.pool_mode
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        can_switch = (self.last_switch_date is None or
                      (date_obj - self.last_switch_date).days >= self.cooldown)

        if can_switch:
            if self.pool_mode == 'all' and len(self.rolling_pnl) >= 5:
                # 当前净值（卖出后的现金）
                cur_eq = sell_price * shares  # 简化：卖出后全为现金
                threshold = -self.loss_pct * cur_eq
                if sum(self.rolling_pnl) < threshold:
                    self.pool_mode = 'defensive'
                    self.last_switch_date = date_obj

        self.position = None
        self.trade_count += 1

        return {
            'action': 'sell', 'code': pos['code'], 'shares': shares,
            'buy_price': buy_price, 'sell_price': sell_price, 'pnl': pnl,
            'return': (sell_price - buy_price) / buy_price if buy_price > 0 else 0,
            'exit_reason': exit_reason, 'date': date_str,
            'pool_switch': self.pool_mode != prev_mode,
            'prev_pool': prev_mode, 'new_pool': self.pool_mode,
        }

    def on_entry(self, code: str, price: float, shares: float, date_str: str):
        """买入回调"""
        self.position = {
            'code': code, 'shares': shares, 'buy_price': price,
            'peak': price, 'entry_date': datetime.strptime(date_str, '%Y-%m-%d'),
        }
        self.trade_count += 1

    def check_pool_switch_back(self, index_slopes: dict, date_str: str):
        """★ DEF→ALL 切回（含冷却期）"""
        if self.pool_mode == 'defensive':
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            can_switch = (self.last_switch_date is None or
                          (date_obj - self.last_switch_date).days >= self.cooldown)
            if can_switch:
                n_bull = sum(1 for c in self.index_codes
                             if index_slopes.get(c, {}).get(date_str, False))
                if n_bull >= self.need_n:
                    self.pool_mode = 'all'
                    self.last_switch_date = date_obj
                    return True
        return False

    def reset_daily(self, date_str: str):
        if self.last_trade_date != date_str:
            self.trade_count = 0
            self.last_trade_date = date_str

    def get_state(self) -> dict:
        return {
            'position': self.position, 'pool_mode': self.pool_mode,
            'rolling_pnl': self.rolling_pnl, 'trade_count': self.trade_count,
        }
