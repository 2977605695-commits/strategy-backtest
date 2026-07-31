"""
risk_manager.py — 策略风控模块

用法:
    from logger import StrategyLogger
    from risk_manager import RiskManager

    config = {
        "stop_loss_pct": -0.05,          # 止损线 -5%
        "take_profit_pct": 0.10,         # 止盈线 +10%
        "max_positions": 5,              # 最大持仓数
        "max_order_amount": 500000,      # 单笔最大下单金额
        "daily_trade_limit": 20,         # 日内最大交易次数
        "cancel_reissue_limit": 3,       # 撤单重发上限
    }
    logger = StrategyLogger(log_dir="logs", logger_func=print)
    rm = RiskManager(config, logger)

    # 单项检查
    passed, reason = rm.check_stop_loss(10000, 9.50)   # 持仓10手, 现价9.50
    passed, reason = rm.check_max_positions(6)          # 当前6个持仓

    # 综合风控
    passed, reason = rm.check_all(order, position, context)

特性:
    - 所有阈值从配置读取，不硬编码
    - 触发风控时写入日志
    - 线程安全 (threading.Lock)
"""

from __future__ import annotations

import threading
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from logger import StrategyLogger




class _NullLogger:
    def log_system(self, *a, **kw): pass
    def log_signal(self, *a, **kw): pass
    def log_trade(self, *a, **kw): pass
    def log_exception(self, *a, **kw): pass

