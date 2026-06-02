import sys

sys.dont_write_bytecode = True

import argparse
import atexit
import errno
import glob
import json
import hashlib
import mimetypes
import os
import re
import shutil
import subprocess
import stat
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.parse
import urllib.request
import webbrowser
from math import ceil
from collections import Counter, defaultdict, OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic",
})
VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".flv",
})
AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav", ".opus", ".wma",
    ".aiff", ".aif", ".alac", ".ape", ".wv", ".ra", ".mid", ".midi",
    ".caf", ".amr", ".3ga",
})
PDF_EXTENSIONS = frozenset({".pdf"})
TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml",
    ".html", ".htm", ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".rb", ".go",
    ".rs", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".sh", ".zsh", ".bash",
    ".toml", ".ini", ".cfg", ".conf", ".log", ".sql", ".rtf",
})

OS_MACOS = "macos"
OS_WINDOWS = "windows"
OS_LINUX = "linux"
OS_OTHER = "other"
TEXT_PREVIEW_BYTES = 8192
TEXT_PREVIEW_CHARS = 4000
MAX_THUMBNAIL_CACHE_SIZE = 500
MAX_THUMBNAIL_CACHE_BYTES = 128 * 1024 * 1024
THUMBNAIL_FORMAT = "mjpeg"
THUMBNAIL_CONTENT_TYPE = "image/jpeg"
THUMBNAIL_QUALITY = 4
MIN_VIDEO_HOVER_THUMBNAILS = 4
MAX_VIDEO_HOVER_THUMBNAILS = 12
VIDEO_MULTI_THUMBNAIL_SECONDS = 15
FAST_HASH_NAME = "blake2b"
FULL_HASH_NAME = "blake2b"
MACOS_TRASH_CMD = shutil.which("trash")
FAST_SAMPLE_BYTES = 65536
MIN_FAST_SAMPLE_COUNT = 8
MAX_FAST_SAMPLE_COUNT = 64
SMALL_FILE_FULL_HASH_BYTES = 1048576  # 1MB — small files get full hash
FULL_HASH_CHUNK_BYTES = 1048576
RANGE_SERVE_CHUNK_BYTES = 65536
SUBPROCESS_SEMAPHORE = threading.Semaphore(4)
THUMBNAIL_SUBPROCESS_SEMAPHORE = threading.Semaphore(8)
DEFAULT_PAGINATION_LIMIT = 500
MAX_PAGINATION_LIMIT = 2000
DEFAULT_PROGRESS_EVERY = 5000
VERIFY_FULL = "full"
VERIFY_FAST = "fast"
DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".svn",
    "vendor", ".fseventsd", ".Spotlight-V100", ".Trashes", ".DocumentRevisions-V100",
    ".TemporaryItems", ".AppleDouble", "$RECYCLE.BIN",
    "System Volume Information", "Recovery", "Windows", "Program Files",
    "Program Files (x86)", "ProgramData", "AppData", "Local Settings",
    "Application Data", "All Users", "Default User", "Public", "Users",
    "Documents and Settings",
}
DEFAULT_IGNORE_FILES = {
    ".DS_Store", "Thumbs.db", ".localized", "Icon?", "desktop.ini", "pagefile.sys",
    "hiberfil.sys", "swapfile.sys", ".metadata_never_index", "autorun.inf", "boot.ini",
    "bootmgr", "BOOTNXT", "bootsect.bak", "ntldr", "ntuser.dat", "ntuser.dat.log",
    "ntuser.ini", "system.ini", "win.ini",
}
MACOS_BLOCKED_ROOTS = {"/System", "/Library", "/Applications", "/Users", "/Volumes"}
PHOTOS_LIBRARY_SUFFIXES = (".photoslibrary",)
FFMPEG_PATH = shutil.which("ffmpeg")
FFPROBE_PATH = shutil.which("ffprobe")
EXIFTOOL_PATH = shutil.which("exiftool")
COPY_PATTERN = re.compile(r"(\bcopy\b|\bduplicate\b|\s\(\d+\)$)", re.IGNORECASE)
# NAS server-side recycle folders, checked at the volume root.
# Synology DSM uses #recycle. QNAP uses @Recycle. Samba recycle often uses .recycle.
# Used only when one of these already exists on the volume — never created.
NAS_RECYCLE_DIR_NAMES = ("#recycle", "@Recycle", ".recycle")


def cleanup_own_bytecode():
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__")
    module_name = os.path.splitext(os.path.basename(__file__))[0]
    for bytecode_path in glob.glob(os.path.join(cache_dir, f"{module_name}*.pyc")):
        try:
            os.remove(bytecode_path)
        except OSError:
            pass
    try:
        os.rmdir(cache_dir)
    except OSError:
        pass


atexit.register(cleanup_own_bytecode)


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    mtime_ns: int
    st_dev: int = 0
    st_ino: int = 0


@dataclass(frozen=True)
class DuplicateGroup:
    hash: str
    files: tuple
    hash_name: str = FULL_HASH_NAME


@dataclass(frozen=True)
class ScanOptions:
    path: str
    real_path: str
    ignore_dirs: frozenset = field(default_factory=lambda: frozenset(DEFAULT_IGNORE_DIRS))
    ignore_files: frozenset = field(default_factory=lambda: frozenset(DEFAULT_IGNORE_FILES))
    ignore_file_suffixes: tuple = field(init=False, repr=False)
    include_hidden: bool = False
    allow_home_root: bool = False
    allow_photo_library: bool = False
    progress_every: int = DEFAULT_PROGRESS_EVERY
    verify_mode: str = VERIFY_FAST

    def __post_init__(self):
        object.__setattr__(
            self,
            "ignore_file_suffixes",
            tuple(item for item in self.ignore_files if item.startswith(".")),
        )


@dataclass
class ScanStats:
    scanned: int = 0
    ignored: int = 0
    unreadable: int = 0
    cloud_skipped: int = 0
    dirs_scanned: int = 0
    size_candidates: int = 0
    duplicate_groups: int = 0
    duplicate_files: int = 0
    unreadable_paths: list = field(default_factory=list)
    cloud_skipped_paths: list = field(default_factory=list)
    ignored_dir_counts: Counter = field(default_factory=Counter)
    ignored_hidden_count: int = 0

    def print_summary(self, result=None):
        print("\nSummary")
        print("-" * 60)
        print(f"Scanned files:     {self.scanned}")
        print(f"Scanned dirs:      {self.dirs_scanned}")
        print(f"Ignored entries:   {self.ignored}")
        if self.unreadable:
            print(f"WARNING: {self.unreadable} unreadable file(s) skipped — check file permissions.")
            for path in self.unreadable_paths[:10]:
                print(f"  unreadable: {path}")
            if self.unreadable > len(self.unreadable_paths[:10]):
                print(f"  ... and {self.unreadable - len(self.unreadable_paths[:10])} more")
        else:
            print(f"Unreadable entries: 0")
        if self.cloud_skipped:
            print(f"Cloud placeholders skipped: {self.cloud_skipped} (iCloud files not yet downloaded locally)")
            for path in self.cloud_skipped_paths[:10]:
                print(f"  cloud placeholder: {path}")
            if self.cloud_skipped > len(self.cloud_skipped_paths[:10]):
                print(f"  ... and {self.cloud_skipped - len(self.cloud_skipped_paths[:10])} more")
        if self.ignored_dir_counts:
            common_dirs = ", ".join(
                f"{name} ({count})"
                for name, count in self.ignored_dir_counts.most_common(8)
            )
            print(f"Ignored directories by name: {common_dirs}")
        if self.ignored_hidden_count:
            print(f"Hidden entries skipped: {self.ignored_hidden_count} (use --include-hidden to scan them)")
        print(f"Size candidates:   {self.size_candidates}")
        print(f"Duplicate groups:  {self.duplicate_groups}")
        print(f"Duplicate files:   {self.duplicate_files}")
        if result:
            print(f"Selected files:    {result.selected}")
            print(f"Skipped files:     {result.skipped}")
            if result.dry_run:
                print("Mode:              dry-run")
            else:
                print(f"Trashed files:     {result.trashed}")
                if result.permanently_deleted:
                    print(f"Permanently deleted: {result.permanently_deleted}")
                print(f"Trash errors:      {result.errors}")


@dataclass
class TrashResult:
    selected: int = 0
    trashed: int = 0
    permanently_deleted: int = 0
    skipped: int = 0
    errors: int = 0
    dry_run: bool = True


class VolumeHasNoTrashError(Exception):
    """Raised when a path cannot be moved through a recoverable trash route.

    The caller decides what to do: prompt for permanent deletion (default
    interactive behavior), permanently delete unconditionally
    (``--permanent-on-no-trash``), or copy to local ``~/.Trash``
    (``--allow-slow-local-trash``).
    """

    def __init__(self, path, volume_root, send_error, cmd_error):
        self.path = path
        self.volume_root = volume_root
        self.send_error = send_error
        self.cmd_error = cmd_error
        super().__init__(
            f"send2trash failed: {send_error}; /usr/bin/trash failed: {cmd_error}"
        )


class SymlinkReplacementError(OSError):
    """Raised when a validated path is replaced with a symlink before removal."""


def detect_os(platform=None, os_name=None):
    platform = platform if platform is not None else os.sys.platform
    os_name = os_name if os_name is not None else os.name
    if platform == "darwin":
        return OS_MACOS
    if os_name == "nt":
        return OS_WINDOWS
    if platform.startswith("linux"):
        return OS_LINUX
    return OS_OTHER


CURRENT_OS = detect_os()


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024**2:
        return f"{size_bytes / 1024:.2f} KB"
    if size_bytes < 1024**3:
        return f"{size_bytes / 1024**2:.2f} MB"
    return f"{size_bytes / 1024**3:.2f} GB"


def is_mpeg2_ts(path):
    # MPEG-2 TS packets are 188 bytes each, always starting with sync byte 0x47.
    # Two matching sync bytes rules out TypeScript source with very high confidence.
    try:
        with open(path, "rb") as f:
            header = f.read(377)
        return len(header) >= 189 and header[0] == 0x47 and header[188] == 0x47
    except OSError:
        return False


def get_media_kind(path):
    extension = os.path.splitext(path or "")[1].lower()
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension == ".ts" and is_mpeg2_ts(path):
        return "video"
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    return None


def is_probably_text_bytes(data):
    if b"\x00" in data:
        return False
    if not data:
        return True
    control_bytes = sum(1 for byte in data if byte < 32 and byte not in (9, 10, 13, 12))
    return control_bytes / len(data) < 0.05


def is_readable_text_file(path, size=None):
    if get_media_kind(path):
        return False
    extension = os.path.splitext(path or "")[1].lower()
    if extension in TEXT_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as file_obj:
            sample = file_obj.read(min(TEXT_PREVIEW_BYTES, size or TEXT_PREVIEW_BYTES))
    except OSError:
        return False
    return is_probably_text_bytes(sample)


def read_text_preview(path, limit=TEXT_PREVIEW_BYTES):
    try:
        with open(path, "rb") as file_obj:
            data = file_obj.read(limit)
    except OSError:
        return None
    if not is_probably_text_bytes(data):
        return None
    text = data.decode("utf-8", errors="replace")
    if len(text) > TEXT_PREVIEW_CHARS:
        text = text[:TEXT_PREVIEW_CHARS] + "\n..."
    return text


