import pytz
from datetime import datetime
from typing import Dict, Tuple, List
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtconstant
from xtquant.xtdata import subscribe_whole_quote, subscribe_quote, get_full_tick, get_instrument_detail

from vnpy.event import EventEngine, EVENT_TIMER
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    OrderData,
    TradeData,
    PositionData,
    AccountData,
    OrderRequest,
    CancelRequest,
    SubscribeRequest,
    ContractData,
    TickData
)
from vnpy.trader.constant import (
    OrderType,
    Direction,
    Exchange,
    Status,
    Product
)


# 委托状态映射
STATUS_XT2VT: Dict[str, Status] = {
    xtconstant.ORDER_UNREPORTED: Status.SUBMITTING,
    xtconstant.ORDER_WAIT_REPORTING: Status.SUBMITTING,
    xtconstant.ORDER_REPORTED: Status.NOTTRADED,
    xtconstant.ORDER_REPORTED_CANCEL: Status.CANCELLED,
    xtconstant.ORDER_PARTSUCC_CANCEL: Status.CANCELLED,
    xtconstant.ORDER_PART_CANCEL: Status.CANCELLED,
    xtconstant.ORDER_CANCELED: Status.CANCELLED,
    xtconstant.ORDER_PART_SUCC: Status.PARTTRADED,
    xtconstant.ORDER_SUCCEEDED: Status.ALLTRADED,
    xtconstant.ORDER_JUNK: Status.REJECTED
}

# 多空方向映射
DIRECTION_VT2XT: Dict[Direction, str] = {
    Direction.LONG: xtconstant.STOCK_BUY,
    Direction.SHORT: xtconstant.STOCK_SELL,
}
DIRECTION_XT2VT: Dict[str, Direction] = {v: k for k, v in DIRECTION_VT2XT.items()}

# 委托类型映射
ORDERTYPE_VT2XT: Dict[Tuple, int] = {
    (Exchange.SSE, OrderType.MARKET): xtconstant.MARKET_SH_CONVERT_5_CANCEL,
    (Exchange.SZSE, OrderType.MARKET): xtconstant.MARKET_SZ_CONVERT_5_CANCEL,
    (Exchange.SSE, OrderType.LIMIT): xtconstant.FIX_PRICE,
    (Exchange.SZSE, OrderType.LIMIT): xtconstant.FIX_PRICE,
}
ORDERTYPE_XT2VT: Dict[int, OrderType] = {
    50: OrderType.LIMIT,
    88: OrderType.MARKET,
}

# 交易所映射
EXCHANGE_XT2VT: Dict[str, Exchange] = {
    "SH": Exchange.SSE,
    "SZ": Exchange.SZSE,
}
EXCHANGE_VT2XT: Dict[Exchange, str] = {v: k for k, v in EXCHANGE_XT2VT.items()}

# 其他常量
CHINA_TZ = pytz.timezone("Asia/Shanghai")       # 中国时区

# 合约数据全局缓存字典
symbol_contract_map: Dict[str, ContractData] = {}


