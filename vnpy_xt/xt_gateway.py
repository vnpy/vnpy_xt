from datetime import datetime, timedelta
from typing import Dict, Tuple, List

from xtquant import (
    xtdata,
    xtdatacenter as xtdc
)
from xtquant.xtconstant import (
    ORDER_UNREPORTED,
    ORDER_WAIT_REPORTING,
    ORDER_REPORTED,
    ORDER_REPORTED_CANCEL,
    ORDER_PARTSUCC_CANCEL,
    ORDER_PART_CANCEL,
    ORDER_CANCELED,
    ORDER_PART_SUCC,
    ORDER_SUCCEEDED,
    ORDER_JUNK,
    STOCK_BUY,
    STOCK_SELL,
    FUTURE_OPEN_LONG,
    FUTURE_OPEN_SHORT,
    FUTURE_CLOSE_SHORT_TODAY,
    FUTURE_CLOSE_LONG_TODAY,
    FUTURE_CLOSE_SHORT_HISTORY,
    FUTURE_CLOSE_LONG_HISTORY,
    DIRECTION_FLAG_BUY,
    DIRECTION_FLAG_SELL,
    MARKET_SH_CONVERT_5_CANCEL,
    MARKET_SZ_CONVERT_5_CANCEL,
    FIX_PRICE,
)
from filelock import FileLock, Timeout

from vnpy.event import EventEngine
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    OrderRequest,
    CancelRequest,
    SubscribeRequest,
    ContractData,
    TickData,
    HistoryRequest,
    OptionType,
    Offset
)
from vnpy.trader.constant import (
    OrderType,
    Direction,
    Exchange,
    Status,
    Product,
    Interval
)
from vnpy.trader.utility import (
    ZoneInfo,
    get_file_path,
    round_to
)


# 委托状态映射
STATUS_XT2VT: Dict[str, Status] = {
    ORDER_UNREPORTED: Status.SUBMITTING,
    ORDER_WAIT_REPORTING: Status.SUBMITTING,
    ORDER_REPORTED: Status.NOTTRADED,
    ORDER_REPORTED_CANCEL: Status.CANCELLED,
    ORDER_PARTSUCC_CANCEL: Status.CANCELLED,
    ORDER_PART_CANCEL: Status.CANCELLED,
    ORDER_CANCELED: Status.CANCELLED,
    ORDER_PART_SUCC: Status.PARTTRADED,
    ORDER_SUCCEEDED: Status.ALLTRADED,
    ORDER_JUNK: Status.REJECTED
}

# 多空方向映射
DIRECTION_VT2XT: Dict[tuple, int] = {
    (Direction.LONG, Offset.NONE): STOCK_BUY,
    (Direction.SHORT, Offset.NONE): STOCK_SELL,
    (Direction.LONG, Offset.OPEN): FUTURE_OPEN_LONG,
    (Direction.SHORT, Offset.OPEN): FUTURE_OPEN_SHORT,
    (Direction.LONG, Offset.CLOSE): FUTURE_CLOSE_SHORT_TODAY,
    (Direction.SHORT, Offset.CLOSE): FUTURE_CLOSE_LONG_TODAY,
    (Direction.LONG, Offset.CLOSETODAY): FUTURE_CLOSE_SHORT_TODAY,
    (Direction.SHORT, Offset.CLOSETODAY): FUTURE_CLOSE_LONG_TODAY,
    (Direction.LONG, Offset.CLOSEYESTERDAY): FUTURE_CLOSE_SHORT_HISTORY,
    (Direction.SHORT, Offset.CLOSEYESTERDAY): FUTURE_CLOSE_LONG_HISTORY,
}
DIRECTION_XT2VT: Dict[int, Direction] = {
    DIRECTION_FLAG_BUY: Direction.LONG,
    DIRECTION_FLAG_SELL: Direction.SHORT
}
STKDIRECTION_XT2VT: Dict[int, Direction] = {
    STOCK_BUY: Direction.LONG,
    STOCK_SELL: Direction.SHORT
}
FUTOFFSET_XT2VT: Dict[int, Offset] = {
    23: Offset.OPEN,
    24: Offset.CLOSE
}