@lru_cache(maxsize=2048)
def get_video_duration(path):
    if not FFPROBE_PATH:
        return None
    try:
        with SUBPROCESS_SEMAPHORE:
            completed = subprocess.run(
                [
                    FFPROBE_PATH,
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
                timeout=3,
            )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        duration = float(completed.stdout.strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


def get_video_thumbnail_count(duration):
    if not duration or duration <= VIDEO_MULTI_THUMBNAIL_SECONDS:
        return 1
    return min(MAX_VIDEO_HOVER_THUMBNAILS, max(MIN_VIDEO_HOVER_THUMBNAILS, ceil(duration / 60)))


def get_video_thumbnail_timestamp(duration, index, count):
    if not duration:
        return 1
    if count <= 1:
        return min(max(duration * 0.1, 1), max(duration - 0.5, 0))
    safe_duration = max(duration - 1, 1)
    position = (index + 1) / (count + 1)
    return min(max(safe_duration * position, 1), max(duration - 0.5, 0))


@lru_cache(maxsize=4096)
def get_media_info(path):
    if not FFPROBE_PATH:
        return None
    try:
        with SUBPROCESS_SEMAPHORE:
            result = subprocess.run(
                [FFPROBE_PATH, "-v", "quiet", "-print_format", "json",
                 "-show_streams", "-show_format", path],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, check=False, timeout=10,
            )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    info = {}
    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type", "")
        if codec_type == "video" and "width" not in info:
            if stream.get("width"):
                info["width"] = stream["width"]
                info["height"] = stream.get("height")
            if stream.get("codec_name"):
                info["codec"] = stream["codec_name"]
        elif codec_type == "audio" and "audioCodec" not in info:
            if stream.get("codec_name"):
                info["audioCodec"] = stream["codec_name"]
            if stream.get("sample_rate"):
                info["sampleRate"] = stream["sample_rate"]
            if stream.get("channels"):
                info["channels"] = stream["channels"]
    fmt = data.get("format", {})
    if fmt.get("bit_rate"):
        try:
            info["bitrate"] = int(fmt["bit_rate"])
        except (ValueError, TypeError):
            pass
    return info or None


@lru_cache(maxsize=4096)
def get_exif_info(path):
    if not EXIFTOOL_PATH:
        return None
    try:
        with SUBPROCESS_SEMAPHORE:
            result = subprocess.run(
                [EXIFTOOL_PATH, "-json", "-n", "-fast2", path],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, check=False, timeout=15,
            )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        if not data:
            return None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    tags = data[0]
    keep_keys = {
        "Make", "Model", "LensModel", "FocalLength", "FNumber",
        "ExposureTime", "ISO", "Flash", "GPSLatitude", "GPSLongitude",
        "GPSAltitude", "DateTimeOriginal", "ColorSpace", "Orientation",
        "MegaPixels",
    }
    return {k: v for k, v in tags.items() if k in keep_keys and v is not None} or None


def build_thumbnail_command(path, kind, width=360, height=240, ffmpeg_path="ffmpeg", timestamp=1):
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        "format=yuvj420p"
    )
    command = [ffmpeg_path, "-v", "error"]
    if kind == "video":
        command.extend(["-ss", f"{max(timestamp, 0):.3f}"])
        command.extend(["-skip_frame", "nokey"])
    command.extend([
        "-i", path,
        "-frames:v", "1",
        "-vf", vf,
        "-q:v", str(THUMBNAIL_QUALITY),
        "-f", "image2pipe",
        "-vcodec", THUMBNAIL_FORMAT,
        "-",
    ])
    return command


def get_thumbnail_cache_key(path, width, height, thumb_index=0):
    try:
        file_stat = os.stat(path, follow_symlinks=False)
    except OSError:
        return None
    return (
        path,
        file_stat.st_mtime_ns,
        file_stat.st_size,
        width,
        height,
        thumb_index,
        THUMBNAIL_FORMAT,
        THUMBNAIL_QUALITY,
    )


class ThumbnailCache:
    def __init__(self, max_items=MAX_THUMBNAIL_CACHE_SIZE, max_bytes=MAX_THUMBNAIL_CACHE_BYTES):
        self.max_items = max_items
        self.max_bytes = max_bytes
        self.items = OrderedDict()
        self.byte_size = 0

    def get(self, key):
        body = self.items.get(key)
        if body is not None:
            self.items.move_to_end(key)
        return body

    def put(self, key, body):
        previous = self.items.pop(key, None)
        if previous is not None:
            self.byte_size -= len(previous)
        self.items[key] = body
        self.byte_size += len(body)
        self.trim()

    def trim(self):
        while self.items and (len(self.items) > self.max_items or self.byte_size > self.max_bytes):
            _key, body = self.items.popitem(last=False)
            self.byte_size -= len(body)

    def clear(self):
        self.items.clear()
        self.byte_size = 0


def render_thumbnail(path, width=360, height=240, thumb_index=0):
    kind = get_media_kind(path)
    if kind not in ("video", "image") or not FFMPEG_PATH:
        return None

    timestamp = 1
    if kind == "video":
        duration = get_video_duration(path)
        count = get_video_thumbnail_count(duration)
        timestamp = get_video_thumbnail_timestamp(duration, max(0, min(thumb_index, count - 1)), count)

    try:
        with THUMBNAIL_SUBPROCESS_SEMAPHORE:
            completed = subprocess.run(
                build_thumbnail_command(path, kind, width, height, FFMPEG_PATH, timestamp),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=20,
            )
    except subprocess.TimeoutExpired:
        print(f"[dedup] ffmpeg thumbnail timed out: {path}", file=sys.stderr)
        return None
    except OSError:
        return None
    if completed.returncode != 0:
        msg = completed.stderr.decode("utf-8", errors="replace").strip()
        print(f"[dedup] ffmpeg thumbnail failed (exit {completed.returncode}): {msg or '(no output)'}", file=sys.stderr)
        return None

    return completed.stdout


def get_thumbnail(path, cache, width=360, height=240, thumb_index=0):
    cache_key = get_thumbnail_cache_key(path, width, height, thumb_index)
    if not cache_key:
        return None
    cached_body = cache.get(cache_key)
    if cached_body is not None:
        return cached_body

    body = render_thumbnail(path, width, height, thumb_index)
    if body is not None:
        cache.put(cache_key, body)
    return body


def get_thumbnail_threadsafe(path, cache, cache_lock, in_flight, width=360, height=240, thumb_index=0):
    cache_key = get_thumbnail_cache_key(path, width, height, thumb_index)
    if not cache_key:
        return None

    with cache_lock:
        cached_body = cache.get(cache_key)
        if cached_body is not None:
            return cached_body
        event = in_flight.get(cache_key)
        if event is None:
            event = threading.Event()
            in_flight[cache_key] = event
            owns_render = True
        else:
            owns_render = False

    if not owns_render:
        event.wait()
        with cache_lock:
            return cache.get(cache_key)

    try:
        body = render_thumbnail(path, width, height, thumb_index)
        with cache_lock:
            if body is not None:
                cache.put(cache_key, body)
            return body
    finally:
        with cache_lock:
            in_flight.pop(cache_key, None)
            event.set()


def _warm_thumbnails(cache, cache_lock, in_flight, groups):
    """Pre-generate thumbnails for every video file in background.

    Phase 1 warms frame 0 for all files so the grid paints immediately.
    Phase 2 warms hover-cycling frames so cycling is instant after the grid loads.
    Runs in a daemon thread; failures are silently ignored.
    """
    video_paths = []
    for group in groups:
        for file_info in group["files"]:
            path = file_info.get("path")
            if path and get_media_kind(path) == "video":
                video_paths.append(path)

    if not video_paths:
        return

    unique_paths = list(dict.fromkeys(video_paths))

    def warm_one(path, thumb_index):
        get_thumbnail_threadsafe(path, cache, cache_lock, in_flight, 360, 240, thumb_index)

    with ThreadPoolExecutor(max_workers=16) as pool:
        # Phase 1: frame 0 for all files — feeds the initial grid paint
        phase1 = [pool.submit(warm_one, p, 0) for p in unique_paths]
        for f in as_completed(phase1):
            try:
                f.result()
            except Exception:
                pass

        # Phase 2: remaining hover frames — so cycling is instant from cache
        hover_tasks = []
        for path in unique_paths:
            duration = get_video_duration(path)
            count = get_video_thumbnail_count(duration)
            for i in range(1, count):
                hover_tasks.append((path, i))
        phase2 = [pool.submit(warm_one, p, i) for p, i in hover_tasks]
        for f in as_completed(phase2):
            try:
                f.result()
            except Exception:
                pass


def copy_name_rank(path):
    basename = os.path.splitext(os.path.basename(path))[0]
    return 1 if COPY_PATTERN.search(basename) else 0


def guess_original_filename(files):
    candidates = list(files)
    candidates.sort(key=lambda info: (info.mtime_ns, copy_name_rank(info.path), len(info.path), info.path))
    return candidates[0]


def describe_original_reason(info, group_files, original):
    if info.path == original.path:
        if info.mtime_ns == min(item.mtime_ns for item in group_files):
            return "older file"
        if copy_name_rank(info.path) == min(copy_name_rank(item.path) for item in group_files):
            return "not copy-name"
        if len(info.path) == min(len(item.path) for item in group_files):
            return "shorter path"
        return "path tiebreak"
    if info.mtime_ns > original.mtime_ns:
        return "newer copy"
    if copy_name_rank(info.path) > copy_name_rank(original.path):
        return "copy-name"
    if len(info.path) > len(original.path):
        return "longer path"
    return "path tiebreak"


def make_hasher(hash_name):
    try:
        return hashlib.new(hash_name, usedforsecurity=False)
    except TypeError:
        return hashlib.new(hash_name)


def make_fast_hasher():
    return make_hasher(FAST_HASH_NAME)


def get_sparse_sample_count(size, chunk_size=FAST_SAMPLE_BYTES):
    file_chunks = max(1, ceil(size / chunk_size))
    if size <= 64 * 1024 * 1024:
        target = MIN_FAST_SAMPLE_COUNT
    elif size <= 512 * 1024 * 1024:
        target = 16
    elif size <= 4 * 1024**3:
        target = 32
    elif size <= 16 * 1024**3:
        target = 48
    else:
        target = MAX_FAST_SAMPLE_COUNT
    return min(file_chunks, target)


def iter_sparse_offsets(size, chunk_size=FAST_SAMPLE_BYTES, sample_count=None):
    sample_count = get_sparse_sample_count(size, chunk_size) if sample_count is None else sample_count
    if size <= chunk_size or sample_count <= 1:
        return [0]

    max_offset = size - chunk_size
    offsets = {
        int(round((max_offset * idx) / (sample_count - 1)))
        for idx in range(sample_count)
    }
    return sorted(offsets)


def get_fast_multichunk_hash(
    path,
    size,
    chunk_size=FAST_SAMPLE_BYTES,
    sample_count=None,
):
    hasher = make_fast_hasher()
    hasher.update(str(size).encode("ascii"))
    try:
        with open(path, "rb") as file_obj:
            offsets = set(iter_sparse_offsets(size, chunk_size, sample_count))
            for offset in sorted(offsets):
                file_obj.seek(offset, os.SEEK_SET)
                hasher.update(offset.to_bytes(8, "big", signed=False))
                hasher.update(file_obj.read(chunk_size))
    except OSError:
        return None
    return hasher.hexdigest()


def get_full_content_hash(path, chunk_size=FULL_HASH_CHUNK_BYTES):
    try:
        with open(path, "rb") as file_obj:
            if hasattr(hashlib, "file_digest"):
                return hashlib.file_digest(file_obj, lambda: make_hasher(FULL_HASH_NAME)).hexdigest()
            hasher = make_hasher(FULL_HASH_NAME)
            while True:
                chunk = file_obj.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        return None
    return hasher.hexdigest()


def get_candidate_hash(path, size, sparse_label):
    if size <= SMALL_FILE_FULL_HASH_BYTES:
        return FULL_HASH_NAME, get_full_content_hash(path)
    return sparse_label, get_fast_multichunk_hash(path, size)


def is_drive_root(path):
    drive, tail = os.path.splitdrive(os.path.abspath(path))
    return bool(drive) and os.path.abspath(path) == drive + os.sep and tail in ("\\", "/")


def is_photo_library_path(path):
    parts = os.path.realpath(os.path.abspath(path)).split(os.sep)
    return any(part.lower().endswith(PHOTOS_LIBRARY_SUFFIXES) for part in parts)


def validate_scan_root(path, allow_home_root=False, allow_photo_library=False):
    abs_path = os.path.abspath(path)
    real_path = os.path.realpath(abs_path)
    if not os.path.isdir(real_path):
        raise ValueError(f"Scan path is not a directory: {real_path}")
    if real_path == os.path.abspath(os.sep) or is_drive_root(real_path) or is_drive_root(abs_path):
        raise ValueError(f"Refusing to scan filesystem root: {real_path}")
    if CURRENT_OS == OS_MACOS and (real_path in MACOS_BLOCKED_ROOTS or abs_path in MACOS_BLOCKED_ROOTS):
        raise ValueError(f"Refusing to scan system root: {abs_path}")
    home = os.path.realpath(os.path.expanduser("~"))
    if real_path == home and not allow_home_root:
        raise ValueError(f"Refusing to scan home root: {real_path}. Use --allow-home-root to opt in.")
    if is_photo_library_path(real_path) and not allow_photo_library:
        raise ValueError(
            f"Refusing to scan macOS Photos Library package: {real_path}. "
            "Export originals to a normal folder, or use --allow-photo-library only with a full backup."
        )
    return abs_path, real_path


def should_ignore_entry(name, is_dir, options):
    if not options.include_hidden and name.startswith("."):
        return True
    if is_dir:
        if not options.allow_photo_library and name.lower().endswith(PHOTOS_LIBRARY_SUFFIXES):
            return True
        return name in options.ignore_dirs
    if name in options.ignore_files:
        return True
    return name.endswith(options.ignore_file_suffixes)


def print_progress(label, done, total=None, stats=None):
    if total:
        percent = (done / total) * 100 if total else 100
        print(f"\r[{label}] {done}/{total} ({percent:.1f}%)", end="", flush=True)
    elif stats:
        print(
            f"\r[{label}] files={stats.scanned} dirs={stats.dirs_scanned} "
            f"ignored={stats.ignored} unreadable={stats.unreadable}",
            end="",
            flush=True,
        )
    else:
        print(f"\r[{label}] {done}", end="", flush=True)


def finish_progress():
    print()


_SF_DATALESS = 0x40000000  # BSD stat flag set on macOS 10.15+ iCloud Drive evicted files
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000


def _is_cloud_placeholder(file_stat, path=None):
    """Return True if the file is a cloud-storage placeholder with no local bytes.

    On macOS, iCloud Drive marks evicted (cloud-only) files with the SF_DATALESS
    BSD flag.  Reading such a file would trigger a silent background download;
    skipping them prevents unexpected gigabytes of iCloud sync during a scan.
    The file is counted in stats.cloud_skipped so users know why it is absent.

    On Windows, cloud providers commonly expose recall-on-open/data-access file
    attributes for cloud-only files.  Treat those as placeholders for the same
    reason: a hash read may hydrate large remote content unexpectedly.
    """
    if CURRENT_OS == OS_MACOS:
        return bool(getattr(file_stat, "st_flags", 0) & _SF_DATALESS)
    if CURRENT_OS == OS_WINDOWS:
        attrs = getattr(file_stat, "st_file_attributes", 0)
        return bool(attrs & (_FILE_ATTRIBUTE_RECALL_ON_OPEN | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS))
    return False


def scan_by_size(options, stats):
    current_real = os.path.realpath(options.path)
    if current_real != options.real_path:
        raise ValueError(
            f"Scan root changed since validation: {options.real_path} -> {current_real}. "
            "Aborting for safety."
        )
    sizes = defaultdict(list)
    singletons = {}
    entries_seen = 0

    for current_dir, dirnames, filenames in os.walk(options.path, topdown=True, followlinks=False):
        stats.dirs_scanned += 1
        kept_dirs = []
        for dirname in dirnames:
            entries_seen += 1
            if should_ignore_entry(dirname, True, options):
                if not options.include_hidden and dirname.startswith("."):
                    stats.ignored_hidden_count += 1
                else:
                    stats.ignored_dir_counts[dirname] += 1
                stats.ignored += 1
            else:
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            entries_seen += 1
            if should_ignore_entry(filename, False, options):
                if not options.include_hidden and filename.startswith("."):
                    stats.ignored_hidden_count += 1
                stats.ignored += 1
                continue
            path = os.path.join(current_dir, filename)
            try:
                file_stat = os.stat(path, follow_symlinks=False)
            except OSError:
                stats.unreadable += 1
                if len(stats.unreadable_paths) < 50:
                    stats.unreadable_paths.append(path)
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                stats.ignored += 1
                continue
            if _is_cloud_placeholder(file_stat, path):
                stats.cloud_skipped += 1
                if len(stats.cloud_skipped_paths) < 50:
                    stats.cloud_skipped_paths.append(path)
                stats.ignored += 1
                continue

            info = FileInfo(path, file_stat.st_size, file_stat.st_mtime_ns, file_stat.st_dev, file_stat.st_ino)
            previous = singletons.pop(info.size, None)
            if previous is not None:
                sizes[info.size].extend((previous, info))
            elif info.size in sizes:
                sizes[info.size].append(info)
            else:
                singletons[info.size] = info
            stats.scanned += 1

            if entries_seen % options.progress_every == 0:
                print_progress("scan", entries_seen, stats=stats)

    if entries_seen:
        print_progress("scan", entries_seen, stats=stats)
        finish_progress()
    return sizes


def find_duplicates(options):
    stats = ScanStats()
    full_verification = options.verify_mode == VERIFY_FULL
    stage_count = 3 if full_verification else 2
    print(f"Starting duplicate file scan in '{options.path}'")
    print("-" * 60)
    sparse_label = (
        f"{FAST_HASH_NAME}-scaled-sparse-{MIN_FAST_SAMPLE_COUNT}-"
        f"{MAX_FAST_SAMPLE_COUNT}x{format_size(FAST_SAMPLE_BYTES)}"
    )
    if full_verification:
        print(
            f"Hash function: {FULL_HASH_NAME} for <= {format_size(SMALL_FILE_FULL_HASH_BYTES)}, "
            f"{sparse_label} prefilter for larger files, then {FULL_HASH_NAME} full verification"
        )
    else:
        print(
            f"Hash function: {FULL_HASH_NAME} for <= {format_size(SMALL_FILE_FULL_HASH_BYTES)}, "
            f"{sparse_label} sampled verification for larger files (fast, probabilistic)"
        )
    print(f"Ignoring directories named: {', '.join(sorted(options.ignore_dirs))}")
    print(f"Ignoring files named: {', '.join(sorted(options.ignore_files))}")
    print("-" * 60)

    print(f"\n[Stage 1/{stage_count}] Scanning files and grouping by size...")
    potential_by_size = scan_by_size(options, stats)
    size_candidates = [info for files in potential_by_size.values() for info in files]
    stats.size_candidates = len(size_candidates)
    if not size_candidates:
        print("\nNo files with duplicate sizes found.")
        return [], stats

    print(f"\n[Stage 2/{stage_count}] Sparse multi-chunk hashing {len(size_candidates)} files...")
    fingerprint_groups = defaultdict(list)
    total = len(size_candidates)
    for done, info in enumerate(size_candidates, 1):
        hash_name, fingerprint = get_candidate_hash(
            info.path,
            info.size,
            sparse_label,
        )
        if fingerprint:
            fingerprint_groups[(info.size, hash_name, fingerprint)].append(info)
        else:
            stats.unreadable += 1
            if len(stats.unreadable_paths) < 50:
                stats.unreadable_paths.append(info.path)
        if done == total or done % options.progress_every == 0:
            print_progress("sparse hash", done, total)
    finish_progress()

    sparse_candidates = [
        info
        for files in fingerprint_groups.values()
        if len(files) > 1
        for info in files
    ]
    if not sparse_candidates:
        print("\nNo potential duplicates found after sparse multi-chunk hashing.")
        return [], stats

    if not full_verification:
        duplicates = [
            DuplicateGroup(
                fingerprint,
                tuple(sorted(files, key=lambda item: item.path)),
                hash_name,
            )
            for (_size, hash_name, fingerprint), files in fingerprint_groups.items()
            if len(files) > 1
        ]
        duplicates.sort(key=lambda group: group.files[0].path)
        stats.duplicate_groups = len(duplicates)
        stats.duplicate_files = sum(len(group.files) for group in duplicates)
        return duplicates, stats

    print(f"\n[Stage 3/{stage_count}] Full content hashing {len(sparse_candidates)} files...")
    full_hash_groups = defaultdict(list)
    total = len(sparse_candidates)
    for done, info in enumerate(sparse_candidates, 1):
        full_hash = get_full_content_hash(info.path)
        if full_hash:
            full_hash_groups[(info.size, full_hash)].append(info)
        else:
            stats.unreadable += 1
            if len(stats.unreadable_paths) < 50:
                stats.unreadable_paths.append(info.path)
        if done == total or done % options.progress_every == 0:
            print_progress("full hash", done, total)
    finish_progress()

    duplicates = [
        DuplicateGroup(full_hash, tuple(sorted(files, key=lambda item: item.path)), FULL_HASH_NAME)
        for (_size, full_hash), files in full_hash_groups.items()
        if len(files) > 1
    ]
    duplicates.sort(key=lambda group: group.files[0].path)
    stats.duplicate_groups = len(duplicates)
    stats.duplicate_files = sum(len(group.files) for group in duplicates)
    if not duplicates:
        print("\nNo duplicates found after full content verification.")
    return duplicates, stats


def find_empty_dirs(options):
    """Return sorted list of paths that are effectively empty.

    A directory is empty if it contains only files matched by the ignore-file
    rules and/or subdirectories that are themselves empty by the same rule.
    The scan root itself is never included.
    """
    current_real = os.path.realpath(options.path)
    if current_real != options.real_path:
        raise ValueError(
            f"Scan root changed since validation: {options.real_path} -> {current_real}. "
            "Aborting for safety."
        )
    scan_root = os.path.abspath(options.path)
    empty_set = set()
    result = []

    for dirpath, dirnames, filenames in os.walk(scan_root, topdown=False, followlinks=False):
        if dirpath == scan_root:
            continue
        if should_ignore_entry(os.path.basename(dirpath), True, options):
            continue
        if any(not should_ignore_entry(f, False, options) for f in filenames):
            continue
        if all(
            should_ignore_entry(d, True, options)
            or os.path.join(dirpath, d) in empty_set
            for d in dirnames
        ):
            empty_set.add(dirpath)
            result.append(dirpath)

    return sorted(result)


def build_browser_payload(duplicate_groups):
    groups = []
    next_id = 1
    for group_idx, group in enumerate(duplicate_groups):
        original = guess_original_filename(group.files)
        # Detect hardlink partners: multiple paths that share (st_dev, st_ino).
        # Trashing a hardlink does not reclaim disk space until all links are gone.
        inode_counts = {}
        for info in group.files:
            if info.st_dev or info.st_ino:
                key = (info.st_dev, info.st_ino)
                inode_counts[key] = inode_counts.get(key, 0) + 1
        hardlink_inodes = {k for k, v in inode_counts.items() if v > 1}
        group_files = []
        for info in sorted(group.files, key=lambda item: (item.mtime_ns, item.path)):
            file_id = str(next_id)
            next_id += 1
            media_kind = get_media_kind(info.path)
            extension = os.path.splitext(info.path or "")[1].lower()
            preview_kind = media_kind or ("pdf" if extension in PDF_EXTENSIONS else "text" if extension in TEXT_EXTENSIONS else None)
            is_hardlink = bool(hardlink_inodes and (info.st_dev, info.st_ino) in hardlink_inodes)
            default_trash = info.path != original.path and not is_hardlink
            group_files.append({
                "id": file_id,
                "path": info.path,
                "name": os.path.basename(info.path),
                "directory": os.path.dirname(info.path) or ".",
                "size": info.size,
                "sizeLabel": format_size(info.size),
                "mtime": str(info.mtime_ns),
                "mediaKind": media_kind,
                "previewKind": preview_kind,
                "thumbnailCount": 1,
                "isOriginalGuess": info.path == original.path,
                "isHardlink": is_hardlink,
                "selectionReason": "hardlink path" if is_hardlink else "original guess" if info.path == original.path else "auto-marked copy",
                "originalReason": describe_original_reason(info, group.files, original),
                "defaultTrash": default_trash,
            })
        groups.append({
            "id": group.hash or str(group_idx),
            "hash": group.hash,
            "hashName": group.hash_name,
            "fileCount": len(group_files),
            "files": group_files,
        })
    return {"groups": groups}


def build_windows_reveal_command(path):
    script = r'''
param([string]$Path)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class WindowFocus {
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@
$hwnd = [WindowFocus]::GetForegroundWindow()
Start-Process explorer.exe -ArgumentList ("/select,`"$Path`"")
Start-Sleep -Milliseconds 200
[WindowFocus]::SetForegroundWindow($hwnd) | Out-Null
'''.strip()
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-Command", script,
        path,
    ]


def build_reveal_command(path, current_os=CURRENT_OS):
    if current_os == OS_MACOS:
        return [
            "osascript",
            "-e", "on run argv",
            "-e", "set frontApp to path to frontmost application as text",
            "-e", "set targetFile to POSIX file (item 1 of argv) as alias",
            "-e", 'tell application "Finder" to reveal targetFile',
            "-e", "tell application frontApp to activate",
            "-e", "end run",
            path,
        ]
    if current_os == OS_WINDOWS:
        return build_windows_reveal_command(path)
    return ["xdg-open", os.path.dirname(path) or "."]


# Shared across both UI pages. Edit here to change the palette, base rules, or esc().
_SHARED_CSS = """\
:root {
  color-scheme: light dark;
  --bg: #fafafa;
  --panel: #ffffff;
  --text: #18181b;
  --muted: #71717a;
  --line: #e4e4e7;
  --surface: #f4f4f5;
  --keep: #16a34a;
  --keep-bg: #f0fdf4;
  --keep-border: #86efac;
  --danger: #dc2626;
  --danger-bg: #fef2f2;
  --danger-border: #fca5a5;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #09090b;
    --panel: #18181b;
    --text: #fafafa;
    --muted: #a1a1aa;
    --line: #27272a;
    --surface: #27272a;
    --keep: #22c55e;
    --keep-bg: #052e16;
    --keep-border: #166534;
    --danger: #ef4444;
    --danger-bg: #450a0a;
    --danger-border: #991b1b;
  }
}
* { box-sizing: border-box; }
button { border: 1px solid var(--line); border-radius: 6px; background: var(--surface); color: var(--text); padding: 6px 10px; font: inherit; cursor: pointer; white-space: nowrap; }
button.primary { background: var(--text); border-color: var(--text); color: var(--panel); }
button:disabled { opacity: .45; cursor: not-allowed; }
button:hover:not(:disabled) { opacity: .8; }
.modal-backdrop { position: fixed; inset: 0; z-index: 50; display: none; align-items: center; justify-content: center; padding: 20px; background: rgba(0,0,0,.48); }
.modal-backdrop.is-open { display: flex; }
.modal h2 { margin: 0; font-size: 15px; }
.modal p { margin: 0; color: var(--muted); font-size: 12px; }\
"""

_SHARED_JS = """\
const DEDUP_SESSION_TOKEN = new URLSearchParams(window.location.search).get("token") || "";
function esc(value) { return String(value).replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); }
function urlId(value) { return encodeURIComponent(String(value)); }
function authUrl(path) {
  if (!DEDUP_SESSION_TOKEN) return path;
  return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(DEDUP_SESSION_TOKEN);
}
function authUrlAttr(path) { return authUrl(path).replace(/&/g, "&amp;"); }\
"""


