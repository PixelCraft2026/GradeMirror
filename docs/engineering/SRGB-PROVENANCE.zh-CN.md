# sRGB 备用配置文件来源

简体中文 · [English original](../../engineering/source/assets/color/SRGB-PROVENANCE.txt)

这个 480 字节的 sRGB 备用配置文件是未经修改的 Qt 6.10.1 内置 sRGB 配置，由 `QColorSpace(QColorSpace.SRgb).iccProfile()` 取得。ICC 创建者版本字段的字节由 QtBase 的 `qicc.cpp` 写出。GradeMirror 固定使用这份配置，只用于桌面端导入原本未标记色彩配置的图片时的预览。出现这一配置并不能证明原文件内嵌了 ICC。

上游源码见 [QtBase qicc.cpp（v6.10.1）](https://code.qt.io/cgit/qt/qtbase.git/tree/src/gui/painting/qicc.cpp?h=v6.10.1)。

底层 Qt 实现适用 QtBase 的 LGPL-3.0 或可选 GPL 条款，原通知继续适用。用相同 Qt 版本和上述 API 可以重建字节完全相同的资源。

SHA-256：`cbdad829fe24e3c008b758aa8f68b849168296010c8b4947bdacfddadbc3c563`
