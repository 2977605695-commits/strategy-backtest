"""
V10 Adaptive Trend Rotation — PTrade (恒生电子) 适配版
================================================
平台标准函数结构: initialize / handle_data / before_trading_start / after_trading_end

用法:
  1. 将本文件加载到 PTrade 策略平台
  2. 修改 config.yaml 中的 platform 为 ptrade
  3. 在 PTrade 中绑定 ETF 标的池
  4. 运行策略

依赖:
  - strategy_core.py (策略核心逻辑)
  - risk_manager.py (风控)
  - logger.py (日志)
  - ledger.py (台账)
"""

import os
import sys
import json
import math
import time
import traceback
from datetime import datetime, timedelta

# ── 添加模块路径 ──
STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, STRATEGY_DIR)

from strategy_core import StrategyCore, calc_ma, calc_slope
from risk_manager import RiskManager
from logger import StrategyLogger
from ledger import TradeLedger

# ── 加载配置 ──
def load_config():
    """加载 YAML 配置文件"""
    config_path = os.path.join(STRATEGY_DIR, 'config.yaml')
    try:
        import yaml
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except ImportError:
        return _parse_simple_yaml(config_path)

def _parse_simple_yaml(path):
    """简易 YAML 解析器（无第三方依赖时的降级方案）"""
    json_path = path.replace('.yaml', '.json')
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    raise FileNotFoundError(f"无法解析 {path}，请安装 PyYAML 或提供 config.json")

# ── 全局变量 ──
config = None
strategy = None
risk_mgr = None
logger_inst = None
ledger = None
aStock = []           # PTrade 标的列表
index_signals = {}    # 指数 slope 信号
etf_signals = {}      # ETF 信号数据
last_signal_date = None
bar_data_cache = {}   # 行情缓存
RECONNECT_MAX_RETRIES = 3
RECONNECT_BACKOFF = 2  # 秒

# ============================================================
# PTrade 平台标准函数
# ============================================================

def initialize(context):
    """PTrade 初始化函数"""
    global config, strategy, risk_mgr, logger_inst, ledger, aStock

    config = load_config()

    # 初始化日志
    log_dir = os.path.join(STRATEGY_DIR, config.get('logging', {}).get('log_dir', 'logs'))
    logger_inst = StrategyLogger(
        log_dir=log_dir,
        level=config.get('logging', {}).get('level', 'INFO'),
        console=config.get('logging', {}).get('console', True),
        platform_logger=_ptrade_log
    )
    logger_inst.log_system('INFO', 'V10 策略初始化开始', mode=config['mode'], platform='ptrade')

    # 初始化风控
    risk_mgr = RiskManager(config.get('risk', {}), logger_inst)

    # 初始化台账
    ledger = TradeLedger(os.path.join(STRATEGY_DIR, 'ledger.db'), log_dir)

    # 初始化策略核心
    strategy = StrategyCore(config)

    # 设置 PTrade 标的池
    pool = config['pool']['full'] + config['pool']['defensive']
    seen = set()
    aStock = []
    for code in pool:
        if code not in seen:
            seen.add(code)
            aStock.append(code)

    for code in config['pool']['index_codes']:
        if code not in seen:
            seen.add(code)
            aStock.append(code)

    # 设置标的池（PTrade 方式）
    try:
        set_universe([_format_ptrade_code(c) for c in aStock])
    except Exception:
        # 部分 PTrade 版本不支持 set_universe，忽略
        pass

    logger_inst.log_system('INFO', f'策略初始化完成 | 标的池: {len(aStock)} 个 | 模式: {config["mode"]}')


def before_trading_start(context, data):
    """PTrade 开盘前回调 — 每日重置 + 信号更新"""
    global last_signal_date, index_signals, etf_signals, bar_data_cache

    try:
        date_str = context.current_dt.strftime('%Y-%m-%d')

        # 每日初始化
        strategy.reset_daily(date_str)

        # 更新信号
        if last_signal_date != date_str:
            _update_signals(context, date_str)
            last_signal_date = date_str

    except Exception as e:
        logger_inst.log_exception('ERROR', f'before_trading_start 异常: {e}',
                                 error=str(e), traceback=traceback.format_exc())


