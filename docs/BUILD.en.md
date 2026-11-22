# Build from the frozen engineering source kit

English · [简体中文](BUILD.md)

For normal use, download a [portable edition](DOWNLOAD.en.md). This guide is for developers building or modifying the source.

## Engineering source

The [`engineering/` directory](../engineering/) contains the 533 files of this release's engineering source kit. The internal version is `0.1.0-preview.50`, with this build identity.

```text
936c1c497980009489a7de4dbda024589a2519247b7ae4788e98aa3b1aa9d7fa
```

The first public release uses the `v0.1.0` tag. See [BUILD.txt](../engineering/BUILD.txt) and the lockfiles alongside it for the build procedure.

## Prepare and run the build

1. Copy the entire `engineering/` directory to a new writable build directory. Give the build fresh temporary directories.
2. Use the bundled `source-fetch.py` separately with `dependency-lock.json`, `exiftool-lock.json`, and `python-toolchain-lock.json` to obtain the pinned inputs. Use a dedicated download cache. Use `--verify-only` to check an existing cache. Read the applicable third-party terms before obtaining these inputs.
3. Extract the pinned Astral Windows x64 CPython 3.12.14 toolchain. Follow BUILD.txt to set up dedicated temporary directories, PATH, and the build environment, and clear PYTHONPATH and PYTHONHOME.
4. From the new engineering directory, run the pinned toolchain's `python.exe`.

   ```text
   python.exe -I -S -B build-portable.py <offline-cache> <Windows-dir>
   ```

The entry point builds both CPU and GPU onedir editions and checks the identities of its inputs. If the cache is incomplete, obtain the missing pinned inputs and run with the checks enabled. The build needs no developer worktree, historical test directories, or existing user projects. The division into four ZIP attachments is a release packaging choice. Both editions use the same algorithm version.

## Third-party source and Qt modifications

Download the matching [Source-and-ThirdParty.zip](https://github.com/PixelCraft2026/GradeMirror/releases/download/v0.1.0/GradeMirror-0.1.0-Source-and-ThirdParty.zip). It includes `third-party/USER-QT-BUILD.md`, `Prepare-User-Qt-Build.py`, the pinned Qt source, and notices.

The helper's first argument is the complete engineering ZIP in the material bundle's `engineering/` directory. Follow the bundled guide to build and substitute your modified libraries in a separate directory.

Keep the applicable licenses, notices, and source delivery materials with the build outputs. Test the functions affected by your changes. The official build's test results apply to that build. [Third-party source and notices](THIRD-PARTY.en.md)
