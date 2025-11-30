# 手机照片视频压缩工具（HEIC/H.265）

解决手机图片视频占用太多的问题。

将原始目录中的图片转换为 HEIC，视频转换为 H.265 (HEVC)，并将输出文件的修改时间设为源文件的修改时间，同时精确复制元数据。

## 功能
- 图片 -> HEIC（优先使用 `magick`）
- 视频 -> H.265/HEVC（`ffmpeg`）
- 精确复制元数据：
  - 图片使用 `exiftool` 复制 EXIF 元数据
  - 视频使用 `ffmpeg -map_metadata` 精确保留所有元数据（包括 GPS 坐标、设备信息等）
- 输出文件修改时间与源文件一致
- 保持目录结构、可选择覆盖已有文件、可选择复制其他文件
- 跳过转换模式 - 仅更新已存在文件的时间和元数据，不进行格式转换

## 前置依赖
- Windows：请安装并将以下工具加入 PATH：
  - ffmpeg（https://ffmpeg.org/ 或通过 `choco install ffmpeg`）
  - exiftool（https://exiftool.org/ 或通过 `choco install exiftool`）
  - ImageMagick（`magick`，用于优先 HEIC 编码：`choco install imagemagick`，需带 HEIF 支持）

## 使用

```cmd
cd c:\Users\User\Documents\VSCode\新建文件夹

:: 交互式输入（推荐）：不带参数运行
python "Photo & Video Efficient Codec Converter.py"

:: 实际转换，覆盖已存在的输出文件
python "Photo & Video Efficient Codec Converter.py" "C:\path\to\input" "C:\path\to\output" --overwrite

:: 跳过转换，仅更新已存在文件的时间和元数据（需配合 --overwrite 使用）
python "Photo & Video Efficient Codec Converter.py" "C:\path\to\input" "C:\path\to\output" --skip-convert --overwrite

:: 调整质量参数
python "Photo & Video Efficient Codec Converter.py" "C:\path\to\input" "C:\path\to\output" --image-crf 28 --video-crf 22 --video-preset slow

:: 复制非媒体文件
python "Photo & Video Efficient Codec Converter.py" "C:\path\to\input" "C:\path\to\output" --copy-others
```

## 路径与依赖提示
- 路径可使用引号包裹以兼容空格，例如：`"C:\Users\User\Pictures Folder"`。脚本会自动去除首尾引号。
- 脚本启动时会先检查 `ffmpeg` 是否已安装并在 `PATH` 中；未安装会直接报错退出。
- 如未安装 `exiftool`，会跳过 EXIF 元数据迁移，但其他转换流程仍可运行。

## HEIC 支持说明与优先级
- 优先顺序：`magick` (ImageMagick) > `heif-enc` (libheif) > `ffmpeg`（需支持 heic muxer）。
- 原因：ImageMagick 与 heif-enc 通常对单张图片 HEIC 编码更直接，失败回退到 ffmpeg。
- 若均不可用或 ffmpeg 构建不支持 heic，将提示无法输出 HEIC。

### Windows 安装建议
- ffmpeg：`choco install ffmpeg` 或从官方网站下载并将 `ffmpeg.exe` 加入 PATH。
- exiftool：`choco install exiftool` 或从官方网站下载并将 `exiftool.exe` 加入 PATH。
- libheif（heif-enc）：可从第三方预编译包或通过 MSYS2/Chocolatey（如有）安装，并将 `heif-enc.exe` 加入 PATH。
- ImageMagick：`choco install imagemagick`（请选带 HEIF 支持的版本），确保 `magick.exe` 在 PATH。

## 环境变量（可选）
- `FFMPEG_PATH`：指定 ffmpeg 可执行文件路径，例如：`C:\ffmpeg\bin\ffmpeg.exe`
- `MAGICK_PATH`：指定 ImageMagick 可执行文件路径，例如：`C:\Program Files\ImageMagick-7.1.1-Q16-HDRI\magick.exe`
- `EXIFTOOL_PATH`：指定 exiftool 可执行文件路径，例如：`C:\exiftool\exiftool.exe`

脚本在找不到可执行时会进入交互式循环，提示输入路径；对于 `exiftool` 和 `magick`，也可选择回车跳过或使用回退方案。

## 说明
- **HEIC 编码**：优先使用 ImageMagick (`magick`)，回退到 `heif-enc` 或 `ffmpeg -c:v hevc -f heic`，使用 `-crf` 控制质量（数值越低质量越好，体积越大）。
- **H.265 编码**：使用 `ffmpeg` 的 `libx265`，默认 `-crf 23 -preset medium`，可按需求调整。转换前会自动检测视频是否已是 H.265 编码，如是则跳过转换。
- **元数据迁移**：
  - **图片**：通过 `exiftool -TagsFromFile` 复制 EXIF 元数据。若使用 ImageMagick（`magick`）进行 HEIC 编码，会自动迁移元数据，无需再调用 `exiftool`。
  - **视频**：使用 `ffmpeg -map_metadata` 在转换时直接复制所有元数据，确保 GPS 坐标、设备信息等精确保留，不会出现精度丢失或格式变化。
- **跳过转换模式** (`--skip-convert`)：
  - 不进行格式转换，仅更新目标目录中已存在文件的时间和元数据
  - 需配合 `--overwrite` 使用才会更新；若 `overwrite=否`，则完全跳过不做任何操作
  - 适用场景：已完成转换，仅需批量修正时间戳或元数据
- **修改时间**：通过 Python 的 `os.utime` 将输出文件的修改时间设为源文件的修改时间。

## 注意
- 原始 RAW 格式图片（如 `.nef`, `.cr2`）的转换支持取决于 `ffmpeg` 的解码能力；如遇到不支持的格式，请先转为常见格式再处理。
- 某些容器格式视频在转换为 H.265 后会保持原扩展（如 `.mp4`/`.mkv`/`.mov`），其他扩展默认输出为 `.mp4`。
- 如需保留音频编码不变，当前脚本已默认 `-c:a copy`；如不兼容，可改为 `-c:a aac`。
