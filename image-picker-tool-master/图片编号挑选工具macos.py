#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片编号批量挑选工具 v4.0 (macOS 优化版)
========================
工作流：
  1. 源目录：原图（文件名如 FDV- (1).jpg, IMG_0922.jpeg）
  2. 挑选目录：工人改好名的拼图（如 001-(1).jpg）
  3. 自动从挑选目录提取编号 → 精确匹配源目录 → 复制到输出目录

核心能力：
  - 安全提取编号：按非数字字符分割，避免前缀混淆
  - 支持开头序号过滤：# 通配数字，如 ###- 过滤 001-
  - 支持"挑选目录"模式：扫描工人改名的文件夹自动获取编号
  - 支持"手动输入"模式：直接粘贴编号列表
  - 匹配预览：执行前展示匹配结果，确认后再操作

macOS 适配：
  - 配置存储：~/Library/Application Support/图片编号挑选工具/
  - 文件打开：使用 macOS open 命令
  - 字体：使用 .AppleSystemUIFont
  - 快捷键：⌘ (Command) 替代 Ctrl
  - 路径：原生正斜杠
"""

import os
import re
import sys
import json
import shutil
import hmac
import base64
import hashlib
import threading
import subprocess

# ── 最早期的错误捕获（import 阶段） ──
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
    from datetime import datetime
    from pathlib import Path
    from collections import defaultdict
except Exception as _e:
    import traceback
    _log_path = os.path.join(os.path.expanduser('~'), 'Desktop', 'image_picker_crash.log')
    with open(_log_path, 'w', encoding='utf-8') as _f:
        _f.write(f"Import crash: {_e}\n\n{traceback.format_exc()}")
    raise


# ============================================================
# 平台检测
# ============================================================

IS_MACOS = sys.platform == 'darwin'
IS_WINDOWS = sys.platform == 'win32'

# 试用版构建标记 — CI 构建试用版时会 sed 替换为 True
# 试用版跳过授权验证，改用 24 小时试用期逻辑
IS_TRIAL_BUILD = False

# 免费版构建标记 — CI 构建免费版时会 sed 替换为 True
# 免费版永久免激活，无时间限制
IS_FREE_BUILD = False


# ============================================================
# macOS 专用工具函数
# ============================================================

def open_file_or_folder(path):
    """跨平台打开文件或文件夹（macOS 用 open 命令）"""
    if not path or not os.path.exists(path):
        return
    try:
        if IS_MACOS:
            subprocess.run(['open', path], check=False)
        elif IS_WINDOWS:
            os.startfile(path)
        else:
            subprocess.run(['xdg-open', path], check=False)
    except Exception:
        pass


def get_app_config_dir():
    """
    获取应用配置目录（专业 macOS 做法）
    macOS: ~/Library/Application Support/图片编号挑选工具/
    Windows: %APPDATA%/图片编号挑选工具/
    """
    if IS_MACOS:
        base = os.path.expanduser('~/Library/Application Support')
    elif IS_WINDOWS:
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
    else:
        base = os.path.expanduser('~/.config')

    config_dir = os.path.join(base, '图片编号挑选工具')
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


def get_config_path():
    """获取配置文件路径"""
    return os.path.join(get_app_config_dir(), 'config.txt')


def get_system_font():
    """获取系统字体"""
    if IS_MACOS:
        return '.AppleSystemUIFont'
    elif IS_WINDOWS:
        return 'Segoe UI'
    else:
        return 'Sans'


def get_monospace_font():
    """获取等宽字体"""
    if IS_MACOS:
        return 'Menlo'
    elif IS_WINDOWS:
        return 'Consolas'
    else:
        return 'Monospace'


def normalize_path(path):
    """统一路径分隔符（保持原生，不做强制转换）"""
    if not path:
        return path
    # 移除首尾空白，但不强制转换分隔符（保持系统原生）
    return path.strip()


def get_modifier_key():
    """获取修饰键名称（用于显示）"""
    return '⌘' if IS_MACOS else 'Ctrl'


def is_modifier_pressed(event):
    """检测是否按下了修饰键（macOS: Command, Windows: Ctrl）"""
    if IS_MACOS:
        return bool(event.state & 0x0008)
    else:
        return bool(event.state & 0x0004)


# ============================================================
# 授权系统（序列号 + 激活码）
# ============================================================

# 你的签名密钥 - 请修改成你自己的随机字符串（至少16位，妥善保管）
# 警告：生成后不要再修改，否则以前生成的激活码会全部失效！
LICENSE_SECRET_KEY = "PYTNV5CCECTGGH5KC73ZUWCKEKK83VXR"


def get_mac_serial():
    """获取 Mac 序列号"""
    if not IS_MACOS:
        return "WINDOWS-PC"

    try:
        result = subprocess.run(
            ['system_profiler', 'SPHardwareDataType'],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.split('\n'):
            if 'Serial Number' in line:
                serial = line.split(':')[-1].strip()
                if serial:
                    return serial
    except Exception:
        pass

    # 备选：用 ioreg
    try:
        result = subprocess.run(
            ['ioreg', '-l'], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.split('\n'):
            if 'IOPlatformSerialNumber' in line:
                parts = line.split('"')
                for i, p in enumerate(parts):
                    if 'IOPlatformSerialNumber' in p and i + 2 < len(parts):
                        return parts[i + 2]
    except Exception:
        pass

    return "UNKNOWN"


def generate_activation_code(serial):
    """根据序列号生成激活码（给卖家用的工具函数）"""
    msg = serial.encode('utf-8')
    sig = hmac.new(LICENSE_SECRET_KEY.encode('utf-8'), msg, hashlib.sha256).digest()
    # 转 base32 去掉易混淆字符，格式 XXXXX-XXXXX-XXXXX
    encoded = base64.b32encode(sig).decode('utf-8')
    # 去掉易混淆的 0/O/I/L/1/8/B，只保留安全字符
    safe = encoded.translate(str.maketrans('0OIL18B', 'XXXXXXX'))[:15]
    # 如果不够15位，用安全字符补齐
    safe_chars = 'ACDEFGHJKMNPQRTUVWXY2345679'
    while len(safe) < 15:
        idx = len(safe) % len(safe_chars)
        safe += safe_chars[idx]
    # 格式: XXXXX-XXXXX-XXXXX
    return f"{safe[:5]}-{safe[5:10]}-{safe[10:15]}"


def verify_activation_code(code, serial):
    """验证激活码是否匹配序列号"""
    expected = generate_activation_code(serial)
    # 标准化输入
    code_clean = code.strip().upper().replace(' ', '').replace('-', '')
    expected_clean = expected.replace('-', '')
    return code_clean == expected_clean


def get_activation_file_path():
    """获取激活状态文件路径"""
    return os.path.join(get_app_config_dir(), '.activated')


def get_trial_file_path():
    """获取试用期记录文件路径"""
    return os.path.join(get_app_config_dir(), '.trial')


def check_trial():
    """检查试用期状态，返回 (是否可用, 剩余小时数, 消息)"""
    trial_file = get_trial_file_path()
    TRIAL_HOURS = 24  # 24小时

    if not os.path.exists(trial_file):
        # 首次启动，记录开始时间
        now = datetime.now().isoformat()
        try:
            with open(trial_file, 'w', encoding='utf-8') as f:
                json.dump({'start': now}, f)
        except Exception:
            pass
        return True, TRIAL_HOURS, f"试用期第 1 小时，共 {TRIAL_HOURS} 小时"

    try:
        with open(trial_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        start_str = data.get('start', '')
        start = datetime.fromisoformat(start_str)
        elapsed_hours = (datetime.now() - start).total_seconds() / 3600
        remaining = TRIAL_HOURS - elapsed_hours

        if remaining > 0:
            return True, remaining, f"试用期剩余 {int(remaining)} 小时"
        else:
            return False, 0, "试用期已结束，请购买激活"
    except Exception:
        return True, TRIAL_HOURS, f"试用期第 1 小时，共 {TRIAL_HOURS} 小时"


def check_license():
    """检查本机授权状态"""
    if not IS_MACOS:
        return True, "Windows 版无需激活"

    # 免费版：永久免激活，无时间限制
    if IS_FREE_BUILD:
        return True, "免费版"

    # 试用版：24 小时限时
    if IS_TRIAL_BUILD:
        return check_trial()

    serial = get_mac_serial()
    act_file = get_activation_file_path()

    if not os.path.exists(act_file):
        return False, "未激活"

    try:
        with open(act_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        saved_code = data.get('code', '')
        saved_serial = data.get('serial', '')

        # 序列号匹配 + 激活码验证
        if saved_serial == serial and verify_activation_code(saved_code, serial):
            return True, "已激活"
        else:
            return False, "授权无效"
    except Exception:
        return False, "授权数据损坏"


def save_activation(code):
    """保存激活状态"""
    serial = get_mac_serial()
    act_file = get_activation_file_path()
    data = {
        'code': code.strip().upper(),
        'serial': serial,
        'activated_at': datetime.now().isoformat()
    }
    with open(act_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


# ============================================================
# 核心逻辑
# ============================================================

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.webp', '.heic', '.heif'}

MATCH_MODE_MAP = {"精确": "exact", "包含": "contains"}
OP_MODE_MAP = {"复制": "copy", "移动": "move"}
DUP_MODE_MAP = {"重命名": "rename", "覆盖": "overwrite", "跳过": "skip"}


def _resolve_mode(cn_value, mapping):
    return mapping.get(cn_value, cn_value)


def is_image_file(filename: str) -> bool:
    """判断是否为图片文件（支持扩展名前后跟中文备注如 .jpg 去1, 留2.jpg）"""
    # 去掉扩展名前的备注（如 留2.jpg → .jpg）
    name = re.sub(r'\s*(?:去|留)\d+(\.(?:jpg|jpeg|png|bmp|gif|tiff|tif|webp|heic|heif))$', r'\1', filename, flags=re.IGNORECASE)
    # 去掉扩展名后的备注部分（如 .jpg 去1 → .jpg）
    name = re.sub(r'\.(jpg|jpeg|png|bmp|gif|tiff|tif|webp|heic|heif)\D.*$', r'.\1', name, flags=re.IGNORECASE)
    return Path(name).suffix.lower() in IMAGE_EXTENSIONS


def parse_prefix_pattern(pattern: str) -> str:
    """
    将用户填写的前缀模式转为正则表达式。
    # 匹配任意单个数字，其他字符原样匹配。

    例: "###-"  →  "\\d{3}-"
        "0##"   →  "0\\d{2}"
        "FDV-"  →  "FDV-"
    """
    regex = ""
    for ch in pattern:
        if ch == '#':
            regex += r'\d'
        else:
            regex += re.escape(ch)
    return regex


def strip_prefix_number(filename: str, prefix_pattern: str) -> str:
    """
    如果文件名开头匹配序号模式，去掉序号及紧跟的分隔符。
    例: ("001-0955.0959.jpg", "###-") → "0955.0959.jpg"
        ("002-0922.0933.jpg", "###-") → "0922.0933.jpg"
    """
    if not prefix_pattern:
        return filename
    regex = parse_prefix_pattern(prefix_pattern)
    # 匹配开头的序号模式
    m = re.match(regex, filename)
    if m:
        # 去掉匹配到的序号部分
        rest = filename[m.end():]
        # 如果后面紧跟分隔符（- . _ 空格），也去掉
        rest = re.sub(r'^[-._\s]+', '', rest)
        return rest
    return filename


def extract_numbers_from_filename(filename: str, min_digits: int = 4,
                                 prefix_filter: str = "") -> list:
    stem = Path(filename).stem

    # 步骤1: 去掉开头的序号前缀（如 001- ）
    stem = strip_prefix_number(stem, prefix_filter)

    # 步骤2: 按非数字字符分割，提取编号
    parts = re.split(r'[^0-9]', stem)
    numbers = [p for p in parts if p.isdigit() and len(p) >= min_digits]
    return numbers


def build_source_index(source_dir: str, min_digits: int = 4,
                       prefix_filter: str = "") -> dict:
    index = defaultdict(list)
    for entry in os.scandir(source_dir):
        if not entry.is_file():
            continue
        if not is_image_file(entry.name):
            continue
        numbers = extract_numbers_from_filename(entry.name, min_digits, prefix_filter)
        for num in numbers:
            index[num].append(entry.path)
    return dict(index)


def extract_numbers_from_directory(dir_path: str, min_digits: int = 4,
                                  prefix_filter: str = "") -> list:
    numbers = []
    for entry in os.scandir(dir_path):
        if not entry.is_file():
            continue
        if not is_image_file(entry.name):
            continue
        nums = extract_numbers_from_filename(entry.name, min_digits, prefix_filter)
        numbers.extend(nums)
    return numbers


def parse_numbers(text: str) -> list:
    for sep in [',', ';', '\t', '|', '，', '；']:
        text = text.replace(sep, ' ')
    return [p.strip() for p in text.split() if p.strip()]


def match_numbers(source_index: dict, target_numbers: list, match_mode: str = "exact") -> dict:
    result = {}
    for num in target_numbers:
        if num in source_index:
            result[num] = {"files": source_index[num], "matched": True}
        else:
            if match_mode == "contains":
                matched_files = []
                for key, files in source_index.items():
                    if num in key:
                        matched_files.extend(files)
                if matched_files:
                    result[num] = {"files": matched_files, "matched": True}
                else:
                    result[num] = {"files": [], "matched": False}
            else:
                result[num] = {"files": [], "matched": False}
    return result


COPY_WORKERS = 4  # 并行复制线程数


# ── RAW 查找功能 ──
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
    # 构建RAW索引
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

        # 1. 精确匹配
        if jpg_stem in raw_dict:
            found_raw = raw_dict[jpg_stem]
            method = '文件名'

        # 2. 候选匹配
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
    # JPG用PIL
    if ext in ('.jpg', '.jpeg', '.png'):
        try:
            from PIL import Image
            with Image.open(filepath) as img:
                exif = img._getexif()
                if exif:
                    for tag_id in (36867, 36868, 306):  # DateTimeOriginal > DateTimeDigitized > DateTime
                        if tag_id in exif:
                            return exif[tag_id]
        except Exception:
            pass
    # RAW文件搜索时间戳
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

    # 构建RAW的EXIF时间索引
    raw_by_time = {}
    for i, raw_path in enumerate(all_raw_files):
        dt = read_exif_datetime(raw_path)
        if dt:
            if dt not in raw_by_time:
                raw_by_time[dt] = []
            raw_by_time[dt].append(raw_path)
        if progress_cb and (i + 1) % 10 == 0:
            progress_cb(i + 1, len(all_raw_files))

    # 追踪已分配的RAW，避免同一 RAW 匹配多张 JPG
    used_raw = set()
    # 匹配
    updated = []
    for result in unmatched_results:
        if result['raw_path']:
            updated.append(result)
            continue
        dt = read_exif_datetime(result['jpg_path'])
        if dt and dt in raw_by_time:
            # 取该时间戳下第一个未被使用的 RAW
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


# ── 匹配目录文件名解析 ──
# 格式: 003-2380.2375.2402.jpg   编号003，图1=2380，图2=2375，图3=2402
#       010-2608.2569.2583 去2    去2=去掉图2(2569)，匹配2608和2583
#       011-2574.2563.2596 留2    留2=只保留图2(2563)，去掉2574和2596
FILTER_OPTIONS = ["无", "去1", "去2", "去3", "留1", "留2", "留3"]


def parse_match_filename(filename: str):
    """
    解析匹配目录文件名，返回 (编号, [图片列表], 原始备注)
    备注（去N/留N）支持多种位置和空格，均无视空格：
      例: "010-2608.2569.2583.jpg 去2" → ("010", ["2608","2569","2583"], "去2")
          "003-2380.2375.2402.jpg"    → ("003", ["2380","2375","2402"], "")
          "002-5364.5362.jpg留2"      → ("002", ["5364","5362"], "留2")
          "001-2596.2646.2599留2.jpg"  → ("001", ["2596","2646","2599"], "留2")
          "001-2596.2646.2599 留2.jpg" → ("001", ["2596","2646","2599"], "留2")
          "001-2596.2646.2599 留2"     → ("001", ["2596","2646","2599"], "留2")
    """
    note = ""
    clean = filename

    # 模式1: 扩展名后有备注（如 "xxx.jpg 去2" 或 "xxx.jpg去2"）
    m = re.search(r'\.(jpg|jpeg|png|bmp|gif|tiff|tif|webp|heic|heif)(\s*)(去\d+|留\d+)\s*$', clean, flags=re.IGNORECASE)
    if m:
        note = m.group(3)
        clean = clean[:m.start()]
    else:
        # 模式2: 扩展名前有备注（如 "xxx留2.jpg" 或 "xxx 留2.jpg"）
        m = re.search(r'^(.*?)(\s*)(去\d+|留\d+)(\s*)\.(jpg|jpeg|png|bmp|gif|tiff|tif|webp|heic|heif)\s*$', clean, flags=re.IGNORECASE)
        if m:
            note = m.group(3)
            clean = m.group(1).strip()
        else:
            # 模式3: 无扩展名，末尾有备注（如 "xxx 留2" 或 "xxx留2"）
            m = re.search(r'^(.*?)(\s*)(去\d+|留\d+)\s*$', clean)
            if m:
                note = m.group(3)
                clean = m.group(1).strip()
            else:
                # 模式4: 无备注，去掉扩展名
                clean = re.sub(r'\.(jpg|jpeg|png|bmp|gif|tiff|tif|webp|heic|heif)\s*$', '', clean, flags=re.IGNORECASE).strip()

    stem = clean
    # 按 - 分割
    parts = stem.split('-', 1)
    if len(parts) != 2:
        return None
    file_num = parts[0].strip()
    # 图片编号按 . 分割
    img_parts = parts[1].split('.')
    images = [p.strip() for p in img_parts if p.strip().isdigit()]
    if not images:
        return None
    return (file_num, images, note)


def apply_filter(images: list, note: str, swap_13: bool = False) -> list:
    """
    应用备注过滤，返回需要匹配的图片列表
    去N = 去掉第N张图（1-based）
    留N = 只保留第N张图
    swap_13 = True 时，先对调第1和第3张图的位置再应用过滤
    """
    # 先对调1&3（如果启用）
    if swap_13 and len(images) >= 3:
        images = list(images)  # 复制一份，避免修改原列表
        images[0], images[2] = images[2], images[0]
    if not note or note == "无":
        return images
    if note.startswith('留'):
        idx = int(note[1:]) - 1  # 1-based → 0-based
        if 0 <= idx < len(images):
            return [images[idx]]
        return images
    elif note.startswith('去'):
        idx = int(note[1:]) - 1
        if 0 <= idx < len(images):
            return [img for i, img in enumerate(images) if i != idx]
        return images
    return images


def safe_copy(src, dst):
    """安全复制：先写临时文件，校验大小后重命名"""
    try:
        temp_dst = dst + ".tmp"
        shutil.copy2(src, temp_dst)
        if os.path.exists(temp_dst) and os.path.getsize(temp_dst) == os.path.getsize(src):
            if os.path.exists(dst):
                os.remove(dst)
            os.rename(temp_dst, dst)
            return True
        else:
            if os.path.exists(temp_dst):
                os.remove(temp_dst)
            return False
    except Exception:
        return False


def safe_move(src, dst):
    """安全移动：跨驱动器时先复制再删除源文件，复制失败不删源"""
    try:
        # 尝试原子重命名（同驱动器）
        os.rename(src, dst)
        return True
    except OSError:
        # 跨驱动器或目标已存在：先复制再删源
        try:
            if safe_copy(src, dst):
                os.remove(src)
                return True
            return False
        except Exception:
            return False


def execute_pick(match_result: dict, output_dir: str, op_mode: str = "copy",
                  dup_mode: str = "rename", progress_cb=None, cancel_event=None):
    """
    线程池并行复制，不卡界面。
    progress_cb(cur, total, name, status) — 仅在进度变化>=1%或完成时调用
    cancel_event: threading.Event，设置时取消剩余任务
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    os.makedirs(output_dir, exist_ok=True)
    not_found = []

    # 收集待复制任务
    tasks = []  # [(src, dst, filename), ...]
    for num, info in match_result.items():
        if not info["matched"]:
            not_found.append(num)
            continue
        for filepath in info["files"]:
            filename = os.path.basename(filepath)
            dst = os.path.join(output_dir, filename)
            if os.path.exists(dst):
                if dup_mode == "skip":
                    continue
                elif dup_mode == "rename":
                    stem = Path(filename).stem
                    ext = Path(filename).suffix
                    # 循环递增直到找到不冲突的文件名，避免覆盖已有文件
                    counter = 1
                    while True:
                        dst = os.path.join(output_dir, f"{stem}_dup{counter}{ext}")
                        if not os.path.exists(dst):
                            break
                        counter += 1
                        if counter > 9999:  # 安全上限，防止极端死循环
                            break
            tasks.append((filepath, dst, filename))

    total = len(tasks)
    if total == 0:
        if progress_cb:
            progress_cb(0, 0, "", "完成")
        return 0, not_found

    found = 0
    skipped = 0
    last_reported_pct = -1

    with ThreadPoolExecutor(max_workers=COPY_WORKERS) as executor:
        future_map = {}
        for src, dst, filename in tasks:
            if cancel_event and cancel_event.is_set():
                break
            if op_mode == "move":
                future = executor.submit(safe_move, src, dst)
            else:
                future = executor.submit(safe_copy, src, dst)
            future_map[future] = (src, dst, filename)

        for future in as_completed(future_map):
            if cancel_event and cancel_event.is_set():
                for f in future_map:
                    f.cancel()
                break

            src, dst, filename = future_map[future]
            try:
                result = future.result()
                if result:
                    found += 1
                    status = "已复制" if op_mode == "copy" else "已移动"
                else:
                    skipped += 1
                    status = "跳过"
            except Exception:
                skipped += 1
                status = "失败"

            current = found + skipped
            pct = (current * 100) // total

            # 进度变化>=1%或完成时才回调，避免淹没UI
            if progress_cb and (pct != last_reported_pct or current == total):
                last_reported_pct = pct
                progress_cb(current, total, filename, status)

    return found, not_found


