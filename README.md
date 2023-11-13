# VeighNa框架的迅投数据服务接口

<p align="center">
  <img src ="https://vnpy.oss-cn-shanghai.aliyuncs.com/vnpy-logo.png"/>
</p>

<p align="center">
    <img src ="https://img.shields.io/badge/version-1.0.0-blueviolet.svg"/>
    <img src ="https://img.shields.io/badge/platform-windows-yellow.svg"/>
    <img src ="https://img.shields.io/badge/python-3.10-blue.svg" />
    <img src ="https://img.shields.io/github/license/vnpy/vnpy.svg?color=orange"/>
</p>

## 说明

基于迅投XtQuant封装开发的数据服务接口。

## 安装

安装需要基于3.9.0版本的【[**VeighNa**](https://github.com/vnpy/vnpy)】。

直接使用pip命令：

```
pip install vnpy_xt
```


或者下载解压后在cmd中运行：

```
python setup.py install
```

## 连接

客户端连接：

 1. 连接请先登录迅投极速交易终端，同时确保xtquant模块可以正常加载（点击【下载Python库】-【Python库下载】，下载完成后拷贝“Python库路径”下Lib\site-packages文件夹中的xtquant包到自己使用的Python环境的site_packages文件夹下）。
 2. 在Veighna Trader的【全局配置】处进行数据服务配置（datafeed.name填“xt”，datafeed.username填“client”）并保持迅投客户端的运行。
 3. 请注意，若datafeed.username配置为“client”，无论是否配置datafeed.password，datafeed都会通过客户端连接。

Token连接：
 1. 连接前请先确保xtquant模块可以正常加载（在投研知识库（http://docs.thinktrader.net/）下载xtquant的安装包，解压后放置xtquant包到自己使用的Python环境的site_packages文件夹下）。
 2. 登录迅投研服务平台，在【用户中心】-【个人设置】-【接口TOKEN】处获取Token（https://xuntou.net/#/userInfo）。
 3. 在VeighNa Trader的【全局配置】处进行数据服务配置（datafeed.name填“xt”，datafeed.password填复制的Token）。
 4. 请注意，通过Token连接无需保持迅投客户端的运行。
