import importlib_metadata

from .xt_datafeed import XtDatafeed as Datafeed


try:
    __version__ = importlib_metadata.version("vnpy_xt")
except importlib_metadata.PackageNotFoundError:
    __version__ = "dev"
