# VeighNa框架的迅投QMT Mini交易接口

<p align="center">
  <img src ="https://vnpy.oss-cn-shanghai.aliyuncs.com/vnpy-logo.png"/>
</p>

<p align="center">
    <img src ="https://img.shields.io/badge/version-1.0.0-blueviolet.svg"/>
    <img src ="https://img.shields.io/badge/platform-windows-yellow.svg"/>
    <img src ="https://img.shields.io/badge/python-3.7|3.8|3.9-blue.svg" />
    <img src ="https://img.shields.io/github/license/vnpy/vnpy.svg?color=orange"/>
</p>

## 说明

基于迅投MiniQMT的接口封装开发的QMT极速策略交易接口。

## 安装

安装需要基于3.0.0版本的【[**VeighNa**](https://github.com/vnpy/vnpy)】和Python3.7/3.8/3.9环境。

直接使用pip命令：

```
pip install vnpy_xt
```


或者下载解压后在cmd中运行：

```
python setup.py install
```

## 使用

以脚本方式启动：

```
from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp

from vnpy_uf import XtGateway


def main():
    """主入口函数"""
    qapp = create_qapp()

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(XtGateway)
    
    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()

    qapp.exec()


if __name__ == "__main__":
    main()
```

## 连接

连接前请先登录QMT极速策略交易平台交易终端（点击安装文件夹中bin.x64下的XtMiniQmt.exe启动）。

请注意：
1. 目前使用需要先点击安装文件夹中bin.x64下的XtItClient.exe登录客户端，点击中间界面上方的【下载Python库】-【Python库下载】下载所需的第三方包。下载完成后，再将xtquant包的路径添加到python的环境变量中。

2. 登录XtGateway时填写的【路径】参数为f"{放置安装文件夹的路径}\\userdata_mini"；填写的【资金账号】参数是账号资金页面的账号，不是登录客户端的账号。