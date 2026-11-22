# GradeMirror 0.1.0 — 首次预览版

简体中文 · [English](RELEASE-v0.1.0.en.md)

GradeMirror 是 Windows x64 本地图像色彩匹配工具。本预览版支持 JPEG、PNG、TIFF、WebP 和 BMP，可预览、保存工程并导出。

内部构建为 `0.1.0-preview.50`。「帮助」提供设备与运行状态（Ctrl+Alt+R）和重新定位原图。仿色底栏在收到进度后显示当前阶段与计数，并在数据足够时给出剩余时间区间。

从 Windows 资源管理器拖入时，工作台通过文件路径接收图片，预览使用随包解码器。此调整适用于 CPU 和 GPU 便携版。

两版的 `GradeMirror.exe` 和 `GradeMirror-CLI.exe` 在资源管理器中使用 GradeMirror 双镜面标识。

CPU 路线可安全回收已验证结束的旧版 CPU 驻留会话，升级后可继续使用现有工程和配方。CPU 版和 GPU 版的 CPU 路线共用该处理。

取得便携版前请阅读适用的接收者约定。两版均含 Microsoft 运行组件，GPU 版另含 NVIDIA 运行组件。

<details>
<summary>下载前阅读 Microsoft 和 NVIDIA 接收者约定</summary>

### 随附 Microsoft 对象代码的接收者约定

本文仅针对GradeMirror随附的Microsoft运行组件（源码与第三方材料包内 `third-party/msvc/MSVC-FILE-MAP.json`）。分发和使用这些组件时，请同意并遵守本文及随附原许可中适用于这些组件的权利、限制与保护；该约定不提供Visual Studio开发工具本身的使用许可。

- 可随GradeMirror使用所附Microsoft运行组件，保留其版权和其它权利通知。Microsoft及其许可方保留未明确授予的权利。
- 作为GradeMirror分发者转交这些对象代码时，应作为同一应用的一部分、保持组件未修改、附带本说明和适用许可，并要求后续分发者和最终用户接受不低于原许可保护程度的约定。不以此取得单独销售、出租、提供或任意重新组合Microsoft软件的权利。
- 不移除、遮蔽或修改权利通知；不绕过组件技术限制，不进行逆向工程、反编译或反汇编，但适用法律或适用的第三方许可明确允许的范围除外。不以Microsoft商标暗示应用由Microsoft发布或背书。
- 不将Microsoft对象代码冒称AGPL授权代码，不以修改或分发其源代码的方式使其受原许可所排除的源代码披露或修改权要求约束。不得分发不获许可的预发布或调试运行组件。
- 遵守适用法律和出口限制。Microsoft组件按原许可的现状、无保证及责任限制条件提供；原许可及适用的强制性法律规定优先。

GradeMirror自有代码继续依AGPL-3.0-only授权，用户对该自有代码的使用、取得源码、研究、修改及再分发权利不因本文改变。其它第三方组件各按其原许可。

阅读分发说明（源码与第三方材料包内 `third-party/msvc/MSVC-DISTRIBUTION.md`）和Microsoft原许可（源码与第三方材料包内 `third-party/msvc/notices/Visual-Studio-2022-Community-License-EN.txt`）。适用原文及组件范围也随本版程序和完整材料包提供。

### 随附 NVIDIA 对象代码的接收者约定

适用本版 GradeMirror GPU 便携版中列于 NVIDIA 交付说明（源码与第三方材料包内 `third-party/NVIDIA-DISTRIBUTION.md`） 的 NVIDIA CUDA、NVTX 和 cuDNN 二进制。阅读并同意其中所附适用协议及以下约定后，在协议允许的范围内随 GradeMirror 使用这些组件。

- 只通过 GradeMirror 的 GPU 工作角色使用所附组件，并在适用的 NVIDIA GPU 系统上使用。保留厂商版权、许可和知识产权通知。遵守原协议对复制、修改、逆向工程、单独提供组件及出口的限制，包括适用法律明确保留的权利。
- 转交含这些组件的完整 GPU 版时，须有适用的再分发授予，随应用保留同版通知和接收者约定，并使下游分发和使用与适用原协议一致。
- NVIDIA 及其许可方保留未明确授予的权利。原协议及适用法律规定使用、支持、责任和救济。另有明确许可的组件依其适用许可。
- GradeMirror 自有源码适用 AGPL-3.0-only（源码与第三方材料包内 `LICENSE`），其权利继续按该许可行使。其它第三方组件各按原有许可。

</details>

- **CPU**：[下载一个 ZIP](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Windows-CPU.zip)。全部解压，进入 `GradeMirror-CPU`，双击 `GradeMirror.exe`。
- **NVIDIA GPU**：[下载 GPU 1](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Windows-GPU-1.zip) 和 [GPU 2](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Windows-GPU-2.zip)。两份都必须下载。对两个 ZIP 分别选择 Windows“全部解压”，两次都手动设置为同一个目标目录。两个包都应解压到同一目标下的 `GradeMirror-GPU` 文件夹。请替换 Windows 预填的两个不同包名目录。两份都解压完后，再进入 `GradeMirror-GPU` 双击 `GradeMirror.exe`。

两版都可直接解压运行。GPU 版需要适用的 NVIDIA 显卡和兼容驱动。无需另装 Python、CUDA Toolkit、Visual Studio 或额外的解压工具。随包的“开始使用”和许可通知位于解压目录。

## 预览版范围

已在 Windows 10 22H2 x64 验证便携启动。Windows 11 build 26200 已验证从 preview.37 回收旧驻留记录，CPU 版和 GPU 版指定 CPU 均完成合成 TIFF／PNG 快速仿色。Win11 原生 GPU 路线及其他干净离线主机仍待验证。此版没有 Qt PDF 图像插件。

- 写入失败可能留下目标旁的 `.gm004a-…partial`。退出 GradeMirror 和导出进程后，可按目的地、请求键及 `intent.json` 核实归属。归属不明时保留文件。
- 当前 Pillow 版本有 TIFF/EXIF 可能耗尽内存的已知风险，请处理可信图片。 [Pillow 公告](https://github.com/python-pillow/Pillow/security/advisories/GHSA-wqgw-9xfh-qf2g)
- 活跃处理中的 GUI 取消尚未验证。

## 源码与第三方材料

[下载完整源码与第三方材料 ZIP](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Source-and-ThirdParty.zip)。包含完整工程源码 ZIP、第三方对应源码、可替换 Qt 的构建说明、原许可通知和成员清单。GradeMirror 自有代码按 AGPL-3.0-only 发布，第三方按各自许可。GitHub 自动生成的标签快照与本版提供的完整工程源码包各自独立。

<details>
<summary>四个附件的 SHA-256</summary>

```text
41a4584261f9495bcf2b03645a96e37bb7f3d545ceee23febfa6bfdd61bae5b5  GradeMirror-0.1.0-Windows-CPU.zip
0cd3ad0efd503247f5504ec2bcab3e7b2a42d9cd2ac978baf0f2eae3c8afc6ae  GradeMirror-0.1.0-Windows-GPU-1.zip
d68359c78f44fe9bcb8c6ca7f747c2f3707b803e57f6492fa9eb1b48c254d3c6  GradeMirror-0.1.0-Windows-GPU-2.zip
06107d52e17d52b7572d76d6daffcc8b61c3ae0fb45dd6f2bcd436d9f83c4949  GradeMirror-0.1.0-Source-and-ThirdParty.zip
```

</details>

报告问题请提供版型、版本和 Windows build，优先使用小型合成样本。 [问题入口](https://github.com/PixelCraft2026/GradeMirror/issues)
