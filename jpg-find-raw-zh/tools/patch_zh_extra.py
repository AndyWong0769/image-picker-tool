import sys
p = sys.argv[1]
s = open(p, encoding='utf-8').read()
def R(a, b, n=1):
    global s
    c = s.count(a)
    assert c == n, f"expected {n} got {c}: {a!r}"
    s = s.replace(a, b)

# ===== 1. 过滤中文 (filter_chinese) — logic from the user's Chinese edition =====
R('''def extract_possible_raw_names(filename: str,
                               prefix_filters: list = None, suffix_filters: list = None) -> list:
    """Extract possible RAW filename patterns from a JPG filename"""''',
'''def extract_possible_raw_names(filename: str,
                               prefix_filters: list = None, suffix_filters: list = None,
                               filter_chinese: bool = True) -> list:
    """从 JPG 文件名提取可能的 RAW 文件名

    filter_chinese=True : 额外提取字母/数字/下划线片段参与匹配（忽略纯中文部分）
    filter_chinese=False: 不提取片段，只用完整文件名（含中文）和 3 位以上数字匹配
    """''')
R('''    all_patterns = re.findall(r'[a-zA-Z0-9_]{2,}', name_without_ext)
    for p in all_patterns:
        candidates.add(p.lower())''',
'''    if filter_chinese:
        # 过滤中文：提取字母数字下划线片段，忽略纯中文
        for p in re.findall(r'[a-zA-Z0-9_]{2,}', name_without_ext):
            candidates.add(p.lower())''')
R('''    jpg_suffix = parse_filters(settings.get('jpg_suffix_filters', '')) if settings else []
''', '''    jpg_suffix = parse_filters(settings.get('jpg_suffix_filters', '')) if settings else []
    filter_chinese = settings.get('filter_chinese', True) if settings else True
''')
R('''candidates = extract_possible_raw_names(jpg_filename, jpg_prefix, jpg_suffix)''',
  '''candidates = extract_possible_raw_names(jpg_filename, jpg_prefix, jpg_suffix, filter_chinese)''')
R('''        self.settings = {
            'create_raw_folder': True,''', '''        self.settings = {
            'filter_chinese': True,       # 过滤中文（匹配时忽略纯中文字符）
            'create_raw_folder': True,''')
# settings dialog checkbox
R('''        create_raw_var = tk.BooleanVar(value=self.settings.get('create_raw_folder', True))''',
'''        filter_chinese_var = tk.BooleanVar(value=self.settings.get('filter_chinese', True))
        tk.Checkbutton(frame, text="过滤中文（匹配时忽略纯中文字符）",
                       variable=filter_chinese_var, bg=bg, fg=ink, selectcolor=surface,
                       activebackground=bg, activeforeground=ink, highlightthickness=0, bd=0,
                       font=F(10), wraplength=420, justify=tk.LEFT).pack(anchor="w", pady=(0, 12))

        create_raw_var = tk.BooleanVar(value=self.settings.get('create_raw_folder', True))''')
R('''            self.settings['create_raw_folder'] = create_raw_var.get()''',
'''            self.settings['filter_chinese'] = filter_chinese_var.get()
            self.settings['create_raw_folder'] = create_raw_var.get()''')

# ===== 2. wording from the user's existing Chinese app =====
W = [
 ('APP_DISPLAY = "JPG查找RAW"', 'APP_DISPLAY = "JPG 查找 RAW"'),
 ('"支持多个 JPG 文件夹与多个 RAW 文件夹进行匹配，支持文件名匹配和 EXIF 拍摄时间匹配。"', '"支持多个 JPG 文件夹和 RAW 文件夹进行匹配，支持文件名匹配和 EXIF 时间匹配"'),
 ('"留空 = 导出到各 JPG 目录下的 raw 子文件夹"', '"不填写默认导出到jpg目录下"'),
 ('label="移除此路径"', 'label="删除此路径"'),
 ('"创建 raw 子文件夹（导出到 JPG 目录下的 raw 文件夹）"', '"新建raw文件夹（导出时在JPG目录下创建raw子文件夹）"'),
 ('"在默认格式之外，可额外添加扩展名。"', '"下方可添加额外扩展名格式（在默认格式之外）。"'),
 ('"JPG 文件名过滤（多个关键词用逗号分隔）"', '"JPG 文件名过滤（多个关键词用逗号隔开）"'),
 ('"示例：_DSC0531.jpg 前缀填 _ → DSC0531 可匹配 DSC0531.CR2"', '"示例：_DSC0531.jpg 前缀输入 _  →  DSC0531 匹配 DSC0531.CR2"'),
 ('text="已匹配 0"', 'text="0 已匹配"'),
 ('text="未匹配 0"', 'text="0 未匹配"'),
 ('text=f"已匹配 {matched}"', 'text=f"{matched} 已匹配"'),
 ('text=f"未匹配 {unmatched}"', 'text=f"{unmatched} 未匹配"'),
 ('text=f"共 {len(results)} 个 JPG | 已匹配 {matched} | 未匹配 {unmatched}"', 'text=f"{len(results)} JPG | {matched} 匹配 | {unmatched} 未匹配"'),
 ('"导出勾选的 RAW"', '"导出勾选的RAW"'),
 ('text="正在扫描文件夹..."', 'text="扫描中..."'),
 ('"未找到 JPG 文件"', '"未找到JPG文件"', 2),
 ('text="EXIF 匹配中..."', 'text="EXIF匹配中..."'),
 ('"EXIF 匹配完成"', '"EXIF匹配完成"'),
 ('"没有勾选任何 RAW 文件"', '"没有选中的RAW文件"'),
 ('"没有可导出的 RAW 文件"', '"没有可导出的RAW文件"'),
 ('f"目标文件夹中已有 {len(conflict_files)} 个同名文件："', 'f"目标文件夹已有 {len(conflict_files)} 个同名文件："'),
 ("preview += f' ... 等共 {len(conflict_files)} 个'", "preview += f' ...等共 {len(conflict_files)} 个'"),
]
for w in W:
    R(*w)

# (Tk console suppression, version and self-test console report now live in the English source)

open(p, 'w', encoding='utf-8').write(s)
print("ok")
