#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RawPicker 图片筛选工具（中文版）v3.0
====================================
根据挑选好的 JPG，从 RAW 文件夹中找出对应的 RAW 文件，复制到输出文件夹。

v3.0 相比旧版：
  - JPG 来源支持「选择文件夹」或「选择图片」（可单张/多张，选择窗口里能看到缩略图）
  - 每次打开都是空路径（不再记住上次的路径）
  - 修复：输出文件夹 = RAW 文件夹时会把 RAW 原片清空（数据丢失）
  - 修复：exFAT / NAS 上 copystat 失败时会把复制成功的文件删掉
  - 修复：选择文件夹后统计数量会卡住界面；大量文件时匹配极慢
  - 修复：同名 RAW（不同子文件夹）复制时互相覆盖；取消后仍显示“完成”
  - 修复：统计结果不准确，复制失败不提示；并行线程数按 JPG 盘判断（应按 RAW 盘）
  - 匹配规则：去掉中文后英文+数字必须与 RAW 名完全一样（不再模糊匹配，避免 A7S01234↔DSC01234 误匹配）；不复制 XMP
"""

import os
import re
import sys
import json
import time
import queue
import shutil
import threading
import traceback
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from concurrent.futures import ThreadPoolExecutor, as_completed

APP_TITLE = "RawPicker 图片筛选工具"
APP_VERSION = "3.0.3"

_IS_MACOS = sys.platform == 'darwin'
_IS_WINDOWS = sys.platform.startswith('win')


# ============================================================
# 常量 / 平台
# ============================================================

def _get_config_dir():
    if _IS_MACOS:
        base = os.path.expanduser("~/Library/Application Support")
    elif _IS_WINDOWS:
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.path.expanduser("~/.config")
    d = os.path.join(base, "JpgChooseraw")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.expanduser("~")
    return d


UNMATCHED_FILES = os.path.join(_get_config_dir(), "unmatched_files.txt")

JPG_EXTENSIONS = ('.jpg', '.jpeg', '.png')
# RAW 文件夹保留: RAW文件 + JPG（有时小JPG在大JPG文件夹里需要匹配）；过滤 XMP、txt 等
RAW_EXTENSIONS = {'.cr2', '.cr3', '.nef', '.arw', '.orf', '.raf',
                  '.rw2', '.dng', '.pef', '.srw', '.3fr', '.kdc',
                  '.mrw', '.raw', '.rwl', '.sr2', '.srf', '.x3f'}
RAW_FOLDER_ALLOWED = RAW_EXTENSIONS | {'.jpg', '.jpeg', '.png'}

COPY_BUFFER_SIZE = 8 * 1024 * 1024

if _IS_MACOS:
    _UI_FAMILY, _MONO_FAMILY, _FONT_DELTA = "PingFang SC", "Menlo", 3
elif _IS_WINDOWS:
    _UI_FAMILY, _MONO_FAMILY, _FONT_DELTA = "Microsoft YaHei UI", "Consolas", 0
else:
    _UI_FAMILY, _MONO_FAMILY, _FONT_DELTA = "Noto Sans CJK SC", "Noto Sans Mono CJK SC", 0


def F(size, bold=False):
    return (_UI_FAMILY, size + _FONT_DELTA, 'bold') if bold else (_UI_FAMILY, size + _FONT_DELTA)


def M(size):
    return (_MONO_FAMILY, size + _FONT_DELTA)


def open_with_system(path):
    """用系统默认程序打开文件/文件夹"""
    try:
        if _IS_WINDOWS:
            os.startfile(path)  # noqa
        elif _IS_MACOS:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def _detect_copy_workers(src_dir, dst_dir):
    """源/目标同一设备：2 线程（避免机械盘磁头争抢）；不同设备：8 线程"""
    try:
        return 2 if os.stat(src_dir).st_dev == os.stat(dst_dir).st_dev else 8
    except Exception:
        return 4


# ============================================================
# 文件名匹配（规则与旧版一致）
# ============================================================

def is_hidden_file(filepath):
    filename = os.path.basename(filepath)
    return filename.startswith('.') or filename.startswith('~')


_TOKEN_RE = re.compile(r'[A-Za-z0-9_]+')


def jpg_match_keys(jpg_filename):
    """JPG 可用来匹配 RAW 的名字（全部转小写比较）

    中文过滤：去掉中文、空格、横杠、括号等，剩下的每一段“英文+数字”必须和
    RAW 文件名（不含扩展名）完全一样才算匹配。
      婚礼DSC0002_修图.jpg → DSC0002    _DSC8746 副本.jpg → _DSC8746
      IMG_1234-edit.jpg    → IMG_1234   A7S01234修图.jpg  → A7S01234（≠ DSC01234）
    """
    stem = os.path.splitext(jpg_filename)[0]
    keys = {stem.lower()}                       # 完整文件名
    for t in _TOKEN_RE.findall(stem):
        if not re.search(r'\d', t):            # 只看带编号的片段（edit、HDR 之类不算）
            continue
        keys.add(t.lower())
        t2 = t.strip('_')                       # “婚礼_DSC0002_修图” 两边的下划线是分隔符
        if t2:
            keys.add(t2.lower())
    return keys


def is_filename_match(raw_filename, jpg_filename):
    """RAW 是否对应这张 JPG（英文+数字部分完全一致）"""
    return os.path.splitext(raw_filename)[0].lower() in jpg_match_keys(jpg_filename)


def match_files(jpg_image_files, raw_files, output_path, is_cancelled=lambda: False):
    """匹配 JPG 与 RAW，返回 (matched: raw_path → 目标路径, matched_jpg_set)

    规则：RAW 文件名（不含扩展名）与 JPG 文件名、或 JPG 去掉中文后的某一段英文+数字
    完全一样（不区分大小写）。不再做编号的模糊匹配，避免 A7S01234 ↔ DSC01234 这类误匹配。
    """
    raw_name_to_paths = {}
    for raw_file in raw_files:
        raw_name = os.path.splitext(os.path.basename(raw_file))[0]
        raw_name_to_paths.setdefault(raw_name.lower(), []).append(raw_file)

    key_to_jpgs = {}
    for idx, f in enumerate(jpg_image_files):
        for k in jpg_match_keys(os.path.basename(f)):
            key_to_jpgs.setdefault(k, set()).add(idx)

    matched_raw_names = set()
    matched_jpg_idx = set()
    for raw_name in raw_name_to_paths:
        if is_cancelled():
            break
        if raw_name in key_to_jpgs:
            matched_raw_names.add(raw_name)
            matched_jpg_idx.update(key_to_jpgs[raw_name])

    # ---- 生成复制计划（同名 RAW 自动改名，避免互相覆盖）----
    matched = {}
    used_names = set()
    for raw_name, paths in raw_name_to_paths.items():
        if raw_name not in matched_raw_names:
            continue
        for p in paths:
            base = os.path.basename(p)
            stem, ext = os.path.splitext(base)
            name, n = base, 1
            while name.lower() in used_names:
                name = f"{stem}_{n}{ext}"
                n += 1
            used_names.add(name.lower())
            matched[p] = os.path.join(output_path, name)

    matched_jpg_set = {jpg_image_files[i] for i in matched_jpg_idx}
    return matched, matched_jpg_set


# ============================================================
# 文件工具
# ============================================================

def get_all_files(folder):
    """递归获取文件夹中所有文件（忽略隐藏文件，读不了的子文件夹跳过）"""
    file_list = []
    if not folder or not os.path.isdir(folder):
        return file_list
    for root, dirs, files in os.walk(folder, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if not is_hidden_file(d) and not d.startswith('$')
                   and d != 'System Volume Information']
        for file in files:
            if not is_hidden_file(file):
                file_list.append(os.path.join(root, file))
    return file_list


def _cleanup(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _copystat_quiet(src, dst):
    """复制时间等属性；exFAT/NAS 上可能失败，失败不影响复制结果"""
    try:
        shutil.copystat(src, dst)
    except Exception:
        pass


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except Exception:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def safe_copy(src, dst):
    """复制文件；返回 (成功?, 错误信息)"""
    if os.path.exists(dst) and _same_file(src, dst):
        return True, None          # 源和目标是同一个文件：什么都不做（旧版会把原片清空）
    last_err = None
    try:
        with open(src, 'rb') as fsrc, open(dst, 'wb', buffering=COPY_BUFFER_SIZE) as fdst:
            shutil.copyfileobj(fsrc, fdst, COPY_BUFFER_SIZE)
        _copystat_quiet(src, dst)
        return True, None
    except Exception as e:
        last_err = e
        _cleanup(dst)
    for _ in range(2):
        tmp = dst + ".tmp"
        try:
            with open(src, 'rb') as fsrc, open(tmp, 'wb', buffering=COPY_BUFFER_SIZE) as fdst:
                shutil.copyfileobj(fsrc, fdst, COPY_BUFFER_SIZE)
            if os.path.getsize(tmp) == os.path.getsize(src):
                os.replace(tmp, dst)
                _copystat_quiet(src, dst)
                return True, None
            _cleanup(tmp)
            last_err = IOError("复制后大小不一致")
        except Exception as e:
            last_err = e
            _cleanup(tmp)
            time.sleep(0.05)
    return False, str(last_err)


def format_seconds(seconds):
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


# ============================================================
# 后台工作线程
# ============================================================

class FilterWorker:
    """后台扫描、匹配、并行复制；通过 queue 与界面通信（不直接操作界面）"""

    def __init__(self, jpg_source, raw_path, output_path):
        # jpg_source: ('folder', path) 或 ('files', [path, ...])
        self.jpg_source = jpg_source
        self.raw_path = raw_path
        self.output_path = output_path
        self.cancel_event = threading.Event()
        self.progress_queue = queue.Queue()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancel_event.set()

    def _send(self, *msg):
        self.progress_queue.put(msg)

    def _cancelled(self):
        return self.cancel_event.is_set()

    def _run(self):
        try:
            mode, src = self.jpg_source
            if mode == 'files':
                self._send("status", "正在读取选中的图片...")
                jpg_image_files = [f for f in src if os.path.isfile(f)]
            else:
                self._send("status", "正在扫描 JPG 文件夹...")
                jpg_image_files = [f for f in get_all_files(src)
                                   if f.lower().endswith(JPG_EXTENSIONS)]
            if not jpg_image_files:
                self._send("error", "没有找到 JPG 图片。")
                return
            if self._cancelled():
                self._send("cancelled", 0)
                return

            self._send("status", "正在扫描 RAW 文件夹...")
            raw_files = [f for f in get_all_files(self.raw_path)
                         if os.path.splitext(f)[1].lower() in RAW_FOLDER_ALLOWED]
            if not raw_files:
                self._send("error", "RAW 文件夹中没有可用文件！\n"
                                    "支持格式：CR2、CR3、NEF、ARW、ORF、RAF、RW2、DNG、JPG 等")
                return
            if self._cancelled():
                self._send("cancelled", 0)
                return

            self._send("status", f"正在匹配：{len(jpg_image_files)} 张 JPG，{len(raw_files)} 张 RAW...")
            matched, matched_jpg_set = match_files(jpg_image_files, raw_files,
                                                   self.output_path, self._cancelled)
            if self._cancelled():
                self._send("cancelled", 0)
                return

            unmatched = [f for f in jpg_image_files if f not in matched_jpg_set]
            if unmatched:
                try:
                    with open(UNMATCHED_FILES, "w", encoding="utf-8") as f:
                        f.write(f"以下 {len(unmatched)} 张 JPG 没有找到对应的 RAW 文件：\n")
                        f.write("=" * 50 + "\n")
                        for jpg_file in unmatched:
                            f.write(jpg_file + "\n")
                except Exception:
                    pass

            self._copy_all(matched, len(jpg_image_files), unmatched)
        except Exception as e:
            traceback.print_exc()
            self._send("error", f"发生错误：\n{e}")

    def _copy_all(self, matched, total_jpg, unmatched):
        total = len(matched)
        summary = {'total_jpg': total_jpg, 'unmatched': unmatched,
                   'copied': 0, 'failed': [], 'planned': total}
        if total == 0:
            self._send("done", summary)
            return
        workers = _detect_copy_workers(self.raw_path, self.output_path)
        start = time.time()
        done_count = 0
        last_sent = 0.0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(self._copy_one, s, d): (s, d) for s, d in matched.items()}
            for fut in as_completed(futures):
                src, _ = futures[fut]
                try:
                    ok, err = fut.result()
                except Exception as e:
                    ok, err = False, str(e)
                if ok:
                    summary['copied'] += 1
                elif err != "cancelled":
                    summary['failed'].append(f"{os.path.basename(src)}：{err}")
                done_count += 1
                now = time.time()
                if now - last_sent > 0.1 or done_count == total:
                    last_sent = now
                    elapsed = now - start
                    rate = done_count / elapsed if elapsed > 0 else 0
                    remaining = (total - done_count) / rate if rate > 0 else None
                    self._send("progress", done_count, total, os.path.basename(src), elapsed, remaining)
                if self._cancelled():
                    for f in futures:
                        f.cancel()
        if self._cancelled():
            self._send("cancelled", summary['copied'])
        else:
            self._send("done", summary)

    def _copy_one(self, src, dst):
        if self._cancelled():
            return False, "cancelled"
        return safe_copy(src, dst)


# ============================================================
# 控件
# ============================================================

class FlatButton(tk.Label):
    """扁平按钮（tk.Button 在 macOS 上不显示背景色，这里统一用 Label 实现）"""

    def __init__(self, parent, text, command, bg, fg, hover_bg=None,
                 disabled_bg="#2a2a3c", disabled_fg="#5a5a70", font=None, padx=12, pady=6, width=None):
        kw = dict(text=text, bg=bg, fg=fg, font=font, padx=padx, pady=pady, cursor="hand2")
        if width:
            kw['width'] = width
        super().__init__(parent, **kw)
        self._command, self._bg, self._fg = command, bg, fg
        self._hover_bg = hover_bg or bg
        self._disabled_bg, self._disabled_fg = disabled_bg, disabled_fg
        self._enabled, self._hover = True, False
        self.bind('<Enter>', lambda e: self._set_hover(True))
        self.bind('<Leave>', lambda e: self._set_hover(False))
        self.bind('<ButtonRelease-1>', self._click)

    def _paint(self):
        if not self._enabled:
            self.configure(bg=self._disabled_bg, fg=self._disabled_fg, cursor="arrow")
        else:
            self.configure(bg=self._hover_bg if self._hover else self._bg, fg=self._fg, cursor="hand2")

    def _set_hover(self, h):
        self._hover = h
        self._paint()

    def _click(self, e):
        if self._enabled and self._command and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self._command()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self._paint()

    def set_text(self, text):
        self.configure(text=text)

    def set_colors(self, bg, hover_bg):
        self._bg, self._hover_bg = bg, hover_bg
        self._paint()


# ============================================================
# 主界面
# ============================================================

class RawPickerApp:
    BG = "#1e1e2e"
    CARD = "#26263a"
    FIELD = "#1a1a26"
    BORDER = "#363650"
    INK = "#e4e4ed"
    ASH = "#9090a8"
    MUTED = "#6a6a84"
    ACCENT = "#6c8aff"
    ACCENT_HOVER = "#8098ff"
    GREEN = "#4fbf7f"
    GREEN_HOVER = "#62d192"
    RED = "#ef5b5b"
    RED_HOVER = "#ff7272"

    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.configure(bg=self.BG)
        self._set_icon()
        self._fit_window()

        self.worker = None
        self.poll_id = None
        self._quiet = False
        self._last_result = None

        # JPG 来源：('folder', 路径) 或 ('files', [文件...])
        self.jpg_mode = 'folder'
        self.jpg_selected_files = []
        self.jpg_var = tk.StringVar()      # 每次打开都是空路径
        self.raw_var = tk.StringVar()
        self.out_var = tk.StringVar()

        # 后台计数：线程只把结果放进队列，由主线程更新界面
        self._count_queue = queue.Queue()
        self._count_gen = {'jpg': 0, 'raw': 0}

        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._drain_count_queue)

    # ---------- 窗口 ----------
    def _scale(self):
        if not _IS_WINDOWS:
            return 1.0
        try:
            return max(1.0, min(3.0, self.root.winfo_fpixels('1i') / 96.0))
        except Exception:
            return 1.0

    def _fit_window(self):
        sc = self._scale()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        reserve = 160 if _IS_MACOS else int(90 * sc)
        w = max(640, min(int(820 * sc), sw - 60))
        h = max(480, min(int(560 * sc), sh - reserve))
        x = max(0, (sw - w) // 2)
        y = max(25 if _IS_MACOS else 0, (sh - reserve - h) // 2 + (25 if _IS_MACOS else 0))
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(min(int(680 * sc), w), min(int(500 * sc), h))

    def _set_icon(self):
        if not _IS_WINDOWS:
            return
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        ico = os.path.join(base, 'app.ico')
        if os.path.exists(ico):
            try:
                self.root.iconbitmap(default=ico)
            except Exception:
                pass

    # ---------- 界面 ----------
    def _btn(self, parent, text, cmd, kind='accent', **kw):
        colors = {'accent': (self.ACCENT, self.ACCENT_HOVER),
                  'ghost': (self.BORDER, "#44445e"),
                  'green': (self.GREEN, self.GREEN_HOVER),
                  'red': (self.RED, self.RED_HOVER)}[kind]
        return FlatButton(parent, text, cmd, bg=colors[0], fg="#ffffff", hover_bg=colors[1], **kw)

    def _build_ui(self):
        bg, card = self.BG, self.CARD
        main = tk.Frame(self.root, bg=bg, padx=22, pady=18)
        main.pack(fill=tk.BOTH, expand=True)

        # 标题
        head = tk.Frame(main, bg=bg)
        head.pack(fill=tk.X)
        tk.Label(head, text="RawPicker", bg=bg, fg=self.ACCENT, font=F(18, True)).pack(side=tk.LEFT)
        tk.Label(head, text="图片筛选工具", bg=bg, fg=self.INK, font=F(13, True)).pack(side=tk.LEFT, padx=(8, 0), pady=(4, 0))
        tk.Label(head, text=f"v{APP_VERSION}", bg=bg, fg=self.MUTED, font=F(8)).pack(side=tk.LEFT, padx=(8, 0), pady=(8, 0))
        tk.Label(main, text="根据挑好的 JPG，自动找出对应的 RAW 原片并复制到输出文件夹",
                 bg=bg, fg=self.ASH, font=F(9)).pack(anchor="w", pady=(2, 12))

        # 文件夹卡片
        cardf = tk.Frame(main, bg=card, highlightbackground=self.BORDER, highlightthickness=1, padx=16, pady=12)
        cardf.pack(fill=tk.X)
        cardf.grid_columnconfigure(1, weight=1)

        self.jpg_entry, self.jpg_count = self._row(
            cardf, 0, "① JPG 来源", self.jpg_var,
            [("选择文件夹", self._select_jpg_folder, 'accent'),
             ("选择图片", self._select_jpg_files, 'ghost')],
            hint="选择文件夹：整个文件夹（含子文件夹）　选择图片：可看缩略图，单张/多张均可")
        self.raw_entry, self.raw_count = self._row(
            cardf, 2, "② RAW 文件夹", self.raw_var,
            [("选择文件夹", self._select_raw_folder, 'accent')])
        self.out_entry, _ = self._row(
            cardf, 4, "③ 输出文件夹", self.out_var,
            [("选择文件夹", self._select_out_folder, 'accent')], count=False)

        for entry, kind in ((self.jpg_entry, 'jpg'), (self.raw_entry, 'raw')):
            entry.bind('<FocusOut>', lambda e, k=kind: self._on_entry_edited(k))
            entry.bind('<Return>', lambda e, k=kind: self._on_entry_edited(k))
        self.jpg_entry.bind('<Key>', self._on_jpg_typed, add='+')

        # 底部：结果 + 进度 + 按钮（先 pack 到底部，窗口变小时不会被挤掉）
        bottom = tk.Frame(main, bg=bg)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        self.result_label = tk.Label(bottom, text="", bg=bg, fg=self.GREEN, font=F(10, True))
        self.result_label.pack(side=tk.BOTTOM, pady=(8, 0))

        btn_row = tk.Frame(bottom, bg=bg)
        btn_row.pack(side=tk.BOTTOM, pady=(10, 0))
        self.run_btn = self._btn(btn_row, "开始筛选", self._start, 'green', font=F(12, True), padx=46, pady=10)
        self.run_btn.pack()

        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('RP.Horizontal.TProgressbar', background=self.ACCENT, troughcolor=self.FIELD,
                        bordercolor=self.FIELD, lightcolor=self.ACCENT, darkcolor=self.ACCENT, thickness=8)
        self.progress = ttk.Progressbar(bottom, orient="horizontal", mode="determinate",
                                        style='RP.Horizontal.TProgressbar', maximum=100)
        self.progress.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

        self.status_label = tk.Label(bottom, text="就绪", bg=bg, fg=self.ASH, font=F(9), anchor="w", justify=tk.LEFT)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_label.bind('<Configure>', lambda e: self.status_label.configure(wraplength=max(200, e.width)))

    def _row(self, parent, r, title, var, buttons, hint=None, count=True):
        tk.Label(parent, text=title, bg=self.CARD, fg=self.INK, font=F(10, True), anchor="w").grid(
            row=r, column=0, sticky="w", padx=(0, 12), pady=(6, 0))
        entry = tk.Entry(parent, textvariable=var, font=M(9), bg=self.FIELD, fg=self.INK,
                         insertbackground=self.INK, relief="flat", highlightthickness=1,
                         highlightbackground=self.BORDER, highlightcolor=self.ACCENT,
                         readonlybackground=self.FIELD, disabledforeground=self.INK)
        entry.grid(row=r, column=1, sticky="ew", ipady=6, pady=(6, 0))
        bf = tk.Frame(parent, bg=self.CARD)
        bf.grid(row=r, column=2, sticky="e", padx=(10, 0), pady=(6, 0))
        for i, (t, cmd, kind) in enumerate(buttons):
            self._btn(bf, t, cmd, kind, font=F(9, True), padx=12, pady=5).pack(side=tk.LEFT, padx=(0 if i == 0 else 6, 0))
        count_label = None
        sub = tk.Frame(parent, bg=self.CARD)
        sub.grid(row=r + 1, column=1, columnspan=2, sticky="ew", pady=(3, 6))
        if count:
            count_label = tk.Label(sub, text="", bg=self.CARD, fg=self.ACCENT, font=F(9))
            count_label.pack(side=tk.LEFT)
        if hint:
            tk.Label(sub, text=hint, bg=self.CARD, fg=self.MUTED, font=F(8)).pack(side=tk.RIGHT)
        return entry, count_label

    # ---------- 选择 ----------
    def _ask_dir(self, title):
        try:
            d = filedialog.askdirectory(title=title, parent=self.root, mustexist=True)
        except Exception:
            d = ''
        return os.path.normpath(d) if d else ''

    def _select_jpg_folder(self):
        d = self._ask_dir("选择 JPG 文件夹")
        if d:
            self._set_jpg_folder(d)

    def _set_jpg_folder(self, d):
        self.jpg_mode = 'folder'
        self.jpg_selected_files = []
        self.jpg_entry.configure(state='normal')
        self.jpg_var.set(d)
        self._update_count('jpg')

    def _select_jpg_files(self):
        try:
            files = filedialog.askopenfilenames(
                title="选择 JPG 图片（可多选，按 Ctrl+A / ⌘A 全选）", parent=self.root,
                filetypes=[("图片", "*.jpg *.jpeg *.png *.JPG *.JPEG *.PNG"), ("所有文件", "*.*")])
        except Exception:
            files = ()
        if isinstance(files, str):           # 部分平台返回 Tcl 列表字符串
            files = self.root.tk.splitlist(files)
        files = [os.path.normpath(f) for f in files if f]
        if not files:
            return
        self.jpg_mode = 'files'
        self.jpg_selected_files = files
        folder = os.path.dirname(files[0])
        if len(files) == 1:
            shown = files[0]
        else:
            shown = f"已选 {len(files)} 张图片：{os.path.basename(files[0])} 等（{folder}）"
        self.jpg_entry.configure(state='normal')
        self.jpg_var.set(shown)
        self.jpg_entry.configure(state='readonly')
        n = sum(1 for f in files if f.lower().endswith(JPG_EXTENSIONS))
        self.jpg_count.configure(text=f"已选择 {n} 张 JPG 图片")

    def _on_jpg_typed(self, event):
        # 在“选择图片”状态下手动输入 → 切回文件夹模式
        if self.jpg_mode == 'files' and event.keysym not in ('Tab', 'Shift_L', 'Shift_R'):
            self._set_jpg_folder('')

    def _select_raw_folder(self):
        d = self._ask_dir("选择 RAW 文件夹（CR2、NEF、ARW、DNG 等）")
        if d:
            self.raw_var.set(d)
            self._update_count('raw')

    def _select_out_folder(self):
        d = self._ask_dir("选择输出文件夹")
        if d:
            self.out_var.set(d)

    def _on_entry_edited(self, kind):
        if kind == 'jpg' and self.jpg_mode == 'files':
            return
        self._update_count(kind)

    # ---------- 后台计数 ----------
    def _update_count(self, kind):
        label = self.jpg_count if kind == 'jpg' else self.raw_count
        path = (self.jpg_var if kind == 'jpg' else self.raw_var).get().strip()
        self._count_gen[kind] += 1
        gen = self._count_gen[kind]
        if not path:
            label.configure(text="")
            return
        if not os.path.isdir(path):
            label.configure(text="找不到该文件夹", fg=self.RED)
            return
        label.configure(text="正在统计...", fg=self.ASH)
        exts = JPG_EXTENSIONS if kind == 'jpg' else tuple(RAW_FOLDER_ALLOWED)

        def work():
            try:
                n = sum(1 for f in get_all_files(path) if f.lower().endswith(exts))
            except Exception:
                n = None
            self._count_queue.put((kind, gen, n))

        threading.Thread(target=work, daemon=True).start()

    def _drain_count_queue(self):
        try:
            while True:
                kind, gen, n = self._count_queue.get_nowait()
                if gen != self._count_gen[kind]:
                    continue           # 已经换了文件夹，丢弃旧结果
                label = self.jpg_count if kind == 'jpg' else self.raw_count
                if n is None:
                    label.configure(text="无法读取该文件夹", fg=self.RED)
                else:
                    what = "张 JPG 图片" if kind == 'jpg' else "张 RAW"
                    label.configure(text=f"共 {n} {what}", fg=self.ACCENT)
        except queue.Empty:
            pass
        try:
            self.root.after(150, self._drain_count_queue)
        except tk.TclError:
            pass

    # ---------- 开始 / 取消 ----------
    def _error(self, title, msg):
        if self._quiet:
            print(f"[{title}] {msg}", file=sys.stderr)
        else:
            messagebox.showerror(title, msg, parent=self.root)

    def _start(self):
        if self.worker is not None:
            self._cancel()
            return
        raw_path = self.raw_var.get().strip()
        out_path = self.out_var.get().strip()
        if self.jpg_mode == 'files':
            jpg_source = ('files', list(self.jpg_selected_files))
            jpg_ok = bool(self.jpg_selected_files)
        else:
            jpg_dir = self.jpg_var.get().strip()
            jpg_source = ('folder', jpg_dir)
            jpg_ok = bool(jpg_dir)
        if not jpg_ok or not raw_path or not out_path:
            self._error("提示", "请先选择 JPG 来源、RAW 文件夹和输出文件夹！")
            return
        if jpg_source[0] == 'folder' and not os.path.isdir(jpg_source[1]):
            self._error("文件夹不可用", f"找不到 JPG 文件夹：\n{jpg_source[1]}\n\n外接硬盘是否已连接？")
            return
        if not os.path.isdir(raw_path):
            self._error("文件夹不可用", f"找不到 RAW 文件夹：\n{raw_path}\n\n外接硬盘是否已连接？")
            return
        if _same_file(raw_path, out_path) if os.path.exists(out_path) else False:
            self._error("输出文件夹不能和 RAW 文件夹相同", "请另外选择一个输出文件夹。")
            return
        try:
            os.makedirs(out_path, exist_ok=True)
            if not os.access(out_path, os.W_OK):
                raise PermissionError("文件夹为只读")
        except Exception as e:
            self._error("无法写入输出文件夹",
                        f"{out_path}\n\n{e}\n\n提示：NTFS 格式的硬盘在 Mac 上是只读的。")
            return

        self.run_btn.set_text("取消")
        self.run_btn.set_colors(self.RED, self.RED_HOVER)
        self.run_btn.set_enabled(True)
        self.progress['value'] = 0
        self.result_label.configure(text="")
        self.status_label.configure(text="准备中...")

        self.worker = FilterWorker(jpg_source, raw_path, out_path)
        self.worker.start()
        self._poll()

    def _cancel(self):
        if self.worker:
            self.worker.cancel()
        self.run_btn.set_text("正在取消...")
        self.run_btn.set_enabled(False)
        self.status_label.configure(text="正在取消，等待正在复制的文件完成...")

    def _reset_button(self):
        self.run_btn.set_text("开始筛选")
        self.run_btn.set_colors(self.GREEN, self.GREEN_HOVER)
        self.run_btn.set_enabled(True)

    def _poll(self):
        w = self.worker
        if w is None:
            return
        try:
            while True:
                msg = w.progress_queue.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self.status_label.configure(text=msg[1])
                elif kind == "progress":
                    _, cur, total, name, elapsed, remaining = msg
                    pct = cur / total * 100 if total else 0
                    self.progress['value'] = pct
                    self.status_label.configure(
                        text=f"[{cur}/{total}] {pct:.1f}%　正在复制：{name}　"
                             f"已用 {format_seconds(elapsed)}　剩余 {format_seconds(remaining)}")
                elif kind == "done":
                    self._finish(msg[1])
                    return
                elif kind == "cancelled":
                    self.progress['value'] = 0
                    self.result_label.configure(text=f"已取消（已复制 {msg[1]} 张）", fg=self.ASH)
                    self.status_label.configure(text="就绪")
                    self._end_worker()
                    return
                elif kind == "error":
                    self.progress['value'] = 0
                    self.status_label.configure(text="就绪")
                    self._end_worker()
                    self._last_result = {'error': msg[1]}
                    self._error("错误", msg[1])
                    return
        except queue.Empty:
            pass
        if w.thread and w.thread.is_alive():
            self.poll_id = self.root.after(100, self._poll)
        else:
            self.status_label.configure(text="就绪")
            self._end_worker()

    def _end_worker(self):
        self.worker = None
        self.poll_id = None
        self._reset_button()

    def _finish(self, s):
        self._end_worker()
        self._last_result = s
        self.progress['value'] = 100
        copied, failed, unmatched = s['copied'], s['failed'], s['unmatched']
        matched_jpg = s['total_jpg'] - len(unmatched)
        self.result_label.configure(
            text=f"完成：复制了 {copied} 张 RAW" + (f"，{len(failed)} 张失败" if failed else ""),
            fg=self.RED if failed else self.GREEN)
        self.status_label.configure(text="就绪")
        if self._quiet:
            return
        lines = [f"JPG 共 {s['total_jpg']} 张，匹配到 {matched_jpg} 张",
                 f"成功复制 RAW：{copied} 张"]
        if failed:
            lines.append(f"\n复制失败 {len(failed)} 张：")
            lines += failed[:8]
            if len(failed) > 8:
                lines.append(f"... 还有 {len(failed) - 8} 张")
        if unmatched:
            lines.append(f"\n未匹配的 JPG：{len(unmatched)} 张")
            lines.append(f"详细列表已保存到：\n{UNMATCHED_FILES}")
        if unmatched and os.path.exists(UNMATCHED_FILES):
            if messagebox.askyesno("匹配结果", "\n".join(lines) + "\n\n是否打开未匹配列表？", parent=self.root):
                open_with_system(UNMATCHED_FILES)
        else:
            messagebox.showinfo("匹配结果", "\n".join(lines), parent=self.root)

    def _on_close(self):
        if self.worker is not None:
            self.worker.cancel()
            if self.worker.thread and self.worker.thread.is_alive():
                self.worker.thread.join(timeout=3)
        self.root.destroy()          # 不保存任何路径

    # ---------- 自检（打包后 CI 用）----------
    def run_selftest(self, base, result_file):
        self._quiet = True
        mode = os.environ.get('RP_SELFTEST_MODE', 'folder')
        if mode == 'files':
            jdir = os.path.join(base, 'jpg')
            files = sorted(os.path.join(jdir, f) for f in os.listdir(jdir) if f.lower().endswith('.jpg'))
            self.jpg_mode, self.jpg_selected_files = 'files', files[:2]
        else:
            self._set_jpg_folder(os.path.join(base, 'jpg'))
        self.raw_var.set(os.path.join(base, 'raw'))
        self.out_var.set(os.path.join(base, 'out'))
        t0 = time.time()

        def tick():
            if self._last_result is not None or time.time() - t0 > 60:
                r = self._last_result or {'error': 'timeout'}
                out = os.path.join(base, 'out')
                info = {'ok': 'error' not in r,
                        'tk_console': bool(self.root.tk.call('info', 'commands', 'console')),
                        'copied': r.get('copied'), 'failed': r.get('failed'),
                        'unmatched': sorted(os.path.basename(u) for u in r.get('unmatched', [])),
                        'out_files': sorted(os.listdir(out)) if os.path.isdir(out) else [],
                        'error': r.get('error')}
                with open(result_file, 'w', encoding='utf-8') as f:
                    json.dump(info, f, ensure_ascii=False)
                self.root.destroy()
                return
            self.root.after(200, tick)

        self._start()
        self.root.after(200, tick)


# ============================================================
# 入口
# ============================================================

def _disable_tk_console_on_macos():
    """Finder 启动时 stdin 是 /dev/null，Tk 会建隐藏控制台（macOS 11 上会导致崩溃）"""
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
            globals()['_STDIN_PIPE_W'] = w
        except OSError:
            pass


def main():
    _disable_tk_console_on_macos()
    if _IS_WINDOWS:
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    root = tk.Tk()
    app = RawPickerApp(root)
    demo = os.environ.get('RP_PREFILL_DIR')      # 仅用于 CI 截图：预填路径
    if demo:
        app._set_jpg_folder(os.path.join(demo, 'jpg'))
        app.raw_var.set(os.path.join(demo, 'raw'))
        app._update_count('raw')
        app.out_var.set(os.path.join(demo, 'out'))
    st = os.environ.get('RP_SELFTEST_DIR')
    if st:
        app.run_selftest(st, os.environ.get('RP_SELFTEST_RESULT', os.path.join(st, 'result.json')))
    root.mainloop()


if __name__ == "__main__":
    main()
