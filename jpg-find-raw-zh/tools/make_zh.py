import sys, re
src, dst = sys.argv[1], sys.argv[2]
s = open(src, encoding='utf-8').read()
count = {'n': 0}
def R(a, b, n=1):
    c = s.count(a)
    assert c == n, f"expected {n} got {c}: {a!r}"
    globals()['s'] = s.replace(a, b)

# ---------- identity / docstring ----------
R('''JPG Match RAW (JpgFindRaw) - English edition for macOS / Windows
================================================================''',
  '''JPG查找RAW - 中文版 (macOS / Windows)
=====================================
与英文版 JpgFindRaw 同源；区别：界面中文、不保存上次的文件夹路径。''')
R('APP_NAME = "JpgFindRaw"', 'APP_NAME = "JpgFindRaw-ZH"          # config folder name\nAPP_DISPLAY = "JPG查找RAW"')

# ---------- fonts with CJK glyphs ----------
R('''    _UI_FAMILY = "Helvetica Neue"''', '''    _UI_FAMILY = "PingFang SC"''')
R('''    _UI_FAMILY = "Segoe UI"''', '''    _UI_FAMILY = "Microsoft YaHei UI"''')

# (folder paths are not remembered — already handled in the English source)

# ---------- UI text ----------
T = [
 ('self.root.title("JPG Match RAW")', 'self.root.title(APP_DISPLAY)'),
 ('''tk.Label(title_row, text="JPG Match RAW",''', '''tk.Label(title_row, text=APP_DISPLAY,'''),
 ('"Match multiple JPG folders with RAW folders. Supports filename matching and EXIF timestamp matching."',
  '"支持多个 JPG 文件夹与多个 RAW 文件夹进行匹配，支持文件名匹配和 EXIF 拍摄时间匹配。"'),
 ('text="JPG Directory"', 'text="JPG 目录"'),
 ('text="RAW Directory"', 'text="RAW 目录"'),
 ('text="Output Directory"', 'text="输出目录"'),
 ('"Empty = export to \'raw\' subfolder under JPG directory"', '"留空 = 导出到各 JPG 目录下的 raw 子文件夹"'),
 ('"Browse"', '"浏览"'),
 ('"+ Add Folder"', '"+ 添加文件夹"', 2),
 ('"Match by Filename"', '"文件名匹配"'),
 ('"Match by EXIF"', '"EXIF 匹配"'),
 ('text="Ready"', 'text="就绪"'),
 ("heading('checked', text='Select')", "heading('checked', text='勾选')"),
 ("heading('jpg', text='JPG File')", "heading('jpg', text='JPG 文件')"),
 ("heading('raw', text='RAW File')", "heading('raw', text='RAW 文件')"),
 ("heading('method', text='Method')", "heading('method', text='匹配方式')"),
 ('text="0 matched"', 'text="已匹配 0"'),
 ('text="0 unmatched"', 'text="未匹配 0"'),
 ('"Export Selected RAW"', '"导出勾选的 RAW"'),
 ('"Add JPG Folder"', '"添加 JPG 文件夹"'),
 ('"Add RAW Folder"', '"添加 RAW 文件夹"'),
 ('"Select Output Directory"', '"选择输出目录"'),
 ('label="Show in Finder" if _IS_MACOS else "Open Folder"', 'label="在访达中显示" if _IS_MACOS else "打开文件夹"'),
 ('label="Remove Path"', 'label="移除此路径"'),
 # tooltip
 ('"Default Supported Formats"', '"默认支持的格式"'),
 ('"JPG formats:"', '"JPG 格式："'),
 ('"RAW formats:"', '"RAW 格式："'),
 # settings dialog
 ('dlg.title("Settings")', 'dlg.title("设置")'),
 ('"Create \'raw\' subfolder (exports to a raw subfolder under JPG directory)"', '"创建 raw 子文件夹（导出到 JPG 目录下的 raw 文件夹）"'),
 ('text="Export Method:"', 'text="导出方式："'),
 ('(("Copy", \'copy\', (10, 20)), ("Move", \'cut\', 0))', '(("复制", \'copy\', (10, 20)), ("剪切", \'cut\', 0))'),
 ('text="Extension Settings"', 'text="扩展名设置"'),
 ('"Add extra extension formats below (in addition to defaults)."', '"在默认格式之外，可额外添加扩展名。"'),
 ('"JPG Directory Extensions:"', '"JPG 目录扩展名："'),
 ('"RAW Directory Extensions:"', '"RAW 目录扩展名："'),
 ('"Examples: .tif    jpg/png/tif    .jpg,.png    JPG,PNG"', '"示例：.tif    jpg/png/tif    .jpg,.png    JPG,PNG"'),
 ('"JPG Filename Filters (separate multiple keywords with comma)"', '"JPG 文件名过滤（多个关键词用逗号分隔）"'),
 ('"JPG Prefix Filter:"', '"JPG 前缀过滤："'),
 ('"JPG Suffix Filter:"', '"JPG 后缀过滤："'),
 ('"Example: _DSC0531.jpg enter prefix _ → DSC0531 matches DSC0531.CR2"', '"示例：_DSC0531.jpg 前缀填 _ → DSC0531 可匹配 DSC0531.CR2"'),
 ('FlatButton(btn_row, "Cancel", close,', 'FlatButton(btn_row, "取消", close,'),
 ('self._accent_button(btn_row, "Save", save_and_close,', 'self._accent_button(btn_row, "保存", save_and_close,'),
 ('text="Settings saved"', 'text="设置已保存"'),
 ('"Save Failed", f"Could not save settings to:\\n{_CONFIG_PATH}\\n\\nError: {e}"', '"保存失败", f"无法保存设置到：\\n{_CONFIG_PATH}\\n\\n错误：{e}"'),
 # prechecks
 ('messagebox.showwarning("Notice", "Please add at least one JPG folder", parent=self.root)', 'messagebox.showwarning("提示", "请至少添加一个 JPG 文件夹", parent=self.root)'),
 ('messagebox.showwarning("Notice", "Please add at least one RAW folder", parent=self.root)', 'messagebox.showwarning("提示", "请至少添加一个 RAW 文件夹", parent=self.root)'),
 ('self._show_error("Folder Not Available",\n                             "These folders cannot be found. Is the external drive connected?\\n\\n"',
  'self._show_error("文件夹不可用",\n                             "找不到以下文件夹，外接硬盘是否已连接？\\n\\n"'),
 # status
 ('text="Scanning folders..."', 'text="正在扫描文件夹..."'),
 ('"No JPG files found"', '"未找到 JPG 文件"', 2),
 ("{'text': f\"Found {len(jpg_files)} JPG files, scanning RAW folders...\"}", "{'text': f\"找到 {len(jpg_files)} 张 JPG，正在扫描 RAW 文件夹...\"}"),
 ("{'text': f\"Matching {len(jpg_files)} JPG with {len(raw_files)} RAW files...\"}", "{'text': f\"正在匹配 {len(jpg_files)} 张 JPG 与 {len(raw_files)} 张 RAW...\"}"),
 ('"Filename matching complete"', '"文件名匹配完成"'),
 ('"Matching failed"', '"匹配失败"'),
 ('text="EXIF matching..."', 'text="EXIF 匹配中..."'),
 ("{'text': f\"Reading EXIF: {c}/{t} RAW files\"}", "{'text': f\"正在读取 EXIF：{c}/{t} 张 RAW\"}"),
 ('"EXIF matching complete"', '"EXIF 匹配完成"'),
 ('"EXIF matching failed"', '"EXIF 匹配失败"'),
 ('''f"{exc}\\n\\nIf your photos are on an external drive, make sure it is "
                                "connected and that JpgFindRaw is allowed to access it "
                                "(System Settings → Privacy & Security → Files and Folders)."''',
  '''f"{exc}\\n\\n如果照片在外接硬盘上，请确认硬盘已连接，并已允许本软件访问"
                                "（系统设置 → 隐私与安全性 → 文件与文件夹）。"'''),
 ('text=f"{matched} matched"', 'text=f"已匹配 {matched}"'),
 ('text=f"{unmatched} unmatched"', 'text=f"未匹配 {unmatched}"'),
 ('text=f"{len(results)} JPG | {matched} matched | {unmatched} unmatched"', 'text=f"共 {len(results)} 个 JPG | 已匹配 {matched} | 未匹配 {unmatched}"'),
 ("r['raw_name'] or '- Not Found -',\n                r['method'] or '-'", "r['raw_name'] or '- 未找到 -',\n                {'Filename': '文件名', 'EXIF': 'EXIF'}.get(r['method'], '-')"),
 # export
 ('self._show_info("Notice", "No RAW files selected")', 'self._show_info("提示", "没有勾选任何 RAW 文件")'),
 ('self._show_info("Notice", "No RAW files to export")', 'self._show_info("提示", "没有可导出的 RAW 文件")'),
 ("preview += f' ... and {len(conflict_files) - 10} more'", "preview += f' ... 等共 {len(conflict_files)} 个'"),
 ('''msg = (f"Target folder already has {len(conflict_files)} file(s) with the same name:"
                   f"\\n\\n{preview}\\n\\nOverwrite?")''', '''msg = (f"目标文件夹中已有 {len(conflict_files)} 个同名文件："
                   f"\\n\\n{preview}\\n\\n是否覆盖？")'''),
 ('messagebox.askyesno("Overwrite Confirmation", msg', 'messagebox.askyesno("覆盖确认", msg'),
 ('PermissionError("folder is read-only")', 'PermissionError("文件夹为只读")'),
 ('''self._show_error("Cannot Write to Output Folder",
                             "These folders cannot be created or written to:\\n\\n"''', '''self._show_error("无法写入输出文件夹",
                             "以下文件夹无法创建或写入：\\n\\n"'''),
 ('''"\\n\\nNote: NTFS-formatted drives are read-only on macOS. "
                               "Choose another Output Directory."''', '''"\\n\\n注意：NTFS 格式的硬盘在 macOS 上是只读的，"
                               "请换一个输出目录。"'''),
 ('text=f"Exporting {len(tasks)} file(s)..."', 'text=f"正在导出 {len(tasks)} 个文件..."'),
 ('text=f"Exporting... {v}/{len(tasks)}"', 'text=f"正在导出... {v}/{len(tasks)}"'),
 ("action = 'Moved' if export_method == 'cut' else 'Copied'", "action = '剪切' if export_method == 'cut' else '复制'"),
 ('text=f"{action} {count} file(s)" + (f", {len(errors)} failed" if errors else "")', 'text=f"已{action} {count} 个文件" + (f"，{len(errors)} 个失败" if errors else "")'),
 ('msg = f"{action} {count} file(s) to:\\n{folder_list}"', 'msg = f"已{action} {count} 个文件到：\\n{folder_list}"'),
 ('msg += f"\\n\\n{len(errors)} file(s) failed:\\n" + "\\n".join(errors[:10])', 'msg += f"\\n\\n{len(errors)} 个文件失败：\\n" + "\\n".join(errors[:10])'),
 ('msg += f"\\n... and {len(errors) - 10} more"', 'msg += f"\\n... 还有 {len(errors) - 10} 个"'),
 ('self._show_error("Export Finished With Errors", msg)', 'self._show_error("导出完成（有错误）", msg)'),
 ('self._show_info("Done", msg)', 'self._show_info("完成", msg)'),
]
# source-list texts (counts / totals / picked images)
R('''_SRC_TXT = {
    'jpg_count': "{n} images",
    'raw_count': "{n} RAW files",
    'jpg_total': "Total: {n} images",
    'raw_total': "Total: {n} RAW files",
    'counting': "counting...",
    'missing': "not found",
    'picked_many': "{n} picked images: {first} ... ({folder})",
    'picked_one': "{first} ({folder})",
    'add_images': "+ Add Images",
    'pick_title': "Select JPG images (multi-select, Ctrl+A / Cmd+A selects all)",
    'img_type': "Images",
    'all_type': "All files",
}''', '''_SRC_TXT = {
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
}''')

for item in T:
    R(*item)
open(dst, 'w', encoding='utf-8').write(s)
print("generated", dst)
