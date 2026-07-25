"""
Backtrader Runner: Executes a generated strategy against historical data.

Handles date range filtering (via Polars), broker setup, analyzer attachment,
and result collection into the BacktestResult schema.
"""
import logging
import math
from typing import Any, Dict, List, Optional, Tuple, Type, Union

import backtrader as bt
import pandas as pd
import polars as pl

from .custom_analyzers import CriticAnalyzer


class SwapObserver(bt.Observer):
    """
    Observer that charges swap/rollover on open positions each bar.
    
    Swap is charged on weekdays only (Mon-Fri), NOT on weekends.
    Wednesday: triple swap (Mon+Tue+Wed)
    Friday: triple swap (Fri+Sat+Sun)
    
    Swap rates are per standard lot (100 oz for XAUUSD).
    For 0.1 lot, swap is divided by 10.
    """
    
    lines = ('swap_charge',)
    
    params = (
        ('swap_rate_long_per_lot', -71.50),  # XAUUSD: per standard lot per night (negative = pay to hold long)
        ('swap_rate_short_per_lot', 32.50),  # XAUUSD: per standard lot per night (positive = receive on short)
    )
    
    def next(self):
        """Charge swap on open positions."""
        # Access data via self.data[0] (standard observer pattern)
        try:
            dt = self.data[0].datetime.datetime(0)
        except (AttributeError, IndexError):
            return

        weekday = dt.weekday()  # 0=Mon, 5=Sat, 6=Sun

        # No swap on weekends
        if weekday >= 5:
            return

        # Calculate swap multiplier
        if weekday == 2:  # Wednesday - triple
            multiplier = 3
        elif weekday == 4:  # Friday - triple
            multiplier = 3
        else:  # Mon, Tue, Thu
            multiplier = 1

        # Check all positions
        total_swap = 0.0
        # Use self.strategy or self.owner (both should work in observers)
        owner = getattr(self, 'strategy', None) or getattr(self, '_owner', None)
        if owner is None:
            return

        for data, pos in owner.getpositions():
            if pos.size == 0:
                continue

            # Convert position size to lots (1 standard lot = 100 oz for XAUUSD)
            lots = abs(pos.size) / 100.0

            if pos.size > 0:  # Long position
                swap_per_lot = self.p.swap_rate_long_per_lot
            else:  # Short position
                swap_per_lot = self.p.swap_rate_short_per_lot

            total_swap += lots * swap_per_lot * multiplier

        if total_swap != 0.0:
            # Deduct swap from broker cash (add positive = receive, add negative = pay)
            owner.broker.add_cash(total_swap)
            # Record in line for tracking
            self.lines.swap_charge[0] = total_swap

logger = logging.getLogger(__name__)


class SpreadPandasData(bt.feeds.PandasData):
    """PandasData that models spread.

    Expects the input DataFrame to have 'bid', 'ask', and 'mid' columns.
    Buy orders execute at Ask, sell orders at Bid, portfolio value uses mid.
    """

    lines = ("mid",)

    params = (
        ("mid", "mid"),
        ("open", "ask"),
        ("high", "ask"),
        ("low", "bid"),
        ("close", "mid"),
        ("bid", "bid"),
        ("ask", "ask"),
    )


