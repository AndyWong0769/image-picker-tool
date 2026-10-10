#!/usr/bin/env python3
"""RawPicker self-test data.  usage: make_testdata.py <dir> | check <result.json> <folder|files>"""
import json
import os
import sys

JPGS = ['DSC_0001.jpg', '婚礼DSC0002_修图.jpg', 'sub/IMG_1234 副本.jpg', 'DSC0005.jpg', 'nomatch_9999.jpg']
RAWS = ['DSC_0001.NEF', 'DSC0002.CR2', 'deep/IMG_1234.ARW', 'a/DSC0005.NEF', 'b/DSC0005.NEF',
        'DSC_0001.xmp', 'other_7777.NEF']

EXPECTED = {
    # folder mode: every JPG except nomatch; both DSC0005.NEF copies kept (renamed, not overwritten)
    'folder': {'out_files': sorted(['DSC_0001.NEF', 'DSC0002.CR2', 'IMG_1234.ARW', 'DSC0005.NEF', 'DSC0005_1.NEF']),
               'unmatched': ['nomatch_9999.jpg'], 'copied': 5},
    # files mode: only the first two JPGs (sorted) are selected: DSC0005.jpg, DSC_0001.jpg
    'files': {'out_files': sorted(['DSC_0001.NEF', 'DSC0005.NEF', 'DSC0005_1.NEF']),
              'unmatched': [], 'copied': 3},
}


def make(base):
    for rel in JPGS:
        p = os.path.join(base, 'jpg', rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(b'\xff\xd8jpg\xff\xd9')
    for i, rel in enumerate(RAWS):
        p = os.path.join(base, 'raw', rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(b'RAW' + bytes([i]) * 2048)
    os.makedirs(os.path.join(base, 'out'), exist_ok=True)


def check(result_file, mode):
    r = json.load(open(result_file, encoding='utf-8'))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    exp = EXPECTED[mode]
    assert r['ok'], r
    assert r['tk_console'] is False, 'Tk console was created'
    assert r['out_files'] == exp['out_files'], (r['out_files'], exp['out_files'])
    assert r['unmatched'] == exp['unmatched'], r['unmatched']
    assert r['copied'] == exp['copied'] and not r['failed'], r
    print(f"SELFTEST PASSED ({mode})")


if __name__ == '__main__':
    if sys.argv[1] == 'check':
        check(sys.argv[2], sys.argv[3])
    else:
        make(sys.argv[1])
