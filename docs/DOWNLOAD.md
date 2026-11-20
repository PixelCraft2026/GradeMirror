# 下载与使用便携版

[返回中文首页](../README.zh-CN.md) · [本版 Release](https://github.com/PixelCraft2026/GradeMirror/releases/tag/v0.1.0)

本版面向 Windows x64。CPU 和 GPU 是二选一的完整版型，不要将两种版本覆盖到同一个目录。

## CPU：一个 ZIP

1. 阅读 [Microsoft 组件约定](MSVC-RECIPIENT-TERMS.md)。
2. 下载 [CPU ZIP](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Windows-CPU.zip)。
3. 右键“全部解压”，打开 `GradeMirror-CPU`，双击 `GradeMirror.exe`。不要直接从压缩包窗口运行。

## NVIDIA GPU：两个 ZIP

1. 阅读 [Microsoft 组件约定](MSVC-RECIPIENT-TERMS.md) 和 [NVIDIA 组件约定](NVIDIA-RECIPIENT-TERMS.md)。
2. 下载 [GPU 1](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Windows-GPU-1.zip) 与 [GPU 2](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Windows-GPU-2.zip)。两份缺一不可，第一包不能单独使用。
3. 对两包分别执行“全部解压”，两次都指定同一个目标。请替换 Windows 按 ZIP 文件名预填的目标目录；两包应共同写入目标目录下的 `GradeMirror-GPU` 文件夹。
4. 等两包都解压完成，进入共同的 `GradeMirror-GPU`，双击 `GradeMirror.exe`。

这是两个普通 ZIP，不需要合并脚本。无需另装 Python、CUDA Toolkit 或 Visual Studio。GPU 版需要适用的 NVIDIA 显卡及兼容驱动。

## 启动遇到问题

先确认文件已完整解压；GPU 两包内容必须在同一个目录内。请保留 EXE 旁的 `_internal`、运行库和其它随包文件，不要只移动 EXE。

可在程序目录打开终端，运行以下命令取得诊断输出：

```powershell
.\GradeMirror-CLI.exe doctor --json
```

提交问题时附版本、版型、Windows build及诊断信息，分享前移除个人路径或私有内容。不要自行下载来源不明的DLL替换随包文件。

程序目录内的“开始使用”和许可材料随包提供。[发布说明](RELEASE-v0.1.0.md)列出四个附件的SHA-256；[源码与第三方材料](THIRD-PARTY.md)供开发者和再分发者取得对应源码。
