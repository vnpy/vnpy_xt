from datetime import datetime, timedelta, time
from typing import Dict, Tuple, List

from pandas import DataFrame
from xtquant.xtdata import (
    subscribe_quote,
    get_instrument_detail,
    get_local_data,
    download_history_data,
    get_stock_list_in_sector
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

from vnpy.event import EventEngine
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    OrderRequest,
    CancelRequest,
    SubscribeRequest,
    ContractData,
    TickData,
    BarData,
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
from vnpy.trader.utility import ZoneInfo


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
        "路径": "",
        "资金账号": "",
        "账户类型": ["股票", "期货"]
    }

    exchanges: List[str] = list(EXCHANGE_XT2VT.values())

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        """构造函数"""
        super().__init__(event_engine, gateway_name)

        self.md_api: "XtMdApi" = XtMdApi(self)

    def connect(self, setting: dict) -> None:
        """连接交易接口"""
        self.md_api.connect()

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

    def __init__(self, gateway: XtGateway) -> None:
        """构造函数"""
        self.gateway: XtGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.inited: bool = False
        self.subscribed: set = set()
        self.bidding_bar: BarData = None

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
        self.gateway.td_api.query_order()
        self.gateway.td_api.query_trade()

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
                exchange=MDEXCHANGE_XT2VT[xt_exchange],
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
                data: dict = get_instrument_detail(xt_symbol, True)
            else:
                data: dict = get_instrument_detail(xt_symbol)

            if not data["ExpireDate"]:
                continue

            contract: ContractData = ContractData(
                symbol=symbol,
                exchange=MDEXCHANGE_XT2VT[xt_exchange],
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
        data: dict = get_local_data([], [xt_symbol], xt_interval, start, end, -1, "front_ratio", False)      # 默认等比前复权

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
                if exchange in (Exchange.SSE, Exchange.SZSE, Exchange.BSE, Exchange.CFFEX) and dt.time() < time(hour=9, minute=30):
                    self.bidding_bar = BarData(
                        symbol=req.symbol,
                        exchange=req.exchange,
                        datetime=dt,
                        open_price=float(tp.open),
                        volume=float(tp.volume),
                        turnover=float(tp.amount),
                        gateway_name="XT"
                    )
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

            if self.bidding_bar:
                bar.volume += self.bidding_bar.volume
                bar.turnover += self.bidding_bar.turnover
                bar.open_price = self.bidding_bar.open_price
                self.bidding_bar = None

            history.append(bar)

        # 输出日志信息
        if history:
            msg: str = f"获取历史数据成功，{req.vt_symbol} - {interval.value}，{history[0].datetime} - {history[-1].datetime}"
            self.gateway.write_log(msg)

        return history

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
