#!/usr/bin/env python3
"""匹配规则测试：去掉中文后，英文+数字部分必须与 RAW 名完全一样。
usage: python tests/test_match.py   (run from rawpicker-zh/)"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8')   # Windows 控制台默认不是 UTF-8
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import RawPicker as rp  # noqa: E402

CASES = [
    # (RAW, JPG, 应该匹配?)
    ("DSC01234.NEF", "DSC01234.jpg", True),
    ("DSC01234.NEF", "婚礼DSC01234修图.jpg", True),
    ("DSC0002.CR2", "婚礼DSC0002_修图.jpg", True),
    ("DSC0002.CR2", "婚礼_DSC0002_修图.jpg", True),
    ("_DSC8746.NEF", "_DSC8746 副本.jpg", True),
    ("IMG_1234.CR2", "IMG_1234-edit.jpg", True),
    ("IMG_1234.CR2", "IMG_1234 (1).jpg", True),
    ("DSC01234.NEF", "dsc01234.jpg", True),          # 大小写不同也算同一个名字
    ("DSC01234.NEF", "A7S01234修图.jpg", False),      # 前缀不同
    ("DSC01234.NEF", "IMG_01234.jpg", False),
    ("DSC01234.NEF", "Z9_1234.jpg", False),
    ("DSC01234.NEF", "01234.jpg", False),             # 只有编号，不完全一样
    ("DSC01234.NEF", "1234.jpg", False),
    ("DSC01234.NEF", "DSC012345.jpg", False),         # 多一位数字
    ("_DSC8746.NEF", "DSC8746.jpg", False),           # 少了下划线，不完全一样
    ("DSC_0001.NEF", "DSC_0001_HDR.jpg", False),      # 下划线连着，整段是 DSC_0001_HDR
    ("DSC01234.NEF", "婚礼.jpg", False),
]


def main():
    bad = 0
    for raw, jpg, want in CASES:
        got = rp.is_filename_match(raw, jpg)
        # match_files 必须与参考实现一致
        m, mj = rp.match_files(['/j/' + jpg], ['/r/' + raw], '/out')
        got2 = bool(m)
        ok = got == want and got2 == want
        bad += not ok
        print(f"{'OK ' if ok else 'BAD'}  {raw:14} {jpg:24} 期望{'匹配' if want else '不匹配'}  结果{'匹配' if got else '不匹配'}")
    # XMP 等附属文件不参与
    assert '.xmp' not in rp.RAW_FOLDER_ALLOWED
    print("失败:", bad)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