class RiskManager:
    """策略风控管理器。

    通过配置字典初始化，提供单项检查和综合风控检查。
    所有方法返回 (passed: bool, reason: str)。
    """

    def __init__(self, config: dict[str, Any], logger: "StrategyLogger") -> None:
        """
        Args:
            config:  风控配置字典，包含所有阈值。
            logger:  StrategyLogger 实例，用于记录风控日志。
        """
        self._config = config
        self._logger = logger if logger is not None else _NullLogger()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  配置读取辅助
    # ------------------------------------------------------------------ #

    def _get(self, key: str, default: Any = None) -> Any:
        """从配置中读取值，支持默认值。"""
        return self._config.get(key, default)

    # ------------------------------------------------------------------ #
    #  单项检查
    # ------------------------------------------------------------------ #

    def check_stop_loss(
        self, position: dict[str, Any], current_price: float
    ) -> tuple[bool, str]:
        """止损检查。

        计算持仓浮动盈亏率，若低于止损阈值则触发。

        Args:
            position:      持仓信息，至少含 avg_cost (开仓均价), qty (持仓数量)。
            current_price: 当前价格。

        Returns:
            (passed, reason)
        """
        avg_cost = float(position.get("avg_cost", 0))
        qty = float(position.get("qty", 0))
        stop_loss_pct = float(self._get("stop_loss_pct", -0.05))

        if avg_cost <= 0 or qty <= 0:
            return (True, "")

        pnl_pct = (current_price - avg_cost) / avg_cost

        if pnl_pct <= stop_loss_pct:
            reason = (
                f"触发止损: 持仓成本{avg_cost:.4f} 现价{current_price:.4f} "
                f"浮亏{pnl_pct:.2%} <= 阈值{stop_loss_pct:.2%}"
            )
            self._logger.log_system(
                "WARNING", reason,
                avg_cost=avg_cost, current_price=current_price,
                pnl_pct=round(pnl_pct, 6), threshold=stop_loss_pct,
            )
            return (False, reason)

        return (True, "")

    def check_take_profit(
        self, position: dict[str, Any], current_price: float
    ) -> tuple[bool, str]:
        """止盈检查。

        计算持仓浮动盈亏率，若高于止盈阈值则触发。

        Args:
            position:      持仓信息，至少含 avg_cost, qty。
            current_price: 当前价格。

        Returns:
            (passed, reason)
        """
        avg_cost = float(position.get("avg_cost", 0))
        qty = float(position.get("qty", 0))
        take_profit_pct = float(self._get("take_profit_pct", 0.10))

        if avg_cost <= 0 or qty <= 0:
            return (True, "")

        pnl_pct = (current_price - avg_cost) / avg_cost

        if pnl_pct >= take_profit_pct:
            reason = (
                f"触发止盈: 持仓成本{avg_cost:.4f} 现价{current_price:.4f} "
                f"浮盈{pnl_pct:.2%} >= 阈值{take_profit_pct:.2%}"
            )
            self._logger.log_system(
                "WARNING", reason,
                avg_cost=avg_cost, current_price=current_price,
                pnl_pct=round(pnl_pct, 6), threshold=take_profit_pct,
            )
            return (False, reason)

        return (True, "")

    def check_max_positions(self, current_count: int) -> tuple[bool, str]:
        """最大持仓数检查。

        Args:
            current_count: 当前持仓数。

        Returns:
            (passed, reason)
        """
        max_positions = int(self._get("max_positions", 5))

        if current_count >= max_positions:
            reason = (
                f"触发持仓数限制: 当前{current_count} >= 上限{max_positions}"
            )
            self._logger.log_system(
                "WARNING", reason,
                current_count=current_count, max_positions=max_positions,
            )
            return (False, reason)

        return (True, "")

    def check_max_order_amount(self, order_amount: float) -> tuple[bool, str]:
        """单笔最大下单金额检查。

        Args:
            order_amount: 本次下单金额。

        Returns:
            (passed, reason)
        """
        max_order_amount = float(self._get("max_order_amount", 500000))

        if order_amount > max_order_amount:
            reason = (
                f"触发单笔金额限制: 金额{order_amount:.0f} > 上限{max_order_amount:.0f}"
            )
            self._logger.log_system(
                "WARNING", reason,
                order_amount=order_amount, max_order_amount=max_order_amount,
            )
            return (False, reason)

        return (True, "")

    def check_daily_trade_limit(self, trade_count: int) -> tuple[bool, str]:
        """日内交易次数限制检查。

        Args:
            trade_count: 当日已交易次数。

        Returns:
            (passed, reason)
        """
        daily_trade_limit = int(self._get("daily_trade_limit", 20))

        if trade_count >= daily_trade_limit:
            reason = (
                f"触发日内交易次数限制: 已交易{trade_count}次 >= 上限{daily_trade_limit}次"
            )
            self._logger.log_system(
                "WARNING", reason,
                trade_count=trade_count, daily_trade_limit=daily_trade_limit,
            )
            return (False, reason)

        return (True, "")

    def check_cancel_reissue(self, cancel_count: int) -> tuple[bool, str]:
        """撤单重发上限检查。

        Args:
            cancel_count: 当前累计撤单次数。

        Returns:
            (passed, reason)
        """
        cancel_reissue_limit = int(self._get("cancel_reissue_limit", 3))

        if cancel_count >= cancel_reissue_limit:
            reason = (
                f"触发撤单重发限制: 已撤单{cancel_count}次 >= 上限{cancel_reissue_limit}次"
            )
            self._logger.log_system(
                "WARNING", reason,
                cancel_count=cancel_count, cancel_reissue_limit=cancel_reissue_limit,
            )
            return (False, reason)

        return (True, "")

    # ------------------------------------------------------------------ #
    #  综合风控检查
    # ------------------------------------------------------------------ #

    def check_all(
        self,
        order: dict[str, Any],
        position: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[bool, str]:
        """综合风控检查——依次调用所有单项检查。

        Args:
            order:    待发订单信息，含 order_amount。
            position: 目标持仓信息，含 avg_cost, qty。
            context:  上下文信息，含 current_price, current_count,
                      trade_count, cancel_count。

        Returns:
            (passed, reason) — 任一检查未通过即短路返回。
        """
        with self._lock:
            current_price = float(context.get("current_price", 0))

            # 1. 止损
            passed, reason = self.check_stop_loss(position, current_price)
            if not passed:
                return (False, reason)

            # 2. 止盈
            passed, reason = self.check_take_profit(position, current_price)
            if not passed:
                return (False, reason)

            # 3. 最大持仓数
            passed, reason = self.check_max_positions(
                int(context.get("current_count", 0))
            )
            if not passed:
                return (False, reason)

            # 4. 单笔最大下单金额
            passed, reason = self.check_max_order_amount(
                float(order.get("order_amount", 0))
            )
            if not passed:
                return (False, reason)

            # 5. 日内交易次数
            passed, reason = self.check_daily_trade_limit(
                int(context.get("trade_count", 0))
            )
            if not passed:
                return (False, reason)

            # 6. 撤单重发上限
            passed, reason = self.check_cancel_reissue(
                int(context.get("cancel_count", 0))
            )
            if not passed:
                return (False, reason)

            return (True, "")

    # ------------------------------------------------------------------ #
    #  配置更新
    # ------------------------------------------------------------------ #

    def update_config(self, key: str, value: Any) -> None:
        """运行时更新单个配置项。"""
        with self._lock:
            self._config[key] = value
            self._logger.log_system(
                "INFO", f"风控配置更新: {key}={value}", key=key, value=str(value)
            )