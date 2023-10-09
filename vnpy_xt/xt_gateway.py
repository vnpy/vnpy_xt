from datetime import datetime, timedelta, time
from typing import Dict, Tuple, List

from pandas import DataFrame
from xtquant import xtconstant
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import (
    StockAccount,
    XtAsset,
    XtOrder,
    XtPosition,
    XtTrade,
    XtOrderResponse,
    XtCancelOrderResponse,
    XtOrderError,
    XtCancelError
)
from xtquant.xtdata import (
    subscribe_quote,
    get_instrument_detail,
    get_local_data,
    download_history_data,
    get_stock_list_in_sector
)

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
    TickData,
    BarData,
    HistoryRequest,
    OptionType,
)
from vnpy.trader.constant import (
    OrderType,
    Direction,
    Exchange,
    Status,
    Product,
    Interval
)
from vnpy.trader.utility import ZoneInfo


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
    (Exchange.BSE, OrderType.MARKET): xtconstant.MARKET_SZ_CONVERT_5_CANCEL,
    (Exchange.SSE, OrderType.LIMIT): xtconstant.FIX_PRICE,
    (Exchange.SZSE, OrderType.LIMIT): xtconstant.FIX_PRICE,
    (Exchange.BSE, OrderType.LIMIT): xtconstant.FIX_PRICE,
}
ORDERTYPE_XT2VT: Dict[int, OrderType] = {
    50: OrderType.LIMIT,
    88: OrderType.MARKET,
}

# 交易所映射
EXCHANGE_XT2VT: Dict[str, Exchange] = {
    "SH": Exchange.SSE,
    "SZ": Exchange.SZSE,
    "BJ": Exchange.BSE,
    "SHFE": Exchange.SHFE,
    "CFFEX": Exchange.CFFEX,
    "INE": Exchange.INE,
    "DCE": Exchange.DCE,
    "CZCE": Exchange.CZCE,
    "GFEX": Exchange.GFEX
}
EXCHANGE_VT2XT: Dict[Exchange, str] = {v: k for k, v in EXCHANGE_XT2VT.items()}
MDEXCHANGE_VT2XT: Dict[str, Exchange] = {
    Exchange.SSE: "SH",
    Exchange.SZSE: "SZ",
    Exchange.BSE: "BJ",
    Exchange.SHFE: "SF",
    Exchange.CFFEX: "IF",
    Exchange.INE: "INE",
    Exchange.DCE: "DF",
    Exchange.CZCE: "ZF",
    Exchange.GFEX: "GF",
}
MDEXCHANGE_XT2VT: Dict[str, Exchange] = {v: k for k, v in MDEXCHANGE_VT2XT.items()}
MDEXCHANGE_XT2XT: Dict[str, str] = {
    "CFFEX": "IF",
    "SHFE": "SF",
    "INE": "INE",
    "DCE": "DF",
    "CZCE": "ZF",
    "GFEX": "GF"
}

# 数据频率映射
INTERVAL_VT2XT = {
    Interval.MINUTE: "1m",
    Interval.DAILY: "1d",
}

# 数据频率调整映射
INTERVAL_ADJUSTMENT_MAP: Dict[Interval, timedelta] = {
    Interval.MINUTE: timedelta(minutes=1),
    Interval.DAILY: timedelta()
}

# 期权类型映射
OPTIONTYPE_XT2VT: Dict[str, OptionType] = {
    "C": OptionType.CALL,
    "P": OptionType.PUT,
}

# 其他常量
CHINA_TZ = ZoneInfo("Asia/Shanghai")       # 中国时区

# 合约数据全局缓存字典
symbol_contract_map: Dict[str, ContractData] = {}