# 委托类型映射
ORDERTYPE_VT2XT: Dict[Tuple, int] = {
    (Exchange.SSE, OrderType.MARKET): MARKET_SH_CONVERT_5_CANCEL,
    (Exchange.SZSE, OrderType.MARKET): MARKET_SZ_CONVERT_5_CANCEL,
    (Exchange.SSE, OrderType.LIMIT): FIX_PRICE,
    (Exchange.SZSE, OrderType.LIMIT): FIX_PRICE,
    (Exchange.BSE, OrderType.LIMIT): FIX_PRICE,
    (Exchange.SHFE, OrderType.LIMIT): FIX_PRICE,
    (Exchange.INE, OrderType.LIMIT): FIX_PRICE,
    (Exchange.CFFEX, OrderType.LIMIT): FIX_PRICE,
    (Exchange.CZCE, OrderType.LIMIT): FIX_PRICE,
    (Exchange.DCE, OrderType.LIMIT): FIX_PRICE,
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
        "token": ""
    }

    exchanges: List[str] = list(EXCHANGE_XT2VT.values())

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        """构造函数"""
        super().__init__(event_engine, gateway_name)

        self.md_api: "XtMdApi" = XtMdApi(self)

    def connect(self, setting: dict) -> None:
        """连接交易接口"""
        token: str = setting["token"]

        self.md_api.connect(token)

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        self.md_api.subscribe(req)

    def send_order(self, req: OrderRequest) -> str:
        """委托下单"""
        pass

    def cancel_order(self, req: CancelRequest) -> None:
        """委托撤单"""
        pass

    def query_account(self) -> None:
        """查询资金"""
        pass

    def query_position(self) -> None:
        """查询持仓"""
        pass

    def query_history(self, req: HistoryRequest) -> None:
        """查询历史数据"""
        return None

    def close(self) -> None:
        """关闭接口"""
        pass


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

    def connect(self, token: str) -> None:
        """连接"""
        self.token = token

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
        self.query_stock_contracts()
        self.query_future_contracts()

        self.gateway.write_log("合约信息查询成功")

    def query_stock_contracts(self) -> None:
        """查询股票合约信息"""
        xt_symbols: List[str] = []
        markets: list = ["SH", "SZ", "BJ"]

        for i in markets:
            names: list = xtdata.get_stock_list_in_sector(i)
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
            data: dict = xtdata.get_instrument_detail(xt_symbol)

            contract: ContractData = ContractData(
                symbol=symbol,
                exchange=MDEXCHANGE_XT2VT[xt_exchange],
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
        xt_symbols: List[str] = []
        markets: list = ["IF", "SF", "INE", "DF", "ZF", "GF"]

        for i in markets:
            names: list = xtdata.get_stock_list_in_sector(i)
            xt_symbols += names

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
                continue

            contract: ContractData = ContractData(
                symbol=symbol,
                exchange=MDEXCHANGE_XT2VT[xt_exchange],
                name=data["InstrumentName"],
                product=product,
                size=data["VolumeMultiple"],
                pricetick=data["PriceTick"],
                history_data=False,
                gateway_name=self.gateway_name
            )

            symbol_contract_map[contract.vt_symbol] = contract
            self.gateway.on_contract(contract)

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        if req.vt_symbol not in symbol_contract_map:
            return

        xt_symbol: str = req.symbol + "." + MDEXCHANGE_VT2XT[req.exchange]
        if xt_symbol not in self.subscribed:
            xtdata.subscribe_quote(stock_code=xt_symbol, period="tick", callback=self.onMarketData)
            self.subscribed.add(xt_symbol)

    def close(self) -> None:
        """关闭连接"""
        pass


def generate_datetime(timestamp: int, millisecond: bool = True) -> datetime:
    """生成本地时间"""
    if millisecond:
        dt: datetime = datetime.fromtimestamp(timestamp / 1000)
    else:
        dt: datetime = datetime.fromtimestamp(timestamp)
    dt: datetime = dt.replace(tzinfo=CHINA_TZ)
    return dt
