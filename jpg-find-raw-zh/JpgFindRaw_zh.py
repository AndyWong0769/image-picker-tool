#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JPG查找RAW - 中文版 (macOS / Windows)
=====================================
与英文版 JpgFindRaw 同源；区别：界面中文、不保存上次的文件夹路径。
Features:
  - Select multiple JPG folders and RAW folders for matching
  - Supports filename matching and EXIF timestamp matching
  - If output directory is empty, exports to a 'raw' subfolder under each JPG directory

v2.1 (macOS stability release):
  - All UI updates from background threads go through a queue drained on the
    main thread (Tk on macOS is not thread-safe -> random crashes)
  - Background errors (e.g. external drive disconnected / no permission) are
    reported instead of silently leaving the UI stuck
  - Config stored in ~/Library/Application Support (never inside the .app)
  - macOS: open files via Finder, right-click menus, readable flat buttons
  - Pure-Python EXIF reader (no Pillow dependency)
"""

import os
import re
import sys
import json
import queue
import shutil
import struct
import subprocess
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from concurrent.futures import ThreadPoolExecutor, as_completed

APP_NAME = "JpgFindRaw-ZH"          # config folder name
APP_DISPLAY = "JPG 查找 RAW"
APP_VERSION = "2.2.2"

_IS_MACOS = sys.platform == 'darwin'
_IS_WINDOWS = sys.platform.startswith('win')

# Texts for the JPG / RAW source lists
_SRC_TXT = {
    'jpg_count': "{n} 张",
    'raw_count': "{n} 张",
    'jpg_total': "共 {n} 张",
    'raw_total': "共 {n} 张",
    'counting': "统计中...",
    'missing': "找不到文件夹",
    'picked_many': "已选 {n} 张图片：{first} 等（{folder}）",
    'picked_one': "{first}（{folder}）",
    'add_images': "+ 选择图片",
    'pick_title': "选择 JPG 图片（可多选，Ctrl+A / ⌘A 全选）",
    'img_type': "图片",
    'all_type': "所有文件",
}


# ============================================================
# Platform helpers
# ============================================================

def _get_config_path():
    """Per-user config file (writable, outside the app bundle)."""
    if _IS_MACOS:
        base = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', APP_NAME)
    elif _IS_WINDOWS:
        base = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), APP_NAME)
    else:
        base = os.path.join(os.path.expanduser('~'), '.config', APP_NAME)
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = os.path.expanduser('~')
    return os.path.join(base, 'config.json')


_CONFIG_PATH = _get_config_path()

if _IS_MACOS:
    _UI_FAMILY = "PingFang SC"
    _MONO_FAMILY = "Menlo"
    _FONT_DELTA = 3          # macOS renders Tk point sizes smaller
else:
    _UI_FAMILY = "Microsoft YaHei UI"
    _MONO_FAMILY = "Consolas"
    _FONT_DELTA = 0


def F(size, bold=False):
    """UI font tuple"""
    return (_UI_FAMILY, size + _FONT_DELTA, 'bold') if bold else (_UI_FAMILY, size + _FONT_DELTA)


def M(size):
    """Monospace font tuple"""
    return (_MONO_FAMILY, size + _FONT_DELTA)


def open_in_finder(path):
    """Open a file/folder with the system default app (cross-platform)."""
    if not path or not os.path.exists(path):
        return
    try:
        if _IS_MACOS:
            subprocess.Popen(["open", path])
        elif _IS_WINDOWS:
            os.startfile(path)  # noqa: only exists on Windows
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


# ============================================================
# Core logic
# ============================================================

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.webp', '.heic', '.heif'}

RAW_EXTENSIONS = {
    '.cr2', '.cr3', '.nef', '.nrw', '.arw', '.srf', '.sr2',
    '.raf', '.rw2', '.rwl', '.orf', '.pef', '.ptx', '.srw',
    '.raw', '.r3d', '.iiq', '.3fr', '.fff', '.x3f', '.dcr',
    '.kdc', '.mrw', '.erf', '.mef', '.mos', '.dng', '.gpr',
    '.braw', '.ari', '.bay',
}

# System folders on external drives that should never be scanned
_SKIP_DIRS = {'System Volume Information', 'RECYCLER', 'Network Trash Folder', 'Temporary Items'}


def is_raw_file(filepath: str) -> bool:
    """Check if file is a RAW file"""
    ext = os.path.splitext(filepath)[1].lower()
    return ext in RAW_EXTENSIONS


def get_all_files_in_folder(folder: str) -> list:
    """Recursively get all files in folder (unreadable sub-folders are skipped)"""
    file_list = []
    if not folder or not os.path.isdir(folder):
        return file_list
    for root, dirs, files in os.walk(folder, onerror=lambda e: None):
        dirs[:] = [d for d in dirs
                   if not d.startswith('.') and not d.startswith('$') and d not in _SKIP_DIRS]
        for file in files:
            if not file.startswith('.') and not file.startswith('~'):
                file_list.append(os.path.join(root, file))
    return file_list


def parse_filters(filter_str: str) -> list:
    """Parse comma-separated filter keywords, return deduplicated non-empty list"""
    if not filter_str or not filter_str.strip():
        return []
    filter_str = filter_str.replace('，', ',')
    return [f.strip() for f in filter_str.split(',') if f.strip()]


def parse_extensions(ext_str: str) -> set:
    """Parse extension string, return set of lowercase extensions with dot prefix

    Supports: .jpg / .png / .tif   jpg / png   .jpg, .png   jpg,png   JPG / PNG
    """
    if not ext_str or not ext_str.strip():
        return set()
    s = ext_str.strip().replace('，', ',').replace('/', ',')
    result = set()
    for part in s.split(','):
        part = part.strip().lower()
        if not part:
            continue
        if not part.startswith('.'):
            part = '.' + part
        result.add(part)
    return result


# 中文汉字 + 中文标点 / 全角字符
_CJK_RE = re.compile('[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]')
_TRIM_CHARS = ' \t-_.~'


def extract_possible_raw_names(filename: str,
                               prefix_filters: list = None, suffix_filters: list = None,
                               filter_chinese: bool = True) -> list:
    """从 JPG 文件名提取可能的 RAW 文件名

    filter_chinese=True : 额外提取字母/数字/下划线片段参与匹配（忽略纯中文部分）
    filter_chinese=False: 不提取片段，只用完整文件名（含中文）和 3 位以上数字匹配
    """
    name_without_ext = os.path.splitext(filename)[0]

    if prefix_filters or suffix_filters:
        for pf in (prefix_filters or []):
            if name_without_ext.startswith(pf):
                name_without_ext = name_without_ext[len(pf):]
        for sf in (suffix_filters or []):
            if name_without_ext.endswith(sf):
                name_without_ext = name_without_ext[:-len(sf)]

    number_patterns = re.findall(r'\d{3,}', name_without_ext)
    candidates = set()
    candidates.add(name_without_ext.lower())
    if filter_chinese:
        # 过滤中文：提取字母数字下划线片段，忽略纯中文
        for p in re.findall(r'[a-zA-Z0-9_]{2,}', name_without_ext):
            candidates.add(p.lower())
        # 去掉中文后的完整文件名：N-D810 (4)副本 → N-D810 (4)；DSC0001 - 副本 → DSC0001
        no_cjk = _CJK_RE.sub('', name_without_ext)
        if no_cjk != name_without_ext:
            for c in (no_cjk.strip(_TRIM_CHARS), re.sub(r'\s+', ' ', no_cjk).strip(_TRIM_CHARS)):
                if c:
                    candidates.add(c.lower())
    for p in number_patterns:
        candidates.add(p.lower())
    return list(candidates)


def match_jpg_to_raw(jpg_files: list, raw_files: list, settings: dict = None) -> list:
    """Match JPG files to RAW files by filename"""
    jpg_prefix = parse_filters(settings.get('jpg_prefix_filters', '')) if settings else []
    jpg_suffix = parse_filters(settings.get('jpg_suffix_filters', '')) if settings else []
    filter_chinese = settings.get('filter_chinese', True) if settings else True

    raw_dict = {}
    for f in raw_files:
        raw_stem = os.path.splitext(os.path.basename(f))[0]
        raw_dict[raw_stem.lower()] = f

    results = []
    for jpg_path in jpg_files:
        jpg_filename = os.path.basename(jpg_path)
        filtered_jpg_stem = os.path.splitext(jpg_filename)[0]
        for pf in jpg_prefix:
            if filtered_jpg_stem.startswith(pf):
                filtered_jpg_stem = filtered_jpg_stem[len(pf):]
        for sf in jpg_suffix:
            if filtered_jpg_stem.endswith(sf):
                filtered_jpg_stem = filtered_jpg_stem[:-len(sf)]

        found_raw = None
        method = None

        # 1. Exact match (using filtered stem)
        if filtered_jpg_stem.lower() in raw_dict:
            found_raw = raw_dict[filtered_jpg_stem.lower()]
            method = 'Filename'

        # 2. Candidate match (longest candidate first = most specific)
        if not found_raw:
            candidates = extract_possible_raw_names(jpg_filename, jpg_prefix, jpg_suffix, filter_chinese)
            for cand in sorted(candidates, key=len, reverse=True):
                if cand in raw_dict:
                    found_raw = raw_dict[cand]
                    method = 'Filename'
                    break

        results.append({
            'jpg_path': jpg_path,
            'jpg_name': jpg_filename,
            'raw_path': found_raw,
            'raw_name': os.path.basename(found_raw) if found_raw else None,
            'method': method
        })

    return results


# ── EXIF (pure Python, no Pillow) ──

_EXIF_DATE_TAGS = (36867, 36868, 306)   # DateTimeOriginal, DateTimeDigitized, DateTime
_DATE_RE = re.compile(rb'(?:19|20)\d{2}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}')


def _tiff_datetime(buf: bytes, base: int):
    """Read EXIF datetime from a TIFF structure starting at buf[base]."""
    try:
        order = buf[base:base + 2]
        if order == b'II':
            e = '<'
        elif order == b'MM':
            e = '>'
        else:
            return None

        def u16(o):
            return struct.unpack_from(e + 'H', buf, base + o)[0]

        def u32(o):
            return struct.unpack_from(e + 'I', buf, base + o)[0]

        def read_ifd(off):
            tags = {}
            if off <= 0 or base + off + 2 > len(buf):
                return tags
            n = u16(off)
            if n > 1000:
                return tags
            for i in range(n):
                p = off + 2 + i * 12
                if base + p + 12 > len(buf):
                    break
                tag, typ, cnt = u16(p), u16(p + 2), u32(p + 4)
                tags[tag] = (typ, cnt, p + 8)
            return tags

        def ascii_val(entry):
            typ, cnt, p = entry
            if typ != 2 or cnt == 0:
                return None
            off = p if cnt <= 4 else u32(p)
            raw = buf[base + off: base + off + cnt]
            m = _DATE_RE.search(raw)
            return m.group(0).decode('ascii') if m else None

        ifd0 = read_ifd(u32(4))
        found = {}
        if 306 in ifd0:
            found[306] = ascii_val(ifd0[306])
        if 34665 in ifd0:  # Exif sub-IFD pointer
            exif = read_ifd(u32(ifd0[34665][2]))
            for t in (36867, 36868):
                if t in exif:
                    found[t] = ascii_val(exif[t])
        for t in _EXIF_DATE_TAGS:
            if found.get(t):
                return found[t]
    except Exception:
        pass
    return None


def _jpeg_datetime(buf: bytes):
    """Find the EXIF APP1 segment in JPEG data and read its datetime."""
    if buf[:2] != b'\xff\xd8':
        return None
    i = 2
    while i + 4 <= len(buf):
        if buf[i] != 0xFF:
            return None
        marker = buf[i + 1]
        if marker in (0xD9, 0xDA):        # EOI / start of scan
            return None
        seg_len = struct.unpack_from('>H', buf, i + 2)[0]
        if marker == 0xE1 and buf[i + 4:i + 10] == b'Exif\x00\x00':
            return _tiff_datetime(buf, i + 10)
        i += 2 + seg_len
    return None


def read_exif_datetime(filepath: str) -> str:
    """Read capture datetime ('YYYY:MM:DD HH:MM:SS') from JPG or RAW, or None"""
    try:
        with open(filepath, 'rb') as f:
            head = f.read(262144)
    except Exception:
        return None

    dt = None
    if head[:2] == b'\xff\xd8':
        dt = _jpeg_datetime(head)
    elif head[:2] in (b'II', b'MM'):   # TIFF-based RAW: CR2, NEF, ARW, DNG, ORF, RW2, PEF...
        dt = _tiff_datetime(head, 0)
    if dt:
        return dt

    # Fallback (CR3, RAF, others): first timestamp-looking string in the first 1 MB
    try:
        with open(filepath, 'rb') as f:
            data = f.read(1048576)
        m = _DATE_RE.search(data)
        if m:
            return m.group(0).decode('ascii', errors='ignore')
    except Exception:
        pass
    return None


def match_by_exif(unmatched_results: list, all_raw_files: list, progress_cb=None) -> list:
    """Match unmatched JPGs by EXIF datetime; each RAW assigned to at most one JPG"""
    if not unmatched_results:
        return unmatched_results

    raw_by_time = {}
    total = len(all_raw_files)
    for i, raw_path in enumerate(all_raw_files):
        dt = read_exif_datetime(raw_path)
        if dt:
            raw_by_time.setdefault(dt, []).append(raw_path)
        if progress_cb and ((i + 1) % 10 == 0 or i + 1 == total):
            progress_cb(i + 1, total)

    used_raw = set()
    updated = []
    for result in unmatched_results:
        if result['raw_path']:
            updated.append(result)
            continue
        dt = read_exif_datetime(result['jpg_path'])
        if dt and dt in raw_by_time:
            for raw_path in raw_by_time[dt]:
                if raw_path not in used_raw:
                    result = dict(result)
                    result['raw_path'] = raw_path
                    result['raw_name'] = os.path.basename(raw_path)
                    result['method'] = 'EXIF'
                    used_raw.add(raw_path)
                    break
        updated.append(result)

    return updated


# ============================================================
# Widgets
# ============================================================

class FlatButton(tk.Label):
    """Flat colored button.

    tk.Button ignores background colors on macOS (Aqua), which made the
    light button text unreadable. A Label-based button looks identical on
    macOS and Windows.
    """

    def __init__(self, parent, text, command, bg, fg, hover_bg=None, hover_fg=None,
                 disabled_bg=None, disabled_fg=None, font=None, padx=10, pady=4, width=None):
        kw = dict(text=text, bg=bg, fg=fg, font=font, padx=padx, pady=pady, cursor="hand2")
        if width:
            kw['width'] = width
        super().__init__(parent, **kw)
        self._command = command
        self._bg, self._fg = bg, fg
        self._hover_bg = hover_bg or bg
        self._hover_fg = hover_fg or fg
        self._disabled_bg = disabled_bg or bg
        self._disabled_fg = disabled_fg or '#5a5a70'
        self._enabled = True
        self._hover = False
        self.bind('<Enter>', self._on_enter)
        self.bind('<Leave>', self._on_leave)
        self.bind('<ButtonRelease-1>', self._on_click)

    def _paint(self):
        if not self._enabled:
            super().configure(bg=self._disabled_bg, fg=self._disabled_fg, cursor="arrow")
        elif self._hover:
            super().configure(bg=self._hover_bg, fg=self._hover_fg, cursor="hand2")
        else:
            super().configure(bg=self._bg, fg=self._fg, cursor="hand2")

    def _on_enter(self, _e):
        self._hover = True
        self._paint()

    def _on_leave(self, _e):
        self._hover = False
        self._paint()

    def _on_click(self, e):
        if not self._enabled or not self._command:
            return
        # only fire if the mouse is still over the button
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self._command()

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self._paint()

    def is_enabled(self):
        return self._enabled


class ToolTip:
    """Simple tooltip that shows on hover"""
    def __init__(self, widget, text, bg="#2a2a3c", fg="#e4e4ed", font=None):
        self.widget = widget
        self.text = text
        self.bg = bg
        self.fg = fg
        self.font = font or F(9)
        self.tip_window = None
        widget.bind('<Enter>', self.show, add='+')
        widget.bind('<Leave>', self.hide, add='+')

    def show(self, event=None):
        if self.tip_window:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        frame = tk.Frame(tw, bg=self.bg, highlightbackground="#6c8aff",
                         highlightthickness=1, padx=10, pady=8)
        frame.pack()
        tk.Label(frame, text=self.text, bg=self.bg, fg=self.fg,
                 font=self.font, justify=tk.LEFT).pack()

    def hide(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


# ============================================================
# GUI
# ============================================================

class FindRawApp:
    """JPG Match RAW application"""

    BG = "#1e1e2e"
    SURFACE = "#2a2a3c"
    BORDER = "#3a3a4e"
    INK = "#e4e4ed"
    ASH = "#9090a8"
    ACCENT = "#6c8aff"
    ACCENT_HOVER = "#8098ff"
    SUCCESS = "#5acb84"
    ERROR = "#ff6b6b"
    WARN = "#f0c674"

    def __init__(self, root):
        self.root = root
        self.root.title(APP_DISPLAY)
        self._set_window_icon()
        self._fit_window_to_screen()
        self.root.configure(bg=self.BG)

        # Sources shown in the JPG / RAW lists:
        #   {'id', 'type': 'folder' | 'files', 'path', 'files', 'count'}
        self.jpg_sources = []
        self.raw_sources = []
        self._src_seq = 0
        self.results = []
        self.selected_rows = set()
        self._busy = False          # a match/export job is running
        self._quiet = False         # self-test mode: no modal dialogs
        self._last_export = None    # (count, failed, folders) of the last export

        self.settings = {
            'filter_chinese': True,       # 过滤中文（匹配时忽略纯中文字符）
            'create_raw_folder': True,
            'export_method': 'copy',
            'jpg_extensions': '',
            'raw_extensions': '',
            'jpg_prefix_filters': '',
            'jpg_suffix_filters': '',
        }

        # Thread -> UI message queue. Background threads must NEVER touch Tk
        # directly (that crashes Tk on macOS); they call self._post() instead.
        self._ui_queue = queue.Queue()

        self._load_config()
        self._build_ui()

        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.root.after(50, self._drain_ui_queue)

    def _fit_window_to_screen(self):
        """Size and center the window so it fits the usable screen area
        (small laptop screens, Dock, menu bar), instead of a fixed 950x820."""
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
        except tk.TclError:
            sw, sh = 1280, 800
        # leave room for the macOS menu bar + Dock / Windows taskbar
        # Windows high-DPI (125%/150%...): fonts grow with the scale factor,
        # so the window must grow too.
        sc = self._scale = self._ui_scale()
        reserve_h = 160 if _IS_MACOS else int(90 * sc)
        w = max(640, min(int(950 * sc), sw - 60))
        h = max(480, min(int(820 * sc), sh - reserve_h))
        x = max(0, (sw - w) // 2)
        y = max(25 if _IS_MACOS else 0, (sh - reserve_h - h) // 2 + (25 if _IS_MACOS else 0))
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(min(int(720 * sc), w), min(int(520 * sc), h))

    def _ui_scale(self):
        """Screen scale factor relative to 96 dpi (Windows only; 1.0 elsewhere)."""
        if not _IS_WINDOWS:
            return 1.0
        try:
            return max(1.0, min(3.0, self.root.winfo_fpixels('1i') / 96.0))
        except Exception:
            return 1.0

    def _set_window_icon(self):
        """Title-bar / taskbar icon (bundled app.ico on Windows)."""
        if not _IS_WINDOWS:
            return
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        ico = os.path.join(base, 'app.ico')
        if os.path.exists(ico):
            try:
                self.root.iconbitmap(default=ico)
            except Exception:
                pass

    # ── Thread-safe UI dispatch ──
    def _post(self, fn, *args):
        """Schedule fn(*args) on the Tk main thread (safe from any thread)."""
        self._ui_queue.put((fn, args))

    def _drain_ui_queue(self):
        try:
            for _ in range(1000):
                fn, args = self._ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        try:
            self.root.after(50, self._drain_ui_queue)
        except tk.TclError:
            pass  # window closed

    def _show_info(self, title, msg):
        if not self._quiet:
            messagebox.showinfo(title, msg, parent=self.root)

    def _show_error(self, title, msg):
        if self._quiet:
            print(f"[{title}] {msg}", file=sys.stderr)
        else:
            messagebox.showerror(title, msg, parent=self.root)

    def _get_formats_tooltip_text(self):
        jpg_defaults = ['.jpg', '.jpeg', '.png']
        raw_defaults = sorted(RAW_EXTENSIONS)
        mid = len(raw_defaults) // 2
        lines = [
            "默认支持的格式", "",
            "JPG 格式：", "  " + ", ".join(jpg_defaults), "",
            "RAW 格式：",
            "  " + ", ".join(raw_defaults[:mid]),
            "  " + ", ".join(raw_defaults[mid:]),
        ]
        return "\n".join(lines)

    def _make_info_icon(self, parent, tooltip_text):
        size = 16
        btn = tk.Canvas(parent, width=size, height=size, bg=self.BG,
                        highlightthickness=0, relief="flat", bd=0)

        def draw_icon(fg_color):
            btn.delete("all")
            btn.create_oval(2, 2, size - 2, size - 2, outline=fg_color, width=1.5)
            btn.create_oval(size // 2 - 1.5, 4.5, size // 2 + 1.5, 7.5, fill=fg_color, outline="")
            btn.create_line(size // 2, 9, size // 2, 12, fill=fg_color, width=1.5)

        draw_icon(self.ASH)
        btn.bind('<Enter>', lambda e: draw_icon(self.ACCENT))
        btn.bind('<Leave>', lambda e: draw_icon(self.ASH))
        ToolTip(btn, tooltip_text, bg=self.SURFACE, fg=self.INK)
        return btn

    def _accent_button(self, parent, text, command, **kw):
        return FlatButton(parent, text, command, bg=self.ACCENT, fg="#ffffff",
                          hover_bg=self.ACCENT_HOVER, disabled_bg=self.SURFACE,
                          disabled_fg=self.ASH, **kw)

    def _bind_right_click(self, widget, handler):
        if _IS_MACOS:
            widget.bind('<Button-2>', handler)           # right button on macOS Tk 8.6
            widget.bind('<Control-Button-1>', handler)   # ctrl-click
        widget.bind('<Button-3>', handler)

    def _build_ui(self):
        bg = self.BG
        surface = self.SURFACE
        border = self.BORDER
        ink = self.INK
        ash = self.ASH
        accent = self.ACCENT

        main_frame = tk.Frame(self.root, bg=bg, padx=16, pady=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        title_row = tk.Frame(main_frame, bg=bg)
        title_row.pack(fill=tk.X)
        tk.Label(title_row, text=APP_DISPLAY, bg=bg, fg=accent,
                 font=F(16, True)).pack(side=tk.LEFT)
        tk.Label(title_row, text=f"v{APP_VERSION}", bg=bg, fg='#5a5a70',
                 font=F(8)).pack(side=tk.LEFT, padx=(8, 0), pady=(6, 0))

        tk.Label(main_frame, text="支持多个 JPG 文件夹和 RAW 文件夹进行匹配，支持文件名匹配和 EXIF 时间匹配",
                 bg=bg, fg=ash, font=F(9)).pack(anchor="w", pady=(2, 10))

        folder_card = tk.Frame(main_frame, bg=surface, highlightbackground=border,
                               highlightthickness=1, padx=12, pady=10)
        folder_card.pack(fill=tk.X, pady=(0, 8))

        # === JPG Directory ===
        jpg_header = tk.Frame(folder_card, bg=surface)
        jpg_header.pack(fill=tk.X)
        tk.Label(jpg_header, text="JPG 目录", bg=surface, fg=ash,
                 font=F(10, True)).pack(side=tk.LEFT)
        self.jpg_total_label = tk.Label(jpg_header, text="", bg=surface, fg=accent, font=F(9, True))
        self.jpg_total_label.pack(side=tk.LEFT, padx=(10, 0))

        jpg_btn_row = tk.Frame(jpg_header, bg=surface)
        jpg_btn_row.pack(side=tk.RIGHT)

        self.settings_btn = FlatButton(jpg_btn_row, "⚙", self._open_settings,
                                       bg=surface, fg=ash, hover_fg=accent,
                                       font=F(18), padx=4, pady=0)
        self.settings_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.jpg_add_btn = self._accent_button(jpg_btn_row, "+ 添加文件夹", self._add_jpg_folder,
                                               font=F(9, True), padx=10, pady=4)
        self.jpg_add_btn.pack(side=tk.LEFT)
        self.jpg_img_btn = FlatButton(jpg_btn_row, _SRC_TXT['add_images'], self._add_jpg_images,
                                      bg=border, fg="#ffffff", hover_bg="#4a4a62",
                                      disabled_bg=surface, disabled_fg=ash,
                                      font=F(9, True), padx=10, pady=4)
        self.jpg_img_btn.pack(side=tk.LEFT, padx=(6, 0))

        self.jpg_list = self._make_source_list(folder_card, 'jpg')

        # === RAW Directory ===
        raw_header = tk.Frame(folder_card, bg=surface)
        raw_header.pack(fill=tk.X, pady=(12, 0))
        tk.Label(raw_header, text="RAW 目录", bg=surface, fg=ash,
                 font=F(10, True)).pack(side=tk.LEFT)
        self.raw_total_label = tk.Label(raw_header, text="", bg=surface, fg=accent, font=F(9, True))
        self.raw_total_label.pack(side=tk.LEFT, padx=(10, 0))

        raw_btn_row = tk.Frame(raw_header, bg=surface)
        raw_btn_row.pack(side=tk.RIGHT)
        self.raw_add_btn = self._accent_button(raw_btn_row, "+ 添加文件夹", self._add_raw_folder,
                                               font=F(9, True), padx=10, pady=4)
        self.raw_add_btn.pack(side=tk.RIGHT)

        self.raw_list = self._make_source_list(folder_card, 'raw')

        # === Output Directory ===
        out_row = tk.Frame(folder_card, bg=surface)
        out_row.pack(fill=tk.X, pady=(12, 0))
        tk.Label(out_row, text="输出目录", bg=surface, fg=ash,
                 font=F(10, True)).pack(side=tk.LEFT)
        self.out_entry = tk.Entry(out_row, font=M(9), bg=bg, fg=ink, insertbackground=ink,
                                  relief="flat", highlightthickness=1,
                                  highlightbackground=border, highlightcolor=accent)
        self.out_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 6), ipady=5)
        self._accent_button(out_row, "浏览", self._browse_out,
                            font=F(9, True), padx=28, pady=4).pack(side=tk.RIGHT)

        self._out_placeholder = "不填写默认导出到jpg目录下"
        self.out_entry.insert(0, self._out_placeholder)
        self.out_entry.config(fg='#5a5a70')
        self.out_entry.bind('<FocusIn>', self._on_out_focus_in)
        self.out_entry.bind('<FocusOut>', self._on_out_focus_out)

        # ── Action buttons ──
        btn_row = tk.Frame(main_frame, bg=bg)
        btn_row.pack(fill=tk.X, pady=10)
        btn_center = tk.Frame(btn_row, bg=bg)
        btn_center.pack(side=tk.TOP)

        self.match_btn = self._accent_button(btn_center, "文件名匹配", self._start_match,
                                             font=F(10, True), width=18, pady=10)
        self.match_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.exif_btn = self._accent_button(btn_center, "EXIF 匹配", self._start_exif,
                                            font=F(10, True), width=18, pady=10)
        self.exif_btn.set_enabled(False)
        self.exif_btn.pack(side=tk.LEFT)

        status_row = tk.Frame(main_frame, bg=bg)
        status_row.pack(fill=tk.X)
        self.status_label = tk.Label(status_row, text="就绪", bg=bg, fg=ash, font=F(9))
        self.status_label.pack(side=tk.LEFT)
        self.stats_label = tk.Label(status_row, text="", bg=bg, fg=accent, font=F(9, True))
        self.stats_label.pack(side=tk.RIGHT)

        # ── Results table ──
        # Bottom area (progress + export button) is packed before the table with
        # side=BOTTOM, so on small screens the table shrinks instead of the
        # export button being pushed out of the window.
        bottom_area = tk.Frame(main_frame, bg=bg)
        bottom_area.pack(side=tk.BOTTOM, fill=tk.X)

        tree_frame = tk.Frame(main_frame, bg=bg)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Dark.Treeview",
                        background='#1a1a24', foreground='#c0c0d0',
                        fieldbackground='#1a1a24', bordercolor='#1a1a24',
                        lightcolor='#1a1a24', darkcolor='#1a1a24', borderwidth=0,
                        font=F(9), rowheight=int((22 + _FONT_DELTA * 2) * self._scale))
        style.layout('Dark.Treeview', [('Dark.Treeview.treearea', {'sticky': 'nswe'})])
        style.configure("Dark.Treeview.Heading",
                        background='#6c8aff', foreground='#ffffff',
                        bordercolor='#1a1a24', lightcolor='#6c8aff', darkcolor='#6c8aff',
                        relief='flat', font=F(9, True), padding=(6, 4))
        style.map("Dark.Treeview.Heading",
                  background=[('active', '#8098ff'), ('!active', '#6c8aff')],
                  foreground=[('active', '#ffffff'), ('!active', '#ffffff')])
        style.map("Dark.Treeview",
                  background=[('selected', '#2a2a4a')],
                  foreground=[('selected', '#e0e0f0')])
        style.configure("Dark.Vertical.TScrollbar",
                        background='#252535', troughcolor='#1a1a24',
                        borderwidth=0, arrowcolor='#6c8aff', gripcount=0)
        style.map("Dark.Vertical.TScrollbar",
                  background=[('active', '#3a3a4e'), ('!active', '#252535')],
                  troughcolor=[('active', '#1a1a24'), ('!active', '#1a1a24')])

        columns = ('checked', 'jpg', 'raw', 'method')
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings',
                                 selectmode='extended', style="Dark.Treeview", height=4)
        self.tree.heading('checked', text='勾选')
        self.tree.heading('jpg', text='JPG 文件')
        self.tree.heading('raw', text='RAW 文件')
        self.tree.heading('method', text='匹配方式')
        self.tree.column('checked', width=60, anchor='center', stretch=False)
        self.tree.column('jpg', width=200)
        self.tree.column('raw', width=350, minwidth=200, stretch=True)
        self.tree.column('method', width=100, anchor='center', stretch=False)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview,
                            style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.tree.bind('<ButtonRelease-1>', self._on_tree_click)
        self.tree.bind('<Double-1>', self._on_tree_double_click)
        if _IS_MACOS:
            # macOS delivers small deltas (1, 2, 3...) per wheel step
            self.tree.bind('<MouseWheel>',
                           lambda e: (self.tree.yview_scroll(-e.delta, 'units'), 'break')[1])
        else:
            self.tree.bind('<MouseWheel>',
                           lambda e: (self.tree.yview_scroll(int(-1 * (e.delta / 120)) or
                                                            (-1 if e.delta > 0 else 1), 'units'),
                                      'break')[1])
            self.tree.bind('<Button-4>', lambda e: self.tree.yview_scroll(-1, 'units'))
            self.tree.bind('<Button-5>', lambda e: self.tree.yview_scroll(1, 'units'))

        style.configure('Green.Horizontal.TProgressbar',
                        background=self.SUCCESS, troughcolor='#1a1a24',
                        borderwidth=0, thickness=4)
        self.progress = ttk.Progressbar(bottom_area, mode='determinate',
                                        style='Green.Horizontal.TProgressbar')

        # Bottom row
        bottom_row = tk.Frame(bottom_area, bg=bg)
        bottom_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))

        left_frame = tk.Frame(bottom_row, bg=bg)
        left_frame.pack(side=tk.LEFT)
        self.matched_label = tk.Label(left_frame, text="0 已匹配", bg=bg,
                                      fg=self.SUCCESS, font=F(9, True))
        self.matched_label.pack(side=tk.LEFT)
        self.unmatched_label = tk.Label(left_frame, text="0 未匹配", bg=bg,
                                        fg=self.ERROR, font=F(9, True))
        self.unmatched_label.pack(side=tk.LEFT, padx=(12, 0))

        self.export_btn = self._accent_button(bottom_row, "导出勾选的RAW", self._export_selected,
                                              font=F(10, True), padx=20, pady=10)
        self.export_btn.pack(side=tk.LEFT, expand=True)

        right_frame = tk.Frame(bottom_row, bg=bg, width=160)
        right_frame.pack(side=tk.RIGHT)
        right_frame.pack_propagate(False)

        self.root.after(100, self._refresh_both_lists)

    def _refresh_both_lists(self):
        self._refresh_jpg_list()
        self._refresh_raw_list()

    def _set_busy(self, busy: bool):
        """Enable/disable the action buttons while a job runs."""
        self._busy = busy
        for b in (self.match_btn, self.export_btn, self.jpg_add_btn, self.jpg_img_btn, self.raw_add_btn):
            b.set_enabled(not busy)
        has_unmatched = any(not r['raw_path'] for r in self.results)
        self.exif_btn.set_enabled((not busy) and has_unmatched)

    # ── Folder management ──
    def _ask_folder(self, title):
        try:
            d = filedialog.askdirectory(title=title, parent=self.root, mustexist=True)
        except Exception:
            return ''
        # Windows dialogs return C:/a/b; normalise so paths display/compare consistently
        return os.path.normpath(d) if d else ''

    # -- source model helpers --
    @property
    def jpg_folders(self):
        return [x['path'] for x in self.jpg_sources if x['type'] == 'folder']

    @jpg_folders.setter
    def jpg_folders(self, paths):
        self.jpg_sources = [self._new_source('folder', p) for p in paths]

    @property
    def raw_folders(self):
        return [x['path'] for x in self.raw_sources]

    @raw_folders.setter
    def raw_folders(self, paths):
        self.raw_sources = [self._new_source('folder', p) for p in paths]

    def _new_source(self, typ, path, files=None):
        self._src_seq += 1
        return {'id': self._src_seq, 'type': typ, 'path': path, 'files': files or [], 'count': None}

    def _sources(self, kind):
        return self.jpg_sources if kind == 'jpg' else self.raw_sources

    def _exts(self, kind, settings=None):
        st = settings or self.settings
        if kind == 'jpg':
            return {'.jpg', '.jpeg', '.png'} | parse_extensions(st.get('jpg_extensions', ''))
        return set(RAW_EXTENSIONS) | parse_extensions(st.get('raw_extensions', ''))

    def _add_source(self, kind, src):
        self._sources(kind).append(src)
        self._start_count(kind, src)
        self._refresh_list(kind)

    def _add_jpg_folder(self):
        d = self._ask_folder("添加 JPG 文件夹")
        if d and d not in self.jpg_folders:
            self._add_source('jpg', self._new_source('folder', d))

    def _add_raw_folder(self):
        d = self._ask_folder("添加 RAW 文件夹")
        if d and d not in self.raw_folders:
            self._add_source('raw', self._new_source('folder', d))

    def _add_jpg_images(self):
        """Pick single / multiple images (file dialog shows thumbnails)."""
        try:
            files = filedialog.askopenfilenames(
                title=_SRC_TXT['pick_title'], parent=self.root,
                filetypes=[(_SRC_TXT['img_type'], "*.jpg *.jpeg *.png *.JPG *.JPEG *.PNG"),
                           (_SRC_TXT['all_type'], "*.*")])
        except Exception:
            files = ()
        if isinstance(files, str):
            files = self.root.tk.splitlist(files)
        files = [os.path.normpath(f) for f in files if f]
        if not files:
            return
        self._add_source('jpg', self._new_source('files', os.path.dirname(files[0]), files))

    def _start_count(self, kind, src):
        """Count matching files in the background; result goes through the UI queue."""
        src['count'] = None
        exts = self._exts(kind)
        sid = src['id']
        if src['type'] == 'files':
            src['count'] = sum(1 for f in src['files'] if os.path.isfile(f))
            return

        def work():
            if not os.path.isdir(src['path']):
                n = -1
            else:
                try:
                    n = sum(1 for f in get_all_files_in_folder(src['path'])
                            if os.path.splitext(f)[1].lower() in exts)
                except Exception:
                    n = -1
            self._post(self._on_count, kind, sid, n)

        threading.Thread(target=work, daemon=True).start()

    def _on_count(self, kind, sid, n):
        for x in self._sources(kind):
            if x['id'] == sid:
                x['count'] = n
                self._refresh_list(kind)
                return

    def _recount_all(self):
        for kind in ('jpg', 'raw'):
            for x in self._sources(kind):
                self._start_count(kind, x)
            self._refresh_list(kind)

    def _make_source_list(self, parent, kind):
        """Two-column list: path on the left, file count on the right."""
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure("Src.Treeview", background='#1a1a24', foreground='#c0c0d0',
                        fieldbackground='#1a1a24', bordercolor='#1a1a24',
                        lightcolor='#1a1a24', darkcolor='#1a1a24', borderwidth=0,
                        font=M(9), rowheight=int((20 + _FONT_DELTA * 2) * self._scale))
        style.layout('Src.Treeview', [('Src.Treeview.treearea', {'sticky': 'nswe'})])
        style.map("Src.Treeview", background=[('selected', '#2a2a4a')],
                  foreground=[('selected', '#e0e0f0')])
        frame = tk.Frame(parent, bg='#1a1a24', highlightbackground=self.BORDER, highlightthickness=1)
        frame.pack(fill=tk.X, pady=(6, 0))
        tree = ttk.Treeview(frame, columns=('path', 'count'), show='', height=3,
                            selectmode='browse', style="Src.Treeview")
        tree.column('path', stretch=True, width=400, anchor='w')
        tree.column('count', stretch=False, width=int(150 * self._scale), anchor='e')
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview, style="Dark.Vertical.TScrollbar")
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=4)
        tree.tag_configure('missing', foreground='#ff6b6b')
        tree.bind('<Delete>', lambda e: self._remove_selected(kind))
        tree.bind('<BackSpace>', lambda e: self._remove_selected(kind))
        tree.bind('<Double-1>', lambda e: self._open_selected(kind))
        self._bind_right_click(tree, lambda e: self._popup_source_menu(kind, e))
        return tree

    def _source_label(self, src):
        if src['type'] == 'folder':
            return src['path']
        files = src['files']
        fmt = _SRC_TXT['picked_one'] if len(files) == 1 else _SRC_TXT['picked_many']
        return fmt.format(n=len(files), first=os.path.basename(files[0]), folder=src['path'])

    def _refresh_list(self, kind):
        tree = self.jpg_list if kind == 'jpg' else self.raw_list
        total_label = self.jpg_total_label if kind == 'jpg' else self.raw_total_label
        tree.delete(*tree.get_children())
        total, pending = 0, False
        for x in self._sources(kind):
            n = x['count']
            if n is None:
                cnt, pending = _SRC_TXT['counting'], True
            elif n < 0:
                cnt = _SRC_TXT['missing']
            else:
                cnt = _SRC_TXT[kind + '_count'].format(n=n)
                total += n
            tree.insert('', tk.END, iid=str(x['id']), values=(self._source_label(x), cnt),
                        tags=('missing',) if (n is not None and n < 0) else ())
        if not self._sources(kind):
            total_label.config(text="")
        elif pending:
            total_label.config(text=_SRC_TXT['counting'])
        else:
            total_label.config(text=_SRC_TXT[kind + '_total'].format(n=total))

    def _selected_source(self, kind):
        tree = self.jpg_list if kind == 'jpg' else self.raw_list
        sel = tree.selection()
        if not sel:
            return None
        for x in self._sources(kind):
            if str(x['id']) == sel[0]:
                return x
        return None

    def _open_selected(self, kind):
        x = self._selected_source(kind)
        if x:
            open_in_finder(x['path'])

    def _remove_selected(self, kind):
        x = self._selected_source(kind)
        if x and not self._busy:
            self._sources(kind).remove(x)
            self._refresh_list(kind)

    def _popup_source_menu(self, kind, event):
        tree = self.jpg_list if kind == 'jpg' else self.raw_list
        row = tree.identify_row(event.y)
        if not row:
            return
        tree.selection_set(row)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="在访达中显示" if _IS_MACOS else "打开文件夹",
                         command=lambda: self._open_selected(kind))
        menu.add_command(label="删除此路径", command=lambda: self._remove_selected(kind))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # kept for compatibility (self-test)
    def _refresh_jpg_list(self):
        self._refresh_list('jpg')

    def _refresh_raw_list(self):
        self._refresh_list('raw')

    def _browse_out(self):
        d = self._ask_folder("选择输出目录")
        if d:
            self.out_entry.delete(0, tk.END)
            self.out_entry.insert(0, d)
            self.out_entry.config(fg=self.INK)

    # ── Config persistence ──
    def _load_config(self):
        try:
            path = _CONFIG_PATH
            if not os.path.exists(path):
                # v2.0 stored '.jpg_find_raw.json' next to the exe/script
                legacy_dir = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
                              else os.path.dirname(os.path.abspath(__file__)))
                legacy = os.path.join(legacy_dir, '.jpg_find_raw.json')
                if os.path.exists(legacy):
                    path = legacy
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # Folder paths are intentionally NOT restored: the app always starts empty.
                s = data.get('settings', {})
                for k in self.settings:
                    if k in s:
                        self.settings[k] = s[k]
        except Exception:
            pass

    def _save_config(self, show_error=True):
        try:
            data = {'settings': self.settings}   # folder paths are not saved
            tmp = _CONFIG_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _CONFIG_PATH)
            return True
        except Exception as e:
            if show_error:
                self._show_error("保存失败", f"无法保存设置到：\n{_CONFIG_PATH}\n\n错误：{e}")
            return False

    def _open_settings(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("设置")
        dlg.resizable(False, False)
        dlg.configure(bg=self.BG)
        dlg.transient(self.root)

        bg = self.BG
        surface = self.SURFACE
        border = self.BORDER
        ink = self.INK
        ash = self.ASH
        accent = self.ACCENT

        frame = tk.Frame(dlg, bg=bg, padx=16, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)

        filter_chinese_var = tk.BooleanVar(value=self.settings.get('filter_chinese', True))
        tk.Checkbutton(frame, text="过滤中文（匹配时忽略纯中文字符）",
                       variable=filter_chinese_var, bg=bg, fg=ink, selectcolor=surface,
                       activebackground=bg, activeforeground=ink, highlightthickness=0, bd=0,
                       font=F(10), wraplength=420, justify=tk.LEFT).pack(anchor="w", pady=(0, 12))

        create_raw_var = tk.BooleanVar(value=self.settings.get('create_raw_folder', True))
        tk.Checkbutton(frame, text="新建raw文件夹（导出时在JPG目录下创建raw子文件夹）",
                       variable=create_raw_var, bg=bg, fg=ink, selectcolor=surface,
                       activebackground=bg, activeforeground=ink, highlightthickness=0, bd=0,
                       font=F(10), wraplength=420, justify=tk.LEFT).pack(anchor="w", pady=(0, 16))

        tk.Frame(frame, bg=border, height=1).pack(fill=tk.X, pady=(0, 14))

        export_row = tk.Frame(frame, bg=bg)
        export_row.pack(fill=tk.X, pady=(0, 16))
        tk.Label(export_row, text="导出方式：", bg=bg, fg=accent,
                 font=F(10, True)).pack(side=tk.LEFT)
        export_var = tk.StringVar(value=self.settings.get('export_method', 'copy'))
        for text, val, padx in (("复制", 'copy', (10, 20)), ("剪切", 'cut', 0)):
            tk.Radiobutton(export_row, text=text, variable=export_var, value=val,
                           bg=bg, fg=ink, selectcolor=surface,
                           activebackground=bg, activeforeground=ink,
                           highlightthickness=0, bd=0, font=F(10)).pack(side=tk.LEFT, padx=padx)

        tk.Frame(frame, bg=border, height=1).pack(fill=tk.X, pady=(0, 14))

        ext_header_row = tk.Frame(frame, bg=bg)
        ext_header_row.pack(fill=tk.X, pady=(0, 4))
        tk.Label(ext_header_row, text="扩展名设置", bg=bg, fg=accent,
                 font=F(10, True)).pack(side=tk.LEFT)
        self._make_info_icon(ext_header_row, self._get_formats_tooltip_text()).pack(side=tk.LEFT, padx=(6, 0))

        tk.Label(frame, text="下方可添加额外扩展名格式（在默认格式之外）。",
                 bg=bg, fg='#5a5a70', font=F(8)).pack(anchor="w", pady=(0, 10))

        def entry(label, key, pady_after=10):
            tk.Label(frame, text=label, bg=bg, fg=ash, font=F(9)).pack(anchor="w")
            e = tk.Entry(frame, font=M(9), bg=surface, fg=ink, insertbackground=ink,
                         relief="flat", bd=0, highlightthickness=1,
                         highlightbackground=border, highlightcolor=accent)
            e.pack(fill=tk.X, ipady=5, pady=(2, pady_after))
            e.insert(0, self.settings.get(key, ''))
            return e

        jpg_ext_entry = entry("JPG 目录扩展名：", 'jpg_extensions')
        raw_ext_entry = entry("RAW 目录扩展名：", 'raw_extensions')

        tk.Label(frame, text="示例：.tif    jpg/png/tif    .jpg,.png    JPG,PNG",
                 bg=bg, fg='#5a5a70', font=F(8)).pack(anchor="w", pady=(0, 14))

        tk.Frame(frame, bg=border, height=1).pack(fill=tk.X, pady=(0, 14))

        tk.Label(frame, text="JPG 文件名过滤（多个关键词用逗号隔开）",
                 bg=bg, fg=accent, font=F(10, True)).pack(anchor="w", pady=(0, 10))

        jpg_prefix_entry = entry("JPG 前缀过滤：", 'jpg_prefix_filters')
        jpg_suffix_entry = entry("JPG 后缀过滤：", 'jpg_suffix_filters', pady_after=6)

        tk.Label(frame, text="示例：_DSC0531.jpg 前缀输入 _  →  DSC0531 匹配 DSC0531.CR2",
                 bg=bg, fg='#5a5a70', font=F(8)).pack(anchor="w", pady=(0, 4))

        btn_row = tk.Frame(frame, bg=bg)
        btn_row.pack(fill=tk.X, pady=(14, 0))

        def close():
            try:
                dlg.grab_release()
            except tk.TclError:
                pass
            dlg.destroy()

        def save_and_close():
            self.settings['filter_chinese'] = filter_chinese_var.get()
            self.settings['create_raw_folder'] = create_raw_var.get()
            self.settings['export_method'] = export_var.get()
            self.settings['jpg_extensions'] = jpg_ext_entry.get().strip()
            self.settings['raw_extensions'] = raw_ext_entry.get().strip()
            self.settings['jpg_prefix_filters'] = jpg_prefix_entry.get().strip()
            self.settings['jpg_suffix_filters'] = jpg_suffix_entry.get().strip()
            if self._save_config():
                self.status_label.config(text="设置已保存")
            self._recount_all()
            close()

        dlg.protocol('WM_DELETE_WINDOW', save_and_close)

        FlatButton(btn_row, "取消", close, bg=surface, fg=ash, hover_bg=border, hover_fg=ink,
                   font=F(9), padx=16, pady=6).pack(side=tk.RIGHT, padx=(8, 0))
        self._accent_button(btn_row, "保存", save_and_close,
                            font=F(9, True), padx=16, pady=6).pack(side=tk.RIGHT)

        # Size to content (fonts differ per platform), capped to the screen
        dlg.update_idletasks()
        sh = dlg.winfo_screenheight()
        w = max(int(480 * self._scale), dlg.winfo_reqwidth())
        h = min(dlg.winfo_reqheight(), sh - 80)
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        y = max(25, min(y, sh - h - 40))
        dlg.geometry(f"{w}x{h}+{max(x, 0)}+{y}")
        try:
            dlg.wait_visibility()
            dlg.grab_set()
        except tk.TclError:
            pass

    def _on_close(self):
        self._save_config(show_error=False)
        self.root.destroy()

    # ── Output directory placeholder ──
    def _on_out_focus_in(self, event):
        if self.out_entry.get() == self._out_placeholder:
            self.out_entry.delete(0, tk.END)
            self.out_entry.config(fg=self.INK)

    def _on_out_focus_out(self, event):
        if not self.out_entry.get().strip():
            self.out_entry.delete(0, tk.END)
            self.out_entry.insert(0, self._out_placeholder)
            self.out_entry.config(fg='#5a5a70')

    # ── Matching logic ──
    def _check_folders(self):
        """Return list of sources that are no longer reachable."""
        missing = []
        for x in self.jpg_sources + self.raw_sources:
            if x['type'] == 'folder' and not os.path.isdir(x['path']):
                missing.append(x['path'])
            elif x['type'] == 'files' and not any(os.path.isfile(f) for f in x['files']):
                missing.append(self._source_label(x))
        return missing

    def _collect_jpg_files(self, settings, jpg_sources):
        exts = self._exts('jpg', settings)
        files, seen = [], set()
        for x in jpg_sources:
            if x['type'] == 'files':
                cand = [f for f in x['files'] if os.path.isfile(f)]
            else:
                cand = [f for f in get_all_files_in_folder(x['path'])
                        if os.path.splitext(f)[1].lower() in exts]
            for f in cand:
                if f not in seen:          # same image added twice → only once
                    seen.add(f)
                    files.append(f)
        return files

    def _collect_raw_files(self, settings, raw_sources):
        exts = self._exts('raw', settings)
        files, seen = [], set()
        for x in raw_sources:
            for f in get_all_files_in_folder(x['path']):
                if os.path.splitext(f)[1].lower() in exts and f not in seen:
                    seen.add(f)
                    files.append(f)
        return files

    def _precheck(self):
        if self._busy:
            return False
        if not self.jpg_sources:
            if not self._quiet:
                messagebox.showwarning("提示", "请至少添加一个 JPG 文件夹", parent=self.root)
            return False
        if not self.raw_folders:
            if not self._quiet:
                messagebox.showwarning("提示", "请至少添加一个 RAW 文件夹", parent=self.root)
            return False
        missing = self._check_folders()
        if missing:
            self._show_error("文件夹不可用",
                             "找不到以下文件夹，外接硬盘是否已连接？\n\n"
                             + "\n".join(missing))
            return False
        return True

    def _start_match(self):
        """Filename matching"""
        if not self._precheck():
            return

        self._set_busy(True)
        self.status_label.config(text="扫描中...")
        self.tree.delete(*self.tree.get_children())
        self.results = []

        # Snapshot state for the worker thread (never read Tk widgets from threads)
        settings = dict(self.settings)
        jpg_folders = [dict(x) for x in self.jpg_sources]
        raw_folders = [dict(x) for x in self.raw_sources]

        def worker():
            try:
                jpg_files = self._collect_jpg_files(settings, jpg_folders)
                if not jpg_files:
                    self._post(self._on_match_done, [], "未找到JPG文件")
                    return
                self._post(self.status_label.config, {'text': f"找到 {len(jpg_files)} 张 JPG，正在扫描 RAW 文件夹..."})
                raw_files = self._collect_raw_files(settings, raw_folders)
                self._post(self.status_label.config, {'text': f"正在匹配 {len(jpg_files)} 张 JPG 与 {len(raw_files)} 张 RAW..."})
                results = match_jpg_to_raw(jpg_files, raw_files, settings)
                self._post(self._on_match_done, results, "文件名匹配完成")
            except Exception as e:
                traceback.print_exc()
                self._post(self._on_worker_error, "匹配失败", e)

        threading.Thread(target=worker, daemon=True).start()

    def _start_exif(self):
        """EXIF matching"""
        if not self._precheck():
            return

        self._set_busy(True)
        self.status_label.config(text="EXIF匹配中...")

        settings = dict(self.settings)
        jpg_folders = [dict(x) for x in self.jpg_sources]
        raw_folders = [dict(x) for x in self.raw_sources]
        prev_results = list(self.results)

        def progress(c, t):
            self._post(self.status_label.config, {'text': f"正在读取 EXIF：{c}/{t} 张 RAW"})

        def worker():
            try:
                jpg_files = self._collect_jpg_files(settings, jpg_folders)
                if not jpg_files:
                    self._post(self._on_match_done, [], "未找到JPG文件")
                    return
                raw_files = self._collect_raw_files(settings, raw_folders)

                base_results = list(prev_results)
                existing_jpg = {r['jpg_path'] for r in base_results}
                for f in jpg_files:
                    if f not in existing_jpg:
                        base_results.append({'jpg_path': f, 'jpg_name': os.path.basename(f),
                                             'raw_path': None, 'raw_name': None, 'method': None})

                # RAWs already used by a filename match must not be reassigned
                used = {r['raw_path'] for r in base_results if r['raw_path']}
                unmatched = [r for r in base_results if not r['raw_path']]
                matched = match_by_exif(unmatched, [r for r in raw_files if r not in used],
                                        progress_cb=progress)
                matched_dict = {r['jpg_path']: r for r in matched}
                final = [matched_dict.get(r['jpg_path'], r) for r in base_results]
                self._post(self._on_match_done, final, "EXIF匹配完成")
            except Exception as e:
                traceback.print_exc()
                self._post(self._on_worker_error, "EXIF 匹配失败", e)

        threading.Thread(target=worker, daemon=True).start()

    def _on_worker_error(self, title, exc):
        self._set_busy(False)
        self.status_label.config(text=f"{title}")
        self._show_error(title, f"{exc}\n\n如果照片在外接硬盘上，请确认硬盘已连接，并已允许本软件访问"
                                "（系统设置 → 隐私与安全性 → 文件与文件夹）。")

    def _on_match_done(self, results, status):
        self.results = results
        matched = sum(1 for r in results if r['raw_path'])
        unmatched = len(results) - matched
        self.matched_label.config(text=f"{matched} 已匹配")
        self.unmatched_label.config(text=f"{unmatched} 未匹配")
        self.stats_label.config(text=f"{len(results)} JPG | {matched} 匹配 | {unmatched} 未匹配")
        self.status_label.config(text=status)

        self.tree.delete(*self.tree.get_children())
        self.selected_rows = set()
        self.tree.tag_configure('notfound', foreground='#ff6b6b')
        self.tree.tag_configure('found', foreground='#c0c0d0')
        for r in results:
            item = self.tree.insert('', tk.END, values=(
                '☑' if r['raw_path'] else '☐',
                r['jpg_name'],
                r['raw_name'] or '- 未找到 -',
                {'Filename': '文件名', 'EXIF': 'EXIF'}.get(r['method'], '-')
            ), tags=('found' if r['raw_path'] else 'notfound',))
            if r['raw_path']:
                self.selected_rows.add(item)
        self._set_busy(False)

    def _on_tree_click(self, event):
        """Click checkbox column"""
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != '#1':
            return
        item = self.tree.identify_row(event.y)
        if not item:
            return
        if item in self.selected_rows:
            self.selected_rows.discard(item)
            self.tree.set(item, 'checked', '☐')
        else:
            self.selected_rows.add(item)
            self.tree.set(item, 'checked', '☑')

    def _on_tree_double_click(self, event):
        """Double-click to open corresponding file"""
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        col = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if not item:
            return
        idx = self.tree.index(item)
        if 0 <= idx < len(self.results):
            result = self.results[idx]
            if col == '#2':
                open_in_finder(result.get('jpg_path'))
            elif col == '#3':
                open_in_finder(result.get('raw_path'))

    def _find_jpg_folder_for_file(self, jpg_path):
        for folder in self.jpg_folders:
            if jpg_path.startswith(folder.rstrip('/\\') + os.sep) or jpg_path.startswith(folder.rstrip('/') + '/'):
                return folder
        return None

    def _export_selected(self):
        """Export selected RAW files"""
        if self._busy:
            return
        if not self.selected_rows:
            self._show_info("提示", "没有选中的RAW文件")
            return

        out_dir = self.out_entry.get().strip()
        if out_dir == self._out_placeholder:
            out_dir = ''

        folder_files = {}  # dst_folder -> [{'src','dst','basename'}]
        for item in self.selected_rows:
            try:
                idx = self.tree.index(item)
            except tk.TclError:
                continue
            if not (0 <= idx < len(self.results)):
                continue
            result = self.results[idx]
            raw_path = result.get('raw_path')
            jpg_path = result.get('jpg_path')
            if not raw_path or not os.path.exists(raw_path):
                continue
            if out_dir:
                target_folder = out_dir
            else:
                jpg_dir = os.path.dirname(jpg_path) if jpg_path else None
                if not jpg_dir:
                    jpg_dir = (self._find_jpg_folder_for_file(jpg_path) if jpg_path else None) \
                        or (self.jpg_folders[0] if self.jpg_folders else None)
                if not jpg_dir:
                    continue
                target_folder = os.path.join(jpg_dir, 'raw') if self.settings.get('create_raw_folder', True) else jpg_dir

            basename = os.path.basename(raw_path)
            folder_files.setdefault(target_folder, []).append(
                {'src': raw_path, 'dst': os.path.join(target_folder, basename), 'basename': basename})

        if not folder_files:
            self._show_info("提示", "没有可导出的RAW文件")
            return

        conflict_files = []
        for folder, files in folder_files.items():
            seen = set()
            for f in files:
                if os.path.exists(f['dst']) or f['basename'] in seen:
                    conflict_files.append(f['basename'])
                seen.add(f['basename'])

        if conflict_files and not self._quiet:
            preview = ', '.join(conflict_files[:10])
            if len(conflict_files) > 10:
                preview += f' ...等共 {len(conflict_files)} 个'
            msg = (f"目标文件夹已有 {len(conflict_files)} 个同名文件："
                   f"\n\n{preview}\n\n是否覆盖？")
            if not messagebox.askyesno("覆盖确认", msg, parent=self.root):
                return

        # Create target folders up-front and report problems clearly
        bad_folders = []
        for folder in folder_files:
            try:
                os.makedirs(folder, exist_ok=True)
                if not os.access(folder, os.W_OK):
                    raise PermissionError("文件夹为只读")
            except Exception as e:
                bad_folders.append(f"{folder}\n    ({e})")
        if bad_folders:
            self._show_error("无法写入输出文件夹",
                             "以下文件夹无法创建或写入：\n\n"
                             + "\n".join(bad_folders)
                             + "\n\n注意：NTFS 格式的硬盘在 macOS 上是只读的，"
                               "请换一个输出目录。")
            return

        tasks = [(f['src'], f['dst'], folder) for folder, files in folder_files.items() for f in files]
        export_method = self.settings.get('export_method', 'copy')

        self._set_busy(True)
        self.progress['maximum'] = len(tasks)
        self.progress['value'] = 0
        self.progress.pack(side=tk.TOP, fill=tk.X, pady=(4, 0))
        self.status_label.config(text=f"正在导出 {len(tasks)} 个文件...")

        COPY_WORKERS = 4
        state = {'done': 0, 'ok': 0, 'errors': [], 'folders': set()}
        lock = threading.Lock()

        def copy_one(src_path, dst_path, dst_folder):
            err = None
            try:
                if export_method == 'cut':
                    shutil.move(src_path, dst_path)
                else:
                    shutil.copy2(src_path, dst_path)
            except Exception as e:
                err = f"{os.path.basename(src_path)}: {e}"
            with lock:
                state['done'] += 1
                if err:
                    state['errors'].append(err)
                else:
                    state['ok'] += 1
                    state['folders'].add(dst_folder)
                return state['done']

        def update_progress(v):
            self.progress.config(value=v)
            self.status_label.config(text=f"正在导出... {v}/{len(tasks)}")

        def worker():
            try:
                with ThreadPoolExecutor(max_workers=COPY_WORKERS) as executor:
                    futures = [executor.submit(copy_one, *t) for t in tasks]
                    last_posted = 0
                    for fut in as_completed(futures):
                        done = fut.result()
                        # throttle UI updates
                        if done - last_posted >= max(1, len(tasks) // 200) or done == len(tasks):
                            last_posted = done
                            self._post(update_progress, done)
            except Exception as e:
                traceback.print_exc()
                with lock:
                    state['errors'].append(str(e))
            self._post(_done)

        def _done():
            self.progress.pack_forget()
            self._set_busy(False)
            action = '剪切' if export_method == 'cut' else '复制'
            count, errors = state['ok'], state['errors']
            self._last_export = (count, len(errors), sorted(state['folders']))
            self.status_label.config(text=f"已{action} {count} 个文件" + (f"，{len(errors)} 个失败" if errors else ""))
            folder_list = '\n'.join(sorted(state['folders']))
            msg = f"已{action} {count} 个文件到：\n{folder_list}"
            if errors:
                msg += f"\n\n{len(errors)} 个文件失败：\n" + "\n".join(errors[:10])
                if len(errors) > 10:
                    msg += f"\n... 还有 {len(errors) - 10} 个"
                self._show_error("导出完成（有错误）", msg)
            else:
                self._show_info("完成", msg)

        threading.Thread(target=worker, daemon=True).start()

    # ── Self-test (used by the CI build to verify the packaged app) ──
    def run_selftest(self, base, result_file):
        import time
        self._quiet = True
        if os.environ.get('JFR_SELFTEST_PICK'):
            picked = [f for f in get_all_files_in_folder(os.path.join(base, 'jpg'))
                      if f.lower().endswith('.jpg')]
            self.jpg_sources = [self._new_source('files', os.path.join(base, 'jpg'), picked)]
        else:
            self.jpg_folders = [os.path.join(base, 'jpg')]
        self.raw_folders = [os.path.join(base, 'raw')]
        self._recount_all()
        self.out_entry.delete(0, tk.END)
        self.out_entry.insert(0, os.path.join(base, 'out'))
        st = {'stage': 'match', 't0': time.time()}

        def finish(ok, info):
            with open(result_file, 'w') as f:
                json.dump({'ok': ok, **info}, f)
            self.root.destroy()

        def tick():
            if time.time() - st['t0'] > 60:
                return finish(False, {'error': f'timeout in stage {st["stage"]}'})
            if st['stage'] == 'match' and not self._busy and self.results:
                st['stage'] = 'exif'
                self._start_exif()
            elif st['stage'] == 'exif' and not self._busy:
                st['stage'] = 'export'
                self._export_selected()
            elif st['stage'] == 'export' and self._last_export is not None:
                count, failed, _ = self._last_export
                matched = {r['jpg_name']: (r['raw_name'], r['method']) for r in self.results}
                out_files = sorted(os.listdir(os.path.join(base, 'out')))
                return finish(True, {'tk_console': bool(self.root.tk.call('info', 'commands', 'console')), 'matched': matched, 'copied': count,
                                     'failed': failed, 'out_files': out_files})
            self.root.after(200, tick)

        self._start_match()
        self.root.after(200, tick)


# ============================================================
# Entry point
# ============================================================

def _disable_tk_console_on_macos():
    """Tk opens a hidden console when stdin looks like /dev/null (Finder launch).
    Replace stdin with a pipe so Tk skips it (avoids a menubar crash seen on macOS 11)."""
    if not _IS_MACOS:
        return
    import stat
    try:
        st = os.fstat(0)
        nullish = (not os.isatty(0)) and stat.S_ISCHR(st.st_mode)
    except OSError:
        nullish = True
    if nullish:
        try:
            r, w = os.pipe()
            os.dup2(r, 0)
            os.close(r)
            globals()['_STDIN_PIPE_W'] = w   # keep the write end open
        except OSError:
            pass


def main():
    _disable_tk_console_on_macos()
    if _IS_WINDOWS:
        # Must be called before the first window is created, otherwise Windows
        # bitmap-stretches the window on high-DPI screens (blurry text).
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    root = tk.Tk()
    app = FindRawApp(root)
    demo = os.environ.get('JFR_PREFILL_DIR')          # CI screenshots only
    if demo:
        jd = os.path.join(demo, 'jpg')
        app._add_source('jpg', app._new_source('folder', jd))
        app._add_source('jpg', app._new_source('files', jd, [os.path.join(jd, 'DSC_0001.jpg'),
                                                             os.path.join(jd, 'holiday.jpg')]))
        app._add_source('raw', app._new_source('folder', os.path.join(demo, 'raw')))
    selftest_dir = os.environ.get('JFR_SELFTEST_DIR')
    if selftest_dir:
        app.run_selftest(selftest_dir, os.environ.get('JFR_SELFTEST_RESULT',
                                                      os.path.join(selftest_dir, 'result.json')))
    root.mainloop()


if __name__ == '__main__':
    main()
