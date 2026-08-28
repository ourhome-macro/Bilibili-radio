# 桌面客户端 0.1.5 重打包记录

日期：2026-08-28

## 目的

基于“下载保存不到系统下载目录”的修复，重新生成可分发给其他用户的 Windows 桌面客户端安装包。

## 版本处理

先打出过一份 `0.1.4` 修复包，但同版本会和旧安装包混淆，不适合分发。因此将桌面客户端版本提升到 `0.1.5`：

- `bilibili-player/src-tauri/tauri.conf.json`
- `bilibili-player/src-tauri/Cargo.toml`

## 打包命令

在 `bilibili-player/` 下执行：

```powershell
npm run desktop:build
```

该命令会执行：

1. `vue-tsc && vite build`
2. `deploy/build-desktop-backend.ps1`
3. `tauri build`

## 产物

- Tauri 原始输出：`E:\tool-project\bilibili-radio\bilibili-player\src-tauri\target\release\bundle\nsis\Bilibili Radio_0.1.5_x64-setup.exe`
- 已复制到系统下载目录：`D:\Downloads\Bilibili Radio_0.1.5_x64-setup.exe`
- 大小：`16766788` bytes
- SHA256：`3DCED3FA4E2540CC54180BC1A97C416A9F6D54F8D275654814F055035F4C00DE`

## 清理

删除了本次中途生成并复制到下载目录的临时旧版本包：

- `D:\Downloads\Bilibili Radio_0.1.4_x64-setup_2026-08-28-download-fix.exe`

避免误发同版本旧文件。
