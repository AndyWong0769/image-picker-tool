# -*- mode: python ; coding: utf-8 -*-
# JPG查找RAW 中文版 — Windows 单文件 exe
APP_VERSION = '2.2.1'
_v = tuple(int(x) for x in APP_VERSION.split('.')) + (0,)

from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_v, prodvers=_v, mask=0x3f, flags=0x0, OS=0x40004,
                      fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable('080404B0', [
            StringStruct('CompanyName', 'JPG查找RAW'),
            StringStruct('FileDescription', 'JPG 查找 RAW'),
            StringStruct('FileVersion', APP_VERSION),
            StringStruct('InternalName', 'JpgFindRawCN'),
            StringStruct('OriginalFilename', 'JPG查找RAW.exe'),
            StringStruct('ProductName', 'JPG 查找 RAW'),
            StringStruct('ProductVersion', APP_VERSION),
        ])]),
        VarFileInfo([VarStruct('Translation', [2052, 1200])]),
    ],
)

a = Analysis(['JpgFindRaw_zh.py'], pathex=[], binaries=[], datas=[('app.ico', '.')],
             hiddenimports=[], hookspath=[], runtime_hooks=[],
             excludes=['PIL', 'numpy', 'matplotlib', 'scipy', 'tkinter.test', 'unittest', 'pydoc'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='JPG查找RAW', debug=False, strip=False,
          upx=False, console=False, icon='app.ico', version=version_info)