def build_browser_html():
    # HTML assembled from _SHARED_CSS + _SHARED_JS (above) plus page-specific content below.
    return (
"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dedup Review</title>
<style>"""
+ _SHARED_CSS
+ """
/* ── page-specific ─────────────────────────────────── */
body { margin: 0; font: 13px/1.5 "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; }
header { position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 10px 16px; background: var(--panel); border-bottom: 1px solid var(--line); flex-shrink: 0; }
input[type="search"] { flex: 1; min-width: 140px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); color: var(--text); font: inherit; }
input[type="search"]:focus { outline: 2px solid var(--text); outline-offset: 1px; }
select { padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); color: var(--text); font: inherit; cursor: pointer; }
button.keep { color: var(--keep); }
button.danger { color: var(--danger); }
button#cancel { background: transparent; border-color: transparent; color: var(--muted); font-size: 12px; }
button#cancel:hover { color: var(--text); opacity: 1; }
button[aria-pressed="true"], button.active-toggle { border-color: var(--text); }
.stats-bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 6px 16px; background: var(--surface); border-bottom: 1px solid var(--line); font-size: 12px; color: var(--muted); flex-shrink: 0; }
.stats-inline { display: flex; gap: 6px; flex-wrap: wrap; }
.stats-inline b { color: var(--text); font-weight: 600; }
.filter-hint { font-style: italic; }
.header-divider { width: 1px; background: var(--line); align-self: stretch; margin: 0 2px; flex-shrink: 0; }
.header-actions { display: flex; gap: 6px; align-items: center; margin-left: auto; }
button.danger-action { background: var(--danger); border-color: var(--danger); color: #fff; }
button.danger-action:hover:not(:disabled) { opacity: .85; }
.folder-clear { margin-left: 2px; font-style: normal; }
.content-scroll { overflow-y: scroll; overflow-x: hidden; flex: 1; contain: layout size; }
.content { padding: 12px 16px; }
.group { margin-bottom: 10px; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; min-height: 100px; }
.group-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 12px; background: var(--surface); font-size: 12px; }
.group-title { min-width: 0; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.group-title span { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.group-actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.group-actions > button { font-size: 11px; padding: 3px 8px; border-radius: 4px; }
.group-actions .trash-copies { border-color: var(--danger-border); color: var(--danger); background: var(--danger-bg); }
.group-impact { font-size: 11px; color: var(--muted); }
.files { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; padding: 10px; }
.file { border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: var(--panel); position: relative; display: grid; grid-template-rows: 140px 1fr; transition: border-color .12s; }
.file.is-trash { border-color: var(--danger-border); }
.file.is-trash .thumb { opacity: .55; filter: grayscale(.35); }
.file.is-keep { border-color: var(--keep-border); }
.file:has(.choice .keep:hover) { border-color: var(--keep-border); }
.file:has(.choice .trash:hover) { border-color: var(--danger-border); }
.thumb { display: grid; place-items: center; background: var(--surface); color: var(--muted); height: 140px; position: relative; overflow: hidden; cursor: zoom-in; }
.thumb:focus { outline: 2px solid var(--text); outline-offset: -2px; }
.thumb img { width: 100%; max-height: 140px; object-fit: contain; display: block; }
.thumb iframe { width: 100%; height: 100%; border: 0; background: var(--panel); display: block; pointer-events: none; }
.video-fallback { display: block; padding: 20px; text-align: center; color: var(--muted); font-size: 12px; word-break: break-word; }
.text-preview { width: 100%; height: 100%; max-height: 108px; margin: 0; padding: 8px; overflow: hidden; white-space: pre-wrap; font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: var(--text); background: var(--surface); display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 7; }
.meta { padding: 8px; display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.name { font-weight: 600; font-size: 12px; word-break: break-all; line-height: 1.3; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.path { color: var(--muted); font-size: 11px; word-break: break-all; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.badges { display: flex; gap: 4px; flex-wrap: wrap; align-content: flex-start; min-height: 16px; }
.badge { font-size: 11px; line-height: 16px; padding: 1px 6px; border-radius: 999px; background: var(--surface); color: var(--muted); height: 18px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.badge.original { background: #fef9c3; color: #854d0e; }
.badge.manual { color: var(--text); font-style: italic; }
.badge.hardlink { background: #dbeafe; color: #1d4ed8; }
@media (prefers-color-scheme: dark) {
  .badge.original { background: #422006; color: #fde68a; }
  .badge.hardlink { background: #1e3a5f; color: #93c5fd; }
}
.choice { display: grid; grid-template-columns: 1fr 1fr; border-top: 1px solid var(--line); margin-top: auto; }
.choice button { border: 0; border-radius: 0; padding: 6px; font-size: 12px; font-weight: 500; background: transparent; color: var(--muted); }
.choice button.active.keep { background: var(--keep-bg); color: var(--keep); font-weight: 600; }
.choice button.active.trash { background: var(--danger-bg); color: var(--danger); font-weight: 600; }
.choice button.keep:not(.active):hover { background: var(--keep-bg); color: var(--keep); opacity: 1; }
.choice button.trash:not(.active):hover { background: var(--danger-bg); color: var(--danger); opacity: 1; }
.reveal-btn { position: absolute; top: 6px; right: 6px; border: 1px solid var(--line); background: var(--panel); opacity: 0; transition: opacity .15s; z-index: 2; padding: 3px 7px; font-size: 11px; border-radius: 5px; }
.file:hover .reveal-btn, .file:focus-within .reveal-btn { opacity: 1; }
.empty-state { padding: 48px 20px; color: var(--muted); text-align: center; display: grid; justify-items: center; gap: 12px; }
.empty-state button { color: var(--text); }
.folder-strip { display: none; flex-wrap: wrap; gap: 4px; padding: 4px 16px; background: var(--panel); border-bottom: 1px solid var(--line); flex-shrink: 0; }
.folder-row { display: inline-flex; flex-direction: column; gap: 1px; padding: 2px 8px; border-radius: 4px; font: inherit; font-size: 10px; text-align: left; border: 1px solid var(--line); background: transparent; cursor: pointer; max-width: 200px; overflow: hidden; }
.folder-row:hover:not(:disabled) { background: var(--surface); opacity: 1; }
.folder-row.is-active { background: var(--surface); border-color: var(--text); }
.folder-row b { color: var(--text); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block; }
.folder-row span { color: var(--muted); white-space: nowrap; }
.modal { width: min(440px, 100%); background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 20px; display: grid; gap: 14px; }
.modal.wide { width: min(920px, 100%); max-height: 90vh; overflow: auto; }
.modal-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.confirm-list, .preview-meta { max-height: 160px; overflow: auto; border: 1px solid var(--line); border-radius: 6px; padding: 8px; background: var(--surface); font-size: 11px; color: var(--muted); overflow-wrap: anywhere; }
.confirm-list div, .preview-meta div { word-break: break-word; overflow-wrap: anywhere; margin-bottom: 4px; }
.confirm-folders { display: grid; gap: 4px; font-size: 11px; color: var(--muted); }
.preview-header { display: flex; align-items: flex-start; gap: 12px; justify-content: space-between; min-width: 0; }
.preview-header > div, .preview-header h2 { min-width: 0; }
.preview-header h2 { overflow-wrap: anywhere; word-break: break-word; }
.preview-decision { display: flex; gap: 6px; flex-shrink: 0; }
.preview-keep-btn { color: var(--keep); border-color: var(--keep-border); background: var(--keep-bg); font-size: 12px; padding: 4px 10px; }
.preview-keep-btn.active { background: var(--keep); color: #fff; border-color: var(--keep); opacity: 1; }
.preview-trash-btn { color: var(--danger); border-color: var(--danger-border); background: var(--danger-bg); font-size: 12px; padding: 4px 10px; }
.preview-trash-btn.active { background: var(--danger); color: #fff; border-color: var(--danger); opacity: 1; }
.preview-body { min-height: 320px; min-width: 0; display: grid; place-items: center; background: var(--surface); border-radius: 8px; overflow: hidden; }
.preview-body img, .preview-body iframe, .preview-body video { max-width: 100%; width: 100%; max-height: 70vh; border: 0; object-fit: contain; background: var(--panel); }
.preview-body pre { width: 100%; max-width: 100%; max-height: 70vh; overflow: auto; margin: 0; padding: 12px; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: var(--surface); color: var(--text); }
.dir-picker { position: absolute; right: 10px; top: 40px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.12); z-index: 20; width: min(460px, 90%); max-height: 400px; overflow: auto; }
.dir-picker-row { border-bottom: 1px solid var(--line); padding: 10px 12px; }
.dir-picker-row:last-child { border-bottom: 0; }
.dir-picker-path { font-size: 11px; color: var(--text); word-break: break-all; margin-bottom: 3px; line-height: 1.4; }
.dir-picker-impact { font-size: 10px; color: var(--muted); margin-bottom: 8px; }
.dir-picker-btns { display: flex; gap: 6px; justify-content: flex-end; flex-wrap: wrap; }
.dir-picker button { padding: 3px 8px; font-size: 10px; font-weight: 600; }
.folder-rules-modal { gap: 12px; width: min(920px, 100%); max-height: 90vh; }
.folder-rules-active, .folder-rules-preview { display: grid; gap: 6px; font-size: 12px; }
.folder-rules-help { color: var(--muted); }
.folder-rule-chip { display: flex; gap: 8px; align-items: center; justify-content: space-between; border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; background: var(--surface); }
.folder-rule-chip-main { display: flex; gap: 6px; align-items: center; min-width: 0; }
.rule-pill { border-radius: 999px; padding: 1px 6px; font-size: 10px; font-weight: 700; letter-spacing: .04em; }
.rule-pill.keep { background: var(--keep-bg); color: var(--keep); }
.rule-pill.trash { background: var(--danger-bg); color: var(--danger); }
.rule-pill.scope { background: var(--panel); color: var(--muted); border: 1px solid var(--line); }
.folder-rule-chip-path { color: var(--muted); word-break: break-all; }
.folder-rule-chip button { padding: 2px 7px; font-size: 11px; }
.folder-rules-preview { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: var(--surface); color: var(--muted); }
.folder-rules-preview b { color: var(--text); }
.folder-rules-preview-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 8px; }
.folder-rules-metric { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.folder-rules-metric .danger { color: var(--danger); font-weight: 700; }
.folder-rules-metric .keep { color: var(--keep); font-weight: 700; }
.folder-rules-warning { color: var(--danger); font-size: 11px; }
.folder-rules-preview-list { display: grid; gap: 3px; font-size: 11px; }
.folder-rules-list { display: grid; gap: 6px; max-height: 360px; overflow: auto; border: 1px solid var(--line); border-radius: 8px; padding: 8px; }
.folder-rule-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; border-bottom: 1px solid var(--line); padding: 7px 0; }
.folder-rule-row:last-child { border-bottom: 0; }
.folder-rule-name { color: var(--text); font-size: 12px; font-weight: 600; word-break: break-all; }
.folder-rule-path { color: var(--muted); word-break: break-all; font-size: 11px; }
.folder-rule-stats { color: var(--muted); font-size: 11px; white-space: nowrap; }
.folder-rule-actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.folder-rule-actions label { display: flex; gap: 4px; align-items: center; color: var(--muted); font-size: 11px; white-space: nowrap; }
.folder-rule-actions button { padding: 3px 8px; font-size: 11px; }
@media (max-width: 640px) { .folder-rules-preview-grid { grid-template-columns: 1fr; } .folder-rule-row { grid-template-columns: 1fr; } .folder-rule-actions { justify-content: stretch; } .folder-rule-actions button { flex: 1; } }
/* ── Main area: groups list + side pane ──────────────── */
#mainArea { display: flex; flex: 1; min-height: 0; overflow: hidden; }
#mainArea > .content-scroll { flex: 1; min-width: 0; }
#previewPane { width: 420px; min-width: 200px; border-left: 0; background: var(--panel); display: flex; flex-direction: column; overflow: hidden; flex-shrink: 0; }
body:not(.pane-open) #previewPane { display: none; }
body:not(.pane-open) .pane-resizer { display: none; }
.pane-resizer { flex: 0 0 5px; background: var(--line); cursor: col-resize; position: relative; flex-shrink: 0; transition: background .15s; }
.pane-resizer:hover, .pane-resizer.is-dragging { background: var(--muted); }
.pane-resizer::before { content: ''; position: absolute; top: 0; bottom: 0; left: -6px; right: -6px; cursor: col-resize; }
.pane-placeholder { display: flex; flex-direction: column; align-items: center; justify-content: center; flex: 1; color: var(--muted); text-align: center; font-size: 13px; gap: 8px; padding: 24px; }
.pane-preview-area { flex: 1; min-height: 0; min-width: 0; display: flex; align-items: center; justify-content: center; background: var(--surface); position: relative; overflow: hidden; border-bottom: 1px solid var(--line); }
.pane-preview-area pre { max-width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }
.pane-preview-area img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; display: block; }
.pane-preview-area video { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }
.pane-preview-area iframe { width: 100%; height: 100%; border: 0; background: var(--panel); display: block; }
.pane-audio-placeholder { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; padding: 28px 16px; width: 100%; }
.pane-audio-icon { font-size: 52px; color: var(--muted); line-height: 1; }
.pane-audio-player { width: calc(100% - 24px); }
.pane-play-btn { position: absolute; width: 52px; height: 52px; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; border: 2px solid rgba(255,255,255,.45); font-size: 18px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background .15s; padding-left: 4px; }
.pane-play-btn:hover { background: rgba(0,0,0,.82); opacity: 1; }
.pane-metadata { flex: 0 0 auto; max-height: 40%; overflow-y: auto; padding: 10px 12px; font-size: 12px; border-top: 1px solid var(--line); }
.pane-meta-who { font-size: 11px; color: var(--muted); font-style: italic; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid var(--line); word-break: break-all; }
.pane-meta-row { display: flex; gap: 6px; margin-bottom: 4px; min-width: 0; }
.pane-meta-label { color: var(--muted); flex: 0 0 76px; font-size: 11px; padding-top: 1px; }
.pane-meta-value { color: var(--text); word-break: break-all; flex: 1; min-width: 0; line-height: 1.4; }
.pane-meta-section { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 10px 0 5px; }
/* ── Active group / file highlighting ────────────────── */
.group.is-active { background: var(--surface); }
.group.is-active > .group-head { background: var(--line); }
.file.is-pane-active { background: var(--surface); }
.file.is-pane-active::after { content: ''; position: absolute; inset: 0; border: 2px solid var(--text); border-radius: 7px; pointer-events: none; z-index: 10; }
body.list-view .file.is-pane-active::after { border-width: 0 0 0 3px; border-radius: 0; }
/* ── List view ───────────────────────────────────────── */
#listViewHeader { display: none; position: sticky; top: 0; z-index: 5; background: var(--surface); border-bottom: 2px solid var(--line); font-size: 11px; font-weight: 600; color: var(--muted); flex-shrink: 0; }
body.list-view #listViewHeader { display: flex; }
.lv-col { padding: 6px 8px; cursor: pointer; user-select: none; display: flex; align-items: center; gap: 3px; white-space: nowrap; border-right: 1px solid var(--line); }
.lv-col:last-child { border-right: 0; cursor: default; }
.lv-col:hover:not(:last-child) { color: var(--text); }
.lv-col.sort-active { color: var(--text); }
.lv-col-type { flex: 0 0 52px; }
.lv-col-name { flex: 2; min-width: 100px; }
.lv-col-dir { flex: 2; min-width: 80px; }
.lv-col-size { flex: 0 0 76px; }
.lv-col-reason { flex: 1; min-width: 60px; cursor: default; }
.lv-col-actions { flex: 0 0 130px; }
body.list-view .content { padding: 0; }
body.list-view .group { border-radius: 0; border-left: 0; border-right: 0; border-top: 0; margin-bottom: 0; min-height: 0; }
body.list-view .group + .group { border-top: 1px solid var(--line); }
body.list-view .files { display: flex; flex-direction: column; gap: 0; padding: 0; grid-template-columns: none; }
body.list-view .file { display: flex; flex-direction: row; border-radius: 0; border-left: 0; border-right: 0; border-bottom: 0; border-top: 1px solid var(--line); grid-template-rows: none; align-items: stretch; min-height: 40px; height: 40px; cursor: pointer; }
body.list-view .file:first-child { border-top: 0; }
body.list-view .thumb { display: none; }
body.list-view .reveal-btn { display: none; }
body.list-view .meta { flex-direction: row; padding: 0; gap: 0; flex: 1; align-items: center; overflow: hidden; min-height: 40px; }
body.list-view .file-type-badge { flex: 0 0 52px; display: flex; align-items: center; justify-content: center; font-size: 10px; color: var(--muted); font-weight: 700; letter-spacing: .03em; border-right: 1px solid var(--line); height: 100%; }
body.list-view .name { flex: 2; min-width: 100px; padding: 0 8px; -webkit-line-clamp: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }
body.list-view .path { flex: 2; min-width: 80px; padding: 0 8px; -webkit-line-clamp: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; font-size: 11px; }
body.list-view .badges { display: none; }
body.list-view .file-size-lv { flex: 0 0 76px; font-size: 11px; color: var(--muted); padding: 0 8px; white-space: nowrap; display: flex; align-items: center; border-right: 1px solid var(--line); height: 100%; }
body.list-view .choice { border-top: 0; border-left: 1px solid var(--line); flex: 0 0 130px; height: 40px; margin-top: 0; }
.lv-preview-btn { display: none; }
body.list-view .lv-preview-btn { display: flex; align-items: center; justify-content: center; font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: -.5px; border: 0; border-radius: 0; border-right: 1px solid var(--line); width: 36px; flex-shrink: 0; background: transparent; color: var(--muted); padding: 0; cursor: pointer; height: 100%; }
body.list-view .lv-preview-btn:hover { color: var(--text); background: var(--surface); opacity: 1; }
.file-type-badge { display: none; }
.file-size-lv { display: none; }
.file-reason { display: none; }
body.list-view .file-reason { flex: 1; min-width: 60px; font-size: 10px; color: var(--muted); padding: 0 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: flex; align-items: center; border-right: 1px solid var(--line); height: 100%; font-style: italic; }
/* ── Compact modal ───────────────────────────────────── */
.modal.wide { width: min(600px, 100%); max-height: 80vh; }
.preview-modal { width: min(900px, 100%); max-height: 90vh; }
.preview-body { min-height: 200px; }
.preview-body img, .preview-body video { max-height: 50vh; }
.preview-body pre { max-height: 40vh; }
@media (max-width: 640px) {
  input[type="search"] { min-width: 100px; }
  .files { grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); }
  .file { grid-template-rows: 110px 1fr; }
  .thumb { height: 110px; }
  .thumb img { max-height: 110px; }
  #previewPane { display: none !important; }
  .header-divider { display: none; }
}
</style>
</head>
<body>
<header>
  <input id="search" type="search" placeholder="Filter by name, folder, or path">
  <select id="mediaFilter"><option value="">All types</option><option value="image">Images</option><option value="video">Videos</option><option value="audio">Audio</option><option value="pdf">PDFs</option><option value="text">Text</option><option value="other">Other</option></select>
  <select id="sortOrder"><option value="path">Sort: path</option><option value="size">Sort: size</option><option value="count">Sort: count</option><option value="directory">Sort: directory</option><option value="type">Sort: type</option></select>
  <div class="header-divider" aria-hidden="true"></div>
  <button id="collapseClean" aria-pressed="false">Trashed only</button>
  <button id="toggleListView" aria-pressed="true">List</button>
  <button id="togglePane" aria-pressed="true">Preview</button>
  <button id="folderRules">Folder rules…</button>
  <button id="undo" disabled>Undo</button>
  <div class="header-actions">
    <button id="cancel">Cancel</button>
    <button id="finish" class="primary">Move to Trash</button>
  </div>
</header>
<div class="stats-bar">
  <span class="stats-inline"><b id="groupCount">0</b> groups · <b id="fileCount">0</b> files · <b id="trashCount">0</b> marked · <b id="reclaimSize">0 B</b> reclaimable</span>
  <span id="subtitle" class="filter-hint"></span>
</div>
<div class="folder-strip" id="folderSummary"></div>
<div id="mainArea">
<div class="content-scroll" id="scrollRoot">
  <div id="listViewHeader">
    <div class="lv-col lv-col-type" data-lv-sort="type">Type</div>
    <div class="lv-col lv-col-name" data-lv-sort="path">Name</div>
    <div class="lv-col lv-col-dir" data-lv-sort="directory">Directory</div>
    <div class="lv-col lv-col-size" data-lv-sort="size">Size</div>
    <div class="lv-col lv-col-reason">Reason</div>
    <div class="lv-col lv-col-actions">Actions</div>
  </div>
  <section class="content" id="groups"></section>
</div>
<div class="pane-resizer" id="paneResizer"></div>
<div id="previewPane">
  <div class="pane-placeholder" id="panePlaceholder">
    <p>Select a file to preview</p>
  </div>
  <div id="paneContent" style="display:none;flex-direction:column;flex:1;min-height:0;overflow:hidden;">
    <div class="pane-preview-area" id="panePreviewArea"></div>
    <div class="pane-metadata" id="paneMetadata"></div>
  </div>
</div>
</div>
<div id="confirmOverlay" class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="confirmTitle">
  <div class="modal">
    <h2 id="confirmTitle">Move selected files?</h2>
    <p id="confirmSummary"></p>
    <p>This moves marked duplicates to the Trash/Recycle Bin after one final safety check.</p>
    <div id="confirmFolders" class="confirm-folders"></div>
    <div id="confirmPaths" class="confirm-list"></div>
    <div class="modal-actions">
      <button id="confirmBack">Review again</button>
      <button id="confirmMove" class="danger-action">Move to trash</button>
    </div>
  </div>
</div>
<div id="folderRulesOverlay" class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="folderRulesTitle">
  <div class="modal wide folder-rules-modal">
    <div class="preview-header">
      <div>
        <h2 id="folderRulesTitle">Folder rules</h2>
        <p>Session-only rules for duplicate files only. Preview before applying; manual choices are preserved.</p>
      </div>
      <button id="folderRulesClose">Close</button>
    </div>
    <div id="folderRulesActive" class="folder-rules-active"></div>
    <div id="folderRulesPreview" class="folder-rules-preview"></div>
    <div class="modal-actions">
      <button id="folderRulesClear">Clear rules</button>
      <span style="flex:1"></span>
      <button id="folderRulesApply" class="primary">Apply folder rules</button>
    </div>
    <input id="folderRulesSearch" type="search" placeholder="Search folders in duplicate groups">
    <div id="folderRulesList" class="folder-rules-list"></div>
  </div>
</div>
<div id="previewOverlay" class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="previewTitle">
  <div class="modal wide preview-modal">
    <div class="preview-header">
      <h2 id="previewTitle">Preview</h2>
      <div class="preview-decision">
        <button id="previewKeep" class="preview-keep-btn">Keep</button>
        <button id="previewTrash" class="preview-trash-btn">Trash</button>
      </div>
    </div>
    <div id="previewMeta" class="preview-meta"></div>
    <div id="previewBody" class="preview-body"></div>
    <div class="modal-actions">
      <button id="previewPrev">&#8592; Prev</button>
      <button id="previewNext">Next &#8594;</button>
      <span style="flex:1"></span>
      <button id="previewKeep2" class="preview-keep-btn">Keep</button>
      <button id="previewTrash2" class="preview-trash-btn">Trash</button>
      <button id="previewClose">Close</button>
    </div>
  </div>
</div>
<script>
let allData = {groups: []};
let requireMoveConfirmation = false;
let filteredGroups = [];
let renderGroups = [];
let allFilesList = [];
let fileById = new Map();
let groupById = new Map();
let totalFileCount = 0;
let defaultSortIndex = 0;
const trash = new Set();
const manualChoices = new Set();
let undoSnapshot = null;
let trashedOnly = false;
let isSubmitting = false;
let sessionId = null;
let previewContext = null;
let previewRenderToken = 0;
let activeGroupId = null;
let activeFileId = null;
let paneOpen = true;
let listViewMode = true;
let paneMetaToken = 0;
let paneImageToken = 0;
let paneVideoToken = 0;
let paneVideoTimer = null;
let sortDescending = false;
let folderRules = [];
let nextFolderRuleId = 1;
let folderRulesDirty = false;
let folderRuleRowsCache = null;
const folderRuleReasons = new Map();
const $ = (id) => document.getElementById(id);
let searchTimeout = null;
const thumbTimers = new WeakMap();
const thumbPreloadQueue = [];
const queuedThumbPreloads = new Set();
const completedThumbPreloads = new Set();
let thumbPreloadActive = 0;
const MAX_THUMB_PRELOADS = 2;
const MIN_VIDEO_HOVER_THUMBNAILS = 4;
const MAX_VIDEO_HOVER_THUMBNAILS = 12;
const VIDEO_MULTI_THUMBNAIL_SECONDS = 15;
const observer = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    const idx = parseInt(entry.target.dataset.index);
    if (entry.isIntersecting && !entry.target.dataset.rendered) renderGroupContent(entry.target, renderGroups[idx]);
  });
}, { root: $("scrollRoot"), rootMargin: "200px" });
"""
+ _SHARED_JS
+ """
function formatSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(2) + " KB";
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(2) + " MB";
  return (bytes / 1073741824).toFixed(2) + " GB";
}
function getFilters() {
  return { q: $("search").value.trim().toLowerCase(), media: $("mediaFilter").value };
}
function filtersAreActive(filters = getFilters()) {
  return Boolean(filters.q || filters.media || trashedOnly);
}
function setTrashedOnly(value) {
  trashedOnly = value;
  const button = $("collapseClean");
  button.textContent = trashedOnly ? "Show all" : "Show trashed only";
  button.setAttribute("aria-pressed", String(trashedOnly));
  button.classList.toggle("active-toggle", trashedOnly);
}
function updateFilterHint(filters = getFilters()) {
  const active = [];
  if (filters.q) active.push("search");
  if (filters.media) active.push($("mediaFilter").selectedOptions[0].textContent.toLowerCase());
  if (trashedOnly) active.push("trashed only");
  $("subtitle").textContent = active.length ? `Filters active: ${active.join(", ")}` : "";
}
function getTrashSummary() {
  return getTrashDetails();
}
function allFiles() {
  return allFilesList;
}
function findFile(fileId) {
  return fileById.get(fileId);
}
function findCard(fileId) {
  return Array.from(document.querySelectorAll("[data-file-id]")).find(card => card.dataset.fileId === fileId);
}
function isTextInputTarget(target) {
  return target && typeof target.closest === "function" && (target.closest("input, textarea, select, video, audio") || target.isContentEditable);
}
function getTrashDetails(fileSet = trash) {
  const files = allFiles().filter(f => fileSet.has(f.id));
  const bytes = files.reduce((sum, f) => sum + f.size, 0);
  const folders = new Map();
  files.forEach((file) => {
    const current = folders.get(file.directory) || { directory:file.directory, count:0, bytes:0, paths:[] };
    current.count += 1;
    current.bytes += file.size;
    current.paths.push(file.path);
    folders.set(file.directory, current);
  });
  return { count:files.length, bytes, files, folders:Array.from(folders.values()).sort((a, b) => b.bytes - a.bytes || a.directory.localeCompare(b.directory)) };
}
function getFolderRuleRows() {
  if (folderRuleRowsCache) return folderRuleRowsCache;
  const folders = new Map();
  allFiles().forEach((file) => {
    const row = folders.get(file.directory) || { directory:file.directory, count:0, bytes:0 };
    row.count += 1;
    row.bytes += file.size;
    folders.set(file.directory, row);
  });
  folderRuleRowsCache = Array.from(folders.values()).sort((a, b) => b.count - a.count || b.bytes - a.bytes || a.directory.localeCompare(b.directory));
  return folderRuleRowsCache;
}
function directoryMatchesRule(directory, rule) {
  if (directory === rule.directory) return true;
  if (!rule.subtree) return false;
  return directory.startsWith(rule.directory + "/") || directory.startsWith(rule.directory + "\\\\");
}
function folderRuleSpecificity(directory, rule) {
  if (!directoryMatchesRule(directory, rule)) return null;
  return { length: rule.directory.length, exact: directory === rule.directory ? 1 : 0, exactRule: rule.subtree ? 0 : 1 };
}
function bestFolderRuleForFile(file) {
  let best = null;
  let bestSpec = null;
  folderRules.forEach((rule) => {
    const spec = folderRuleSpecificity(file.directory, rule);
    if (!spec) return;
    if (!bestSpec || spec.length > bestSpec.length || (spec.length === bestSpec.length && (spec.exact > bestSpec.exact || (spec.exact === bestSpec.exact && spec.exactRule > bestSpec.exactRule)))) {
      best = rule;
      bestSpec = spec;
    }
  });
  return best;
}
function summarizeChangedFolders(files) {
  const folders = new Map();
  files.forEach((file) => {
    const row = folders.get(file.directory) || { directory:file.directory, count:0, bytes:0 };
    row.count += 1;
    row.bytes += file.size;
    folders.set(file.directory, row);
  });
  return Array.from(folders.values()).sort((a, b) => b.bytes - a.bytes || b.count - a.count || a.directory.localeCompare(b.directory));
}
function computeFolderRulePreview() {
  const before = new Set(trash);
  const beforeReasons = new Map(folderRuleReasons);
  const nextTrash = new Set(trash);
  const nextFolderRuleReasons = new Map(folderRuleReasons);
  const skippedGroups = [];
  allData.groups.forEach((group) => {
    const groupBefore = new Set(group.files.filter(f => before.has(f.id)).map(f => f.id));
    group.files.forEach((file) => {
      if (manualChoices.has(file.id)) return;
      nextFolderRuleReasons.delete(file.id);
      const rule = bestFolderRuleForFile(file);
      if (rule && rule.type === "keep") {
        nextTrash.delete(file.id);
        nextFolderRuleReasons.set(file.id, `folder rule: keep ${rule.subtree ? "subtree" : "folder"}`);
      } else if (rule && rule.type === "trash") {
        nextTrash.add(file.id);
        nextFolderRuleReasons.set(file.id, `folder rule: trash ${rule.subtree ? "subtree" : "folder"}`);
      } else if (file.defaultTrash) {
        nextTrash.add(file.id);
      } else {
        nextTrash.delete(file.id);
      }
    });
    if (group.files.every(file => nextTrash.has(file.id))) {
      skippedGroups.push(group);
      group.files.forEach((file) => {
        if (groupBefore.has(file.id)) nextTrash.add(file.id);
        else nextTrash.delete(file.id);
        if (beforeReasons.has(file.id)) nextFolderRuleReasons.set(file.id, beforeReasons.get(file.id));
        else nextFolderRuleReasons.delete(file.id);
      });
    }
  });
  const newlyMarked = allFiles().filter(f => !before.has(f.id) && nextTrash.has(f.id));
  const newlyProtected = allFiles().filter(f => before.has(f.id) && !nextTrash.has(f.id));
  const reasonChangedIds = allFiles().filter(f => beforeReasons.get(f.id) !== nextFolderRuleReasons.get(f.id)).map(f => f.id);
  const stateChangedIds = allFiles().filter(f => before.has(f.id) !== nextTrash.has(f.id)).map(f => f.id);
  return {
    nextTrash,
    nextFolderRuleReasons,
    newlyMarked,
    newlyProtected,
    skippedGroups,
    changedIds: Array.from(new Set(stateChangedIds.concat(reasonChangedIds))),
    markedFolders: summarizeChangedFolders(newlyMarked),
    protectedFolders: summarizeChangedFolders(newlyProtected),
  };
}
function folderRuleLabel(rule) {
  return `${rule.type === "keep" ? "Keep" : "Mark for Trash"}${rule.subtree ? " subtree" : " exact"}`;
}
function basenameForDirectory(directory) {
  const trimmed = String(directory || ".").replace(/[\\/]+$/, "");
  const parts = trimmed.split(/[\\/]+/);
  return parts[parts.length - 1] || trimmed || ".";
}
function addFolderRule(directory, type, subtree) {
  folderRules = folderRules.filter(rule => !(rule.directory === directory && rule.subtree === subtree));
  folderRules.push({ id:String(nextFolderRuleId++), directory, type, subtree });
  folderRulesDirty = true;
  updateFolderRulesButton();
  renderFolderRulesPanel();
}
function removeFolderRule(ruleId) {
  const beforeCount = folderRules.length;
  folderRules = folderRules.filter(rule => rule.id !== ruleId);
  if (folderRules.length !== beforeCount) folderRulesDirty = true;
  updateFolderRulesButton();
  renderFolderRulesPanel();
}
function clearFolderRules() {
  const hadRulesOrReasons = folderRules.length > 0 || folderRuleReasons.size > 0;
  folderRules = [];
  if (hadRulesOrReasons) folderRulesDirty = true;
  updateFolderRulesButton();
  renderFolderRulesPanel();
}
function renderFolderRulesPreview() {
  const el = $("folderRulesPreview");
  if (!el) return;
  if (!folderRules.length && !folderRuleReasons.size && !folderRulesDirty) {
    el.innerHTML = '<div class="folder-rules-help">Add a keep or trash rule from the folder list below. Rules only affect duplicate files and never trash every copy in a group.</div>';
    $("folderRulesApply").disabled = true;
    $("folderRulesApply").textContent = "Apply folder rules";
    return;
  }
  const preview = computeFolderRulePreview();
  const markedBytes = preview.newlyMarked.reduce((sum, f) => sum + f.size, 0);
  const protectedBytes = preview.newlyProtected.reduce((sum, f) => sum + f.size, 0);
  const folderList = (rows, empty) => rows.slice(0, 6).map(row => `<div><b>${esc(row.count)}</b> · ${esc(formatSize(row.bytes))} · ${esc(row.directory)}</div>`).join("") || `<div>${empty}</div>`;
  const skipped = preview.skippedGroups.length ? `<div class="folder-rules-warning">${preview.skippedGroups.length} group(s) skipped because every copy matched trash rules; skipped groups remain unchanged.</div>` : "";
  el.innerHTML = `<div class="folder-rules-metric"><span><b>${preview.changedIds.length}</b> file(s) would change</span><span class="danger">${esc(formatSize(markedBytes))} newly marked for Trash</span><span class="keep">${esc(formatSize(protectedBytes))} newly protected</span></div>${skipped}<div class="folder-rules-preview-grid"><div><b>Newly marked for Trash</b><div class="folder-rules-preview-list">${folderList(preview.markedFolders, "No files newly marked")}</div></div><div><b>Newly protected</b><div class="folder-rules-preview-list">${folderList(preview.protectedFolders, "No files newly protected")}</div></div></div>`;
  $("folderRulesApply").disabled = preview.changedIds.length === 0;
  $("folderRulesApply").textContent = preview.changedIds.length ? `Apply: update ${preview.changedIds.length} file${preview.changedIds.length === 1 ? "" : "s"}` : "Apply: 0 changes";
}
function renderFolderRulesPanel() {
  const active = $("folderRulesActive");
  if (!active) return;
  active.innerHTML = folderRules.length ? folderRules.map(rule => `<div class="folder-rule-chip"><span class="folder-rule-chip-main"><span class="rule-pill ${rule.type === "keep" ? "keep" : "trash"}">${rule.type === "keep" ? "KEEP" : "TRASH"}</span><span class="rule-pill scope">${rule.subtree ? "subtree" : "exact"}</span><span class="folder-rule-chip-path">${esc(rule.directory)}</span></span><button data-remove-rule="${esc(rule.id)}">Remove</button></div>`).join("") : '<div class="folder-rules-help">No rules yet. Add a keep or trash rule from the folder list below.</div>';
  const q = ($("folderRulesSearch")?.value || "").trim().toLowerCase();
  const rows = getFolderRuleRows().filter(row => !q || row.directory.toLowerCase().includes(q)).slice(0, 200);
  $("folderRulesList").innerHTML = rows.map((row, idx) => `<div class="folder-rule-row"><div><div class="folder-rule-name">${esc(basenameForDirectory(row.directory))}</div><div class="folder-rule-path">${esc(row.directory)}</div><div class="folder-rule-stats">${row.count} duplicate file(s) · ${esc(formatSize(row.bytes))}</div></div><div class="folder-rule-actions"><label><input type="checkbox" data-rule-subtree="${idx}"> apply to subfolders too</label><button class="keep" data-add-rule="keep" data-dir-index="${idx}">Keep duplicates here</button><button class="danger" data-add-rule="trash" data-dir-index="${idx}">Mark duplicates here for Trash</button></div></div>`).join("") || '<div class="empty-state">No matching folders.</div>';
  $("folderRulesList")._folderRuleRows = rows;
  renderFolderRulesPreview();
}
function openFolderRules() {
  renderFolderRulesPanel();
  $("folderRulesOverlay").classList.add("is-open");
  $("folderRulesSearch").focus();
}
function closeFolderRules(force = false) {
  if (!force && folderRulesDirty && !window.confirm("Close with unapplied folder rule changes? Rules will stay in this review session.")) return;
  $("folderRulesOverlay").classList.remove("is-open");
}
function applyFolderRules() {
  const preview = computeFolderRulePreview();
  if (!preview.changedIds.length) return;
  recordUndo("folder rules");
  replaceSet(trash, Array.from(preview.nextTrash));
  replaceMap(folderRuleReasons, Array.from(preview.nextFolderRuleReasons.entries()));
  folderRulesDirty = false;
  updateFolderRulesButton();
  closeFolderRules(true);
  refreshSelectionUi(preview.changedIds);
}
function replaceSet(target, values) {
  target.clear();
  values.forEach(value => target.add(value));
}
function replaceMap(target, entries) {
  target.clear();
  entries.forEach(([key, value]) => target.set(key, value));
}
function selectionReasonForFile(file, isTrash) {
  if (manualChoices.has(file.id)) return "manual";
  return folderRuleReasons.get(file.id) || (isTrash ? file.selectionReason : file.originalReason) || "";
}
function updateFolderRulesButton() {
  const button = $("folderRules");
  if (!button) return;
  if (!folderRules.length && !folderRulesDirty) button.textContent = "Folder rules…";
  else if (!folderRules.length) button.textContent = "Folder rules (unapplied)";
  else button.textContent = `Folder rules (${folderRules.length}${folderRulesDirty ? " unapplied" : ""})`;
  button.classList.toggle("active-toggle", folderRules.length > 0 || folderRulesDirty);
}
function recordUndo(label) {
  undoSnapshot = { label, trashIds:Array.from(trash), manualIds:Array.from(manualChoices), folderRuleReasonEntries:Array.from(folderRuleReasons.entries()) };
  updateUndoButton();
}
function undoLastAction() {
  if (!undoSnapshot) return;
  const before = new Set(trash);
  const beforeReasons = new Map(folderRuleReasons);
  replaceSet(trash, undoSnapshot.trashIds);
  replaceSet(manualChoices, undoSnapshot.manualIds);
  replaceMap(folderRuleReasons, undoSnapshot.folderRuleReasonEntries || []);
  const changedIds = allFiles().filter(f => before.has(f.id) !== trash.has(f.id) || beforeReasons.get(f.id) !== folderRuleReasons.get(f.id)).map(f => f.id);
  undoSnapshot = null;
  refreshSelectionUi(changedIds);
}
function updateUndoButton() {
  const button = $("undo");
  if (!button) return;
  button.disabled = !undoSnapshot;
  button.textContent = undoSnapshot ? `Undo ${undoSnapshot.label}` : "Undo";
}
function setManual(ids) {
  ids.forEach((id) => {
    manualChoices.add(id);
    folderRuleReasons.delete(id);
  });
}
function updateFileCard(fileId) {
  const file = findFile(fileId);
  const card = findCard(fileId);
  if (!file || !card) return;
  const isTrash = trash.has(file.id);
  const paneActive = card.classList.contains("is-pane-active");
  card.className = `file ${isTrash ? 'is-trash' : 'is-keep'}${paneActive ? ' is-pane-active' : ''}`;
  const keepButton = card.querySelector(".keep");
  const trashButton = card.querySelector(".trash");
  const badges = card.querySelector(".badges");
  const reasonEl = card.querySelector(".file-reason");
  if (keepButton) keepButton.className = !isTrash ? "active keep" : "keep";
  if (trashButton) trashButton.className = isTrash ? "active trash" : "trash";
  if (badges) badges.innerHTML = fileBadgesHtml(file, isTrash);
  if (reasonEl) reasonEl.textContent = selectionReasonForFile(file, isTrash);
  if (previewContext && previewContext.fileId === fileId) updatePreviewDecision(fileId);
}
function refreshSelectionUi(changedIds = []) {
  if (trashedOnly) {
    render();
    return;
  }
  changedIds.forEach(updateFileCard);
  updateStats();
  saveSelections();
}
function fileMatches(file, filters) {
  const kind = file.previewKind || "other";
  if (filters.media && kind !== filters.media) return false;
  if (trashedOnly && !trash.has(file.id)) return false;
  return !filters.q || file.path.toLowerCase().includes(filters.q);
}
function visibleFilesForGroup(group, filters) {
  return group.files.filter(f => fileMatches(f, filters));
}
function computeRenderState(filters) {
  allData.groups.forEach((group) => {
    group._visibleFiles = visibleFilesForGroup(group, filters);
    group._directorySortKey = group._visibleFiles
      .map(file => `${file.directory}\n${file.path}`)
      .sort((a, b) => a.localeCompare(b))[0] || "";
  });
  renderGroups = allData.groups.filter(g => g._visibleFiles.length);
  filteredGroups = renderGroups;
}
function clearFilters() {
  $("search").value = "";
  $("mediaFilter").value = "";
  setTrashedOnly(false);
  render();
}
function mark(fileId, shouldTrash) {
  if (trash.has(fileId) === shouldTrash) return;
  recordUndo("last change");
  if (shouldTrash) trash.add(fileId); else trash.delete(fileId);
  manualChoices.add(fileId);
  folderRuleReasons.delete(fileId);
  refreshSelectionUi([fileId]);
}
function applyMarkGroup(baseTrash, group, mode, dirPath, global = false) {
  const nextTrash = new Set(baseTrash);
  const targetGroups = global ? allData.groups : [group];
  targetGroups.forEach(g => {
    if (!g) return;
    if (global && !g.files.some(f => f.directory === dirPath)) return;
    const ids = g.files.map(f => f.id);
    if (mode !== "keepDir") ids.forEach(id => nextTrash.delete(id));
    if (mode === "keepDir") {
      g.files.forEach(f => { if (f.directory === dirPath) nextTrash.delete(f.id); });
    } else if (mode === "trash") {
      ids.forEach(id => nextTrash.add(id));
      const original = g.files.find(f => f.isOriginalGuess) || g.files[0];
      nextTrash.delete(original.id);
    } else if (mode === "oldest") {
      const sorted = [...g.files].sort((a, b) => a.mtime.length !== b.mtime.length ? a.mtime.length - b.mtime.length : a.mtime.localeCompare(b.mtime));
      sorted.slice(1).forEach(f => nextTrash.add(f.id));
    } else if (mode === "default") {
      g.files.forEach(f => { if (f.defaultTrash) nextTrash.add(f.id); });
    } else if (mode === "byDirKeep") {
      g.files.forEach(f => { if (f.directory !== dirPath) nextTrash.add(f.id); });
    } else if (mode === "byDirTrash") {
      g.files.forEach(f => { if (f.directory === dirPath) nextTrash.add(f.id); });
    }
    const markedInGroup = g.files.filter(f => nextTrash.has(f.id));
    if (markedInGroup.length === g.files.length) {
      const original = g.files.find(f => f.isOriginalGuess) || g.files[0];
      nextTrash.delete(original.id);
    }
  });
  return nextTrash;
}
function markGroup(group, mode, dirPath, global = false) {
  recordUndo("bulk change");
  const before = new Set(trash);
  const nextTrash = applyMarkGroup(before, group, mode, dirPath, global);
  replaceSet(trash, Array.from(nextTrash));
  const changedIds = allFiles().filter(f => before.has(f.id) !== nextTrash.has(f.id)).map(f => f.id);
  setManual(changedIds);
  refreshSelectionUi(changedIds);
}
function folderActionImpact(group, mode, dirPath, global = false) {
  const nextTrash = applyMarkGroup(trash, group, mode, dirPath, global);
  const affected = allFiles().filter(f => !trash.has(f.id) && nextTrash.has(f.id));
  const bytes = affected.reduce((sum, f) => sum + f.size, 0);
  return `${affected.length} marked, ${formatSize(bytes)}`;
}
function showDirPicker(group, event) {
  event.stopPropagation();
  const existing = document.querySelector(".dir-picker");
  if (existing) existing.remove();
  const dirs = [...new Set(group.files.map((f) => f.directory))].sort();
  const picker = document.createElement("div");
  picker.className = "dir-picker";
  dirs.forEach((dir) => {
    const row = document.createElement("div");
    row.className = "dir-picker-row";
    const path = document.createElement("div");
    path.className = "dir-picker-path";
    path.textContent = dir;
    const impact = document.createElement("div");
    impact.className = "dir-picker-impact";
    impact.textContent = `Keep only: ${folderActionImpact(group, "byDirKeep", dir, false)} · Trash here: ${folderActionImpact(group, "byDirTrash", dir, false)} · Everywhere: ${folderActionImpact(group, "byDirTrash", dir, true)}`;
    const btns = document.createElement("div");
    btns.className = "dir-picker-btns";
    [["Keep this folder", "keepDir", false, "keep"], ["Keep only this folder", "byDirKeep", false, "keep"], ["Trash this folder", "byDirTrash", false, "danger"], ["Trash this folder everywhere", "byDirTrash", true, "danger"]].forEach(([label, mode, global, className]) => {
      const button = document.createElement("button");
      button.textContent = label;
      button.title = folderActionImpact(group, mode, dir, global);
      button.className = className;
      button.onclick = (e) => { e.stopPropagation(); markGroup(group, mode, dir, global); picker.remove(); };
      btns.appendChild(button);
    });
    row.appendChild(path);
    row.appendChild(impact);
    row.appendChild(btns);
    picker.appendChild(row);
  });
  event.target.closest(".group").appendChild(picker);
  const hide = (e) => { if (!picker.contains(e.target)) { picker.remove(); document.removeEventListener("click", hide); } };
  setTimeout(() => document.addEventListener("click", hide), 10);
}
function getGroupVideoThumbs(groupEl) {
  return Array.from(groupEl.querySelectorAll("img[data-video-thumb='1']"));
}
function ffmpegThumbUrl(fileId, thumbIndex) {
  return authUrl(`/thumb/${urlId(fileId)}?i=${thumbIndex}`);
}
async function fetchServerMetadata(file) {
  const response = await fetch(authUrl(`/meta/${urlId(file.id)}`));
  if (!response.ok) throw new Error("metadata failed");
  return response.json();
}
async function hydrateVideoFile(file) {
  if (!file || file.mediaKind !== "video" || file.videoMetaLoaded) return file;
  if (!file.videoMetaPromise) {
    file.videoMetaPromise = (async () => {
      try {
        const response = await fetch(authUrl(`/meta/${urlId(file.id)}`));
        if (!response.ok) throw new Error("metadata failed");
        const payload = await response.json();
        file.thumbnailCount = Math.max(1, Number(payload.thumbnailCount || 1));
      } catch {
        file.thumbnailCount = 1;
      } finally {
        file.videoMetaLoaded = true;
        file.videoMetaPromise = null;
      }
      return file;
    })();
  }
  return file.videoMetaPromise;
}
async function hydrateVideoMetadata(root) {
  const nodes = Array.from(root.querySelectorAll("img[data-video-thumb='1']")).filter(n => !n.dataset.metaLoaded);
  await Promise.all(nodes.map(async (node) => {
    const file = findFile(node.dataset.fileId);
    await hydrateVideoFile(file);
    const count = Math.max(1, Number(file && file.thumbnailCount || 1));
    node.dataset.thumbCount = String(count);
    node.dataset.metaLoaded = "1";
  }));
}
function queueVideoThumbPreload(img, thumbIndex) {
  const count = Math.max(1, Number(img.dataset.thumbCount || "1"));
  if (count <= 1) return;
  const normalizedIndex = thumbIndex % count;
  const key = `${img.dataset.fileId}:${normalizedIndex}`;
  if (queuedThumbPreloads.has(key) || completedThumbPreloads.has(key)) return;
  queuedThumbPreloads.add(key);
  thumbPreloadQueue.push({
    key,
    fileId: img.dataset.fileId,
    thumbIndex: normalizedIndex,
    order: Number(img.dataset.videoOrder || "0"),
  });
  thumbPreloadQueue.sort((a, b) => a.order - b.order || a.thumbIndex - b.thumbIndex || a.fileId.localeCompare(b.fileId));
  processVideoThumbPreloadQueue();
}
function preloadFfmpegThumb(fileId, thumbIndex) {
  return new Promise((resolve) => {
    const preload = new Image();
    preload.onload = resolve;
    preload.onerror = resolve;
    preload.src = ffmpegThumbUrl(fileId, thumbIndex);
  });
}
async function processVideoThumbPreloadQueue() {
  while (thumbPreloadActive < MAX_THUMB_PRELOADS && thumbPreloadQueue.length) {
    const item = thumbPreloadQueue.shift();
    thumbPreloadActive += 1;
    preloadFfmpegThumb(item.fileId, item.thumbIndex).finally(() => {
      queuedThumbPreloads.delete(item.key);
      completedThumbPreloads.add(item.key);
      thumbPreloadActive -= 1;
      processVideoThumbPreloadQueue();
    });
  }
}
function preloadGroupVideoThumbs(thumbs, index) {
  thumbs
    .slice()
    .sort((a, b) => Number(a.dataset.videoOrder || "0") - Number(b.dataset.videoOrder || "0"))
    .forEach((img) => queueVideoThumbPreload(img, index));
}
function startGroupVideoThumbCycle(groupEl) {
  const thumbs = getGroupVideoThumbs(groupEl);
  const maxCount = Math.max(...thumbs.map(img => Number(img.dataset.thumbCount || "1")), 1);
  if (maxCount <= 1 || thumbTimers.has(groupEl)) return;
  let index = Number(groupEl.dataset.thumbIndex || "0");
  preloadGroupVideoThumbs(thumbs, index + 1);
  preloadGroupVideoThumbs(thumbs, index + 2);
  const timer = setInterval(() => {
    index = (index + 1) % maxCount;
    groupEl.dataset.thumbIndex = String(index);
    thumbs.forEach((img) => {
      const count = Math.max(1, Number(img.dataset.thumbCount || "1"));
      const thumbIndex = index % count;
      img.dataset.thumbIndex = String(thumbIndex);
      img.src = ffmpegThumbUrl(img.dataset.fileId, thumbIndex);
    });
    preloadGroupVideoThumbs(thumbs, index + 1);
    preloadGroupVideoThumbs(thumbs, index + 2);
  }, 650);
  thumbTimers.set(groupEl, timer);
}
function stopGroupVideoThumbCycle(groupEl) {
  const timer = thumbTimers.get(groupEl);
  if (timer) {
    clearInterval(timer);
    thumbTimers.delete(groupEl);
  }
  groupEl.dataset.thumbIndex = "0";
  getGroupVideoThumbs(groupEl).forEach((img) => {
    img.dataset.thumbIndex = "0";
    img.src = ffmpegThumbUrl(img.dataset.fileId, 0);
    delete img.dataset.prefetched;
  });
}
function fallbackVideoThumb(img) {
  img.onerror = null;
  const fallback = document.createElement("span");
  fallback.className = "video-fallback";
  fallback.textContent = img.dataset.fileName || "Video preview unavailable";
  img.replaceWith(fallback);
}
/* ── Pane state ─────────────────────────────────────── */
function applyPaneOpen() {
  document.body.classList.toggle("pane-open", paneOpen);
  const btn = $("togglePane");
  if (btn) { btn.setAttribute("aria-pressed", String(paneOpen)); btn.classList.toggle("active-toggle", paneOpen); }
}
function togglePane() {
  paneOpen = !paneOpen;
  try { localStorage.setItem("dedupPaneOpen", String(paneOpen)); } catch {}
  applyPaneOpen();
}
function applyListView() {
  document.body.classList.toggle("list-view", listViewMode);
  const btn = $("toggleListView");
  if (btn) { btn.setAttribute("aria-pressed", String(listViewMode)); btn.classList.toggle("active-toggle", listViewMode); }
  if (listViewMode && !paneOpen) { paneOpen = true; try { localStorage.setItem("dedupPaneOpen", "true"); } catch {} applyPaneOpen(); }
  updateListViewHeader();
}
function initPaneResizer() {
  const resizer = $("paneResizer");
  const pane = $("previewPane");
  const mainArea = $("mainArea");
  if (!resizer || !pane || !mainArea) return;
  try {
    const saved = localStorage.getItem("dedupPaneWidth");
    if (saved) pane.style.width = Math.max(200, Math.min(Number(saved), window.innerWidth * 0.8)) + "px";
  } catch {}
  resizer.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    resizer.setPointerCapture(e.pointerId);
    resizer.classList.add("is-dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    const startX = e.clientX;
    const startWidth = pane.offsetWidth;
    const maxWidth = mainArea.getBoundingClientRect().width * 0.75;
    let rafPending = false;
    const onMove = (e) => {
      const w = Math.max(200, Math.min(startWidth + (startX - e.clientX), maxWidth));
      pane.style.width = w + "px";
      if (!rafPending) {
        rafPending = true;
        requestAnimationFrame(() => { rafPending = false; applyPaneImageQuality(); });
      }
    };
    const onUp = () => {
      resizer.classList.remove("is-dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      const w = pane.offsetWidth;
      if (w > 0) try { localStorage.setItem("dedupPaneWidth", w); } catch {}
      resizer.removeEventListener("pointermove", onMove);
      resizer.removeEventListener("pointerup", onUp);
      resizer.removeEventListener("pointercancel", onUp);
    };
    resizer.addEventListener("pointermove", onMove);
    resizer.addEventListener("pointerup", onUp);
    resizer.addEventListener("pointercancel", onUp);
  });
}
function toggleListView() {
  listViewMode = !listViewMode;
  try { localStorage.setItem("dedupListView", String(listViewMode)); } catch {}
  applyListView();
  render();
}
function updateListViewHeader() {
  const header = $("listViewHeader");
  if (!header) return;
  const current = $("sortOrder").value;
  header.querySelectorAll("[data-lv-sort]").forEach(col => {
    const active = col.dataset.lvSort === current;
    col.classList.toggle("sort-active", active);
    col.textContent = col.textContent.replace(/ [↑↓]$/, "");
    if (active) col.textContent += sortDescending ? " ↓" : " ↑";
  });
}
/* ── Pane content ───────────────────────────────────── */
function refreshActiveFileHighlight() {
  document.querySelectorAll(".file.is-pane-active").forEach(el => el.classList.remove("is-pane-active"));
  if (!activeFileId) return;
  const el = document.querySelector(`.file[data-file-id="${CSS.escape(activeFileId)}"]`);
  if (el) el.classList.add("is-pane-active");
}
function refreshActiveGroupHighlight() {
  document.querySelectorAll(".group.is-active").forEach(el => el.classList.remove("is-active"));
  if (!activeGroupId) return;
  const el = document.querySelector(`.group[data-group-id="${CSS.escape(activeGroupId)}"]`);
  if (el) el.classList.add("is-active");
}
function setActiveGroup(groupId) {
  if (activeGroupId === groupId) return;
  activeGroupId = groupId;
  const group = groupById.get(groupId);
  const repr = group ? (group.files.find(f => f.isOriginalGuess) || group.files[0]) : null;
  activeFileId = repr ? repr.id : null;
  refreshActiveGroupHighlight();
  refreshActiveFileHighlight();
  renderPane();
}
function setActiveFile(fileId) {
  activeFileId = fileId;
  refreshActiveFileHighlight();
  const file = fileById.get(fileId);
  if (file) renderPaneMeta(file);
}
function startPaneVideo(file) {
  stopPaneVideoCycle();
  const area = $("panePreviewArea");
  if (!area) return;
  area.innerHTML = "";
  const video = document.createElement("video");
  video.controls = true;
  video.autoplay = true;
  video.style.cssText = "max-width:100%;max-height:100%;object-fit:contain;display:block";
  video.src = authUrl(`/media/${urlId(file.id)}`);
  area.appendChild(video);
}
function stopPaneVideoCycle() {
  paneVideoToken++;
  if (paneVideoTimer) {
    clearInterval(paneVideoTimer);
    paneVideoTimer = null;
  }
}
async function startPaneVideoCycle(file) {
  stopPaneVideoCycle();
  const token = ++paneVideoToken;
  await hydrateVideoFile(file);
  if (token !== paneVideoToken) return;
  const count = Math.max(1, Number(file.thumbnailCount || 1));
  const area = $("panePreviewArea");
  const img = area ? area.querySelector("img[data-pane-video-thumb='1']") : null;
  if (!img || !img.isConnected || img.dataset.fileId !== file.id) return;
  img.dataset.thumbCount = String(count);
  if (count <= 1) return;
  let index = 0;
  const tick = () => {
    if (token !== paneVideoToken) return;
    const area = $("panePreviewArea");
    const img = area ? area.querySelector("img[data-pane-video-thumb='1']") : null;
    if (!img || !img.isConnected || img.dataset.fileId !== file.id) {
      stopPaneVideoCycle();
      return;
    }
    index = (index + 1) % count;
    img.dataset.thumbIndex = String(index);
    img.src = ffmpegThumbUrl(file.id, index);
  };
  paneVideoTimer = setInterval(tick, 650);
}
async function hydratePaneText(fileId, node) {
  try {
    const response = await fetch(authUrl(`/text/${urlId(fileId)}`));
    if (!response.ok) throw new Error("unavailable");
    const payload = await response.json();
    if (node.isConnected && node.dataset.paneText === fileId)
      node.textContent = typeof payload.text === "string" ? payload.text : "Preview unavailable";
  } catch { if (node.isConnected) node.textContent = "Preview unavailable"; }
}
const reprDimsCache = new Map();
async function loadReprDims(file) {
  if (reprDimsCache.has(file.id)) return reprDimsCache.get(file.id);
  try {
    const resp = await fetch(authUrl(`/meta/${urlId(file.id)}`));
    if (!resp.ok) return null;
    const payload = await resp.json();
    if (!payload.width) return null;
    const dims = { width: payload.width };
    reprDimsCache.set(file.id, dims);
    return dims;
  } catch { return null; }
}
function applyPaneImageQuality() {
  if (!activeGroupId) return;
  const g = groupById.get(activeGroupId);
  const repr = g ? (g.files.find(f => f.isOriginalGuess) || g.files[0]) : null;
  if (!repr || repr.mediaKind !== "image") return;
  const dims = reprDimsCache.get(repr.id);
  if (!dims) return;
  const pane = $("previewPane");
  const w = pane ? pane.clientWidth : 0;
  if (!w) return;
  const area = $("panePreviewArea");
  if (!area) return;
  const img = area.querySelector("img");
  if (!img || !img.isConnected) return;
  const useOriginal = w / dims.width >= 0.5;
  const isOriginal = !img.src.includes("/thumb/");
  if (useOriginal && !isOriginal) img.src = authUrl(`/media/${urlId(repr.id)}`);
  else if (!useOriginal && isOriginal) img.src = authUrl(`/thumb/${urlId(repr.id)}`);
}
async function maybeSwitchToOriginal(file) {
  const token = ++paneImageToken;
  const dims = await loadReprDims(file);
  if (!dims || token !== paneImageToken) return;
  applyPaneImageQuality();
}
function renderPanePreview(file) {
  const area = $("panePreviewArea");
  if (!area) return;
  stopPaneVideoCycle();
  const oldVideo = area.querySelector("video");
  if (oldVideo) { oldVideo.pause(); oldVideo.src = ""; }
  const oldIframe = area.querySelector("iframe");
  if (oldIframe) oldIframe.src = "";
  if (file.mediaKind === "image") {
    area.innerHTML = `<img src="${authUrl(`/thumb/${urlId(file.id)}`)}" onerror="this.onerror=null;this.src='${authUrl(`/media/${urlId(file.id)}`)}'" alt="">`;
    maybeSwitchToOriginal(file);
  } else if (file.mediaKind === "video") {
    area.innerHTML = `<img src="${authUrlAttr(`/thumb/${urlId(file.id)}?i=0`)}" onerror="fallbackVideoThumb(this)" data-video-thumb="1" data-pane-video-thumb="1" data-file-id="${esc(file.id)}" data-file-name="${esc(file.name)}" data-thumb-index="0" data-thumb-count="1" alt=""><button class="pane-play-btn" title="Play video">&#9654;</button>`;
    area.querySelector(".pane-play-btn").addEventListener("click", () => startPaneVideo(file));
    startPaneVideoCycle(file);
  } else if (file.mediaKind === "audio") {
    area.innerHTML = `<div class="pane-audio-placeholder"><div class="pane-audio-icon">&#9835;</div><audio class="pane-audio-player" controls src="${authUrl(`/media/${urlId(file.id)}`)}"></audio></div>`;
  } else if (file.previewKind === "pdf") {
    area.innerHTML = `<iframe src="${authUrl(`/pdf/${urlId(file.id)}`)}#page=1&toolbar=0&navpanes=0" title="PDF preview"></iframe>`;
  } else if (file.previewKind === "text") {
    area.innerHTML = `<pre style="width:100%;height:100%;padding:10px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;font:11px/1.4 ui-monospace,monospace;margin:0;background:var(--surface);color:var(--text)" data-pane-text="${esc(file.id)}">Loading...</pre>`;
    hydratePaneText(file.id, area.querySelector("[data-pane-text]"));
  } else {
    area.innerHTML = `<div style="padding:24px;color:var(--muted);text-align:center;font-size:12px;word-break:break-all">${esc(file.name)}</div>`;
  }
}
function metaRow(label, value) {
  return `<div class="pane-meta-row"><span class="pane-meta-label">${esc(String(label))}</span><span class="pane-meta-value">${esc(String(value ?? ""))}</span></div>`;
}
function formatDuration(seconds) {
  if (!seconds || isNaN(Number(seconds))) return "";
  const s = Math.round(Number(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;
  return `${m}:${String(sec).padStart(2,"0")}`;
}
function formatBitrate(bps) {
  if (!bps) return "";
  const kbps = Math.round(Number(bps) / 1000);
  return kbps >= 1000 ? `${(kbps / 1000).toFixed(1)} Mbps` : `${kbps} kbps`;
}
async function renderPaneMeta(file) {
  const area = $("paneMetadata");
  if (!area || !file) return;
  const token = ++paneMetaToken;
  const date = new Date(Number(file.mtime) / 1e6);
  const dateStr = isNaN(date.getTime()) ? "" : date.toLocaleString();
  let html = `<div class="pane-meta-who">Metadata: ${esc(file.name)}</div>`;
  html += metaRow("Name", file.name);
  html += metaRow("Path", file.path);
  html += metaRow("Size", file.sizeLabel);
  if (dateStr) html += metaRow("Modified", dateStr);
  html += `<div id="paneMetaExtra"></div>`;
  area.innerHTML = html;
  try {
    const payload = await fetchServerMetadata(file);
    if (token !== paneMetaToken) return;
    const extra = $("paneMetaExtra");
    if (!extra || token !== paneMetaToken) return;
    let x = "";
    if (payload.duration != null) x += metaRow("Duration", formatDuration(payload.duration));
    if (payload.width && payload.height) x += metaRow("Dimensions", `${payload.width} × ${payload.height}`);
    if (payload.codec) x += metaRow("Codec", payload.codec);
    if (payload.audioCodec) x += metaRow("Audio", payload.audioCodec);
    if (payload.sampleRate) x += metaRow("Sample rate", `${payload.sampleRate} Hz`);
    if (payload.channels) x += metaRow("Channels", payload.channels);
    if (payload.bitrate) x += metaRow("Bitrate", formatBitrate(payload.bitrate));
    if (payload.exif && Object.keys(payload.exif).length) {
      x += `<div class="pane-meta-section">EXIF</div>`;
      for (const [k, v] of Object.entries(payload.exif)) x += metaRow(k, v);
    }
    extra.innerHTML = x;
  } catch {}
}
function renderPane() {
  if (!paneOpen) return;
  const group = activeGroupId ? groupById.get(activeGroupId) : null;
  const placeholder = $("panePlaceholder");
  const content = $("paneContent");
  if (!placeholder || !content) return;
  if (!group) {
    placeholder.style.display = "";
    content.style.display = "none";
    return;
  }
  placeholder.style.display = "none";
  content.style.display = "flex";
  const repr = group.files.find(f => f.isOriginalGuess) || group.files[0];
  if (repr) renderPanePreview(repr);
  const metaFile = activeFileId ? fileById.get(activeFileId) : repr;
  if (metaFile) renderPaneMeta(metaFile);
}
function mediaHtml(file) {
  if (file.mediaKind === "image") return `<img loading="lazy" src="${authUrl(`/thumb/${urlId(file.id)}`)}" onerror="this.onerror=null;this.src='${authUrl(`/media/${urlId(file.id)}`)}'" alt="">`;
  if (file.mediaKind === "video") {
    const count = Math.max(1, Number(file.thumbnailCount || 1));
    return `<img loading="lazy" src="${authUrlAttr(`/thumb/${urlId(file.id)}?i=0`)}" onerror="fallbackVideoThumb(this)" data-video-thumb="1" data-file-id="${esc(file.id)}" data-file-name="${esc(file.name)}" data-video-order="${file.defaultSortIndex || 0}" data-thumb-index="0" data-thumb-count="${count}" alt="">`;
  }
  if (file.previewKind === "pdf") return `<iframe loading="lazy" src="${authUrl(`/pdf/${urlId(file.id)}`)}#page=1&toolbar=0&navpanes=0" title="PDF preview for ${esc(file.name)}"></iframe>`;
  if (file.previewKind === "text") return `<pre class="text-preview" data-text-id="${esc(file.id)}">Loading...</pre>`;
  return `<span>${esc(file.name)}</span>`;
}
function previewHtml(file) {
  if (file.mediaKind === "image") return `<img src="${authUrl(`/media/${urlId(file.id)}`)}" alt="">`;
  if (file.mediaKind === "video") return `<video controls autoplay muted playsinline src="${authUrl(`/media/${urlId(file.id)}`)}"></video>`;
  if (file.previewKind === "pdf") return `<div style="padding:32px;text-align:center;color:var(--muted);font-size:13px;display:flex;flex-direction:column;align-items:center;gap:12px"><p style="margin:0">PDF preview available in the side panel.</p><a href="${authUrl(`/pdf/${urlId(file.id)}`)}" target="_blank" style="color:var(--text);font-weight:600">↗ Open PDF in new tab</a></div>`;
  if (file.previewKind === "text") return `<pre data-preview-text="${esc(file.id)}">Loading...</pre>`;
  return `<p>${esc(file.name)}</p>`;
}
function updatePreviewDecision(fileId) {
  const isTrash = trash.has(fileId);
  const keepBtn = $("previewKeep");
  const trashBtn = $("previewTrash");
  const keepBtn2 = $("previewKeep2");
  const trashBtn2 = $("previewTrash2");
  if (keepBtn) keepBtn.classList.toggle("active", !isTrash);
  if (trashBtn) trashBtn.classList.toggle("active", isTrash);
  if (keepBtn2) keepBtn2.classList.toggle("active", !isTrash);
  if (trashBtn2) trashBtn2.classList.toggle("active", isTrash);
}
function updatePreviewNav() {
  if (!previewContext) return;
  const group = groupById.get(previewContext.groupId);
  if (!group) return;
  const files = group._visibleFiles || group.files;
  const idx = files.findIndex(f => f.id === previewContext.fileId);
  const prevBtn = $("previewPrev");
  const nextBtn = $("previewNext");
  if (prevBtn) prevBtn.disabled = idx <= 0;
  if (nextBtn) nextBtn.disabled = idx < 0 || idx >= files.length - 1;
}
function navigatePreview(dir) {
  if (!previewContext) return;
  const group = groupById.get(previewContext.groupId);
  if (!group) return;
  const files = group._visibleFiles || group.files;
  const idx = files.findIndex(f => f.id === previewContext.fileId);
  if (idx < 0) {
    updatePreviewNav();
    return;
  }
  const next = files[idx + dir];
  if (next) { renderPreview(next.id); setActiveFile(next.id); }
}
function getVisibleFlatFiles() {
  const result = [];
  renderGroups.forEach(group => {
    (group._visibleFiles || []).forEach(file => result.push({ file, groupId: group.id }));
  });
  return result;
}
function navigateMainView(dir) {
  const flat = getVisibleFlatFiles();
  if (!flat.length) return;
  let idx = activeFileId ? flat.findIndex(entry => entry.file.id === activeFileId) : -1;
  const nextIdx = idx < 0 ? (dir > 0 ? 0 : flat.length - 1) : Math.max(0, Math.min(flat.length - 1, idx + dir));
  if (nextIdx === idx && idx >= 0) return;
  const { file, groupId } = flat[nextIdx];
  const groupChanged = groupId !== activeGroupId;
  activeGroupId = groupId;
  activeFileId = file.id;
  refreshActiveGroupHighlight();
  refreshActiveFileHighlight();
  if (groupChanged) {
    renderPane();
  } else {
    renderPaneMeta(file);
  }
  const card = findCard(file.id);
  if (card) {
    card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  } else {
    const groupEl = document.querySelector(`.group[data-group-id="${CSS.escape(groupId)}"]`);
    if (groupEl) groupEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}
function renderPreview(fileId) {
  if (!previewContext) return;
  const file = findFile(fileId);
  if (!file) return;
  const token = ++previewRenderToken;
  const oldVideo = $("previewBody").querySelector("video");
  if (oldVideo) { oldVideo.pause(); oldVideo.src = ""; }
  previewContext.fileId = fileId;
  $("previewTitle").textContent = file.name;
  $("previewMeta").innerHTML = `<div>${esc(file.path)}</div><div>${esc(file.sizeLabel)}</div>`;
  $("previewBody").innerHTML = previewHtml(file);
  updatePreviewDecision(fileId);
  updatePreviewNav();
  const textNode = $("previewBody").querySelector("[data-preview-text]");
  if (textNode) {
    hydratePreviewText(file.id, token, textNode);
  }
}
async function hydratePreviewText(fileId, token, textNode) {
  try {
    const response = await fetch(authUrl(`/text/${urlId(fileId)}`));
    if (!response.ok) throw new Error("Preview unavailable");
    const payload = await response.json();
    if (!previewContext || previewContext.fileId !== fileId || token !== previewRenderToken) return;
    textNode.textContent = typeof payload.text === "string" ? payload.text : "Preview unavailable";
  } catch {
    if (!previewContext || previewContext.fileId !== fileId || token !== previewRenderToken) return;
    textNode.textContent = "Preview unavailable";
  }
}
function openPreview(fileId, groupId, event) {
  if (event) event.stopPropagation();
  const overlay = $("previewOverlay");
  if (overlay.classList.contains("is-open") && previewContext && previewContext.fileId === fileId) {
    closePreview();
    return;
  }
  previewContext = { fileId, groupId };
  overlay.classList.add("is-open");
  renderPreview(fileId);
  $("previewClose").focus();
}
function closePreview(restoreFocus = true) {
  previewRenderToken++;
  const video = $("previewBody").querySelector("video");
  if (video) { video.pause(); video.src = ""; }
  $("previewOverlay").classList.remove("is-open");
  $("previewBody").innerHTML = "";
  previewContext = null;
  if (restoreFocus) $("groups").focus();
}
async function hydrateTextPreviews(root) {
  const nodes = Array.from(root.querySelectorAll("[data-text-id]")).filter(n => !n.dataset.loaded);
  await Promise.all(nodes.map(async (node) => {
    node.dataset.loaded = "1";
    try {
      const response = await fetch(authUrl(`/text/${urlId(node.dataset.textId)}`));
      if (!response.ok) throw new Error("Preview unavailable");
      const payload = await response.json();
      node.textContent = typeof payload.text === "string" ? payload.text : "Preview unavailable";
    } catch { node.textContent = "Preview unavailable"; }
  }));
}
function fileBadgesHtml(file, isTrash) {
  const badges = [`<span class="badge">${esc(file.sizeLabel)}</span>`];
  if (file.isOriginalGuess) badges.push(`<span class="badge original">original</span>`);
  if (file.isHardlink) badges.push(`<span class="badge hardlink" title="Hardlink — trashing this copy won't free disk space until every link to this inode is removed">hardlink</span>`);
  const reason = selectionReasonForFile(file, isTrash);
  if (reason === "manual") badges.push(`<span class="badge manual">manual</span>`);
  else if (reason) badges.push(`<span class="badge">${esc(reason)}</span>`);
  return badges.join("");
}
async function revealFile(fileId, button) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Opening...";
  try {
    const response = await fetch(authUrl(`/reveal/${urlId(fileId)}`));
    if (!response.ok) throw new Error("Reveal failed");
    button.textContent = "Opened";
    setTimeout(() => { button.disabled = false; button.textContent = originalText; }, 1200);
  } catch {
    button.textContent = "Failed";
    setTimeout(() => { button.disabled = false; button.textContent = originalText; }, 1600);
  }
}
function renderGroupContent(el, group) {
  el.dataset.rendered = "1";
  const files = group._visibleFiles || visibleFilesForGroup(group, getFilters());
  const fileCountLabel = files.length === group.files.length ? `${group.files.length} files` : `${files.length} of ${group.files.length} files`;
  const groupTrashCount = files.filter(f => trash.has(f.id)).length;
  const groupTrashBytes = files.reduce((s, f) => trash.has(f.id) ? s + f.size : s, 0);
  const groupImpactHtml = groupTrashCount > 0 ? `<span class="group-impact">${groupTrashCount} marked · ${formatSize(groupTrashBytes)}</span>` : "";
  const cards = files.map((file) => {
    const isTrash = trash.has(file.id);
    const typeLabel = {"image":"IMG","video":"VID","audio":"AUD","pdf":"PDF","text":"TXT"}[file.previewKind] || "—";
    const reasonText = selectionReasonForFile(file, isTrash);
    return `<article class="file ${isTrash ? 'is-trash' : 'is-keep'}" data-file-id="${esc(file.id)}">
      <button class="reveal-btn" data-action="reveal" title="Reveal ${esc(file.name)} in Finder/Explorer" aria-label="Reveal ${esc(file.name)} in Finder/Explorer">Open</button>
      <div class="thumb" role="button" tabindex="0" data-action="preview" data-group-id="${esc(group.id)}">${mediaHtml(file)}</div>
      <div class="meta"><div class="file-type-badge">${esc(typeLabel)}</div><div class="name" title="${esc(file.name)}">${esc(file.name)}</div><div class="path" title="${esc(file.path)}">${esc(file.directory)}</div><div class="badges">${fileBadgesHtml(file, isTrash)}</div><div class="file-size-lv">${esc(file.sizeLabel)}</div><div class="file-reason">${esc(reasonText)}</div><div class="choice"><button class="lv-preview-btn" data-action="preview" data-group-id="${esc(group.id)}" title="Preview">(o)</button><button class="${!isTrash ? 'active keep' : 'keep'}" data-mark="keep">Keep</button><button class="${isTrash ? 'active trash' : 'trash'}" data-mark="trash">Trash</button></div></div>
    </article>`;
  }).join("");
  el.innerHTML = `<div class="group-head"><div class="group-title"><b>${fileCountLabel}</b><span title="${esc(group.hashName + ' ' + group.hash)}" style="cursor:help">${esc(group.hashName)}</span>${groupImpactHtml}</div><div class="group-actions" data-group-id="${esc(group.id)}"><button class="trash-copies" data-group-action="trash">Trash copies</button><button data-group-action="folder">By folder</button><button data-group-action="oldest">Keep oldest</button><button data-group-action="none">Keep all</button></div></div><div class="files">${cards}</div>`;
  el.onmouseenter = () => {
    el.dataset.hovering = "1";
    hydrateVideoMetadata(el).then(() => {
      if (el.dataset.hovering === "1") startGroupVideoThumbCycle(el);
    });
  };
  el.onmouseleave = () => {
    delete el.dataset.hovering;
    stopGroupVideoThumbCycle(el);
  };
  hydrateVideoMetadata(el);
  hydrateTextPreviews(el);
  refreshActiveGroupHighlight();
  refreshActiveFileHighlight();
}
function handleGroupsClick(event) {
  const target = event.target;
  const root = event.currentTarget;
  const markButton = target.closest("[data-mark]");
  if (markButton && root.contains(markButton)) {
    event.stopPropagation();
    const card = markButton.closest("[data-file-id]");
    if (card) mark(card.dataset.fileId, markButton.dataset.mark === "trash");
    return;
  }

  const revealButton = target.closest("[data-action='reveal']");
  if (revealButton && root.contains(revealButton)) {
    event.stopPropagation();
    const card = revealButton.closest("[data-file-id]");
    if (card) revealFile(card.dataset.fileId, revealButton);
    return;
  }

  const previewTarget = target.closest("[data-action='preview']");
  if (previewTarget && root.contains(previewTarget)) {
    event.stopPropagation();
    const card = previewTarget.closest("[data-file-id]");
    if (card) openPreview(card.dataset.fileId, previewTarget.dataset.groupId, event);
    return;
  }

  const groupButton = target.closest("[data-group-action]");
  if (groupButton && root.contains(groupButton)) {
    event.stopPropagation();
    const groupActions = groupButton.closest("[data-group-id]");
    const group = groupActions ? groupById.get(groupActions.dataset.groupId) : null;
    if (!group) return;
    const action = groupButton.dataset.groupAction;
    if (action === "folder") showDirPicker(group, event);
    else markGroup(group, action);
    return;
  }
  // File card click → set active file (and group) for pane
  const fileCard = target.closest(".file[data-file-id]");
  if (fileCard && root.contains(fileCard)) {
    const groupEl = fileCard.closest(".group[data-group-id]");
    if (groupEl) {
      const gid = groupEl.dataset.groupId;
      if (gid !== activeGroupId) {
        activeGroupId = gid;
        refreshActiveGroupHighlight();
        const group = groupById.get(gid);
        const repr = group ? (group.files.find(f => f.isOriginalGuess) || group.files[0]) : null;
        if (repr) renderPanePreview(repr);
        const placeholder = $("panePlaceholder");
        const content = $("paneContent");
        if (placeholder) placeholder.style.display = "none";
        if (content) content.style.display = "flex";
      }
      setActiveFile(fileCard.dataset.fileId);
    }
    return;
  }
  // Group card click (not on any specific element) → set active group
  const groupEl = target.closest(".group[data-group-id]");
  if (groupEl && root.contains(groupEl)) {
    setActiveGroup(groupEl.dataset.groupId);
  }
}
function handleGroupsKeydown(event) {
  if (event.key !== "Enter" && event.key !== " ") return;
  const previewTarget = event.target.closest("[data-action='preview']");
  if (!previewTarget || !event.currentTarget.contains(previewTarget)) return;
  event.preventDefault();
  const card = previewTarget.closest("[data-file-id]");
  if (card) openPreview(card.dataset.fileId, previewTarget.dataset.groupId, event);
}
function updateStats(filters = getFilters()) {
  const totalGroups = allData.groups.length;
  const visibleFiles = renderGroups.reduce((sum, g) => sum + (g._visibleFiles || []).length, 0);
  const trashSummary = getTrashSummary();
  $("groupCount").textContent = `${renderGroups.length} / ${totalGroups}`;
  $("fileCount").textContent = `${visibleFiles} / ${totalFileCount}`;
  $("trashCount").textContent = trashSummary.count;
  $("reclaimSize").textContent = formatSize(trashSummary.bytes);
  updateFilterHint(filters);
  renderFolderSummary(filters);
  updateUndoButton();
}
function getFolderSummary(filters = getFilters()) {
  const folders = new Map();
  renderGroups.forEach(group => {
    (group._visibleFiles || []).forEach(file => {
      const row = folders.get(file.directory) || { directory:file.directory, visible:0, marked:0, bytes:0 };
      row.visible += 1;
      if (trash.has(file.id)) {
        row.marked += 1;
        row.bytes += file.size;
      }
      folders.set(file.directory, row);
    });
  });
  return Array.from(folders.values()).sort((a, b) => b.bytes - a.bytes || b.marked - a.marked || a.directory.localeCompare(b.directory));
}
function focusFolder(directory) {
  $("search").value = $("search").value.trim() === directory ? "" : directory;
  setTrashedOnly(false);
  render();
}
function focusFolderFromButton(button) {
  focusFolder(button.dataset.directory || "");
}
function renderFolderSummary(filters = getFilters()) {
  const el = $("folderSummary");
  if (!el) return;
  const rows = getFolderSummary(filters).filter(r => r.marked > 0).slice(0, 8);
  el.style.display = rows.length ? "flex" : "none";
  const activeDir = $("search").value.trim();
  el.innerHTML = rows.map(row => {
    const active = row.directory === activeDir;
    return `<button class="folder-row${active ? ' is-active' : ''}" data-directory="${esc(row.directory)}" onclick="focusFolderFromButton(this)" title="${active ? 'Click to clear filter' : esc(row.directory)}"><b>${esc(row.directory)}</b><span>${row.marked} of ${row.visible} · ${formatSize(row.bytes)}${active ? '<span class="folder-clear" aria-label="clear"> ×</span>' : ''}</span></button>`;
  }).join("");
}
function showMoveConfirmation() {
  const summary = getTrashDetails();
  if (!requireMoveConfirmation || summary.count === 0) {
    submitSelection(false);
    return;
  }
  $("confirmSummary").textContent = `${summary.count} file(s), ${formatSize(summary.bytes)} reclaimable across ${summary.folders.length} folder(s).`;
  $("confirmFolders").innerHTML = summary.folders.slice(0, 5).map(folder => `<div><b>${esc(folder.directory)}</b>: ${folder.count} file(s), ${formatSize(folder.bytes)}</div>`).join("");
  $("confirmPaths").innerHTML = summary.files.map(file => `<div>${esc(file.path)}</div>`).join("");
  $("confirmOverlay").classList.add("is-open");
  $("confirmMove").focus();
}
function hideMoveConfirmation() {
  $("confirmOverlay").classList.remove("is-open");
  $("finish").focus();
}
function directorySortKey(group, filters) {
  return group._directorySortKey || "";
}
function render() {
  const filters = getFilters();
  const sort = $("sortOrder").value;
  computeRenderState(filters);
  const repr = g => g.files.find(f => f.isOriginalGuess) || g.files[0];
  const dir = sortDescending ? -1 : 1;
  if (sort === "size") renderGroups.sort((a, b) => dir * (repr(a).size - repr(b).size));
  else if (sort === "count") renderGroups.sort((a, b) => dir * (a.files.length - b.files.length));
  else if (sort === "directory") renderGroups.sort((a, b) => dir * directorySortKey(a, filters).localeCompare(directorySortKey(b, filters)));
  else if (sort === "type") renderGroups.sort((a, b) => dir * ((repr(a).previewKind || "other").localeCompare(repr(b).previewKind || "other") || repr(a).path.localeCompare(repr(b).path)));
  else renderGroups.sort((a, b) => dir * repr(a).path.localeCompare(repr(b).path));
  const root = $("groups");
  root.innerHTML = "";
  renderGroups.forEach((group, idx) => {
    const gEl = document.createElement("section");
    gEl.className = "group";
    gEl.dataset.index = idx;
    gEl.dataset.groupId = group.id;
    root.appendChild(gEl);
    observer.observe(gEl);
  });
  if (!renderGroups.length) {
    const message = filtersAreActive(filters) ? "No files match the active filters." : "No duplicate groups to show.";
    root.innerHTML = `<div class="empty-state"><p>${message}</p>${filtersAreActive(filters) ? '<button onclick="clearFilters()">Clear filters</button>' : ""}</div>`;
  }
  updateStats(filters);
  refreshActiveGroupHighlight();
  refreshActiveFileHighlight();
  saveSelections();
}
function saveSelections() {
  if (!sessionId) return;
  try {
    localStorage.setItem(
      "dedup:" + sessionId,
      JSON.stringify({ trash: [...trash], folderRules })
    );
  } catch {}
}
function restoreFromLocalStorage() {
  if (!sessionId) return;
  try {
    const raw = localStorage.getItem("dedup:" + sessionId);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (Array.isArray(data.trash)) {
      trash.clear();
      data.trash.forEach(id => { if (fileById.has(String(id))) trash.add(String(id)); });
    }
    if (Array.isArray(data.folderRules) && data.folderRules.length) {
      folderRules = data.folderRules;
      nextFolderRuleId = folderRules.reduce((m, r) => Math.max(m, Number(r.id || 0) + 1), nextFolderRuleId);
    }
  } catch {}
}
async function submitSelection(cancelled) {
  if (isSubmitting) return;
  isSubmitting = true;
  const finishButton = $("finish");
  const cancelButton = $("cancel");
  const confirmMoveButton = $("confirmMove");
  const confirmBackButton = $("confirmBack");
  const finishText = finishButton.textContent;
  const cancelText = cancelButton.textContent;
  const confirmMoveText = confirmMoveButton.textContent;
  finishButton.disabled = true;
  cancelButton.disabled = true;
  confirmMoveButton.disabled = true;
  confirmBackButton.disabled = true;
  if (cancelled) cancelButton.textContent = "Cancelling...";
  else {
    finishButton.textContent = "Submitting...";
    confirmMoveButton.textContent = "Moving...";
  }
  try {
    const response = await fetch(authUrl("/api/selection"), { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({cancelled, trashIds: Array.from(trash)}) });
    if (response.status === 409) throw new Error("This session was already submitted from another tab. Close this tab.");
    if (!response.ok) throw new Error("Server error");
    document.body.innerHTML = `<main style="display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;"><div><h1>${cancelled ? "Cancelled" : "Done"}</h1><p>Returning to terminal...</p><button onclick="window.close()">Close Window</button></div></main>`;
    setTimeout(() => window.close(), 800);
  } catch (err) {
    isSubmitting = false;
    finishButton.disabled = false;
    cancelButton.disabled = false;
    confirmMoveButton.disabled = false;
    confirmBackButton.disabled = false;
    finishButton.textContent = finishText;
    cancelButton.textContent = cancelText;
    confirmMoveButton.textContent = confirmMoveText;
    alert("Failed to submit selection: " + err.message);
  }
}
function appendClientIndexes(groups) {
  folderRuleRowsCache = null;
  groups.forEach((group) => {
    groupById.set(group.id, group);
    group.files.forEach((file) => {
      file.defaultSortIndex = defaultSortIndex++;
      allFilesList.push(file);
      fileById.set(file.id, file);
    });
    totalFileCount += group.files.length;
  });
}
async function init() {
  let offset = 0;
  const limit = 500;
  let totalGroupCount = 0;
  const subtitleEl = $("subtitle");

  try {
    do {
      let payload;
      let attempts = 0;
      while (true) {
        try {
          const res = await fetch(authUrl(`/api/groups?offset=${offset}&limit=${limit}`));
          if (!res.ok) throw new Error("Failed to load groups");
          payload = await res.json();
          break;
        } catch (fetchErr) {
          attempts++;
          if (attempts >= 2) throw fetchErr;
          await new Promise(r => setTimeout(r, 1000));
        }
      }

      if (offset === 0) {
        requireMoveConfirmation = Boolean(payload.requireMoveConfirmation);
        totalGroupCount = payload.totalGroupCount || 0;
        sessionId = payload.sessionId || null;
        allData = { groups: [] };

        if (totalGroupCount === 0) {
          render();
          return;
        }
      }

      const groups = payload.groups || [];
      allData.groups = allData.groups.concat(groups);
      appendClientIndexes(groups);
      offset += limit;

      if (totalGroupCount > 0 && subtitleEl) {
        subtitleEl.textContent = `Loading groups... ${allData.groups.length} / ${totalGroupCount}`;
      }
    } while (offset < totalGroupCount);
  } catch (err) {
    if (subtitleEl) subtitleEl.textContent = "Error loading groups: " + err.message;
    return;
  }

  allFilesList.forEach(f => { if (f.defaultTrash) trash.add(f.id); });
  restoreFromLocalStorage();
  setTrashedOnly(false);
  if (subtitleEl) subtitleEl.textContent = "";
  try { paneOpen = localStorage.getItem("dedupPaneOpen") !== "false"; } catch {}
  try { const lv = localStorage.getItem("dedupListView"); if (lv !== null) listViewMode = lv === "true"; } catch {}
  applyPaneOpen();
  applyListView();
  updateFolderRulesButton();
  initPaneResizer();
  $("togglePane").addEventListener("click", togglePane);
  $("toggleListView").addEventListener("click", toggleListView);
  $("folderRules").addEventListener("click", openFolderRules);
  $("folderRulesClose").addEventListener("click", closeFolderRules);
  $("folderRulesClear").addEventListener("click", clearFolderRules);
  $("folderRulesApply").addEventListener("click", applyFolderRules);
  $("folderRulesSearch").addEventListener("input", renderFolderRulesPanel);
  $("folderRulesOverlay").addEventListener("click", (event) => { if (event.target.id === "folderRulesOverlay") closeFolderRules(); });
  $("folderRulesActive").addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-rule]");
    if (button) removeFolderRule(button.dataset.removeRule);
  });
  $("folderRulesList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-add-rule]");
    if (!button) return;
    const rows = $("folderRulesList")._folderRuleRows || [];
    const row = rows[Number(button.dataset.dirIndex)];
    if (!row) return;
    const subtree = Boolean($("folderRulesList").querySelector(`[data-rule-subtree="${button.dataset.dirIndex}"]`)?.checked);
    addFolderRule(row.directory, button.dataset.addRule, subtree);
  });
  $("listViewHeader").addEventListener("click", (e) => {
    const col = e.target.closest("[data-lv-sort]");
    if (!col) return;
    const newSort = col.dataset.lvSort;
    if ($("sortOrder").value === newSort) {
      sortDescending = !sortDescending;
    } else {
      $("sortOrder").value = newSort;
      sortDescending = false;
    }
    render();
    updateListViewHeader();
  });
  $("search").addEventListener("input", () => { clearTimeout(searchTimeout); searchTimeout = setTimeout(render, 150); });
  $("mediaFilter").addEventListener("change", render);
  $("sortOrder").addEventListener("change", () => { sortDescending = false; render(); updateListViewHeader(); });
  $("collapseClean").addEventListener("click", () => { setTrashedOnly(!trashedOnly); render(); });
  $("undo").addEventListener("click", undoLastAction);
  $("finish").addEventListener("click", showMoveConfirmation);
  $("cancel").addEventListener("click", () => submitSelection(true));
  $("groups").addEventListener("click", handleGroupsClick);
  $("groups").addEventListener("keydown", handleGroupsKeydown);
  $("confirmMove").addEventListener("click", () => submitSelection(false));
  $("confirmBack").addEventListener("click", hideMoveConfirmation);
  $("confirmOverlay").addEventListener("click", (event) => { if (event.target.id === "confirmOverlay") hideMoveConfirmation(); });
  $("previewClose").addEventListener("click", () => closePreview());
  $("previewOverlay").addEventListener("click", (event) => { if (event.target.id === "previewOverlay") closePreview(); });
  $("previewPrev").addEventListener("click", () => navigatePreview(-1));
  $("previewNext").addEventListener("click", () => navigatePreview(1));
  $("previewKeep").addEventListener("click", () => { if (previewContext) { mark(previewContext.fileId, false); } });
  $("previewTrash").addEventListener("click", () => { if (previewContext) { mark(previewContext.fileId, true); } });
  $("previewKeep2").addEventListener("click", () => { if (previewContext) { mark(previewContext.fileId, false); } });
  $("previewTrash2").addEventListener("click", () => { if (previewContext) { mark(previewContext.fileId, true); } });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if ($("previewOverlay").classList.contains("is-open")) closePreview();
      else if ($("folderRulesOverlay").classList.contains("is-open")) closeFolderRules();
      else if ($("confirmOverlay").classList.contains("is-open")) hideMoveConfirmation();
    }
    if (isTextInputTarget(event.target)) return;
    if ($("previewOverlay").classList.contains("is-open")) {
      if (event.key === "ArrowLeft") { event.preventDefault(); navigatePreview(-1); }
      if (event.key === "ArrowRight") { event.preventDefault(); navigatePreview(1); }
    } else if (!$("confirmOverlay").classList.contains("is-open") && !$("folderRulesOverlay").classList.contains("is-open")) {
      if (event.key === "ArrowDown") { event.preventDefault(); navigateMainView(1); }
      if (event.key === "ArrowUp") { event.preventDefault(); navigateMainView(-1); }
    }
  });
  render();
}
init();
</script>
</body>
</html>""")


class BrowserSelectionState:
    def __init__(self, duplicate_groups, require_move_confirmation=False):
        payload = build_browser_payload(duplicate_groups)
        self.groups = payload["groups"]
        self.require_move_confirmation = require_move_confirmation
        self.session_id = os.urandom(16).hex()
        self._submitted = False
        self._path_by_id = {
            file_info["id"]: file_info["path"]
            for group in self.groups
            for file_info in group["files"]
        }
        self.selected_paths = []
        self.done = threading.Event()
        self.thumbnail_cache = ThumbnailCache()
        self.cache_lock = threading.Lock()
        self.thumbnail_inflight = {}
        self._groups_json = json.dumps(
            {
                "groups": self.groups,
                "requireMoveConfirmation": self.require_move_confirmation,
                "sessionId": self.session_id,
                "totalGroupCount": len(self.groups),
            },
            separators=(",", ":"),
        ).encode("utf-8")


def sanitize_browser_trash_selection(groups, trash_ids):
    trash_ids = {str(file_id) for file_id in trash_ids}
    selected_paths = []
    for group in groups:
        group_files = group["files"]
        group_ids = {file_info["id"] for file_info in group_files}
        selected_ids = group_ids & trash_ids
        if len(selected_ids) >= len(group_ids):
            original = next((file_info for file_info in group_files if file_info["isOriginalGuess"]), group_files[0])
            selected_ids.discard(original["id"])
        for file_info in group_files:
            if file_info["id"] in selected_ids:
                selected_paths.append(file_info["path"])
    return selected_paths


class _BaseHandler(BaseHTTPRequestHandler):
    def log_message(self, format_text, *args):
        return

    def send_error(self, code, message=None, explain=None):
        try:
            super().send_error(code, message=message, explain=explain)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def send_bytes(self, status, body, content_type, cacheable=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600" if cacheable else "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def send_json(self, payload, status=200):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_bytes(status, body, "application/json")

    def read_json_body(self):
        raw = self.headers.get("Content-Length", "0") or "0"
        try:
            length = int(raw)
        except (ValueError, TypeError):
            self.send_json({"ok": False}, 400)
            return None, False
        if length < 0:
            self.send_json({"ok": False}, 400)
            return None, False
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")), True
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json({"ok": False}, 400)
            return None, False

    def serve_file_with_range(self, path, content_type):
        try:
            size = os.path.getsize(path)
            range_header = self.headers.get("Range")

            if range_header:
                m = re.match(r"^bytes=(\d+)-(\d*)$", range_header.strip())
                if m:
                    start = int(m.group(1))
                    end_str = m.group(2)

                    if start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", "bytes */" + str(size))
                        self.send_header("Accept-Ranges", "bytes")
                        self.end_headers()
                        return

                    if end_str == "":
                        end = size - 1
                    else:
                        end = int(end_str)
                        if end >= size:
                            end = size - 1

                    content_length = end - start + 1

                    self.send_response(206)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(content_length))
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()

                    with open(path, "rb") as file_obj:
                        file_obj.seek(start)
                        remaining = content_length
                        while remaining > 0:
                            chunk_size = RANGE_SERVE_CHUNK_BYTES if remaining > RANGE_SERVE_CHUNK_BYTES else remaining
                            data = file_obj.read(chunk_size)
                            if not data:
                                break
                            self.wfile.write(data)
                            remaining -= len(data)
                    return

            # No Range header or unparseable Range — serve full file
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with open(path, "rb") as file_obj:
                shutil.copyfileobj(file_obj, self.wfile)
        except OSError as exc:
            if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                return
            if not self.wfile.closed:
                try:
                    self.send_error(404)
                except OSError:
                    pass


def make_browser_handler(state):
    class BrowserSelectionHandler(_BaseHandler):
        def is_authorized(self, parsed):
            query = urllib.parse.parse_qs(parsed.query)
            token = query.get("token", [""])[0] or self.headers.get("X-Dedup-Session", "")
            return bool(token) and token == state.session_id

        def reject_unauthorized(self):
            self.send_json({"ok": False, "reason": "unauthorized"}, 403)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if not self.is_authorized(parsed):
                self.reject_unauthorized()
                return
            if parsed.path == "/":
                self.send_bytes(200, build_browser_html().encode("utf-8"), "text/html; charset=utf-8")
            elif parsed.path == "/api/groups":
                query = urllib.parse.parse_qs(parsed.query)
                offset_str = query.get("offset", ["0"])[0]
                limit_str = query.get("limit", [str(DEFAULT_PAGINATION_LIMIT)])[0]
                try:
                    offset = max(0, int(offset_str))
                except (TypeError, ValueError):
                    offset = 0
                try:
                    limit = min(MAX_PAGINATION_LIMIT, max(1, int(limit_str)))
                except (TypeError, ValueError):
                    limit = DEFAULT_PAGINATION_LIMIT

                if offset == 0 and limit >= len(state.groups) and not query.get("offset") and not query.get("limit"):
                    self.send_bytes(200, state._groups_json, "application/json")
                else:
                    sliced = state.groups[offset:offset + limit]
                    payload = {"groups": sliced, "totalGroupCount": len(state.groups)}
                    if offset == 0:
                        payload["requireMoveConfirmation"] = state.require_move_confirmation
                        payload["sessionId"] = state.session_id
                    self.send_json(payload)
            elif parsed.path.startswith("/media/"):
                self.serve_media(urllib.parse.unquote(parsed.path[7:]))
            elif parsed.path.startswith("/thumb/"):
                query = urllib.parse.parse_qs(parsed.query)
                try:
                    thumb_index = int(query.get("i", ["0"])[0])
                except (TypeError, ValueError):
                    thumb_index = 0
                self.serve_thumbnail(urllib.parse.unquote(parsed.path[7:]), thumb_index)
            elif parsed.path.startswith("/meta/"):
                self.serve_meta(urllib.parse.unquote(parsed.path[6:]))
            elif parsed.path.startswith("/text/"):
                self.serve_text(urllib.parse.unquote(parsed.path[6:]))
            elif parsed.path.startswith("/pdf/"):
                self.serve_pdf(urllib.parse.unquote(parsed.path[5:]))
            elif parsed.path.startswith("/reveal/"):
                self.reveal_file(urllib.parse.unquote(parsed.path[8:]))
            else:
                self.send_error(404)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            if not self.is_authorized(parsed):
                self.reject_unauthorized()
                return
            if parsed.path != "/api/selection":
                self.send_error(404)
                return
            payload, ok = self.read_json_body()
            if not ok:
                return
            with state.cache_lock:
                if state._submitted:
                    self.send_json({"ok": False, "reason": "already-submitted"}, 409)
                    return
                state.selected_paths = [] if payload.get("cancelled") else sanitize_browser_trash_selection(state.groups, payload.get("trashIds", []))
                state._submitted = True
                state.done.set()
            self.send_json({"ok": True, "selected": len(state.selected_paths)})

        def reveal_file(self, file_id):
            path = state._path_by_id.get(file_id)
            if not path:
                self.send_error(404)
                return
            try:
                subprocess.Popen(build_reveal_command(path))
                self.send_json({"ok": True})
            except OSError:
                self.send_error(500)

        def serve_media(self, file_id):
            path = state._path_by_id.get(file_id)
            if not path or get_media_kind(path) not in ("image", "video", "audio"):
                self.send_error(404)
                return
            content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
            self.serve_file_with_range(path, content_type)

        def serve_pdf(self, file_id):
            path = state._path_by_id.get(file_id)
            extension = os.path.splitext(path or "")[1].lower()
            if not path or extension not in PDF_EXTENSIONS:
                self.send_error(404)
                return
            self.serve_file_with_range(path, "application/pdf")

        def serve_thumbnail(self, file_id, thumb_index=0):
            path = state._path_by_id.get(file_id)
            if not path:
                self.send_error(404)
                return
            body = get_thumbnail_threadsafe(
                path,
                state.thumbnail_cache,
                state.cache_lock,
                state.thumbnail_inflight,
                thumb_index=max(0, thumb_index),
            )
            if not body:
                self.send_error(404)
                return
            self.send_bytes(200, body, THUMBNAIL_CONTENT_TYPE)

        def serve_meta(self, file_id):
            path = state._path_by_id.get(file_id)
            if not path:
                self.send_error(404)
                return
            media_kind = get_media_kind(path)
            payload = {"mediaKind": media_kind}
            if media_kind == "video":
                duration = get_video_duration(path)
                payload["thumbnailCount"] = get_video_thumbnail_count(duration)
                if duration:
                    payload["duration"] = duration
                info = get_media_info(path)
                if info:
                    payload.update(info)
            elif media_kind == "audio":
                duration = get_video_duration(path)
                if duration:
                    payload["duration"] = duration
                info = get_media_info(path)
                if info:
                    payload.update(info)
            elif media_kind == "image":
                info = get_media_info(path)
                if info:
                    payload.update(info)
                exif = get_exif_info(path)
                if exif:
                    payload["exif"] = exif
            self.send_json(payload)

        def serve_text(self, file_id):
            path = state._path_by_id.get(file_id)
            if not path or not is_readable_text_file(path):
                self.send_error(404)
                return
            text = read_text_preview(path)
            if text is None:
                self.send_error(404)
                return
            self.send_json({"text": text})

    return BrowserSelectionHandler


def focus_terminal():
    if CURRENT_OS == OS_MACOS:
        apps = ["iTerm2", "iTerm", "Terminal", "Code", "Cursor"]
        term = os.environ.get("TERM_PROGRAM", "")
        if "iterm" in term.lower():
            apps.insert(0, "iTerm")
        if "apple_terminal" in term.lower():
            apps.insert(0, "Terminal")
        if "code" in term.lower():
            apps.insert(0, "Code")
        for app in apps:
            script = f'if application "{app}" is running then tell application "{app}" to activate'
            try:
                subprocess.run(["osascript", "-e", script], capture_output=True, check=False, timeout=2)
            except Exception:
                pass
    elif CURRENT_OS == OS_WINDOWS:
        try:
            ps_script = f"""
