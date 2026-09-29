# Bilibili Radio 桌面播放器

面向 Windows 的 B 站音频播放器，使用 Vue 3 / Pinia、Tauri 2 和内嵌 Flask 后端。支持多 P 曲目、播放队列、歌单、收藏、最近播放、悬浮歌词、音质与倍速选择。

0.2.0 增加持续保存播放进度、后台快捷键、系统托盘、双窗口位置记忆和当前曲目定位。重复启动会唤起已有窗口；上一曲点击一次即切换。实现及测试记录见 [桌面体验验证报告](doc/desktop-playback-tray-implementation-2026-09-29.md)。

0.2.1 将续播位置保留期设为最后实际播放后的 24 小时，暂停和重新打开软件不延长期限；推荐只保存选中结果，后台自动回收过期展示记录和无关联元数据。收藏、歌单、队列与收听统计保留，见 [生命周期与验证说明](doc/metadata-retention-and-progress-0.2.1-2026-09-29.md)。

## 桌面操作

- `Ctrl+Alt+Space`：播放/暂停；`Ctrl+Alt+←`：上一曲；`Ctrl+Alt+→`：下一曲，后台或托盘中均可用。
- 最小化和关闭主窗口都会进入托盘。双击托盘图标恢复，右键菜单选择“退出”才完全退出。
- 播放队列打开时定位当前曲目；队列和歌单中的“定位当前播放”按钮可随时找回当前项。
- 主窗口和歌词窗口分别记住位置；重新启动恢复队列及进度，点击播放后续播。

## 下载与发布

安装包通过 [GitHub Releases](https://github.com/ourhome-macro/Bilibili-radio/releases) 分发。当前采用无代码签名的 Windows x64 NSIS 安装包，Windows 可能显示未知发布者或 SmartScreen 提示。

当前正式版：[v0.2.0](https://github.com/ourhome-macro/Bilibili-radio/releases/tag/v0.2.0)。发布与本机升级记录见 [0.2.0 交付记录](doc/desktop-0.2.0-install-and-release-2026-09-29.md)。

GitHub Actions 在提交和 PR 时执行自动测试与构建验证；只在推送与桌面版本一致的 `v*` 标签时发布 Release，没有定时发布。详见 [CI/CD 说明](doc/github-actions-desktop-ci-cd-2026-09-29.md)。

## 源码结构

本目录位于 Git 仓库的 `bilibili-radio/` 子目录中，工作流位于仓库根目录的 `.github/workflows/desktop.yml`。

| 路径 | 用途 |
| --- | --- |
| `bilibili-player/` | Vue 界面、播放器状态和 Tauri 桌面外壳 |
| `py-radio/` | Flask HTTP API、音频代理、SQLite 曲库与播放数据 |
| `deploy/build-desktop-backend.ps1` | 将 Python 后端打包为 Windows exe |
| `scripts/` | 测试入口、版本检查和打包后端启动验证 |
| `doc/` | 当前操作说明、需求分析与历史实施记录 |

音频由浏览器音频元素播放，前端通过 HTTP API 与后端通信；旧版文档中的 Socket.IO 架构不是当前运行链路。

## 本地桌面构建

需要 Windows、Node.js 24、Python 3.12、Rust 1.97.1、Visual Studio C++ Build Tools 和 WebView2。GitHub Actions 会在 Windows runner 上准备所需构建环境。

在 `bilibili-player` 目录执行：

```powershell
npm ci
npm run desktop:build -- --ci --no-sign --bundles nsis -- --locked
```

构建前置步骤会安装后端依赖并生成内嵌后端，再输出安装包到 `bilibili-player/src-tauri/target/release/bundle/nsis/`。安装包版本以 Tauri/Cargo 清单为准，三个版本位置必须一致。

## 测试

在本项目目录，使用已安装 `py-radio/requirements.txt` 的 Python 环境：

```powershell
python scripts/run_backend_tests.py
python -m unittest discover -s scripts/tests -v
python scripts/check_desktop_version.py
```

后端测试使用临时数据库。版本检查需要 Python 3.11+，流水线固定为 3.12。在 `bilibili-player` 目录运行 `npm test` 执行播放器及组件回归。完整流水线还包含前端类型检查、Windows 打包、原生窗口规则测试及打包后端启动测试。

## 文档

从 [文档索引](doc/README.md) 阅读当前构建、发布和桌面体验任务说明。带日期的旧实施记录用于追溯背景，不应当作当前行为或功能已完成的保证。
