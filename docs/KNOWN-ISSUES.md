# 0.1.0 预览版已知限制

简体中文 · [English](KNOWN-ISSUES.en.md)

本版内部构建编号为 `0.1.0-preview.50`。Windows 11 build 26200 已验证两版的 CPU 快速小图流程及 preview.37 驻留回收。Win11 的原生 GPU 计算和其他干净离线主机仍待验证。

- **写入失败残留**：可能在目标旁留下 `.gm004a-…partial`。退出GradeMirror和导出进程后，可按目的地、请求键及 `intent.json` 核实归属；归属不明时保留，不要直接批量删除。
- **可信输入**：当前Pillow版本有TIFF/EXIF可能耗尽内存的已知风险，请处理可信图片。[已有安全公告](https://github.com/python-pillow/Pillow/security/advisories/GHSA-wqgw-9xfh-qf2g)
- **取消操作**：活跃处理中的GUI取消尚未验证。
- **PDF**：本版不包含Qt PDF图像插件。

本次发布包装没有更改冻结算法或运行库，不代表对所有硬件、驱动、文件格式变体或故障场景作出保证。反馈时请提供版型、内部版本、Windows build与复现步骤，优先使用可分享的小型样例。

[返回下载说明](DOWNLOAD.md) · [反馈问题](https://github.com/PixelCraft2026/GradeMirror/issues)