$wshell = New-Object -ComObject WScript.Shell
$wshell.AppActivate({os.getppid()})
"""
            subprocess.run(["powershell.exe", "-Command", ps_script], capture_output=True, check=False, timeout=2)
        except Exception:
            pass


def _bind_server(handler, port):
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        print(f"\nNOTICE: Port {port} is busy — using a random port. Check the URL printed below.\n", flush=True)
        return ThreadingHTTPServer(("127.0.0.1", 0), handler)


def _run_browser_session(state, handler_factory, url_label, cleanup=None, port=7979):
    server = _bind_server(handler_factory(state), port)
    token = getattr(state, "session_id", "")
    query = f"?token={urllib.parse.quote(token)}" if token else ""
    url = f"http://127.0.0.1:{server.server_port}/{query}"
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"\n{url_label}")
    print(f"  → {url}")
    print("  (If the browser did not open automatically, copy the URL above.)")
    print("Press Ctrl+C to cancel.", flush=True)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        state.done.wait()
    except KeyboardInterrupt:
        state.selected_paths = []
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
        if cleanup:
            cleanup()
    focus_terminal()
    print(flush=True)
    return state.selected_paths


def select_files_in_browser(duplicate_groups, require_move_confirmation=False, port=7979):
    state = BrowserSelectionState(duplicate_groups, require_move_confirmation)
    # Warm thumbnail cache in background so first paint is instant.
    threading.Thread(
        target=_warm_thumbnails,
        args=(state.thumbnail_cache, state.cache_lock, state.thumbnail_inflight, state.groups),
        daemon=True,
    ).start()
    def cleanup():
        with state.cache_lock:
            state.thumbnail_cache.clear()
            state.thumbnail_inflight.clear()
        get_video_duration.cache_clear()
    return _run_browser_session(state, make_browser_handler, "Browser review UI", cleanup=cleanup, port=port)


def smoke_test_browser_server():
    group = DuplicateGroup(
        "smoke",
        (
            FileInfo("/tmp/dedup-smoke-a.txt", 1, 1),
            FileInfo("/tmp/dedup-smoke-b.txt", 1, 2),
        ),
        FULL_HASH_NAME,
    )
    state = BrowserSelectionState([group])
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_browser_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = (
            f"http://127.0.0.1:{server.server_port}/api/groups"
            f"?offset=0&limit=1&token={urllib.parse.quote(state.session_id)}"
        )
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("totalGroupCount") != 1:
            raise RuntimeError("browser smoke test returned unexpected payload")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    print("Browser server smoke test passed.")
    return 0


def build_empty_dirs_html():
    # HTML assembled from _SHARED_CSS + _SHARED_JS (above) plus page-specific content below.
    return (
"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Empty Folder Cleanup</title>
<style>"""
+ _SHARED_CSS
+ """
/* ── page-specific ─────────────────────────────────── */
body { margin: 0; font: 13px/1.5 "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; display: flex; flex-direction: column; }
header { position: sticky; top: 0; z-index: 10; display: flex; gap: 12px; align-items: center; justify-content: space-between; padding: 10px 16px; background: var(--panel); border-bottom: 1px solid var(--line); }
h1 { margin: 0; font-size: 15px; }
.toolbar { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
button.danger-outline { color: var(--danger); border-color: var(--danger-border); background: var(--danger-bg); }
.content { max-width: 960px; margin: 0 auto; padding: 16px; width: 100%; }
.summary { margin-bottom: 12px; color: var(--muted); font-size: 12px; }
.tree-list { display: grid; gap: 8px; }
.tree-group { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.root-row { display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px; cursor: pointer; user-select: none; }
.root-row:hover { background: var(--surface); }
.root-row.sel { background: var(--danger-bg); }
.root-row input[type=checkbox] { margin-top: 2px; flex-shrink: 0; accent-color: var(--danger); width: 15px; height: 15px; cursor: pointer; }
.node-info { min-width: 0; flex: 1; }
.node-name { font-weight: 600; font-size: 13px; word-break: break-all; }
.node-full-path { font-size: 11px; color: var(--muted); word-break: break-all; margin-top: 2px; }
.node-badge { display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 999px; background: var(--surface); color: var(--muted); margin-left: 6px; vertical-align: middle; font-weight: 400; }
.children-block { border-top: 1px solid var(--line); background: var(--bg); }
.child-row { display: flex; align-items: center; gap: 8px; padding: 6px 12px; cursor: pointer; user-select: none; border-left: 3px solid transparent; }
.child-row:hover { background: var(--surface); }
.child-row.sel { background: var(--danger-bg); border-left-color: var(--danger-border); }
.child-row input[type=checkbox] { flex-shrink: 0; accent-color: var(--danger); width: 14px; height: 14px; cursor: pointer; }
.child-indent { flex-shrink: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; color: var(--line); white-space: pre; line-height: 1; }
.child-name { font-size: 12px; font-weight: 500; word-break: break-all; }
.empty-state { padding: 40px 20px; text-align: center; color: var(--muted); }
.modal { width: min(520px, 100%); background: var(--panel); border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 20px 60px rgba(0,0,0,.18); padding: 20px; display: grid; gap: 14px; }
.confirm-list { max-height: 220px; overflow: auto; border: 1px solid var(--line); border-radius: 6px; padding: 8px; background: var(--surface); font-size: 11px; color: var(--muted); }
.confirm-list div { word-break: break-all; margin-bottom: 4px; }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; }
</style>
</head>
<body>
<header>
  <div><h1>Empty Folder Cleanup</h1><div id="subtitle" style="font-size:12px;color:var(--muted)"></div></div>
  <div class="toolbar">
    <button onclick="selectAll()">Select All</button>
    <button onclick="selectNone()">Select None</button>
    <button class="danger-outline" id="confirmBtn" onclick="openConfirm()" disabled>Delete Selected</button>
    <button onclick="cancel()">Cancel</button>
  </div>
</header>
<div class="content">
  <div class="summary" id="summary"></div>
  <div class="tree-list" id="dirList"></div>
</div>

<div class="modal-backdrop" id="confirmModal">
  <div class="modal">
    <h2>Confirm Deletion</h2>
    <p id="confirmNote"></p>
    <div class="confirm-list" id="confirmList"></div>
    <div class="modal-actions">
      <button onclick="closeConfirm()">Back</button>
      <button class="primary" style="background:var(--danger);border-color:var(--danger)" onclick="doConfirm()">Move to Trash</button>
    </div>
  </div>
</div>

<script>"""
+ _SHARED_JS
+ """
let dirs = [];
let selected = new Set(); // Set<path string>

async function init() {
  const res = await fetch(authUrl('/api/empty-dirs'));
  const data = await res.json();
  dirs = data.dirs || [];
  document.getElementById('subtitle').textContent =
    dirs.length + ' empty folder' + (dirs.length === 1 ? '' : 's') + ' found';
  render();
}

// ── Tree ────────────────────────────────────────────────────────────────────

function buildTree(paths) {
  const pathSet = new Set(paths);
  const childrenMap = new Map();
  const roots = [];
  for (const p of paths) {
    const anc = nearestAncestor(p, pathSet);
    if (anc) {
      if (!childrenMap.has(anc)) childrenMap.set(anc, []);
      childrenMap.get(anc).push(p);
    } else {
      roots.push(p);
    }
  }
  return { roots, childrenMap };
}

function nearestAncestor(path, pathSet) {
  let p = path;
  while (true) {
    const i = p.lastIndexOf('/');
    if (i <= 0) return null;
    p = p.slice(0, i);
    if (!p) return null;
    if (pathSet.has(p)) return p;
  }
}

// Flatten a subtree into [{path, prefix}] rows with ASCII tree connectors.
function collectChildren(path, childrenMap, continuationPrefix, result) {
  const children = childrenMap.get(path) || [];
  children.forEach((c, i) => {
    const isLast = i === children.length - 1;
    result.push({ path: c, prefix: continuationPrefix + (isLast ? '└─ ' : '├─ ') });
    collectChildren(c, childrenMap, continuationPrefix + (isLast ? '   ' : '│  '), result);
  });
}

function effectiveSelection() {
  const sorted = [...selected].sort((a, b) => a.split('/').length - b.split('/').length);
  const accepted = [];
  for (const p of sorted) {
    if (!accepted.some(a => p.startsWith(a + '/'))) accepted.push(p);
  }
  return accepted;
}

// ── Rendering ───────────────────────────────────────────────────────────────

function renderGroup(root, childrenMap) {
  const rootSel = selected.has(root);
  const rootName = root.split('/').filter(Boolean).pop() || root;
  const rootChildren = childrenMap.get(root) || [];
  const badge = rootChildren.length
    ? `<span class="node-badge">${rootChildren.length} inside</span>` : '';

  const flatChildren = [];
  collectChildren(root, childrenMap, '', flatChildren);

  const childRows = flatChildren.map(({ path, prefix }) => {
    const sel = selected.has(path);
    const name = path.split('/').filter(Boolean).pop() || path;
    const ch = childrenMap.get(path) || [];
    const cbadge = ch.length ? `<span class="node-badge">${ch.length} inside</span>` : '';
    return `<div class="child-row${sel ? ' sel' : ''}" data-path="${esc(path)}" onclick="handleRowClick(event)">
      <input type="checkbox" data-path="${esc(path)}" ${sel ? 'checked' : ''} onchange="togglePath(this.dataset.path, this.checked)" onclick="event.stopPropagation()">
      <span class="child-indent">${esc(prefix)}</span>
      <span class="child-name">${esc(name)}/${cbadge}</span>
    </div>`;
  }).join('');

  return `<div class="tree-group">
    <div class="root-row${rootSel ? ' sel' : ''}" data-path="${esc(root)}" onclick="handleRowClick(event)">
      <input type="checkbox" data-path="${esc(root)}" ${rootSel ? 'checked' : ''} onchange="togglePath(this.dataset.path, this.checked)" onclick="event.stopPropagation()">
      <div class="node-info">
        <div class="node-name">${esc(rootName)}/${badge}</div>
        <div class="node-full-path">${esc(root)}</div>
      </div>
    </div>
    ${childRows ? `<div class="children-block">${childRows}</div>` : ''}
  </div>`;
}

function render() {
  const list = document.getElementById('dirList');
  if (!dirs.length) {
    list.innerHTML = '<div class="empty-state">No empty folders found.</div>';
    return;
  }
  const { roots, childrenMap } = buildTree(dirs);
  list.innerHTML = roots.map(r => renderGroup(r, childrenMap)).join('');

  const n = selected.size;
  const eff = effectiveSelection().length;
  let summary = n === 0
    ? dirs.length + ' folder' + (dirs.length === 1 ? '' : 's') + ' found — none selected'
    : n + ' selected';
  if (n > 0 && eff < n) summary += ` · ${eff} will be trashed (${n - eff} covered by a selected parent)`;
  document.getElementById('summary').textContent = summary;
  document.getElementById('confirmBtn').disabled = n === 0;
}

// ── Selection ───────────────────────────────────────────────────────────────

// Handles clicks on the row area (not the checkbox — that uses onchange).
function handleRowClick(event) {
  if (event.target.tagName === 'INPUT') return;
  const path = event.currentTarget.dataset.path;
  togglePath(path, !selected.has(path));
}

function togglePath(path, checked) {
  if (checked) selected.add(path); else selected.delete(path);
  render();
}

function selectAll() { dirs.forEach(d => selected.add(d)); render(); }
function selectNone() { selected.clear(); render(); }

// ── Confirm modal ───────────────────────────────────────────────────────────

function openConfirm() {
  const eff = effectiveSelection();
  const skipped = selected.size - eff.length;
  document.getElementById('confirmNote').textContent = skipped > 0
    ? `Move ${eff.length} folder${eff.length === 1 ? '' : 's'} to the Trash? (${skipped} covered by parent and omitted.)`
    : `Move ${eff.length} folder${eff.length === 1 ? '' : 's'} to the Trash?`;
  document.getElementById('confirmList').innerHTML = eff.map(p => `<div>${esc(p)}</div>`).join('');
  document.getElementById('confirmModal').classList.add('is-open');
}

function closeConfirm() {
  document.getElementById('confirmModal').classList.remove('is-open');
}

function showDone(label) {
  document.body.innerHTML = `<main style="display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;"><div><h1>${label}</h1><p>Returning to terminal...</p><button onclick="window.close()">Close Tab</button></div></main>`;
  setTimeout(() => window.close(), 800);
}

async function doConfirm() {
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  closeConfirm();
  await fetch(authUrl('/api/empty-dirs-selection'), {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({selected: effectiveSelection()}),
  });
  showDone('Done');
}

async function cancel() {
  await fetch(authUrl('/api/empty-dirs-selection'), {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({cancelled: true}),
  });
  showDone('Cancelled');
}

init();
</script>
</body>
</html>""")


