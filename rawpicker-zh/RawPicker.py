#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RawPicker 图片筛选工具（中文版）v3.1
====================================
根据挑选好的 JPG，从 RAW 文件夹中找出对应的 RAW 文件，复制到输出文件夹。

v3.1：
  - 分两步：先「开始匹配」列出 JPG ↔ RAW 对照表（可勾选），再「导出勾选的RAW」
  - 输出文件夹不填：默认导出到 JPG 目录下的 raw 文件夹（没有就新建，有就直接放进去）
  - 输出文件夹里已有同名文件：弹窗选择 覆盖 / 跳过 / 取消
  - 中文过滤修复：N-D810 (4)副本.jpg 现在能匹配 N-D810 (4).NEF

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
APP_VERSION = "3.1.0"

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
# 文件名匹配
# ============================================================

def is_hidden_file(filepath):
    filename = os.path.basename(filepath)
    return filename.startswith('.') or filename.startswith('~')


_TOKEN_RE = re.compile(r'[A-Za-z0-9_]+')
# 中文汉字 + 中文标点 / 全角字符
_CJK_RE = re.compile('[　-〿㐀-䶿一-鿿豈-﫿＀-￯]')
_TRIM_CHARS = ' \t-_.~'


def jpg_match_keys(jpg_filename):
    """JPG 可用来匹配 RAW 的名字（全部转小写比较）

    中文过滤：
      1) 去掉中文后剩下的完整文件名：N-D810 (4)副本 → N-D810 (4)　DSC0001 - 副本 → DSC0001
      2) 每一段“英文+数字”：婚礼DSC0002_修图 → DSC0002　IMG_1234-edit → IMG_1234
    RAW 文件名（不含扩展名）必须和其中某一个完全一样才算匹配
    （A7S01234修图 → A7S01234，不会匹配 DSC01234）。
    """
    stem = os.path.splitext(jpg_filename)[0]
    keys = {stem.lower()}                       # 完整文件名
    no_cjk = _CJK_RE.sub('', stem)
    if no_cjk != stem:
        for c in (no_cjk.strip(_TRIM_CHARS), re.sub(r'\s+', ' ', no_cjk).strip(_TRIM_CHARS)):
            if c:
                keys.add(c.lower())
    for t in _TOKEN_RE.findall(stem):
        if not re.search(r'\d', t):            # 只看带编号的片段（edit、HDR 之类不算）
            continue
        keys.add(t.lower())
        t2 = t.strip('_')                       # “婚礼_DSC0002_修图” 两边的下划线是分隔符
        if t2:
            keys.add(t2.lower())
    return keys


def is_filename_match(raw_filename, jpg_filename):
    """RAW 是否对应这张 JPG"""
    return os.path.splitext(raw_filename)[0].lower() in jpg_match_keys(jpg_filename)


def match_rows(jpg_image_files, raw_files, is_cancelled=lambda: False):
    """逐张 JPG 找对应的 RAW，返回 [{'jpg': 路径, 'raws': [RAW 路径, ...]}, ...]（顺序同 JPG）"""
    raw_name_to_paths = {}
    for raw_file in raw_files:
        raw_name = os.path.splitext(os.path.basename(raw_file))[0]
        raw_name_to_paths.setdefault(raw_name.lower(), []).append(raw_file)
    rows = []
    for f in jpg_image_files:
        if is_cancelled():
            break
        found = []
        for k in jpg_match_keys(os.path.basename(f)):
            found.extend(raw_name_to_paths.get(k, ()))
        seen, raws = set(), []
        for p in found:
            if p not in seen:
                seen.add(p)
                raws.append(p)
        raws.sort(key=lambda p: (os.path.basename(p).lower(), p))
        rows.append({'jpg': f, 'raws': raws})
    return rows


def build_copy_plan(raw_paths, output_path):
    """RAW 路径列表 → {源: 目标}；不同子文件夹里的同名 RAW 自动改名 _1、_2，避免互相覆盖"""
    plan, used = {}, set()
    for p in sorted(dict.fromkeys(raw_paths), key=lambda p: (os.path.basename(p).lower(), p)):
        base = os.path.basename(p)
        stem, ext = os.path.splitext(base)
        name, n = base, 1
        while name.lower() in used:
            name = f"{stem}_{n}{ext}"
            n += 1
        used.add(name.lower())
        plan[p] = os.path.join(output_path, name)
    return plan


def match_files(jpg_image_files, raw_files, output_path, is_cancelled=lambda: False):
    """（兼容旧接口）返回 (matched: RAW → 目标路径, 匹配到的 JPG 集合)"""
    rows = match_rows(jpg_image_files, raw_files, is_cancelled)
    raws = [p for r in rows for p in r['raws']]
    return build_copy_plan(raws, output_path), {r['jpg'] for r in rows if r['raws']}


# ============================================================
# 文件工具
# ============================================================

