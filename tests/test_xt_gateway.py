import importlib
import sys
import types
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from vnpy.trader.constant import Direction, Exchange, Offset, Product, Status
from vnpy.trader.object import ContractData, OrderData, SubscribeRequest, TickData
from vnpy.trader.utility import round_to


class _XtState:
    def __init__(self) -> None:
        self.quote_calls: list[tuple[str, str, object]] = []

    def subscribe_quote(self, stock_code: str, period: str, callback: object) -> None:
        self.quote_calls.append((stock_code, period, callback))


class _GatewayProbe:
    def __init__(self) -> None:
        self.gateway_name: str = "XT"
        self.ticks: list[TickData] = []
        self.orders: list[OrderData] = []

    def on_tick(self, tick: TickData) -> None:
        self.ticks.append(tick)

    def on_order(self, order: OrderData) -> None:
        self.orders.append(order)


class _XtOrder:
    def __init__(self, order_type: str, order_status: str, order_time: int) -> None:
        self.order_remark: str = "vnpy-1"
        # ORDERTYPE_XT2VT 只把 50 认作限价，其他价格类型不会生成 OrderData。
        self.price_type: int = 50
        self.order_type: str = order_type
        self.order_status: str = order_status
        self.stock_code: str = "600009.SH"
        self.price: float = 10.5
        self.order_volume: int = 200
        self.traded_volume: int = 0
        self.order_time: int = order_time
        self.order_sysid: str = "100"


def _purge(prefix: str) -> None:
    for name in list(sys.modules):
        if name == prefix or name.startswith(prefix + "."):
            del sys.modules[name]


def _vendor_module(name: str) -> types.ModuleType:
    module: types.ModuleType = types.ModuleType(name)
    module.__path__ = []
    module.__package__ = name
    sys.modules[name] = module
    if "." in name:
        parent, child = name.rsplit(".", 1)
        setattr(sys.modules[parent], child, module)
    return module


def _refuse_xtquant(*args: object, **kwargs: object) -> None:
    raise RuntimeError("xtquant network entry is disabled in unit tests")


