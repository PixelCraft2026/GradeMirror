# 工程源码包第三方组件说明

简体中文 · [English original](../../engineering/THIRD-PARTY.txt)

配套的第三方组件包括 Qt for Python Essentials 和 Shiboken 6.11.3、Qt 6.11.3 及其插件。锁文件标识原始上游输入。请参阅随附的 `third-party/QT-SOURCE-INDEX.json` 和分发材料，或查看以下上游目录。

- [Qt 6.11.3 源码](https://download.qt.io/official_releases/qt/6.11/6.11.3/submodules/)
- [PySide6 6.11.3 源码](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.3-src/)

GradeMirror 的备用 ICC 是此前用 Qt 6.10.1 生成的原始配置文件，见 [sRGB 配置文件来源](SRGB-PROVENANCE.zh-CN.md)。

运行时还包含冻结版本的 CPython、Qt 厂商组件、Torch、GPU 版中的 NVIDIA 库、NumPy、SciPy、OpenCV、Pillow、其它 wheel 包，以及 ExifTool 和它的 Windows 运行环境。随附的通知和索引列出条款及开放源码条件。各软件文件保留原有许可。请按[构建说明](BUILD.zh-CN.md)和各锁文件取得独立构建所需的精确输入。

`qt-prune.json` 记录了 Qt 桌面 wheel 中未随包提供的可选项目。存在 ABI 依赖的部分一并排除。本预览版不含 Qt PDF 图像插件。应用使用其余已绑定身份的 Qt 文件。锁文件仍标识完整的原始 wheel。
