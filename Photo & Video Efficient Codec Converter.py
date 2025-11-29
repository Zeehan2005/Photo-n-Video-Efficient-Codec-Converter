import argparse
import os
import sys
import subprocess
import shutil
import tempfile
from pathlib import Path
from datetime import datetime

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".jp2", ".nef", ".cr2", ".arw", ".raf"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".wmv", ".mts", ".m2ts", ".flv", ".webm"}


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def run_cmd(cmd: list[str]) -> int:
    try:
        # 不捕获输出，让 ffmpeg 进度条直接显示
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            print(f"[CMD-ERR] Command failed with code {proc.returncode}: {' '.join(cmd)}", file=sys.stderr)
        return proc.returncode
    except FileNotFoundError:
        print(f"[NOT FOUND] Command not found: {cmd[0]}", file=sys.stderr)
        return 127


def cmd_output(cmd: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"Command not found: {cmd[0]}"


def ensure_tool_available(name: str, fallback_path: str | None = None) -> bool:
    if fallback_path:
        try:
            return Path(fallback_path).exists()
        except Exception:
            return False
    return shutil.which(name) is not None


def copy_exif(source: Path, target: Path) -> bool:
    # Use exiftool to copy all tags and set FileModifyDate from source.
    # Two-step to avoid edge cases: copy tags, then sync FileModifyDate.
    # 支持自定义 exiftool 路径（在 args 中或环境变量）
    exiftool_bin = os.environ.get("EXIFTOOL_PATH", "exiftool")
    if not ensure_tool_available("exiftool", exiftool_bin if exiftool_bin != "exiftool" else None):
        print("[WARN] exiftool not found in PATH; skipping EXIF copy.")
        return False
    copy_cmd = [
        exiftool_bin,
        "-overwrite_original",
        "-P",  # preserve file times of target when possible
        f"-TagsFromFile={str(source)}",
        str(target)
    ]
    code1 = run_cmd(copy_cmd)
    sync_cmd = [
        exiftool_bin,
        "-overwrite_original",
        f"-FileModifyDate<={str(source)}",
        str(target)
    ]
    code2 = run_cmd(sync_cmd)
    return code1 == 0 and code2 == 0


def set_mtime_like_source(source: Path, target: Path) -> None:
    try:
        stat = source.stat()
        os.utime(target, (stat.st_atime, stat.st_mtime))
    except Exception as e:
        print(f"[WARN] Failed to set mtime: {target} <- {source} ({e})")


def ffmpeg_supports_heic(ffmpeg_bin: str) -> bool:
    code, out, err = cmd_output([ffmpeg_bin, "-hide_banner", "-muxers"])
    if code != 0:
        return False
    return "heic" in out.lower()


def convert_image_to_heic(src: Path, dst: Path, quality: int = 30, preset: str = "medium", ffmpeg_bin: str = "ffmpeg", magick_bin: str = "magick") -> tuple[bool, str]:
    # 优先使用指定或默认的 magick_bin -> heif-enc -> ffmpeg
    if ensure_tool_available("magick", magick_bin if magick_bin != "magick" else None):
        q = max(10, min(95, 70 - (quality - 18)))
        cmd = [
            magick_bin,
            str(src),
            "-quality", str(q),
            "-define", "heic:speed=5",
            str(dst)
        ]
        ok = run_cmd(cmd) == 0
        return ok, "magick"
    if ensure_tool_available("heif-enc"):
        q = max(10, min(95, 70 - (quality - 18)))
        cmd = [
            "heif-enc",
            "-q", str(q),
            str(src),
            "-o", str(dst)
        ]
        ok = run_cmd(cmd) == 0
        return ok, "heif-enc"
    if ffmpeg_supports_heic(ffmpeg_bin):
        cmd = [
            ffmpeg_bin, "-y",
            "-i", str(src),
            "-vf", "format=yuv420p",
            "-c:v", "libx265",
            "-preset", preset,
            "-crf", str(quality),
            "-tag:v", "hvc1",
            "-f", "heic",
            str(dst)
        ]
        ok = run_cmd(cmd) == 0
        return ok, "ffmpeg"
    print("[ERR] 无法输出 HEIC：未检测到 magick / heif-enc，且 ffmpeg 不支持 heic muxer。")
    return False, "none"


def is_video_h265(src: Path, ffmpeg_bin: str = "ffmpeg") -> bool:
    """检测视频是否已经是 H.265/HEVC 编码"""
    probe_cmd = [ffmpeg_bin, "-i", str(src)]
    code, _, err = cmd_output(probe_cmd)
    # 查找视频流编码信息
    for line in err.split('\n'):
        if 'Video:' in line:
            # 检查是否包含 hevc 或 h265 关键词
            line_lower = line.lower()
            if 'hevc' in line_lower or 'h265' in line_lower:
                return True
    return False


def convert_video_to_h265(src: Path, dst: Path, crf: int = 23, preset: str = "medium", ffmpeg_bin: str = "ffmpeg", skip_h265: bool = True) -> bool:
    # 检测是否已经是 H.265
    if is_video_h265(src, ffmpeg_bin):
        if skip_h265:
            print(f"[INFO] 视频已是 H.265 编码，跳过转换: {src.name}")
            # 直接复制文件
            try:
                shutil.copy2(src, dst)
                return True
            except Exception as e:
                print(f"[ERR] 复制失败: {e}")
                return False
        else:
            try:
                response = input(f"[?] 视频 {src.name} 已是 H.265，是否跳过？(Y/n，默认跳过): ").strip().lower()
                if not response or response.startswith('y'):
                    print(f"[INFO] 跳过 H.265 视频: {src.name}")
                    shutil.copy2(src, dst)
                    return True
            except EOFError:
                print(f"[INFO] 跳过 H.265 视频: {src.name}")
                shutil.copy2(src, dst)
                return True
    
    cmd = [
        ffmpeg_bin, "-y",
        "-progress", "pipe:1",  # 输出进度到 stdout
        "-i", str(src),
        "-c:v", "libx265",
        "-crf", str(crf),
        "-preset", preset,
        "-tag:v", "hvc1",
        "-c:a", "copy",
        str(dst)
    ]
    
    # 获取视频时长用于计算进度百分比
    duration_cmd = [ffmpeg_bin, "-i", str(src)]
    code, _, err = cmd_output(duration_cmd)
    duration_sec = None
    for line in err.split('\n'):
        if 'Duration:' in line:
            try:
                time_str = line.split('Duration:')[1].split(',')[0].strip()
                h, m, s = time_str.split(':')
                duration_sec = int(h) * 3600 + int(m) * 60 + float(s)
            except Exception:
                pass
            break
    
    # 运行 ffmpeg 并解析进度
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        current_time = 0.0
        for line in proc.stdout:
            line = line.strip()
            if line.startswith('out_time_ms='):
                try:
                    time_ms = int(line.split('=')[1])
                    current_time = time_ms / 1000000.0
                    if duration_sec and duration_sec > 0:
                        progress = min(100, (current_time / duration_sec) * 100)
                        bar_len = 40
                        filled = int(bar_len * progress / 100)
                        bar = '█' * filled + '░' * (bar_len - filled)
                        print(f'\r[进度] {bar} {progress:.1f}% ({current_time:.1f}/{duration_sec:.1f}s)', end='', flush=True)
                except Exception:
                    pass
        proc.wait()
        if proc.returncode == 0 and duration_sec:
            bar = '█' * 40
            print(f'\r[进度] {bar} 100.0% ({duration_sec:.1f}/{duration_sec:.1f}s)')
        elif proc.returncode == 0:
            print()
        return proc.returncode == 0
    except FileNotFoundError:
        print(f"[NOT FOUND] Command not found: {cmd[0]}", file=sys.stderr)
        return False


def should_skip(src: Path, dst: Path, overwrite: bool) -> bool:
    if not dst.exists():
        return False
    if overwrite:
        try:
            dst.unlink()
        except Exception:
            pass
        return False
    # If destination exists and newer or same size, skip
    try:
        s_stat = src.stat()
        d_stat = dst.stat()
        if d_stat.st_mtime >= s_stat.st_mtime and d_stat.st_size > 0:
            return True
    except Exception:
        return False
    return False


def process_file(src: Path, in_root: Path, out_root: Path, args) -> None:
    rel = src.relative_to(in_root)
    out_dir = out_root / rel.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    if is_image(src):
        out_ext = ".heic"
        dst = out_dir / (src.stem + out_ext)
        if should_skip(src, dst, args.overwrite):
            print(f"[SKIP] {rel} -> {dst.relative_to(out_root)}")
            return
        ffmpeg_bin = getattr(args, 'ffmpeg', 'ffmpeg')
        magick_bin = getattr(args, 'magick', 'magick')
        ok, backend = convert_image_to_heic(src, dst, quality=args.image_crf, preset=args.video_preset, ffmpeg_bin=ffmpeg_bin, magick_bin=magick_bin)
    elif is_video(src):
        # Normalize container to .mp4 for better compatibility
        out_ext = ".mp4"
        dst = out_dir / (src.stem + out_ext)
        if should_skip(src, dst, args.overwrite):
            print(f"[SKIP] {rel} -> {dst.relative_to(out_root)}")
            return
        ffmpeg_bin = getattr(args, 'ffmpeg', 'ffmpeg')
        ok = convert_video_to_h265(src, dst, crf=args.video_crf, preset=args.video_preset, ffmpeg_bin=ffmpeg_bin)
    else:
        # Copy non-media files as-is or skip
        if args.copy_others:
            dst = out_dir / src.name
            if should_skip(src, dst, args.overwrite):
                print(f"[SKIP] {rel} (copy)")
                return
            try:
                shutil.copy2(src, dst)
                print(f"[COPY] {rel} -> {dst.relative_to(out_root)}")
            except Exception as e:
                print(f"[ERR] copy failed: {src} -> {dst}: {e}")
            return
        else:
            return

    if ok:
        # 若使用 magick，则通常已迁移 EXIF；此时跳过 exiftool 拷贝
        if 'backend' in locals() and backend == 'magick':
            pass
        else:
            copy_exif(src, dst)
        set_mtime_like_source(src, dst)
        print(f"[OK] {rel} -> {dst.relative_to(out_root)}")
    else:
        # Clean up partials
        try:
            if dst.exists():
                dst.unlink()
        except Exception:
            pass
        print(f"[FAIL] {rel}")


def gather_files(in_root: Path) -> list[Path]:
    files: list[Path] = []
    for p in in_root.rglob('*'):
        if p.is_file():
            files.append(p)
    return files


def parse_args():
    parser = argparse.ArgumentParser(description="Convert images to HEIC and videos to H.265, preserve mtime, copy EXIF.")
    parser.add_argument("input", type=str, nargs="?", help="Input directory (source)")
    parser.add_argument("output", type=str, nargs="?", help="Output directory")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs")
    parser.add_argument("--image-crf", type=int, default=30, help="HEIC quality (CRF, lower is better quality)")
    parser.add_argument("--video-crf", type=int, default=23, help="H.265 CRF")
    parser.add_argument("--video-preset", type=str, default="medium", help="H.265 preset (ultrafast..veryslow)")
    parser.add_argument("--copy-others", action="store_true", help="Copy non-media files as-is")
    parser.add_argument("--dry-run", action="store_true", help="List planned operations without executing")
    return parser.parse_args()


def interactive_prompt(args):
    print("=== 交互模式：配置转换选项 ===")
    def normalize_path_input(v: str) -> str:
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        return v.strip()
    def prompt_dir(label: str, default: str | None = None) -> str:
        while True:
            v = input(f"{label}目录路径{f' (默认 {default})' if default else ''}: ")
            v = normalize_path_input(v)
            if not v and default:
                v = default
            p = Path(v)
            if p.exists() and p.is_dir():
                return str(p)
            else:
                print("路径不存在或不是目录，请重试。")

    def prompt_int(label: str, default: int) -> int:
        v = input(f"{label} (默认 {default}): ").strip()
        if not v:
            return default
        try:
            return int(v)
        except ValueError:
            print("请输入整数，已使用默认值。")
            return default

    def prompt_choice(label: str, choices: list[str], default: str) -> str:
        chs = ",".join(choices)
        v = input(f"{label} [{chs}] (默认 {default}): ").strip().lower()
        if not v:
            return default
        if v in choices:
            return v
        print("非法选项，已使用默认值。")
        return default

    def prompt_bool(label: str, default: bool) -> bool:
        v = input(f"{label} (y/n，默认 {'y' if default else 'n'}): ").strip().lower()
        if not v:
            return default
        return v.startswith('y')

    in_dir = prompt_dir("源输入")
    out_dir = prompt_dir("输出")
    image_crf = prompt_int("图片 CRF (质量，数字越低质量越好)", args.image_crf)
    video_crf = prompt_int("视频 CRF", args.video_crf)
    video_preset = prompt_choice("视频编码预设", ["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow"], args.video_preset)
    overwrite = prompt_bool("是否覆盖已存在的输出文件", args.overwrite)
    copy_others = prompt_bool("是否复制非媒体文件", args.copy_others)
    dry_run = prompt_bool("仅干跑（不执行）", args.dry_run)

    ffmpeg_default = os.environ.get("FFMPEG_PATH", "")
    ffmpeg_path_input = input(f"ffmpeg 可执行路径（留空使用 PATH；默认 {ffmpeg_default or 'PATH'}）：").strip()
    ffmpeg_path_input = ffmpeg_path_input or ffmpeg_default or ""

    exiftool_default = os.environ.get("EXIFTOOL_PATH", "")
    exiftool_path_input = input(f"exiftool 可执行路径（留空使用 PATH；默认 {exiftool_default or 'PATH'}）：").strip()
    exiftool_path_input = exiftool_path_input or exiftool_default or ""

    # Fill back to args
    args.input = in_dir
    args.output = out_dir
    args.image_crf = image_crf
    args.video_crf = video_crf
    args.video_preset = video_preset
    args.overwrite = overwrite
    args.copy_others = copy_others
    args.dry_run = dry_run
    args.ffmpeg = ffmpeg_path_input
    args.exiftool = exiftool_path_input
    return args


def main():
    args = parse_args()
    # 支持通过环境变量或交互提供 ffmpeg 路径
    ffmpeg_override = os.environ.get("FFMPEG_PATH", "")
    if args.input is None or args.output is None:
        # 将在交互模式中询问 ffmpeg 路径
        pass
    else:
        if ffmpeg_override:
            setattr(args, 'ffmpeg', ffmpeg_override)
    # If positional args missing, enter interactive mode
    if args.input is None or args.output is None:
        args = interactive_prompt(args)

    # 兼容带引号的路径
    def strip_quotes(s: str) -> str:
        s = s.strip()
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            s = s[1:-1]
        return s.strip()

    in_root = Path(strip_quotes(args.input)).resolve()
    out_root = Path(strip_quotes(args.output)).resolve()
    # 校验并循环询问 ffmpeg 直到找到
    ffmpeg_bin = (getattr(args, 'ffmpeg', '') or os.environ.get("FFMPEG_PATH", '') or "ffmpeg").strip()
    while not ensure_tool_available("ffmpeg", ffmpeg_bin if ffmpeg_bin != "ffmpeg" else None):
        print("[ERR] ffmpeg not found. 请安装 ffmpeg 或设置环境变量 FFMPEG_PATH，或在交互模式中提供可执行路径。")
        try:
            # 若是非交互模式（已有位置参数），也允许继续询问一次 ffmpeg 路径
            new_path = input("请提供 ffmpeg 可执行路径（如 C:\\ffmpeg\\bin\\ffmpeg.exe），或回车继续使用 PATH: ").strip()
        except EOFError:
            new_path = ""
        if new_path:
            ffmpeg_bin = new_path
        else:
            ffmpeg_bin = "ffmpeg"
        # 循环继续校验，直到可用
    # magick 路径与交互
    magick_bin = (getattr(args, 'magick', '') or os.environ.get("MAGICK_PATH", '') or "magick").strip()
    if not in_root.exists() or not in_root.is_dir():
        print(f"[ERR] Input directory not found: {in_root}")
        sys.exit(2)
    out_root.mkdir(parents=True, exist_ok=True)

    # 校验并循环询问 exiftool 直到找到（若用户需要 EXIF 复制）
    exiftool_bin = (getattr(args, 'exiftool', '') or os.environ.get("EXIFTOOL_PATH", '') or "exiftool").strip()
    if not ensure_tool_available("exiftool", exiftool_bin if exiftool_bin != "exiftool" else None):
        print("[WARN] exiftool 未找到。将尝试询问路径；若仍不可用，则跳过 EXIF 复制。")
        while True:
            try:
                new_exif = input("请提供 exiftool 可执行路径（如 C:\\exiftool\\exiftool.exe），或回车跳过 EXIF：").strip()
            except EOFError:
                new_exif = ""
            if not new_exif:
                print("[WARN] EXIF 复制将被跳过。")
                # 清空以便 copy_exif 判断不到时直接跳过
                args.exiftool = ""
                break
            elif ensure_tool_available("exiftool", new_exif):
                args.exiftool = new_exif
                exiftool_bin = new_exif
                break
            else:
                print("[ERR] 路径不可用，请重试。")

    files = gather_files(in_root)
    if args.dry_run:
        for f in files:
            rel = f.relative_to(in_root)
            if is_image(f):
                print(f"[PLAN] IMAGE: {rel} -> {Path(rel.parent) / (f.stem + '.heic')}")
            elif is_video(f):
                print(f"[PLAN] VIDEO: {rel} -> {Path(rel.parent) / (f.stem + '.mp4')}")
            elif args.copy_others:
                print(f"[PLAN] COPY: {rel}")
        return

    # 若存在图片且 magick 不可用，允许循环输入 magick 路径或直接使用回退
    has_images = any(is_image(f) for f in files)
    if has_images and not ensure_tool_available("magick", magick_bin if magick_bin != "magick" else None):
        print("[INFO] 未检测到可用的 ImageMagick (magick)。可提供路径获得更佳 HEIC 编码；直接回车使用 heif-enc 或 ffmpeg 回退。")
        while True:
            try:
                new_magick = input("请输入 magick 可执行路径（如 C:\\Program Files\\ImageMagick-7.1.1-Q16-HDRI\\magick.exe），或回车使用回退: ").strip()
            except EOFError:
                new_magick = ""
            if not new_magick:
                print("[INFO] 使用回退链：heif-enc -> ffmpeg。")
                break
            if ensure_tool_available("magick", new_magick):
                magick_bin = new_magick
                print(f"[OK] 使用提供的 magick 路径: {magick_bin}")
                break
            else:
                print("[ERR] 提供的路径不可用，请重试。")

    # 将路径保存在 args 供 process_file 内部使用
    args.ffmpeg = ffmpeg_bin
    args.magick = magick_bin

    # 修改 process_file 调用逻辑以传递路径（通过 args 内属性）
    for f in files:
        process_file(f, in_root, out_root, args)


if __name__ == "__main__":
    main()
