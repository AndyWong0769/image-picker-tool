#!/usr/bin/env python3
"""Create a small test data set for the JpgFindRaw self-test.

Layout under <base>:
  jpg/      DSC_0001.jpg, edited_DSC_0002.jpg, sub/IMG_1234-edit.jpg, holiday.jpg (EXIF only), nomatch.jpg
  raw/      DSC_0001.NEF, DSC_0002.ARW, deep/IMG_1234.CR2, P9990001.ORF (EXIF date = holiday.jpg), other.DNG
  out/      (export target)
"""
import os
import struct
import sys


def tiff_with_date(date: str, endian='<') -> bytes:
    """Minimal TIFF: IFD0 with DateTime + ExifIFD pointer -> DateTimeOriginal."""
    d = date.encode('ascii') + b'\x00'          # 20 bytes
    e = endian
    hdr = (b'II' if e == '<' else b'MM') + struct.pack(e + 'HI', 42, 8)
    # IFD0 at 8: 2 entries
    ifd0_off = 8
    ifd0_size = 2 + 2 * 12 + 4
    exif_off = ifd0_off + ifd0_size
    exif_size = 2 + 1 * 12 + 4
    data_off = exif_off + exif_size
    dt_modified = b'2001:01:01 00:00:00\x00'     # DateTime (should NOT win)
    ifd0 = struct.pack(e + 'H', 2)
    ifd0 += struct.pack(e + 'HHII', 306, 2, 20, data_off)
    ifd0 += struct.pack(e + 'HHII', 34665, 4, 1, exif_off)
    ifd0 += struct.pack(e + 'I', 0)
    exif = struct.pack(e + 'H', 1)
    exif += struct.pack(e + 'HHII', 36867, 2, 20, data_off + 20)
    exif += struct.pack(e + 'I', 0)
    return hdr + ifd0 + exif + dt_modified + d


def jpeg_with_date(date: str) -> bytes:
    tiff = tiff_with_date(date, '>')
    app1 = b'Exif\x00\x00' + tiff
    return (b'\xff\xd8' + b'\xff\xe0' + struct.pack('>H', 16) + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
            + b'\xff\xe1' + struct.pack('>H', len(app1) + 2) + app1
            + b'\xff\xda' + b'\x00' * 64 + b'\xff\xd9')


def main(base):
    for d in ('jpg/sub', 'raw/deep', 'out'):
        os.makedirs(os.path.join(base, d), exist_ok=True)

    def w(rel, data):
        with open(os.path.join(base, rel), 'wb') as f:
            f.write(data)

    w('jpg/DSC_0001.jpg', jpeg_with_date('2024:05:01 10:00:01'))
    w('jpg/edited_DSC_0002.jpg', jpeg_with_date('2024:05:01 10:00:02'))
    w('jpg/sub/IMG_1234-edit.jpg', jpeg_with_date('2024:05:01 10:00:03'))
    w('jpg/holiday.jpg', jpeg_with_date('2024:07:15 18:30:00'))
    w('jpg/nomatch.jpg', jpeg_with_date('2020:01:01 00:00:00'))

    w('raw/DSC_0001.NEF', tiff_with_date('2024:05:01 10:00:01') + b'\x00' * 1000)
    w('raw/DSC_0002.ARW', tiff_with_date('2024:05:01 10:00:02') + b'\x00' * 1000)
    w('raw/deep/IMG_1234.CR2', tiff_with_date('2024:05:01 10:00:03') + b'\x00' * 1000)
    w('raw/P9990001.ORF', tiff_with_date('2024:07:15 18:30:00', '>') + b'\x00' * 1000)
    w('raw/other.DNG', tiff_with_date('2019:01:01 00:00:00') + b'\x00' * 1000)
    w('raw/.hidden.NEF', b'x')


EXPECTED = {
    'DSC_0001.jpg': ['DSC_0001.NEF', 'Filename'],
    'edited_DSC_0002.jpg': ['DSC_0002.ARW', 'EXIF'],
    'IMG_1234-edit.jpg': ['IMG_1234.CR2', 'Filename'],
    'holiday.jpg': ['P9990001.ORF', 'EXIF'],
    'nomatch.jpg': [None, None],
}
EXPECTED_OUT = sorted(['DSC_0001.NEF', 'DSC_0002.ARW', 'IMG_1234.CR2', 'P9990001.ORF'])


def check(result_file):
    import json
    r = json.load(open(result_file))
    print(json.dumps(r, indent=2))
    assert r['ok'], r
    assert r['matched'] == EXPECTED, r['matched']
    assert r['out_files'] == EXPECTED_OUT, r['out_files']
    assert r['copied'] == 4 and r['failed'] == 0
    print("SELFTEST PASSED")


if __name__ == '__main__':
    if sys.argv[1] == 'check':
        check(sys.argv[2])
    else:
        main(sys.argv[1])
