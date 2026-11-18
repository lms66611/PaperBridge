# PaperBridge · 文献对照翻译

**把英文 PDF 和中文译文放在同一页左右对照，保留段落位置，并把双语版带回 Zotero。**

当前桌面程序和 Zotero 插件版本为 **1.4.7**。本仓库由作者实际使用的版本整理，面向 Windows 10/11 x64。作者：李名硕，大连理工大学能源与动力学院。

## 为什么做这个软件

阅读英文文献时，来回切换原文、译文和 AI 对话，容易看错段落，也不方便检查数字、公式和术语。PaperBridge 把重复的翻译、对照和校对步骤整理到一个桌面入口。

它也是“文献阅读与专利分析助手”Skill 的实践来源之一。**PaperBridge 是独立软件，智慧芽 Skill 是另一项作品**，后者吸收了双语证据和原文回查的做法，使用 Skill 不必安装本软件。

## 可以做什么

- **左右对照阅读**：PDF 左侧保留原页面，右侧放中文译文，按原文位置安排段落。
- **校对与继续处理**：保存段落和任务进度，支持暂停、继续、修改译文及重新导出。
- **保留科学信息**：检查数字、单位和受保护符号，按原文证据处理上下标。检查不能代替专业人工核对。
- **Zotero 联动**：从所选文献发起任务，将生成的双语 PDF 作为附件附回，保留附件版本。
- **不同翻译路径**：通过 Translate for Zotero 调用在线初译；备齐本地资源后可离线翻译。OpenAI API 校核为可选项。
- **处理记录**：显示已记录的耗时、服务调用和 Token 用量。费用是估算，不是服务商账单。

## 先了解下载内容

GitHub 的 **Code / Download ZIP 是源码包**，不是免安装的 Windows 完整包。源码不包含约数 GB 的本地语言模型、版面模型和原生推理库，也不包含作者的论文、翻译记录、账户或 API 密钥。

运行源程序需要 Python 依赖与 `assets/` 资源。现有完整便携包的持有者可用下述工具导入资源；从零配置见 [资源说明](docs/ASSETS.md)。发布页若提供便携包，请完整下载该版本所需的所有分卷，不能只复制一个 EXE。

## 从源码运行

建议 Windows x64 和 **Python 3.12**，在可写目录中操作：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-deps -r requirements-runtime.lock
.\.venv\Scripts\python.exe prepare_vendor.py
# 将下方路径换成自己持有的完整便携包目录，只复制公共运行资源
.\.venv\Scripts\python.exe tools/prepare_assets.py --from-portable "D:\Software\PaperBridge-Portable"
.\.venv\Scripts\python.exe main.py
```

锁定文件记录本次已有运行环境的版本。依赖源找不到其中某个版本时，应核对环境兼容性，不建议直接把所有包升级到最新。

## 日常使用

1. 启动 PaperBridge，拖入可提取文字的 PDF。
2. 在设置中选择翻译方式。在线初译需要启动 Zotero 并安装 **Translate for Zotero**；离线模式需要本地模型和推理运行库。
3. 点击“开始 / 继续”，任务完成后打开双语 PDF。
4. 需要修订时打开校对界面，修改并重新导出。

连接 Zotero：在“工具 / 插件 / 从文件安装插件”中安装 [`zotero/PaperBridge.xpi`](zotero/PaperBridge.xpi)，重启 Zotero 并启动 PaperBridge，然后右键带 PDF 的文献使用 PaperBridge 菜单。Translate for Zotero 需[另外安装](https://github.com/windingwind/zotero-pdf-translate)。

## API 与联网范围

没有 GPT 账号或密钥也能选择其他翻译路径。可选 OpenAI API 需要用户自己的可用服务和额度，模型 ID 需按服务商实际支持情况设置。密钥由本机凭据管理器保存，不随仓库分发。

在线模式会将所需文本发送给所选翻译或模型服务；离线模式使用本机模型。免费在线服务可能限流或改变接口。当前独立软件的可选 GPT 校核使用 Responses 协议，**没有把参赛 Skill 扩展中的千问、DeepSeek 接口直接合并到此版本**。

## 限制

- 首版针对有文字层的 PDF，扫描件暂无 OCR。
- 复杂表格、公式、图像和拥挤页面可能需要校对，不承诺任意论文都严格一一复刻。
- 参考文献保留原文，原 PDF 的批注坐标不会自动迁移到重排后的双语 PDF。
- 当前主要输出为双语 PDF 和校对材料，旧版文档中提及的 Word 输出不作为 1.4.7 的保证。
- 本机使用和既有回归测试不能代替另一台电脑的完整安装验证。

## 开发与构建

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools/build_zotero.py
.\.venv\Scripts\python.exe build.py "D:\Software\PaperBridge-Portable\assets"
```

见 [开发说明](docs/DEVELOPMENT.md)。构建产物放在仓库内 `dist/`，构建缓存放在 `build/`，不会改动已安装程序。

## 许可证与致谢

应用源码沿用 **AGPL-3.0-or-later**。字体、模型和第三方运行库遵循各自条款，详见 [LICENSE](LICENSE)、[第三方组件说明](THIRD_PARTY_NOTICES.md) 和 [来源清单](vendor-source/source-manifest.json)。

感谢开源社区提供的 BabelDOC、PyMuPDF、llama.cpp、Qwen、Zotero 及相关工具。本项目不是 Zotero、OpenAI、智慧芽或其他服务商的官方产品。作者提出需求、参与方案选择和使用核对，开发与文档整理使用了 AI 协作。
