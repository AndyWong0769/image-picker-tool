#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JPG 查找 RAW — macOS 独立版
============================
功能：
  - 选择多个 JPG 文件夹和多个 RAW 文件夹进行匹配
  - 支持文件名匹配和 EXIF 时间匹配
  - 输出目录不填写则默认导出到 JPG 目录下 raw 文件夹

增强（v2）：
  - JPG/RAW 目录支持添加多个文件夹（来自不同路径）
  - 文件夹列表可单独移除
  - 配置自动保存/加载
"""

import os
import re
import sys
import json
import shutil
import subprocess
import platform
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ═══════════════════════════════════════════════
# 平台检测
# ═══════════════════════════════════════════════
_IS_MACOS = platform.system() == 'Darwin'
_IS_WINDOWS = platform.system() == 'Windows'

# 配置文件路径
if _IS_MACOS:
    _CONFIG_DIR = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'jpg_find_raw')
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    _CONFIG_PATH = os.path.join(_CONFIG_DIR, 'config.json')
else:
    _CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.jpg_find_raw.json')

# 平台字体
if _IS_MACOS:
    _FONT_FAMILY = ".AppleSystemUIFont"
    _FONT_MONO = "Menlo"
else:
    _FONT_FAMILY = "Segoe UI"
    _FONT_MONO = "Consolas"


def open_in_finder(path):
    """跨平台打开文件/文件夹"""
    if not os.path.exists(path):
        return
    if _IS_MACOS:
        subprocess.Popen(["open", path])
    elif _IS_WINDOWS:
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])


# ============================================================
# 核心逻辑
# ============================================================

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.webp', '.heic', '.heif'}

RAW_EXTENSIONS = {
    '.cr2', '.cr3', '.nef', '.nrw', '.arw', '.srf', '.sr2',
    '.raf', '.rw2', '.rwl', '.orf', '.pef', '.ptx', '.srw',
    '.raw', '.r3d', '.iiq', '.3fr', '.fff', '.x3f', '.dcr',
    '.kdc', '.mrw', '.erf', '.mef', '.mos', '.dng', '.gpr',
    '.braw', '.ari', '.bay',
}


def is_raw_file(filepath: str) -> bool:
    """判断是否为RAW文件"""
    ext = os.path.splitext(filepath)[1].lower()
    return ext in RAW_EXTENSIONS


def get_all_files_in_folder(folder: str) -> list:
    """递归获取文件夹内所有文件"""
    file_list = []
    if not folder or not os.path.isdir(folder):
        return file_list
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('$')]
        for file in files:
            if not file.startswith('.') and not file.startswith('~'):
                file_list.append(os.path.join(root, file))
    return file_list


def extract_possible_raw_names(filename: str) -> list:
    """从JPG文件名提取可能的RAW文件名模式"""
    name_without_ext = os.path.splitext(filename)[0]
    all_patterns = re.findall(r'[a-zA-Z0-9_]{2,}', name_without_ext)
    number_patterns = re.findall(r'\d{3,}', name_without_ext)
    candidates = set()
    candidates.add(name_without_ext.lower())
    for p in all_patterns:
        candidates.add(p.lower())
    for p in number_patterns:
        candidates.add(p.lower())
    return list(candidates)


def match_jpg_to_raw(jpg_files: list, raw_files: list) -> list:
    """匹配JPG到RAW文件 - 文件名匹配"""
    raw_dict = {}
    for f in raw_files:
        stem = os.path.splitext(os.path.basename(f))[0].lower()
        raw_dict[stem] = f

    results = []
    for jpg_path in jpg_files:
        jpg_filename = os.path.basename(jpg_path)
        jpg_stem = os.path.splitext(jpg_filename)[0].lower()
        found_raw = None
        method = None

        if jpg_stem in raw_dict:
            found_raw = raw_dict[jpg_stem]
            method = '文件名'

        if not found_raw:
            candidates = extract_possible_raw_names(jpg_filename)
            for cand in candidates:
                if cand in raw_dict:
                    found_raw = raw_dict[cand]
                    method = '文件名'
                    break

        results.append({
            'jpg_path': jpg_path,
            'jpg_name': jpg_filename,
            'raw_path': found_raw,
            'raw_name': os.path.basename(found_raw) if found_raw else None,
            'method': method
        })

    return results


def read_exif_datetime(filepath: str) -> str:
    """读取EXIF拍摄时间"""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in ('.jpg', '.jpeg', '.png'):
        try:
            from PIL import Image
            with Image.open(filepath) as img:
                exif = img._getexif()
                if exif:
                    for tag_id in (36867, 36868, 306):
                        if tag_id in exif:
                            return exif[tag_id]
        except Exception:
            pass
    try:
        with open(filepath, 'rb') as f:
            data = f.read(1048576)
        import re
        all_dates = re.findall(rb'20\d{2}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}', data)
        if all_dates:
            return all_dates[0].decode('ascii', errors='ignore')
    except Exception:
        pass
    return None


def match_by_exif(unmatched_results: list, all_raw_files: list, progress_cb=None) -> list:
    """通过EXIF时间匹配未匹配的JPG，同一RAW不会重复分配给多张JPG"""
    if not unmatched_results:
        return unmatched_results

    raw_by_time = {}
    for i, raw_path in enumerate(all_raw_files):
        dt = read_exif_datetime(raw_path)
        if dt:
            if dt not in raw_by_time:
                raw_by_time[dt] = []
            raw_by_time[dt].append(raw_path)
        if progress_cb and (i + 1) % 10 == 0:
            progress_cb(i + 1, len(all_raw_files))

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
# GUI
# ============================================================

class FindRawApp:
    """JPG查找RAW 独立应用程序"""

    # 配色
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
        self.root.title("JPG 查找 RAW")
        self.root.geometry("950x770")
        self.root.minsize(850, 580)
        self.root.configure(bg=self.BG)

        # Windows DPI 感知
        if _IS_WINDOWS:
            try:
                from ctypes import windll
                windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass

        # 数据
        self.jpg_folders = []
        self.raw_folders = []
        self.results = []
        self.selected_rows = set()

        self._load_config()
        self._build_ui()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    def _build_ui(self):
        bg = self.BG
        surface = self.SURFACE
        border = self.BORDER
        ink = self.INK
        ash = self.ASH
        accent = self.ACCENT
        F = _FONT_FAMILY
        M = _FONT_MONO

        main_frame = tk.Frame(self.root, bg=bg, padx=16, pady=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(main_frame, text="JPG 查找 RAW", bg=bg, fg=accent,
                 font=(F, 16, "bold")).pack(anchor="w")
        tk.Label(main_frame, text="支持多个 JPG 文件夹和 RAW 文件夹进行匹配，支持文件名匹配和 EXIF 时间匹配",
                 bg=bg, fg=ash, font=(F, 9)).pack(anchor="w", pady=(2, 10))

        # ── 文件夹选择区（卡片）──
        folder_card = tk.Frame(main_frame, bg=surface, highlightbackground=border,
                               highlightthickness=1, padx=12, pady=10)
        folder_card.pack(fill=tk.X, pady=(0, 8))

        # === JPG 目录 ===
        jpg_header = tk.Frame(folder_card, bg=surface)
        jpg_header.pack(fill=tk.X)
        tk.Label(jpg_header, text="JPG 目录", bg=surface, fg=ash,
                 font=(F, 10, "bold")).pack(side=tk.LEFT)

        jpg_btn_row = tk.Frame(jpg_header, bg=surface)
        jpg_btn_row.pack(side=tk.RIGHT)

        self.jpg_add_btn = tk.Button(jpg_btn_row, text=" ＋ 添加文件夹",
                                     command=self._add_jpg_folder,
                                     bg=accent, fg=ink,
                                     activebackground=self.ACCENT_HOVER,
                                     activeforeground=ink,
                                     relief="flat", padx=10, pady=4,
                                     font=(F, 9, "bold"),
                                     cursor="hand2")
        self.jpg_add_btn.pack(side=tk.RIGHT)

        self.jpg_listbox_frame = tk.Frame(folder_card, bg='#1a1a24',
                                           highlightbackground=border, highlightthickness=1)
        self.jpg_listbox_frame.pack(fill=tk.X, pady=(6, 0), ipady=2)
        self.jpg_listbox = tk.Listbox(self.jpg_listbox_frame, height=3,
                                       bg='#1a1a24', fg='#c0c0d0',
                                       font=(M, 9),
                                       selectbackground='#2a2a4a',
                                       selectforeground='#e0e0f0',
                                       relief="flat", bd=0,
                                       highlightthickness=0,
                                       activestyle='none')
        self.jpg_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=4)
        self.jpg_listbox.bind('<Delete>', lambda e: self._remove_selected_jpg())
        self.jpg_listbox.bind('<Double-1>', lambda e: self._open_jpg_folder())
        self.jpg_listbox.bind('<Button-3>' if _IS_MACOS else '<Button-3>', lambda e: self._popup_jpg_menu(e))
        # macOS 右键是 Button-2 或 Button-3 with Control
        if _IS_MACOS:
            self.jpg_listbox.bind('<Control-Button-1>', lambda e: self._popup_jpg_menu(e))

        jpg_scroll = ttk.Scrollbar(self.jpg_listbox_frame, orient="vertical",
                                    command=self.jpg_listbox.yview,
                                    style="Dark.Vertical.TScrollbar")
        jpg_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.jpg_listbox.config(yscrollcommand=jpg_scroll.set)

        # === RAW 目录 ===
        raw_header = tk.Frame(folder_card, bg=surface)
        raw_header.pack(fill=tk.X, pady=(12, 0))
        tk.Label(raw_header, text="RAW 目录", bg=surface, fg=ash,
                 font=(F, 10, "bold")).pack(side=tk.LEFT)

        raw_btn_row = tk.Frame(raw_header, bg=surface)
        raw_btn_row.pack(side=tk.RIGHT)

        self.raw_add_btn = tk.Button(raw_btn_row, text=" ＋ 添加文件夹",
                                     command=self._add_raw_folder,
                                     bg=accent, fg=ink,
                                     activebackground=self.ACCENT_HOVER,
                                     activeforeground=ink,
                                     relief="flat", padx=10, pady=4,
                                     font=(F, 9, "bold"),
                                     cursor="hand2")
        self.raw_add_btn.pack(side=tk.RIGHT)

        self.raw_listbox_frame = tk.Frame(folder_card, bg='#1a1a24',
                                           highlightbackground=border, highlightthickness=1)
        self.raw_listbox_frame.pack(fill=tk.X, pady=(6, 0), ipady=2)
        self.raw_listbox = tk.Listbox(self.raw_listbox_frame, height=3,
                                       bg='#1a1a24', fg='#c0c0d0',
                                       font=(M, 9),
                                       selectbackground='#2a2a4a',
                                       selectforeground='#e0e0f0',
                                       relief="flat", bd=0,
                                       highlightthickness=0,
                                       activestyle='none')
        self.raw_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=4)
        self.raw_listbox.bind('<Delete>', lambda e: self._remove_selected_raw())
        self.raw_listbox.bind('<Double-1>', lambda e: self._open_raw_folder())
        self.raw_listbox.bind('<Button-3>' if _IS_MACOS else '<Button-3>', lambda e: self._popup_raw_menu(e))
        if _IS_MACOS:
            self.raw_listbox.bind('<Control-Button-1>', lambda e: self._popup_raw_menu(e))

        raw_scroll = ttk.Scrollbar(self.raw_listbox_frame, orient="vertical",
                                    command=self.raw_listbox.yview,
                                    style="Dark.Vertical.TScrollbar")
        raw_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.raw_listbox.config(yscrollcommand=raw_scroll.set)

        # === 输出目录 ===
        out_row = tk.Frame(folder_card, bg=surface)
        out_row.pack(fill=tk.X, pady=(12, 0))
        tk.Label(out_row, text="输出目录", bg=surface, fg=ash,
                 font=(F, 10, "bold")).pack(side=tk.LEFT)
        self.out_entry = tk.Entry(out_row, font=(M, 9),
                                   bg=bg, fg=ink, insertbackground=ink,
                                   relief="flat",
                                   highlightthickness=1, highlightbackground=border)
        self.out_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 6), ipady=5)
        tk.Button(out_row, text="浏览", command=self._browse_out,
                  bg=accent, fg=ink,
                  activebackground=self.ACCENT_HOVER,
                  activeforeground=ink,
                  relief="flat", padx=10, pady=4,
                  font=(F, 9, "bold"),
                  cursor="hand2").pack(side=tk.RIGHT)

        self._out_placeholder = "不填写默认导出到JPG目录下RAW文件夹"
        self.out_entry.insert(0, self._out_placeholder)
        self.out_entry.config(fg='#5a5a70')
        self.out_entry.bind('<FocusIn>', self._on_out_focus_in)
        self.out_entry.bind('<FocusOut>', self._on_out_focus_out)

        # ── 操作按钮行 ──
        btn_row = tk.Frame(main_frame, bg=bg)
        btn_row.pack(fill=tk.X, pady=10)

        btn_style = {"bg": accent, "fg": ink, "relief": "flat",
                     "width": 14, "pady": 10,
                     "font": (F, 10, "bold"),
                     "activebackground": self.ACCENT_HOVER,
                     "activeforeground": ink,
                     "cursor": "hand2"}

        btn_center = tk.Frame(btn_row, bg=bg)
        btn_center.pack(side=tk.TOP)

        self.match_btn = tk.Button(btn_center, text="文件名匹配",
                                   command=self._start_match, **btn_style)
        self.match_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.exif_btn = tk.Button(btn_center, text="EXIF 匹配",
                                  command=self._start_exif, **btn_style)
        self.exif_btn.config(state=tk.DISABLED, bg=self.SURFACE, fg=self.ASH)
        self.exif_btn.pack(side=tk.LEFT)

        # 状态行
        status_row = tk.Frame(main_frame, bg=bg)
        status_row.pack(fill=tk.X)
        self.status_label = tk.Label(status_row, text="就绪", bg=bg, fg=ash,
                                      font=(F, 9))
        self.status_label.pack(side=tk.LEFT)
        self.stats_label = tk.Label(status_row, text="", bg=bg, fg=accent,
                                     font=(F, 9, "bold"))
        self.stats_label.pack(side=tk.RIGHT)

        # ── 结果表格 ──
        tree_frame = tk.Frame(main_frame, bg=bg)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        style = ttk.Style()
        if _IS_MACOS:
            style.theme_use('aqua')
        else:
            style.theme_use('clam')

        style.configure("Dark.Treeview",
                        background='#1a1a24',
                        foreground='#c0c0d0',
                        fieldbackground='#1a1a24',
                        bordercolor='#1a1a24',
                        lightcolor='#1a1a24',
                        darkcolor='#1a1a24',
                        borderwidth=0)
        style.layout('Dark.Treeview', [('Dark.Treeview.treearea', {'sticky': 'nswe'})])
        style.configure("Dark.Treeview.Heading",
                        background='#6c8aff',
                        foreground='#ffffff',
                        bordercolor='#1a1a24',
                        lightcolor='#6c8aff',
                        darkcolor='#6c8aff',
                        relief='flat',
                        font=(F, 9, 'bold'),
                        padding=(6, 4))
        style.map("Dark.Treeview.Heading",
                  background=[('active', '#8098ff'), ('!active', '#6c8aff')],
                  foreground=[('active', '#ffffff'), ('!active', '#ffffff')])
        style.map("Dark.Treeview",
                  background=[('selected', '#2a2a4a')],
                  foreground=[('selected', '#e0e0f0')])
        style.configure("Dark.Vertical.TScrollbar",
                        background='#252535',
                        troughcolor='#1a1a24',
                        borderwidth=0,
                        arrowcolor='#6c8aff',
                        gripcount=0)
        style.map("Dark.Vertical.TScrollbar",
                  background=[('active', '#3a3a4e'), ('!active', '#252535')],
                  troughcolor=[('active', '#1a1a24'), ('!active', '#1a1a24')])

        columns = ('checked', 'jpg', 'raw', 'method')
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings',
                                 selectmode='extended', style="Dark.Treeview")
        self.tree.heading('checked', text='勾选')
        self.tree.heading('jpg', text='JPG 文件')
        self.tree.heading('raw', text='RAW 文件')
        self.tree.heading('method', text='匹配方式')
        self.tree.column('checked', width=60, anchor='center')
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
        self.tree.bind('<MouseWheel>',
                        lambda e: self.tree.yview_scroll(int(-1 * (e.delta / 120)), 'units'))
        # Linux 滚轮
        if not _IS_MACOS:
            self.tree.bind('<Button-4>', lambda e: self.tree.yview_scroll(-1, 'units'))
            self.tree.bind('<Button-5>', lambda e: self.tree.yview_scroll(1, 'units'))

        # 进度条
        style.configure('Green.Horizontal.TProgressbar',
                        background=self.SUCCESS,
                        troughcolor='#1a1a24',
                        borderwidth=0,
                        thickness=4)
        self.progress = ttk.Progressbar(main_frame, mode='determinate',
                                         style='Green.Horizontal.TProgressbar')

        # 底部按钮
        bottom_row = tk.Frame(main_frame, bg=bg)
        bottom_row.pack(fill=tk.X, pady=(8, 0))

        self.matched_label = tk.Label(bottom_row, text="0 已匹配", bg=bg,
                                       fg=self.SUCCESS,
                                       font=(F, 9, "bold"))
        self.matched_label.pack(side=tk.LEFT)
        self.unmatched_label = tk.Label(bottom_row, text="0 未匹配", bg=bg,
                                        fg=self.ERROR,
                                        font=(F, 9, "bold"))
        self.unmatched_label.pack(side=tk.LEFT, padx=(12, 0))

        tk.Button(bottom_row, text="导出勾选的RAW",
                  command=self._export_selected,
                  bg=accent, fg=ink,
                  activebackground=self.ACCENT_HOVER,
                  activeforeground=ink,
                  relief="flat", padx=16, pady=6,
                  font=(F, 9, "bold"),
                  cursor="hand2").pack(side=tk.RIGHT)

        self.root.after(100, self._refresh_both_lists)

    def _refresh_both_lists(self):
        self._refresh_jpg_list()
        self._refresh_raw_list()

    # ── 文件夹管理 ──
    def _add_jpg_folder(self):
        d = filedialog.askdirectory(title="添加 JPG 文件夹")
        if d and d not in self.jpg_folders:
            self.jpg_folders.append(d)
            self._refresh_jpg_list()

    def _add_raw_folder(self):
        d = filedialog.askdirectory(title="添加 RAW 文件夹")
        if d and d not in self.raw_folders:
            self.raw_folders.append(d)
            self._refresh_raw_list()

    def _browse_out(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.out_entry.delete(0, tk.END)
            self.out_entry.insert(0, d)
            self.out_entry.config(fg=self.INK)

    def _open_jpg_folder(self):
        sel = self.jpg_listbox.curselection()
        if sel:
            folder = self.jpg_listbox.get(sel[0])
            if os.path.isdir(folder):
                open_in_finder(folder)

    def _open_raw_folder(self):
        sel = self.raw_listbox.curselection()
        if sel:
            folder = self.raw_listbox.get(sel[0])
            if os.path.isdir(folder):
                open_in_finder(folder)

    def _popup_jpg_menu(self, event):
        sel = self.jpg_listbox.nearest(event.y)
        self.jpg_listbox.selection_clear(0, tk.END)
        self.jpg_listbox.selection_set(sel)
        menu = tk.Menu(self.root, tearoff=0, bg='#2a2a3c', fg='#c0c0d0',
                       font=(_FONT_FAMILY, 9), activebackground='#3a3a4e',
                       activeforeground='#e0e0f0', borderwidth=1)
        menu.add_command(label="打开文件夹", command=self._open_jpg_folder)
        menu.add_command(label="删除此路径", command=self._remove_selected_jpg)
        menu.post(event.x_root, event.y_root)

    def _popup_raw_menu(self, event):
        sel = self.raw_listbox.nearest(event.y)
        self.raw_listbox.selection_clear(0, tk.END)
        self.raw_listbox.selection_set(sel)
        menu = tk.Menu(self.root, tearoff=0, bg='#2a2a3c', fg='#c0c0d0',
                       font=(_FONT_FAMILY, 9), activebackground='#3a3a4e',
                       activeforeground='#e0e0f0', borderwidth=1)
        menu.add_command(label="打开文件夹", command=self._open_raw_folder)
        menu.add_command(label="删除此路径", command=self._remove_selected_raw)
        menu.post(event.x_root, event.y_root)

    def _refresh_jpg_list(self):
        self.jpg_listbox.delete(0, tk.END)
        for folder in self.jpg_folders:
            self.jpg_listbox.insert(tk.END, folder)

    def _refresh_raw_list(self):
        self.raw_listbox.delete(0, tk.END)
        for folder in self.raw_folders:
            self.raw_listbox.insert(tk.END, folder)

    def _remove_selected_jpg(self):
        sel = self.jpg_listbox.curselection()
        if sel:
            idx = sel[0]
            if 0 <= idx < len(self.jpg_folders):
                self.jpg_folders.pop(idx)
                self._refresh_jpg_list()

    def _remove_selected_raw(self):
        sel = self.raw_listbox.curselection()
        if sel:
            idx = sel[0]
            if 0 <= idx < len(self.raw_folders):
                self.raw_folders.pop(idx)
                self._refresh_raw_list()

    # ── 配置持久化 ──
    def _load_config(self):
        try:
            if os.path.exists(_CONFIG_PATH):
                with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.jpg_folders = [p for p in data.get('jpg_folders', []) if os.path.isdir(p)]
                self.raw_folders = [p for p in data.get('raw_folders', []) if os.path.isdir(p)]
        except Exception:
            pass

    def _save_config(self):
        try:
            data = {
                'jpg_folders': self.jpg_folders,
                'raw_folders': self.raw_folders,
            }
            with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_close(self):
        self._save_config()
        self.root.destroy()

    # ── 输出目录占位提示 ──
    def _on_out_focus_in(self, event):
        current = self.out_entry.get()
        if current == self._out_placeholder:
            self.out_entry.delete(0, tk.END)
            self.out_entry.config(fg=self.INK)

    def _on_out_focus_out(self, event):
        if not self.out_entry.get().strip():
            self.out_entry.delete(0, tk.END)
            self.out_entry.insert(0, self._out_placeholder)
            self.out_entry.config(fg='#5a5a70')

    # ── 匹配逻辑 ──
    def _collect_jpg_files(self):
        files = []
        for folder in self.jpg_folders:
            files.extend(f for f in get_all_files_in_folder(folder)
                         if f.lower().endswith(('.jpg', '.jpeg', '.png')))
        return files

    def _collect_raw_files(self):
        files = []
        for folder in self.raw_folders:
            files.extend(f for f in get_all_files_in_folder(folder)
                         if is_raw_file(f))
        return files

    def _start_match(self):
        if not self.jpg_folders:
            messagebox.showwarning("提示", "请至少添加一个 JPG 文件夹")
            return

        self.match_btn.config(state=tk.DISABLED)
        self.exif_btn.config(state=tk.DISABLED)
        self.status_label.config(text="扫描中...")
        self.tree.delete(*self.tree.get_children())
        self.results.clear()

        def worker():
            jpg_files = self._collect_jpg_files()
            if not jpg_files:
                self.root.after(0, lambda: self._on_match_done([], "未找到JPG文件"))
                return

            raw_files = self._collect_raw_files()
            results = match_jpg_to_raw(jpg_files, raw_files)
            self.root.after(0, lambda: self._on_match_done(results, "文件名匹配完成"))

        threading.Thread(target=worker, daemon=True).start()

    def _start_exif(self):
        if not self.jpg_folders:
            messagebox.showwarning("提示", "请至少添加一个 JPG 文件夹")
            return

        self.match_btn.config(state=tk.DISABLED)
        self.exif_btn.config(state=tk.DISABLED)
        self.status_label.config(text="EXIF匹配中...")

        def worker():
            jpg_files = self._collect_jpg_files()
            if not jpg_files:
                self.root.after(0, lambda: self._on_match_done([], "未找到JPG文件"))
                return

            raw_files = self._collect_raw_files()

            if not self.results:
                base_results = [{
                    'jpg_path': f,
                    'jpg_name': os.path.basename(f),
                    'raw_path': None,
                    'raw_name': None,
                    'method': None
                } for f in jpg_files]
            else:
                base_results = list(self.results)
                existing_jpg = {r['jpg_path'] for r in base_results}
                for f in jpg_files:
                    if f not in existing_jpg:
                        base_results.append({
                            'jpg_path': f,
                            'jpg_name': os.path.basename(f),
                            'raw_path': None,
                            'raw_name': None,
                            'method': None
                        })

            unmatched = [r for r in base_results if not r['raw_path']]
            matched = match_by_exif(unmatched, raw_files,
                                    progress_cb=lambda c, t: self.root.after(
                                        0, lambda: self.status_label.config(
                                            text=f"EXIF: {c}/{t}")))
            matched_dict = {r['jpg_path']: r for r in matched}
            final = [matched_dict.get(r['jpg_path'], r) for r in base_results]
            self.root.after(0, lambda: self._on_match_done(final, "EXIF匹配完成"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_match_done(self, results, status):
        self.results = results
        self.match_btn.config(state=tk.NORMAL)
        matched = sum(1 for r in results if r['raw_path'])
        unmatched = len(results) - matched
        self.matched_label.config(text=f"{matched} 已匹配")
        self.unmatched_label.config(text=f"{unmatched} 未匹配")
        self.stats_label.config(
            text=f"{len(results)} JPG | {matched} 匹配 | {unmatched} 未匹配")
        self.status_label.config(text=status)

        if unmatched > 0:
            self.exif_btn.config(state=tk.NORMAL, bg=self.ACCENT, fg=self.INK)
        else:
            self.exif_btn.config(state=tk.DISABLED, bg=self.SURFACE, fg=self.ASH)

        self.tree.delete(*self.tree.get_children())
        self.selected_rows = set()
        for idx, r in enumerate(results):
            raw_text = r['raw_name'] or '- 未找到 -'
            item = self.tree.insert('', tk.END, values=(
                '☑' if r['raw_path'] else '☐',
                r['jpg_name'],
                raw_text,
                r['method'] or '-'
            ), tags=('found' if r['raw_path'] else 'notfound',))
            if r['raw_path']:
                self.selected_rows.add(item)

        self.tree.tag_configure('notfound', foreground='#ff6b6b')
        self.tree.tag_configure('found', foreground='#c0c0d0')
        self.tree.update_idletasks()

    def _on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        if col != '#1':
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
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if not item:
            return
        idx = self.tree.index(item)
        if 0 <= idx < len(self.results):
            result = self.results[idx]
            if col == '#2':
                jpg_path = result.get('jpg_path')
                if jpg_path and os.path.exists(jpg_path):
                    open_in_finder(jpg_path)
            elif col == '#3':
                raw_path = result.get('raw_path')
                if raw_path and os.path.exists(raw_path):
                    open_in_finder(raw_path)

    def _find_jpg_folder_for_file(self, jpg_path):
        for folder in self.jpg_folders:
            if jpg_path.startswith(folder + os.sep) or jpg_path.startswith(folder + '/'):
                return folder
        return None

    def _export_selected(self):
        if not self.selected_rows:
            messagebox.showinfo("提示", "没有选中的RAW文件")
            return

        out_dir = self.out_entry.get().strip()
        if out_dir == self._out_placeholder:
            out_dir = ''

        tasks = []
        folder_files = {}

        for item in self.selected_rows:
            idx = self.tree.index(item)
            if 0 <= idx < len(self.results):
                result = self.results[idx]
                raw_path = result.get('raw_path')
                jpg_path = result.get('jpg_path')
                if raw_path and os.path.exists(raw_path):
                    if out_dir:
                        target_folder = out_dir
                    else:
                        jpg_folder = self._find_jpg_folder_for_file(jpg_path) if jpg_path else None
                        if jpg_folder:
                            target_folder = os.path.join(jpg_folder, 'raw')
                        elif self.jpg_folders:
                            target_folder = os.path.join(self.jpg_folders[0], 'raw')
                        else:
                            target_folder = None

                    if not target_folder:
                        continue

                    basename = os.path.basename(raw_path)
                    dst_path = os.path.join(target_folder, basename)

                    if target_folder not in folder_files:
                        folder_files[target_folder] = []
                    folder_files[target_folder].append({
                        'src': raw_path,
                        'dst': dst_path,
                        'basename': basename,
                    })

        if not folder_files:
            messagebox.showinfo("提示", "没有可导出的RAW文件")
            return

        conflict_files = []
        for folder, files in folder_files.items():
            existing_names = set()
            for f in files:
                if os.path.exists(f['dst']) or f['basename'] in existing_names:
                    conflict_files.append(f['basename'])
                existing_names.add(f['basename'])

        if conflict_files:
            preview = ', '.join(conflict_files[:10])
            if len(conflict_files) > 10:
                preview += f' ...等共 {len(conflict_files)} 个'
            msg = f"目标文件夹已有 {len(conflict_files)} 个同名文件：\n\n{preview}\n\n是否覆盖？"
            if not messagebox.askyesno("覆盖确认", msg):
                return

        for folder, files in folder_files.items():
            for f in files:
                tasks.append((f['src'], f['dst'], folder))

        if not tasks:
            messagebox.showinfo("提示", "没有可导出的RAW文件")
            return

        self.progress['maximum'] = len(tasks)
        self.progress['value'] = 0
        self.progress.pack(fill=tk.X, pady=(4, 0))

        # 预创建所有目标文件夹
        for _, _, dst_folder in tasks:
            os.makedirs(dst_folder, exist_ok=True)

        COPY_WORKERS = 4  # 并行复制线程数

        def worker():
            count = 0
            lock = threading.Lock()

            def copy_one(idx, src_path, dst_path, dst_folder):
                nonlocal count
                try:
                    # 同名文件加序号避免覆盖
                    if os.path.exists(dst_path):
                        stem = os.path.splitext(os.path.basename(dst_path))[0]
                        ext = os.path.splitext(os.path.basename(dst_path))[1]
                        counter = 1
                        while True:
                            new_name = f"{stem}_{counter}{ext}"
                            dst_path = os.path.join(dst_folder, new_name)
                            if not os.path.exists(dst_path):
                                break
                            counter += 1
                    shutil.copy2(src_path, dst_path)
                    with lock:
                        count += 1
                except Exception:
                    pass
                self.root.after(0, lambda v=idx + 1: self.progress.config(value=v))

            with ThreadPoolExecutor(max_workers=COPY_WORKERS) as executor:
                futures = []
                for i, (src_path, dst_path, dst_folder) in enumerate(tasks):
                    futures.append(executor.submit(copy_one, i, src_path, dst_path, dst_folder))
                for f in as_completed(futures):
                    f.result()

            def _done():
                self.progress.pack_forget()
                messagebox.showinfo("完成", f"已导出 {count} 个RAW文件")
            self.root.after(0, _done)

        threading.Thread(target=worker, daemon=True).start()


# ============================================================
# 入口
# ============================================================

def main():
    root = tk.Tk()
    if _IS_WINDOWS:
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = FindRawApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
