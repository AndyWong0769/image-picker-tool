# -*- mode: python ; coding: utf-8 -*-
# RawPicker 图片筛选工具 — macOS universal2 .app (onedir)
APP_VERSION = '3.0.2'

a = Analysis(['RawPicker.py'], pathex=[], binaries=[], datas=[], hiddenimports=[], hookspath=[],
             runtime_hooks=[], excludes=['PIL', 'numpy', 'matplotlib', 'scipy', 'tkinter.test', 'unittest', 'pydoc'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='RawPicker', debug=False, strip=False, upx=False,
          console=False, argv_emulation=False, target_arch='universal2', codesign_identity=None,
          entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='RawPicker')
app = BUNDLE(
    coll, name='RawPicker.app', icon='logo.icns', bundle_identifier='com.rawpicker.zh', version=APP_VERSION,
    info_plist={
        'CFBundleName': 'RawPicker',            # must stay ASCII (Tk menu title; macOS 11 crash otherwise)
        'CFBundleDisplayName': 'RawPicker',
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'CFBundleDevelopmentRegion': 'en',
        'LSMinimumSystemVersion': '10.13',
        'LSApplicationCategoryType': 'public.app-category.photography',
        'NSHighResolutionCapable': True,
        'NSRemovableVolumesUsageDescription': 'RawPicker 需要访问外接硬盘，以查找和复制您的 JPG 和 RAW 照片。',
        'NSNetworkVolumesUsageDescription': 'RawPicker 需要访问网络硬盘，以查找和复制您的照片。',
        'NSDesktopFolderUsageDescription': 'RawPicker 需要访问桌面，以查找和复制您的照片。',
        'NSDocumentsFolderUsageDescription': 'RawPicker 需要访问“文稿”文件夹，以查找和复制您的照片。',
        'NSDownloadsFolderUsageDescription': 'RawPicker 需要访问“下载”文件夹，以查找和复制您的照片。',
        'NSPicturesFolderUsageDescription': 'RawPicker 需要访问“图片”文件夹，以查找和复制您的照片。',
    },
)
