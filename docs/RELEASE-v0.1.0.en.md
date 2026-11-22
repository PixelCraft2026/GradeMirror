# GradeMirror 0.1.0 — First preview release

English · [简体中文](RELEASE-v0.1.0.md)

GradeMirror matches a source image to the colors of a reference image on your Windows x64 PC. This first preview supports JPEG, PNG, TIFF, WebP and BMP. You can preview a match, save the project and export the result.

### What’s in build `0.1.0-preview.50`

- Open **Help** to check device and runtime status (**Ctrl+Alt+R**) or locate a missing original image. During matching, the status bar shows the current stage and item counts as progress arrives. A remaining-time range appears once enough timing data is available.
- Drag images from Windows Explorer into either edition. GradeMirror receives their file paths and uses its bundled decoders for previews. Both `GradeMirror.exe` and `GradeMirror-CLI.exe` display the GradeMirror twin-mirror icon in Explorer.
- Both editions can clean up old CPU worker sessions once GradeMirror confirms those sessions have ended. Existing projects and recipes remain usable after upgrading.

### Download and run

Read the recipient terms before downloading. Both editions include Microsoft runtime components. The GPU edition also includes NVIDIA components.

<details>
<summary>Microsoft and NVIDIA recipient terms</summary>

#### Microsoft object code

These terms apply to the Microsoft runtime components listed in `third-party/msvc/MSVC-FILE-MAP.json` in the source and third-party package. To distribute or use those components, accept and observe these terms and the applicable rights, restrictions and protections in the original license. This grant does not license the Visual Studio development tools.

- You may use the bundled runtime components with GradeMirror. Keep all copyright and other rights notices. Microsoft and its licensors reserve all rights they have not expressly granted.
- If you redistribute this object code, distribute it unchanged as part of the same application, include these terms and the applicable license, and require downstream distributors and end users to accept terms at least as protective as the original license. This grant gives no independent right to sell, rent, supply or recombine Microsoft software.
- Keep rights notices intact and visible. Do not circumvent technical restrictions, reverse engineer, decompile or disassemble the components, except to the extent expressly permitted by applicable law or an applicable third-party license. Do not use Microsoft trademarks to suggest that Microsoft publishes or endorses GradeMirror.
- Do not represent Microsoft object code as AGPL-licensed or modify or distribute its source in a way that subjects it to source disclosure or modification requirements excluded by the original license. Do not redistribute unauthorized prerelease or debug runtimes.
- Observe applicable law and export restrictions. Microsoft components are supplied as is, subject to the original license’s warranty disclaimers and liability limits. The original license and mandatory provisions of applicable law take precedence.

GradeMirror’s own code is licensed under AGPL-3.0-only. Your rights to use, obtain, study, modify and redistribute that code continue under that license. Other third-party components retain their respective licenses. The source and third-party package includes the distribution notes in `third-party/msvc/MSVC-DISTRIBUTION.md` and the original license in `third-party/msvc/notices/Visual-Studio-2022-Community-License-EN.txt`. The applicable original text and the list of covered components accompany this release.

#### NVIDIA object code

These terms apply to the CUDA, NVTX and cuDNN binaries listed in `third-party/NVIDIA-DISTRIBUTION.md` in the source and third-party package. Read and accept the applicable bundled agreements and the terms below before using these components with GradeMirror within the scope of those agreements.

- Use the bundled components only through GradeMirror’s GPU workers on a supported NVIDIA GPU system. Retain the vendor’s copyright, license and intellectual property notices. Observe the original agreements’ restrictions on copying, modification, reverse engineering, standalone distribution and export, subject to rights expressly preserved by applicable law.
- Redistribution of the complete GPU edition requires an applicable redistribution grant. Include the matching notices and recipient terms with the application, and ensure that downstream distribution and use comply with the applicable original agreements.
- NVIDIA and its licensors reserve all rights they have not expressly granted. The original agreements and applicable law govern use, support, liability and remedies. Separately licensed components follow their respective licenses.
- GradeMirror’s own source remains available under AGPL-3.0-only. See `LICENSE` in the source and third-party package. Other third-party components retain their respective licenses.

</details>

- **CPU edition** — [Download the CPU ZIP](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Windows-CPU.zip), choose **Extract All**, open `GradeMirror-CPU` and double-click `GradeMirror.exe`.
- **NVIDIA GPU edition** — Download both [GPU part 1](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Windows-GPU-1.zip) and [GPU part 2](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Windows-GPU-2.zip). Choose **Extract All** for each ZIP. Windows suggests a different destination for each archive. Manually select the **same destination** both times so the contents go into a single `GradeMirror-GPU` folder. When both extractions finish, open that folder and double-click `GradeMirror.exe`.

Both editions run from the extracted folder, which also contains the getting-started guide and license notices. The GPU edition requires a supported NVIDIA GPU and compatible driver. No separate installation of Python, the CUDA Toolkit, Visual Studio or an extraction tool is needed.

### Tested configurations and known limitations

- Portable startup was verified on Windows 10 22H2 x64. On Windows 11 build 26200, both editions completed Fast color matching on synthetic TIFF/PNG images using the CPU backend. Recovery of stale worker records from preview.37 was also verified on that build. Native GPU processing on Windows 11 and operation on other clean offline machines remain unverified.
- The Qt PDF image plugin is omitted. GUI cancellation during active processing remains unverified.
- If an export fails, a `.gm004a-…partial` file may remain beside the destination. After GradeMirror and its exporter have exited, check the destination, request key and `intent.json` to identify the file. Keep it if its origin is unclear.
- Use trusted images. Malformed TIFF/EXIF data can exhaust memory in the bundled version of Pillow. [Pillow advisory](https://github.com/python-pillow/Pillow/security/advisories/GHSA-wqgw-9xfh-qf2g)

### Source and third-party materials

The [source and third-party ZIP](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Source-and-ThirdParty.zip) contains the complete project source archive, corresponding third-party source, instructions for building with a replacement Qt, original license notices and file inventories. GradeMirror’s own code is licensed under AGPL-3.0-only. Third-party components retain their respective licenses. GitHub also generates a separate snapshot of the tagged repository.

When reporting a problem, include the edition, GradeMirror version and Windows build number. A small synthetic sample helps others reproduce the issue. [Report an issue](https://github.com/PixelCraft2026/GradeMirror/issues)

<details>
<summary>SHA-256 for all four downloads</summary>

```text
41a4584261f9495bcf2b03645a96e37bb7f3d545ceee23febfa6bfdd61bae5b5  GradeMirror-0.1.0-Windows-CPU.zip
0cd3ad0efd503247f5504ec2bcab3e7b2a42d9cd2ac978baf0f2eae3c8afc6ae  GradeMirror-0.1.0-Windows-GPU-1.zip
d68359c78f44fe9bcb8c6ca7f747c2f3707b803e57f6492fa9eb1b48c254d3c6  GradeMirror-0.1.0-Windows-GPU-2.zip
06107d52e17d52b7572d76d6daffcc8b61c3ae0fb45dd6f2bcd436d9f83c4949  GradeMirror-0.1.0-Source-and-ThirdParty.zip
```

</details>
