# macOS DMG 打包说明

## 概述

本项目使用 GitHub Actions 自动构建 macOS DMG 安装包。
你不需要苹果电脑即可完成打包，但需要 macOS 环境来测试。

## 自动化打包流程

### 1. 推送代码到 GitHub

```bash
git init
git add .
git commit -m "v4.0 macOS 优化版"
git remote add origin https://github.com/你的用户名/仓库名.git
git push -u origin main
```

### 2. 创建版本标签触发构建

```bash
git tag v4.0.0
git push origin v4.0.0
```

### 3. 下载构建产物

1. 打开 GitHub 仓库页面
2. 进入 Actions 标签
3. 找到最新的构建任务
4. 在 Artifacts 区域下载 `图片编号挑选工具-macos`
5. 解压后得到 `.dmg` 文件

## 本地测试（需要 macOS）

### 安装依赖

```bash
# 确保 Python 3.8+ 已安装
python3 -m pip install --upgrade pip
pip3 install py2app pillow
```

### 打包

```bash
python3 setup.py py2app
```

### 创建 DMG

```bash
cd dist
hdiutil create -volname "图片编号挑选工具" -srcfolder "图片编号挑选工具.app" -ov -format UDZO ../图片编号挑选工具.dmg
cd ..
```

### 安装测试

1. 双击 `图片编号挑选工具.dmg`
2. 将 `图片编号挑选工具.app` 拖到 Applications 文件夹
3. 首次打开可能需要在 系统设置 → 隐私与安全性 中允许

## macOS 虚拟化测试（无 Mac 时）

如果你没有苹果电脑，可以使用以下方式测试：

### 方案一：macOS 虚拟机（推荐）

1. **UTM** (免费) - https://mac.getutm.app/
   - 支持 Apple Silicon 和 Intel Mac
   - 在 Windows/Linux 上也可运行
   
2. **VMware/VirtualBox**
   - 需要 macOS 镜像（ISO）
   - 仅支持 Intel 平台

### 方案二：GitHub Actions 交互式调试

在仓库设置中启用 `debugging with ssh`：

```yaml
# 在 .github/workflows/build-macos.yml 的 jobs.build 下添加
- uses: mxschmitt/action-tmate@v3
    if: ${{ failure() }}
    with:
      limit-access-to-actor: true
```

## 关键优化点

### 1. 配置存储（专业 macOS 做法）

- 旧版：`.picker_config` 放在脚本目录（不专业）
- 新版：`~/Library/Application Support/图片编号挑选工具/config.txt`

### 2. 文件打开方式

- 旧版：`os.startfile()`（Windows 专用）
- 新版：`subprocess.run(['open', path])`（macOS 原生）

### 3. 字体

- 旧版：Segoe UI（Windows 字体）
- 新版：`.AppleSystemUIFont` / `Menlo`（macOS 字体）

### 4. 快捷键

- 旧版：Ctrl+点击
- 新版：⌘+点击（macOS 原生体验）

### 5. 路径处理

- 旧版：强制转换为反斜杠
- 新版：保持系统原生分隔符

### 6. 菜单语言

- 通过 `CFBundleDevelopmentRegion: zh_CN` 设置默认语言
- 自定义中文菜单栏防止浏览后变英文

## 常见问题

### Q: 首次打开提示"无法验证开发者"？

A: 右键点击 .app → 打开 → 确认打开。或在 系统设置 → 隐私与安全性 中允许。

### Q: 在虚拟机中运行卡顿？

A: 给虚拟机分配至少 4GB 内存和 2 个 CPU 核心。

### Q: 如何卸载？

A: 将 `图片编号挑选工具.app` 从 Applications 文件夹拖到废纸篓即可。
配置文件在 `~/Library/Application Support/图片编号挑选工具/`，可手动删除。

## 依赖

- Python 3.8+
- tkinter（Python 自带）
- Pillow（可选，用于 EXIF 读取）
- py2app（打包用）
