# Known limitations in the 0.1.0 preview

English · [简体中文](KNOWN-ISSUES.md)

The internal build is `0.1.0-preview.50`. On Windows 11 build 26200, both editions passed a small-image Fast test on CPU, and cleanup of stale sessions from preview.37 was verified. Native GPU processing on Windows 11 and operation on other clean offline machines remain unverified.

- **Files left after a write failure.** A `.gm004a-…partial` file may remain beside the destination. After GradeMirror and its export process exit, confirm what created the file using the destination, request key, and `intent.json`. Keep files of uncertain origin and avoid bulk deletion.
- **Trusted inputs.** Certain TIFF/EXIF inputs can exhaust memory in the bundled Pillow version. Process images from trusted sources. [Pillow security advisory](https://github.com/python-pillow/Pillow/security/advisories/GHSA-wqgw-9xfh-qf2g)
- **Cancellation.** GUI cancellation during active processing remains unverified.
- **PDF.** This preview omits the Qt PDF image plugin.

The release packaging uses the frozen algorithm and runtime libraries. Validation applies to the tested hardware, drivers, file variants, and failure scenarios. When reporting a problem, include the edition, internal version, Windows build, and steps to reproduce it. Prefer a small sample that you can share.

[Back to the download guide](DOWNLOAD.en.md) · [Report an issue](https://github.com/PixelCraft2026/GradeMirror/issues)
