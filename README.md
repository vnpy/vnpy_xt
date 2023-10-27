# VeighNa框架的迅投接口

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

基于迅投极速交易终端 睿智融科版API（XtQuant）封装开发的数据接口。

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

连接请先登录迅投极速交易终端，同时确保xtquant模块可以正常加载（点击【下载Python库】-【Python库下载】，下载完成后拷贝“Python库路径”下Lib\site-packages文件夹中的xtquant包到自己使用的python环境的site_packages文件夹下）。