def get_all_files(folder, exclude_dirs=()):
    """递归获取文件夹中所有文件（忽略隐藏文件，读不了的子文件夹跳过）"""
    file_list = []
    if not folder or not os.path.isdir(folder):
        return file_list
    excl = {os.path.normcase(os.path.abspath(d)) for d in exclude_dirs if d}
    for root, dirs, files in os.walk(folder, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if not is_hidden_file(d) and not d.startswith('$')
                   and d != 'System Volume Information'
                   and os.path.normcase(os.path.abspath(os.path.join(root, d))) not in excl]
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
    """复制文件（目标已存在则覆盖）；返回 (成功?, 错误信息)"""
    if os.path.exists(dst) and _same_file(src, dst):
        return True, None          # 源和目标是同一个文件：什么都不做
    last_err = None
    for _ in range(3):
        tmp = dst + ".rptmp"       # 先写临时文件再替换：中途失败不会毁掉已有的同名文件
        try:
            with open(src, 'rb') as fsrc, open(tmp, 'wb', buffering=COPY_BUFFER_SIZE) as fdst:
                shutil.copyfileobj(fsrc, fdst, COPY_BUFFER_SIZE)
            if os.path.getsize(tmp) == os.path.getsize(src):
                _copystat_quiet(src, tmp)
                os.replace(tmp, dst)
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


def write_unmatched_list(unmatched):
    try:
        with open(UNMATCHED_FILES, "w", encoding="utf-8") as f:
            f.write(f"以下 {len(unmatched)} 张 JPG 没有找到对应的 RAW 文件：\n")
            f.write("=" * 50 + "\n")
            for jpg_file in unmatched:
                f.write(jpg_file + "\n")
        return True
    except Exception:
        return False


# ============================================================
# 后台工作线程（只通过 queue 与界面通信，不直接操作界面）
# ============================================================

class _Worker:
    def __init__(self):
        self.cancel_event = threading.Event()
        self.progress_queue = queue.Queue()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._safe_run, daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancel_event.set()

    def _send(self, *msg):
        self.progress_queue.put(msg)

    def _cancelled(self):
        return self.cancel_event.is_set()

    def _safe_run(self):
        try:
            self._run()
        except Exception as e:
            traceback.print_exc()
            self._send("error", f"发生错误：\n{e}")


class MatchWorker(_Worker):
    """第一步：扫描 + 文件名匹配"""

    def __init__(self, jpg_source, raw_path, exclude_dir=None):
        super().__init__()
        self.jpg_source = jpg_source          # ('folder', 路径) 或 ('files', [路径...])
        self.raw_path = raw_path
        self.exclude_dir = exclude_dir        # 默认输出文件夹（JPG目录\raw），扫描 JPG 时跳过

    def _run(self):
        mode, src = self.jpg_source
        if mode == 'files':
            self._send("status", "正在读取选中的图片...")
            jpg_files = [f for f in src if os.path.isfile(f) and f.lower().endswith(JPG_EXTENSIONS)]
        else:
            self._send("status", "正在扫描 JPG 文件夹...")
            jpg_files = [f for f in get_all_files(src, (self.exclude_dir,))
                         if f.lower().endswith(JPG_EXTENSIONS)]
        if not jpg_files:
            self._send("error", "没有找到 JPG 图片。")
            return
        if self._cancelled():
            self._send("cancelled")
            return
        self._send("status", "正在扫描 RAW 文件夹...")
        raw_files = [f for f in get_all_files(self.raw_path)
                     if os.path.splitext(f)[1].lower() in RAW_FOLDER_ALLOWED]
        if not raw_files:
            self._send("error", "RAW 文件夹中没有可用文件！\n"
                                "支持格式：CR2、CR3、NEF、ARW、ORF、RAF、RW2、DNG、JPG 等")
            return
        if self._cancelled():
            self._send("cancelled")
            return
        self._send("status", f"正在匹配：{len(jpg_files)} 张 JPG，{len(raw_files)} 张 RAW...")
        jpg_files.sort(key=lambda p: (os.path.dirname(p).lower(), _natural_key(os.path.basename(p))))
        rows = match_rows(jpg_files, raw_files, self._cancelled)
        if self._cancelled():
            self._send("cancelled")
            return
        unmatched = [r['jpg'] for r in rows if not r['raws']]
        if unmatched:
            write_unmatched_list(unmatched)
        self._send("matched", rows, len(raw_files))


class ExportWorker(_Worker):
    """第二步：并行复制勾选的 RAW"""

    def __init__(self, plan, raw_path, output_path, skipped):
        super().__init__()
        self.plan = plan                      # {源: 目标}
        self.raw_path = raw_path
        self.output_path = output_path
        self.skipped = skipped                # 因“跳过”而不复制的数量

    def _run(self):
        total = len(self.plan)
        summary = {'copied': 0, 'failed': [], 'planned': total, 'skipped': self.skipped,
                   'output': self.output_path}
        if total == 0:
            self._send("exported", summary)
            return
        workers = _detect_copy_workers(self.raw_path, self.output_path)
        start = time.time()
        done_count, last_sent = 0, 0.0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(self._copy_one, s, d): s for s, d in self.plan.items()}
            for fut in as_completed(futures):
                src = futures[fut]
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
            self._send("exported", summary)

    def _copy_one(self, src, dst):
        if self._cancelled():
            return False, "cancelled"
        return safe_copy(src, dst)


