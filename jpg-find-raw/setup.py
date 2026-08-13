"""
JPG 查找 RAW — macOS DMG 打包脚本
使用 py2app 打包为 .app，然后用 hdiutil 创建 .dmg

使用方法：
  1. 安装依赖: pip3 install py2app pillow
  2. 打包 .app: python3 setup.py py2app
  3. 创建 .dmg: ./build_dmg.sh

生成文件：
  dist/JPG查找RAW.app  — 应用程序
  JPG查找RAW.dmg       — 安装镜像
"""

from setuptools import setup

APP = ['jpg找raw_macos.py']
DATA_FILES = []

OPTIONS = {
    'argv_emulation': False,
    'iconfile': 'logo.icns',

    # 支持 Apple Silicon 和 Intel
    'arch': 'arm64',

    'plist': {
        'CFBundleName': 'JPG查找RAW',
        'CFBundleDisplayName': 'JPG查找RAW',
        'CFBundleIdentifier': 'com.imagepicker.jpgfindraw',
        'CFBundleVersion': '2.0.0',
        'CFBundleShortVersionString': '2.0.0',

        # 默认中文
        'CFBundleDevelopmentRegion': 'zh_CN',
        'CFBundleLocalizations': ['zh_CN', 'zh-Hans'],

        'CFBundleGetInfoString': 'JPG查找RAW v2.0 — JPG查找RAW工具',

        # 最低 macOS 11.0 (Big Sur)
        'LSMinimumSystemVersion': '11.0',

        # 高分辨率支持
        'NSHighResolutionCapable': True,

        # 前台应用
        'LSBackgroundOnly': False,

        # 支持打开的文件类型
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'Image Files',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': [
                    'public.image',
                    'public.jpeg',
                    'public.png',
                    'com.canon.cr2-raw-image',
                    'com.nikon.nef-raw-image',
                    'com.sony.arw-raw-image',
                ],
            }
        ],

        # 支持深色模式
        'NSRequiresAquaSystemAppearance': False,
    },
    'packages': ['PIL'],
    'use_old_sdk': True,
    'excludes': [
        'tkinter.test',
        'tkinter.tix',
        'matplotlib',
        'numpy',
        'scipy',
        'PIL._tkinter_finder',
    ],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
