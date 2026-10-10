# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — JPG查找RAW 中文版 (macOS, universal2, onedir .app)
#   pyinstaller --noconfirm --clean JpgFindRaw_zh.spec

APP_VERSION = '2.1.2'

a = Analysis(
    ['JpgFindRaw_zh.py'],
    pathex=[],
    binaries=[],
    datas=[],
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
    [],
    exclude_binaries=True,
    name='JpgFindRawCN',          # internal executable name (ASCII)
    debug=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch='universal2',
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='JpgFindRawCN')

app = BUNDLE(
    coll,
    name='JPG查找RAW.app',
    icon='logo.icns',
    bundle_identifier='com.imagepicker.jpgfindraw.zh',
    version=APP_VERSION,
    info_plist={
        'CFBundleName': 'JPG查找RAW',
        'CFBundleDisplayName': 'JPG查找RAW',
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'CFBundleDevelopmentRegion': 'zh_CN',
        'CFBundleLocalizations': ['zh_CN', 'zh-Hans'],
        'LSMinimumSystemVersion': '10.13',
        'LSApplicationCategoryType': 'public.app-category.photography',
        'NSHighResolutionCapable': True,
        'NSRemovableVolumesUsageDescription': 'JPG查找RAW 需要访问外接硬盘，以查找和复制您的 JPG 和 RAW 照片。',
        'NSNetworkVolumesUsageDescription': 'JPG查找RAW 需要访问网络硬盘，以查找和复制您的 JPG 和 RAW 照片。',
        'NSDesktopFolderUsageDescription': 'JPG查找RAW 需要访问桌面，以查找和复制您的照片。',
        'NSDocumentsFolderUsageDescription': 'JPG查找RAW 需要访问“文稿”文件夹，以查找和复制您的照片。',
        'NSDownloadsFolderUsageDescription': 'JPG查找RAW 需要访问“下载”文件夹，以查找和复制您的照片。',
        'NSPicturesFolderUsageDescription': 'JPG查找RAW 需要访问“图片”文件夹，以查找和复制您的照片。',
    },
)
