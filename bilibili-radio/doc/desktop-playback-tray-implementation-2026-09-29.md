# 桌面播放体验实现与验证

日期：2026-09-29。对应 [已确认的需求与诊断](desktop-playback-tray-requirements-and-diagnosis-2026-09-29.md)。

## 当前实现

- 数据库升级至 v9，独立保存用户/分 P 的最新进度；心跳和最近播放计数分离，以会话和事件序号拒绝陈旧覆盖。
- 前端每 5 秒保存，并在暂停、拖动、切曲和退出前保存；按用户保留本地待同步记录。先恢复到已存位置，再开始播放；启动只恢复显示，不自动出声。
- 上一曲一次切换；播放器按钮、歌词、托盘和后台快捷键使用相同控制入口。
- Windows 原生托盘与全局快捷键：Ctrl+Alt+Space 播放/暂停、Ctrl+Alt+Left 上一曲、Ctrl+Alt+Right 下一曲。冲突时显示具体提示，不使整个应用退出。
- 主窗口最小化/关闭进入托盘；双击托盘恢复，菜单明确退出。退出前等待前端落盘，异常情况下有超时退出路径。
- Windows 在创建窗口和后端之前获取生命周期互斥锁，重复启动通过命名事件唤回原窗口；原生单实例插件提供进程身份与其他平台支持。Windows Job Object 管理整个内嵌 Python 进程树，正常退出及桌面进程被强杀时都会清理子进程。
- 主窗口与歌词窗口分别保存逻辑尺寸及相对显示器工作区的位置；恢复时处理 DPI 和显示器消失。歌词隐藏再显示不再强制底部居中。
- 播放队列与歌单增加当前曲目定位；打开队列自动定位，筛选隐藏当前项时可显式清除筛选并定位，用户手动浏览时停止自动跟随。

版本更新为 0.2.0，仍采用无签名安装包和 v* 标签发布策略。本次不会自动创建发布标签或安装覆盖用户现有版本。

## 根因与修复位置

| 问题 | 原因与本次修复 | 主要源码 |
| --- | --- | --- |
| 同一集重新从头 | 旧逻辑只在达到历史阈值时写一次，续播还读取另一张表；新增独立检查点，按实际音频时间保存，恢复完成后才播放 | [playback_progress.py](../py-radio/playback_progress.py)、[playbackProgress.ts](../bilibili-player/src/audio/playbackProgress.ts)、[playerStore.ts](../bilibili-player/src/stores/playerStore.ts)、[StreamingAudioPlayer.ts](../bilibili-player/src/audio/StreamingAudioPlayer.ts) |
| 上一曲要点两次 | prev() 原先超过 3 秒就 seek(0) 并返回；取消该分支，各入口共用 prev() | [playerStore.ts](../bilibili-player/src/stores/playerStore.ts) |
| 后台快捷键无效 | 原先没有原生全局注册；新增全局快捷键，通过主窗口控制桥调用同一播放器 | [desktop_controls.rs](../bilibili-player/src-tauri/src/desktop_controls.rs)、[DesktopControlBridge.vue](../bilibili-player/src/components/DesktopControlBridge.vue) |
| 关闭后后台残留 | 主窗口关闭与歌词 WebView/后端退出并不等价；新增托盘生命周期、退出保存握手和进程树清理 | [main.rs](../bilibili-player/src-tauri/src/main.rs)、[backend_job.rs](../bilibili-player/src-tauri/src/backend_job.rs) |
| 多点出现多实例 | 原先启动入口没有互斥；Windows 在任何窗口和后端创建之前阻止第二实例并通知原窗口恢复 | [startup_gate.rs](../bilibili-player/src-tauri/src/startup_gate.rs) |
| 窗口位置乱跳 | 主窗口未保存，歌词每次显示又覆盖为默认位置；分别保存，主窗口另外保留最大化前的正常矩形 | [window_geometry.rs](../bilibili-player/src-tauri/src/window_geometry.rs)、[main.rs](../bilibili-player/src-tauri/src/main.rs) |
| 列表找不到当前项 | 只有高亮，缺少滚动与解除筛选入口；以稳定曲目标识定位，允许用户停止自动跟随 | [QueueDrawer.vue](../bilibili-player/src/components/layout/QueueDrawer.vue)、[PlaylistDetailView.vue](../bilibili-player/src/views/PlaylistDetailView.vue) |

## 技术范围

- 新增 playback_progress 表以及 playback_sessions 的顺序/计数字段；兼容读取旧 recent 和 playback_recent 历史。
- 桌面插件升级到与 Tauri 2.12 兼容的版本，前端 Tauri API/CLI 同步升级。
- 位置管理采用明确的双窗口状态文件 window-geometry.json，按显示器工作区存逻辑偏移，不持久化“隐藏/最小化”作为下次启动状态。
- Windows 主窗口使用 GetWindowPlacement/SetWindowPlacement 保存和恢复正常矩形及最大化状态，避免把最大化的屏幕矩形误当作正常窗口尺寸；通过 AdjustWindowRectExForDpi 转换内外尺寸。
- 冷启动互斥锁覆盖整个进程生命周期；唤回事件在取得锁之前创建，因此启动尚未完成时收到的重复启动请求也不会丢失。单独依赖插件存在互斥锁已创建、通信窗口尚未创建的竞争窗口，此处在插件之前阻断第二个实例。
- 收窄 .gitignore 的归档规则，避免原来的 *tar* 意外忽略 startup_gate.rs 等源文件。
- 为隔离原生验证可设置 BILIBILI_RADIO_PROFILE_DIR；默认仍使用原有应用配置/数据目录。该变量不是用户日常使用的必需配置。
- 新增 Vitest 播放器、进度缓存及队列组件回归，并加入 GitHub Actions；原生位置规则单元测试也加入 Windows 构建任务。

