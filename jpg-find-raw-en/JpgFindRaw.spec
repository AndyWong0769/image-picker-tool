# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for JpgFindRaw (English, macOS)
#   pyinstaller --noconfirm --clean JpgFindRaw.spec
#
# Notes:
#  - onedir .app bundle (NOT onefile): onefile .app bundles are deprecated by
#    PyInstaller on macOS and misbehave with Gatekeeper / privacy prompts.
#  - universal2: one app for Intel and Apple Silicon Macs. Requires the
#    python.org universal2 Python (see .github/workflows/build-jpgfindraw-en.yml).

APP_VERSION = '2.1.3'

a = Analysis(
    ['JpgFindRaw.py'],
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
    name='JpgFindRaw',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch='universal2',
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='JpgFindRaw',
)

app = BUNDLE(
    coll,
    name='JpgFindRaw.app',
    icon='logo.icns',
    bundle_identifier='com.imagepicker.jpgfindraw',
    version=APP_VERSION,
    info_plist={
        'CFBundleName': 'JpgFindRaw',
        'CFBundleDisplayName': 'JpgFindRaw',
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'CFBundleDevelopmentRegion': 'en',
        'LSMinimumSystemVersion': '10.13',
        'LSApplicationCategoryType': 'public.app-category.photography',
        'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'JPG Match RAW',
        # Text shown in macOS privacy prompts (external drives, user folders)
        'NSRemovableVolumesUsageDescription':
            'JpgFindRaw needs access to your external drives to find and copy your JPG and RAW photos.',
        'NSNetworkVolumesUsageDescription':
            'JpgFindRaw needs access to network drives to find and copy your JPG and RAW photos.',
        'NSDesktopFolderUsageDescription':
            'JpgFindRaw needs access to your Desktop to find and copy your photos.',
        'NSDocumentsFolderUsageDescription':
            'JpgFindRaw needs access to your Documents folder to find and copy your photos.',
        'NSDownloadsFolderUsageDescription':
            'JpgFindRaw needs access to your Downloads folder to find and copy your photos.',
        'NSPicturesFolderUsageDescription':
            'JpgFindRaw needs access to your Pictures folder to find and copy your photos.',
    },
)
