# Third-party components

PaperBridge source is licensed under AGPL-3.0-or-later. This repository contains the application source. Large model/runtime binaries and unmodified upstream source archives are not stored in Git; see vendor-source/source-manifest.json and docs/ASSETS.md.

| Component | Purpose | License / upstream |
|---|---|---|
| BabelDOC 0.6.4 | Document layout implementation and PDF resource definitions | AGPL-3.0, https://github.com/funstory-ai/BabelDOC/tree/v0.6.4 |
| DocLayout-YOLO DocStructBench ONNX | Layout detection weights | Upstream model terms, https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx |
| llama.cpp b12069 | CPU and Vulkan GGUF inference executables | MIT, https://github.com/ggml-org/llama.cpp/tree/b12069 |
| Qwen2.5-7B-Instruct-GGUF Q4_K_M | Local model weights | Apache-2.0, https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF |
| Noto Sans SC | Embedded PDF font | SIL Open Font License 1.1, https://github.com/notofonts/noto-cjk |
| Noto Sans | Latin, subscript and scientific text fallback, unmodified font | SIL Open Font License 1.1, https://github.com/notofonts/noto-fonts; license at resources/fonts/OFL-NotoSans.txt |
| Noto Sans Math | Embedded scientific symbols, unmodified font | SIL Open Font License 1.1, https://github.com/google/fonts/tree/main/ofl/notosansmath; license at resources/fonts/OFL-NotoSansMath.txt |
| Translate for Zotero | Public translation API used by bridge; plugin installed separately | AGPL-3.0, https://github.com/windingwind/zotero-pdf-translate |
| Zotero PDF2zh | Architecture reference for attachment import; not a required backend | AGPL-3.0, https://github.com/guaguastandup/zotero-pdf2zh |
| PyMuPDF | PDF text/image access | AGPL-3.0, https://github.com/pymupdf/PyMuPDF |
| MuPDF 1.28.6 | Native PDF engine in PyMuPDF 2.1 | AGPL-3.0, https://mupdf.com/downloads/archive/mupdf-1.28.6-source.tar.gz |
| ReportLab | PDF generation | BSD, https://www.reportlab.com |
| python-docx | DOCX generation | MIT, https://github.com/python-openxml/python-docx |
| tkinterdnd2 / TkDND | Desktop drag and drop | MIT/BSD, upstream notices retained in runtime |
| PyInstaller | Frozen runtime packaging | GPL with bootloader distribution exception, https://pyinstaller.org |
| ONNX / ONNX Runtime | Layout inference | Apache-2.0 / MIT |

Additional runtime package licenses are retained in the distribution and recorded in the locked requirements. No cloud translation credentials are included. Official provider services are contacted only for the selected online mode.

The Noto Sans SC font used in this package was converted to a static weight-400 TrueType font with fontTools. Its original copyright and reserved-font-name metadata are retained in `licenses/Noto-font-metadata.json`; the family name does not use the reserved name Source. The OFL text is included.

llama.cpp's LLVM OpenMP notice is retained in each native engine directory. Windows Microsoft Visual C++ runtime DLLs are placed beside the native executables as app-local runtime components; those Microsoft components retain their original copyright and are not relicensed under the application's AGPL.

Modification of BabelDOC: resource-root initialization supports `PAPERBRIDGE_BABELDOC_CACHE`; no other upstream behavior is required to be modified. PaperBridge uses the upstream ONNX model wrapper and applies its own paragraph bookkeeping and bilingual renderer.
