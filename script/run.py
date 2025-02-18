from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp

from vnpy_xt import XtGateway

# from vnpy_datamanager import DataManagerApp


# 配置datafeed相关信息，也可以通过vt_setting.json全局文件配置
# from vnpy.trader.setting import SETTINGS
# SETTINGS["datafeed.name"] = "xt"
# SETTINGS["datafeed.username"] = "token"
# SETTINGS["datafeed.password"] = "xxx"


def main():
    """主入口函数"""
    qapp = create_qapp()

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(XtGateway)
    # main_engine.add_app(DataManagerApp)

    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()

    qapp.exec()


if __name__ == "__main__":
    main()
