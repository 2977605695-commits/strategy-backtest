"""
V10 Adaptive Trend Rotation — 策略核心逻辑（平台无关）
=====================================================
从 etf30_vs_33.py 提取，去除回测框架，保留信号生成 + 交易决策逻辑。
被 qmt_strategy.py / ptrade_strategy.py 引用。

核心规则：
  信号: MA6 > MA15 + MA8斜率正 (用T-1日close计算)
  选股: MA6/MA15比值降序 Top1
  止损: 牛市Trail 3% / 熊市Trail 6%
  池切换: 连续5笔亏损>10%→防御池, 7/10指数slope正→全池
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


class StrategyCore:
    """V10 策略核心逻辑，平台无关"""

    def __init__(self, config: dict):
        self.fast_ma = config['strategy']['fast_ma']
        self.slow_ma = config['strategy']['slow_ma']
        self.slope_ma = config['strategy']['slope_ma']
        self.trail_bull = config['strategy']['trail_bull']
        self.trail_bear = config['strategy']['trail_bear']
        self.bull_mh = config['strategy']['bull_min_hold']
        self.bear_mh = config['strategy']['bear_min_hold']
        self.pullback_min = config['strategy']['pullback_min']
        self.need_n = config['strategy']['need_n_index']
        self.pnl_loss_threshold = config['strategy']['pnl_loss_threshold']
        self.risk_free = config['strategy']['risk_free']

        self.full_pool = config['pool']['full']
        self.defensive_pool = config['pool']['defensive']
        self.index_codes = config['pool']['index_codes']

        self.slippage = config['execution']['slippage']
        self.buy_fee = config['execution']['buy_fee']
        self.sell_fee = config['execution']['sell_fee']
        self.stamp_tax = config['execution']['stamp_tax']

        # 运行时状态
        self.position = None       # {'code': str, 'shares': float, 'buy_price': float, 'peak': float, 'entry_date': datetime}
        self.pool_mode = 'all'
        self.rolling_pnl = []      # 最近5笔交易PnL
        self.trade_count = 0       # 当日交易次数
        self.last_trade_date = None

    def compute_signals(self, bars: List[dict], code: str) -> dict:
        """
        计算单个ETF的信号数据
        bars: [{'date': str, 'close': float, 'high': float}, ...]
        返回: {'trend': {date: bool}, 'ratio': {date: float}, 'above_ma60': {date: bool}, 'highs': {date: float}}
        """
        closes = [b['close'] for b in bars]
        highs = [b['high'] for b in bars]
        dates = [b['date'] for b in bars]

        mf = calc_ma(closes, self.fast_ma)
        ms = calc_ma(closes, self.slow_ma)
        msl = calc_ma(closes, self.slope_ma)
        m60 = calc_ma(closes, 60)
        slo_ = calc_slope(msl, max(self.slope_ma // 2, 3))

        trend = {}
        ratio = {}
        above_ma60 = {}
        etf_highs = {}

        for i in range(len(bars)):
            d = dates[i]
            if not math.isnan(mf[i]) and not math.isnan(ms[i]) and ms[i] > 0:
                sk = not math.isnan(slo_[i]) and slo_[i] > 0
                # T-1 信号：用 i-1 的 MA 值
                if i > 0 and not math.isnan(mf[i-1]) and not math.isnan(ms[i-1]) and ms[i-1] > 0:
                    trend[d] = mf[i-1] > ms[i-1] and sk
                    ratio[d] = mf[i-1] / ms[i-1]
                else:
                    trend[d] = False
                    ratio[d] = 1.0
            else:
                trend[d] = False
                ratio[d] = 1.0

            # above_ma60 用 T-1
            if i > 0 and not math.isnan(m60[i-1]):
                above_ma60[d] = closes[i-1] > m60[i-1]
            else:
                above_ma60[d] = False

            etf_highs[d] = highs[i]

        return {'trend': trend, 'ratio': ratio, 'above_ma60': above_ma60, 'highs': etf_highs}

    def compute_index_slope(self, bars: List[dict]) -> dict:
        """计算指数 MA60 斜率信号"""
        closes = [b['close'] for b in bars]
        dates = [b['date'] for b in bars]
        m60 = calc_ma(closes, 60)
        sl = calc_slope(m60, 20)

        result = {}
        for i in range(len(dates)):
            if i > 0 and not math.isnan(sl[i-1]):
                result[dates[i]] = sl[i-1] > 0
            else:
                result[dates[i]] = False
        return result

    def is_bear_market(self, index_slopes: dict, date_str: str) -> bool:
        """判断牛熊：HS300(510300) slope <= 0 为熊市"""
        return not index_slopes.get('510300', {}).get(date_str, False)

    def get_trail_and_min_hold(self, is_bear: bool) -> Tuple[float, int]:
        """根据牛熊返回 trail 比例和最小持仓天数"""
        return (self.trail_bear, self.bear_mh) if is_bear else (self.trail_bull, self.bull_mh)

    def check_exit(self, current_price: float, date_str: str, trend_on: bool,
                   is_bear: bool) -> Optional[str]:
        """
        检查是否应该卖出
        返回: exit_reason(str) 或 None
        """
        if not self.position:
            return None

        pos = self.position
        if current_price > pos['peak']:
            pos['peak'] = current_price

        trail, min_hold = self.get_trail_and_min_hold(is_bear)

        # Trail 止损
        if current_price <= pos['peak'] * (1 - trail):
            return 'trail'

        # 趋势退出
        if not trend_on:
            if min_hold > 0 and pos['entry_date']:
                days_held = (datetime.strptime(date_str, '%Y-%m-%d') - pos['entry_date']).days
                if days_held >= min_hold:
                    return 'trend_off'
            else:
                return 'trend_off'

        return None

    def select_buy(self, date_str: str, available_codes: List[str],
                   signals: dict, bars_data: dict, recent_highs: dict) -> Optional[Tuple[str, float, float]]:
        """
        选股买入
        返回: (code, ratio, price) 或 None
        """
        if self.position or not available_codes:
            return None

        cands = []
        for c in available_codes:
            # 防御池过滤
            if self.pool_mode == 'defensive' and c not in self.defensive_pool:
                continue
            # 趋势信号
            if not signals.get('trend', {}).get(c, {}).get(date_str, False):
                continue
            # MA60 过滤
            if not signals.get('above_ma60', {}).get(c, {}).get(date_str, False):
                continue
            # 回调过滤
            if self.pullback_min > 0:
                hi20 = recent_highs.get(c, 0)
                px = bars_data.get(c, {}).get(date_str, {}).get('close', 0)
                if hi20 > 0 and (hi20 - px) / hi20 < self.pullback_min:
                    continue

            bar = bars_data.get(c, {}).get(date_str)
            if bar:
                ratio = signals.get('ratio', {}).get(c, {}).get(date_str, 1.0)
                cands.append((c, ratio, bar['close']))

        if cands:
            cands.sort(key=lambda x: x[1], reverse=True)
            return cands[0]
        return None

    def on_exit(self, exit_reason: str, sell_price: float, date_str: str) -> dict:
        """卖出回调，更新状态"""
        pos = self.position
        shares = pos['shares']
        buy_price = pos['buy_price']
        pnl = shares * sell_price - shares * buy_price

        self.rolling_pnl.append(pnl)
        if len(self.rolling_pnl) > 5:
            self.rolling_pnl.pop(0)

        # 池切换判断
        prev_mode = self.pool_mode
        if len(self.rolling_pnl) >= 5 and self.pool_mode == 'all' and sum(self.rolling_pnl) < self.pnl_loss_threshold:
            self.pool_mode = 'defensive'
        if self.pool_mode == 'defensive':
            # 检查是否可以切回全池（需要外部传入 index_slopes）
            pass

        self.position = None
        self.trade_count += 1

        return {
            'action': 'sell',
            'code': pos['code'],
            'shares': shares,
            'buy_price': buy_price,
            'sell_price': sell_price,
            'pnl': pnl,
            'return': (sell_price - buy_price) / buy_price if buy_price > 0 else 0,
            'exit_reason': exit_reason,
            'date': date_str,
            'pool_switch': self.pool_mode != prev_mode,
            'prev_pool': prev_mode,
            'new_pool': self.pool_mode,
        }

    def on_entry(self, code: str, price: float, shares: float, date_str: str):
        """买入回调，更新状态"""
        self.position = {
            'code': code,
            'shares': shares,
            'buy_price': price,
            'peak': price,
            'entry_date': datetime.strptime(date_str, '%Y-%m-%d'),
        }
        self.trade_count += 1

    def check_pool_switch_back(self, index_slopes: dict, date_str: str):
        """检查是否从防御池切回全池"""
        if self.pool_mode == 'defensive':
            n_bull = sum(1 for c in self.index_codes
                        if index_slopes.get(c, {}).get(date_str, False))
            if n_bull >= self.need_n:
                self.pool_mode = 'all'
                return True
        return False

    def reset_daily(self, date_str: str):
        """每日重置"""
        if self.last_trade_date != date_str:
            self.trade_count = 0
            self.last_trade_date = date_str

    def get_state(self) -> dict:
        """获取当前状态快照（用于日志和台账）"""
        return {
            'position': self.position,
            'pool_mode': self.pool_mode,
            'rolling_pnl': self.rolling_pnl,
            'trade_count': self.trade_count,
        }
