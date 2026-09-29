# 文档索引

当前交付形式是 Windows 桌面安装包，采用无代码签名、版本标签触发 GitHub Release。先阅读当前指南；历史设计记录用于追溯，不代表当前版本已实现全部设想。

## 当前操作指南

- [项目运行、构建和测试入口](../README.md)
- [GitHub Actions CI/CD 与版本发布](github-actions-desktop-ci-cd-2026-09-29.md)
- [代码签名方案参考](windows-code-signing-guide-2026-09-29.md)：当前已选择无签名发布，暂不实施本文中的签名接入。

## 桌面和播放器历史

- [Tauri 桌面客户端与内嵌后端](windows-desktop-client-tauri-sidecar-2026-08-07.md)
- [悬浮歌词](desktop-floating-lyrics-2026-08-07.md)
- [播放队列与详情页](player-queue-and-detail-tabs.md)
- [队列抽屉布局与排序](queue-drawer-compact-sortable-2026-08-08.md)
- [多 P 曲目集合与播放音质](track-collection-playerbar-multipage-audio-quality-2026-08-08.md)
- [随机播放与歌词字号](shuffle-wrap-desktop-lyrics-font-size-2026-08-08.md)
- [循环、歌词与本地搜索](loop-lyrics-local-search-2026-08-08.md)
- [规则推荐、收藏与下载](rule-recommendations-favorites-download-2026-08-07.md)

## 后端与数据设计历史

- [后端 API 契约](backend-api-contract.md)
- [后端路线图](backend-roadmap.md)
- [多 P 播放测试记录](multi-part-playback-test-2026-07-20.md)
- [OIDC 与 B 站二维码登录的区别](auth-oidc-vs-bilibili-qr-decision-2026-07-22.md)
- [Auth0 配置历史](auth0-oidc-setup-2026-07-22.md)
- [生产优化与响应时间历史](project-production-optimization-and-response-speed-2026-07-21.md)

其他带日期的前端布局、后端实施和研究文档保留原路径，避免破坏已有链接。服务器部署、智能体研究等历史记录不是当前桌面安装包发布的必需步骤。功能行为有冲突时，以当前源码、可复现测试和最新需求拆解为准。

## 文档维护规则

- 当前操作入口保持简短，新增任务报告从本索引链接。
- 诊断报告区分已复现、源码确认和待系统实测，记录源码提交及运行版本。
- 历史记录不伪装成当前功能保证；配置说明写明是否已执行、推送或发布。
- 本机备份目录、恢复过程等本地审计记录不纳入用户使用指南。
