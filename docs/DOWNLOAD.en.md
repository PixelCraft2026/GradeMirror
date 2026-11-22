# Download and run the portable edition

English · [简体中文](DOWNLOAD.md)

[English README](../README.md) · [GitHub Release](https://github.com/PixelCraft2026/GradeMirror/releases/tag/v0.1.0)

This release supports Windows x64. Choose either the CPU edition or the GPU edition and extract it to its own directory.

## CPU edition · One ZIP

1. Read the [Microsoft component terms](MSVC-RECIPIENT-TERMS.en.md).
2. Download the [CPU ZIP](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Windows-CPU.zip).
3. Right-click the ZIP and choose **Extract All**. Open the extracted `GradeMirror-CPU` folder and double-click `GradeMirror.exe`. Run the app from the extracted folder.

## NVIDIA GPU edition · Two ZIPs

1. Read the [Microsoft component terms](MSVC-RECIPIENT-TERMS.en.md) and [NVIDIA component terms](NVIDIA-RECIPIENT-TERMS.en.md).
2. Download both [GPU part 1](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Windows-GPU-1.zip) and [GPU part 2](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Windows-GPU-2.zip). The first archive needs the second.
3. Choose **Extract All** for each archive. Replace the destination Windows suggests based on the ZIP name and enter the **same destination** both times. The contents of both archives should go into one `GradeMirror-GPU` folder under that destination.
4. After both extractions finish, open the shared `GradeMirror-GPU` folder and double-click `GradeMirror.exe`.

Both downloads are ordinary ZIP archives. Windows can extract them without a merge script. Python, CUDA Toolkit, and Visual Studio require no separate installation. The GPU edition requires a supported NVIDIA GPU and a compatible driver.

## If GradeMirror does not start

Check that extraction completed. For the GPU edition, confirm that both archives were extracted into the same location. Keep `_internal`, the runtime libraries, and the other bundled files beside the EXE. If you move the app, move the whole folder.

To collect diagnostic output, open a terminal in the application directory and run this command.

```powershell
.\GradeMirror-CLI.exe doctor --json
```

When reporting an issue, include the version, edition, Windows build, and diagnostics. Remove personal paths and private information before sharing the output. Keep the bundled DLLs and avoid supplying replacements from unknown sources.

The application folder includes a getting-started guide and license materials. See the [release notes](RELEASE-v0.1.0.en.md) for the SHA-256 hashes of all four downloads. Developers and redistributors can obtain corresponding source through the [source and third-party materials guide](THIRD-PARTY.en.md).