def handle_data(context, data):
    """PTrade 主回调函数（每 bar 触发）"""
    try:
        date_str = context.current_dt.strftime('%Y-%m-%d')
        current_time = context.current_dt.strftime('%H:%M')

        # 实盘模式：14:50 才执行交易
        if config['mode'] == 'live' and current_time < '14:50':
            return

        # ── Step 1: 检查卖出 ──
        if strategy.position:
            pos_code = strategy.position['code']
            current_price = _get_current_price(context, pos_code)

            if current_price is None:
                logger_inst.log_exception('WARNING', f'无法获取 {pos_code} 当前价格，跳过',
                                         code=pos_code, date=date_str)
                return

            # 检查停牌
            if _is_suspended(context, pos_code):
                logger_inst.log_signal('WARNING', f'{pos_code} 停牌，无法交易',
                                       code=pos_code, date=date_str)
                return

            # 风控检查
            passed, reason = risk_mgr.check_all(
                order=None,
                position=strategy.position,
                context={'current_price': current_price, 'date': date_str}
            )

            is_bear = strategy.is_bear_market(index_signals, date_str)
            trend_on = etf_signals.get('trend', {}).get(pos_code, {}).get(date_str, False)
            exit_reason = strategy.check_exit(current_price, date_str, trend_on, is_bear)

            if exit_reason:
                if not passed and 'stop_loss' in reason:
                    exit_reason = 'risk_stop_loss'
                _execute_sell(context, pos_code, strategy.position['shares'],
                             current_price, exit_reason, date_str)

        # ── Step 2: 检查买入 ──
        if not strategy.position and strategy.pool_mode:
            # 检查防御池切回
            strategy.check_pool_switch_back(index_signals, date_str)

            available = [c for c in config['pool']['full'] if c in aStock]
            recent_highs = _compute_recent_highs(context, available, date_str, 20)

            selected = strategy.select_buy(
                date_str, available, etf_signals,
                bar_data_cache, recent_highs
            )

            if selected:
                code, ratio, price = selected

                # 检查停牌
                if _is_suspended(context, code):
                    logger_inst.log_signal('WARNING', f'{code} 停牌，跳过买入',
                                           code=code, date=date_str)
                    return

                # 检查涨停
                if _is_limit_up(context, code, price):
                    logger_inst.log_signal('WARNING', f'{code} 涨停，无法买入',
                                           code=code, date=date_str)
                    return

                order_amount = _calc_order_amount(price)
                passed, reason = risk_mgr.check_all(
                    order={'code': code, 'amount': order_amount, 'side': 'buy'},
                    position=None,
                    context={'trade_count': strategy.trade_count, 'date': date_str}
                )

                if passed:
                    shares = int(order_amount / price / 100) * 100  # 按100股取整
                    if shares > 0:
                        _execute_buy(context, code, shares, price, ratio, date_str)
                else:
                    logger_inst.log_signal('WARNING', f'风控拦截买入 {code}: {reason}',
                                          code=code, reason=reason, amount=order_amount)

    except Exception as e:
        logger_inst.log_exception('ERROR', f'handle_data 异常: {e}',
                                 error=str(e), traceback=traceback.format_exc())
        if config['mode'] == 'live':
            raise


def after_trading_end(context, data):
    """PTrade 收盘后回调"""
    try:
        date_str = context.current_dt.strftime('%Y-%m-%d')

        # 记录持仓快照
        if strategy.position:
            pos = strategy.position
            current_price = _get_current_price(context, pos['code'])
            if current_price:
                market_value = pos['shares'] * current_price
                unrealized = pos['shares'] * (current_price - pos['buy_price'])
                ledger.snapshot_position(
                    pos['code'], pos['shares'], pos['buy_price'],
                    market_value, unrealized
                )

        # 台账日终汇总
        summary = ledger.get_daily_summary(date_str)
        logger_inst.log_system('INFO', '日终汇总',
                              date=date_str, trades=summary.get('trade_count', 0),
                              pnl=summary.get('total_pnl', 0),
                              position=strategy.get_state())

        # 检查未成交委托并处理
        _check_pending_orders(context)

        logger_inst.log_system('INFO', f'收盘处理完成 | 日期: {date_str}')

    except Exception as e:
        logger_inst.log_exception('ERROR', f'after_trading_end 异常: {e}',
                                 error=str(e), traceback=traceback.format_exc())


