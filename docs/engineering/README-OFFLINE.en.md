# GradeMirror preview.50 · Windows x64 portable edition

English · [简体中文原文](../../engineering/source/README-%E7%A6%BB%E7%BA%BF.md)

Extract the whole CPU ZIP, or extract both GPU ZIPs separately to the same destination. Open the application folder and run `GradeMirror.exe`.

Run `GradeMirror-CLI.exe doctor --json` to see the actual version, worker roles, and enumerated devices.

Working data goes to `%LOCALAPPDATA%/GradeMirror/LightPreview`. Keep the original photos and their projects. Device enumeration and the checks of worker roles before model execution cover startup. Actual processing remains subject to resource checks.

The **Help** menu shows device and runtime status and lets you locate a missing original image. After a job starts, the status bar displays stage and progress updates as they arrive. It shows a time estimate once it has enough data.

Consult the matching [engineering source and materials](../THIRD-PARTY.en.md) for build and license information. The applicable materials retain the upstream attributions and third-party terms. PixelCraft2026 is credited for the GradeMirror-owned portions.