def _install_xtquant(state: _XtState) -> None:
    _purge("xtquant")
    _purge("filelock")
    xtquant: types.ModuleType = _vendor_module("xtquant")
    xtdata: types.ModuleType = _vendor_module("xtquant.xtdata")
    xtdc: types.ModuleType = _vendor_module("xtquant.xtdatacenter")
    xtconstant: types.ModuleType = _vendor_module("xtquant.xtconstant")
    xttrader: types.ModuleType = _vendor_module("xtquant.xttrader")
    xttype: types.ModuleType = _vendor_module("xtquant.xttype")
    xtquant.xtdata = xtdata
    xtquant.xtdatacenter = xtdc
    xtquant.xtconstant = xtconstant
    xtquant.xttrader = xttrader
    xtquant.xttype = xttype

    constants: dict[str, str | int] = {
        "ORDER_UNREPORTED": "ORDER_UNREPORTED",
        "ORDER_WAIT_REPORTING": "ORDER_WAIT_REPORTING",
        "ORDER_REPORTED": "ORDER_REPORTED",
        "ORDER_REPORTED_CANCEL": "ORDER_REPORTED_CANCEL",
        "ORDER_PARTSUCC_CANCEL": "ORDER_PARTSUCC_CANCEL",
        "ORDER_PART_CANCEL": "ORDER_PART_CANCEL",
        "ORDER_CANCELED": "ORDER_CANCELED",
        "ORDER_PART_SUCC": "ORDER_PART_SUCC",
        "ORDER_SUCCEEDED": "ORDER_SUCCEEDED",
        "ORDER_JUNK": "ORDER_JUNK",
        "STOCK_BUY": "STOCK_BUY",
        "STOCK_SELL": "STOCK_SELL",
        "STOCK_OPTION_BUY_OPEN": "STOCK_OPTION_BUY_OPEN",
        "STOCK_OPTION_BUY_CLOSE": "STOCK_OPTION_BUY_CLOSE",
        "STOCK_OPTION_SELL_OPEN": "STOCK_OPTION_SELL_OPEN",
        "STOCK_OPTION_SELL_CLOSE": "STOCK_OPTION_SELL_CLOSE",
        "DIRECTION_FLAG_BUY": 1,
        "DIRECTION_FLAG_SELL": 2,
        "FIX_PRICE": 11,
    }
    name: str
    value: str | int
    for name, value in constants.items():
        setattr(xtconstant, name, value)

    xtdata.enable_hello = True
    xtdata.subscribe_quote = state.subscribe_quote
    xtdata.get_instrument_detail = _refuse_xtquant
    xtdata.get_stock_list_in_sector = _refuse_xtquant
    xtdata.download_history_data = _refuse_xtquant
    xtdata.get_local_data = _refuse_xtquant
    xtdc.set_token = _refuse_xtquant
    xtdc.set_allow_optmize_address = _refuse_xtquant
    xtdc.set_future_realtime_mode = _refuse_xtquant
    xtdc.init = _refuse_xtquant
    xtdc.listen = _refuse_xtquant

    class XtQuantTrader:
        def __init__(self, path: str, session: int) -> None:
            raise RuntimeError("xtquant network entry is disabled in unit tests")

        def register_callback(self, callback: object) -> None:
            _refuse_xtquant()

        def start(self) -> None:
            _refuse_xtquant()

        def connect(self) -> int:
            _refuse_xtquant()
            return 1

        def subscribe(self, account: object) -> int:
            _refuse_xtquant()
            return 1

    class XtQuantTraderCallback:
        pass

    class StockAccount:
        def __init__(self, account_id: str, account_type: str = "") -> None:
            self.account_id: str = account_id
            self.account_type: str = account_type

    class _XtType:
        pass

    xttrader.XtQuantTrader = XtQuantTrader
    xttrader.XtQuantTraderCallback = XtQuantTraderCallback
    xttype.StockAccount = StockAccount
    xttype.XtAsset = _XtType
    xttype.XtOrder = _XtType
    xttype.XtPosition = _XtType
    xttype.XtTrade = _XtType
    xttype.XtOrderResponse = _XtType
    xttype.XtCancelOrderResponse = _XtType
    xttype.XtOrderError = _XtType
    xttype.XtCancelError = _XtType

    filelock: types.ModuleType = types.ModuleType("filelock")

    class FileLock:
        def __init__(self, path: object) -> None:
            self.path: object = path

        def acquire(self, timeout: int = 1) -> None:
            return None

    class Timeout(Exception):
        pass

    filelock.FileLock = FileLock
    filelock.Timeout = Timeout
    sys.modules["filelock"] = filelock


def _preload_package(package_dir: Path) -> None:
    _purge(package_dir.name)
    package: types.ModuleType = types.ModuleType(package_dir.name)
    package.__path__ = [str(package_dir)]
    package.__package__ = package_dir.name
    sys.modules[package_dir.name] = package


def _put_contract(symbol: str, exchange: Exchange, pricetick: float) -> ContractData:
    contract: ContractData = ContractData(
        symbol=symbol,
        exchange=exchange,
        name=symbol,
        product=Product.EQUITY,
        size=1,
        pricetick=pricetick,
        gateway_name="XT",
    )
    gw.symbol_contract_map[contract.vt_symbol] = contract
    return contract


def _level_prices(tick: TickData, side: str) -> tuple[float, float, float, float, float]:
    if side == "bid":
        return (
            tick.bid_price_1,
            tick.bid_price_2,
            tick.bid_price_3,
            tick.bid_price_4,
            tick.bid_price_5,
        )
    return (
        tick.ask_price_1,
        tick.ask_price_2,
        tick.ask_price_3,
        tick.ask_price_4,
        tick.ask_price_5,
    )


STATE: _XtState = _XtState()
_install_xtquant(STATE)
_preload_package(Path(__file__).resolve().parent.parent / "vnpy_xt")
gw = importlib.import_module("vnpy_xt.xt_gateway")
_quote = gw.xtdata.subscribe_quote
if getattr(_quote, "__self__", None) is not STATE or getattr(_quote, "__func__", None) is not _XtState.subscribe_quote:
    raise RuntimeError("xt_gateway imported xtquant before the test fake was installed")