class EmptyDirSelectionState:
    def __init__(self, dirs):
        self.dirs = dirs
        self.session_id = os.urandom(16).hex()
        self.selected_paths = []
        self.done = threading.Event()


def make_empty_dir_handler(state):
    dirs_set = set(state.dirs)

    class EmptyDirHandler(_BaseHandler):
        def is_authorized(self, parsed):
            query = urllib.parse.parse_qs(parsed.query)
            token = query.get("token", [""])[0] or self.headers.get("X-Dedup-Session", "")
            return bool(token) and token == state.session_id

        def reject_unauthorized(self):
            self.send_json({"ok": False, "reason": "unauthorized"}, 403)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if not self.is_authorized(parsed):
                self.reject_unauthorized()
                return
            if parsed.path == "/":
                self.send_bytes(200, build_empty_dirs_html().encode("utf-8"), "text/html; charset=utf-8")
            elif parsed.path == "/api/empty-dirs":
                self.send_json({"dirs": state.dirs})
            else:
                self.send_error(404)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            if not self.is_authorized(parsed):
                self.reject_unauthorized()
                return
            if parsed.path != "/api/empty-dirs-selection":
                self.send_error(404)
                return
            payload, ok = self.read_json_body()
            if not ok:
                return
            if payload.get("cancelled"):
                state.selected_paths = []
            else:
                state.selected_paths = _deduplicate_by_ancestry(
                    [p for p in payload.get("selected", []) if p in dirs_set]
                )
            state.done.set()
            self.send_json({"ok": True})

    return EmptyDirHandler


