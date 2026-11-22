# Third-party notices

SPA QuickLook bundles or depends on the following projects. Their licenses
apply to their respective files; the project's own code is MIT (see LICENSE).

| Component | License | Use |
|---|---|---|
| [RapidOCR](https://github.com/RapidAI/RapidOCR) | Apache-2.0 | OCR inference wrapper |
| PP-OCR models (PaddleOCR) | Apache-2.0 | Chinese text detection/recognition models bundled with RapidOCR |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | MIT | OCR inference runtime |
| [OpenCV](https://github.com/opencv/opencv) | Apache-2.0 | Image decoding / drawing |
| [NumPy](https://numpy.org/) | BSD-3-Clause | Array math |
| [Pillow](https://python-pillow.org/) | HPND | Image helpers used by OCR dependencies |
| [Python](https://www.python.org/) | PSF-2.0 | Runtime bundled by PyInstaller |
| [.NET 8](https://dotnet.microsoft.com/) | MIT | Runtime bundled in the self-contained `wincap.exe` |
| Windows SDK WinRT projections | Microsoft | WGC interop in `capture/` |

The capture helper's WGC implementation is original code in this repository.
The recognition grid/colour approach was informed by the Apache-2.0
[StrongholdAssistant](https://github.com/LBEILC/StrongholdAssistant) project;
no source files from it are vendored.

Game names, operator names and game data are property of Shanghai Hypergryph
Network Technology Co., Ltd. This is an unofficial, non-commercial fan tool.
No game artwork or screenshots are distributed in the release zip.