if "vnpy_xt.xt_datafeed" in sys.modules:
    raise RuntimeError("vnpy_xt package init loaded the gateway before the test fake was installed")


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    STATE.quote_calls.clear()
    gw.symbol_contract_map.clear()
    gw.symbol_limit_map.clear()


def test_generate_datetime_millisecond_and_second_use_china_tz() -> None:
    naive: datetime = datetime(2024, 1, 2, 9, 30, 0)
    seconds: int = int(naive.timestamp())
    millis: int = seconds * 1000
    from_millis: datetime = gw.generate_datetime(millis)
    from_seconds: datetime = gw.generate_datetime(seconds, False)
    expected: datetime = datetime.fromtimestamp(seconds).replace(tzinfo=gw.CHINA_TZ)

    assert datetime.fromtimestamp(seconds) == naive
    assert from_millis.tzinfo is gw.CHINA_TZ
    assert from_seconds.tzinfo is gw.CHINA_TZ
    assert from_millis == expected
    assert from_seconds == expected
    assert gw.generate_datetime(seconds) != from_seconds


def test_on_market_data_rounds_bid_ask_to_pricetick() -> None:
    pricetick: float = 0.01
    contract: ContractData = _put_contract("600009", Exchange.SSE, pricetick)
    bid_raw: list[float] = [10.014, 10.016, 10.024, 10.026, 10.034]
    ask_raw: list[float] = [10.044, 10.046, 10.054, 10.056, 10.064]
    probe: _GatewayProbe = _GatewayProbe()
    api: gw.XtMdApi = gw.XtMdApi(cast(gw.XtGateway, probe))
    api.onMarketData(
        {
            "600009.SH": [
                {
                    "time": 1_704_164_462_000,
                    "volume": 1000,
                    "amount": 10000.0,
                    "openInt": 0,
                    "bidPrice": bid_raw,
                    "askPrice": ask_raw,
                    "bidVol": [1, 2, 3, 4, 5],
                    "askVol": [6, 7, 8, 9, 10],
                    "lastPrice": 10.015,
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "lastClose": 9.8,
                }
            ]
        }
    )

    assert len(probe.ticks) == 1
    tick: TickData = probe.ticks[0]
    raw_levels: tuple[list[float], list[float]] = (bid_raw, ask_raw)
    sides: tuple[str, str] = ("bid", "ask")
    index: int
    for index, side in enumerate(sides):
        level: float
        for level_index, level in enumerate(raw_levels[index]):
            rounded: float = _level_prices(tick, side)[level_index]
            assert rounded == round_to(level, contract.pricetick)
            assert rounded != level


def test_subscribe_sse_symbol_calls_subscribe_quote_once() -> None:
    _put_contract("600009", Exchange.SSE, 0.01)
    probe: _GatewayProbe = _GatewayProbe()
    api: gw.XtMdApi = gw.XtMdApi(cast(gw.XtGateway, probe))

    api.subscribe(SubscribeRequest(symbol="600009", exchange=Exchange.SSE))

    assert len(STATE.quote_calls) == 1
    stock_code: str
    period: str
    callback: object
    stock_code, period, callback = STATE.quote_calls[0]
    assert stock_code == "600009.SH"
    assert period == "tick"
    assert callback == api.onMarketData


def test_on_stock_order_maps_direction_offset_and_status() -> None:
    order_type: str = gw.xtconstant.STOCK_OPTION_SELL_OPEN
    order_status: str = gw.xtconstant.ORDER_REPORTED
    xt_order: _XtOrder = _XtOrder(order_type, order_status, 1_704_164_462)
    probe: _GatewayProbe = _GatewayProbe()
    api: gw.XtTdApi = gw.XtTdApi(cast(gw.XtGateway, probe))

    api.on_stock_order(xt_order)

    assert len(probe.orders) == 1
    order: OrderData = probe.orders[0]
    assert order.direction == Direction.SHORT
    assert order.offset == Offset.OPEN
    assert order.status == Status.NOTTRADED
