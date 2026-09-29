# 桌面版 GitHub Actions CI/CD

日期：2026-09-29。

## 范围与选择

项目是 Windows 桌面安装包，不需要部署服务器。CI 负责测试与构建验证，CD 负责把验证通过的 Windows x64 NSIS 安装包发布到 GitHub Release。

用户已选择：**只在推送 v* 版本标签时发布**。本次没有添加定时任务、服务器部署、RabbitMQ 或自动更新客户端。

签名决策：**直接发布无签名安装包**。无需申请证书、开通签名服务或配置签名 Secrets。工作流显式传入 Tauri 的 `--no-sign`，build-info.json 保持 `signed: false`；测试、构建验证、SHA-256 校验及标签发布流程照常执行。

工作流位于实际 Git 根目录的 `.github/workflows/desktop.yml`，对应本机 `E:\tool-project\.github\workflows\desktop.yml`。应用源码位于仓库的 `bilibili-radio/` 子目录，所有工作流路径已按此设置。

## 触发规则

| 事件 | 测试 | 构建安装包 | 发布 Release |
| --- | --- | --- | --- |
| 向 main、develop/** 推送提交 | 是 | 是 | 否 |
| 提交目标为 main 的 PR | 是 | 是 | 否 |
| Actions 页面手动运行 | 是 | 是 | 否 |
| 推送与桌面版本一致的 v* 标签 | 是 | 是 | 是 |

每次构建会保存 `windows-x64-installer` Actions artifact，保留 14 天。普通分支构建可在 Actions 运行页面下载验证包；它不会自动变成正式版。

npm、pip 和 Rust 依赖启用缓存。Rust 构建缓存只允许 main 的 push 保存，PR 和版本标签构建可以读取可用缓存，以减少重复编译。

## CI 检查

测试任务分别运行在 Ubuntu 24.04 和 Windows Server 2022：

- 安装固定直接版本的 Python 依赖。
- 执行 95 项后端测试，包括字幕来源约束、缓存并发、身份与权限、会话与 CSRF、曲库、播放、推荐、下载基础行为、数据隔离和数据库迁移。
- 执行 6 项发布版本规则测试，验证标签不匹配、Cargo 清单或锁文件未同步等情况会失败。
- 校验 Tauri、Cargo.toml、Cargo.lock 的桌面版本一致。
- 使用 npm ci 安装前端依赖，执行 TypeScript/Vue 类型检查和 Vite 生产构建。

所有测试通过后，在 Windows Server 2022 上：

1. 使用固定 Rust 工具链构建；Cargo 使用 --locked，不自动改写依赖锁。
2. 调用现有 Tauri 构建入口，先构建前端，再用 PyInstaller 6.11.1 打包内嵌 Python 后端。
3. 生成 x64 NSIS `.exe` 安装包。
4. 启动打包后的后端，以临时数据目录和随机本地端口检查 `/health/live`、`/health/ready`，结束后终止本次测试创建的进程树。
5. 生成安装包 SHA-256 和包含版本、提交、平台的 build-info.json。

测试和打包后的后端启动检查都使用临时数据库，不操作用户已有曲库。GUI 点击、音频播放和实际登录不在本轮自动化覆盖范围内。

## 为何补回测试和打包脚本

8 月 28 日 main 快照删除了 py-radio/tests 和 deploy，而 Tauri 配置仍引用 deploy/build-desktop-backend.ps1。直接添加 YAML 会遇到“无测试可跑”和“打包脚本不存在”。

本次恢复了 7 个与当前源码相关的历史测试模块，并对齐当前 main 已存在的推荐规则、播放计数和 schema v8；没有恢复后来的 RabbitMQ 或下载完整性功能。后者尚未在当前基线实现，因此对应的新增测试没有混入。

同时补回唯一需要的桌面后端打包脚本。没有恢复整套服务器部署配置。测试运行器在找不到测试时会明确失败，避免空测试集被当成 CI 成功。

## 发布流程

只有 tag push 可以进入发布任务。测试与构建任务只有 contents: read，发布任务单独获得 contents: write，使用 GitHub 内置 GITHUB_TOKEN，无需额外 PAT。

发布任务下载当前运行生成的 artifact，验证 SHA-256，然后创建草稿 Release，上传全部文件，最后转为公开发布。如果中途上传失败，草稿保留，避免出现缺少安装包的公开版本。对同一标签重跑时可以修复草稿，但不会覆盖已公开版本的附件。

发布附件为：

- `Bilibili Radio_<版本>_x64-setup.exe`
- `SHA256SUMS.txt`
- `build-info.json`

正式版本标签格式为 `vX.Y.Z`。也支持显式测试版本 `vX.Y.Z-alpha.N`、`vX.Y.Z-beta.N`、`vX.Y.Z-rc.N`，自动标为预发布，不替换 Latest。

标签必须与以下三个位置一致：

- bilibili-player/src-tauri/tauri.conf.json 的 version；
- bilibili-player/src-tauri/Cargo.toml 的 package.version；
- bilibili-player/src-tauri/Cargo.lock 中 bilibili-radio-desktop 包的 version。

前端 npm 包的 1.0.0 是内部包版本，不参与桌面安装包版本校验。版本校验不替用户自动修改源码，避免标签叫 0.1.5、安装包却仍是 0.1.4。

## 首次启用与发布

工作流文件需要先提交并推送到 GitHub，才会在云端运行。建议先合并至 main，确认分支 CI 成功，再发布版本标签。若仓库组织策略限制了 Actions 或 GITHUB_TOKEN 写权限，需要在仓库/组织设置中允许这些操作。

例如要发布 0.1.5，先将三个桌面版本位置一起更新为 0.1.5，提交代码，并确保当前提交已经包含工作流，然后执行：

```powershell
git tag -a v0.1.5 -m "Bilibili Radio v0.1.5"
git push bilibili-radio v0.1.5
```

这两条命令仅为发布说明，本次未执行，未创建标签或 Release。当前源码仍保持 0.1.4。

## 本地检查

在 bilibili-radio 项目目录，使用已安装项目依赖的 Python 环境：

```powershell
python scripts/run_backend_tests.py
```

版本校验脚本使用 Python 3.11+，流水线固定为 3.12：

```powershell
py -3.12 -m unittest discover -s scripts/tests -v
py -3.12 scripts/check_desktop_version.py --tag v0.1.4
```

在 bilibili-player 目录：

```powershell
npm ci
npm run desktop:build -- --ci --no-sign --bundles nsis -- --locked
```

再回到项目目录：

```powershell
py -3.12 scripts/smoke_desktop_backend.py py-radio/dist/bilibili-radio-backend.exe
```

## 本次验证记录

- 95 项后端测试：在 Windows 的 Python 3.10 环境及全新 Python 3.12 环境均通过。
- 6 项发布版本规则测试：通过。
- actionlint 1.7.12：工作流校验通过；本地没有额外启用 shellcheck/pyflakes。
- Vue/TypeScript 检查与 Vite 生产构建：通过。
- Tauri + PyInstaller + Rust + NSIS 完整构建：通过，生成 0.1.4 x64 安装包。
- 对本次打包后端的启动、存活和数据库就绪检查：通过。
- 使用全新 Python 3.12 环境另外完成 PyInstaller 打包和后端启动检查：通过，验证流水线所选 Python 版本可以实际打包运行。
- Ubuntu 运行、GitHub 云端运行和真实 Release 发布：尚未执行，需要工作流推送后由 GitHub 执行。

本地验证安装包位于 `bilibili-player/src-tauri/target/release/bundle/nsis/Bilibili Radio_0.1.4_x64-setup.exe`，本次没有执行安装器或覆盖已安装客户端。完整本地安装包构建使用现有 Python 3.10 打包环境；Python 3.12 的后端单独在临时目录构建并验证，GitHub Windows 构建任务会在干净环境使用 Python 3.12 完成整包构建。

当前按用户选择发布无签名包。Windows 可能显示未知发布者或 SmartScreen 提示，受管理的设备也可能禁止运行；不能保证所有电脑都允许继续安装。[微软说明](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)。此流程也不等于应用内自动更新。

## 官方参考

- [Tauri GitHub Actions 构建与发布](https://v2.tauri.app/distribute/pipelines/github/)
- [GitHub Actions 工作流语法、触发和权限](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- [GitHub CLI 创建 Release 和 verify-tag](https://cli.github.com/manual/gh_release_create)

Actions 依赖使用核验过的完整提交 SHA，避免浮动标签直接改变执行代码。