# ============================================================
# 紧急停止
# ============================================================

def emergency_stop(context=None):
    """
    紧急停止：撤销所有未成交委托 + 导出持仓快照
    可由外部信号触发，也可在异常时自动调用
    """
    logger_inst.log_system('WARNING', '紧急停止触发！开始撤销所有委托')

    # 撤销所有未成交委托
    try:
        orders = get_orders()
        for o_id, order_info in orders.items():
            if hasattr(order_info, 'status') and order_info.status in ['pending', 'partial_filled', 'queued']:
                cancel_order(o_id)
                logger_inst.log_system('WARNING', f'撤销委托: {o_id}')
            elif isinstance(order_info, dict) and order_info.get('status') in ['pending', 'partial_filled', 'queued']:
                cancel_order(o_id)
                logger_inst.log_system('WARNING', f'撤销委托: {o_id}')
    except Exception as e:
        logger_inst.log_exception('ERROR', f'撤单失败: {e}', error=str(e))

    # 导出持仓快照
    if strategy.position:
        pos = strategy.position
        try:
            current_price = _get_current_price(context, pos['code']) if context else 0
        except Exception:
            current_price = 0
        market_value = pos['shares'] * current_price if current_price else 0
        unrealized = pos['shares'] * (current_price - pos['buy_price']) if current_price else 0
        ledger.snapshot_position(
            pos['code'], pos['shares'], pos['buy_price'],
            market_value, unrealized
        )
        logger_inst.log_system('WARNING', f'持仓快照已导出: {pos["code"]} {pos["shares"]}股')

    logger_inst.log_system('WARNING', '紧急停止完成，请在 PTrade 平台确认委托状态')


# ============================================================
# 辅助函数 — 行情数据
# ============================================================

def _ptrade_log(level, msg):
    """PTrade 平台日志适配"""
    try:
        if level in ('ERROR', 'CRITICAL'):
            log.error(msg)
        else:
            log.info(msg)
    except Exception:
        print(f'[{level}] {msg}')


def _format_ptrade_code(code):
    """格式化为 PTrade 标的代码 (515880.SZ / 510300.SH)"""
    code = str(code)
    if code.startswith(('0', '1', '2', '3')):
        return f'{code}.SZ'
    elif code.startswith(('5', '6')):
        return f'{code}.SH'
    return code


def _update_signals(context, date_str):
    """更新所有 ETF 和指数的信号"""
    global etf_signals, index_signals, bar_data_cache

    etf_signals = {'trend': {}, 'ratio': {}, 'above_ma60': {}, 'highs': {}}
    index_signals = {}
    bar_data_cache = {}

    # 获取 ETF 信号
    for code in config['pool']['full']:
        bars = _get_history_bars(context, code, 120)
        if not bars or len(bars) < 60:
            continue

        sig = strategy.compute_signals(bars, code)
        etf_signals['trend'][code] = sig['trend']
        etf_signals['ratio'][code] = sig['ratio']
        etf_signals['above_ma60'][code] = sig['above_ma60']
        etf_signals['highs'][code] = sig['highs']

        bar_data_cache[code] = {b['date']: b for b in bars}

    # 获取指数信号
    for code in config['pool']['index_codes']:
        bars = _get_history_bars(context, code, 120)
        if not bars or len(bars) < 60:
            continue
        index_signals[code] = strategy.compute_index_slope(bars)

    logger_inst.log_signal('DEBUG', f'信号更新完成 | ETF: {len(etf_signals["trend"])} | 指数: {len(index_signals)}',
                          date=date_str)


