"""
图片编号挑选工具 - macOS DMG 打包配置
使用 py2app 打包为 .app，然后创建 .dmg

使用方法：
  1. 安装依赖: pip install py2app pillow
  2. 打包: python setup.py py2app
  3. 生成的 .app 在 dist/ 目录下

创建 DMG（可选）：
  hdiutil create -volname "图片编号挑选工具" -srcfolder dist/ -ov -format UDZO 图片编号挑选工具.dmg
"""

from setuptools import setup

APP = ['图片编号挑选工具macos.py']
DATA_FILES = []

OPTIONS = {
    'argv_emulation': False,  # macOS 上不需要
    'iconfile': 'logo.icns',  # macOS 应用图标
    'plist': {
        # 应用基本信息
        'CFBundleName': '图片编号挑选工具',
        'CFBundleDisplayName': '图片编号挑选工具',
        'CFBundleIdentifier': 'com.imagepicker.app',
        'CFBundleVersion': '4.0.0',
        'CFBundleShortVersionString': '4.0.0',

        # 关键：设置默认语言为中文，避免菜单变英文
        'CFBundleDevelopmentRegion': 'zh_CN',
        'CFBundleLocalizations': ['zh_CN', 'zh-Hans'],

        # macOS 显示名称
        'CFBundleGetInfoString': '图片编号挑选工具 v4.0',

        # 高分辨率支持
        'NSHighResolutionCapable': True,

        # 后台应用（不显示 Dock 图标菜单）
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
                ],
            }
        ],

        # 不使用 macOS 原生标题栏
        'NSRequiresAquaSystemAppearance': False,
    },
    'packages': ['PIL'],  # 包含 Pillow
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