def _natural_key(s):
    """N-D810 (2) 排在 N-D810 (10) 前面"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


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

CHK_ON, CHK_OFF = '☑', '☐'


class RawPickerApp:
    BG = "#1e1e2e"
    CARD = "#26263a"
    FIELD = "#1a1a26"
    BORDER = "#363650"
    INK = "#e4e4ed"
    ROW_INK = "#c0c0d0"
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

        self.worker = None            # MatchWorker / ExportWorker
        self.poll_id = None
        self._quiet = False           # 自检模式：不弹窗
        self._last_result = None
        self._selftest_auto_export = False

        # JPG 来源：('folder', 路径) 或 ('files', [文件...])
        self.jpg_mode = 'folder'
        self.jpg_selected_files = []
        self.jpg_var = tk.StringVar()      # 每次打开都是空路径
        self.raw_var = tk.StringVar()
        self.out_var = tk.StringVar()

        self.rows = []                     # 匹配结果
        self.item_row = {}                 # Treeview item → row
        self.checked = set()               # 勾选的 item
        self._match_ctx = None             # 匹配时的 (jpg_source, raw_path)

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
        w = max(640, min(int(880 * sc), sw - 60))
        h = max(480, min(int(760 * sc), sh - reserve))
        x = max(0, (sw - w) // 2)
        y = max(25 if _IS_MACOS else 0, (sh - reserve - h) // 2 + (25 if _IS_MACOS else 0))
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(min(int(700 * sc), w), min(int(560 * sc), h))

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
        main = tk.Frame(self.root, bg=bg, padx=22, pady=14)
        main.pack(fill=tk.BOTH, expand=True)

        # 标题
        head = tk.Frame(main, bg=bg)
        head.pack(fill=tk.X)
        tk.Label(head, text="RawPicker", bg=bg, fg=self.ACCENT, font=F(18, True)).pack(side=tk.LEFT)
        tk.Label(head, text="图片筛选工具", bg=bg, fg=self.INK, font=F(13, True)).pack(side=tk.LEFT, padx=(8, 0), pady=(4, 0))
        tk.Label(head, text=f"v{APP_VERSION}", bg=bg, fg=self.MUTED, font=F(8)).pack(side=tk.LEFT, padx=(8, 0), pady=(8, 0))
        tk.Label(main, text="根据挑好的 JPG，按文件名找出对应的 RAW 原片，确认后导出到输出文件夹",
                 bg=bg, fg=self.ASH, font=F(9)).pack(anchor="w", pady=(2, 10))

        # 文件夹卡片
        cardf = tk.Frame(main, bg=card, highlightbackground=self.BORDER, highlightthickness=1, padx=16, pady=8)
        cardf.pack(fill=tk.X)
        cardf.grid_columnconfigure(1, weight=1)

        self.jpg_entry, self.jpg_count, _ = self._row(
            cardf, 0, "① JPG 来源", self.jpg_var,
            [("选择文件夹", self._select_jpg_folder, 'accent'),
             ("选择图片", self._select_jpg_files, 'ghost')],
            hint="选择文件夹：整个文件夹（含子文件夹）　选择图片：可看缩略图，单张/多张均可")
        self.raw_entry, self.raw_count, _ = self._row(
            cardf, 2, "② RAW 文件夹", self.raw_var,
            [("选择文件夹", self._select_raw_folder, 'accent')])
        self.out_entry, _, self.out_hint = self._row(
            cardf, 4, "③ 输出文件夹", self.out_var,
            [("选择文件夹", self._select_out_folder, 'accent')], count=False, hint=" ")
        self._update_out_hint()

        for entry, kind in ((self.jpg_entry, 'jpg'), (self.raw_entry, 'raw')):
            entry.bind('<FocusOut>', lambda e, k=kind: self._on_entry_edited(k))
            entry.bind('<Return>', lambda e, k=kind: self._on_entry_edited(k))
        self.jpg_entry.bind('<Key>', self._on_jpg_typed, add='+')
        self.out_var.trace_add('write', lambda *a: self._update_out_hint())

        # 开始匹配
        mrow = tk.Frame(main, bg=bg)
        mrow.pack(fill=tk.X, pady=(10, 4))
        self.match_btn = self._btn(mrow, "开始匹配", self._start_match, 'green', font=F(11, True), padx=56, pady=8)
        self.match_btn.pack()

        # 底部（先 pack 到底部，窗口变小时不会被挤掉）
        bottom = tk.Frame(main, bg=bg)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('RP.Horizontal.TProgressbar', background=self.GREEN, troughcolor=self.FIELD,
                        bordercolor=self.FIELD, lightcolor=self.GREEN, darkcolor=self.GREEN, thickness=6)
        self.progress = ttk.Progressbar(bottom, orient="horizontal", mode="determinate",
                                        style='RP.Horizontal.TProgressbar', maximum=100)
        self.progress.pack(fill=tk.X, pady=(8, 0))
        self.progress_text = tk.Label(bottom, text="", bg=bg, fg=self.ASH, font=F(8), anchor="w")
        self.progress_text.pack(fill=tk.X)

        brow = tk.Frame(bottom, bg=bg)
        brow.pack(fill=tk.X, pady=(4, 0))
        brow.grid_columnconfigure(0, weight=1, uniform='side')
        brow.grid_columnconfigure(2, weight=1, uniform='side')
        cnt = tk.Frame(brow, bg=bg)
        cnt.grid(row=0, column=0, sticky="w")
        self.matched_label = tk.Label(cnt, text="0 已匹配", bg=bg, fg=self.GREEN, font=F(10, True))
        self.matched_label.pack(side=tk.LEFT)
        self.unmatched_label = tk.Label(cnt, text="0 未匹配", bg=bg, fg=self.RED, font=F(10, True))
        self.unmatched_label.pack(side=tk.LEFT, padx=(14, 0))
        self.unmatched_label.bind('<Button-1>', lambda e: self._open_unmatched())
        self.export_btn = self._btn(brow, "导出勾选的RAW", self._start_export, 'accent',
                                    font=F(11, True), padx=28, pady=8)
        self.export_btn.grid(row=0, column=1)
        self.export_btn.set_enabled(False)
        self.selected_label = tk.Label(brow, text="", bg=bg, fg=self.ASH, font=F(9))
        self.selected_label.grid(row=0, column=2, sticky="e")

        # 结果表
        rhead = tk.Frame(main, bg=bg)
        rhead.pack(fill=tk.X, pady=(6, 4))
        self.status_label = tk.Label(rhead, text="选好 JPG 和 RAW 后，点「开始匹配」", bg=bg, fg=self.ASH,
                                     font=F(9), anchor="w")
        self.status_label.pack(side=tk.LEFT)
        self.stats_label = tk.Label(rhead, text="", bg=bg, fg=self.ACCENT, font=F(9, True))
        self.stats_label.pack(side=tk.RIGHT)

        tree_frame = tk.Frame(main, bg=self.FIELD)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        sc = self._scale()
        style.configure("RP.Treeview", background=self.FIELD, foreground=self.ROW_INK,
                        fieldbackground=self.FIELD, bordercolor=self.FIELD, lightcolor=self.FIELD,
                        darkcolor=self.FIELD, borderwidth=0, font=F(9),
                        rowheight=int((22 + _FONT_DELTA * 2) * sc))
        style.layout('RP.Treeview', [('RP.Treeview.treearea', {'sticky': 'nswe'})])
        style.configure("RP.Treeview.Heading", background=self.ACCENT, foreground="#ffffff",
                        bordercolor=self.FIELD, lightcolor=self.ACCENT, darkcolor=self.ACCENT,
                        relief='flat', font=F(9, True), padding=(6, 4))
        style.map("RP.Treeview.Heading", background=[('active', self.ACCENT_HOVER), ('!active', self.ACCENT)],
                  foreground=[('active', '#ffffff'), ('!active', '#ffffff')])
        style.map("RP.Treeview", background=[('selected', '#2a2a4a')], foreground=[('selected', '#e0e0f0')])
        style.configure("RP.Vertical.TScrollbar", background='#252535', troughcolor=self.FIELD,
                        borderwidth=0, arrowcolor=self.ACCENT, gripcount=0)
        style.map("RP.Vertical.TScrollbar", background=[('active', '#3a3a4e'), ('!active', '#252535')])

        self.tree = ttk.Treeview(tree_frame, columns=('chk', 'jpg', 'raw'), show='headings',
                                 selectmode='extended', style="RP.Treeview", height=6)
        self.tree.heading('chk', text='勾选', command=self._toggle_all)
        self.tree.heading('jpg', text='JPG 文件')
        self.tree.heading('raw', text='RAW 文件')
        self.tree.column('chk', width=int(60 * sc), anchor='center', stretch=False)
        self.tree.column('jpg', width=int(320 * sc), minwidth=120)
        self.tree.column('raw', width=int(360 * sc), minwidth=120, stretch=True)
        self.tree.tag_configure('miss', foreground='#ff6b6b')
        self.tree.tag_configure('hit', foreground=self.ROW_INK)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview,
                            style="RP.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind('<ButtonRelease-1>', self._on_tree_click)
        self.tree.bind('<Double-1>', self._on_tree_double_click)
        self.tree.bind('<space>', self._on_space)
        if _IS_MACOS:
            self.tree.bind('<MouseWheel>', lambda e: (self.tree.yview_scroll(-e.delta, 'units'), 'break')[1])
        else:
            self.tree.bind('<MouseWheel>', lambda e: (self.tree.yview_scroll(
                int(-1 * (e.delta / 120)) or (-1 if e.delta > 0 else 1), 'units'), 'break')[1])
            self.tree.bind('<Button-4>', lambda e: self.tree.yview_scroll(-1, 'units'))
            self.tree.bind('<Button-5>', lambda e: self.tree.yview_scroll(1, 'units'))

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
        count_label = hint_label = None
        sub = tk.Frame(parent, bg=self.CARD)
        sub.grid(row=r + 1, column=1, columnspan=2, sticky="ew", pady=(3, 4))
        if count:
            count_label = tk.Label(sub, text="", bg=self.CARD, fg=self.ACCENT, font=F(9))
            count_label.pack(side=tk.LEFT)
        if hint:
            hint_label = tk.Label(sub, text=hint, bg=self.CARD, fg=self.MUTED, font=F(8), anchor="w")
            hint_label.pack(side=tk.LEFT if not count else tk.RIGHT, fill=tk.X if not count else None, expand=not count)
        return entry, count_label, hint_label

    # ---------- 输出文件夹 ----------
    def _jpg_base_dir(self):
        """JPG 所在目录：文件夹模式 = 该文件夹；选择图片模式 = 第一张图片所在文件夹"""
        if self.jpg_mode == 'files':
            return os.path.dirname(self.jpg_selected_files[0]) if self.jpg_selected_files else ''
        return self.jpg_var.get().strip()

    def _default_out_dir(self):
        base = self._jpg_base_dir()
        return os.path.join(base, 'raw') if base else ''

    def _resolve_out_dir(self):
        o = self.out_var.get().strip()
        return os.path.normpath(o) if o else self._default_out_dir()

    def _update_out_hint(self):
        if not getattr(self, 'out_hint', None):
            return
        if self.out_var.get().strip():
            self.out_hint.configure(text="RAW 将直接导出到上面这个文件夹", fg=self.MUTED)
        else:
            d = self._default_out_dir()
            if d:
                exists = os.path.isdir(d)
                self.out_hint.configure(
                    text=f"不填写：导出到 {d}" + ("（已存在，直接放进去）" if exists else "（会自动新建）"),
                    fg=self.ACCENT)
            else:
                self.out_hint.configure(text="不填写：默认在 JPG 目录下新建 raw 文件夹，导出到里面",
                                        fg=self.MUTED)

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
        self._inputs_changed()

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
        self._set_jpg_files(files)

    def _set_jpg_files(self, files):
        self.jpg_mode = 'files'
        self.jpg_selected_files = files
        folder = os.path.dirname(files[0])
        shown = files[0] if len(files) == 1 else \
            f"已选 {len(files)} 张图片：{os.path.basename(files[0])} 等（{folder}）"
        self.jpg_entry.configure(state='normal')
        self.jpg_var.set(shown)
        self.jpg_entry.configure(state='readonly')
        n = sum(1 for f in files if f.lower().endswith(JPG_EXTENSIONS))
        self.jpg_count.configure(text=f"已选择 {n} 张 JPG 图片", fg=self.ACCENT)
        self._inputs_changed()

    def _on_jpg_typed(self, event):
        # 在“选择图片”状态下手动输入 → 切回文件夹模式
        if self.jpg_mode == 'files' and event.keysym not in ('Tab', 'Shift_L', 'Shift_R'):
            self._set_jpg_folder('')

    def _select_raw_folder(self):
        d = self._ask_dir("选择 RAW 文件夹（CR2、NEF、ARW、DNG 等）")
        if d:
            self.raw_var.set(d)
            self._update_count('raw')
            self._inputs_changed()

    def _select_out_folder(self):
        d = self._ask_dir("选择输出文件夹")
        if d:
            self.out_var.set(d)

    def _on_entry_edited(self, kind):
        if kind == 'jpg' and self.jpg_mode == 'files':
            return
        self._update_count(kind)
        self._inputs_changed()

    def _inputs_changed(self):
        """JPG / RAW 改了 → 之前的匹配结果作废"""
        self._update_out_hint()
        if self.rows and self._current_ctx() != self._match_ctx and self.worker is None:
            self._clear_results("文件夹已更改，请重新点「开始匹配」")

    def _current_ctx(self):
        src = ('files', tuple(self.jpg_selected_files)) if self.jpg_mode == 'files' \
            else ('folder', self.jpg_var.get().strip())
        return (src, self.raw_var.get().strip())

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
        excl = (os.path.join(path, 'raw'),) if kind == 'jpg' and not self.out_var.get().strip() else ()

        def work():
            try:
                n = sum(1 for f in get_all_files(path, excl) if f.lower().endswith(exts))
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

    # ---------- 提示 ----------
    def _error(self, title, msg):
        if self._quiet:
            print(f"[{title}] {msg}", file=sys.stderr)
            self._last_result = {'error': msg}
        else:
            messagebox.showerror(title, msg, parent=self.root)

    def _open_unmatched(self):
        if any(not r['raws'] for r in self.rows) and os.path.exists(UNMATCHED_FILES):
            open_with_system(UNMATCHED_FILES)

    # ---------- 第一步：匹配 ----------
    def _start_match(self):
        if self.worker is not None:
            if isinstance(self.worker, MatchWorker):
                self.worker.cancel()
            return
        raw_path = self.raw_var.get().strip()
        if self.jpg_mode == 'files':
            jpg_source = ('files', list(self.jpg_selected_files))
            jpg_ok = bool(self.jpg_selected_files)
        else:
            jpg_dir = self.jpg_var.get().strip()
            jpg_source = ('folder', jpg_dir)
            jpg_ok = bool(jpg_dir)
        if not jpg_ok or not raw_path:
            self._error("提示", "请先选择 JPG 来源和 RAW 文件夹！")
            return
        if jpg_source[0] == 'folder' and not os.path.isdir(jpg_source[1]):
            self._error("文件夹不可用", f"找不到 JPG 文件夹：\n{jpg_source[1]}\n\n外接硬盘是否已连接？")
            return
        if not os.path.isdir(raw_path):
            self._error("文件夹不可用", f"找不到 RAW 文件夹：\n{raw_path}\n\n外接硬盘是否已连接？")
            return

        self._clear_results("正在匹配...")
        self._match_ctx = self._current_ctx()
        self.match_btn.set_text("取消匹配")
        self.match_btn.set_colors(self.RED, self.RED_HOVER)
        self.export_btn.set_enabled(False)
        self.progress.configure(mode='indeterminate')
        self.progress.start(12)
        excl = self._default_out_dir() if not self.out_var.get().strip() else None
        self.worker = MatchWorker(jpg_source, raw_path, excl)
        self.worker.start()
        self._poll()

    def _clear_results(self, status=""):
        self.rows = []
        self.item_row = {}
        self.checked = set()
        self.tree.delete(*self.tree.get_children())
        self.stats_label.configure(text="")
        self.matched_label.configure(text="0 已匹配")
        self.unmatched_label.configure(text="0 未匹配", cursor="")
        self.selected_label.configure(text="")
        self.status_label.configure(text=status)
        self.progress_text.configure(text="")
        self.progress['value'] = 0
        self.export_btn.set_enabled(False)

    def _show_rows(self, rows):
        self.rows = rows
        self.item_row, self.checked = {}, set()
        for r in rows:
            name = os.path.basename(r['jpg'])
            if r['raws']:
                raw_txt = '，'.join(os.path.basename(p) for p in r['raws'])
                item = self.tree.insert('', tk.END, values=(CHK_ON, name, raw_txt), tags=('hit',))
                self.checked.add(item)
            else:
                item = self.tree.insert('', tk.END, values=(CHK_OFF, name, '- 未找到 -'), tags=('miss',))
            self.item_row[item] = r
        matched = sum(1 for r in rows if r['raws'])
        unmatched = len(rows) - matched
        self.matched_label.configure(text=f"{matched} 已匹配")
        self.unmatched_label.configure(text=f"{unmatched} 未匹配",
                                       cursor="hand2" if unmatched else "")
        self.stats_label.configure(text=f"{len(rows)} JPG  |  {matched} 匹配  |  {unmatched} 未匹配")
        self.status_label.configure(
            text="文件名匹配完成" + ("　（点“未匹配”可打开未匹配列表）" if unmatched else ""))
        self._update_selected()

    def _update_selected(self):
        raws = self._checked_raws()
        self.selected_label.configure(text=f"已勾选 {len(raws)} 张 RAW" if self.rows else "")
        self.export_btn.set_enabled(bool(raws) and self.worker is None)

    def _checked_raws(self):
        out = []
        for item in self.tree.get_children():
            if item in self.checked:
                out.extend(self.item_row[item]['raws'])
        return list(dict.fromkeys(out))

    # ---------- 勾选 ----------
    def _toggle(self, item):
        r = self.item_row.get(item)
        if not r or not r['raws'] or self.worker is not None:
            return
        if item in self.checked:
            self.checked.discard(item)
            self.tree.set(item, 'chk', CHK_OFF)
        else:
            self.checked.add(item)
            self.tree.set(item, 'chk', CHK_ON)

    def _on_tree_click(self, event):
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != '#1':
            return
        item = self.tree.identify_row(event.y)
        if item:
            sel = self.tree.selection()
            targets = sel if item in sel and len(sel) > 1 else (item,)
            want_on = item not in self.checked
            for it in targets:
                if (it in self.checked) != want_on:
                    self._toggle(it)
            self._update_selected()

    def _on_space(self, event):
        sel = self.tree.selection()
        if sel:
            want_on = sel[0] not in self.checked
            for it in sel:
                if (it in self.checked) != want_on:
                    self._toggle(it)
            self._update_selected()
        return 'break'

    def _toggle_all(self):
        if not self.rows or self.worker is not None:
            return
        hits = [it for it, r in self.item_row.items() if r['raws']]
        want_on = len(self.checked) < len(hits)
        for it in hits:
            if (it in self.checked) != want_on:
                self._toggle(it)
        self._update_selected()

    def _on_tree_double_click(self, event):
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        col = self.tree.identify_column(event.x)
        r = self.item_row.get(self.tree.identify_row(event.y))
        if not r:
            return
        if col == '#2':
            open_with_system(r['jpg'])
        elif col == '#3' and r['raws']:
            open_with_system(r['raws'][0])

    # ---------- 第二步：导出 ----------
    def _ask_conflict(self, conflicts, out_dir):
        """输出文件夹里已有同名文件 → 'overwrite' / 'skip' / None(取消)"""
        if self._quiet:
            return 'overwrite'
        dlg = tk.Toplevel(self.root)
        dlg.title("文件已存在")
        dlg.configure(bg=self.BG)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        result = {'v': None}
        names = [os.path.basename(d) for d in conflicts[:6]]
        more = f"\n…… 等共 {len(conflicts)} 个" if len(conflicts) > 6 else ""
        tk.Label(dlg, text=f"输出文件夹里已经有 {len(conflicts)} 个同名文件：", bg=self.BG, fg=self.INK,
                 font=F(10, True), anchor="w", justify=tk.LEFT).pack(fill=tk.X, padx=22, pady=(18, 4))
        tk.Label(dlg, text=out_dir, bg=self.BG, fg=self.MUTED, font=F(8), anchor="w",
                 wraplength=440, justify=tk.LEFT).pack(fill=tk.X, padx=22)
        tk.Label(dlg, text="\n".join(names) + more, bg=self.FIELD, fg=self.ROW_INK, font=M(9),
                 anchor="w", justify=tk.LEFT, padx=10, pady=8).pack(fill=tk.X, padx=22, pady=(8, 8))
        tk.Label(dlg, text="覆盖：用 RAW 文件夹里的原片替换它们\n跳过：保留已有文件，只导出其余的",
                 bg=self.BG, fg=self.ASH, font=F(9), anchor="w", justify=tk.LEFT).pack(fill=tk.X, padx=22)
        bar = tk.Frame(dlg, bg=self.BG)
        bar.pack(pady=(14, 18))

        def choose(v):
            result['v'] = v
            dlg.destroy()
        self._btn(bar, "覆盖", lambda: choose('overwrite'), 'red', font=F(10, True), padx=20, pady=6).pack(side=tk.LEFT, padx=6)
        self._btn(bar, "跳过", lambda: choose('skip'), 'accent', font=F(10, True), padx=20, pady=6).pack(side=tk.LEFT, padx=6)
        self._btn(bar, "取消", lambda: choose(None), 'ghost', font=F(10, True), padx=20, pady=6).pack(side=tk.LEFT, padx=6)
        dlg.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        dlg.bind('<Escape>', lambda e: choose(None))
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_reqheight()) // 3
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        try:
            dlg.grab_set()
        except tk.TclError:
            pass
        dlg.focus_set()
        self.root.wait_window(dlg)
        return result['v']

    def _start_export(self):
        if self.worker is not None:
            if isinstance(self.worker, ExportWorker):
                self._cancel_export()
            return
        raws = self._checked_raws()
        if not raws:
            self._error("提示", "没有勾选任何 RAW。")
            return
        raw_path = self._match_ctx[1] if self._match_ctx else self.raw_var.get().strip()
        out_dir = self._resolve_out_dir()
        if not out_dir:
            self._error("提示", "请选择输出文件夹。")
            return
        if os.path.isdir(out_dir) and os.path.isdir(raw_path) and _same_file(raw_path, out_dir):
            self._error("输出文件夹不能和 RAW 文件夹相同",
                        f"{out_dir}\n\n请在「③ 输出文件夹」另外选择一个文件夹。")
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
            if not os.access(out_dir, os.W_OK):
                raise PermissionError("文件夹为只读")
        except Exception as e:
            self._error("无法写入输出文件夹",
                        f"{out_dir}\n\n{e}\n\n提示：NTFS 格式的硬盘在 Mac 上是只读的。")
            return
        self._update_out_hint()

        plan = build_copy_plan(raws, out_dir)
        conflicts = [d for s, d in plan.items() if os.path.exists(d) and not _same_file(s, d)]
        skipped = 0
        if conflicts:
            choice = self._ask_conflict(conflicts, out_dir)
            if choice is None:
                return
            if choice == 'skip':
                cset = set(conflicts)
                plan = {s: d for s, d in plan.items() if d not in cset}
                skipped = len(conflicts)

        self._set_busy_export(True)
        self.progress.configure(mode='determinate')
        self.progress['value'] = 0
        self.progress_text.configure(text="准备复制...")
        self.worker = ExportWorker(plan, raw_path, out_dir, skipped)
        self.worker.start()
        self._poll()

    def _set_busy_export(self, busy):
        if busy:
            self.export_btn.set_text("取消导出")
            self.export_btn.set_colors(self.RED, self.RED_HOVER)
            self.export_btn.set_enabled(True)
            self.match_btn.set_enabled(False)
        else:
            self.export_btn.set_text("导出勾选的RAW")
            self.export_btn.set_colors(self.ACCENT, self.ACCENT_HOVER)
            self.match_btn.set_enabled(True)

    def _cancel_export(self):
        if self.worker:
            self.worker.cancel()
        self.export_btn.set_text("正在取消...")
        self.export_btn.set_enabled(False)
        self.progress_text.configure(text="正在取消，等待正在复制的文件完成...")

    # ---------- 后台消息 ----------
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
                    self.progress_text.configure(
                        text=f"[{cur}/{total}] {pct:.1f}%　正在复制：{name}　"
                             f"已用 {format_seconds(elapsed)}　剩余 {format_seconds(remaining)}")
                elif kind == "matched":
                    self._end_worker()
                    self._show_rows(msg[1])
                    self._last_result = {'matched': True}
                    if self._selftest_auto_export:
                        self.root.after(50, self._start_export)
                    return
                elif kind == "exported":
                    self._end_worker()
                    self._finish_export(msg[1])
                    return
                elif kind == "cancelled":
                    was_export = isinstance(w, ExportWorker)
                    self._end_worker()
                    if was_export:
                        self.progress['value'] = 0
                        self.progress_text.configure(text=f"已取消导出（已复制 {msg[1]} 张）")
                    else:
                        self._clear_results("已取消匹配")
                    return
                elif kind == "error":
                    was_match = isinstance(w, MatchWorker)
                    self._end_worker()
                    if was_match:
                        self._clear_results("")
                    self._error("错误", msg[1])
                    return
        except queue.Empty:
            pass
        if w.thread and w.thread.is_alive():
            self.poll_id = self.root.after(100, self._poll)
        else:
            self._end_worker()

    def _end_worker(self):
        self.progress.stop()
        self.progress.configure(mode='determinate')
        self.worker = None
        self.poll_id = None
        self.match_btn.set_text("开始匹配")
        self.match_btn.set_colors(self.GREEN, self.GREEN_HOVER)
        self.match_btn.set_enabled(True)
        self._set_busy_export(False)
        self._update_selected()

    def _finish_export(self, s):
        self._last_result = s
        self.progress['value'] = 100
        copied, failed, skipped = s['copied'], s['failed'], s['skipped']
        txt = f"导出完成：复制了 {copied} 张 RAW"
        if skipped:
            txt += f"，跳过 {skipped} 张已存在"
        if failed:
            txt += f"，{len(failed)} 张失败"
        self.progress_text.configure(text=txt + f"　→ {s['output']}")
        if self._quiet:
            return
        lines = [txt, f"\n输出文件夹：\n{s['output']}"]
        if failed:
            lines.append(f"\n复制失败 {len(failed)} 张：")
            lines += failed[:8]
            if len(failed) > 8:
                lines.append(f"... 还有 {len(failed) - 8} 张")
        if messagebox.askyesno("导出结果", "\n".join(lines) + "\n\n是否打开输出文件夹？", parent=self.root):
            open_with_system(s['output'])

    def _on_close(self):
        if self.worker is not None:
            self.worker.cancel()
            if self.worker.thread and self.worker.thread.is_alive():
                self.worker.thread.join(timeout=3)
        self.root.destroy()          # 不保存任何路径

    # ---------- 自检（打包后 CI 用）----------
    def run_selftest(self, base, result_file):
        """RP_SELFTEST_MODE: folder / files / default(不填输出文件夹 → JPG目录\\raw)"""
        self._quiet = True
        self._selftest_auto_export = True
        mode = os.environ.get('RP_SELFTEST_MODE', 'folder')
        jdir = os.path.join(base, 'jpg')
        if mode == 'files':
            files = sorted(os.path.join(jdir, f) for f in os.listdir(jdir) if f.lower().endswith('.jpg'))
            self._set_jpg_files(files[:2])
        else:
            self._set_jpg_folder(jdir)
        self.raw_var.set(os.path.join(base, 'raw'))
        out = os.path.join(jdir, 'raw') if mode == 'default' else os.path.join(base, 'out')
        if mode == 'default':
            os.makedirs(out, exist_ok=True)              # 已存在的 raw 文件夹 + 同名文件 → 覆盖
            with open(os.path.join(out, 'DSC0002.CR2'), 'wb') as f:
                f.write(b'old')
        else:
            self.out_var.set(out)
        t0 = time.time()

        def tick():
            r = self._last_result
            if (r is not None and ('copied' in r or 'error' in r)) or time.time() - t0 > 60:
                r = r if r and ('copied' in r or 'error' in r) else {'error': 'timeout'}
                info = {'ok': 'error' not in r,
                        'tk_console': bool(self.root.tk.call('info', 'commands', 'console')),
                        'copied': r.get('copied'), 'failed': r.get('failed'),
                        'unmatched': sorted(os.path.basename(x['jpg']) for x in self.rows if not x['raws']),
                        'out_files': sorted(os.listdir(out)) if os.path.isdir(out) else [],
                        'overwritten': (os.path.getsize(os.path.join(out, 'DSC0002.CR2')) > 3
                                        if os.path.exists(os.path.join(out, 'DSC0002.CR2')) else None),
                        'error': r.get('error')}
                with open(result_file, 'w', encoding='utf-8') as f:
                    json.dump(info, f, ensure_ascii=False)
                self.root.destroy()
                return
            self.root.after(200, tick)

        self._start_match()
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
        root.after(800, app._start_match)
    st = os.environ.get('RP_SELFTEST_DIR')
    if st:
        app.run_selftest(st, os.environ.get('RP_SELFTEST_RESULT', os.path.join(st, 'result.json')))
    root.mainloop()


if __name__ == "__main__":
    main()
