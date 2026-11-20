# 从冻结工程源码构建

普通使用请直接下载[便携版](DOWNLOAD.md)。本页面向构建或修改源码的开发者。

## 工程位置

本仓库 [`engineering/`](../engineering/BUILD.txt) 包含本版工程源包的 533 项文件。内部版本为 `0.1.0-preview.50`，构建身份如下。

```text
936c1c497980009489a7de4dbda024589a2519247b7ae4788e98aa3b1aa9d7fa
```

首次公开版本使用标签 `v0.1.0`。构建流程见 [BUILD.txt](../engineering/BUILD.txt) 及同目录锁文件。

## 构建准备与入口

1. 将整个 `engineering/` 复制到新的可写构建目录。不要在旧构建结果上直接复用临时目录。
2. 使用工程内 `source-fetch.py`，分别按 `dependency-lock.json`、`exiftool-lock.json`、`python-toolchain-lock.json` 取得固定输入；为下载缓存使用独立目录。已有缓存可使用 `--verify-only` 只核对。取件前阅读相应第三方条款。
3. 解压锁定的 Astral Windows x64 CPython 3.12.14 工具链，按 BUILD.txt 设置独立的临时目录、PATH及构建环境，清除 PYTHONPATH/PYTHONHOME。
4. 从新的工程目录，用指定工具链中的 `python.exe` 执行：

   ```text
   python.exe -I -S -B build-portable.py <offline-cache> <Windows-dir>
   ```

入口会构建CPU/GPU两个onedir版本，检查输入身份；缓存不完整时应补齐锁定输入，而不是跳过核验。无需原开发者的工作树、历史测试目录或用户工程。最终四附件ZIP的分配方式是发布包装，不是算法版本差异。

## 第三方源码和修改 Qt

取得同版 [Source-and-ThirdParty.zip](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Source-and-ThirdParty.zip)。其中包含 `third-party/USER-QT-BUILD.md`、`Prepare-User-Qt-Build.py`、固定Qt源码及通知。

该 helper 的第一个参数是材料包 `engineering/` 内的完整工程 ZIP。按随包指引在独立目录构建和替换自己修改的库。

构建输出须保留相应许可、通知和源码交付材料。修改后的版本不自动继承官方构建的验证结果；请根据实际改动验证受影响功能。[第三方材料说明](THIRD-PARTY.md)
