# Antigravity 中文汉化补丁（Windows）

一套轻量化、纯本地实现的 **Google Antigravity 桌面端简体中文汉化补丁**。无需第三方主题或额外常驻进程，直接基于本机官方 `app.asar` 自动解包、注入原生菜单/托盘/界面汉化并重新计算 SHA256 完整性重打包，支持版本升级后自动识别并重新适配。

## ✨ 核心特性

- **原生级 Electron 汉化**：直接汉化应用主菜单（文件 / 编辑 / 视图 / 窗口 / 帮助 / WSL 连接）、托盘状态菜单、自动更新提示与退出确认弹窗。
- **前端界面实时汉化**：内置 `dict.json`（含 `uiPhrases`、`chromeOnly`、`attrs`、`patterns` 四类规则共 1000+ 词条），结合 `MutationObserver` 增量遍历 DOM，自动跳过代码块（`pre`/`code`/Monaco/Xterm）与输入框，避免误译代码和用户输入。
- **零依赖 ASAR 完整性校验**：纯 Python 标准库实现 ASAR 解包与重打包，自动生成官方兼容的 4MB 分块 SHA256 `integrity` 校验信息（若系统安装 `node` 会自动执行 `node --check` 语法校验）。
- **自动备份 & 一键无损还原**：打补丁前自动按版本+哈希备份官方原版 `app.asar`，随时可一键恢复至原版。
- **自动适配更新**：官方客户端升级后再次运行一键脚本，会自动识别新版本并重新构建补丁。

## 📁 文件说明

| 文件 | 用途 |
|---|---|
| `一键应用汉化.ps1` | 一键检测版本、构建补丁、关闭进程并替换 `app.asar` |
| `一键还原原版.ps1` | 一键恢复为备份的官方原版 `app.asar` |
| `patch-app.py` | ASAR 解包、JS 菜单/钩子注入、完整性校验与重打包主程序 |
| `injector.js` | 前端 DOM 实时增量翻译引擎 |
| `dict.json` | 简体中文词库与正则替换规则 |

## 🚀 使用方法

### 前置要求
- Windows 10 / 11，已安装 Google Antigravity 桌面端
- Python 3.10+（已加入 `PATH`）
- （可选）Node.js（用于在打包前执行 `node --check` 语法校验）

### 应用汉化
1. 克隆或下载本仓库到本地目录：
   ```powershell
   git clone https://github.com/jushaoheng123/antigravity-zh-cn.git
   cd antigravity-zh-cn
   ```
2. 在 PowerShell 中运行：
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\一键应用汉化.ps1
   ```
3. 脚本校验完成后重新打开 Antigravity 即可生效。

> 若 Antigravity 安装在非默认路径，可提前指定环境变量：
> ```powershell
> $env:AGY_ASAR_PATH = "你的\Antigravity\resources\app.asar"
> ```

### 还原官方原版
```powershell
powershell -ExecutionPolicy Bypass -File .\一键还原原版.ps1
```

## 📝 说明

- 本项目仅包含汉化词库与补丁构建脚本，不包含 Antigravity 原程序或任何二进制文件。
- 词库支持热更新：修改本地 `dict.json` 后新开的 Antigravity 窗口会自动加载最新词条。