def select_empty_dirs_in_browser(dirs, port=7979):
    state = EmptyDirSelectionState(dirs)
    return _run_browser_session(state, make_empty_dir_handler, "Empty folder review UI", port=port)


def build_expected_hashes(groups):
    expected = {}
    for group in groups:
        for info in group.files:
            expected[info.path] = (info, group)
    return expected


def exact_hash_paths_for_selection(files, groups):
    selected = set(files)
    expected = build_expected_hashes(groups)
    needed = set()
    for path in selected:
        entry = expected.get(path)
        if not entry:
            continue
        _info, group = entry
        if group.hash_name == FULL_HASH_NAME:
            continue
        needed.add(path)
        for peer in group.files:
            if peer.path not in selected:
                needed.add(peer.path)
    return needed


class FullHashPreloader:
    """Opportunistically full-hash fast-mode candidates during browser review."""

    def __init__(self, groups):
        self.pending = {}
        for group in groups:
            if group.hash_name == FULL_HASH_NAME:
                continue
            for info in group.files:
                self.pending.setdefault(info.path, None)
        self.cache = {}
        self.failed = set()
        self.in_flight = set()
        self.allowed_paths = None
        self.stop_all = False
        self.condition = threading.Condition()
        self.thread = None

    def _compute_full_hash_entry(self, path):
        try:
            before = os.stat(path, follow_symlinks=False)
        except OSError:
            return None
        if not stat.S_ISREG(before.st_mode) or _is_cloud_placeholder(before, path):
            return None
        if self._should_abort_path(path):
            return None
        hasher = make_hasher(FULL_HASH_NAME)
        try:
            with open(path, "rb") as file_obj:
                while True:
                    if self._should_abort_path(path):
                        return None
                    chunk = file_obj.read(FULL_HASH_CHUNK_BYTES)
                    if not chunk:
                        break
                    hasher.update(chunk)
        except OSError:
            return None
        try:
            after = os.stat(path, follow_symlinks=False)
        except OSError:
            return None
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or not stat.S_ISREG(after.st_mode)
            or _is_cloud_placeholder(after, path)
            or self._should_abort_path(path)
        ):
            return None
        return before.st_size, before.st_mtime_ns, hasher.hexdigest()

    def _entry_digest_if_current(self, path, entry):
        if not entry:
            return None
        expected_size, expected_mtime_ns, digest = entry
        try:
            current = os.stat(path, follow_symlinks=False)
        except OSError:
            return None
        if current.st_size != expected_size or current.st_mtime_ns != expected_mtime_ns:
            return None
        return digest

    def _should_abort_path(self, path):
        # Reads stop_all and allowed_paths without the lock; both flags only
        # transition one way (False→True, None→set), so a stale read at most
        # processes one extra chunk before the next iteration catches it.
        if self.stop_all:
            return True
        allowed = self.allowed_paths
        return allowed is not None and path not in allowed

    def start(self):
        if not self.pending or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="dedup-fullhash-preloader", daemon=True)
        self.thread.start()

    def restrict_to(self, paths):
        with self.condition:
            allowed = set(paths)
            self.allowed_paths = allowed
            for path in list(self.pending):
                if path not in allowed:
                    del self.pending[path]
            self.condition.notify_all()

    def stop(self):
        with self.condition:
            self.stop_all = True
            self.pending.clear()
            self.condition.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def _next_path_locked(self):
        # Before restrict_to: allowed_paths is None so any path is eligible.
        # After restrict_to: pending only contains allowed paths (restrict_to
        # pruned the rest), so the first entry is always eligible.
        path = next(iter(self.pending), None)
        if path is None:
            return None
        del self.pending[path]
        self.in_flight.add(path)
        return path

    def _run(self):
        while True:
            with self.condition:
                if self.stop_all:
                    return
                path = self._next_path_locked()
                if path is None:
                    if self.allowed_paths is not None or not self.pending:
                        return
                    self.condition.wait(timeout=0.2)
                    continue
            entry = self._compute_full_hash_entry(path)
            with self.condition:
                if entry:
                    self.cache[path] = entry
                else:
                    self.failed.add(path)
                self.in_flight.discard(path)
                self.condition.notify_all()

    def get(self, path):
        while True:
            with self.condition:
                if path in self.cache:
                    digest = self._entry_digest_if_current(path, self.cache[path])
                    if digest:
                        return digest
                    self.cache.pop(path, None)
                if path in self.failed:
                    return None
                if path in self.in_flight:
                    self.condition.wait(timeout=0.2)
                    continue
                self.pending.pop(path, None)
                self.in_flight.add(path)
                break
        entry = self._compute_full_hash_entry(path)
        with self.condition:
            if entry:
                self.cache[path] = entry
            else:
                self.failed.add(path)
            self.in_flight.discard(path)
            self.condition.notify_all()
        return entry[2] if entry else None