def _get_history_bars(context, code, count):
    """获取历史 K 线数据（带断线重连）"""
    ptrade_code = _format_ptrade_code(code)

    for attempt in range(RECONNECT_MAX_RETRIES):
        try:
            hist = get_history(
                ptrade_code,
                count=count,
                unit='1d',
                fields=['close', 'high', 'low', 'open', 'volume']
            )
            if hist is None or len(hist) == 0:
                if attempt < RECONNECT_MAX_RETRIES - 1:
                    logger_inst.log_exception('WARNING',
                        f'获取 {code} 历史数据为空，重试 {attempt + 1}/{RECONNECT_MAX_RETRIES}',
                        code=code, attempt=attempt + 1)
                    time.sleep(RECONNECT_BACKOFF * (attempt + 1))
                    continue
                return []

            bars = []
            for i in range(len(hist)):
                bar = {
                    'date': str(hist.index[i])[:10] if hasattr(hist.index[i], 'strftime') else str(hist.index[i])[:10],
                    'close': float(hist['close'].iloc[i]),
                    'high': float(hist['high'].iloc[i]),
                    'low': float(hist['low'].iloc[i]),
                    'open': float(hist['open'].iloc[i]),
                    'volume': float(hist['volume'].iloc[i]) if 'volume' in hist else 0,
                }
                bars.append(bar)
            return bars

        except Exception as e:
            if attempt < RECONNECT_MAX_RETRIES - 1:
                logger_inst.log_exception('WARNING',
                    f'获取 {code} 历史数据异常，重试 {attempt + 1}/{RECONNECT_MAX_RETRIES}: {e}',
                    code=code, attempt=attempt + 1, error=str(e))
                time.sleep(RECONNECT_BACKOFF * (attempt + 1))
            else:
                logger_inst.log_exception('ERROR',
                    f'获取 {code} 历史数据最终失败: {e}',
                    code=code, error=str(e))
    return []


def _get_current_price(context, code):
    """获取当前价格（带断线重连）"""
    ptrade_code = _format_ptrade_code(code)

    for attempt in range(RECONNECT_MAX_RETRIES):
        try:
            # 方式1: 尝试 get_current_data
            try:
                current = get_current_data()
                if current is not None:
                    if hasattr(current, '__getitem__'):
                        snap = current[ptrade_code]
                        if hasattr(snap, 'last_price'):
                            p = float(snap.last_price)
                            if p > 0:
                                return p
                        if isinstance(snap, dict) and 'last_price' in snap:
                            p = float(snap['last_price'])
                            if p > 0:
                                return p
                    elif hasattr(current, 'last_price'):
                        p = float(current.last_price)
                        if p > 0:
                            return p
            except Exception:
                pass

            # 方式2: 用 get_history 取最新一根
            hist = get_history(ptrade_code, count=1, unit='1d', fields=['close'])
            if hist is not None and len(hist) > 0:
                p = float(hist['close'].iloc[-1])
                if p > 0:
                    return p

            # 重试
            if attempt < RECONNECT_MAX_RETRIES - 1:
                logger_inst.log_exception('WARNING',
                    f'获取 {code} 当前价格为空，重试 {attempt + 1}/{RECONNECT_MAX_RETRIES}',
                    code=code, attempt=attempt + 1)
                time.sleep(RECONNECT_BACKOFF * (attempt + 1))

        except Exception as e:
            if attempt < RECONNECT_MAX_RETRIES - 1:
                logger_inst.log_exception('WARNING',
                    f'获取 {code} 当前价格异常，重试 {attempt + 1}/{RECONNECT_MAX_RETRIES}: {e}',
                    code=code, attempt=attempt + 1, error=str(e))
                time.sleep(RECONNECT_BACKOFF * (attempt + 1))
            else:
                logger_inst.log_exception('ERROR',
                    f'获取 {code} 当前价格最终失败: {e}',
                    code=code, error=str(e))
    return None


