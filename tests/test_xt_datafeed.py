import importlib
import sys
import types
from datetime import datetime
from pathlib import Path
from typing import cast

import pandas as pd
import pytest

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData, HistoryRequest


class _XtState:
    def __init__(self) -> None:
        self.downloads: list[tuple[str, str, str, str]] = []
        self.frame: pd.DataFrame | None = None

    def download_history_data(self, xt_symbol: str, period: str, start: str, end: str) -> None:
        self.downloads.append((xt_symbol, period, start, end))

    def get_local_data(self, *args: object) -> dict[str, pd.DataFrame]:
        stocks: list[str] = cast(list[str], args[1])
        frame: pd.DataFrame = self.frame if self.frame is not None else pd.DataFrame()
        return {stocks[0]: frame}

    def get_instrument_detail(self, symbol: str) -> None:
        raise RuntimeError("xtdata.get_instrument_detail must not run")


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
    xtquant.xtdata = xtdata
    xtquant.xtdatacenter = xtdc
    xtdata.enable_hello = True
    xtdata.download_history_data = state.download_history_data
    xtdata.get_local_data = state.get_local_data
    xtdata.get_instrument_detail = state.get_instrument_detail
    xtdc.set_token = _refuse_xtquant
    xtdc.set_allow_optmize_address = _refuse_xtquant
    xtdc.set_future_realtime_mode = _refuse_xtquant
    xtdc.init = _refuse_xtquant
    xtdc.listen = _refuse_xtquant

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


STATE: _XtState = _XtState()
_install_xtquant(STATE)
_preload_package(Path(__file__).resolve().parent.parent / "vnpy_xt")
xt = importlib.import_module("vnpy_xt.xt_datafeed")


def _request(symbol: str, exchange: Exchange, interval: Interval) -> HistoryRequest:
    return HistoryRequest(
        symbol=symbol,
        exchange=exchange,
        start=datetime(2024, 1, 2, 9, 30),
        end=datetime(2024, 1, 2, 15, 0),
        interval=interval,
    )


def _feed() -> object:
    feed: object = xt.XtDatafeed()
    feed.inited = True
    return feed


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    STATE.downloads.clear()
    STATE.frame = None


@pytest.mark.parametrize(
    ("symbol", "exchange", "interval", "xt_symbol", "xt_interval"),
    [
        ("600009", Exchange.SSE, Interval.MINUTE, "600009.SH", "1m"),
        ("000001", Exchange.SZSE, Interval.DAILY, "000001.SZ", "1d"),
        ("830799", Exchange.BSE, Interval.MINUTE, "830799.BJ", "1m"),
        ("rb2410", Exchange.SHFE, Interval.MINUTE, "rb2410.SF", "1m"),
        ("IF2406", Exchange.CFFEX, Interval.MINUTE, "IF2406.IF", "1m"),
        ("sc2412", Exchange.INE, Interval.MINUTE, "sc2412.INE", "1m"),
        ("i2409", Exchange.DCE, Interval.MINUTE, "i2409.DF", "1m"),
        ("TA501", Exchange.CZCE, Interval.MINUTE, "TA501.ZF", "1m"),
        ("si2412", Exchange.GFEX, Interval.MINUTE, "si2412.GF", "1m"),
        ("10005467", Exchange.SSE, Interval.MINUTE, "10005467.SHO", "1m"),
    ],
)
def test_history_request_symbol_and_interval(
    symbol: str,
    exchange: Exchange,
    interval: Interval,
    xt_symbol: str,
    xt_interval: str,
) -> None:
    feed = cast(xt.XtDatafeed, _feed())

    assert feed.query_bar_history(_request(symbol, exchange, interval)) == []
    assert STATE.downloads == [(xt_symbol, xt_interval, "20240102093000", "20240103150000")]


def test_fake_response_becomes_bar() -> None:
    bar_end: datetime = datetime(2024, 1, 2, 10, 1)
    STATE.frame = pd.DataFrame(
        {
            "time": [int(bar_end.timestamp() * 1000)],
            "open": [10.5],
            "high": [11.0],
            "low": [10.0],
            "close": [10.75],
            "volume": [100.0],
            "amount": [1075.0],
            "openInterest": [8.0],
        }
    )
    feed = cast(xt.XtDatafeed, _feed())
    bars: list[BarData] = feed.query_bar_history(_request("600009", Exchange.SSE, Interval.MINUTE))

    assert STATE.downloads == [("600009.SH", "1m", "20240102093000", "20240103150000")]
    assert len(bars) == 1
    bar: BarData = bars[0]
    assert bar.symbol == "600009"
    assert bar.exchange == Exchange.SSE
    assert bar.vt_symbol == "600009.SSE"
    assert bar.datetime == datetime(2024, 1, 2, 10, 0, tzinfo=xt.CHINA_TZ)
    assert bar.open_price == 10.5
    assert bar.high_price == 11.0
    assert bar.low_price == 10.0
    assert bar.close_price == 10.75
    assert bar.volume == 100.0


def test_unsupported_interval_does_not_download() -> None:
    feed = cast(xt.XtDatafeed, _feed())
    logs: list[str] = []

    assert feed.query_bar_history(_request("600009", Exchange.SSE, Interval.HOUR), logs.append) == []
    assert STATE.downloads == []
    assert logs == ["迅投研查询历史数据失败：不支持的时间周期1h"]