class XtGateway(BaseGateway):
    """
    VeighNa用于对接迅投XT的交易接口。
    """

    default_name: str = "XT"

    default_setting: Dict[str, str] = {
        "路径": "",
        "资金账号": ""
    }

    exchanges: List[str] = list(EXCHANGE_XT2VT.values())

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        """构造函数"""
        super().__init__(event_engine, gateway_name)

        self.td_api: "XtTdApi" = XtTdApi(self)
        self.md_api: "XtMdApi" = XtMdApi(self)

        self.orders: Dict[str, OrderData] = {}

    def connect(self, setting: dict) -> None:
        """连接交易接口"""
        path: str = setting["路径"]
        accountid: str = setting["资金账号"]

        self.td_api.init(path, accountid)
        self.md_api.connect()

        self.init_query()

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        self.md_api.subscribe(req)

    def send_order(self, req: OrderRequest) -> str:
        """委托下单"""
        return self.td_api.send_order(req)

    def cancel_order(self, req: CancelRequest) -> None:
        """委托撤单"""
        self.td_api.cancel_order(req)

    def query_account(self) -> None:
        """查询资金"""
        self.td_api.query_account()

    def query_position(self) -> None:
        """查询持仓"""
        self.td_api.query_position()

    def on_order(self, order: OrderData) -> None:
        """推送委托数据"""
        self.orders[order.orderid] = order  # 先做一次缓存
        super().on_order(order)

    def get_order(self, orderid: str) -> OrderData:
        """查询委托数据"""
        return self.orders.get(orderid, None)

    def close(self) -> None:
        """关闭接口"""
        self.td_api.close()

    def process_timer_event(self, event) -> None:
        """定时事件处理"""
        self.count += 1
        if self.count < 2:
            return
        self.count = 0

        func = self.query_functions.pop(0)
        func()
        self.query_functions.append(func)

    def init_query(self) -> None:
        """初始化查询任务"""
        self.count: int = 0
        self.query_functions: list = [self.query_account, self.query_position]
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)


class XtMdApi:

    def __init__(self, gateway: XtGateway):
        """构造函数"""
        super().__init__()

        self.gateway: XtGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.inited: bool = False
        self.subscribed: set = set()

    def onMarketData(self, datas: dict) -> None:
        """订阅行情回报"""
        for k, d in datas.items():
            symbol, exchange = k.split(".")
            if symbol in self.subscribed:
                tick: TickData = TickData(
                    symbol=symbol,
                    exchange=symbol_contract_map[symbol].exchange,
                    name=symbol_contract_map[symbol].name,
                    datetime=generate_datetime(d["time"], True),
                    volume=d["volume"],
                    turnover=d["amount"],
                    open_interest=d["openInt"],
                    last_price=d["lastPrice"],
                    open_price=d["open"],
                    high_price=d["high"],
                    low_price=d["low"],
                    pre_close=d["lastClose"],
                    gateway_name=self.gateway_name
                )
                tick.bid_price_1, tick.bid_price_2, tick.bid_price_3, tick.bid_price_4, tick.bid_price_5 = d["bidPrice"]
                tick.ask_price_1, tick.ask_price_2, tick.ask_price_3, tick.ask_price_4, tick.ask_price_5 = d["askPrice"]
                tick.bid_volume_1, tick.bid_volume_2, tick.bid_volume_3, tick.bid_volume_4, tick.bid_volume_5 = d["bidVol"]
                tick.ask_volume_1, tick.ask_volume_2, tick.ask_volume_3, tick.ask_volume_4, tick.ask_volume_5 = d["askVol"]
                self.gateway.on_tick(tick)

    def onmarketdata(self, datas: dict) -> None:
        """订阅行情回报"""
        k: str = next(iter(datas.keys()))
        d: dict = next(iter(datas.values()))[0]
        symbol, exchange = k.split(".")
        tick: TickData = TickData(
            symbol=symbol,
            exchange=symbol_contract_map[symbol].exchange,
            name=symbol_contract_map[symbol].name,
            datetime=generate_datetime(d["time"], True),
            volume=d["volume"],
            turnover=d["amount"],
            open_interest=d["openInt"],
            last_price=d["lastPrice"],
            open_price=d["open"],
            high_price=d["high"],
            low_price=d["low"],
            pre_close=d["lastClose"],
            gateway_name=self.gateway_name
        )
        tick.bid_price_1, tick.bid_price_2, tick.bid_price_3, tick.bid_price_4, tick.bid_price_5 = d["bidPrice"]
        tick.ask_price_1, tick.ask_price_2, tick.ask_price_3, tick.ask_price_4, tick.ask_price_5 = d["askPrice"]
        tick.bid_volume_1, tick.bid_volume_2, tick.bid_volume_3, tick.bid_volume_4, tick.bid_volume_5 = d["bidVol"]
        tick.ask_volume_1, tick.ask_volume_2, tick.ask_volume_3, tick.ask_volume_4, tick.ask_volume_5 = d["askVol"]
        self.gateway.on_tick(tick)

    def connect(self) -> None:
        """连接服务器"""
        if not self.inited:
            self.inited = True
            # subscribe_whole_quote(['SH', 'SZ'], callback=self.onMarketData)  全市场订阅

            datas: list = list(get_full_tick(['SH', 'SZ']).keys())
            for d in datas:
                SZ_stock = d.startswith('00') and d.endswith('SZ')
                if d.startswith(('159','51','60','68')) or SZ_stock:
                    symbol, exchange = d.split(".")
                    data: dict = get_instrument_detail(d)
                    contract: ContractData = ContractData(
                        symbol=symbol,
                        exchange=EXCHANGE_XT2VT[exchange],
                        name=data["InstrumentName"],
                        product=Product.EQUITY,
                        size=1,
                        pricetick=data["PriceTick"],
                        gateway_name=self.gateway_name
                    )
                    if d.startswith(('159','51')):
                        contract.product = Product.FUND
                    symbol_contract_map[symbol] = contract
                    self.gateway.on_contract(contract)
            self.gateway.write_log("合约信息查询成功")

        else:
            self.gateway.write_log("行情API已经初始化，请勿重复操作")

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        if req.symbol in symbol_contract_map:
            symbol: str = req.symbol + "." + EXCHANGE_VT2XT[req.exchange]
            if req.symbol not in self.subscribed:
                subscribe_quote(stock_code=symbol, period='tick', callback=self.onmarketdata)
                self.subscribed.add(req.symbol)

    def close(self) -> None:
        """关闭连接"""
        pass


