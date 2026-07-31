"""
ledger.py — 交易台账模块

用法:
    from logger import StrategyLogger
    from ledger import TradeLedger

    logger = StrategyLogger(log_dir="logs", logger_func=print)
    ledger = TradeLedger(db_path="ledger.db", csv_dir="logs", logger=logger)

    # 委托记录
    order_id = ledger.record_order("515880", "buy", 1.050, 100000, "MA6>MA15", "QMT")

    # 成交更新
    ledger.update_fill(order_id, 1.051, 100000, 5.0, 0.001)

    # 撤单记录
    ledger.record_cancel(order_id, "超时未成交")

    # 持仓快照
    ledger.snapshot_position("515880", 100000, 1.050, 105500, 500)

    # 查询
    summary = ledger.get_daily_summary("2026-07-31")
    trades = ledger.get_trades_by_code("515880", "2026-07-01", "2026-07-31")

特性:
    - SQLite 主存储 (WAL 模式)，线程安全
    - CSV 镜像备份，按日分文件: logs/YYYY-MM-DD_ledger.csv
    - 纯 Python 标准库，无第三方依赖
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from logger import StrategyLogger


class TradeLedger:
    """交易台账——委托/成交/撤单/持仓的全生命周期记录。

    双写策略: SQLite (主) + CSV (镜像备份)。
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS orders (
        order_id    TEXT PRIMARY KEY,
        timestamp   TEXT NOT NULL,
        code        TEXT NOT NULL,
        side        TEXT NOT NULL,
        price       REAL NOT NULL,
        qty         REAL NOT NULL,
        status     TEXT NOT NULL DEFAULT 'pending',
        strategy_signal TEXT,
        platform   TEXT
    );

    CREATE TABLE IF NOT EXISTS fills (
        fill_id     TEXT PRIMARY KEY,
        order_id    TEXT NOT NULL,
        timestamp   TEXT NOT NULL,
        code        TEXT NOT NULL,
        side        TEXT NOT NULL,
        fill_price  REAL NOT NULL,
        fill_qty    REAL NOT NULL,
        commission  REAL DEFAULT 0,
        slippage    REAL DEFAULT 0,
        FOREIGN KEY (order_id) REFERENCES orders(order_id)
    );

    CREATE TABLE IF NOT EXISTS cancels (
        cancel_id   TEXT PRIMARY KEY,
        order_id    TEXT NOT NULL,
        timestamp   TEXT NOT NULL,
        reason      TEXT,
        FOREIGN KEY (order_id) REFERENCES orders(order_id)
    );

    CREATE TABLE IF NOT EXISTS positions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp   TEXT NOT NULL,
        code        TEXT NOT NULL,
        qty         REAL NOT NULL,
        avg_cost    REAL NOT NULL,
        market_value REAL NOT NULL,
        unrealized_pnl REAL NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_orders_code ON orders(code);
    CREATE INDEX IF NOT EXISTS idx_orders_timestamp ON orders(timestamp);
    CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);
    CREATE INDEX IF NOT EXISTS idx_cancels_order ON cancels(order_id);
    CREATE INDEX IF NOT EXISTS idx_positions_code ON positions(code);
    CREATE INDEX IF NOT EXISTS idx_positions_timestamp ON positions(timestamp);
    """

    CSV_HEADERS = [
        "record_type", "id", "timestamp", "code", "side",
        "price", "qty", "status", "fill_price", "fill_qty",
        "commission", "slippage", "avg_cost", "market_value",
        "unrealized_pnl", "strategy_signal", "platform", "reason",
        "order_id",
    ]

    def __init__(
        self,
        db_path: str = "ledger.db",
        csv_dir: str = "logs",
        logger: Optional["StrategyLogger"] = None,
    ) -> None:
        """
        Args:
            db_path:  SQLite 数据库文件路径。
            csv_dir: CSV 镜像目录路径。
            logger:  StrategyLogger 实例，可选。
        """
        self._db_path = os.path.abspath(db_path)
        self._csv_dir = os.path.abspath(csv_dir)
        self._logger = logger
        self._lock = threading.Lock()

        # 确保目录存在
        os.makedirs(self._csv_dir, exist_ok=True)
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        # 初始化数据库
        self._init_db()

    # ------------------------------------------------------------------ #
    #  数据库初始化
    # ------------------------------------------------------------------ #

    def _init_db(self) -> None:
        """初始化 SQLite 数据库，启用 WAL 模式，建表。"""
        with self._lock:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(self._DDL)
            conn.commit()
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（非线程安全调用时需自行加锁）。"""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------ #
    #  唯一 ID 生成
    # ------------------------------------------------------------------ #

    @staticmethod
    def _uid() -> str:
        """生成唯一 ID。"""
        return uuid.uuid4().hex[:16]

    @staticmethod
    def _now() -> str:
        """当前时间戳字符串。"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------ #
    #  CSV 镜像写
    # ------------------------------------------------------------------ #

    def _write_csv(self, record: dict[str, Any]) -> None:
        """追加一条记录到当日的 CSV 镜像文件。"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        csv_path = os.path.join(self._csv_dir, f"{date_str}_ledger.csv")

        # 确保 headers 正确排列
        row = [record.get(h, "") for h in self.CSV_HEADERS]

        with self._lock:
            file_exists = os.path.exists(csv_path)
            try:
                with open(csv_path, "a", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(self.CSV_HEADERS)
                    writer.writerow(row)
            except OSError:
                if self._logger:
                    self._logger.log_exception(
                        "ERROR", f"CSV写入失败: {csv_path}", record_type=record.get("record_type")
                    )

    # ------------------------------------------------------------------ #
    #  委托记录
    # ------------------------------------------------------------------ #

    def record_order(
        self,
        code: str,
        side: str,
        price: float,
        qty: float,
        signal: str = "",
        platform: str = "",
    ) -> str:
        """记录一条委托订单。

        Args:
            code:     标的代码。
            side:     买卖方向 ("buy" / "sell")。
            price:    委托价格。
            qty:      委托数量（股/手）。
            signal:   策略信号标识。
            platform: 交易平台标识 ("QMT" / "PTrade")。

        Returns:
            order_id
        """
        order_id = self._uid()
        ts = self._now()

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO orders (order_id, timestamp, code, side, price, qty, status, strategy_signal, platform) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (order_id, ts, code, side, price, qty, signal, platform),
                )
                conn.commit()
            finally:
                conn.close()

        # CSV 镜像
        self._write_csv({
            "record_type": "order",
            "id": order_id,
            "timestamp": ts,
            "code": code,
            "side": side,
            "price": price,
            "qty": qty,
            "strategy_signal": signal,
            "platform": platform,
        })

        if self._logger:
            self._logger.log_trade(
                "INFO",
                f"委托: {side} {code} 价格{price} 数量{qty}",
                order_id=order_id, code=code, side=side,
                price=price, qty=qty, signal=signal, platform=platform,
            )

        return order_id

    # ------------------------------------------------------------------ #
    #  成交更新
    # ------------------------------------------------------------------ #

    def update_fill(
        self,
        order_id: str,
        fill_price: float,
        fill_qty: float,
        commission: float = 0.0,
        slippage: float = 0.0,
    ) -> Optional[str]:
        """更新成交记录，同时更新订单状态为 'filled'。

        Args:
            order_id:   关联的委托订单 ID。
            fill_price: 成交均价。
            fill_qty:   成交数量。
            commission: 手续费。
            slippage:   滑点（绝对值）。

        Returns:
            fill_id，若订单不存在则返回 None。
        """
        fill_id = self._uid()
        ts = self._now()

        with self._lock:
            conn = self._get_conn()
            try:
                # 取订单信息
                row = conn.execute(
                    "SELECT code, side FROM orders WHERE order_id = ?", (order_id,)
                ).fetchone()

                if row is None:
                    if self._logger:
                        self._logger.log_exception(
                            "ERROR", f"成交记录失败: 订单不存在",
                            order_id=order_id,
                        )
                    return None

                code, side = row[0], row[1]

                conn.execute(
                    "INSERT INTO fills (fill_id, order_id, timestamp, code, side, fill_price, fill_qty, commission, slippage) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (fill_id, order_id, ts, code, side, fill_price, fill_qty, commission, slippage),
                )
                conn.execute(
                    "UPDATE orders SET status = 'filled' WHERE order_id = ?",
                    (order_id,),
                )
                conn.commit()
            finally:
                conn.close()

        # CSV 镜像
        self._write_csv({
            "record_type": "fill",
            "id": fill_id,
            "order_id": order_id,
            "timestamp": ts,
            "code": code,
            "side": side,
            "fill_price": fill_price,
            "fill_qty": fill_qty,
            "commission": commission,
            "slippage": slippage,
        })

        if self._logger:
            self._logger.log_trade(
                "INFO",
                f"成交: {side} {code} 价格{fill_price} 数量{fill_qty} 滑点{slippage}",
                fill_id=fill_id, order_id=order_id, code=code, side=side,
                fill_price=fill_price, fill_qty=fill_qty,
                commission=commission, slippage=slippage,
            )

        return fill_id

    # ------------------------------------------------------------------ #
    #  撤单记录
    # ------------------------------------------------------------------ #

    def record_cancel(self, order_id: str, reason: str = "") -> Optional[str]:
        """记录撤单，同时更新订单状态为 'cancelled'。

        Args:
            order_id:  关联的委托订单 ID。
            reason:    撤单原因。

        Returns:
            cancel_id，若订单不存在则返回 None。
        """
        cancel_id = self._uid()
        ts = self._now()

        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT order_id FROM orders WHERE order_id = ?", (order_id,)
                ).fetchone()

                if row is None:
                    if self._logger:
                        self._logger.log_exception(
                            "ERROR", f"撤单记录失败: 订单不存在",
                            order_id=order_id,
                        )
                    return None

                conn.execute(
                    "INSERT INTO cancels (cancel_id, order_id, timestamp, reason) "
                    "VALUES (?, ?, ?, ?)",
                    (cancel_id, order_id, ts, reason),
                )
                conn.execute(
                    "UPDATE orders SET status = 'cancelled' WHERE order_id = ?",
                    (order_id,),
                )
                conn.commit()
            finally:
                conn.close()

        # CSV 镜像
        self._write_csv({
            "record_type": "cancel",
            "id": cancel_id,
            "order_id": order_id,
            "timestamp": ts,
            "reason": reason,
        })

        if self._logger:
            self._logger.log_trade(
                "INFO",
                f"撤单: order_id={order_id} reason={reason}",
                cancel_id=cancel_id, order_id=order_id, reason=reason,
            )

        return cancel_id

    # ------------------------------------------------------------------ #
    #  持仓快照
    # ------------------------------------------------------------------ #

    def snapshot_position(
        self,
        code: str,
        qty: float,
        avg_cost: float,
        market_value: float,
        unrealized_pnl: float,
    ) -> int:
        """写入一条持仓快照记录。

        Args:
            code:           标的代码。
            qty:            持仓数量。
            avg_cost:       持仓均价。
            market_value:   市值。
            unrealized_pnl: 未实现盈亏。

        Returns:
            自增 id。
        """
        ts = self._now()

        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "INSERT INTO positions (timestamp, code, qty, avg_cost, market_value, unrealized_pnl) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, code, qty, avg_cost, market_value, unrealized_pnl),
                )
                conn.commit()
                row_id = cur.lastrowid
            finally:
                conn.close()

        # CSV 镜像
        self._write_csv({
            "record_type": "position",
            "id": row_id,
            "timestamp": ts,
            "code": code,
            "qty": qty,
            "avg_cost": avg_cost,
            "market_value": market_value,
            "unrealized_pnl": unrealized_pnl,
        })

        return row_id

    # ------------------------------------------------------------------ #
    #  查询
    # ------------------------------------------------------------------ #

    def get_daily_summary(self, date: str) -> dict[str, Any]:
        """获取当日交易汇总。

        Args:
            date: 日期字符串 "YYYY-MM-DD"。

        Returns:
            dict with keys: date, order_count, fill_count, cancel_count,
            total_buy_amount, total_sell_amount, total_commission, codes_traded
        """
        with self._lock:
            conn = self._get_conn()
            try:
                date_prefix = date + "%"

                # 订单数
                order_count = conn.execute(
                    "SELECT COUNT(*) FROM orders WHERE timestamp LIKE ?",
                    (date_prefix,),
                ).fetchone()[0]

                # 成交数
                fill_count = conn.execute(
                    "SELECT COUNT(*) FROM fills WHERE timestamp LIKE ?",
                    (date_prefix,),
                ).fetchone()[0]

                # 撤单数
                cancel_count = conn.execute(
                    "SELECT COUNT(*) FROM cancels WHERE timestamp LIKE ?",
                    (date_prefix,),
                ).fetchone()[0]

                # 买入总金额
                buy_row = conn.execute(
                    "SELECT COALESCE(SUM(fill_price * fill_qty), 0) FROM fills "
                    "WHERE timestamp LIKE ? AND side = 'buy'",
                    (date_prefix,),
                ).fetchone()
                total_buy_amount = buy_row[0]

                # 卖出总金额
                sell_row = conn.execute(
                    "SELECT COALESCE(SUM(fill_price * fill_qty), 0) FROM fills "
                    "WHERE timestamp LIKE ? AND side = 'sell'",
                    (date_prefix,),
                ).fetchone()
                total_sell_amount = sell_row[0]

                # 总手续费
                comm_row = conn.execute(
                    "SELECT COALESCE(SUM(commission), 0) FROM fills WHERE timestamp LIKE ?",
                    (date_prefix,),
                ).fetchone()
                total_commission = comm_row[0]

                # 交易标的
                codes = conn.execute(
                    "SELECT DISTINCT code FROM orders WHERE timestamp LIKE ?",
                    (date_prefix,),
                ).fetchall()
                codes_traded = [r[0] for r in codes]

            finally:
                conn.close()

        return {
            "date": date,
            "order_count": order_count,
            "fill_count": fill_count,
            "cancel_count": cancel_count,
            "total_buy_amount": round(total_buy_amount, 2),
            "total_sell_amount": round(total_sell_amount, 2),
            "total_commission": round(total_commission, 2),
            "codes_traded": codes_traded,
        }

    def get_trades_by_code(
        self, code: str, start_date: str, end_date: str
    ) -> list[dict[str, Any]]:
        """按标的查询交易记录（含委托、成交关联）。

        Args:
            code:       标的代码。
            start_date: 开始日期 "YYYY-MM-DD"。
            end_date:   结束日期 "YYYY-MM-DD"。

        Returns:
            订单列表，每个订单含成交信息 (fills)。
        """
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            try:
                orders = conn.execute(
                    """SELECT * FROM orders
                       WHERE code = ?
                         AND timestamp >= ?
                         AND timestamp < ?
                       ORDER BY timestamp DESC""",
                    (code, start_date + " 00:00:00", end_date + " 23:59:59"),
                ).fetchall()

                result: list[dict[str, Any]] = []
                for order in orders:
                    order_dict = dict(order)

                    # 关联成交
                    fills = conn.execute(
                        "SELECT * FROM fills WHERE order_id = ? ORDER BY timestamp",
                        (order_dict["order_id"],),
                    ).fetchall()
                    order_dict["fills"] = [dict(f) for f in fills]

                    # 关联撤单
                    cancels = conn.execute(
                        "SELECT * FROM cancels WHERE order_id = ? ORDER BY timestamp",
                        (order_dict["order_id"],),
                    ).fetchall()
                    order_dict["cancels"] = [dict(c) for c in cancels]

                    result.append(order_dict)

            finally:
                conn.close()

        return result

    # ------------------------------------------------------------------ #
    #  关闭
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """关闭台账（WAL checkpoint）。"""
        with self._lock:
            try:
                conn = sqlite3.connect(self._db_path)
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.close()
            except Exception:
                pass

        if self._logger:
            self._logger.log_system("INFO", "台账已关闭")