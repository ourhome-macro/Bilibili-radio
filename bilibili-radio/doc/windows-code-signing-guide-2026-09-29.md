# Windows 桌面安装包代码签名配置

日期：2026-09-29。本文是配置说明，尚未启用任何签名服务，未修改当前工作流或 Tauri 配置。

后续决定：用户已选择直接发布无签名安装包，不继续配置签名身份或服务。Actions 构建命令现已显式加入 `--no-sign`；本文保留作将来参考，不属于当前必做事项，也不再需要提供个人/公司身份或证书信息。

## 先选择签名身份和服务

Windows 公开分发使用 Authenticode 代码签名。需要受信任的代码签名证书或云签名服务；网站 HTTPS 证书和 Tauri 更新包签名密钥不能替代它。

| 路线 | 适用条件 | 对当前 GitHub Actions 的影响 |
| --- | --- | --- |
| 商业 CA 的云签名/HSM 服务 | 供应商接受发布者所在地区和个人/公司身份，完成验证并取得代码签名资格 | 适合 GitHub 托管 runner 无人值守签名；购买前确认 SDK、认证方式和自动化支持 |
| Azure Artifact Signing（原 Trusted Signing） | 必须符合微软 Public Trust 身份验证地区条件 | 支持 GitHub Actions，但不能只因有 Azure 账号就认为能签公开发行版 |
| SignPath Foundation 开源计划 | 符合其开源许可、维护、发布及项目审核要求 | 可申请免费签名，但其条款要求每次发布人工批准，不是完全无人值守 |

微软目前的 Public Trust 个人身份验证只接受美国或加拿大个人开发者；组织的支持地区范围另列。若以中国大陆个人身份申请，目前不符合该个人开发者地区条件。[微软资格要求](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart)

SignPath Foundation 提供免费开源签名，但要求项目采用认可的开源许可，并满足其其他条件；证书发布者是 SignPath Foundation，申请不保证通过。其条款也明确每次发布需要人工批准。[项目介绍](https://signpath.org/)、[申请条件](https://signpath.org/terms)

当前仓库未查到已跟踪的 LICENSE/COPYING 文件；如选择 SignPath，需要先由维护者明确项目许可，不能只凭仓库公开就判断已经符合条件。本次没有替项目选择或添加许可证。

对于没有自托管 runner 的现有部署方式，应优先确认供应商能提供云端密钥访问。不要先购买只能插 USB Token 操作的产品，再期望直接在 GitHub 托管 Windows runner 上使用。

## 项目需要签哪些文件

当前 Tauri 配置把 Python 后端 exe 作为资源装进 NSIS 安装器，因此至少需要覆盖以下项目自产文件：

1. `py-radio/dist/bilibili-radio-backend.exe`：内嵌 Python 后端。
2. `bilibili-player/src-tauri/target/release/bilibili-radio-desktop.exe`：桌面主程序。
3. `bilibili-player/src-tauri/target/release/bundle/nsis/Bilibili Radio_<版本>_x64-setup.exe`：最终安装器。

仅在最后给安装器签名，不会让内部 exe 自动获得签名。

正确的阶段顺序为：编译 Python 后端 → 签后端 → Tauri 构建并签主程序 → 把已签名文件打入 NSIS → 签最终安装器 → 验证签名 → 计算最终 SHA-256 → 上传 Release。对于所选服务不能直接参与 Tauri 构建的情况，需要按其服务支持的方式拆分签名与打包阶段，保证顺序不变。

## Tauri 如何接入

若 runner 的 Windows 证书库已经能访问该证书及其对应签名密钥，可在独立的发行配置中合并类似下列字段。示例中的证书指纹必须替换；仅导入不含密钥访问能力的公钥证书不能完成签名。

```json
{
  "bundle": {
    "windows": {
      "certificateThumbprint": "实际代码签名证书的SHA1指纹",
      "digestAlgorithm": "sha256",
      "timestampUrl": "http://timestamp.digicert.com",
      "tsp": true
    }
  }
}
```

时间戳地址与协议应按所选签名供应商要求设置。certificateThumbprint 中的 SHA1 是证书标识，并不表示使用 SHA1 文件摘要；文件签名摘要使用 SHA-256。

云服务使用专用 CLI/SDK 时，可通过 Tauri 的 `bundle.windows.signCommand` 接入。该字段支持自定义命令，`%1` 代表需要签名的文件路径。具体命令和认证变量取决于供应商，不能在尚未选定服务时编造一套通用凭据参数。[Tauri Windows 签名文档](https://v2.tauri.app/distribute/sign/windows/)

后端签名需要加在 `deploy/build-desktop-backend.ps1` 的 PyInstaller 成功之后，或等价的构建阶段中；不能默认认为 Tauri 会给所有 resources 自动签名。

## GitHub Actions 配置位置

选择并开通签名服务后，再完成这些配置：

1. 在 GitHub 仓库 Settings → Environments 创建专门的签名环境，例如 release-signing，并限制允许使用的发行标签。
2. 在该环境中配置供应商要求的认证资料：敏感凭据用 Secrets，非敏感账号/证书配置标识可用 Variables。支持 GitHub OIDC 的供应商优先使用短期身份令牌和最小签名权限。
3. 普通分支和 PR 的构建继续使用无签名模式；只有已通过 CI 的版本标签构建进入签名环境。
4. 在发行构建中安装所选供应商的签名工具，以前述顺序签后端、主程序和安装器。
5. 签名或验证失败时直接使发行任务失败，不能退回无签名包继续公开发布。
6. 必须在最后一次签名完成后重算 SHA256SUMS.txt；签名会改变文件内容。build-info.json 的 signed 状态应由成功验证结果决定，不能仅因配置了凭据就写 true。

当前 `.github/workflows/desktop.yml` 把 signed 写为 false，是对现状的准确描述。本次没有在缺少签名身份的情况下把它改成 true。

## 验证方法

在 Windows 上，对三个实际产物逐个验证，例如：

```powershell
Get-AuthenticodeSignature -LiteralPath '完整路径\Bilibili Radio_0.1.4_x64-setup.exe' |
    Format-List Status, StatusMessage, SignerCertificate, TimeStamperCertificate
```

发行门禁应检查签名有效、签名身份符合预期并带有有效时间戳；仅仅文件上存在签名信息不够。主程序和后端应在被装入安装器前完成验证。

## 对安装体验的实际作用

受信任的签名可以显示经过验证的发布者并建立信誉，但新签名应用仍可能遇到 SmartScreen 提示。微软明确说明 EV 证书不再自动绕过 SmartScreen；不应仅为“绝对不弹警告”而支付 EV 溢价。[微软 SmartScreen 说明](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)

自签名证书适合受控测试，不会自动变成所有用户机器都信任的公开发行签名。代码签名也不会自动实现客户端在线更新。

## 本次核验

- 核对了当前 Tauri 配置、本地 Tauri CLI 配置 schema、PyInstaller 配置和 Actions 构建顺序。
- 查询了 Tauri、微软、SignPath 的当前官方资料。
- 当前未提供发布者身份、地区、证书或服务信息，因此保留现有可运行的无签名流程；未购买、注册、上传签名请求或变更云端设置。