## 本地验证结果

- 后端 107 项测试通过，包含新增 12 项进度回归；发布版本规则 6 项通过。
- 前端 21 项回归测试通过，覆盖续播等待 seek 完成、切曲竞态、暂停、心跳、退出、离线重试、乱序应答和队列定位；类型检查和生产构建通过。
- Rust 窗口几何规则 3 项通过；Tauri、PyInstaller、NSIS 完整构建生成无签名 0.2.0 x64 安装包。
- Edge 中运行实际 Vue/Pinia 和受控音频，验证续播、60 秒进度保存并重载、离线保存恢复、1,000 项队列及歌单的当前项定位。
- Windows 原生实测：冷启动同时启动 10 次，仅 1 个实例存活；进入托盘后再启动 10 次，仍是原窗口句柄、1 个桌面实例、1 组内嵌后端，并恢复可见。
- 原生实测：主窗口移动到 (120,150)、尺寸 1100×720 后，最大化、关闭入托盘并重启，最大化状态与还原后的正常矩形均正确；歌词窗口移动到 (160,180)、尺寸 920×112，隐藏重开和进程重启均保持。
- 原生实测：托盘菜单退出完成 quit 进度保存并清理后端；仅强制结束桌面主进程也会自动清理后端进程树。

公开验证证据见 [evidence-desktop-implementation-2026-09-29](evidence-desktop-implementation-2026-09-29/)。原生界面实测在本机 Windows 执行；GitHub runner 执行自动测试与构建，不等同于实际 GUI 交互。DPI 换算和显示器缺失覆盖了单元测试，未实际更换显示器或多 DPI 硬件。

本地完整安装包使用现有 Python 3.10.11 打包环境、Node 24.13 和 Rust 1.97.1；云端固定使用 Python 3.12、Node 24 和 Rust 1.97.1。无签名包经 Get-AuthenticodeSignature 确认为 NotSigned。

## 真实安装数据验证

已按用户要求读取安装版的 SQLite，并通过 SQLite backup API 创建独立副本。原库为 v8，含 1,853 条曲目、105 条队列、114 条最近播放记录及 1 个歌单。副本升级 v9 后各项数量保持一致，数据库 quick_check 为 ok。

使用副本运行实际桌面程序和打包后端，已经验证从现存的约 5 分 11 秒位置续播、后台快捷键播放/暂停、随机模式切歌与一次返回上一曲、最小化和关闭入托盘、重复启动唤回。实际 B 站音频流解析成功。真实数据库、用户曲目清单及凭据均未放入仓库；公开证据只记录验证结果。

桌面已安装的 0.1.6 客户端没有被替换。测试程序通过独立配置目录运行，数据库使用 SQLite 一致性备份副本。

结束时只读复核发现原库与备份时的哈希不同，原库仍为 v8、没有 playback_progress 表，曲目增至 1,886 条且队列/推荐数据也有变化。因此不声称原库“字节完全未变”；本轮新版本验证指向独立副本，未将副本回写或用旧备份覆盖当前原库。

旧版本没有持续写入的历史播放位置无法反推；本版本会兼容已有的保存位置，并在之后持续记录最新位置。

## 使用说明

最小化和关闭按钮都会保留后台播放。需要完全退出时，右键系统托盘中的 Bilibili Radio 图标并选择“退出”。双击图标或选择“打开播放器”可找回主窗口。托盘菜单也提供播放、切曲和定位入口。

进度写入失败会在播放器显示状态，后端恢复后自动重试。本机缓存和远程进度均不可写时会明确报告失败；不会把“尚未保存”显示成“已经保存”。

## 交付

本地安装包：`bilibili-player/src-tauri/target/release/bundle/nsis/Bilibili Radio_0.2.0_x64-setup.exe`。

SHA-256：`4a1a86f62552473708a00d2d08dc32057c83d420858ec1c9ce741d4a3f478ea9`。

功能代码已随 `2f19d17f415e74cd6129aeac2e72efb221ea7eae` 推送至 `develop/restore-main-0828`。

[GitHub Actions 本轮运行](https://github.com/ourhome-macro/Bilibili-radio/actions/runs/36572707174)全部通过：Windows、Ubuntu 自动测试与前端构建，以及 Windows 完整安装包、原生窗口规则测试、打包后端存活和数据库就绪检查。云端 [windows-x64-installer 产物](https://github.com/ourhome-macro/Bilibili-radio/actions/runs/36572707174/artifacts/11035133444)为 21,079,114 字节，内含安装包、SHA256SUMS.txt 和 build-info.json；云端与本地构建环境不同，校验云端包时使用产物内部的校验文件。

未创建版本标签、未发布 Release、未合并 main。Release 任务因普通分支推送按规则跳过。后续仅补齐本报告及证据的文档提交使用 [skip ci]，功能源码与通过云端检查的提交一致。