def _compute_recent_highs(context, codes, date_str, lookback):
    """计算近期最高价"""
    highs = {}
    for code in codes:
        bars = _get_history_bars(context, code, lookback + 5)
        if bars:
            recent = bars[-lookback:] if len(bars) >= lookback else bars
            highs[code] = max(b['high'] for b in recent if b['high'] > 0)
    return highs


# ============================================================
# 辅助函数 — 状态检查
# ============================================================

def _is_suspended(context, code):
    """检查是否停牌"""
    ptrade_code = _format_ptrade_code(code)
    try:
        current = get_current_data()
        if current is not None:
            snap = None
            try:
                snap = current[ptrade_code]
            except (KeyError, TypeError):
                pass
            if snap is not None:
                if hasattr(snap, 'is_paused') and snap.is_paused:
                    return True
                if isinstance(snap, dict) and snap.get('is_paused', False):
                    return True
                # 如果快照价格为 0 或 None，也视为停牌
                if hasattr(snap, 'last_price') and (snap.last_price == 0 or snap.last_price is None):
                    return True
                if isinstance(snap, dict) and snap.get('last_price', 1) == 0:
                    return True
    except Exception:
        pass
    return False


def _is_limit_up(context, code, price):
    """检查是否涨停"""
    ptrade_code = _format_ptrade_code(code)
    try:
        current = get_current_data()
        if current is not None:
            snap = None
            try:
                snap = current[ptrade_code]
            except (KeyError, TypeError):
                pass
            if snap is not None:
                high_limit = None
                if hasattr(snap, 'high_limit'):
                    high_limit = snap.high_limit
                elif isinstance(snap, dict):
                    high_limit = snap.get('high_limit')
                if high_limit and high_limit > 0 and price >= high_limit * 0.999:
                    return True
    except Exception:
        pass
    return False


def _is_limit_down(context, code, price):
    """检查是否跌停"""
    ptrade_code = _format_ptrade_code(code)
    try:
        current = get_current_data()
        if current is not None:
            snap = None
            try:
                snap = current[ptrade_code]
            except (KeyError, TypeError):
                pass
            if snap is not None:
                low_limit = None
                if hasattr(snap, 'low_limit'):
                    low_limit = snap.low_limit
                elif isinstance(snap, dict):
                    low_limit = snap.get('low_limit')
                if low_limit and low_limit > 0 and price <= low_limit * 1.001:
                    return True
    except Exception:
        pass
    return False


def _check_pending_orders(context):
    """检查未成交委托，处理超时和部分成交"""
    try:
        orders = get_orders()
        timeout_sec = config.get('execution', {}).get('order_timeout', 60)

        for o_id, order_info in orders.items():
            status = None
            filled_qty = 0
            if hasattr(order_info, 'status'):
                status = order_info.status
                filled_qty = getattr(order_info, 'filled', 0)
            elif isinstance(order_info, dict):
                status = order_info.get('status')
                filled_qty = order_info.get('filled', 0)

            if status in ['pending', 'queued', 'partial_filled']:
                logger_inst.log_signal('WARNING',
                    f'未成交委托 {o_id} 状态: {status} | 已成交: {filled_qty}',
                    order_id=o_id, status=status, filled=filled_qty)

                # 撤销超时未成交委托
                try:
                    cancel_order(o_id)
                    logger_inst.log_signal('WARNING', f'撤销超时委托: {o_id}',
                                          order_id=o_id)
                except Exception as e:
                    logger_inst.log_exception('ERROR', f'撤单失败 {o_id}: {e}',
                                             order_id=o_id, error=str(e))

    except Exception as e:
        logger_inst.log_exception('ERROR', f'检查未成交委托异常: {e}', error=str(e))


# ============================================================
# 辅助函数 — 下单
# ============================================================

def _calc_order_amount(price):
    """计算下单金额"""
    capital = config['account']['initial_capital']
    max_amount = config['risk']['max_order_amount']
    return min(capital, max_amount)