class XtTdApi(XtQuantTraderCallback):

    def __init__(self, gateway: XtGateway):
        """构造函数"""
        super().__init__()

        self.gateway: XtGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.inited: bool = False
        self.connected: bool = False

        self.order_ref: int = 0

        self.localid_sysid_map: Dict[str, str] = {}

        self.client: XtQuantTrader = None
        self.acc: StockAccount = None

    def init(self, path: str, accountid: str) -> None:
        """初始化"""
        if not self.inited:
            self.inited = True
            self.connect(path, accountid)

        else:
            self.gateway.write_log("已经初始化，请勿重复操作")

#    def on_connected(self):
#        """
#        连接成功推送
#        """
#        print("on_connected!!!")
#
#    def on_disconnected(self):
#        """
#        连接断开:
#        return:
#        """
#        print("connection lost")
#        self.gateway.write_log("交易服务器连接断开")
#        self.connected = False
#        connect_result = self.client.connect()
#
#        if connect_result:
#            self.gateway.write_log("交易服务器重连失败")
#        else:
#            self.gateway.write_log("交易服务器连接成功")

    def on_stock_order(self, data) -> None:
        """
        委托回报推送
        :param order: XtOrder对象
        :return:
        """
        symbol, exchange = (data.stock_code).split(".")
        order: OrderData = OrderData(
            symbol=symbol,
            exchange=EXCHANGE_XT2VT[exchange],
            orderid=data.order_remark,
            direction=DIRECTION_XT2VT[data.order_type],
            type=ORDERTYPE_XT2VT[data.price_type],                #目前测出来与文档不同，限价返回50，市价返回88
            price=data.price,
            volume=data.order_volume,
            traded=data.traded_volume,
            status=STATUS_XT2VT.get(data.order_status, Status.SUBMITTING),
            datetime=generate_datetime(data.order_time),
            gateway_name=self.gateway_name
        )
        self.gateway.on_order(order)
        self.localid_sysid_map[data.order_remark] = data.order_id

    def on_query_order_async(self, orders) -> None:
        """委托信息异步查询回报"""
        if orders:
            for d in orders:
                symbol, exchange = (d.stock_code).split(".")
                order: OrderData = OrderData(
                    symbol=symbol,
                    exchange=EXCHANGE_XT2VT[exchange],
                    orderid=d.order_remark,
                    direction=DIRECTION_XT2VT[d.order_type],
                    type=ORDERTYPE_XT2VT[d.price_type],                #目前测出来与文档不同，限价返回50，深圳市价返回88
                    price=d.price,
                    volume=d.order_volume,
                    traded=d.traded_volume,
                    status=STATUS_XT2VT.get(d.order_status, Status.SUBMITTING),
                    datetime=generate_datetime(d.order_time),
                    gateway_name=self.gateway_name
                )
                self.gateway.on_order(order)
                self.localid_sysid_map[order.orderid] = d.order_id          #str

    def on_query_asset_async(self, asset) -> None:
        """资金信息异步查询回报"""
        if asset:
            account: AccountData = AccountData(
                accountid=asset.account_id,
                balance=asset.total_asset,
                frozen=asset.frozen_cash,
                gateway_name=self.gateway_name
            )
            account.available = asset.cash
            self.gateway.on_account(account)

    def on_stock_trade(self, data) -> None:
        """
        成交变动推送
        :param trade: XtTrade对象
        :return:
        """
        symbol, exchange = (data.stock_code).split(".")
        trade: TradeData = TradeData(
            symbol=symbol,
            exchange=EXCHANGE_XT2VT[exchange],
            orderid=data.order_remark,
            tradeid=data.traded_id,
            direction=DIRECTION_XT2VT[data.order_type],
            price=data.traded_price,
            volume=data.traded_volume,
            datetime=generate_datetime(data.traded_time),
            gateway_name=self.gateway_name
        )
        self.gateway.on_trade(trade)

    def on_query_trades_async(self, trades) -> None:
        """成交信息异步查询回报"""
        if trades:
            for d in trades:
                symbol, exchange = (d.stock_code).split(".")
                trade: TradeData = TradeData(
                    symbol=symbol,
                    exchange=EXCHANGE_XT2VT[exchange],
                    orderid=d.order_remark,
                    tradeid=d.traded_id,
                    direction=DIRECTION_XT2VT[d.order_type],
                    price=d.traded_price,
                    volume=d.traded_volume,
                    datetime=generate_datetime(d.traded_time),
                    gateway_name=self.gateway_name
                )
                self.gateway.on_trade(trade)

    def on_query_positions_async(self, positions) -> None:
        """持仓信息异步查询回报"""
        if positions:
            for d in positions:
                if not d.market_value:
                    continue
                symbol, exchange = (d.stock_code).split(".")
                position: PositionData = PositionData(
                    symbol=symbol,
                    exchange=EXCHANGE_XT2VT[exchange],
                    direction=Direction.NET,
                    volume=d.volume,
                    yd_volume=d.can_use_volume,
                    frozen=d.volume - d.can_use_volume,
                    price=d.open_price,
                    gateway_name=self.gateway_name
                )
                self.gateway.on_position(position)

    def on_order_error(self, error) -> None:
        """
        委托失败推送
        :param order_error:XtOrderError 对象
        :return:
        """
        order: OrderData = self.gateway.get_order(error.order_remark)
        if order:
            order.status = Status.REJECTED
            self.gateway.on_order(order)
            
            self.gateway.write_log(f"交易委托失败, 错误代码{error.error_id}, 错误信息{error.error_msg}")

    def on_cancel_error(self, cancel_error) -> None:
        """
        撤单失败推送
        :param cancel_error: XtCancelError 对象
        :return:
        """
        print("on cancel_error callback")
        print(cancel_error.order_id, cancel_error.error_id,
        cancel_error.error_msg)

    def on_order_stock_async_response(self, response) -> None:
        """
        异步下单回报推送
        :param response: XtOrderResponse 对象
        :return:
        """
        print("on_order_stock_async_response")

    def on_cancel_order_stock_async_response(self, response) -> None:
        """
        :param response: XtCancelOrderResponse 对象
        :return:
        """
        print("on_order_stock_async_response!!!")
        print(response.cancel_result, response.order_id, response.seq, response.order_sysid)

    def new_orderid(self) -> str:
        """生成本地委托号"""
        prefix: str = datetime.now().strftime("1%m%d%H%M%S")

        self.order_ref += 1
        suffix: str = str(self.order_ref).rjust(6, "0")

        orderid: str = prefix + suffix
        return orderid

    def send_order(self, req: OrderRequest) -> str:
        """委托下单"""
        contract: ContractData = symbol_contract_map.get(req.symbol, None)
        if not contract:
            self.gateway.write_log(f"找不到该合约{req.symbol}")
            return ""

        if not req.price:
            self.gateway.write_log("请检查委托价格")
            return ""

        if req.type not in [OrderType.LIMIT, OrderType.MARKET]:
            self.gateway.write_log(f"不支持的委托类型: {req.type.value}")
            return ""

        stock_code: str = req.symbol + "." + EXCHANGE_VT2XT[req.exchange]
        orderid: str = self.new_orderid()
        self.client.order_stock_async(
            self.acc,
            stock_code,
            DIRECTION_VT2XT[req.direction],
            int(req.volume),
            ORDERTYPE_VT2XT[(req.exchange, req.type)],
            req.price,
            "",
            orderid
        )

        order: OrderData = req.create_order_data(orderid, self.gateway_name)
        self.gateway.on_order(order)

        return order.vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        """委托撤单"""
        sysid: str = self.localid_sysid_map.get(req.orderid, None)
        if not sysid:
            self.gateway.write_log("撤单失败，找不到委托号")
            return

        self.client.cancel_order_stock_async(self.acc, sysid)

    def query_position(self) -> None:
        """查询持仓"""
        self.client.query_stock_positions_async(self.acc, self.on_query_positions_async)

    def query_account(self) -> None:
        """查询账户资金"""
        self.client.query_stock_asset_async(self.acc, self.on_query_asset_async)

    def query_order(self) -> None:
        """查询委托信息"""
        self.client.query_stock_orders_async(self.acc, self.on_query_order_async)

    def query_trade(self) -> None:
        """查询成交信息"""
        self.client.query_stock_trades_async(self.acc, self.on_query_trades_async)

    def connect(self, path: str, accountid: str) -> str:
        """连接服务器"""
        session: int = int(float(datetime.now().strftime("%H%M%S.%f"))*1000)
        self.client = XtQuantTrader(path, session)

        self.acc = StockAccount(accountid)

        # 创建交易回调类对象，并声明接收回调
        self.client.register_callback(self)

        # 启动交易线程
        self.client.start()

        # 建立交易连接，返回0表示连接成功
        connect_result: int = self.client.connect()
        if connect_result:
            self.gateway.write_log("交易服务器连接失败")
            return
        
        self.connected = True
        self.gateway.write_log("交易服务器连接成功")

        # 对交易回调进行订阅，订阅后可以收到交易主推，返回0表示订阅成功
        subscribe_result: int = self.client.subscribe(self.acc)
        if subscribe_result:
            self.gateway.write_log("交易服务器订阅失败")
            return
            
        self.gateway.write_log("交易服务器订阅成功")

        self.query_account()
        self.query_position()
        self.query_order()
        self.query_trade()

    def close(self) -> None:
        """关闭连接"""
        if self.inited:
            self.client.stop()


def generate_datetime(timestamp: int, millisecond=False) -> datetime:
    """生成本地时间"""
    if millisecond:
        dt: datetime = datetime.fromtimestamp(timestamp/1000)
    else:
        dt: datetime = datetime.fromtimestamp(timestamp)
    dt: datetime = CHINA_TZ.localize(dt)
    return dt
