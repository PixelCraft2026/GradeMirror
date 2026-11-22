# GradeMirror 0.1.0-preview.50 工程构建说明

简体中文 · [English original](../../engineering/BUILD.txt)

将整个工程源码包解压到一个干净且可写的目录。请使用新的目录构建。

分别用随附的 `source-fetch.py` 和 `dependency-lock.json`、`exiftool-lock.json`、`python-toolchain-lock.json` 获取固定版本的输入，并使用新的缓存目录。已有缓存时，对每条命令加上 `--verify-only`，可核对锁定的文件字节，无需重新下载。

将固定版本的 Astral Windows x64 CPython 3.12.14 归档解压到新的工具链目录。用解压得到的 `python/python.exe` 执行以下命令。

```text
python.exe -I -S -B build-portable.py <offline-cache> <Windows-dir>
```

在 PATH 中保留 Windows 及指定工具链目录。清除 PYTHONPATH 和 PYTHONHOME。给 TEMP、TMP 和 PYINSTALLER_CONFIG_DIR 分别设置位于现有安装之外的专用可写路径。

入口会构建 CPU 和 GPU 两个 onedir 版，校验精确的数值及 I/O 清单，输入与锁定值不符时停止。源码包包含全部产品源码、运行时数据、锁文件和构建控制文件。

构建无需此前的产品目录、开发者工作区路径、历史测试素材或已保存的用户工程。第三方源码及通知另见随附的材料索引。