def load_and_slice_data(
    data_source: Union[str, pd.DataFrame, pl.DataFrame],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    Load OHLCV data and apply date-range slicing.

    Args:
        data_source: Path to parquet/CSV, a Polars DataFrame, or a Pandas DataFrame.
        start_date: Inclusive start filter (ISO string).
        end_date: Inclusive end filter (ISO string).

    Returns:
        Pandas DataFrame with 'datetime' as the DatetimeIndex.

    Raises:
        ValueError: If required columns are missing or no data after filtering.
    """
    # Load FULL dataset - MT4 loads all history for indicator warmup.
    # SMA(200) needs 200+ prior bars; EMA(55) needs 55+.
    # Date slicing is applied AFTER loading to preserve indicator accuracy.
    if isinstance(data_source, pl.DataFrame):
        df_pl = data_source.clone()
    elif isinstance(data_source, pd.DataFrame):
        df_pl = pl.DataFrame(data_source)
    else:
        # Path to file
        path = str(data_source)
        if path.lower().endswith(".parquet"):
            df_pl = pl.read_parquet(path)
        else:
            df_pl = pl.read_csv(path, try_parse_dates=True)

        if "datetime" not in df_pl.columns and {"date", "time"}.issubset(df_pl.columns):
            df_pl = df_pl.with_columns(
                pl.concat_str(
                    [pl.col("date").cast(pl.String), pl.lit(" "), pl.col("time").cast(pl.String)]
                )
                .str.strptime(pl.Datetime, format="%Y-%m-%d %H:%M:%S", strict=False)
                .alias("datetime")
            )

        # Ensure datetime column exists
        if "datetime" not in df_pl.columns:
            raise ValueError("Data must contain a 'datetime' column or both 'date' and 'time' columns")

        # Ensure datetime is Datetime type
    if not df_pl.schema["datetime"].is_temporal():
        df_pl = df_pl.with_columns(pl.col("datetime").str.strptime(pl.Datetime, format="%Y-%m-%d %H:%M:%S"))

    # Sort
    df_pl = df_pl.sort("datetime")

    # Apply end_date filter first (if specified)
    if end_date:
        df_pl = df_pl.filter(pl.col("datetime") <= pl.lit(end_date).str.strptime(pl.Datetime))

    if len(df_pl) == 0:
        raise ValueError(f"No data available for range {start_date or 'start'} to {end_date or 'end'}")

    # Convert to Pandas for backtrader
    df_pd = df_pl.to_pandas()
    df_pd["datetime"] = pd.to_datetime(df_pd["datetime"])
    df_pd = df_pd.set_index("datetime")
    df_pd.index.name = None

    # Add spread columns for MT4-style pricing
    # XAUUSD spread = 50 pips = $0.50 total
    # Mid = close (we treat close as the mid price)
    # Ask = mid + 0.25 (half spread)
    # Bid = mid - 0.25 (half spread)
    spread_total = 0.50  # 50 pips for XAUUSD
    half_spread = spread_total / 2.0
    df_pd["mid"] = df_pd["close"]
    df_pd["ask"] = df_pd["close"] + half_spread
    df_pd["bid"] = df_pd["close"] - half_spread

    return df_pd


def run_backtest(
    strategy_class: Type[bt.Strategy],
    data_class: Type[bt.feeds.PandasData],
    data_source: Union[str, pd.DataFrame, pl.DataFrame],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    initial_capital: float = 10_000.0,  # MT4 uses $10K initial deposit
    commission: float = 0.035,  # MT4: $7/lot round turn = $0.035/oz (1 lot = 100 oz)
    slippage: float = 0.0,
    position_sizing: str = 'fixed',
    stake: int = 1,
    coc: bool = False,
    coo: bool = True,
    swap_rate_long_per_lot: float = -71.50,  # XAUUSD: per standard lot per night
    swap_rate_short_per_lot: float = 32.50,  # XAUUSD: per standard lot per night
    warmup_bars: int = 300,
    **strategy_params,  # Extra params forwarded to cerebro.addstrategy()
) -> Dict[str, Any]:
    """
    Run a backtest with the given strategy and data.

    Args:
        strategy_class: Compiled bt.Strategy subclass.
        data_class: Custom bt.feeds.PandasData subclass.
        data_source: Path, Polars DF, or Pandas DF.
        start_date: Inclusive start date (ISO).
        end_date: Inclusive end date (ISO).
        initial_capital: Starting broker cash.
        commission: Per-trade commission fraction or fixed amount.
        slippage: Per-trade slippage fraction.
        position_sizing: 'fixed' or 'dynamic'.
        stake: Fixed position size stake.
        coc: Close-on-close execution.
        coo: Close-on-open execution.
        swap_rate_long_per_lot: Swap rate for long positions per standard lot per night.
        swap_rate_short_per_lot: Swap rate for short positions per standard lot per night.

    Returns:
        Dict with keys: sharpe_ratio, max_drawdown, total_return_pct,
        total_trades, win_rate, profit_factor, trades (detailed list),
        plus expanded metrics (realized_return, sqn, sortino, calmar, etc.).
    """
    # Load FULL dataset for indicator warmup (MT4-style).
    # SMA(200) needs 200+ prior bars; EMA(55) needs 55+.
    # Only filter by end_date; start_date is handled by strategy's warmup skip logic.
    df = load_and_slice_data(data_source, start_date=None, end_date=end_date)

    # Record actual data start for reporting
    data_start_date_actual = str(df.index[0])[:10]
    data_end_date_actual = str(df.index[-1])[:10]

    # Data feed
    data = data_class(dataname=df)

    # Cerebro
    cerebro = bt.Cerebro()
    cerebro.adddata(data)
    # Pass start_date and warmup_bars to strategy so it can skip warmup period
    cerebro.addstrategy(
        strategy_class,
        start_date=start_date,
        warmup_bars=warmup_bars,
        **strategy_params
    )

    # Position sizing
    if position_sizing == 'fixed':
        # Fixed stake — strategy must use self.p.fixed_shares or self.buy(size=stake)
        pass  # No sizer; strategy controls size directly
    else:
        # Default: 100% equity per trade
        cerebro.addsizer(bt.sizers.PercentSizer, percents=100)

    # Broker
    cerebro.broker.setcash(initial_capital)

    # Fixed commission per oz (no leverage multiplier - XAUUSD is priced per oz)
    cerebro.broker.setcommission(
        commission=commission,
        commtype=bt.CommInfoBase.COMM_FIXED,
    )
    if slippage > 0:
        cerebro.broker.set_slippage_perc(slippage)

    # Analyzer
    cerebro.addanalyzer(CriticAnalyzer, _name="critic")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trade_analyzer")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(
        bt.analyzers.SharpeRatio,
        _name="sharpe",
        timeframe=bt.TimeFrame.Days,
    )
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")

    # Swap observer: charges swap/rollover on open positions (MT4-style)
    cerebro.addobserver(
        SwapObserver,
        swap_rate_long_per_lot=swap_rate_long_per_lot,
        swap_rate_short_per_lot=swap_rate_short_per_lot,
    )

    # Set execution model for MT4 compatibility: orders execute at bar close
    cerebro.broker.set_coo(coo)
    cerebro.broker.set_coc(coc)  # coc=True: execute at bar close (MT4-style)
    # NOTE: bt.analyzers.SQN removed — it reports 0 trades when strategies
    # pass string tradeid to buy/close/sell (backtrader metaclass bug).
    # We compute SQN manually from the critic trade log below.

    # Run (single pass)
    results = cerebro.run(stdstats=False)
    strat = results[0]

    # ── Extract metrics ────────────────────────────────────────────

    # Check for manual_trade_log on strategy early (needed for P&L calc)
    manual_trade_log = getattr(strat, "manual_trade_log", None) or []

    # Critic trade log (fallback)
    critic = strat.analyzers.critic.get_analysis()
    critic_trade_log = critic.get("trades", []) or []
    max_position_size = critic.get("max_position_size", 0.0)

    # Use manual_trade_log if available, otherwise fall back to critic_trade_log
    trade_log = manual_trade_log if manual_trade_log else critic_trade_log

    # Final portfolio value
    final_value = cerebro.broker.getvalue()
    final_cash = cerebro.broker.getcash()

    # Net profit: use broker final value (includes swap charges)
    # Manual trade log PnL doesn't include swap/rollover, so broker value is more accurate
    net_profit = final_value - initial_capital
    total_return_pct = (net_profit / initial_capital) * 100.0

    # Open position detection
    has_open_position = False
    open_position_value = 0.0
    unrealized_pnl = 0.0
    for pos in cerebro.broker.positions.values():
        if pos.size != 0:
            has_open_position = True
            open_position_value = pos.size * df["close"].iloc[-1]
            unrealized_pnl = (pos.size * df["close"].iloc[-1]) - (pos.size * pos.price)
            break

    # Data stats (use actual sliced range including warmup)
    data_start_date = data_start_date_actual
    data_end_date = data_end_date_actual
    total_bars = len(df)
    first_price = df["close"].iloc[0]
    last_price = df["close"].iloc[-1]

    # Buy-and-hold benchmark
    bhh_shares = initial_capital / first_price
    bhh_final = bhh_shares * last_price
    bhh_return_pct = ((bhh_final - initial_capital) / initial_capital) * 100.0

    # Max drawdown
    dd = strat.analyzers.drawdown.get_analysis()
    max_dd_obj = dd.get("max", dd)
    max_drawdown_pct = max_dd_obj.get("drawdown", 0.0)

    # Calmar ratio = annualized return / max drawdown
    years = total_bars / 252.0 if total_bars > 0 else 1.0
    ret_ratio = final_value / initial_capital if initial_capital > 0 else 0.0
    if ret_ratio > 0 and years > 0:
        annualized_return = ret_ratio ** (1.0 / years) - 1.0
    else:
        annualized_return = 0.0  # Avoid complex numbers from negative base
    calmar_ratio = annualized_return / (max_drawdown_pct / 100.0) if max_drawdown_pct > 0 else None

    # (trade_log, net_profit, total_return_pct already computed above from manual_trade_log)

    # Realized return from closed trades only (excludes unrealized PnL)
    if trade_log:
        total_pnl = sum(t.get("pnl_net", 0) for t in trade_log)
    else:
        total_pnl = 0.0
    realized_return_pct = (total_pnl / initial_capital) * 100.0

    # Win rate & trade count: prefer manual_trade_log if available,
    # otherwise fall back to TradeAnalyzer + CriticAnalyzer.
    ta = strat.analyzers.trade_analyzer.get_analysis()
    if trade_log and len(trade_log) > 0:
        closed = len(trade_log)
        won = sum(1 for t in trade_log if t.get("pnl_net", 0) > 0)
        lost = sum(1 for t in trade_log if t.get("pnl_net", 0) <= 0)
        win_rate = won / closed if closed > 0 else 0.0
    else:
        closed = ta.get("total", {}).get("closed", 0)
        won = ta.get("won", {}).get("total", 0)
        lost = ta.get("lost", {}).get("total", 0)
        win_rate = won / closed if closed > 0 else 0.0
        if closed == 0 and critic_trade_log:
            closed = len(critic_trade_log)
            won = sum(1 for t in critic_trade_log if t.get("pnl_net", 0) > 0)
            lost = sum(1 for t in critic_trade_log if t.get("pnl_net", 0) <= 0)
            win_rate = won / closed if closed > 0 else 0.0

    # Sharpe
    sharpe_raw = strat.analyzers.sharpe.get_analysis()
    sharpe_val = sharpe_raw.get("sharperatio")
    if isinstance(sharpe_val, (list, tuple)):
        sharpe_val = sharpe_val[0] if sharpe_val else None
    sharpe_ratio = float(sharpe_val) if sharpe_val is not None else None

    # Sortino ratio (approximation from returns analyzer)
    returns_raw = strat.analyzers.returns.get_analysis()
    rnorm = returns_raw.get("rnorm", 0.0)
    if isinstance(rnorm, (list, tuple)):
        rnorm = rnorm[0] if rnorm else 0.0
    sortino_ratio = None
    if sharpe_ratio is not None and max_drawdown_pct > 0:
        sortino_ratio = rnorm / (max_drawdown_pct / 100.0)

    # SQN — System Quality Number — computed manually from critic trade log
    # because backtrader's SQN analyzer reports 0 trades when strategies
    # pass string tradeid to buy/close/sell (backtrader metaclass bug).
    # Formula: SQN = sqrt(N) * (avg_pnl / std_pnl) where N = number of trades
    sqn_val = None
    if trade_log and len(trade_log) > 1:
        pnls = [t.get("pnl_net", 0) for t in trade_log]
        mean_pnl = sum(pnls) / len(pnls)
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)
        std_pnl = math.sqrt(variance)
        if std_pnl > 0:
            sqn_val = math.sqrt(len(pnls)) * (mean_pnl / std_pnl)

    # Profit factor from critic trade log (more accurate)
    if trade_log:
        gross_wins = sum(abs(t.get("pnl_net", 0)) for t in trade_log if t.get("pnl_net", 0) > 0)
        gross_losses = sum(abs(t.get("pnl_net", 0)) for t in trade_log if t.get("pnl_net", 0) <= 0)
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else None
    else:
        won_profit = ta.get("won", {}).get("profit", {}).get("total", 0.0)
        lost_loss = ta.get("lost", {}).get("loss", {}).get("total", 0.0)
        profit_factor = won_profit / abs(lost_loss) if lost_loss != 0 else None

    # Avg win / avg loss
    if trade_log:
        winning_pnls = [t.get("pnl_net", 0) for t in trade_log if t.get("pnl_net", 0) > 0]
        losing_pnls = [t.get("pnl_net", 0) for t in trade_log if t.get("pnl_net", 0) <= 0]
        avg_win = sum(winning_pnls) / len(winning_pnls) if winning_pnls else 0.0
        avg_loss = sum(losing_pnls) / len(losing_pnls) if losing_pnls else 0.0
    else:
        avg_win = ta.get("won", {}).get("average")
        avg_loss = ta.get("lost", {}).get("average")
        if avg_win is None and won:
            won_profit = ta.get("won", {}).get("profit", {}).get("total", 0.0)
            avg_win = won_profit / won
        if avg_loss is None and lost:
            lost_loss = ta.get("lost", {}).get("loss", {}).get("total", 0.0)
            avg_loss = lost_loss / lost

    # Total commissions from trade log
    total_commissions = sum(t.get("commission_paid", 0) for t in trade_log) if trade_log else 0.0

    # Avg trade duration
    durations = [t.get("trade_duration", 0) for t in trade_log] if trade_log else []
    avg_trade_duration = sum(durations) / len(durations) if durations else 0
    max_trade_duration = max(durations) if durations else 0
    min_trade_duration = min(durations) if durations else 0

    # Max consecutive losses
    max_consec_losses = 0
    current_streak = 0
    for t in trade_log:
        if t.get("pnl_net", 0) < 0:
            current_streak += 1
            max_consec_losses = max(max_consec_losses, current_streak)
        else:
            current_streak = 0

    # Avg MAFE / MFE
    avg_mafe = 0.0
    avg_mfe = 0.0
    if trade_log:
        mafes = [t.get("mafe", 0) for t in trade_log if t.get("mafe") is not None]
        mfes = [t.get("mfe", 0) for t in trade_log if t.get("mfe") is not None]
        avg_mafe = sum(mafes) / len(mafes) if mafes else 0.0
        avg_mfe = sum(mfes) / len(mfes) if mfes else 0.0

    # Strategy params snapshot
    strategy_params = {}
    try:
        for pname, pval in strat.params._getAliases().items():
            strategy_params[pname] = pval
    except Exception:
        pass

    # Error flag: set if strategy had an open position at end (may indicate
    # missing exit logic) or if realized return diverges significantly from total
    error_flag = None
    if has_open_position and closed > 0:
        error_flag = "open_position_at_end"
    elif has_open_position and closed == 0:
        error_flag = "never_closed_any_trade"

    return {
        # Portfolio metrics
        "initial_capital": initial_capital,
        "final_value": round(final_value, 2),
        "final_cash": round(final_cash, 2),
        "net_profit": round(net_profit, 2),
        "total_return_pct": round(total_return_pct, 2),
        "realized_return_pct": round(realized_return_pct, 2),
        "annualized_return": round(annualized_return, 4),
        "has_open_position": has_open_position,
        "open_position_value": round(open_position_value, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),

        # Risk metrics
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "sharpe_ratio": round(sharpe_ratio, 4) if sharpe_ratio is not None else None,
        "sortino_ratio": round(sortino_ratio, 4) if sortino_ratio is not None else None,
        "calmar_ratio": round(calmar_ratio, 4) if calmar_ratio is not None else None,
        "sqn": round(sqn_val, 2) if sqn_val is not None else None,

        # Trade statistics
        "total_trades": closed,
        "winning_trades": won,
        "losing_trades": lost,
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2) if avg_win else 0.0,
        "avg_loss": round(avg_loss, 2) if avg_loss else 0.0,
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "total_commissions": round(total_commissions, 4),
        "max_consecutive_losses": max_consec_losses,

        # Trade duration stats
        "avg_trade_duration": round(avg_trade_duration, 1),
        "max_trade_duration": max_trade_duration,
        "min_trade_duration": min_trade_duration,

        # Position sizing
        "max_position_size": round(max_position_size, 2),

        # Excursion stats
        "avg_mafe": round(avg_mafe, 2),
        "avg_mfe": round(avg_mfe, 2),

        # Data info
        "data_start_date": data_start_date,
        "data_end_date": data_end_date,
        "total_bars": total_bars,
        "first_price": round(first_price, 2),
        "last_price": round(last_price, 2),
        "buy_and_hold_return_pct": round(bhh_return_pct, 2),

        # Strategy params snapshot
        "strategy_params": strategy_params,

        # Error flag
        "error_flag": error_flag,

        # Per-trade log
        "trades": trade_log,
    }
