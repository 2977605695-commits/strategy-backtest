"""
logger.py — 四通道策略日志模块

用法:
    from logger import StrategyLogger

    logger = StrategyLogger(log_dir="logs", logger_func=print)
    logger.log_signal("INFO", "MA6上穿MA15", code="515880", ratio=1.05)
    logger.log_trade("INFO", "买入515880 金额500000", order_id="O001")
    logger.log_exception("ERROR", "委托失败", err="timeout")
    logger.log_system("INFO", "策略启动")

通道:
    - signal:    信号日志（指标触发、买卖信号等）
    - trade:     交易日志（委托、成交、撤单等）
    - exception: 异常日志（错误、超时、重试等）
    - system:    系统日志（启动、停止、配置等）

特性:
    - 本地文件持久化，按日自动切割: logs/YYYY-MM-DD_{channel}.log
    - 同时调用平台日志函数 (logger_func)
    - 线程安全 (threading.Lock)
    - 支持多进程追加写 (文件以 append 模式打开)
"""

from __future__ import annotations

import os
import json
import threading
from datetime import datetime
from typing import Any, Callable, Optional


class StrategyLogger:
    """四通道策略日志器：signal / trade / exception / system。

    每条日志含: timestamp, channel, level, message, context(dict)。
    本地文件按日切割，同时转发到平台日志函数。
    """

    CHANNELS = ("signal", "trade", "exception", "system")
    _CHANNEL_TAG = {
        "signal": "SIGNAL",
        "trade": "TRADE",
        "exception": "EXCEPTION",
        "system": "SYSTEM",
    }

    def __init__(
        self,
        log_dir: str = "logs",
        logger_func: Optional[Callable[..., None]] = None,
        level: str = 'INFO',
        console: bool = True,
        platform_logger: Optional[Callable[..., None]] = None,
    ) -> None:
        """
        Args:
            log_dir:      日志目录路径（相对或绝对）。
            logger_func:  平台日志函数，默认 print。
        """
        self._log_dir = os.path.abspath(log_dir)
        os.makedirs(self._log_dir, exist_ok=True)
        self._logger_func: Callable[..., None] = (
            logger_func if logger_func is not None else print
        )
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  公共 API
    # ------------------------------------------------------------------ #

    def log_signal(self, level: str, msg: str, **ctx: Any) -> None:
        """记录信号日志。"""
        self._write("signal", level, msg, ctx)

    def log_trade(self, level: str, msg: str, **ctx: Any) -> None:
        """记录交易日志。"""
        self._write("trade", level, msg, ctx)

    def log_exception(self, level: str, msg: str, **ctx: Any) -> None:
        """记录异常日志。"""
        self._write("exception", level, msg, ctx)

    def log_system(self, level: str, msg: str, **ctx: Any) -> None:
        """记录系统日志。"""
        self._write("system", level, msg, ctx)

    # ------------------------------------------------------------------ #
    #  内部实现
    # ------------------------------------------------------------------ #

    def _write(self, channel: str, level: str, msg: str, ctx: dict[str, Any]) -> None:
        """写入一条日志。"""
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        tag = self._CHANNEL_TAG.get(channel, channel.upper())
        ctx_str = (
            json.dumps(ctx, ensure_ascii=False, default=str) if ctx else ""
        )

        # 组装日志行
        if ctx_str:
            line = f"[{ts}] [{tag}] [{level.upper()}] {msg} | context={ctx_str}"
        else:
            line = f"[{ts}] [{tag}] [{level.upper()}] {msg}"

        # 写本地文件
        date_str = now.strftime("%Y-%m-%d")
        filepath = os.path.join(self._log_dir, f"{date_str}_{channel}.log")

        with self._lock:
            try:
                with open(filepath, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass  # 文件写入失败不应中断策略

        # 转发到平台日志函数
        try:
            self._logger_func(line)
        except Exception:
            pass  # 平台日志函数出错也不应中断策略

    # ------------------------------------------------------------------ #
    #  便捷方法
    # ------------------------------------------------------------------ #

    def get_log_file(
        self, channel: str, date_str: Optional[str] = None
    ) -> str:
        """获取指定通道和日期的日志文件路径。"""
        if channel not in self.CHANNELS:
            raise ValueError(f"未知通道: {channel}，可选: {self.CHANNELS}")
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self._log_dir, f"{date_str}_{channel}.log")