# 开发说明

## 结构

- main.py：GUI 和命令行入口。
- paperbridge/：解析、排版、翻译、术语检查、Zotero 通信、任务保存等。
- zotero/：Zotero 插件源码及 XPI 安装包。
- resources/fonts/：科学符号字体与许可。
- tools/：资源准备和插件构建工具。
- tests/：不依赖个人论文或真实 API 的回归测试。

程序运行后在本地创建 `data/`、`results/`。Zotero 连接发现文件保存在当前用户的本机应用数据目录。`review.py` 包含显式调用时读取用户自己 CC Switch 配置的辅助校核功能，该配置与数据库不随项目分发，日常翻译入口不要求 CC Switch。

## 测试

在仓库根目录执行 `python -m pytest -q`。测试使用合成句子、本地临时文件和替身回包，不发送付费模型调用。若在指定磁盘测试，可加 `--basetemp 自己的临时目录`。

## Windows 便携包构建

先安装锁定依赖，运行 `prepare_vendor.py`，准备完整 `assets/` 后执行 `python build.py PATH_TO_ASSETS`。构建器将 PyInstaller 运行库、当前应用源码、字体资源、插件和运行资源整理到 `dist/PaperBridge-Portable`，每次使用新的暂存目录，不读取个人任务和结果。

`build.py` 是公开源码版的构建入口。它不重用作者个人电脑的安装目录、凭据或更新缓存。本次发布会进行源码回归与干净输入导出检查，未重新执行完整的多 GB Windows 安装包构建时，不将其写成新便携包已验收。
