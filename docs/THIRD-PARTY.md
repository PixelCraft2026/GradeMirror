# 许可、第三方源码与通知

简体中文 · [English](THIRD-PARTY.en.md)

GradeMirror自有代码及本仓库新增说明采用 [AGPL-3.0-only](../LICENSE)。第三方组件各按其许可，品牌素材的范围另见冻结工程的 [SOURCE-LICENSE-SCOPE.json](../engineering/SOURCE-LICENSE-SCOPE.json)。该清单绑定原工程文件，不是整个二进制便携包的统一许可声明。

## 随便携版提供

相应组件的原许可、版权通知和接收者约定位于程序目录原有的许可文件及新增的 `release-info/`。两版均提供Microsoft约定，GPU版另提供NVIDIA约定；这些约定不额外限制自有AGPL代码的权利。

- [Microsoft组件约定](MSVC-RECIPIENT-TERMS.md)
- [NVIDIA组件约定](NVIDIA-RECIPIENT-TERMS.md)

## 同版完整材料

下载 [GradeMirror-0.1.0-Source-and-ThirdParty.zip](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Source-and-ThirdParty.zip)，可取得：

- 本版 533 项完整工程源码 ZIP，位于 `engineering/`。
- Qt、ExifTool及其它已随版提供的对应源码、许可和通知；精确成员见 `third-party/ATTACHMENTS.json`。
- Qt库修改与独立构建指引，见 `third-party/USER-QT-BUILD.md`。
- Microsoft、Intel及NVIDIA的原许可和随版分发说明。
- 本版构建清单、便携 ZIP 成员映射与材料校验和。

完整第三方源码归档集中在此Release附件中，没有重复塞进Git仓库。GitHub自动生成的Source code (zip/tar.gz)只是仓库快照，不包含这份材料包的全部内容。

官方软件下载免费；AGPL允许收费及商业使用，并要求遵守其源码交付等条件。任何另行商定的商业授权仅能覆盖项目拥有相应权利的部分，不替代第三方条款。保留通知，不以厂商或项目名称暗示未经提供的背书。

[构建入口](BUILD.md) · [返回中文首页](../README.zh-CN.md)