class XtGateway(BaseGateway):
    """
    VeighNa用于对接迅投QMT Mini的交易接口。
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

    def query_history(self, req: HistoryRequest):
        """查询历史数据"""
        return self.md_api.query_history(req)

    def on_order(self, order: OrderData) -> None:
        """推送委托数据"""
        self.orders[order.orderid] = order
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
    """行情API"""

    def __init__(self, gateway: XtGateway) -> None:
        """构造函数"""
        self.gateway: XtGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.inited: bool = False
        self.subscribed: set = set()

    def onMarketData(self, data: dict) -> None:
        """行情推送回调"""
        for xt_symbol, buf in data.items():
            for d in buf:
                xt_symbol: str = next(iter(data.keys()))
                symbol, xt_exchange = xt_symbol.split(".")
                exchange = MDEXCHANGE_XT2VT[xt_exchange]

                tick: TickData = TickData(
                    symbol=symbol,
                    exchange=exchange,
                    datetime=generate_datetime(d["time"]),
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

                contract = symbol_contract_map[tick.vt_symbol]
                tick.name = contract.name

                tick.bid_price_1, tick.bid_price_2, tick.bid_price_3, tick.bid_price_4, tick.bid_price_5 = d["bidPrice"]
                tick.ask_price_1, tick.ask_price_2, tick.ask_price_3, tick.ask_price_4, tick.ask_price_5 = d["askPrice"]
                tick.bid_volume_1, tick.bid_volume_2, tick.bid_volume_3, tick.bid_volume_4, tick.bid_volume_5 = d["bidVol"]
                tick.ask_volume_1, tick.ask_volume_2, tick.ask_volume_3, tick.ask_volume_4, tick.ask_volume_5 = d["askVol"]

                self.gateway.on_tick(tick)

    def connect(self) -> None:
        """连接"""
        if self.inited:
            self.gateway.write_log("行情接口已经初始化，请勿重复操作")
            return
        self.inited = True

        self.query_contracts()

    def query_contracts(self) -> None:
        """查询合约信息"""
        if self.gateway.stock_trading:
            self.query_stock_contracts()
        else:
            self.query_future_contracts()
        self.gateway.write_log("合约信息查询成功")

    def query_stock_contracts(self) -> None:
        """查询股票合约信息"""
        xt_symbols: List[str] = []
        markets: list = ["SH", "SZ", "BJ"]

        for i in markets:
            names: list = get_stock_list_in_sector(i)
            xt_symbols += names

        for xt_symbol in xt_symbols:
            # 筛选需要的合约
            product = None
            symbol, xt_exchange = xt_symbol.split(".")

            if xt_exchange == "SZ":
                if xt_symbol.startswith("00"):
                    product = Product.EQUITY
                elif xt_symbol.startswith("159"):
                    product = Product.FUND
            elif xt_exchange == "SH":
                if xt_symbol.startswith(("60", "68")):
                    product = Product.EQUITY
                elif xt_symbol.startswith("51"):
                    product = Product.FUND
            elif xt_exchange == "BJ":
                product = Product.EQUITY

            if not product:
                continue

            # 生成并推送合约信息
            data: dict = get_instrument_detail(xt_symbol)

            contract: ContractData = ContractData(
                symbol=symbol,
                exchange=EXCHANGE_XT2VT[xt_exchange],
                name=data["InstrumentName"],
                product=product,
                size=data["VolumeMultiple"],
                pricetick=data["PriceTick"],
                history_data=True,
                gateway_name=self.gateway_name
            )

            symbol_contract_map[contract.vt_symbol] = contract
            self.gateway.on_contract(contract)

    def query_future_contracts(self) -> None:
        """查询期货合约信息"""
        xt_symbols: List[str] = []
        markets: list = ["IF", "SF", "INE", "DF", "ZF", "GF"]

        for i in markets:
            names: list = get_stock_list_in_sector(i)
            xt_symbols += names

        for xt_symbol in xt_symbols:
            # 筛选需要的合约
            product = None
            symbol, xt_exchange = xt_symbol.split(".")
            md_exchange: str = MDEXCHANGE_XT2XT[xt_exchange]
            xt_symbol = xt_symbol.replace(xt_exchange, md_exchange)

            if xt_exchange == "CZCE" and len(symbol) > 6 and "&" not in symbol:
                product = Product.OPTION
            elif xt_exchange in ("CFFEX", "GFEX") and "-" in symbol:
                product = Product.OPTION
            elif xt_exchange in ("DCE", "INE", "SHFE") and ("C" in symbol or "P" in symbol) and "SP" not in symbol:
                product = Product.OPTION
            else:
                product = Product.FUTURES

            # 生成并推送合约信息
            if product == Product.OPTION:
                data: dict = get_instrument_detail(xt_symbol, True)
            else:
                data: dict = get_instrument_detail(xt_symbol)

            if not data["ExpireDate"]:
                continue

            contract: ContractData = ContractData(
                symbol=symbol,
                exchange=EXCHANGE_XT2VT[xt_exchange],
                name=data["InstrumentName"],
                product=product,
                size=data["VolumeMultiple"],
                pricetick=data["PriceTick"],
                history_data=True,
                gateway_name=self.gateway_name
            )

            if product == Product.OPTION:
                if contract.exchange == Exchange.CZCE:
                    contract.option_portfolio = data["ProductID"][:-1]
                else:
                    contract.option_portfolio = data["ProductID"]
                contract.option_underlying = data["ExtendInfo"]["OptUndlCode"]
                contract.option_type = OPTIONTYPE_XT2VT[data["ProductName"].replace("-", "")[-1]]
                contract.option_strike = data["ExtendInfo"]["OptExercisePrice"]
                contract.option_index = str(data["ExtendInfo"]["OptExercisePrice"])
                contract.option_listed = datetime.strptime(data["OpenDate"], "%Y%m%d")
                contract.option_expiry = datetime.strptime(data["ExpireDate"], "%Y%m%d")

            symbol_contract_map[contract.vt_symbol] = contract
            self.gateway.on_contract(contract)

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        if req.vt_symbol not in symbol_contract_map:
            return

        xt_symbol: str = req.symbol + "." + MDEXCHANGE_VT2XT[req.exchange]
        if xt_symbol not in self.subscribed:
            subscribe_quote(stock_code=xt_symbol, period="tick", callback=self.onMarketData)
            self.subscribed.add(xt_symbol)

    def query_history(self, req: HistoryRequest) -> List[BarData]:
        """查询K线历史"""
        history: List[BarData] = []
        symbol: str = req.symbol
        exchange: Exchange = req.exchange
        interval: Interval = req.interval

        # 检查是否支持该数据
        if req.vt_symbol not in symbol_contract_map:
            self.gateway.write_log(f"获取K线数据失败，找不到{req.vt_symbol}合约")
            return history

        xt_interval: str = INTERVAL_VT2XT.get(interval, None)
        if xt_interval is None:
            self.gateway.write_log(f"获取K线数据失败，接口暂不提供{interval.value}级别历史数据")
            return history

        # 从服务器下载获取
        xt_symbol: str = symbol + "." + MDEXCHANGE_VT2XT[exchange]
        start: str = req.start.strftime("%Y%m%d%H%M%S")
        end: str = req.end.strftime("%Y%m%d%H%M%S")

        download_history_data(xt_symbol, xt_interval, start, end)
        data: dict = get_local_data([], [xt_symbol], xt_interval, start, end, -1, "front_ratio")      # 默认等比前复权

        df: DataFrame = data[xt_symbol]

        if df.empty:
            return history

        adjustment: timedelta = INTERVAL_ADJUSTMENT_MAP[interval]

        # 遍历解析
        for tp in df.itertuples():

            # 为了xtdata时间戳（K线结束时点）转换为VeighNa时间戳（K线开始时点）
            dt: datetime = generate_datetime(tp.time)
            dt = dt - adjustment

            # 日线过滤尚未走完的当日数据
            if interval == Interval.DAILY:
                incomplete_bar: bool = (
                    dt.date() == datetime.now().date()
                    and datetime.now().time() < time(hour=15)
                )
                if incomplete_bar:
                    continue
            # 分钟线过滤开盘脏前数据
            else:
                if exchange in (Exchange.SSE, Exchange.SZSE) and dt.time() < time(hour=9, minute=30):
                    continue

            bar: BarData = BarData(
                symbol=symbol,
                exchange=exchange,
                datetime=dt,
                interval=interval,
                volume=float(tp.volume),
                turnover=float(tp.amount),
                open_interest=float(tp.openInterest),
                open_price=float(tp.open),
                high_price=float(tp.high),
                low_price=float(tp.low),
                close_price=float(tp.close),
                gateway_name=self.gateway_name
            )
            history.append(bar)

        # 输出日志信息
        if history:
            msg: str = f"获取历史数据成功，{req.vt_symbol} - {interval.value}，{history[0].datetime} - {history[-1].datetime}"
            self.gateway.write_log(msg)

        return history

    def close(self) -> None:
        """关闭连接"""
        pass


class XtTdApi(XtQuantTraderCallback):
    """交易API"""

    def __init__(self, gateway: XtGateway):
        """构造函数"""
        super().__init__()

        self.gateway: XtGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.inited: bool = False
        self.connected: bool = False

        self.order_count: int = 0

        self.active_localid_sysid_map: Dict[str, str] = {}

        self.xt_client: XtQuantTrader = None
        self.xt_account: StockAccount = None

    def init(self, path: str, accountid: str) -> None:
        """初始化"""
        if self.inited:
            self.gateway.write_log("已经初始化，请勿重复操作")
            return

        self.inited = True
        self.connect(path, accountid)

    def on_connected(self):
        """
        连接成功推送
        """
        pass

    def on_disconnected(self):
        """连接断开"""
        self.gateway.write_log("交易接口连接断开，请检查与客户端的连接状态")
        self.connected = False

        # 尝试重连
        connect_result = self.xt_client.connect()

        if connect_result:
            self.gateway.write_log("交易接口重连失败")
        else:
            self.gateway.write_log("交易接口重连成功")

    def on_stock_order(self, xt_order: XtOrder) -> None:
        """委托回报推送"""
        # 过滤非VeighNa Trader发出的委托
        if not xt_order.order_remark:
            return

        # 过滤不支持的委托类型
        type: OrderType = ORDERTYPE_XT2VT.get(xt_order.price_type, None)
        if not type:
            return

        symbol, xt_exchange = xt_order.stock_code.split(".")

        order: OrderData = OrderData(
            symbol=symbol,
            exchange=EXCHANGE_XT2VT[xt_exchange],
            orderid=xt_order.order_remark,
            direction=DIRECTION_XT2VT[xt_order.order_type],
            type=type,                  # 目前测出来与文档不同，限价返回50，市价返回88
            price=xt_order.price,
            volume=xt_order.order_volume,
            traded=xt_order.traded_volume,
            status=STATUS_XT2VT.get(xt_order.order_status, Status.SUBMITTING),
            datetime=generate_datetime(xt_order.order_time, False),
            gateway_name=self.gateway_name
        )

        if order.is_active():
            self.active_localid_sysid_map[xt_order.order_remark] = xt_order.order_id
        else:
            self.active_localid_sysid_map.pop(xt_order.order_remark, None)

        self.gateway.on_order(order)

    def on_query_order_async(self, xt_orders: List[XtOrder]) -> None:
        """委托信息异步查询回报"""
        if not xt_orders:
            return

        for data in xt_orders:
            self.on_stock_order(data)

    def on_query_asset_async(self, xt_asset: XtAsset) -> None:
        """资金信息异步查询回报"""
        if not xt_asset:
            return

        account: AccountData = AccountData(
            accountid=xt_asset.account_id,
            balance=xt_asset.total_asset,
            frozen=xt_asset.frozen_cash,
            gateway_name=self.gateway_name
        )
        account.available = xt_asset.cash

        self.gateway.on_account(account)

    def on_stock_trade(self, xt_trade: XtTrade) -> None:
        """成交变动推送"""
        if not xt_trade.order_remark:
            return

        symbol, xt_exchange = xt_trade.stock_code.split(".")

        trade: TradeData = TradeData(
            symbol=symbol,
            exchange=EXCHANGE_XT2VT[xt_exchange],
            orderid=xt_trade.order_remark,
            tradeid=xt_trade.traded_id,
            direction=DIRECTION_XT2VT[xt_trade.order_type],
            price=xt_trade.traded_price,
            volume=xt_trade.traded_volume,
            datetime=generate_datetime(xt_trade.traded_time, False),
            gateway_name=self.gateway_name
        )

        self.gateway.on_trade(trade)

    def on_query_trades_async(self, xt_trades: List[XtTrade]) -> None:
        """成交信息异步查询回报"""
        if not xt_trades:
            return

        for xt_trade in xt_trades:
            self.on_stock_trade(xt_trade)

    def on_query_positions_async(self, xt_positions: List[XtPosition]) -> None:
        """持仓信息异步查询回报"""
        if not xt_positions:
            return

        for xt_position in xt_positions:
            if not xt_position.market_value:
                continue

            symbol, xt_exchange = xt_position.stock_code.split(".")

            position: PositionData = PositionData(
                symbol=symbol,
                exchange=EXCHANGE_XT2VT[xt_exchange],
                direction=Direction.NET,
                volume=xt_position.volume,
                yd_volume=xt_position.can_use_volume,
                frozen=xt_position.volume - xt_position.can_use_volume,
                price=xt_position.open_price,
                gateway_name=self.gateway_name
            )

            self.gateway.on_position(position)

    def on_order_error(self, xt_error: XtOrderError) -> None:
        """委托失败推送"""
        order: OrderData = self.gateway.get_order(xt_error.order_remark)
        if order:
            order.status = Status.REJECTED
            self.gateway.on_order(order)

        self.gateway.write_log(f"交易委托失败, 错误代码{xt_error.error_id}, 错误信息{xt_error.error_msg}")

    def on_cancel_error(self, xt_error: XtCancelError) -> None:
        """撤单失败推送"""
        self.gateway.write_log(f"交易撤单失败, 错误代码{xt_error.error_id}, 错误信息{xt_error.error_msg}")

    def on_order_stock_async_response(self, response: XtOrderResponse) -> None:
        """异步下单回报推送"""
        pass

    def on_cancel_order_stock_async_response(self, response: XtCancelOrderResponse) -> None:
        """异步撤单回报推送"""
        pass

    def new_orderid(self) -> str:
        """生成本地委托号"""
        prefix: str = datetime.now().strftime("1%m%d%H%M%S")

        self.order_count += 1
        suffix: str = str(self.order_count).rjust(6, "0")

        orderid: str = prefix + suffix
        return orderid

    def send_order(self, req: OrderRequest) -> str:
        """委托下单"""
        contract: ContractData = symbol_contract_map.get(req.vt_symbol, None)
        if not contract:
            self.gateway.write_log(f"找不到该合约{req.vt_symbol}")
            return ""

        if req.type not in {OrderType.LIMIT, OrderType.MARKET}:
            self.gateway.write_log(f"不支持的委托类型: {req.type.value}")
            return ""

        stock_code: str = req.symbol + "." + EXCHANGE_VT2XT[req.exchange]
        orderid: str = self.new_orderid()

        self.xt_client.order_stock_async(
            account=self.xt_account,
            stock_code=stock_code,
            order_type=DIRECTION_VT2XT[req.direction],
            order_volume=int(req.volume),
            price_type=ORDERTYPE_VT2XT[(req.exchange, req.type)],
            price=req.price,
            strategy_name=req.reference,
            order_remark=orderid
        )

        order: OrderData = req.create_order_data(orderid, self.gateway_name)
        self.gateway.on_order(order)

        return order.vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        """委托撤单"""
        sysid: str = self.active_localid_sysid_map.get(req.orderid, None)
        if not sysid:
            self.gateway.write_log("撤单失败，找不到委托号")
            return

        self.xt_client.cancel_order_stock_async(self.xt_account, sysid)

    def query_position(self) -> None:
        """查询持仓"""
        self.xt_client.query_stock_positions_async(self.xt_account, self.on_query_positions_async)

    def query_account(self) -> None:
        """查询账户资金"""
        self.xt_client.query_stock_asset_async(self.xt_account, self.on_query_asset_async)

    def query_order(self) -> None:
        """查询委托信息"""
        self.xt_client.query_stock_orders_async(self.xt_account, self.on_query_order_async)

    def query_trade(self) -> None:
        """查询成交信息"""
        self.xt_client.query_stock_trades_async(self.xt_account, self.on_query_trades_async)

    def connect(self, path: str, accountid: str) -> str:
        """发起连接"""
        # 创建客户端和账号实例
        session: int = int(float(datetime.now().strftime("%H%M%S.%f")) * 1000)
        self.xt_client = XtQuantTrader(path, session)

        self.xt_account = StockAccount(accountid)

        # 注册回调接口
        self.xt_client.register_callback(self)

        # 启动交易线程
        self.xt_client.start()

        # 建立交易连接，返回0表示连接成功
        connect_result: int = self.xt_client.connect()
        if connect_result:
            self.gateway.write_log("交易接口连接失败")
            return

        self.connected = True
        self.gateway.write_log("交易接口连接成功")

        # 订阅交易回调推送
        subscribe_result: int = self.xt_client.subscribe(self.xt_account)
        if subscribe_result:
            self.gateway.write_log("交易接口订阅失败")
            return

        self.gateway.write_log("交易接口订阅成功")

        # 初始化数据查询
        self.query_account()
        self.query_position()
        self.query_order()
        self.query_trade()

    def close(self) -> None:
        """关闭连接"""
        if self.inited:
            self.xt_client.stop()


def generate_datetime(timestamp: int, millisecond: bool = True) -> datetime:
    """生成本地时间"""
    if millisecond:
        dt: datetime = datetime.fromtimestamp(timestamp / 1000)
    else:
        dt: datetime = datetime.fromtimestamp(timestamp)
    dt: datetime = dt.replace(tzinfo=CHINA_TZ)
    return dt