def revalidate_file(path, expected_size, expected_mtime_ns, expected_hash, expected_hash_name):
    try:
        file_stat = os.stat(path, follow_symlinks=False)
    except OSError:
        return False, "missing/unreadable"
    if not stat.S_ISREG(file_stat.st_mode):
        return False, "not a regular file"
    if file_stat.st_size != expected_size:
        return False, "size changed"
    if expected_hash_name == FULL_HASH_NAME:
        current_hash = get_full_content_hash(path)
    else:
        current_hash = get_fast_multichunk_hash(path, expected_size)
    if current_hash != expected_hash:
        return False, "hash changed"
    return True, ""


def _cached_full_hash(path, cache, full_hash_reader=None):
    if path not in cache:
        reader = full_hash_reader or get_full_content_hash
        cache[path] = reader(path)
    return cache[path]


def revalidate_selected_file_exact(path, info, group, selected_paths, full_hash_cache, full_hash_reader=None):
    valid, reason = revalidate_file(
        path,
        info.size,
        info.mtime_ns,
        group.hash,
        group.hash_name,
    )
    if not valid:
        return False, reason
    if group.hash_name == FULL_HASH_NAME:
        return True, ""

    selected_hash = _cached_full_hash(path, full_hash_cache, full_hash_reader)
    if not selected_hash:
        return False, "full hash unavailable"

    for peer in group.files:
        if peer.path == path or peer.path in selected_paths:
            continue
        peer_valid, _peer_reason = revalidate_file(
            peer.path,
            peer.size,
            peer.mtime_ns,
            group.hash,
            group.hash_name,
        )
        if not peer_valid:
            continue
        peer_hash = _cached_full_hash(peer.path, full_hash_cache, full_hash_reader)
        if peer_hash and peer_hash == selected_hash:
            return True, ""
    return False, "no exact kept duplicate"


def load_send_to_trash():
    from send2trash import send2trash
    return send2trash


def is_macos_external_volume_path(path):
    normalized = os.path.normpath(os.path.abspath(path))
    parts = normalized.split(os.sep)
    return len(parts) > 3 and parts[1] == "Volumes"


def move_to_trash_with_cmd(path):
    _ensure_no_symlink_replacement(path)
    if not MACOS_TRASH_CMD:
        raise OSError("trash command not found")
    completed = subprocess.run(
        [MACOS_TRASH_CMD, path],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or f"trash exited with {completed.returncode}").strip()
        raise OSError(detail)


def get_macos_volume_root(path):
    """Return /Volumes/<name> for a /Volumes/<name>/... path, else None."""
    parts = os.path.normpath(os.path.abspath(path)).split(os.sep)
    if len(parts) >= 3 and parts[1] == "Volumes":
        return os.sep + os.sep.join(parts[1:3])
    return None


def get_volume_root(path):
    macos_root = get_macos_volume_root(path)
    if macos_root:
        return macos_root
    abs_path = os.path.abspath(path)
    drive, _tail = os.path.splitdrive(abs_path)
    if drive:
        return drive + os.sep
    current = abs_path if os.path.isdir(abs_path) else os.path.dirname(abs_path)
    previous = None
    while current and current != previous:
        if os.path.ismount(current):
            return current
        previous = current
        current = os.path.dirname(current)
    return os.path.abspath(os.sep)


def find_nas_recycle_root(volume_root):
    """Return an existing writable NAS recycle folder at ``volume_root``, else None.

    Looks for Synology's ``#recycle``, QNAP's ``@Recycle``, and the Samba
    recycle module's ``.recycle``. Never creates the folder — only uses it
    when the NAS admin has already enabled it.
    """
    for name in NAS_RECYCLE_DIR_NAMES:
        candidate = os.path.join(volume_root, name)
        if os.path.isdir(candidate) and os.access(candidate, os.W_OK):
            return candidate
    return None


def make_unique_collision_path(path):
    """Return ``path`` or a non-existing sibling with the process collision suffix."""
    if not os.path.lexists(path):
        return path
    parent = os.path.dirname(path)
    stem, ext = os.path.splitext(os.path.basename(path))
    pid = os.getpid()
    candidate = os.path.join(parent, f"{stem} {pid}{ext}")
    if not os.path.lexists(candidate):
        return candidate
    suffix = 1
    while True:
        candidate = os.path.join(parent, f"{stem} {pid}-{suffix}{ext}")
        if not os.path.lexists(candidate):
            return candidate
        suffix += 1


def _ensure_no_symlink_replacement(path):
    """Raise OSError if *path* exists and is a symlink (TOCTOU guard).
    Missing paths are silently allowed — the caller's own operation
    will fail naturally if the file is gone.
    """
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return  # path may have been cleaned up — let the caller handle it
    if stat.S_ISLNK(st.st_mode):
        raise SymlinkReplacementError(
            f"Safety abort: {path!r} was replaced with a symlink to "
            f"{os.readlink(path)!r}"
        )


