# 下载不到系统下载目录问题修复

日期：2026-08-28

## 现象

桌面端点击下载后，文件没有进入 Windows 资源管理器显示的系统“下载”目录。

## 根因

有两个问题叠加：

1. 前端桌面运行时判断只识别 `tauri:` 和 `tauri.localhost`。Tauri 开发态常见页面地址是 `http://localhost:3000`，会被误判成普通 Web 页面，从而走浏览器 `<a download>` 分支，而不是调用后端本机落盘接口。
2. 后端默认下载目录用 `USERPROFILE/Downloads` 拼接。这个路径不是 Windows 的真实 Known Folder；如果用户把下载目录迁移到 `D:\Downloads`、OneDrive 或其他位置，文件就会保存到错误位置。

## 修复

- `bilibili-player/src/desktop/runtime.ts`
  - 在 `initializeDesktopRuntime()` 成功调用 Tauri `desktop_backend_endpoint` 后记录桌面运行时状态。
  - 导出统一的 `isDesktopRuntime()`，同时兼容 Tauri 生产地址、Tauri 内部对象和开发态 invoke 成功场景。
- `bilibili-player/src/stores/playerStore.ts`
  - 下载分支改用统一的 `isDesktopRuntime()`，确保桌面端调用 `/api/downloads/track`。
- `bilibili-player/src/components/DesktopLyricsBridge.vue`
- `bilibili-player/src/views/DesktopLyricsOverlayView.vue`
  - 复用统一桌面运行时判断，避免同类误判继续扩散。
- `py-radio/app.py`
  - `DOWNLOADS_DIR` 环境变量仍为最高优先级。
  - Windows 下优先通过 `SHGetKnownFolderPath(FOLDERID_Downloads)` 获取系统下载目录。
  - Known Folder 获取失败时再回退到 `USERPROFILE/Downloads`。
- `py-radio/tests/test_desktop_runtime.py`
  - 增加下载目录解析测试。

## 本机验证

- `python -c "import app; print(app._downloads_dir())"` 输出：`D:\Downloads`
- `python -m pytest tests/test_desktop_runtime.py`：9 passed
- `python -m pytest tests/test_desktop_runtime.py tests/test_services.py`：59 passed
- `python -m pytest`：98 passed
- `npm run type-check`：通过
- `npm run build`：通过

## 注意

前端 `npm run build` 会刷新 `bilibili-player/dist/` 构建产物；该目录原本就是构建输出，不属于本次修复源码范围。
