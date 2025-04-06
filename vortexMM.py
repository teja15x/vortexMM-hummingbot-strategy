from decimal import Decimal
from typing import Dict, List

from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory, CandlesConfig
from hummingbot.connector.connector_base import ConnectorBase

class AVTMMStrategy(ScriptStrategyBase):
    order_amount = 0.02
    refresh_interval = 10
    trading_pair = "ETH-USDT"
    exchange = "binance_paper_trade"
    price_source = PriceType.MidPrice

    candle_config = CandlesConfig(
        connector="binance",
        trading_pair=trading_pair,
        interval="1m",
        max_records=200
    )
    candles = CandlesFactory.get_candle(candle_config)
    markets = {exchange: {trading_pair}}

    def _init_(self, connectors: Dict[str, ConnectorBase]):
        super()._init_(connectors)
        self.bid_spread = Decimal("0.001")
        self.ask_spread = Decimal("0.001")
        self.next_update = 0
        self.candles.start()

    def on_stop(self):
        self.candles.stop()

    def on_tick(self):
        if self.current_timestamp < self.next_update:
            return
        self.cancel_all_orders()
        self.update_indicators()
        proposal = self.generate_orders()
        adjusted = self.adjust_proposal_to_budget(proposal)
        self.place_orders(adjusted)
        self.next_update = self.current_timestamp + self.refresh_interval

    def update_indicators(self):
        df = self.candles.candles_df.copy()
        df["returns"] = df["close"].pct_change()
        df["volatility"] = df["returns"].rolling(window=10).std()
        df["ma"] = df["close"].rolling(window=10).mean()
        vol = df["volatility"].iloc[-1] or 0.001
        trend_up = df["close"].iloc[-1] > df["ma"].iloc[-1]
        self.bid_spread = Decimal(str(vol * (0.5 if trend_up else 1)))
        self.ask_spread = Decimal(str(vol * (1.5 if trend_up else 1)))

    def generate_orders(self) -> List[OrderCandidate]:
        ref_price = self.connectors[self.exchange].get_price_by_type(self.trading_pair, self.price_source)
        best_bid = self.connectors[self.exchange].get_price(self.trading_pair, False)
        best_ask = self.connectors[self.exchange].get_price(self.trading_pair, True)

        buy_price = min(ref_price * (1 - self.bid_spread), best_bid)
        sell_price = max(ref_price * (1 + self.ask_spread), best_ask)

        buy = OrderCandidate(
            trading_pair=self.trading_pair,
            is_maker=True,
            order_type=OrderType.LIMIT,
            order_side=TradeType.BUY,
            amount=Decimal(self.order_amount),
            price=buy_price
        )
        sell = OrderCandidate(
            trading_pair=self.trading_pair,
            is_maker=True,
            order_type=OrderType.LIMIT,
            order_side=TradeType.SELL,
            amount=Decimal(self.order_amount),
            price=sell_price
        )
        return [buy, sell]

    def adjust_proposal_to_budget(self, proposal: List[OrderCandidate]) -> List[OrderCandidate]:
        return self.connectors[self.exchange].budget_checker.adjust_candidates(proposal, all_or_none=True)

    def place_orders(self, proposal: List[OrderCandidate]):
        for order in proposal:
            if order.order_side == TradeType.BUY:
                self.buy(self.exchange, order.trading_pair, order.amount, order.order_type, order.price)
            else:
                self.sell(self.exchange, order.trading_pair, order.amount, order.order_type, order.price)

    def cancel_all_orders(self):
        for order in self.get_active_orders(self.exchange):
            self.cancel(self.exchange, order.trading_pair, order.client_order_id)

    def did_fill_order(self, event: OrderFilledEvent):
        msg = f"{event.trade_type.name} {event.amount} {event.trading_pair} at {event.price}"
        self.log_with_clock(event_type=event.type, msg=msg)
        self.notify_hb_app_with_timestamp(msg)
      