def move_to_nas_recycle(path, recycle_root, volume_root):
    """Move ``path`` into a NAS server-side recycle folder via os.rename.

    Same-volume atomic rename — no data crosses the network. The file's
    path relative to the volume root is preserved inside the recycle
    folder so files of the same name from different directories don't
    collide and so the NAS UI can show recovery context.
    """
    _ensure_no_symlink_replacement(path)
    rel = os.path.relpath(path, volume_root)
    dest = os.path.join(recycle_root, rel)
    parent = os.path.dirname(dest)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    dest = make_unique_collision_path(dest)
    os.rename(path, dest)
    return dest


def prompt_permanent_delete(
    volume_root,
    files_on_volume,
    input_func=input,
    item_label="file",
    allow_slow_local_trash=False,
):
    """Ask whether to permanently delete items on a volume with no trash.

    Returns ``"permanent"``, ``"local"``, or ``"skip"``. EOF returns
    ``"skip"`` as the safe default.
    """
    count = len(files_on_volume)
    print()
    print(f"Volume {volume_root!r} has no recycle bin.")
    print(f"{count} {item_label}(s) on this volume cannot be moved to a system trash:")
    preview = files_on_volume[:5]
    for path in preview:
        print(f"  - {path}")
    if count > len(preview):
        print(f"  ... and {count - len(preview)} more")
    print()
    if allow_slow_local_trash:
        print("Choose how to handle these items:")
        print("  [p] Permanently delete them. This is irreversible (like Finder's 'Delete Immediately').")
        print("  [l] Move them to local ~/.Trash. This may be slow across the network.")
        print(f"  [s] Skip these {item_label}s.")
        prompt = "Permanently delete, use local trash, or skip? [p/l/S]: "
    else:
        print("Permanently delete them? This is irreversible (like Finder's 'Delete Immediately').")
        print(f"Decline to skip these {item_label}s — pass --allow-slow-local-trash to copy them to ~/.Trash instead.")
        prompt = "Permanently delete? [y/N]: "
    try:
        answer = input_func(prompt).strip().lower()
    except EOFError:
        return "skip"
    if allow_slow_local_trash and answer in ("l", "local", "trash", "local trash"):
        return "local"
    if answer in ("y", "yes", "p", "permanent", "delete"):
        return "permanent"
    return "skip"


def move_to_local_trash(path):
    _ensure_no_symlink_replacement(path)
    trash_dir = os.path.expanduser("~/.Trash")
    if not os.path.isdir(trash_dir):
        raise OSError("~/.Trash not found")
    name = os.path.basename(path)
    dest = os.path.join(trash_dir, name)
    dest = make_unique_collision_path(dest)
    shutil.move(path, dest)
    return dest


def move_to_trash_safely(path, send_to_trash):
    """Try the system trash mechanisms for ``path``.

    On macOS /Volumes/ paths the chain is:
      1. ``send2trash``
      2. ``/usr/bin/trash``
      3. an existing NAS server-side recycle folder (#recycle, @Recycle,
         .recycle) at the volume root — same-volume rename, no network copy
    If all three fail (the volume genuinely has no trash), raises
    :class:`VolumeHasNoTrashError` so the caller can decide between
    permanent deletion, ``~/.Trash`` copy, or skipping.
    """
    _ensure_no_symlink_replacement(path)
    try:
        send_to_trash(path)
        return "send2trash"
    except Exception as send_error:
        if not (CURRENT_OS == OS_MACOS and is_macos_external_volume_path(path)):
            # Permission errors mean the user can't write to this path at all;
            # wrapping them as VolumeHasNoTrashError would mislead callers into
            # offering permanent deletion when the real problem is access rights.
            if isinstance(send_error, PermissionError):
                raise
            volume_root = get_volume_root(path)
            raise VolumeHasNoTrashError(
                path,
                volume_root,
                send_error,
                OSError("no platform trash fallback available"),
            ) from send_error
        try:
            move_to_trash_with_cmd(path)
            return "trash-cmd"
        except SymlinkReplacementError:
            raise
        except Exception as cmd_error:
            volume_root = get_macos_volume_root(path)
            if volume_root:
                recycle_root = find_nas_recycle_root(volume_root)
                if recycle_root is not None:
                    try:
                        move_to_nas_recycle(path, recycle_root, volume_root)
                        return "nas-recycle"
                    except SymlinkReplacementError:
                        raise
                    except Exception:
                        # If the same-volume rename fails (e.g. cross-device,
                        # permission), fall through to the no-trash signal.
                        pass
            raise VolumeHasNoTrashError(path, volume_root, send_error, cmd_error) from cmd_error


def trash_files(
    files,
    groups,
    dry_run=True,
    permanent_on_no_trash=False,
    allow_slow_local_trash=False,
    interactive=True,
    prompt_func=prompt_permanent_delete,
    full_hash_preloader=None,
):
    """Trash the selected files with NAS-aware fallbacks.

    Per-file the chain is: send2trash → /usr/bin/trash → existing NAS
    server-side recycle folder (#recycle, @Recycle, .recycle). When all
    three fail for a macOS /Volumes/ path, the per-volume strategy is
    decided once and reused for every other file on that same volume:

      - ``permanent_on_no_trash=True``: permanently delete (irreversible).
      - ``allow_slow_local_trash=True``: copy across the network to
        ``~/.Trash`` (slow but recoverable).
      - ``interactive=True``: ask via ``prompt_func`` — y/yes permanently
        deletes, anything else skips. Pass ``--allow-slow-local-trash``
        alongside to allow choosing the slow copy.
      - ``interactive=False`` (e.g. --yes mode without either flag): skip
        the file with an error so nothing is permanently deleted by
        accident in non-interactive runs.
    """
    expected = build_expected_hashes(groups)
    result = TrashResult(selected=len(files), dry_run=dry_run)
    selected_set = set(files)
    full_hash_cache = {}
    if full_hash_preloader is not None:
        needed_hashes = exact_hash_paths_for_selection(files, groups)
        full_hash_preloader.restrict_to(needed_hashes)
        if needed_hashes:
            print(
                f"Completing exact verification for {len(needed_hashes)} file(s) "
                "from the reviewed selection..."
            )
    send_to_trash = None
    if not dry_run:
        try:
            send_to_trash = load_send_to_trash()
        except ImportError:
            print("Error: install send2trash to move files safely: pip install send2trash", file=sys.stderr)
            result.errors = len(files)
            return result

    validated_paths = []
    for path in sorted(files):
        if path not in expected:
            print(f"Skipped (not in duplicate set): {path}", file=sys.stderr)
            result.skipped += 1
            continue
        info, group = expected[path]
        valid, reason = revalidate_selected_file_exact(
            path,
            info,
            group,
            selected_set,
            full_hash_cache,
            full_hash_preloader.get if full_hash_preloader is not None else None,
        )
        if not valid:
            print(f"Skipped ({reason}): {path}", file=sys.stderr)
            result.skipped += 1
            continue
        validated_paths.append(path)

    if dry_run:
        for path in validated_paths:
            print(f"Would move to trash: {path}")
        return result

    # Per-volume cached decision for "no trash on volume" situations.
    no_trash_strategy = {}
    try:
        for path in validated_paths:
            try:
                trash_method = move_to_trash_safely(path, send_to_trash)
            except VolumeHasNoTrashError as no_trash:
                strategy = no_trash_strategy.get(no_trash.volume_root)
                if strategy is None:
                    strategy = _decide_no_trash_strategy(
                        no_trash.volume_root,
                        validated_paths,
                        permanent_on_no_trash,
                        allow_slow_local_trash,
                        interactive,
                        prompt_func,
                    )
                    no_trash_strategy[no_trash.volume_root] = strategy
                try:
                    if strategy == "permanent":
                        _ensure_no_symlink_replacement(path)
                        os.remove(path)
                        trash_method = "permanent-delete"
                    elif strategy == "local":
                        _ensure_no_symlink_replacement(path)
                        move_to_local_trash(path)
                        trash_method = "local-trash"
                    else:
                        print(
                            f"Skipped ({no_trash}; volume has no trash and strategy is 'skip'): {path}",
                            file=sys.stderr,
                        )
                        result.skipped += 1
                        continue
                except Exception as fallback_error:
                    print(
                        f"Error trashing {path}: {no_trash}; fallback ({strategy}) failed: {fallback_error}",
                        file=sys.stderr,
                    )
                    result.errors += 1
                    continue
            except Exception as exc:
                print(f"Error trashing {path}: {exc}", file=sys.stderr)
                result.errors += 1
                continue

            if trash_method == "trash-cmd":
                print(f"Moved to trash via /usr/bin/trash: {path}")
            elif trash_method == "nas-recycle":
                print(f"Moved to NAS recycle folder (no network copy): {path}")
            elif trash_method == "permanent-delete":
                print(f"Permanently deleted (volume has no recycle bin): {path}")
                result.permanently_deleted += 1
                continue
            elif trash_method == "local-trash":
                print(f"Copied to local ~/.Trash (slow network fallback): {path}")
            else:
                print(f"Moved to trash: {path}")
            result.trashed += 1
    except KeyboardInterrupt:
        done = result.trashed + result.errors + result.skipped + result.permanently_deleted
        remaining = len(validated_paths) - done
        print(
            f"\nInterrupted — {result.trashed} file(s) moved to Trash, "
            f"{remaining} not yet processed.",
            file=sys.stderr,
        )
        raise
    return result


def _deduplicate_by_ancestry(paths):
    """Drop any path that is a descendant of another path in the list."""
    normed = [(os.path.normpath(p), p) for p in paths]
    normed.sort(key=lambda x: x[0].count(os.sep))
    accepted = []
    result = []
    for norm, original in normed:
        if not any(norm.startswith(a + os.sep) for a in accepted):
            accepted.append(norm)
            result.append(original)
    return result


def is_effectively_empty_dir(path, options):
    try:
        dir_stat = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    if not stat.S_ISDIR(dir_stat.st_mode):
        return False
    try:
        entries = list(os.scandir(path))
    except OSError:
        return False
    for entry in entries:
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            return False
        if is_dir:
            if not is_effectively_empty_dir(entry.path, options):
                return False
        elif not should_ignore_entry(entry.name, False, options):
            return False
    return True


def trash_empty_dirs(
    dirs,
    options,
    dry_run=True,
    permanent_on_no_trash=False,
    allow_slow_local_trash=False,
    interactive=True,
    prompt_func=None,
):
    if prompt_func is None:
        prompt_func = lambda v, p, **kwargs: prompt_permanent_delete(
            v, p, item_label="folder", **kwargs
        )
    dirs = _deduplicate_by_ancestry(dirs)
    result = TrashResult(selected=len(dirs), dry_run=dry_run)
    send_to_trash = None
    if not dry_run:
        try:
            send_to_trash = load_send_to_trash()
        except ImportError:
            print("Error: install send2trash to move files safely: pip install send2trash", file=sys.stderr)
            result.errors = len(dirs)
            return result

    if dry_run:
        for path in dirs:
            print(f"Would move to trash: {path}")
        return result

    no_trash_strategy = {}
    try:
        for path in dirs:
            if not is_effectively_empty_dir(path, options):
                print(f"Skipped (directory is no longer empty): {path}", file=sys.stderr)
                result.skipped += 1
                continue
            try:
                trash_method = move_to_trash_safely(path, send_to_trash)
            except VolumeHasNoTrashError as no_trash:
                strategy = no_trash_strategy.get(no_trash.volume_root)
                if strategy is None:
                    strategy = _decide_no_trash_strategy(
                        no_trash.volume_root,
                        dirs,
                        permanent_on_no_trash,
                        allow_slow_local_trash,
                        interactive,
                        prompt_func,
                    )
                    no_trash_strategy[no_trash.volume_root] = strategy
                try:
                    if strategy == "permanent":
                        if not is_effectively_empty_dir(path, options):
                            print(
                                f"Skipped (directory no longer empty before delete): {path}",
                                file=sys.stderr,
                            )
                            result.skipped += 1
                            continue
                        _ensure_no_symlink_replacement(path)
                        shutil.rmtree(path)
                        trash_method = "permanent-delete"
                    elif strategy == "local":
                        _ensure_no_symlink_replacement(path)
                        move_to_local_trash(path)
                        trash_method = "local-trash"
                    else:
                        print(
                            f"Skipped ({no_trash}; volume has no trash and strategy is 'skip'): {path}",
                            file=sys.stderr,
                        )
                        result.skipped += 1
                        continue
                except Exception as fallback_error:
                    print(
                        f"Error trashing {path}: {no_trash}; fallback ({strategy}) failed: {fallback_error}",
                        file=sys.stderr,
                    )
                    result.errors += 1
                    continue
            except FileNotFoundError:
                print(f"Skipped (no longer exists): {path}", file=sys.stderr)
                result.skipped += 1
                continue
            except Exception as exc:
                print(f"Error trashing {path}: {exc}", file=sys.stderr)
                result.errors += 1
                continue

            if trash_method == "trash-cmd":
                print(f"Moved to trash via /usr/bin/trash: {path}")
            elif trash_method == "nas-recycle":
                print(f"Moved to NAS recycle folder (no network copy): {path}")
            elif trash_method == "permanent-delete":
                print(f"Permanently deleted (volume has no recycle bin): {path}")
                result.permanently_deleted += 1
                continue
            elif trash_method == "local-trash":
                print(f"Copied to local ~/.Trash (slow network fallback): {path}")
            else:
                print(f"Moved to trash: {path}")
            result.trashed += 1
    except KeyboardInterrupt:
        done = result.trashed + result.errors + result.skipped + result.permanently_deleted
        remaining = len(dirs) - done
        print(
            f"\nInterrupted — {result.trashed} folder(s) moved to Trash, "
            f"{remaining} not yet processed.",
            file=sys.stderr,
        )
        raise
    return result


def _decide_no_trash_strategy(
    volume_root,
    all_paths,
    permanent_on_no_trash,
    allow_slow_local_trash,
    interactive,
    prompt_func,
):
    """Resolve what to do for files on a volume with no recycle bin.

    Returns one of ``"permanent"``, ``"local"``, or ``"skip"``.
    """
    if permanent_on_no_trash:
        print(f"Volume {volume_root!r} has no recycle bin; --permanent-on-no-trash is set.")
        return "permanent"
    if interactive:
        files_on_volume = [p for p in all_paths if get_macos_volume_root(p) == volume_root]
        return prompt_func(
            volume_root,
            files_on_volume,
            allow_slow_local_trash=allow_slow_local_trash,
        )
    if allow_slow_local_trash:
        return "local"
    return "skip"


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Find duplicate files and review them in a local browser UI.")
    parser.add_argument("path", nargs="?", default=".", help="Directory to scan.")
    parser.add_argument("-d", "--dry-run", action="store_true", help="Print selected files without moving them.")
    parser.add_argument("--yes", action="store_true", help="Skip final browser confirmation when trashing.")
    parser.add_argument("--include-hidden", action="store_true", help="Scan hidden files and directories.")
    parser.add_argument("--ignore-dir", action="append", help="Additional directory name to ignore.")
    parser.add_argument("--ignore-file", action="append", help="Additional file name or suffix to ignore.")
    parser.add_argument("--allow-home-root", action="store_true", help="Allow scanning your home directory root.")
    parser.add_argument(
        "--allow-photo-library",
        action="store_true",
        help="Allow scanning inside macOS .photoslibrary packages; backup strongly recommended.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help="Print progress every N scanned or hashed files.",
    )
    parser.add_argument(
        "--fast-only",
        action="store_true",
        help=(
            "Use sampled chunks only. This is the default and is much faster for huge files, "
            "but duplicate detection is probabilistic."
        ),
    )
    parser.add_argument(
        "--full-verify",
        action="store_true",
        help="Read and hash full file contents after sampling for exact duplicate verification.",
    )
    parser.add_argument(
        "--permanent-on-no-trash",
        action="store_true",
        help=(
            "When a path cannot be moved to a recoverable Trash or recycle "
            "folder, permanently delete the selected files instead of "
            "prompting (interactive) or skipping (non-interactive). "
            "Irreversible. Required for permanent deletion in --yes mode."
        ),
    )
    parser.add_argument(
        "--allow-slow-local-trash",
        action="store_true",
        help=(
            "Allow the last-resort fallback that copies files into local "
            "~/.Trash when the volume has no recycle bin. Slow for large or "
            "remote files but keeps them recoverable."
        ),
    )
    parser.add_argument(
        "-e", "--clean-empty-dirs",
        action="store_true",
        help=(
            "After the duplicate-file phase, scan for empty directories "
            "(folders containing only ignored system files or other empty "
            "folders) and open a browser UI to select which ones to trash."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7979,
        help="Port for the local browser UI (default: 7979). A fixed port lets the browser remember site permissions across runs.",
    )
    parser.add_argument(
        "--smoke-test-browser",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def build_options(args):
    ignore_dirs = set(DEFAULT_IGNORE_DIRS)
    ignore_dirs.update(args.ignore_dir or [])
    ignore_files = set(DEFAULT_IGNORE_FILES)
    ignore_files.update(args.ignore_file or [])
    abs_path, real_path = validate_scan_root(args.path, args.allow_home_root, args.allow_photo_library)
    return ScanOptions(
        path=abs_path,
        real_path=real_path,
        ignore_dirs=frozenset(ignore_dirs),
        ignore_files=frozenset(ignore_files),
        include_hidden=args.include_hidden,
        allow_home_root=args.allow_home_root,
        allow_photo_library=args.allow_photo_library,
        progress_every=max(1, args.progress_every),
        verify_mode=VERIFY_FULL if args.full_verify else VERIFY_FAST,
    )


def find_and_process_duplicates(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.smoke_test_browser:
        return smoke_test_browser_server()

    # H12: verify send2trash is available before doing any scanning work.
    if not args.dry_run:
        try:
            load_send_to_trash()
        except ImportError:
            print(
                "Error: 'send2trash' is not installed.\n"
                "  Install it first:  pip install send2trash",
                file=sys.stderr,
            )
            return 1

    # H3: warn loudly when permanent-on-no-trash is active so users understand
    # they opted into irreversible deletion on volumes without a recycle bin.
    if args.permanent_on_no_trash:
        print("\n" + "!" * 60, file=sys.stderr)
        print(
            "WARNING: --permanent-on-no-trash is active.\n"
            "Files on volumes without a recycle bin will be PERMANENTLY\n"
            "deleted and cannot be recovered. Press Ctrl+C now to abort.",
            file=sys.stderr,
        )
        print("!" * 60 + "\n", file=sys.stderr)
        if sys.stdin.isatty():
            try:
                input("Press Enter to continue, or Ctrl+C to abort: ")
            except EOFError:
                pass

    options = build_options(args)

    # H2: require an explicit typed confirmation when scanning inside a Photos
    # Library — moving files here can silently corrupt the SQLite database.
    if args.allow_photo_library and is_photo_library_path(options.path):
        print("\n" + "!" * 60, file=sys.stderr)
        print(
            "WARNING: Scanning inside a macOS Photos Library.\n"
            "Moving files here can CORRUPT your library and permanently\n"
            "destroy photos. Ensure Photos.app is closed and you have a\n"
            "verified, complete backup before continuing.",
            file=sys.stderr,
        )
        print("!" * 60, file=sys.stderr)
        if sys.stdin.isatty():
            try:
                answer = input("\nType exactly 'I have a backup' to proceed: ").strip()
            except EOFError:
                answer = ""
            if answer != "I have a backup":
                print("Aborted.", file=sys.stderr)
                return 2
        else:
            print(
                "\nRefusing to proceed non-interactively with --allow-photo-library.\n"
                "Run interactively and confirm the backup prompt.",
                file=sys.stderr,
            )
            return 2

    duplicate_groups, stats = find_duplicates(options)
    if not duplicate_groups:
        stats.print_summary()
        if args.clean_empty_dirs:
            return _run_empty_dir_phase(args, options)
        return 0

    # H15: flag when the result set is large enough to slow the browser UI.
    if stats.duplicate_groups > 1000:
        print(
            f"\nNOTICE: {stats.duplicate_groups} duplicate groups found. "
            "The browser UI may be slow for very large result sets.\n"
            "Consider narrowing the scan path or adding --ignore-dir.",
            flush=True,
        )

    dry_run = args.dry_run
    full_hash_preloader = FullHashPreloader(duplicate_groups)
    full_hash_preloader.start()
    try:
        files_to_trash = select_files_in_browser(
            duplicate_groups,
            require_move_confirmation=not dry_run and not args.yes,
            port=args.port,
        )
        print("-" * 60)
        if not files_to_trash:
            full_hash_preloader.stop()
            print("\nScan complete. No files were selected for removal.")
            stats.print_summary()
            if args.clean_empty_dirs:
                return _run_empty_dir_phase(args, options)
            return 0

        action = "would be moved to the Trash/Recycle Bin" if dry_run else "will be moved to the Trash/Recycle Bin"
        print(f"\nYou selected {len(files_to_trash)} file(s) that {action}:")
        for path in sorted(files_to_trash):
            print(f"  - {path}")

        result = trash_files(
            files_to_trash,
            duplicate_groups,
            dry_run=dry_run,
            permanent_on_no_trash=args.permanent_on_no_trash,
            allow_slow_local_trash=args.allow_slow_local_trash,
            interactive=not args.yes,
            full_hash_preloader=full_hash_preloader,
        )
    finally:
        full_hash_preloader.stop()
    stats.print_summary(result)
    exit_code = 0 if result.errors == 0 else 1

    # H4: make errors and unexpected skips stand out so they aren't missed.
    if result.errors:
        print(
            f"\nWARNING: {result.errors} file(s) could not be trashed (see above).\n"
            "Check whether your drive is still connected.",
            file=sys.stderr,
        )
    if not dry_run and result.skipped:
        print(
            f"NOTE: {result.skipped} file(s) were skipped "
            "(changed or moved since the scan).",
            file=sys.stderr,
        )

    if args.clean_empty_dirs:
        exit_code = max(exit_code, _run_empty_dir_phase(args, options))

    return exit_code


def _run_empty_dir_phase(args, options):
    print("\n" + "=" * 60)
    print("Empty folder scan")
    print("=" * 60)
    empty_dirs = find_empty_dirs(options)
    if not empty_dirs:
        print("\nNo empty folders found.")
        return 0

    print(f"\nFound {len(empty_dirs)} empty folder(s).")
    dirs_to_trash = select_empty_dirs_in_browser(empty_dirs, port=args.port)
    print("-" * 60)
    if not dirs_to_trash:
        print("\nNo folders were selected for removal.")
        return 0

    dry_run = args.dry_run
    action = "would be moved to the Trash/Recycle Bin" if dry_run else "will be moved to the Trash/Recycle Bin"
    print(f"\nYou selected {len(dirs_to_trash)} folder(s) that {action}:")
    for path in sorted(dirs_to_trash):
        print(f"  - {path}")

    result = trash_empty_dirs(
        dirs_to_trash,
        options,
        dry_run=dry_run,
        permanent_on_no_trash=args.permanent_on_no_trash,
        allow_slow_local_trash=args.allow_slow_local_trash,
        interactive=not args.yes,
    )
    print("\nEmpty folder cleanup summary")
    print("-" * 60)
    print(f"Selected folders:  {result.selected}")
    if result.dry_run:
        print("Mode:              dry-run")
    else:
        print(f"Trashed folders:   {result.trashed}")
        if result.permanently_deleted:
            print(f"Permanently deleted: {result.permanently_deleted}")
        print(f"Skipped:           {result.skipped}")
        print(f"Errors:            {result.errors}")
    return 0 if result.errors == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(find_and_process_duplicates())
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user. Exiting.")
        sys.exit(130)
    except ValueError as exc:
        print(f"\nSafety error: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"\nAn unexpected error occurred: {exc}", file=sys.stderr)
        sys.exit(1)
