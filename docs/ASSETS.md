# 运行资源

源码仓库不内置大模型和个人数据。程序使用以下相对路径：

| 路径 | 内容与来源 |
| --- | --- |
| assets/babeldoc/models/doclayout_yolo_docstructbench_imgsz1024.onnx | DocLayout-YOLO 版面识别模型，[模型来源](https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx) |
| assets/babeldoc/cmap/ | PDF CMap 资源，来自 BabelDOC 配套资源 |
| assets/babeldoc/tiktoken/ | 分词缓存，来自 BabelDOC 配套资源 |
| assets/fonts/NotoSansSC*.ttf | Noto Sans SC 中文字体，现用静态 400 字重版本，许可见 licenses/ |
| assets/models/*.gguf | [Qwen2.5-7B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF) Q4_K_M，两分片须同时保留 |
| assets/llama/cpu/ | [llama.cpp b12069](https://github.com/ggml-org/llama.cpp/releases/tag/b12069) CPU Windows 可执行文件及运行依赖 |
| assets/llama/vulkan/ | 同版本 Vulkan 可执行文件及运行依赖，GPU 初始化失败时尝试 CPU |

`resources/fonts/` 中的小型科学符号字体已随源码保留，不等于上述完整中文字体。

## 已有完整便携包

运行 `python tools/prepare_assets.py --from-portable "自己的便携包目录"`。工具仅复制 `assets/`，不会读取或复制论文、任务、翻译结果、设置或密钥。复制后按 `assets-manifest.json` 检查每个资源。

## 从零准备

按上述上游项目取得对应版本与许可，放入约定目录。`assets-manifest.json` 保存作者现有运行资源的 SHA-256 与大小，便于核对已有完整资源。字体转换或上游重新打包可能改变散列，不能把散列不符称为相同发行资源。

本仓库暂不声称提供了“从零一键下载全部模型”的安装器。缺少资源时程序会报出缺失项。完整离线翻译会占用数 GB 磁盘空间，CPU 推理速度取决于电脑配置。