def _execute_sell(context, code, shares, price, reason, date_str):
    """执行卖出"""
    ptrade_code = _format_ptrade_code(code)

    # 检查跌停
    if _is_limit_down(context, code, price):
        logger_inst.log_signal('WARNING', f'{code} 跌停，无法卖出',
                               code=code, date=date_str, reason=reason)
        return

    # 计算实际卖出价格（含滑点和费用）
    sell_price = price * (1 - strategy.slippage - strategy.sell_fee - strategy.stamp_tax)

    # 记录台账
    order_id = ledger.record_order(
        code=code, side='sell', price=price, qty=shares,
        signal=reason, platform='ptrade'
    )

    # PTrade 下单
    try:
        if config['mode'] == 'live' or config['mode'] == 'paper':
            # PTrade 下单 API: order(security, amount, style=None)
            # 负数表示卖出
            result = order(ptrade_code, -shares)
            logger_inst.log_trade('INFO',
                f'卖出委托 {code} | 数量: {shares} | 价格: {price:.4f} | 原因: {reason}',
                code=code, side='sell', qty=shares, price=price,
                reason=reason, order_id=order_id, date=date_str)

            # 等待成交确认（带超时）
            _wait_for_fill(context, result, order_id, date_str, is_sell=True)
        else:
            logger_inst.log_trade('INFO',
                f'[回测] 卖出 {code} | 数量: {shares} | 价格: {price:.4f} | 原因: {reason}',
                code=code, side='sell', qty=shares, price=price,
                reason=reason, order_id=order_id, date=date_str)

        # 更新策略状态
        trade_info = strategy.on_exit(reason, sell_price, date_str)
        ledger.update_fill(order_id, sell_price, shares,
                          strategy.sell_fee * price * shares + strategy.stamp_tax * price * shares,
                          strategy.slippage * price * shares)

        logger_inst.log_trade('INFO',
            f'卖出成交 {code} | PnL: {trade_info["pnl"]:.0f} | 收益率: {trade_info["return"]:.2%}',
            code=code, pnl=trade_info['pnl'], return_pct=trade_info['return'])

        if trade_info.get('pool_switch'):
            logger_inst.log_signal('INFO',
                f'池切换: {trade_info["prev_pool"]} → {trade_info["new_pool"]}',
                prev=trade_info['prev_pool'], new=trade_info['new_pool'])

    except Exception as e:
        logger_inst.log_exception('ERROR', f'卖出下单失败 {code}: {e}',
                                 code=code, error=str(e), shares=shares, price=price)
        ledger.record_cancel(order_id, f'下单异常: {e}')


def _execute_buy(context, code, shares, price, ratio, date_str):
    """执行买入"""
    ptrade_code = _format_ptrade_code(code)

    # 检查资金是否充足
    try:
        portfolio = context.portfolio
        available_cash = getattr(portfolio, 'available_cash', None)
        if available_cash is not None:
            required = shares * price
            if available_cash < required:
                logger_inst.log_signal('WARNING',
                    f'资金不足: 需要 {required:.0f} | 可用 {available_cash:.0f} | 缩减数量',
                    code=code, required=required, available=available_cash)
                shares = int(available_cash / price / 100) * 100
                if shares <= 0:
                    logger_inst.log_signal('WARNING', f'资金不足，放弃买入 {code}', code=code)
                    return
    except Exception:
        # 无法获取资金信息，继续尝试下单
        pass

    # 计算实际买入价格
    buy_price = price * (1 + strategy.slippage + strategy.buy_fee)

    # 记录台账
    order_id = ledger.record_order(
        code=code, side='buy', price=price, qty=shares,
        signal=f'MA{strategy.fast_ma}>MA{strategy.slow_ma}|ratio={ratio:.3f}',
        platform='ptrade'
    )

    # PTrade 下单
    try:
        if config['mode'] == 'live' or config['mode'] == 'paper':
            result = order(ptrade_code, shares)
            logger_inst.log_trade('INFO',
                f'买入委托 {code} | 数量: {shares} | 价格: {price:.4f} | 比值: {ratio:.3f}',
                code=code, side='buy', qty=shares, price=price,
                ratio=ratio, order_id=order_id, date=date_str)

            # 等待成交确认（带超时）
            _wait_for_fill(context, result, order_id, date_str, is_sell=False)
        else:
            logger_inst.log_trade('INFO',
                f'[回测] 买入 {code} | 数量: {shares} | 价格: {price:.4f} | 比值: {ratio:.3f}',
                code=code, side='buy', qty=shares, price=price,
                ratio=ratio, order_id=order_id, date=date_str)

        # 更新策略状态
        strategy.on_entry(code, buy_price, shares, date_str)
        ledger.update_fill(order_id, buy_price, shares,
                          strategy.buy_fee * price * shares,
                          strategy.slippage * price * shares)

    except Exception as e:
        logger_inst.log_exception('ERROR', f'买入下单失败 {code}: {e}',
                                 code=code, error=str(e), shares=shares, price=price)
        ledger.record_cancel(order_id, f'下单异常: {e}')


