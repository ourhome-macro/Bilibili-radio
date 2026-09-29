# 0.2.0 本机替换与正式发布

日期：2026-09-29。用户本轮要求替换桌面旧版，并发布 GitHub Release。

## 为什么上一轮没有 Release

发布策略已确定为只在推送 v* 标签时发布。上一轮推送的是功能分支，没有版本标签，因此 Release 任务按规则跳过。功能分支的 Windows 安装包和自动测试已通过。

## 本机替换结果

- 升级前备份了完整 0.1.6 安装目录和应用数据，并额外使用 SQLite backup API 生成一致性数据库快照。
- 用已验证 SHA-256 的 0.2.0 无签名 NSIS 安装包执行原目录更新，安装器退出码为 0。
- 安装位置保持 `D:\test\Bilibili Radio`，桌面快捷方式仍指向该目录中的主程序，安装文件版本及卸载注册信息更新为 0.2.0。
- 已启动安装后的实际程序，主窗口正常出现，打包后端 `/health/ready` 成功；再次启动立即退出第二个进程，保留一个桌面实例。
- 原库从 schema v8 升至 v9，quick_check 为 ok；升级前后均为 1,886 条曲目、105 条队列、114 条最近播放记录、1 个歌单。验证没有启动音频播放。
- 本机备份保存在仓库外，数据库和用户凭据没有提交到 Git。

## 正式发布

版本标签 `v0.2.0` 指向已通过 CI 的功能提交 `2f19d17f415e74cd6129aeac2e72efb221ea7eae`。该提交后的分支更新只有文档，发布的业务代码与本机验证版本一致。

[正式发布流水线](https://github.com/ourhome-macro/Bilibili-radio/actions/runs/36574372512)全部成功：Windows/Ubuntu 自动测试、Windows 安装包构建、原生窗口规则、打包后端检查和发布任务均通过。

[v0.2.0 正式 Release](https://github.com/ourhome-macro/Bilibili-radio/releases/tag/v0.2.0)已于北京时间 2026-09-29 21:28:50 公开，并设为 Latest，包含以下三个附件：

- `Bilibili.Radio_0.2.0_x64-setup.exe`（21,092,027 字节）；
- `SHA256SUMS.txt`；
- `build-info.json`（记录版本、代码提交、平台及 signed: false）。

已实际下载 Release 安装包校验，SHA-256 为 `cb8f06d30a0c2338bf4bc623ba7345f41af8827dc3f75a4a1f1a8d68b105df16`。云端使用 Python 3.12，本机安装使用已验证的 Python 3.10 构建，业务源码和应用版本一致，两个独立构建的二进制哈希不同。

## 发布后校验修正

首次下载验收发现 GitHub 将安装包文件名中的空格规范化为点号，但原 SHA256SUMS.txt 仍写有空格，因此按文件名批量校验会失败。安装包本身的哈希正确。

本次只纠正了 Release 的校验清单，安装包、构建信息、标签和业务源码未更改；旧清单已保存到本机备份。重新下载公开的 SHA256SUMS.txt，已确认它引用真实附件名且哈希与下载后的文件一致。

工作流现已在生成校验文件之前将发布产物命名为 `Bilibili.Radio_<版本>_x64-setup.exe`，避免后续版本重现该问题。修正提交为 `df7b6201291c8d586324cf67abc4e456ca994e75`，actionlint、[分支 CI](https://github.com/ourhome-macro/Bilibili-radio/actions/runs/36576067266)和 [PR CI](https://github.com/ourhome-macro/Bilibili-radio/actions/runs/36576073357)均通过。

已实际下载该分支的 windows-x64-installer 产物（artifact 11037418109），确认三个附件完整，SHA256SUMS.txt 引用真实安装包文件名且哈希相符，build-info.json 对应 df7b620。验证未重新发布或覆盖 v0.2.0 安装包。结构化结果见 [交付证据](evidence-desktop-implementation-2026-09-29/release-results.json)。

仍为无代码签名的 Windows x64 安装包，未配置服务器部署或应用内自动更新。本机安装和 GitHub Release 发布是两项独立操作。

功能、根因和完整实测记录见 [0.2.0 实现与验证](desktop-playback-tray-implementation-2026-09-29.md)。