# ============================================================
# GUI
# ============================================================

class ImagePickerApp:
    # 配色 ── 深色产品界面，无纯黑纯白
    BG = "#1e1e2e"          # 主背景
    SURFACE = "#2a2a3c"     # 卡片/框架表面
    BORDER = "#3a3a4e"      # 边框
    INK = "#e4e4ed"         # 主文字
    ASH = "#9090a8"         # 次要文字
    ACCENT = "#6c8aff"      # 强调色（按钮、高亮）
    ACCENT_HOVER = "#8098ff"
    SUCCESS = "#5acb84"     # 成功
    ERROR = "#ff6b6b"       # 错误
    WARN = "#f0c674"        # 警告

    def __init__(self, root):
        self.root = root
        self.root.title("图片编号挑选工具")
        self.root.geometry("1000x820")
        self.root.minsize(800, 500)  # 最小宽度 800，防止内容挤压
        self.root.resizable(True, True)  # 宽度高度均可自由调整
        self.root.configure(bg=self.BG)

        # macOS 启用 Retina 支持（Tk 8.6+ 自动处理）
        if IS_MACOS:
            try:
                # 确保应用名称正确显示在菜单栏
                root.createcommand('tk::mac::ShowPreferences', lambda: None)
            except Exception:
                pass
            # 设置 macOS 菜单栏语言（防止浏览后菜单变英文）
            self._setup_macos_menu()

    def _setup_macos_menu(self):
        """设置 macOS 原生菜单栏为中文（防止文件浏览后菜单变英文）"""
        if not IS_MACOS:
            return
        try:
            # 创建主菜单栏
            menubar = tk.Menu(self.root)
            self.root.config(menu=menubar)

            # 应用菜单（显示在苹果图标旁边）
            app_menu = tk.Menu(menubar, tearoff=0, name='apple')
            menubar.add_cascade(label='图片编号挑选工具', menu=app_menu)

            # 添加关于项
            app_menu.add_command(label='关于 图片编号挑选工具',
                                command=lambda: messagebox.showinfo(
                                    '关于',
                                    '图片编号挑选工具 v4.0\n\n'
                                    '功能：按编号批量挑选图片\n'
                                    '支持精确匹配、包含匹配\n'
                                    '支持JPG找RAW'))
            app_menu.add_separator()
            app_menu.add_command(label='隐藏', accelerator='⌘H',
                                command=lambda: self.root.withdraw())
            app_menu.add_command(label='退出', accelerator='⌘Q',
                                command=self._on_close)

            # 文件菜单
            file_menu = tk.Menu(menubar, tearoff=0, name='file')
            menubar.add_cascade(label='文件', menu=file_menu)
            file_menu.add_command(label='打开源目录', accelerator='⌘O',
                                command=self._browse_src)
            file_menu.add_separator()
            file_menu.add_command(label='退出', accelerator='⌘Q',
                                command=self._on_close)

            # 编辑菜单（保持中文）
            edit_menu = tk.Menu(menubar, tearoff=0, name='edit')
            menubar.add_cascade(label='编辑', menu=edit_menu)
            edit_menu.add_command(label='撤销', accelerator='⌘Z',
                                command=lambda: self.root.event_generate('<<Undo>>'))
            edit_menu.add_separator()
            edit_menu.add_command(label='剪切', accelerator='⌘X',
                                command=lambda: self.root.event_generate('<<Cut>>'))
            edit_menu.add_command(label='复制', accelerator='⌘C',
                                command=lambda: self.root.event_generate('<<Copy>>'))
            edit_menu.add_command(label='粘贴', accelerator='⌘V',
                                command=lambda: self.root.event_generate('<<Paste>>'))
            edit_menu.add_separator()
            edit_menu.add_command(label='全选', accelerator='⌘A',
                                command=lambda: self.root.event_generate('<<SelectAll>>'))

            # 窗口菜单
            window_menu = tk.Menu(menubar, tearoff=0, name='window')
            menubar.add_cascade(label='窗口', menu=window_menu)
            window_menu.add_command(label='最小化', accelerator='⌘M',
                                  command=lambda: self.root.iconify())
            window_menu.add_command(label='缩放', accelerator='⌘⇧M',
                                  command=lambda: self.root.state('zoomed'))

            # 帮助菜单
            help_menu = tk.Menu(menubar, tearoff=0, name='help')
            menubar.add_cascade(label='帮助', menu=help_menu)
            help_menu.add_command(label='使用说明',
                                command=lambda: messagebox.showinfo(
                                    '使用说明',
                                    '1. 选择源图片目录（原始照片）\n'
                                    '2. 选择挑选目录（工人改好名的拼图）\n'
                                    '3. 点击"提取编号"自动获取编号\n'
                                    '4. 确认匹配结果后点击"执行匹配"\n\n'
                                    '快捷键：\n'
                                    '⌘R - 刷新编号\n'
                                    '⌘O - 打开源目录'))
        except Exception:
            pass  # 如果菜单设置失败，静默跳过

        self.source_index = None
        self.match_result = None
        self.running = False
        self.min_digits = tk.IntVar(value=4)
        self.swap_13_var = tk.BooleanVar(value=False)  # 1&3对调（默认不勾选）
        self.match_mode = None  # 在 _build_ui 中设为 Combobox
        self.op_mode = None     # 在 _build_ui 中设为 Combobox
        self.dup_mode = None    # 在 _build_ui 中设为 Combobox

        # 字体
        self.sys_font = get_system_font()
        self.mono_font = get_monospace_font()

        self._setup_styles()
        self._build_ui()
        self._load_settings()
        self._auto_scan_on_startup()

        # 退出时自动保存当前设置
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

        # F5 快捷键刷新
        self.root.bind('<F5>', lambda e: self._refresh_extract())
        # macOS 上 F5 可能需要 fn 键，额外绑定 ⌘R
        if IS_MACOS:
            self.root.bind('<Command-r>', lambda e: self._refresh_extract())

        # 试用版：显示倒计时条
        if IS_MACOS and IS_TRIAL_BUILD:
            self._add_trial_banner()

    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use('default')

        # 全局
        s.configure('.', background=self.BG, foreground=self.INK,
                    font=(self.sys_font, 10), borderwidth=0)

        # 框架
        s.configure('Card.TFrame', background=self.SURFACE, relief='flat')
        s.configure('TLabelframe', background=self.SURFACE, foreground=self.INK,
                    borderwidth=1, relief='solid', bordercolor=self.BORDER)
        s.configure('TLabelframe.Label', background=self.SURFACE, foreground=self.ASH,
                    font=(self.sys_font, 9, 'bold'), padding=(8, 4))

        # 标签
        s.configure('TLabel', background=self.BG, foreground=self.INK, font=(self.sys_font, 10))
        s.configure('Ash.TLabel', background=self.SURFACE, foreground=self.ASH)
        s.configure('Small.TLabel', background=self.SURFACE, foreground=self.ASH,
                    font=(self.sys_font, 8))
        s.configure('Status.TLabel', background=self.SURFACE, foreground=self.ASH,
                    font=(self.sys_font, 9))
        s.configure('Count.TLabel', background=self.SURFACE, foreground=self.ACCENT,
                    font=(self.sys_font, 9, 'bold'))

        # 按钮
        s.configure('TButton', background=self.SURFACE, foreground=self.INK,
                    font=(self.sys_font, 9), padding=(12, 6), borderwidth=0)
        map_normal = {'background': [('active', self.BORDER), ('!disabled', self.SURFACE)]}
        s.map('TButton', **map_normal)

        # 取消按钮样式 - 红灰色调，悬停明显
        s.configure('Cancel.TButton', background="#4a3a3a", fg="#e4e4ed",
                    font=(self.sys_font, 9), padding=(12, 6), borderwidth=0)
        s.map('Cancel.TButton',
              background=[('active', "#5a4a4a"), ('!disabled', "#4a3a3a"),
                          ('disabled', self.SURFACE)],
              foreground=[('disabled', self.ASH)])

        s.configure('Accent.TButton', background=self.ACCENT, foreground='#ffffff',
                    font=(self.sys_font, 9), padding=(10, 6))
        s.map('Accent.TButton',
              background=[('active', self.ACCENT_HOVER), ('!disabled', self.ACCENT)],
              foreground=[('!disabled', '#ffffff'), ('disabled', self.ASH)])

        s.configure('Ghost.TButton', background=self.BG, foreground=self.ASH,
                    font=(self.sys_font, 9), padding=(10, 6))
        s.map('Ghost.TButton', background=[('active', '#222232')],
              foreground=[('!disabled', self.ASH), ('disabled', '#5a5a70')])

        # 输入框
        s.configure('TEntry', fieldbackground=self.BG, foreground=self.INK,
                    insertcolor=self.INK, borderwidth=1, padding=6)

        # Spinbox
        s.configure('TSpinbox', fieldbackground=self.BG, foreground=self.INK,
                    arrowcolor=self.ASH, borderwidth=1, padding=4)

        # Combobox
        s.configure('TCombobox', fieldbackground=self.BG, foreground=self.INK,
                    borderwidth=1, padding=4)
        s.map('TCombobox',
              fieldbackground=[('readonly', self.BG), ('disabled', self.BG)],
              foreground=[('readonly', self.INK), ('disabled', self.ASH)],
              selectbackground=[('readonly', self.BG)],
              selectforeground=[('readonly', self.INK)])

        # Progressbar
        s.configure('Horizontal.TProgressbar', background=self.ACCENT,
                    troughcolor=self.BG, borderwidth=0, thickness=3)

    def _build_ui(self):
        try:
            self._build_ui_inner()
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise

    def _build_ui_inner(self):
        # 主容器
        main = tk.Frame(self.root, bg=self.BG, padx=16, pady=12)
        main.pack(fill=tk.BOTH, expand=True)

        # ── 顶部标签栏 ──
        tab_bar = tk.Frame(main, bg=self.SURFACE, highlightbackground=self.BORDER, highlightthickness=1)
        tab_bar.pack(fill=tk.X, pady=(0, 8))

        self._tab_find = tk.Label(tab_bar, text="  ◆ 编号挑选  ", bg=self.ACCENT, fg="#ffffff",
                                   font=(self.sys_font, 9, "bold"), padx=12, pady=6)
        self._tab_find.pack(side=tk.LEFT)

        self._tab_raw = tk.Label(tab_bar, text="  ◇ JPG找RAW  ", bg=self.SURFACE, fg=self.ASH,
                                  font=(self.sys_font, 9), padx=12, pady=6, cursor="hand2")
        self._tab_raw.pack(side=tk.LEFT)
        self._tab_raw.bind('<Button-1>', lambda e: self._open_find_raw())
        self._tab_raw.bind('<Enter>', lambda e: self._tab_raw.config(bg='#2a2a4a'))
        self._tab_raw.bind('<Leave>', lambda e: self._tab_raw.config(bg=self.SURFACE))

        # ── 路径区 ──
        path_card = tk.Frame(main, bg=self.SURFACE, highlightbackground=self.BORDER,
                             highlightthickness=1, padx=12, pady=10)
        path_card.pack(fill=tk.X, pady=(0, 8))

        # ── 路径区用 grid 布局，保证列对齐 ──
        path_card.columnconfigure(1, weight=1, uniform="entry_col")  # 输入框列等宽

        # 源目录 (row=0)
        tk.Label(path_card, text="源目录", bg=self.SURFACE, fg=self.ASH,
                 font=(self.sys_font, 9, 'bold'), width=7, anchor='w').grid(row=0, column=0, sticky='w', padx=(0, 6), pady=(0, 6))
        self.src_var = tk.StringVar()
        tk.Entry(path_card, textvariable=self.src_var, font=(self.mono_font, 9),
                 bg=self.BG, fg=self.INK, insertbackground=self.INK,
                 highlightthickness=0, bd=1, relief='solid',
                 highlightbackground=self.BORDER).grid(row=0, column=1, sticky='ew', padx=(0, 6), pady=(0, 6))
        ttk.Button(path_card, text="浏览", style='Ghost.TButton',
                   command=self._browse_src, width=6).grid(row=0, column=2, padx=(0, 4), pady=(0, 6))
        self.scan_btn = ttk.Button(path_card, text="扫描", style='Accent.TButton',
                                    command=self._scan_source, width=8)
        self.scan_btn.grid(row=0, column=3, sticky='w', pady=(0, 6))
        self.src_status = tk.Label(path_card, text="未扫描", bg=self.SURFACE,
                                    fg=self.ASH, font=(self.sys_font, 9), anchor='w')
        self.src_status.grid(row=1, column=0, columnspan=4, sticky='ew', pady=(0, 4))

        # 匹配目录 (row=2)
        tk.Label(path_card, text="匹配目录", bg=self.SURFACE, fg=self.ASH,
                 font=(self.sys_font, 9, 'bold'), width=7, anchor='w').grid(row=2, column=0, sticky='w', padx=(0, 6), pady=(0, 6))
        self.pick_var = tk.StringVar()
        tk.Entry(path_card, textvariable=self.pick_var, font=(self.mono_font, 9),
                 bg=self.BG, fg=self.INK, insertbackground=self.INK,
                 highlightthickness=0, bd=1, relief='solid',
                 highlightbackground=self.BORDER).grid(row=2, column=1, sticky='ew', padx=(0, 6), pady=(0, 6))
        ttk.Button(path_card, text="浏览", style='Ghost.TButton',
                   command=self._browse_pick, width=6).grid(row=2, column=2, padx=(0, 4), pady=(0, 6))
        ttk.Button(path_card, text="提取编号", style='Accent.TButton',
                   command=self._extract_from_dir, width=8).grid(row=2, column=3, sticky='w', pady=(0, 6))

        # 输出目录 (row=3)
        tk.Label(path_card, text="输出目录", bg=self.SURFACE, fg=self.ASH,
                 font=(self.sys_font, 9, 'bold'), width=7, anchor='w').grid(row=3, column=0, sticky='ns', padx=(0, 6), pady=(0, 6))
        self.dst_var = tk.StringVar()
        self.dst_entry = tk.Entry(path_card, textvariable=self.dst_var, font=(self.mono_font, 9),
                 bg=self.BG, fg=self.INK, insertbackground=self.INK,
                 highlightthickness=0, bd=1, relief='solid',
                 highlightbackground=self.BORDER)
        self.dst_entry.grid(row=3, column=1, sticky='ew', padx=(0, 6), pady=(0, 6))
        ttk.Button(path_card, text="浏览", style='Ghost.TButton',
                   command=self._browse_dst, width=6).grid(row=3, column=2, sticky='w', pady=(0, 6))

        # 输出目录占位提示（灰色小字）
        self._dst_placeholder = "不填写默认输出到匹配目录下输出文件夹"
        self.dst_entry.insert(0, self._dst_placeholder)
        self.dst_entry.config(fg='#5a5a70')
        self.dst_entry.bind('<FocusIn>', self._on_dst_focus_in)
        self.dst_entry.bind('<FocusOut>', self._on_dst_focus_out)

        # ── 主体：三列等宽布局 ──
        body = tk.Frame(main, bg=self.BG)
        body.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(1, weight=1, uniform="col")
        body.columnconfigure(2, weight=1, uniform="col")
        body.rowconfigure(0, weight=1)

        # ══ 第1列：预览 ══
        preview_card = tk.Frame(body, bg=self.SURFACE, highlightbackground=self.BORDER,
                                highlightthickness=1)
        preview_card.grid(row=0, column=0, sticky='nsew', padx=(0, 4))
        preview_card.rowconfigure(1, weight=1)
        preview_card.columnconfigure(0, weight=1)

        ph = tk.Frame(preview_card, bg=self.SURFACE, padx=6, pady=3)
        ph.grid(row=0, column=0, sticky='ew')
        ph.columnconfigure(0, weight=1)
        tk.Label(ph, text="预览", bg=self.SURFACE, fg=self.ASH,
                 font=(self.sys_font, 9, 'bold')).grid(row=0, column=0, sticky='w')
        self.preview_status = tk.Label(ph, text="扫描源目录后自动显示",
                                       bg=self.SURFACE, fg=self.ASH, font=(self.sys_font, 8))
        self.preview_status.grid(row=0, column=1, sticky='e')
        self.preview_text = scrolledtext.ScrolledText(
            preview_card, font=(self.mono_font, 9), wrap=tk.WORD, state=tk.DISABLED,
            bg='#16161f', fg='#b0b0c0', insertbackground=self.INK,
            highlightthickness=0, bd=0, padx=6, pady=3
        )
        self.preview_text.grid(row=1, column=0, sticky='nsew', padx=3, pady=(0, 3))
        self.preview_text.bind('<Double-1>', self._on_preview_double_click)
        self.preview_text.bind('<Button-1>', self._on_preview_click)
        self.preview_text.tag_config('highlight', background='#2a2a4a')
        self._preview_file_paths = {}  # 行号 → 文件路径列表
        self._preview_job = None

        # ══ 第2列：参数 + 匹配目录文件 ══
        mid_col = tk.Frame(body, bg=self.BG)
        mid_col.grid(row=0, column=1, sticky='nsew', padx=4)
        mid_col.rowconfigure(1, weight=1)
        mid_col.columnconfigure(0, weight=1)

        # 参数（紧凑横向）
        param_card = tk.Frame(mid_col, bg=self.SURFACE, highlightbackground=self.BORDER,
                              highlightthickness=1, padx=6, pady=4)
        param_card.grid(row=0, column=0, sticky='ew', pady=(0, 4))

        tk.Label(param_card, text="位数", bg=self.SURFACE, fg=self.ASH,
                 font=(self.sys_font, 8)).grid(row=0, column=0, padx=(0, 2))
        ttk.Spinbox(param_card, from_=1, to=8, increment=1, width=3,
                    textvariable=self.min_digits,
                    font=(self.sys_font, 8)).grid(row=0, column=1, padx=(0, 6))
        tk.Label(param_card, text="匹配", bg=self.SURFACE, fg=self.ASH,
                 font=(self.sys_font, 8)).grid(row=0, column=2, padx=(0, 2))
        cb_match = ttk.Combobox(param_card, values=["精确", "包含"],
                     width=5, state='readonly', font=(self.sys_font, 8))
        cb_match.grid(row=0, column=3, padx=(0, 6))
        cb_match.set("精确")
        self.match_mode = cb_match
        tk.Label(param_card, text="操作", bg=self.SURFACE, fg=self.ASH,
                 font=(self.sys_font, 8)).grid(row=0, column=4, padx=(0, 2))
        cb_op = ttk.Combobox(param_card, values=["复制", "移动"],
                     width=5, state='readonly', font=(self.sys_font, 8))
        cb_op.grid(row=0, column=5, padx=(0, 6))
        cb_op.set("复制")
        self.op_mode = cb_op
        tk.Label(param_card, text="重名", bg=self.SURFACE, fg=self.ASH,
                 font=(self.sys_font, 8)).grid(row=0, column=6, padx=(0, 2))
        cb_dup = ttk.Combobox(param_card, values=["重命名", "覆盖", "跳过"],
                     width=5, state='readonly', font=(self.sys_font, 8))
        cb_dup.grid(row=0, column=7)
        cb_dup.set("重命名")
        self.dup_mode = cb_dup

        tk.Label(param_card, text="开头序号过滤", bg=self.SURFACE, fg=self.ASH,
                 font=(self.sys_font, 8)).grid(row=1, column=0, padx=(0, 2), pady=(4, 0))
        self.prefix_var = tk.StringVar()
        self.prefix_entry = tk.Entry(param_card, textvariable=self.prefix_var, font=(self.mono_font, 8),
                 bg=self.BG, fg=self.INK, insertbackground=self.INK,
                 highlightthickness=0, bd=1, relief='solid',
                 highlightbackground=self.BORDER, width=5)
        self.prefix_entry.grid(row=1, column=1, columnspan=3, sticky='ew', pady=(4, 0))

        self._prefix_hint = "# #=数字  例: ###- 0## FDV-"
        self._set_prefix_placeholder()
        self.prefix_entry.bind('<FocusIn>', lambda e: self._clear_prefix_placeholder())
        self.prefix_entry.bind('<FocusOut>', lambda e: self._set_prefix_placeholder() if not self.prefix_entry.get().strip() else None)

        # 1&3对调勾选框（放在开头序号过滤同行右侧）
        self.swap_13_cb = tk.Checkbutton(param_card, text="1&3对调", variable=self.swap_13_var,
                 bg=self.SURFACE, fg=self.ASH, activebackground=self.SURFACE,
                 activeforeground=self.INK, selectcolor=self.BG,
                 font=(self.sys_font, 8), command=self._on_swap_changed)
        self.swap_13_cb.grid(row=1, column=4, columnspan=2, sticky='w', pady=(4, 0), padx=(2, 0))

        # 匹配目录文件列表
        self.file_card = tk.Frame(mid_col, bg=self.SURFACE, highlightbackground=self.BORDER,
                             highlightthickness=1)
        self.file_card.grid(row=1, column=0, sticky='nsew')
        file_card = self.file_card
        file_card.rowconfigure(1, weight=1)
        file_card.columnconfigure(0, weight=1)

        self.file_canvas = tk.Canvas(file_card, bg='#1a1a24', highlightthickness=0, bd=0)
        self.file_canvas.grid(row=1, column=0, sticky='nsew', pady=(0, 3))
        file_vscroll = ttk.Scrollbar(file_card, orient=tk.VERTICAL,
                                     command=self.file_canvas.yview)
        file_vscroll.grid(row=1, column=1, sticky='ns', pady=(0, 3))
        self.file_canvas.configure(yscrollcommand=file_vscroll.set)

        self.file_inner = tk.Frame(self.file_canvas, bg='#1a1a24')
        self._file_canvas_window = self.file_canvas.create_window((0, 0), window=self.file_inner, anchor='nw')
        self.file_inner.bind('<Configure>',
                             lambda e: self.file_canvas.configure(
                                 scrollregion=self.file_canvas.bbox('all')))
        # 让canvas窗口随canvas宽度延迟绑定（等_build_ui完成后再绑）
        self.root.after(100, lambda: self.file_canvas.bind('<Configure>', self._on_file_canvas_configure))

        # 右键菜单
        # 右键菜单动态生成（根据选中文件的图片数量）

        self._file_rows = []
        self._selected_files = set()

        # ══ 第3列：编号列表 ══
        num_card = tk.Frame(body, bg=self.SURFACE, highlightbackground=self.BORDER,
                            highlightthickness=1)
        num_card.grid(row=0, column=2, sticky='nsew', padx=(4, 0))
        num_card.rowconfigure(1, weight=1)
        num_card.columnconfigure(0, weight=1)

        nh = tk.Frame(num_card, bg=self.SURFACE, padx=6, pady=3)
        nh.grid(row=0, column=0, sticky='ew')
        nh.columnconfigure(0, weight=1)
        tk.Label(nh, text="编号列表", bg=self.SURFACE, fg=self.INK,
                 font=(self.sys_font, 9, 'bold')).grid(row=0, column=0, sticky='w')
        self.count_label = tk.Label(nh, text="0 个", bg=self.SURFACE,
                                     fg=self.ACCENT, font=(self.sys_font, 9, 'bold'))
        self.count_label.grid(row=0, column=1, sticky='e')

        self.num_text = scrolledtext.ScrolledText(
            num_card, font=(self.mono_font, 9), wrap=tk.WORD,
            bg=self.BG, fg=self.INK, insertbackground=self.INK,
            selectbackground=self.ACCENT, selectforeground='#ffffff',
            highlightthickness=0, bd=1, relief='solid',
            highlightbackground=self.BORDER, padx=6, pady=3
        )
        self.num_text.grid(row=1, column=0, sticky='nsew', padx=3, pady=(0, 4))
        self.num_text.bind('<KeyRelease>', self._on_numbers_changed)
        self.num_text.bind('<ButtonRelease>', self._on_numbers_changed)

        num_btns = tk.Frame(num_card, bg=self.SURFACE)
        num_btns.grid(row=2, column=0, sticky='ew', padx=3, pady=(0, 3))
        ttk.Button(num_btns, text="粘贴", style='Ghost.TButton',
                   command=self._paste_clipboard).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(num_btns, text="文件导入", style='Ghost.TButton',
                   command=self._import_from_file).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(num_btns, text="清空", style='Ghost.TButton',
                   command=self._clear_numbers).pack(side=tk.LEFT, padx=(0, 3))
        refresh_label = "⌘R" if IS_MACOS else "F5"
        ttk.Button(num_btns, text=f"刷新{refresh_label}", style='Ghost.TButton',
                   command=self._refresh_extract).pack(side=tk.LEFT)

        # ── 执行按钮 ──
        action_row = tk.Frame(main, bg=self.BG)
        action_row.pack(fill=tk.X, pady=(0, 8))

        self.run_btn = ttk.Button(action_row, text="执行匹配", style='Accent.TButton',
                                   command=self._start_picking)
        self.run_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.cancel_btn = ttk.Button(action_row, text="取消", style='TButton',
                                      command=self._cancel_pick, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ── 进度条 ──
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(main, variable=self.progress_var,
                                             maximum=100, style='Horizontal.TProgressbar')
        self.progress_bar.pack(fill=tk.X)

        # ── 日志 ──
        log_card = tk.Frame(main, bg=self.SURFACE, highlightbackground=self.BORDER,
                            highlightthickness=1)
        log_card.pack(fill=tk.BOTH, expand=True)

        log_header = tk.Frame(log_card, bg=self.SURFACE, padx=8, pady=2)
        log_header.pack(fill=tk.X)
        tk.Label(log_header, text="日志", bg=self.SURFACE, fg=self.ASH,
                 font=(self.sys_font, 9, 'bold')).pack(side=tk.LEFT)
        ttk.Button(log_header, text="清空", style='Ghost.TButton',
                   command=self._clear_log).pack(side=tk.RIGHT)

        self.log_text = scrolledtext.ScrolledText(
            log_card, font=(self.mono_font, 9), height=4, state=tk.DISABLED,
            wrap=tk.WORD, bg='#16161f', fg='#b0b0c0', insertbackground=self.INK,
            highlightthickness=0, bd=0, padx=8, pady=2
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _update_file_inner_width(self):
        """更新file_inner宽度匹配canvas"""
        try:
            w = self.file_card.winfo_width()
            if w > 1:
                self.file_canvas.itemconfig(self._file_canvas_window, width=w - 25)
                self.file_inner.config(width=w - 25)
            else:
                self.root.after(100, self._update_file_inner_width)
        except Exception:
            pass

    def _on_file_canvas_configure(self, event):
        """canvas宽度变化时，拉伸内部窗口和frame"""
        self.file_canvas.itemconfig(self._file_canvas_window, width=event.width)
        self.file_inner.config(width=event.width)

    # ── 日志 ──
    def _log(self, msg):
        if not hasattr(self, 'log_text'):
            return  # UI未就绪，跳过
        self.log_text.config(state=tk.NORMAL)
        ts = datetime.now().strftime('%H:%M:%S')
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _insert_clickable_path(self, path):
        """在日志中插入可点击的路径（蓝色下划线，点击打开目录）"""
        self.log_text.config(state=tk.NORMAL)
        # 配置超链接样式
        self.log_text.tag_config('link', foreground='#6c8aff', underline=True)
        self.log_text.tag_bind('link', '<Button-1>',
                               lambda e, p=path: open_file_or_folder(p) if os.path.isdir(p) else None)
        self.log_text.tag_bind('link', '<Enter>',
                               lambda e: self.log_text.config(cursor='hand2'))
        self.log_text.tag_bind('link', '<Leave>',
                               lambda e: self.log_text.config(cursor=''))
        self.log_text.insert(tk.END, path, 'link')
        self.log_text.config(state=tk.DISABLED)

    def _clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete('1.0', tk.END)
        self.log_text.config(state=tk.DISABLED)

    # ── 路径浏览 ──
    def _browse_src(self):
        d = filedialog.askdirectory(title="选择源图片目录")
        if d:
            self.src_var.set(normalize_path(d))
            self._scan_source()

    def _browse_dst(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.dst_var.set(normalize_path(d))
            self.dst_entry.config(fg=self.INK)

    def _browse_pick(self):
        d = filedialog.askdirectory(title="选择挑选目录")
        if d:
            self.pick_var.set(normalize_path(d))
            self._extract_from_dir()

    # ── 扫描源目录（后台线程，不卡界面）──
    def _scan_source(self):
        src = self.src_var.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror("错误", "请先选择有效的源图片目录")
            return
        self._log(f"扫描: {src}")
        self.src_status.config(text="扫描中...", fg=self.WARN)
        self.scan_btn.config(state=tk.DISABLED)

        try:
            min_d = int(self.min_digits.get())
        except (ValueError, tk.TclError):
            min_d = 4
        self._log(f"扫描参数: 位数={min_d}")
        prefix = self.prefix_var.get().strip()

        def _do_scan():
            index = build_source_index(src, min_d, prefix)
            self.root.after(0, lambda: self._on_scan_done(index, prefix))

        threading.Thread(target=_do_scan, daemon=True).start()

    def _on_scan_done(self, index, prefix):
        self.source_index = index
        total = sum(len(v) for v in self.source_index.values())
        unique = len(self.source_index)
        prefix_info = f" | 前缀: {prefix}" if prefix else ""
        self.src_status.config(
            text=f"{unique} 个编号 / {total} 条记录",
            fg=self.SUCCESS
        )
        self._log(f"完成: {unique} 个编号, {total} 条记录{prefix_info}")
        self.scan_btn.config(state=tk.NORMAL)
        self._save_settings()
        self._refresh_preview()

    # ── 从挑选目录提取编号（后台线程）──
    def _extract_from_dir(self):
        pick_dir = self.pick_var.get().strip()
        if not pick_dir or not os.path.isdir(pick_dir):
            messagebox.showerror("错误", "请先选择有效的挑选目录")
            return
        self._run_extract(pick_dir)

    def _refresh_extract(self):
        """用当前设置重新提取编号"""
        pick_dir = self.pick_var.get().strip()
        if not pick_dir or not os.path.isdir(pick_dir):
            messagebox.showwarning("提示", "请先选择挑选目录")
            return
        self._log(f"刷新: 位数={self.min_digits.get()}, 前缀=\"{self.prefix_var.get().strip()}\"")
        self._run_extract(pick_dir)

    def _run_extract(self, pick_dir):
        try:
            min_d = int(self.min_digits.get())
        except (ValueError, tk.TclError):
            min_d = 4
        prefix = self.prefix_var.get().strip()

        def _do_extract():
            numbers = extract_numbers_from_directory(pick_dir, min_d, prefix)
            # 同时解析匹配目录文件列表
            file_entries = []
            for entry in os.scandir(pick_dir):
                if not entry.is_file() or not is_image_file(entry.name):
                    continue
                parsed = parse_match_filename(entry.name)
                if parsed:
                    file_entries.append((entry.name, parsed))
            self.root.after(0, lambda: self._on_extract_done(numbers, file_entries))

        threading.Thread(target=_do_extract, daemon=True).start()

    def _on_extract_done(self, numbers, file_entries=None):
        seen = set()
        unique = [n for n in numbers if not (n in seen or seen.add(n))]
        self.num_text.delete('1.0', tk.END)
        self.num_text.insert('1.0', '\n'.join(unique))
        self._on_numbers_changed()
        self._log(f"提取到 {len(unique)} 个编号: {', '.join(unique[:10])}{'...' if len(unique) > 10 else ''}")

        # 更新匹配目录文件列表表格
        if file_entries is not None:
            self._rebuild_file_table(file_entries)

    def _rebuild_file_table(self, file_entries):
        """重建匹配目录文件列表（文件名 + 过滤标签 + 重置按钮）"""
        # 彻底清理：删除canvas内所有窗口，重建file_inner
        self.file_canvas.delete('all')
        self.file_inner = tk.Frame(self.file_canvas, bg='#1a1a24')
        self._file_canvas_window = self.file_canvas.create_window((0, 0), window=self.file_inner, anchor='nw')
        self.file_inner.bind('<Configure>',
                             lambda e: self.file_canvas.configure(
                                 scrollregion=self.file_canvas.bbox('all')))
        # 设置初始宽度
        self.root.after(50, self._update_file_inner_width)

        # 重建表头
        hdr = tk.Frame(self.file_inner, bg='#252535', highlightbackground='#3a3a4e', highlightthickness=1)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="  匹配目录文件", bg='#252535', fg=self.ASH,
                 font=(self.sys_font, 8, 'bold'), anchor='w', padx=4, pady=3).pack(side=tk.LEFT)
        self.file_status = tk.Label(hdr, text="", bg='#252535', fg=self.ASH,
                                     font=(self.sys_font, 8, 'bold'), anchor='e', padx=4, pady=3)
        self.file_status.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(hdr, text="↺", bg='#252535', fg=self.ASH,
                 font=(self.sys_font, 8, 'bold'), width=2, anchor='e', padx=4, pady=3).pack(side=tk.RIGHT)

        self._file_rows.clear()
        self._selected_files.clear()

        if not file_entries:
            self.file_status.config(text="无有效文件", fg=self.ASH)
            return

        for idx, (filename, (file_num, images, orig_note)) in enumerate(file_entries):
            clean_name = self._get_clean_filename(filename)
            note = orig_note if orig_note else ""
            row_bg = '#1a1a24' if idx % 2 == 0 else '#1e1e2a'

            row_frame = tk.Frame(self.file_inner, bg=row_bg)
            row_frame.pack(fill=tk.X)

            # 第1列：文件名（左对齐，占满）
            name_label = tk.Label(row_frame, text=clean_name, bg=row_bg, fg='#c0c0d0',
                                   font=(self.mono_font, 9), anchor='w', padx=4)
            name_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
            name_label.bind('<Button-1>', lambda e, i=idx: self._multi_select(e, i))
            name_label.bind('<Double-1>', lambda e, i=idx: self._open_file_image(i))
            name_label.bind('<Button-3>', lambda e, i=idx: self._show_context_menu(e, i))
            # macOS 右键绑定（Button-2 或 Control+Button-1）
            if IS_MACOS:
                name_label.bind('<Button-2>', lambda e, i=idx: self._show_context_menu(e, i))
                name_label.bind('<Control-1>', lambda e, i=idx: self._show_context_menu(e, i))

            # 第2列：过滤标签（左对齐）
            filter_text = note if note else ""
            filter_color = '#5acb84' if note.startswith('留') else ('#ff6b6b' if note.startswith('去') else '#5a5a70')
            filter_label = tk.Label(row_frame, text=filter_text, bg=row_bg, fg=filter_color,
                                     font=(self.mono_font, 9, 'bold'), anchor='w', width=4)
            filter_label.pack(side=tk.LEFT)
            filter_label.bind('<Button-1>', lambda e, i=idx: self._multi_select(e, i))
            filter_label.bind('<Button-3>', lambda e, i=idx: self._show_context_menu(e, i))
            if IS_MACOS:
                filter_label.bind('<Button-2>', lambda e, i=idx: self._show_context_menu(e, i))
                filter_label.bind('<Control-1>', lambda e, i=idx: self._show_context_menu(e, i))

            # 第3列：重置图标（右对齐，紧贴滚动条）
            reset_label = tk.Label(row_frame, text="↺", bg=row_bg, fg='#5a5a70',
                                    font=(self.sys_font, 9), width=2)
            reset_label.pack(side=tk.RIGHT)
            reset_label.bind('<Button-1>',
                             lambda e, fn=filename, orig=orig_note: self._reset_filter(fn, orig))
            reset_label.bind('<Enter>', lambda e, r=reset_label: r.config(fg=self.ACCENT))
            reset_label.bind('<Leave>', lambda e, r=reset_label: r.config(fg='#5a5a70'))
            reset_label.bind('<Button-3>', lambda e, i=idx: self._show_context_menu(e, i))
            if IS_MACOS:
                reset_label.bind('<Button-2>', lambda e, i=idx: self._show_context_menu(e, i))
                reset_label.bind('<Control-1>', lambda e, i=idx: self._show_context_menu(e, i))

            self._file_rows.append({
                'filename': filename,
                'clean_name': clean_name,
                'file_num': file_num,
                'images': images,
                'orig_note': orig_note,
                'current_note': note,
                'filter_label': filter_label,
                'row_frame': row_frame,
                'name_label': name_label,
                'row_bg': row_bg,
            })

        total_imgs = sum(len(r['images']) for r in self._file_rows)
        self.file_status.config(
            text=f"{len(file_entries)} 个文件 / {total_imgs} 张图",
            fg=self.SUCCESS
        )

    def _get_clean_filename(self, filename):
        """去掉文件名中的去/留备注，返回纯文件名（保留扩展名）"""
        # 去掉扩展名后的备注（如 .jpg 去1 → .jpg, .jpg去1 → .jpg）
        clean = re.sub(r'(\.(?:jpg|jpeg|png|bmp|gif|tiff|tif|webp|heic|heif))\s*(?:去|留)\d+\s*$', r'\1', filename, flags=re.IGNORECASE)
        # 去掉扩展名前的备注（如 留2.jpg → .jpg, 留2 → 空）
        clean = re.sub(r'\s*(?:去|留)\d+(\.(?:jpg|jpeg|png|bmp|gif|tiff|tif|webp|heic|heif))?$', lambda m: m.group(1) or '', clean, flags=re.IGNORECASE)
        return clean if clean else filename

    def _multi_select(self, event, idx):
        """多选：⌘/Ctrl+点击切换，Shift+点击范围，普通点击单选"""
        if is_modifier_pressed(event):  # ⌘ (macOS) 或 Ctrl (Windows)
            if idx in self._selected_files:
                self._selected_files.discard(idx)
                self._restore_bg(idx)
            else:
                self._selected_files.add(idx)
                self._highlight_bg(idx)
        elif event.state & 0x0001:  # Shift
            if self._selected_files:
                start = min(self._selected_files)
                end = max(self._selected_files)
                if idx < start:
                    range_start, range_end = idx, end
                elif idx > end:
                    range_start, range_end = start, idx
                else:
                    range_start, range_end = min(start, idx), max(start, idx)
                self._clear_selection()
                for i in range(range_start, range_end + 1):
                    self._selected_files.add(i)
                    self._highlight_bg(i)
            else:
                self._selected_files.add(idx)
                self._highlight_bg(idx)
        else:  # 普通点击 = 单选
            self._clear_selection()
            self._selected_files.add(idx)
            self._highlight_bg(idx)

    def _highlight_bg(self, idx):
        row = self._file_rows[idx]
        row['row_frame'].config(bg='#2a2a4a')
        row['name_label'].config(bg='#2a2a4a')
        row['filter_label'].config(bg='#2a2a4a')

    def _restore_bg(self, idx):
        row = self._file_rows[idx]
        bg = row['row_bg']
        row['row_frame'].config(bg=bg)
        row['name_label'].config(bg=bg)
        row['filter_label'].config(bg=bg)

    def _open_find_raw(self):
        """打开JPG找RAW窗口，自动填入输出目录"""
        # 获取当前输出目录
        dst = self.dst_var.get().strip()
        if dst == self._dst_placeholder:
            dst = ''
        FindRawDialog(self.root, jpg_folder=dst)

    def _open_file_image(self, idx):
        """双击打开匹配目录里的合成图片"""
        import tempfile
        row = self._file_rows[idx]
        filename = row['filename']
        pick_dir = self.pick_var.get().strip()
        if not pick_dir:
            return
        # 原始文件路径
        src_path = os.path.join(pick_dir, filename)
        if not os.path.exists(src_path):
            return
        # 去掉备注，创建临时副本让系统正确识别为图片
        clean_name = self._get_clean_filename(filename)
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, clean_name)
        try:
            import shutil
            shutil.copy2(src_path, temp_path)
            open_file_or_folder(temp_path)
        except Exception:
            # 失败则尝试直接打开原始文件
            open_file_or_folder(src_path)

    def _show_context_menu(self, event, idx):
        """右键弹出过滤菜单（根据选中文件动态生成选项）"""
        # 如果点击的行未选中，清除其他选中只选当前
        if idx not in self._selected_files:
            self._clear_selection()
            self._multi_select(event, idx)

        # 获取选中文件的最大图片数量
        max_imgs = 0
        for i in self._selected_files:
            if i < len(self._file_rows):
                img_count = len(self._file_rows[i]['images'])
                if img_count > max_imgs:
                    max_imgs = img_count

        # 动态生成菜单
        menu = tk.Menu(self.root, tearoff=0, bg='#2a2a3c', fg='#c0c0d0',
                       font=(self.sys_font, 9))
        if max_imgs == 0:
            menu.add_command(label="无可用操作", command=lambda: None)
        elif max_imgs == 1:
            menu.add_command(label="清除过滤", command=lambda: self._batch_filter("无"))
        else:
            # 去1, 去2, ... 去N
            for n in range(1, max_imgs + 1):
                menu.add_command(label=f"去{n}", command=lambda x=n: self._batch_filter(f"去{x}"))
            menu.add_separator()
            # 留1, 留2, ... 留N
            for n in range(1, max_imgs + 1):
                menu.add_command(label=f"留{n}", command=lambda x=n: self._batch_filter(f"留{x}"))
            menu.add_separator()
            menu.add_command(label="清除过滤", command=lambda: self._batch_filter("无"))

        menu.post(event.x_root, event.y_root)

    def _clear_selection(self):
        """清除所有选中"""
        for idx in list(self._selected_files):
            self._selected_files.discard(idx)
            if idx < len(self._file_rows):
                self._restore_bg(idx)

    def _on_swap_changed(self):
        """1&3对调勾选变化时刷新预览"""
        swap = self.swap_13_var.get()
        self._log(f"1&3对调: {'开启' if swap else '关闭'}")
        self._refresh_preview()

    def _batch_filter(self, note):
        """批量设置过滤条件"""
        if not self._selected_files:
            return
        note_display = note if note != "无" else ""
        for idx in self._selected_files:
            row = self._file_rows[idx]
            old = row['current_note'] if row['current_note'] else "无"
            row['current_note'] = note_display
            if note_display:
                color = '#5acb84' if note_display.startswith('留') else '#ff6b6b'
                row['filter_label'].config(text=note_display, fg=color)
            else:
                row['filter_label'].config(text="", fg='#5a5a70')

            if old != note:
                self._log(f"修改过滤: {row['clean_name']}  {old} → {note}")
        self._clear_selection()
        self._refresh_preview()

    def _reset_filter(self, filename, orig_note):
        """重置单个文件过滤到原始值"""
        for idx, row in enumerate(self._file_rows):
            if row['filename'] == filename:
                orig = orig_note if orig_note else ""
                row['current_note'] = orig
                if orig:
                    color = '#5acb84' if orig.startswith('留') else '#ff6b6b'
                    row['filter_label'].config(text=orig, fg=color)
                else:
                    row['filter_label'].config(text="", fg='#5a5a70')

                self._log(f"重置过滤: {row['clean_name']} → {orig if orig else '无'}")
                self._refresh_preview()
                break

    # ── 编号列表 ──
    def _get_active_numbers(self) -> list:
        """获取经过过滤后的有效编号列表（去重），应用1&3对调设置"""
        if not hasattr(self, '_file_rows'):
            return []
        swap_13 = self.swap_13_var.get() if hasattr(self, 'swap_13_var') else False
        numbers = []
        for row in self._file_rows:
            note = row.get('current_note', '')
            filtered = apply_filter(row['images'], note, swap_13=swap_13)
            numbers.extend(filtered)
        # 去重保序
        seen = set()
        unique = [n for n in numbers if not (n in seen or seen.add(n))]
        return unique

    def _on_numbers_changed(self, event=None):
        """编号变化时更新计数 + 刷新预览（以文本框内容为准）"""
        numbers = parse_numbers(self.num_text.get('1.0', tk.END))
        self.count_label.config(text=f"{len(numbers)} 个")
        self._schedule_preview()

    def _update_count(self, event=None):
        """从文本框解析编号并更新计数（保留方法以兼容）"""
        numbers = parse_numbers(self.num_text.get('1.0', tk.END))
        self.count_label.config(text=f"{len(numbers)} 个")

    def _paste_clipboard(self):
        try:
            self.num_text.insert(tk.INSERT, self.root.clipboard_get())
            self._on_numbers_changed()
        except tk.TclError:
            pass

    def _clear_numbers(self):
        self.num_text.delete('1.0', tk.END)
        self._on_numbers_changed()

    def _import_from_file(self):
        fp = filedialog.askopenfilename(title="导入编号列表",
                                         filetypes=[("文本", "*.txt"), ("CSV", "*.csv")])
        if fp:
            with open(fp, 'r', encoding='utf-8') as f:
                self.num_text.insert(tk.END, f.read())
            self._on_numbers_changed()
            self._log(f"已导入: {fp}")

    # ── 内嵌预览 ──
    def _schedule_preview(self):
        """编号变化时延迟刷新预览（防抖）"""
        if self._preview_job:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(300, self._refresh_preview)

    def _refresh_preview(self):
        """刷新内嵌预览区，显示匹配结果列表"""
        if not hasattr(self, 'preview_text'):
            return
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete('1.0', tk.END)
        self._preview_file_paths.clear()  # 清空路径缓存

        if not self.source_index:
            self.preview_status.config(text="扫描源目录后自动显示匹配结果", fg=self.ASH)
            self.preview_text.config(state=tk.DISABLED)
            return

        # 优先使用文件表中的过滤后编号，否则用文本框中的手动输入
        if self._file_rows:
            numbers = self._get_active_numbers()
        else:
            numbers = parse_numbers(self.num_text.get('1.0', tk.END))
        if not numbers:
            self.preview_status.config(text="输入编号后自动显示匹配结果", fg=self.ASH)
            self.preview_text.config(state=tk.DISABLED)
            return

        mode = _resolve_mode(self.match_mode.get(), MATCH_MODE_MAP)
        result = match_numbers(self.source_index, numbers, mode)

        matched = sum(1 for v in result.values() if v["matched"])
        unmatched = sum(1 for v in result.values() if not v["matched"])
        self.preview_status.config(
            text=f"匹配 {matched}/{len(numbers)}" +
                 (f"  ·  {unmatched} 个未找到" if unmatched else "  ·  全部匹配 ✓"),
            fg=self.SUCCESS if not unmatched else self.WARN
        )

        line_num = 1
        for num, info in result.items():
            if info["matched"]:
                files = ', '.join(Path(f).name for f in info["files"])
                self.preview_text.insert(tk.END, f"✓ {num} → {files}\n")
                # 存储该行对应的文件路径（用于双击打开）
                self._preview_file_paths[line_num] = info["files"]
            else:
                self.preview_text.insert(tk.END, f"✗ {num} → 未找到\n")
            line_num += 1

        self.preview_text.config(state=tk.DISABLED)

    def _on_preview_double_click(self, event):
        """双击预览行打开对应的图片"""
        try:
            # 获取点击位置的行号
            index = self.preview_text.index(f"@{event.x},{event.y}")
            line_num = int(index.split('.')[0])
            # 查找该行的文件路径
            files = self._preview_file_paths.get(line_num, [])
            if files:
                # 打开第一张图片
                open_file_or_folder(files[0])
        except Exception:
            pass

    def _on_preview_click(self, event):
        """单击预览行高亮显示"""
        try:
            # 清除之前的高亮
            self.preview_text.tag_remove('highlight', '1.0', tk.END)
            # 获取点击位置的行号
            index = self.preview_text.index(f"@{event.x},{event.y}")
            line_num = int(index.split('.')[0])
            # 高亮当前行
            line_start = f"{line_num}.0"
            line_end = f"{line_num + 1}.0"
            self.preview_text.tag_add('highlight', line_start, line_end)
        except Exception:
            pass

    # ── 默认输出目录 ──
    def _get_default_output_dir(self):
        """获取默认输出目录：在挑选目录下创建'输出'文件夹"""
        pick_dir = self.pick_var.get().strip()
        if not pick_dir or not os.path.isdir(pick_dir):
            return None
        return os.path.join(pick_dir, "输出")

    # ── 执行 ──
    def _start_picking(self):
        if self.running:
            return
        if not self.source_index:
            messagebox.showwarning("提示", "请先扫描源目录")
            return
        # 优先使用文件表中的过滤后编号，否则用文本框中的手动输入
        if self._file_rows:
            numbers = self._get_active_numbers()
        else:
            numbers = parse_numbers(self.num_text.get('1.0', tk.END))
        if not numbers:
            messagebox.showwarning("提示", "请先扫描匹配目录提取编号")
            return
        dst = self.dst_var.get().strip()
        # 占位提示视为空
        if dst == self._dst_placeholder:
            dst = ''
        if not dst:
            default_dst = self._get_default_output_dir()
            if default_dst:
                dst = normalize_path(default_dst)
                self.dst_var.set(dst)
                self._log(f"输出目录为空，使用默认: {dst}")
            else:
                messagebox.showerror("错误", "输出目录为空且未设置挑选目录，请手动选择输出目录")
                return

        match_mode = _resolve_mode(self.match_mode.get(), MATCH_MODE_MAP)
        self.match_result = match_numbers(self.source_index, numbers, match_mode)
        matched = sum(1 for v in self.match_result.values() if v["matched"])
        unmatched = sum(1 for v in self.match_result.values() if not v["matched"])

        self._log(f"匹配结果: {matched} 个成功, {unmatched} 个未找到")
        self._log(f"来源: {self.src_var.get().strip()}")
        self._log(f"目标: {dst}")

        # 检测输出目录是否有同名文件，有则提示是否覆盖
        dup_files = []
        for num, info in self.match_result.items():
            if not info["matched"]:
                continue
            for filepath in info["files"]:
                filename = os.path.basename(filepath)
                dst_path = os.path.join(dst, filename)
                if os.path.exists(dst_path):
                    dup_files.append(filename)
        # 从 UI 获取重复模式，用户确认覆盖则改为覆盖模式
        dup_mode = _resolve_mode(self.dup_mode.get(), DUP_MODE_MAP)
        if dup_files:
            dup_preview = ', '.join(dup_files[:5])
            if len(dup_files) > 5:
                dup_preview += f' ...等共 {len(dup_files)} 个'
            msg = f"输出目录已有 {len(dup_files)} 个同名文件：\n\n{dup_preview}\n\n是否覆盖？"
            if messagebox.askyesno("覆盖确认", msg):
                dup_mode = "overwrite"  # 用户确认 → 覆盖
                self._log("用户选择覆盖同名文件")
            else:
                self._log("已取消（用户选择不覆盖）")
                return

        self.running = True
        self.run_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self._clear_log()
        self._log(f"开始 {self.op_mode.get()} — {len(numbers)} 个编号")
        self._log("-" * 40)

        op_mode = _resolve_mode(self.op_mode.get(), OP_MODE_MAP)

        thread = threading.Thread(
            target=self._do_pick, args=(dst, op_mode, dup_mode), daemon=True
        )
        thread.start()

    def _do_pick(self, dst, op_mode, dup_mode):
        self.cancel_event = threading.Event()

        def progress_cb(cur, total, name, status):
            pct = (cur / max(total, 1)) * 100
            self.root.after(0, lambda: self.progress_var.set(pct))
            self.root.after(0, lambda: self._log(f"{status} {name}"))

        try:
            found, not_found = execute_pick(
                self.match_result, dst, op_mode, dup_mode, progress_cb,
                cancel_event=self.cancel_event)
            self.root.after(0, lambda: self._on_complete(found, not_found, dst))
        except Exception as e:
            self.root.after(0, lambda: self._on_error(str(e)))

    def _cancel_pick(self):
        """取消正在执行的挑选任务"""
        if hasattr(self, 'cancel_event'):
            self.cancel_event.set()
            self._log("正在取消...")

    def _on_complete(self, found, not_found, dst):
        self.running = False
        self.run_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        self.progress_var.set(100)
        self._log("-" * 40)
        self._log(f"完成: {found} 张成功")
        if not_found:
            self._log(f"未找到: {', '.join(not_found)}")
            miss = os.path.join(dst, "_未找到.txt")
            with open(miss, 'w', encoding='utf-8') as f:
                f.write('\n'.join(not_found))
        self._log("输出目录: ")
        self._insert_clickable_path(dst)
        self._log("")  # 换行
        if found > 0:
            open_file_or_folder(dst)

    def _on_error(self, msg):
        self.running = False
        self.run_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        self._log(f"错误: {msg}")

    # ── 设置持久化（macOS 专业做法）──
    def _save_settings(self):
        try:
            cfg = get_config_path()
            # 保存前清除占位提示，避免写入无效文本
            dst_val = self.dst_var.get()
            if dst_val == self._dst_placeholder:
                dst_val = ''
            with open(cfg, 'w', encoding='utf-8') as f:
                f.write(f"src={normalize_path(self.src_var.get())}\n")
                f.write(f"pick={normalize_path(self.pick_var.get())}\n")
                f.write(f"dst={normalize_path(dst_val)}\n")
                f.write(f"digits={self.min_digits.get()}\n")
                prefix_val = '' if self._is_prefix_placeholder() else self.prefix_var.get()
                f.write(f"prefix={prefix_val}\n")
                f.write(f"swap_13={'1' if self.swap_13_var.get() else '0'}\n")
        except Exception:
            pass

    def _load_settings(self):
        try:
            cfg = get_config_path()
            if os.path.exists(cfg):
                with open(cfg, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('src='):
                            self.src_var.set(normalize_path(line[4:]))
                        elif line.startswith('pick='):
                            self.pick_var.set(normalize_path(line[5:]))
                        elif line.startswith('dst='):
                            val = normalize_path(line[4:])
                            self.dst_var.set(val)
                            if val:
                                self.dst_entry.config(fg=self.INK)
                            else:
                                self.dst_var.set(self._dst_placeholder)
                                self.dst_entry.config(fg='#5a5a70')
                        elif line.startswith('digits='):
                            self.min_digits.set(int(line[7:]))
                        elif line.startswith('prefix='):
                            val = line[8:]
                            if val:
                                self.prefix_var.set(val)
                                self.prefix_entry.config(fg=self.INK)
                            else:
                                self._set_prefix_placeholder()
                        elif line.startswith('swap_13='):
                            self.swap_13_var.set(line[8:] == '1')
        except Exception:
            pass

    def _on_dst_focus_in(self, event):
        """输出目录输入框获得焦点时清除占位提示"""
        if self.dst_var.get() == self._dst_placeholder:
            self.dst_var.set('')
            self.dst_entry.config(fg=self.INK)

    def _on_dst_focus_out(self, event):
        """输出目录输入框失去焦点时，若为空则恢复占位提示"""
        if not self.dst_var.get().strip():
            self.dst_var.set(self._dst_placeholder)
            self.dst_entry.config(fg='#5a5a70')

    def _is_prefix_placeholder(self):
        """当前是否显示占位提示（颜色或内容匹配）"""
        if self.prefix_entry.cget('fg') == '#5a5a70':
            return True
        # 兜底：内容匹配也视为占位
        return self.prefix_entry.get().strip() == self._prefix_hint

    def _set_prefix_placeholder(self):
        """显示占位提示"""
        self.prefix_entry.delete(0, tk.END)
        self.prefix_entry.insert(0, self._prefix_hint)
        self.prefix_entry.config(fg='#5a5a70')

    def _clear_prefix_placeholder(self):
        """清除占位提示"""
        if self._is_prefix_placeholder():
            self.prefix_entry.delete(0, tk.END)
        self.prefix_entry.config(fg=self.INK)

    def _on_close(self):
        """退出时保存设置并关闭窗口"""
        # 保存前清除占位提示，避免保存无效文本
        if self.dst_var.get() == self._dst_placeholder:
            self.dst_var.set('')
        self._save_settings()
        self.root.destroy()

    def _auto_scan_on_startup(self):
        """启动时如果路径有效，自动扫描源目录 + 提取编号（延迟到UI就绪）"""
        src = self.src_var.get().strip()
        pick = self.pick_var.get().strip()
        if src and os.path.isdir(src):
            self.root.after(300, self._scan_source)
        if pick and os.path.isdir(pick):
            self.root.after(400, self._extract_from_dir())

    # ── 授权系统 ──
    def _check_license_on_startup(self):
        """启动时检查授权，未激活则弹出对话框"""
        ok, msg = check_license()
        if not ok:
            self._show_license_dialog()

    def _show_license_dialog(self):
        """显示授权对话框"""
        LicenseDialog(self.root)

    def _add_trial_banner(self):
        """在界面顶部添加试用期倒计时条（实时倒计时）"""
        if not IS_TRIAL_BUILD:
            return
        ok, remaining, msg = check_trial()
        if not ok:
            return  # 试用期结束会弹对话框

        # 始终显示倒计时条
        remaining_secs = int(remaining * 3600)  # 小时转秒
        banner = tk.Frame(self.root, bg="#3a3a4e", padx=8, pady=4)
        children = self.root.winfo_children()
        if children:
            banner.pack(fill=tk.X, side=tk.TOP, before=children[0])
        else:
            banner.pack(fill=tk.X, side=tk.TOP)
        banner.pack_propagate(False)
        banner.configure(height=32)

        self._trial_dot = tk.Label(banner, text="◷", bg="#3a3a4e", fg="#f0c674",
                                  font=(self.sys_font, 10, "bold"))
        self._trial_dot.pack(side=tk.LEFT, padx=(10, 6))

        self._trial_label = tk.Label(banner, text="", bg="#3a3a4e", fg="#f0c674",
                                      font=(self.sys_font, 9))
        self._trial_label.pack(side=tk.LEFT)

        self._trial_tip = tk.Label(banner, text="试用结束后需购买激活码",
                                    bg="#3a3a4e", fg="#9090a8",
                                    font=(self.sys_font, 8))
        self._trial_tip.pack(side=tk.RIGHT, padx=10)

        self._trial_remaining_secs = [remaining_secs]
        self._update_trial_countdown()
        self.root.after(1000, self._tick_trial)

    def _update_trial_countdown(self):
        """更新倒计时显示"""
        secs = self._trial_remaining_secs[0]
        if secs <= 0:
            h, m, s = 0, 0, 0
        else:
            h = secs // 3600
            m = (secs % 3600) // 60
            s = secs % 60
        text = f"试用 {h:02d}:{m:02d}:{s:02d}"
        self._trial_label.config(text=text)
        # 剩余少于1小时变红色
        if secs < 3600:
            self._trial_label.config(fg="#ff6b6b")
            self._trial_dot.config(fg="#ff6b6b")

    def _tick_trial(self):
        """每秒刷新倒计时"""
        self._trial_remaining_secs[0] -= 1
        if self._trial_remaining_secs[0] <= 0:
            self._trial_label.config(text="试用 00:00:00", fg="#ff6b6b")
            self._trial_dot.config(fg="#ff6b6b")
            self.root.after(500, self._on_trial_expired_mac)
            return
        self._update_trial_countdown()
        self.root.after(1000, self._tick_trial)

    def _on_trial_expired_mac(self):
        """试用到期处理"""
        try:
            import tkinter.messagebox as msgbox
            msgbox.showerror("试用到期", "24小时试用已结束！\n请联系开发者购买正式版。")
        except Exception:
            pass
        self.root.destroy()
        import sys
        sys.exit()


class LicenseDialog:
    """授权激活对话框"""

    def __init__(self, parent):
        self.top = tk.Toplevel(parent)
        self.top.title("软件激活")
        self.top.geometry("480x380")
        self.top.resizable(False, False)
        self.top.configure(bg=ImagePickerApp.BG)
        self.top.transient(parent)
        self.top.grab_set()

        # 居中显示
        self.top.update_idletasks()
        x = (self.top.winfo_screenwidth() - 480) // 2
        y = (self.top.winfo_screenheight() - 380) // 2
        self.top.geometry(f"+{x}+{y}")

        self._build_ui()
        self.top.protocol('WM_DELETE_WINDOW', lambda: None)  # 禁止关闭

    def _build_ui(self):
        bg = ImagePickerApp.BG
        surface = ImagePickerApp.SURFACE
        ink = ImagePickerApp.INK
        ash = ImagePickerApp.ASH
        accent = ImagePickerApp.ACCENT
        success = ImagePickerApp.SUCCESS
        error = ImagePickerApp.ERROR
        sys_font = get_system_font()
        mono_font = get_monospace_font()

        main = tk.Frame(self.top, bg=bg, padx=24, pady=20)
        main.pack(fill=tk.BOTH, expand=True)

        # 标题 - 根据是否试用版显示不同文案
        if IS_TRIAL_BUILD:
            tk.Label(main, text="⏳ 试用期已结束", bg=bg, fg=accent,
                     font=(sys_font, 16, "bold")).pack(anchor="w")
            tk.Label(main, text="请购买激活码永久使用", bg=bg, fg=ash,
                     font=(sys_font, 10)).pack(anchor="w", pady=(0, 16))
        else:
            tk.Label(main, text="🔒 软件未激活", bg=bg, fg=accent,
                     font=(sys_font, 16, "bold")).pack(anchor="w")
            tk.Label(main, text="请购买授权后输入激活码使用", bg=bg, fg=ash,
                     font=(sys_font, 10)).pack(anchor="w", pady=(0, 16))

        # 序列号区域
        serial_frame = tk.Frame(main, bg=surface, padx=12, pady=10)
        serial_frame.pack(fill=tk.X, pady=(0, 12))

        tk.Label(serial_frame, text="您的机器码（发给卖家）:", bg=surface, fg=ash,
                 font=(sys_font, 9)).pack(anchor="w")

        serial_row = tk.Frame(serial_frame, bg=surface)
        serial_row.pack(fill=tk.X, pady=(4, 0))

        serial = get_mac_serial()
        serial_entry = tk.Label(serial_row, text=serial, font=(mono_font, 11),
                                bg=surface, fg=ink,
                                anchor="w", padx=8, pady=6)
        serial_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 复制按钮
        def copy_serial():
            self.top.clipboard_clear()
            self.top.clipboard_append(serial)
            copy_btn.config(text="✓ 已复制")
            self.top.after(1500, lambda: copy_btn.config(text="复制"))

        copy_btn = ttk.Button(serial_row, text="复制", style='Accent.TButton',
                               command=copy_serial)
        copy_btn.pack(side=tk.RIGHT, padx=(8, 0))

        # 激活码输入
        tk.Label(main, text="输入激活码:", bg=bg, fg=ash,
                 font=(sys_font, 9)).pack(anchor="w")

        self.code_var = tk.StringVar()
        code_entry = tk.Entry(main, textvariable=self.code_var, font=(mono_font, 14),
                              bg=ImagePickerApp.BG, fg=accent, insertbackground=accent,
                              relief="flat", highlightthickness=1,
                              highlightbackground=ImagePickerApp.BORDER,
                              justify="center")
        code_entry.pack(fill=tk.X, ipady=8, pady=(4, 12))
        code_entry.focus_set()
        code_entry.bind('<Return>', lambda e: self._activate())

        # 状态提示
        self.status_label = tk.Label(main, text="", bg=bg, fg=error,
                                     font=(sys_font, 9), wraplength=430)
        self.status_label.pack(fill=tk.X, pady=(0, 8))

        # 激活按钮
        self.act_btn = ttk.Button(main, text="激 活", style='Accent.TButton',
                                   command=self._activate)
        self.act_btn.pack(fill=tk.X, ipady=4)

        # 提示文字
        tk.Label(main, text="将机器码发给卖家付款后，会收到激活码",
                 bg=bg, fg=ash, font=(sys_font, 8)).pack(pady=(10, 0))

    def _activate(self):
        """执行激活"""
        code = self.code_var.get().strip()
        if not code:
            self.status_label.config(text="请输入激活码", fg=ImagePickerApp.ERROR)
            return

        serial = get_mac_serial()

        if verify_activation_code(code, serial):
            save_activation(code)
            self.status_label.config(text="✓ 激活成功！感谢购买！", fg=ImagePickerApp.SUCCESS)
            self.top.after(1000, self.top.destroy)
        else:
            self.status_label.config(text="✗ 激活码无效，请检查后重试",
                                     fg=ImagePickerApp.ERROR)


# ============================================================
# 入口
# ============================================================

class FindRawDialog:
    """JPG查找RAW对话框"""

    def __init__(self, parent, jpg_folder=''):
        self.top = tk.Toplevel(parent)
        self.top.title("JPG 查找 RAW")
        self.top.geometry("900x650")
        self.top.minsize(800, 550)
        self.top.configure(bg=ImagePickerApp.BG)
        self.top.transient(parent)
        self.top.grab_set()
        self._init_jpg_folder = jpg_folder  # 初始JPG目录

        # 字体
        self.sys_font = get_system_font()
        self.mono_font = get_monospace_font()

        # 配色
        bg = ImagePickerApp.BG
        surface = ImagePickerApp.SURFACE
        border = ImagePickerApp.BORDER
        ink = ImagePickerApp.INK
        ash = ImagePickerApp.ASH
        accent = ImagePickerApp.ACCENT
        success = ImagePickerApp.SUCCESS
        error = ImagePickerApp.ERROR

        self.jpg_folder = tk.StringVar(value=self._init_jpg_folder)
        self.raw_folder = tk.StringVar()
        self.out_folder = tk.StringVar()
        self.results = []
        self.selected_rows = set()
        self._build_ui()

    def _build_ui(self):
        bg = ImagePickerApp.BG
        surface = ImagePickerApp.SURFACE
        border = ImagePickerApp.BORDER
        ink = ImagePickerApp.INK
        ash = ImagePickerApp.ASH
        accent = ImagePickerApp.ACCENT
        success = ImagePickerApp.SUCCESS
        error = ImagePickerApp.ERROR

        # 主容器
        main_frame = tk.Frame(self.top, bg=bg, padx=16, pady=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 标题
        tk.Label(main_frame, text="JPG 查找 RAW", bg=bg, fg=accent,
                 font=(self.sys_font, 14, "bold")).pack(anchor="w")
        tk.Label(main_frame, text="选择JPG文件夹和RAW文件夹进行匹配，支持文件名匹配和EXIF时间匹配",
                 bg=bg, fg=ash, font=(self.sys_font, 9)).pack(anchor="w", pady=(0, 10))

        # JPG文件夹行
        jpg_row = tk.Frame(main_frame, bg=bg)
        jpg_row.pack(fill=tk.X, pady=4)
        tk.Label(jpg_row, text="JPG 目录", bg=surface, fg=ash,
                 font=(self.sys_font, 9, "bold"), width=10).pack(side=tk.LEFT)
        tk.Entry(jpg_row, textvariable=self.jpg_folder, font=(self.mono_font, 9),
                 bg=bg, fg=ink, insertbackground=ink, relief="flat",
                 highlightthickness=1, highlightbackground=border).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6, ipady=5)
        ttk.Button(jpg_row, text="浏览", style='Ghost.TButton',
                   command=lambda: self._browse('jpg'), width=6).pack(side=tk.LEFT)

        # RAW文件夹行
        raw_row = tk.Frame(main_frame, bg=bg)
        raw_row.pack(fill=tk.X, pady=4)
        tk.Label(raw_row, text="RAW 目录", bg=surface, fg=ash,
                 font=(self.sys_font, 9, "bold"), width=10).pack(side=tk.LEFT)
        tk.Entry(raw_row, textvariable=self.raw_folder, font=(self.mono_font, 9),
                 bg=bg, fg=ink, insertbackground=ink, relief="flat",
                 highlightthickness=1, highlightbackground=border).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6, ipady=5)
        ttk.Button(raw_row, text="浏览", style='Ghost.TButton',
                   command=lambda: self._browse('raw'), width=6).pack(side=tk.LEFT)

        # 输出目录行
        out_row = tk.Frame(main_frame, bg=bg)
        out_row.pack(fill=tk.X, pady=4)
        tk.Label(out_row, text="输出目录", bg=surface, fg=ash,
                 font=(self.sys_font, 9, "bold"), width=10).pack(side=tk.LEFT)
        self.out_entry = tk.Entry(out_row, textvariable=self.out_folder, font=(self.mono_font, 9),
                 bg=bg, fg=ink, insertbackground=ink, relief="flat",
                 highlightthickness=1, highlightbackground=border)
        self.out_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6, ipady=5)
        ttk.Button(out_row, text="浏览", style='Ghost.TButton',
                   command=lambda: self._browse('out'), width=6).pack(side=tk.LEFT)

        # 输出目录占位提示
        self._out_placeholder = "不填写默认导出到JPG目录下RAW文件夹"
        self.out_entry.insert(0, self._out_placeholder)
        self.out_entry.config(fg='#5a5a70')
        self.out_entry.bind('<FocusIn>', self._on_out_focus_in)
        self.out_entry.bind('<FocusOut>', self._on_out_focus_out)

        # 按钮行
        btn_row = tk.Frame(main_frame, bg=bg)
        btn_row.pack(fill=tk.X, pady=10)

        # 两个按钮颜色一样，EXIF在前，文件名匹配在后

        self.exif_btn = ttk.Button(btn_row, text="EXIF 匹配", style='Accent.TButton',
                                   command=self._start_exif)
        self.exif_btn.pack(side=tk.RIGHT, padx=(8, 0))

        self.match_btn = ttk.Button(btn_row, text="文件名匹配", style='Accent.TButton',
                                    command=self._start_match)
        self.match_btn.pack(side=tk.RIGHT)

        # 状态行
        status_row = tk.Frame(main_frame, bg=bg)
        status_row.pack(fill=tk.X)
        self.status_label = tk.Label(status_row, text="就绪", bg=bg, fg=ash, font=(self.sys_font, 9))
        self.status_label.pack(side=tk.LEFT)
        self.stats_label = tk.Label(status_row, text="", bg=bg, fg=accent, font=(self.sys_font, 9, "bold"))
        self.stats_label.pack(side=tk.RIGHT)

        # 结果表格
        tree_frame = tk.Frame(main_frame, bg=border, highlightbackground=border, highlightthickness=1)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        # Treeview + 滚动条深色样式
        style = ttk.Style()
        style.configure("Dark.Treeview",
                        background='#1a1a24',
                        foreground='#c0c0d0',
                        fieldbackground='#1a1a24',
                        borderwidth=0)
        style.configure("Dark.Treeview.Heading",
                        background='#252535',
                        foreground='#6c8aff',
                        borderwidth=0)
        style.map("Dark.Treeview.Heading",
                  background=[('active', '#2a2a4a'), ('!active', '#252535')],
                  foreground=[('active', '#6c8aff'), ('!active', '#6c8aff')])
        style.map("Dark.Treeview",
                  background=[('selected', '#2a2a4a')],
                  foreground=[('selected', '#e0e0f0')])
        # 滚动条样式
        style.configure("Dark.Vertical.TScrollbar",
                        background='#252535',
                        troughcolor='#1a1a24',
                        borderwidth=0,
                        arrowcolor='#6c8aff',
                        gripcount=0)
        style.map("Dark.Vertical.TScrollbar",
                  background=[('active', '#3a3a4e'), ('!active', '#252535')],
                  troughcolor=[('active', '#1a1a24'), ('!active', '#1a1a24')])

        columns = ('checked', 'jpg', 'sep1', 'raw', 'sep2', 'method')
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings',
                                 selectmode='extended', style="Dark.Treeview")
        self.tree.heading('checked', text='')
        self.tree.heading('jpg', text='JPG 文件')
        self.tree.heading('sep1', text='')
        self.tree.heading('raw', text='RAW 文件')
        self.tree.heading('sep2', text='')
        self.tree.heading('method', text='匹配方式')
        self.tree.column('checked', width=70, anchor='center')
        self.tree.column('jpg', width=160)
        self.tree.column('sep1', width=12, anchor='center', stretch=False)
        self.tree.column('raw', width=300, minwidth=150, stretch=True)
        self.tree.column('sep2', width=12, anchor='center', stretch=False)
        self.tree.column('method', width=100, anchor='center', stretch=False)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview,
                            style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.tree.bind('<ButtonRelease-1>', self._on_tree_click)
        self.tree.bind('<Double-1>', self._on_tree_double_click)
        # 鼠标滚轮滚动（跨平台）
        self._bind_mousewheel(self.tree)

        # 进度条
        self.progress = ttk.Progressbar(main_frame, mode='determinate')
        self.progress.pack(fill=tk.X, pady=(4, 0))

        # 底部按钮
        bottom_row = tk.Frame(main_frame, bg=bg)
        bottom_row.pack(fill=tk.X, pady=(8, 0))

        self.matched_label = tk.Label(bottom_row, text="0 已匹配", bg=bg, fg=success, font=(self.sys_font, 9, "bold"))
        self.matched_label.pack(side=tk.LEFT)
        self.unmatched_label = tk.Label(bottom_row, text="0 未匹配", bg=bg, fg=error, font=(self.sys_font, 9, "bold"))
        self.unmatched_label.pack(side=tk.LEFT, padx=(12, 0))

        ttk.Button(bottom_row, text="导出选中的RAW", style='Accent.TButton',
                   command=self._export_selected).pack(side=tk.RIGHT)

    def _bind_mousewheel(self, widget):
        """跨平台鼠标滚轮绑定"""
        if IS_MACOS:
            # macOS 使用 MouseWheel，delta 值不同
            widget.bind('<MouseWheel>', lambda e: widget.yview_scroll(int(-1 * e.delta), 'units'))
        elif IS_WINDOWS:
            widget.bind('<MouseWheel>', lambda e: widget.yview_scroll(int(-1 * (e.delta / 120)), 'units'))
        else:
            widget.bind('<Button-4>', lambda e: widget.yview_scroll(-1, 'units'))
            widget.bind('<Button-5>', lambda e: widget.yview_scroll(1, 'units'))

    def _on_out_focus_in(self, event):
        """输出目录获得焦点时清除占位提示"""
        if self.out_folder.get() == self._out_placeholder:
            self.out_folder.set('')
            self.out_entry.config(fg=ImagePickerApp.INK)

    def _on_out_focus_out(self, event):
        """输出目录失去焦点时，若为空则恢复占位提示"""
        if not self.out_folder.get().strip():
            self.out_folder.set(self._out_placeholder)
            self.out_entry.config(fg='#5a5a70')

    def _browse(self, folder_type):
        d = filedialog.askdirectory(title="选择文件夹")
        if d:
            if folder_type == 'jpg':
                self.jpg_folder.set(d)
            elif folder_type == 'raw':
                self.raw_folder.set(d)
            elif folder_type == 'out':
                self.out_folder.set(d)

    def _start_match(self):
        jpg_dir = self.jpg_folder.get().strip()
        raw_dir = self.raw_folder.get().strip()
        if not jpg_dir or not os.path.isdir(jpg_dir):
            messagebox.showwarning("提示", "请选择有效的JPG目录")
            return

        self.match_btn.config(state=tk.DISABLED)
        self.exif_btn.config(state=tk.DISABLED)
        self.status_label.config(text="扫描中...")
        self.tree.delete(*self.tree.get_children())
        self.results.clear()

        def worker():
            jpg_files = [f for f in get_all_files_in_folder(jpg_dir)
                        if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            if not jpg_files:
                self.top.after(0, lambda: self._on_match_done([], "未找到JPG文件"))
                return

            raw_files = get_all_files_in_folder(raw_dir) if raw_dir and os.path.isdir(raw_dir) else []
            results = match_jpg_to_raw(jpg_files, raw_files)
            self.top.after(0, lambda: self._on_match_done(results, "文件名匹配完成"))

        threading.Thread(target=worker, daemon=True).start()

    def _start_exif(self):
        """EXIF匹配 - 可独立使用，也可在文件名匹配后补充匹配"""
        jpg_dir = self.jpg_folder.get().strip()
        raw_dir = self.raw_folder.get().strip()
        if not jpg_dir or not os.path.isdir(jpg_dir):
            messagebox.showwarning("提示", "请选择有效的JPG目录")
            return

        self.match_btn.config(state=tk.DISABLED)
        self.exif_btn.config(state=tk.DISABLED)
        self.status_label.config(text="EXIF匹配中...")

        def worker():
            jpg_files = [f for f in get_all_files_in_folder(jpg_dir)
                        if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            if not jpg_files:
                self.top.after(0, lambda: self._on_match_done([], "未找到JPG文件"))
                return

            raw_files = get_all_files_in_folder(raw_dir) if raw_dir and os.path.isdir(raw_dir) else []

            # 如果没有文件名匹配结果，从头开始EXIF匹配
            if not self.results:
                # 构建初始结果（全部未匹配）
                base_results = [{
                    'jpg_path': f,
                    'jpg_name': os.path.basename(f),
                    'raw_path': None,
                    'raw_name': None,
                    'method': None
                } for f in jpg_files]
            else:
                # 在已有结果基础上补充
                base_results = list(self.results)
                # 添加新的JPG文件（如果有）
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

            # 只对未匹配的进行EXIF匹配
            unmatched = [r for r in base_results if not r['raw_path']]
            matched = match_by_exif(unmatched, raw_files,
                                    progress_cb=lambda c, t: self.top.after(0, lambda: self.status_label.config(text=f"EXIF: {c}/{t}")))
            # 合并结果
            matched_dict = {r['jpg_path']: r for r in matched}
            final = [matched_dict.get(r['jpg_path'], r) for r in base_results]
            self.top.after(0, lambda: self._on_match_done(final, "EXIF匹配完成"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_match_done(self, results, status):
        self.results = results
        self.match_btn.config(state=tk.NORMAL)
        matched = sum(1 for r in results if r['raw_path'])
        unmatched = len(results) - matched
        self.matched_label.config(text=f"{matched} 已匹配")
        self.unmatched_label.config(text=f"{unmatched} 未匹配")
        self.stats_label.config(text=f"{len(results)} JPG | {matched} 匹配 | {unmatched} 未匹配")
        self.status_label.config(text=status)

        # 更新EXIF按钮状态
        if unmatched > 0:
            self.exif_btn.config(state=tk.NORMAL)
        else:
            self.exif_btn.config(state=tk.DISABLED)

        # 填充表格
        self.tree.delete(*self.tree.get_children())
        self.selected_rows = set()
        for idx, r in enumerate(results):
            raw_text = r['raw_name'] or '- 未找到 -'
            item = self.tree.insert('', tk.END, values=(
                '☑' if r['raw_path'] else '☐',
                r['jpg_name'],
                '',  # 分隔列
                raw_text,
                '',  # 分隔列
                r['method'] or '-'
            ), tags=('found' if r['raw_path'] else 'notfound',))
            if r['raw_path']:
                self.selected_rows.add(item)

        self.tree.tag_configure('notfound', foreground='#ff6b6b')
        self.tree.tag_configure('found', foreground='#c0c0d0')
        # 更新滚动区域
        self.tree.update_idletasks()

    def _on_tree_click(self, event):
        """点击复选框列"""
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
        """双击打开对应列的文件"""
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
            # col '#2' = JPG列, '#4' = RAW列
            if col == '#2':
                jpg_path = result.get('jpg_path')
                if jpg_path and os.path.exists(jpg_path):
                    open_file_or_folder(jpg_path)
            elif col == '#4':
                raw_path = result.get('raw_path')
                if raw_path and os.path.exists(raw_path):
                    open_file_or_folder(raw_path)

    def _export_selected(self):
        """导出选中的RAW文件（多线程+进度条）"""
        if not self.selected_rows:
            messagebox.showinfo("提示", "没有选中的RAW文件")
            return
        # 优先使用输出目录
        out_dir = self.out_folder.get().strip()
        # 占位提示视为空
        if out_dir == self._out_placeholder:
            out_dir = ''
        # 为空则默认到JPG目录下的raw文件夹
        if not out_dir:
            jpg_dir = self.jpg_folder.get().strip()
            if jpg_dir and os.path.isdir(jpg_dir):
                out_dir = os.path.join(jpg_dir, 'raw')
                os.makedirs(out_dir, exist_ok=True)
            else:
                out_dir = filedialog.askdirectory(title="选择导出目录")
        if not out_dir:
            return

        # 收集要导出的文件（保留源目录结构，避免不同子目录同名文件互相覆盖）
        tasks = []  # [(src_path, dst_path), ...]
        used_names = set()
        for item in self.selected_rows:
            idx = self.tree.index(item)
            if 0 <= idx < len(self.results):
                raw_path = self.results[idx].get('raw_path')
                if raw_path and os.path.exists(raw_path):
                    # 若同名文件已存在，自动加序号避免覆盖
                    basename = os.path.basename(raw_path)
                    dst_path = os.path.join(out_dir, basename)
                    if basename in used_names or os.path.exists(dst_path):
                        stem = os.path.splitext(basename)[0]
                        ext = os.path.splitext(basename)[1]
                        counter = 1
                        while True:
                            new_name = f"{stem}_{counter}{ext}"
                            dst_path = os.path.join(out_dir, new_name)
                            if new_name not in used_names and not os.path.exists(dst_path):
                                break
                            counter += 1
                            if counter > 9999:
                                break
                    used_names.add(os.path.basename(dst_path))
                    tasks.append((raw_path, dst_path))

        if not tasks:
            messagebox.showinfo("提示", "没有可导出的RAW文件")
            return

        # 禁用按钮，显示进度条
        self.progress['maximum'] = len(tasks)
        self.progress['value'] = 0

        def worker():
            import shutil
            count = 0
            for i, (src_path, dst_path) in enumerate(tasks):
                try:
                    shutil.copy2(src_path, dst_path)
                    count += 1
                except Exception:
                    pass
                # 更新进度
                self.top.after(0, lambda v=i + 1: self.progress.config(value=v))
            # 完成
            def _done():
                messagebox.showinfo("完成", f"已导出 {count} 个RAW文件到:\n{out_dir}")
                if os.path.isdir(out_dir):
                    open_file_or_folder(out_dir)
            self.top.after(0, _done)

        threading.Thread(target=worker, daemon=True).start()


def _check_license_status():
    """
    检查授权状态，返回 (是否可用, 状态码, 消息)
    状态码: 'ok' | 'trial_expired' | 'not_activated'
    """
    # 免费版无需检查
    if IS_FREE_BUILD:
        return True, 'ok', '免费版'

    # 试用版：检查试用期
    if IS_TRIAL_BUILD:
        ok, remaining, msg = check_trial()
        if not ok:
            return False, 'trial_expired', '24小时试用已结束！'
        return True, 'ok', msg

    # 一机一码版（macOS 正式版）
    if IS_MACOS:
        ok, msg = check_license()
        if ok:
            return True, 'ok', '已激活'
        return False, 'not_activated', '未激活'

    return True, 'ok', ''


def _log(msg):
    """写入桌面日志文件（调试用）"""
    try:
        log_path = os.path.join(os.path.expanduser('~'), 'Desktop', 'image_picker_debug.log')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"{msg}\n")
    except Exception:
        pass


def main():
    _log("=== main() start ===")
    _log(f"IS_MACOS={IS_MACOS}, IS_TRIAL_BUILD={IS_TRIAL_BUILD}, IS_FREE_BUILD={IS_FREE_BUILD}")

    root = tk.Tk()
    _log("tk.Tk() created")

    # Windows DPI 感知（仅 Windows）
    if IS_WINDOWS:
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    # 授权检查（在创建主程序之前）
    if IS_MACOS:
        _log("macOS: checking license...")
        ok, status, msg = _check_license_status()
        _log(f"license check: ok={ok}, status={status}, msg={msg}")

        if not ok:
            if status == 'trial_expired':
                _log("trial expired, showing error")
                # 试用过期 → 弹窗并退出
                messagebox.showerror("试用到期", msg + "\n请联系开发者购买正式版。",
                                     parent=root)
                root.destroy()
                sys.exit()
            elif status == 'not_activated':
                _log("not activated, showing license dialog")
                # 未激活 → 弹出激活对话框
                result = {'activated': False}

                def _on_close():
                    ok2, _, _ = _check_license_status()
                    result['activated'] = ok2
                    dlg.top.destroy()

                dlg = LicenseDialog(root)
                dlg.top.protocol('WM_DELETE_WINDOW', _on_close)
                dlg.top.wait_window(dlg.top)

                if not result['activated']:
                    root.destroy()
                    sys.exit()

    _log("license OK, creating ImagePickerApp")
    # 授权通过 → 启动主程序
    ImagePickerApp(root)
    _log("starting mainloop")
    root.mainloop()


def _early_crash_log(msg):
    """最早期的崩溃日志（尽可能早地写入）"""
    try:
        log_path = os.path.join(os.path.expanduser('~'), 'Desktop', 'image_picker_crash.log')
        import traceback
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"{msg}\n\n{traceback.format_exc()}")
    except Exception:
        pass


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        _early_crash_log(f"Main crash: {e}")
        raise
