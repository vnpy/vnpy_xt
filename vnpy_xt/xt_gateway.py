from datetime import datetime
from typing import Callable, Optional

from xtquant import (
    xtdata,
    xtdatacenter as xtdc
)
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
from filelock import FileLock, Timeout

from vnpy.event import EventEngine, EVENT_TIMER
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    OrderRequest,
    CancelRequest,
    SubscribeRequest,
    ContractData,
    TickData,
    HistoryRequest,
    OptionType,
    OrderData,
    Status,
    Direction,
    OrderType,
    AccountData,
    PositionData,
    TradeData,
    Offset
)
from vnpy.trader.constant import (
    Exchange,
    Product
)
from vnpy.trader.utility import (
    ZoneInfo,
    get_file_path,
    round_to
)

# 交易所映射
EXCHANGE_VT2XT: dict[Exchange, str] = {
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

EXCHANGE_XT2VT: dict[str, Exchange] = {v: k for k, v in EXCHANGE_VT2XT.items()}
EXCHANGE_XT2VT["SHO"] = Exchange.SSE
EXCHANGE_XT2VT["SZO"] = Exchange.SZSE


# 委托状态映射
STATUS_XT2VT: dict[str, Status] = {
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
DIRECTION_VT2XT: dict[tuple, str] = {
    (Direction.LONG, Offset.NONE): xtconstant.STOCK_BUY,
    (Direction.SHORT, Offset.NONE): xtconstant.STOCK_SELL,
    (Direction.LONG, Offset.OPEN): xtconstant.STOCK_OPTION_BUY_OPEN,
    (Direction.LONG, Offset.CLOSE): xtconstant.STOCK_OPTION_BUY_CLOSE,
    (Direction.SHORT, Offset.OPEN): xtconstant.STOCK_OPTION_SELL_OPEN,
    (Direction.SHORT, Offset.CLOSE): xtconstant.STOCK_OPTION_SELL_CLOSE,
}
DIRECTION_XT2VT: dict[str, tuple] = {v: k for k, v in DIRECTION_VT2XT.items()}

POSDIRECTION_XT2VT: dict[int, Direction] = {
    xtconstant.DIRECTION_FLAG_BUY: Direction.LONG,
    xtconstant.DIRECTION_FLAG_SELL: Direction.SHORT
}

# 委托类型映射
ORDERTYPE_VT2XT: dict[tuple, int] = {
    (Exchange.SSE, OrderType.LIMIT): xtconstant.FIX_PRICE,
    (Exchange.SZSE, OrderType.LIMIT): xtconstant.FIX_PRICE,
    (Exchange.BSE, OrderType.LIMIT): xtconstant.FIX_PRICE,
}
ORDERTYPE_XT2VT: dict[int, OrderType] = {
    50: OrderType.LIMIT,
}

# 其他常量
CHINA_TZ = ZoneInfo("Asia/Shanghai")       # 中国时区


# 合约数据全局缓存字典
symbol_contract_map: dict[str, ContractData] = {}


class XtGateway(BaseGateway):
    """
    VeighNa用于对接迅投研的实时行情接口。
    """

    default_name: str = "XT"

    default_setting: dict[str, str] = {
        "token": "",
        "股票市场": ["是", "否"],
        "期货市场": ["是", "否"],
        "期权市场": ["是", "否"],
        "交易": ["是", "否"],
        "账号类型": ["股票", "股票期权"],
        "路径": "",
        "资金账号": ""
    }

    exchanges: list[str] = list(EXCHANGE_VT2XT.keys())

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        """构造函数"""
        super().__init__(event_engine, gateway_name)

        self.md_api: "XtMdApi" = XtMdApi(self)
        self.td_api: "XtTdApi" = XtTdApi(self)

        self.trading: bool = False
        self.orders: dict[str, OrderData] = {}

    def connect(self, setting: dict) -> None:
        """连接交易接口"""
        token: str = setting["token"]

        stock_active: bool = setting["股票市场"] == "是"
        futures_active: bool = setting["期货市场"] == "是"
        option_active: bool = setting["期权市场"] == "是"

        self.md_api.connect(token, stock_active, futures_active, option_active)

        self.trading = setting["交易"] == "是"
        if self.trading:
            path: str = setting["路径"]
            accountid: str = setting["资金账号"]
            if setting["账号类型"] == "股票":
                account_type: str = "STOCK"
            else:
                account_type: str = "STOCK_OPTION"

            self.td_api.init(path, accountid, account_type)
            self.init_query()

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        self.md_api.subscribe(req)

    def send_order(self, req: OrderRequest) -> str:
        """委托下单"""
        if self.trading:
            return self.td_api.send_order(req)
        else:
            return ""

    def cancel_order(self, req: CancelRequest) -> None:
        """委托撤单"""
        if self.trading:
            self.td_api.cancel_order(req)

    def query_account(self) -> None:
        """查询资金"""
        if self.trading:
            self.td_api.query_account()

    def query_position(self) -> None:
        """查询持仓"""
        if self.trading:
            self.td_api.query_position()

    def query_history(self, req: HistoryRequest) -> None:
        """查询历史数据"""
        return None

    def on_order(self, order: OrderData) -> None:
        """推送委托数据"""
        self.orders[order.orderid] = order
        super().on_order(order)

    def get_order(self, orderid: str) -> OrderData:
        """查询委托数据"""
        return self.orders.get(orderid, None)

    def close(self) -> None:
        """关闭接口"""
        if self.trading:
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

    lock_filename = "xt_lock"
    lock_filepath = get_file_path(lock_filename)

    def __init__(self, gateway: XtGateway) -> None:
        """构造函数"""
        self.gateway: XtGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.inited: bool = False
        self.subscribed: set = set()

        self.token: str = ""
        self.stock_active: bool = False
        self.futures_active: bool = False
        self.option_active: bool = False

    def onMarketData(self, data: dict) -> None:
        """行情推送回调"""
        for xt_symbol, buf in data.items():
            for d in buf:
                symbol, xt_exchange = xt_symbol.split(".")
                exchange = EXCHANGE_XT2VT[xt_exchange]

                tick: TickData = TickData(
                    symbol=symbol,
                    exchange=exchange,
                    datetime=generate_datetime(d["time"]),
                    volume=d["volume"],
                    turnover=d["amount"],
                    open_interest=d["openInt"],
                    gateway_name=self.gateway_name
                )

                contract = symbol_contract_map[tick.vt_symbol]
                tick.name = contract.name

                bp_data: list = d["bidPrice"]
                ap_data: list = d["askPrice"]
                bv_data: list = d["bidVol"]
                av_data: list = d["askVol"]

                tick.bid_price_1 = round_to(bp_data[0], contract.pricetick)
                tick.bid_price_2 = round_to(bp_data[1], contract.pricetick)
                tick.bid_price_3 = round_to(bp_data[2], contract.pricetick)
                tick.bid_price_4 = round_to(bp_data[3], contract.pricetick)
                tick.bid_price_5 = round_to(bp_data[4], contract.pricetick)

                tick.ask_price_1 = round_to(ap_data[0], contract.pricetick)
                tick.ask_price_2 = round_to(ap_data[1], contract.pricetick)
                tick.ask_price_3 = round_to(ap_data[2], contract.pricetick)
                tick.ask_price_4 = round_to(ap_data[3], contract.pricetick)
                tick.ask_price_5 = round_to(ap_data[4], contract.pricetick)

                tick.bid_volume_1 = bv_data[0]
                tick.bid_volume_2 = bv_data[1]
                tick.bid_volume_3 = bv_data[2]
                tick.bid_volume_4 = bv_data[3]
                tick.bid_volume_5 = bv_data[4]

                tick.ask_volume_1 = av_data[0]
                tick.ask_volume_2 = av_data[1]
                tick.ask_volume_3 = av_data[2]
                tick.ask_volume_4 = av_data[3]
                tick.ask_volume_5 = av_data[4]

                tick.last_price = round_to(d["lastPrice"], contract.pricetick)
                tick.open_price = round_to(d["open"], contract.pricetick)
                tick.high_price = round_to(d["high"], contract.pricetick)
                tick.low_price = round_to(d["low"], contract.pricetick)
                tick.pre_close = round_to(d["lastClose"], contract.pricetick)

                self.gateway.on_tick(tick)

    def connect(
        self,
        token: str,
        stock_active: bool,
        futures_active: bool,
        option_active: bool
    ) -> None:
        """连接"""
        self.token = token
        self.stock_active = stock_active
        self.futures_active = futures_active
        self.option_active = option_active

        if self.inited:
            self.gateway.write_log("行情接口已经初始化，请勿重复操作")
            return

        try:
            self.init_xtdc()

            # 尝试查询合约信息，确认连接成功
            xtdata.get_instrument_detail("000001.SZ")
        except Exception as ex:
            self.gateway.write_log(f"迅投研数据服务初始化失败，发生异常：{ex}")
            return False

        self.inited = True

        self.gateway.write_log("行情接口连接成功")

        self.query_contracts()

    def get_lock(self) -> bool:
        """获取文件锁，确保单例运行"""
        self.lock = FileLock(self.lock_filepath)

        try:
            self.lock.acquire(timeout=1)
            return True
        except Timeout:
            return False

    def init_xtdc(self) -> None:
        """初始化xtdc服务进程"""
        if not self.get_lock():
            return

        # 设置token
        xtdc.set_token(self.token)

        # 将VIP服务器设为连接池
        server_list: list = [
            "115.231.218.73:55310",
            "115.231.218.79:55310",
            "218.16.123.11:55310",
            "218.16.123.27:55310"
        ]
        xtdc.set_allow_optmize_address(server_list)

        # 开启使用期货真实夜盘时间
        xtdc.set_future_realtime_mode(True)

        # 执行初始化，但不启动默认58609端口监听
        xtdc.init(False)

        # 设置监听端口58620
        xtdc.listen(port=58620)

    def query_contracts(self) -> None:
        """查询合约信息"""
        if self.stock_active:
            self.query_stock_contracts()

        if self.futures_active:
            self.query_future_contracts()

        if self.option_active:
            self.query_option_contracts()

        self.gateway.write_log("合约信息查询成功")

    def query_stock_contracts(self) -> None:
        """查询股票合约信息"""
        xt_symbols: list[str] = []
        markets: list = [
            "沪深A股",
            "沪深转债",
            "沪深ETF",
            "沪深指数",
            "京市A股"
        ]

        for i in markets:
            names: list = xtdata.get_stock_list_in_sector(i)
            xt_symbols.extend(names)

        for xt_symbol in xt_symbols:
            # 筛选需要的合约
            product = None
            symbol, xt_exchange = xt_symbol.split(".")

            if xt_exchange == "SZ":
                if xt_symbol.startswith("00"):
                    product = Product.EQUITY
                elif xt_symbol.startswith("159"):
                    product = Product.FUND
                else:
                    product = Product.INDEX
            elif xt_exchange == "SH":
                if xt_symbol.startswith(("60", "68")):
                    product = Product.EQUITY
                elif xt_symbol.startswith("51"):
                    product = Product.FUND
                else:
                    product = Product.INDEX
            elif xt_exchange == "BJ":
                product = Product.EQUITY

            if not product:
                continue

            # 生成并推送合约信息
            data: dict = xtdata.get_instrument_detail(xt_symbol)

            contract: ContractData = ContractData(
                symbol=symbol,
                exchange=EXCHANGE_XT2VT[xt_exchange],
                name=data["InstrumentName"],
                product=product,
                size=data["VolumeMultiple"],
                pricetick=data["PriceTick"],
                history_data=False,
                gateway_name=self.gateway_name
            )

            symbol_contract_map[contract.vt_symbol] = contract
            self.gateway.on_contract(contract)

    def query_future_contracts(self) -> None:
        """查询期货合约信息"""
        xt_symbols: list[str] = []
        markets: list = [
            "中金所期货",
            "上期所期货",
            "能源中心期货",
            "大商所期货",
            "郑商所期货",
            "广期所期货"
        ]

        for i in markets:
            names: list = xtdata.get_stock_list_in_sector(i)
            xt_symbols.extend(names)

        for xt_symbol in xt_symbols:
            # 筛选需要的合约
            product = None
            symbol, xt_exchange = xt_symbol.split(".")

            if xt_exchange == "ZF" and len(symbol) > 6 and "&" not in symbol:
                product = Product.OPTION
            elif xt_exchange in ("IF", "GF") and "-" in symbol:
                product = Product.OPTION
            elif xt_exchange in ("DF", "INE", "SF") and ("C" in symbol or "P" in symbol) and "SP" not in symbol:
                product = Product.OPTION
            else:
                product = Product.FUTURES

            # 生成并推送合约信息
            if product == Product.OPTION:
                data: dict = xtdata.get_instrument_detail(xt_symbol, True)
            else:
                data: dict = xtdata.get_instrument_detail(xt_symbol)

            if not data["ExpireDate"]:
                if "00" not in symbol:
                    continue

            contract: ContractData = ContractData(
                symbol=symbol,
                exchange=EXCHANGE_XT2VT[xt_exchange],
                name=data["InstrumentName"],
                product=product,
                size=data["VolumeMultiple"],
                pricetick=data["PriceTick"],
                history_data=False,
                gateway_name=self.gateway_name
            )

            symbol_contract_map[contract.vt_symbol] = contract
            self.gateway.on_contract(contract)

    def query_option_contracts(self) -> None:
        """查询期权合约信息"""
        xt_symbols: list[str] = []

        markets: list = [
            "上证期权",
            "深证期权",
            "中金所期权",
            "上期所期权",
            "能源中心期权",
            "大商所期权",
            "郑商所期权",
            "广期所期权"
        ]

        for i in markets:
            names: list = xtdata.get_stock_list_in_sector(i)
            xt_symbols.extend(names)

        for xt_symbol in xt_symbols:
            ""
            _, xt_exchange = xt_symbol.split(".")

            if xt_exchange in {"SHO", "SZO"}:
                contract = process_etf_option(xtdata.get_instrument_detail, xt_symbol)
            else:
                contract = process_futures_option(xtdata.get_instrument_detail, xt_symbol)

            if contract:
                symbol_contract_map[contract.vt_symbol] = contract
                self.gateway.on_contract(contract)

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        if req.vt_symbol not in symbol_contract_map:
            return

        xt_exchange: str = EXCHANGE_VT2XT[req.exchange]
        if xt_exchange in {"SH", "SZ"} and len(req.symbol) > 6:
            xt_exchange += "O"

        xt_symbol: str = req.symbol + "." + xt_exchange

        if xt_symbol not in self.subscribed:
            xtdata.subscribe_quote(stock_code=xt_symbol, period="tick", callback=self.onMarketData)
            self.subscribed.add(xt_symbol)

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

        self.account_id: str = ""
        self.path: str = ""
        self.account_type: str = ""

        self.order_count: int = 0

        self.active_localid_sysid_map: dict[str, str] = {}

        self.xt_client: XtQuantTrader = None
        self.xt_account: StockAccount = None

    def init(self, path: str, accountid: str, account_type: str) -> None:
        """初始化"""
        if self.inited:
            self.gateway.write_log("已经初始化，请勿重复操作")
            return

        self.inited = True
        self.account_id = accountid
        self.path = path
        self.account_type = account_type
        self.connect()

    def on_connected(self):
        """
        连接成功推送
        """
        pass

    def on_disconnected(self):
        """连接断开"""
        self.gateway.write_log("交易接口连接断开，请检查与客户端的连接状态")
        self.connected = False

        # 尝试重连，重连需要更换session_id
        session: int = int(float(datetime.now().strftime("%H%M%S.%f")) * 1000)
        connect_result = self.connect(session)

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

        direction, offset = DIRECTION_XT2VT.get(xt_order.order_type, (None, None))
        if direction is None:
            return

        symbol, xt_exchange = xt_order.stock_code.split(".")

        order: OrderData = OrderData(
            symbol=symbol,
            exchange=EXCHANGE_XT2VT[xt_exchange],
            orderid=xt_order.order_remark,
            direction=direction,
            offset=offset,
            type=type,                  # 目前测出来与文档不同，限价返回50，市价返回88
            price=xt_order.price,
            volume=xt_order.order_volume,
            traded=xt_order.traded_volume,
            status=STATUS_XT2VT.get(xt_order.order_status, Status.SUBMITTING),
            datetime=generate_datetime(xt_order.order_time, False),
            gateway_name=self.gateway_name
        )

        if order.is_active():
            self.active_localid_sysid_map[xt_order.order_remark] = xt_order.order_sysid
        else:
            self.active_localid_sysid_map.pop(xt_order.order_remark, None)

        self.gateway.on_order(order)

    def on_query_order_async(self, xt_orders: list[XtOrder]) -> None:
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

        direction, offset = DIRECTION_XT2VT.get(xt_trade.order_type, (None, None))
        if direction is None:
            return

        trade: TradeData = TradeData(
            symbol=symbol,
            exchange=EXCHANGE_XT2VT[xt_exchange],
            orderid=xt_trade.order_remark,
            tradeid=xt_trade.traded_id,
            direction=direction,
            offset=offset,
            price=xt_trade.traded_price,
            volume=xt_trade.traded_volume,
            datetime=generate_datetime(xt_trade.traded_time, False),
            gateway_name=self.gateway_name
        )

        self.gateway.on_trade(trade)

    def on_query_trades_async(self, xt_trades: list[XtTrade]) -> None:
        """成交信息异步查询回报"""
        if not xt_trades:
            return

        for xt_trade in xt_trades:
            self.on_stock_trade(xt_trade)

    def on_query_positions_async(self, xt_positions: list[XtPosition]) -> None:
        """持仓信息异步查询回报"""
        if not xt_positions:
            return

        for xt_position in xt_positions:
            if self.account_type == "STOCK":
                direction: Direction = Direction.NET
            else:
                direction: Direction = POSDIRECTION_XT2VT.get(xt_position.direction, "")

            if not direction:
                continue

            symbol, xt_exchange = xt_position.stock_code.split(".")

            position: PositionData = PositionData(
                symbol=symbol,
                exchange=EXCHANGE_XT2VT[xt_exchange],
                direction=direction,
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

        if contract.exchange not in {Exchange.SSE, Exchange.SZSE, Exchange.BSE}:
            self.gateway.write_log(f"不支持的合约{req.vt_symbol}")
            return

        if req.type not in {OrderType.LIMIT}:
            self.gateway.write_log(f"不支持的委托类型: {req.type.value}")
            return ""

        if req.offset.value:
            if contract.product != Product.OPTION:
                self.gateway.write_log("委托失败，现货交易不需要选择开平方向")
                return ""
        else:
            if contract.product == Product.OPTION:
                self.gateway.write_log("委托失败，期权交易需要选择开平方向")
                return ""

        stock_code: str = req.symbol + "." + EXCHANGE_VT2XT[req.exchange]
        if self.account_type == "STOCK_OPTION":
            stock_code += "O"

        orderid: str = self.new_orderid()

        self.xt_client.order_stock_async(
            account=self.xt_account,
            stock_code=stock_code,
            order_type=DIRECTION_VT2XT[(req.direction, req.offset)],
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

        if req.exchange == Exchange.SSE:
            market: int = 0
        else:
            market: int = 1

        self.xt_client.cancel_order_stock_sysid_async(self.xt_account, market, sysid)

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

    def connect(self, session: int = 0) -> int:
        """发起连接"""
        # 创建客户端和账号实例
        if not session:
            session: int = int(float(datetime.now().strftime("%H%M%S.%f")) * 1000)

        # QMT普通版本path填写f"{放置安装文件夹的路径}\\userdata"
        self.xt_client = XtQuantTrader(self.path, session)

        self.xt_account = StockAccount(self.account_id, account_type=self.account_type)

        # 注册回调接口
        self.xt_client.register_callback(self)

        # 启动交易线程
        self.xt_client.start()

        # 建立交易连接，返回0表示连接成功
        connect_result: int = self.xt_client.connect()
        if connect_result:
            self.gateway.write_log("交易接口连接失败")
            return connect_result

        self.connected = True
        self.gateway.write_log("交易接口连接成功")

        # 订阅交易回调推送
        subscribe_result: int = self.xt_client.subscribe(self.xt_account)
        if subscribe_result:
            self.gateway.write_log("交易接口订阅失败")
            return -1

        self.gateway.write_log("交易接口订阅成功")

        # 初始化数据查询
        self.query_account()
        self.query_position()
        self.query_order()
        self.query_trade()

        return connect_result

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


def process_etf_option(get_instrument_detail: Callable, xt_symbol: str) -> Optional[ContractData]:
    """处理ETF期权"""
    # 拆分XT代码
    symbol, xt_exchange = xt_symbol.split(".")

    # 筛选期权合约合约（ETF期权代码为8位）
    if len(symbol) != 8:
        return None

    # 查询转换数据
    data: dict = get_instrument_detail(xt_symbol, True)

    name: str = data["InstrumentName"]
    if "购" in name:
        option_type = OptionType.CALL
    elif "沽" in name:
        option_type = OptionType.PUT
    else:
        return None

    if "A" in name:
        option_index = str(data["OptExercisePrice"]) + "-A"
    else:
        option_index = str(data["OptExercisePrice"]) + "-M"

    contract: ContractData = ContractData(
        symbol=data["InstrumentID"],
        exchange=EXCHANGE_XT2VT[xt_exchange],
        name=data["InstrumentName"],
        product=Product.OPTION,
        size=data["VolumeMultiple"],
        pricetick=data["PriceTick"],
        min_volume=data["MinLimitOrderVolume"],
        option_strike=data["OptExercisePrice"],
        option_listed=datetime.strptime(data["OpenDate"], "%Y%m%d"),
        option_expiry=datetime.strptime(data["ExpireDate"], "%Y%m%d"),
        option_portfolio=data["OptUndlCode"] + "_O",
        option_index=option_index,
        option_type=option_type,
        option_underlying=data["OptUndlCode"] + "-" + str(data["ExpireDate"])[:6],
        gateway_name="XT"
    )

    return contract


def process_futures_option(get_instrument_detail: Callable, xt_symbol: str) -> Optional[ContractData]:
    """处理期货期权"""
    # 筛选期权合约
    data: dict = get_instrument_detail(xt_symbol, True)

    option_strike: float = data["OptExercisePrice"]
    if not option_strike:
        return None

    # 拆分XT代码
    symbol, xt_exchange = xt_symbol.split(".")

    # 移除产品前缀
    for ix, w in enumerate(symbol):
        if w.isdigit():
            break

    suffix: str = symbol[ix:]

    # 过滤非期权合约
    if "(" in symbol or " " in symbol:
        return None

    # 判断期权类型
    if "C" in suffix:
        option_type = OptionType.CALL
    elif "P" in suffix:
        option_type = OptionType.PUT
    else:
        return None

    # 获取期权标的
    if "-" in symbol:
        option_underlying: str = symbol.split("-")[0]
    else:
        option_underlying: str = data["OptUndlCode"]

    # 转换数据
    contract: ContractData = ContractData(
        symbol=data["InstrumentID"],
        exchange=EXCHANGE_XT2VT[xt_exchange],
        name=data["InstrumentName"],
        product=Product.OPTION,
        size=data["VolumeMultiple"],
        pricetick=data["PriceTick"],
        min_volume=data["MinLimitOrderVolume"],
        option_strike=data["OptExercisePrice"],
        option_listed=datetime.strptime(data["OpenDate"], "%Y%m%d"),
        option_expiry=datetime.strptime(data["ExpireDate"], "%Y%m%d"),
        option_index=str(data["OptExercisePrice"]),
        option_type=option_type,
        option_underlying=option_underlying,
        gateway_name="XT"
    )

    if contract.exchange == Exchange.CZCE:
        contract.option_portfolio = data["ProductID"][:-1]
    else:
        contract.option_portfolio = data["ProductID"]

    return contract
