import importlib_metadata

from .gateway import XtGateway


try:
    __version__ = importlib_metadata.version("vnpy_xt")
except importlib_metadata.PackageNotFoundError:
    __version__ = "dev"