def _wait_for_fill(context, order_result, ledger_order_id, date_str, is_sell=False,
                   timeout=None):
    """
    等待订单成交（带超时）
    处理部分成交：超时后撤销未成交部分
    """
    if timeout is None:
        timeout = config.get('execution', {}).get('order_timeout', 60)

    # PTrade order() 返回值可能直接是 order_id 或对象
    ptrade_order_id = None
    if order_result is not None:
        if isinstance(order_result, str):
            ptrade_order_id = order_result
        elif hasattr(order_result, 'order_id'):
            ptrade_order_id = order_result.order_id
        elif isinstance(order_result, dict):
            ptrade_order_id = order_result.get('order_id')

    if ptrade_order_id is None:
        logger_inst.log_signal('DEBUG', f'未获取到 PTrade order_id，跳过等待成交',
                              ledger_order_id=ledger_order_id)
        return

    elapsed = 0
    poll_interval = 2  # 每2秒检查一次
    last_filled = 0

    while elapsed < timeout:
        try:
            orders = get_orders()
            order_info = orders.get(ptrade_order_id)

            if order_info is None:
                # 订单不存在，可能已完成
                break

            status = None
            filled = 0
            if hasattr(order_info, 'status'):
                status = order_info.status
                filled = getattr(order_info, 'filled', 0)
            elif isinstance(order_info, dict):
                status = order_info.get('status')
                filled = order_info.get('filled', 0)

            if status in ['filled', 'complete', 'all_filled']:
                logger_inst.log_signal('DEBUG',
                    f'订单 {ptrade_order_id} 全部成交 | 数量: {filled}',
                    order_id=ptrade_order_id, filled=filled)
                return

            if status in ['cancelled', 'canceled', 'rejected']:
                logger_inst.log_signal('WARNING',
                    f'订单 {ptrade_order_id} 状态: {status}',
                    order_id=ptrade_order_id, status=status)
                return

            # 部分成交
            if filled > last_filled:
                logger_inst.log_signal('INFO',
                    f'订单 {ptrade_order_id} 部分成交 | 已成交: {filled}',
                    order_id=ptrade_order_id, filled=filled)
                last_filled = filled

        except Exception as e:
            logger_inst.log_exception('WARNING',
                f'查询订单状态异常: {e}', order_id=ptrade_order_id, error=str(e))

        time.sleep(poll_interval)
        elapsed += poll_interval

    # 超时处理：撤销未成交部分
    logger_inst.log_signal('WARNING',
        f'订单 {ptrade_order_id} 超时 {timeout}s，撤销未成交部分',
        order_id=ptrade_order_id, timeout=timeout, filled=last_filled)

    try:
        cancel_order(ptrade_order_id)
        if last_filled > 0:
            logger_inst.log_signal('INFO',
                f'订单 {ptrade_order_id} 部分成交后撤单 | 已成交: {last_filled}',
                order_id=ptrade_order_id, filled=last_filled)
    except Exception as e:
        logger_inst.log_exception('ERROR',
            f'超时撤单失败: {e}', order_id=ptrade_order_id, error=str(e))