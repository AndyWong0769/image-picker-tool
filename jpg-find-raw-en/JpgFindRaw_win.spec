# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for JpgFindRaw (English, Windows, single-file .exe)
#   pyinstaller --noconfirm --clean JpgFindRaw_win.spec

APP_VERSION = '2.1.2'
_v = tuple(int(x) for x in APP_VERSION.split('.')) + (0,)

from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_v, prodvers=_v, mask=0x3f, flags=0x0, OS=0x40004,
                      fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('CompanyName', 'JpgFindRaw'),
            StringStruct('FileDescription', 'JPG Match RAW'),
            StringStruct('FileVersion', APP_VERSION),
            StringStruct('InternalName', 'JpgFindRaw'),
            StringStruct('OriginalFilename', 'JpgFindRaw.exe'),
            StringStruct('ProductName', 'JPG Match RAW'),
            StringStruct('ProductVersion', APP_VERSION),
        ])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ],
)

a = Analysis(
    ['JpgFindRaw.py'],
    pathex=[],
    binaries=[],
    datas=[('app.ico', '.')],          # used for the window title-bar icon
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=['PIL', 'numpy', 'matplotlib', 'scipy', 'tkinter.test', 'unittest', 'pydoc'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='JpgFindRaw',
    debug=False,
    strip=False,
    upx=False,              # UPX triggers more antivirus false positives
    console=False,
    icon='app.ico',
    version=version_info,
)
