"""
Custom Analyzers for the Backtest Engine v2.

Provides specialized analyzers that produce per-trade diagnostics
tailored for the LLM Critic.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

import backtrader as bt


class CriticAnalyzer(bt.Analyzer):
    """
    Uses notify_trade (reliable for complete trade lifecycle) to capture
    every closed trade — both LONG and SHORT — with full diagnostics.
    """

    def __init__(self):
        self.trades: List[Dict[str, Any]] = []
        self._max_position_size: float = 0.0

        # Per-trade tracking via notify_trade
        self._entry_price: Optional[float] = None
        self._entry_size: Optional[int] = None
        self._entry_date: Optional[datetime] = None
        self._entry_tradeid: str = ""
        self._highest_price: Optional[float] = None
        self._lowest_price: Optional[float] = None
        self._commission: float = 0.0
        self._trade_prices: Dict[int, float] = {}  # tradeid -> entry/exit prices
        self._exit_reasons: Dict[int, str] = {}  # tradeid -> exit reason

    def notify_order(self, order):
        """Track order tradeids and commissions."""
        if order.status == order.Completed:
            self._commission += order.executed.comm

    def notify_trade(self, trade):
        """
        Reliable trade lifecycle using trade.status:
        - trade.status == bt.Trade.Open  → entry
        - trade.status == bt.Trade.Closed → exit
        """
        if trade.status == bt.Trade.Open:
            # Track entry info
            self._entry_price = trade.price
            self._entry_size = trade.size  # signed: positive=long, negative=short
            self._entry_date = self._get_current_datetime()
            self._entry_tradeid = str(getattr(trade, 'tradeid', '')) or "Unknown Entry"
            self._highest_price = self.strategy.data.close[0]
            self._lowest_price = self.strategy.data.close[0]
            self._commission = 0.0
            # Initialize exit prices for this trade (empty initially)

        elif trade.status == bt.Trade.Closed:
            # Get exit price from the (potentially overridden) data.close[0]
            exit_price = self.strategy.data.close[0]
            exit_date = self._get_current_datetime()
            close_size = abs(self._entry_size) if self._entry_size else 0

            # Get trade ID if available
            tradeid = getattr(trade, 'tradeid', '')
            if not tradeid:
                tradeid = len(self._trade_prices)  # fallback

            # Path 1: If we already captured entry and exit prices for standard trading
            # (not from wrapper overrides)
            if tradeid in self._trade_prices:
                entry_price = self._trade_prices[tradeid]['entry']
                exit_reason = self._trade_prices[tradeid].get('reason', 'Trade Closed')
                prices_used = tradeid in self._trade_prices
            else:
                # Path 2: Using trade price directly (when wrapper didn't override)
                entry_price = trade.price
                exit_price = trade.price
                prices_used = False
                exit_reason = "Trade Closed"

            close_size = abs(self._entry_size) if self._entry_size else 0

            # trade.pnlcomm = PnL net of commission, trade.pnl = gross PnL
            pnl_net = trade.pnlcomm
            pnl_gross = trade.pnl

            trade_value = abs(entry_price * close_size) if entry_price else 0.0
            pnl_pct = (pnl_net / trade_value * 100.0) if trade_value else 0.0

            # MAFE/MFE — direction-aware
            mafe = 0.0
            mfe = 0.0
            if self._highest_price is not None and self._lowest_price is not None and self._entry_price:
                if (self._entry_size or 0) > 0:
                    mfe = (self._highest_price - self._entry_price) * close_size
                    mafe = (self._lowest_price - self._entry_price) * close_size
                else:
                    mfe = (self._entry_price - self._lowest_price) * close_size
                    mafe = (self._highest_price - self._entry_price) * close_size

            self.trades.append({
                "entry_date": self._entry_date.isoformat() if self._entry_date else None,
                "exit_date": exit_date.isoformat() if exit_date else None,
                "trade_id": tradeid,
                "entry_price": round(entry_price, 4),
                "exit_price": round(exit_price, 4),
                "price_used_entry": entry_price if not prices_used else 0.0,
                "price_used_exit": exit_price if not prices_used else 0.0,
                "trade_size": close_size,
                "exit_type": "close",
                "entry_tradeid": self._entry_tradeid,
                "exit_reason": exit_reason,
                "pnl_gross": round(pnl_gross, 2),
                "pnl_net": round(pnl_net, 2),
                "pnl_percent": round(pnl_pct, 4),
                "commission_paid": round(self._commission, 4),
                "trade_duration": 0,
                "mafe": round(mafe, 2),
                "mfe": round(mfe, 2),
            })

            # Reset tracking state for next trade
            self._entry_price = None
            self._entry_size = None
            self._entry_date = None
            self._entry_tradeid = ""
            self._highest_price = None
            self._lowest_price = None
            self._commission = 0.0

    def next(self):
        """Track price excursion during open positions."""
        if self._entry_price is not None:
            cp = self.strategy.data.close[0]
            if self._highest_price is None or cp > self._highest_price:
                self._highest_price = cp
            if self._lowest_price is None or cp < self._lowest_price:
                self._lowest_price = cp

        # Track max position size
        pos = self.strategy.broker.getposition(self.strategy.data)
        self._max_position_size = max(self._max_position_size, abs(pos.size))

    def _get_current_datetime(self) -> datetime:
        try:
            return self.strategy.datas[0].datetime.datetime(0)
        except Exception:
            return datetime.utcnow()

    def get_analysis(self):
        return {
            "trades": self.trades,
            "max_position_size": self._max_position_size,
        }
