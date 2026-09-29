# Bilibili Radio

**Bilibili Radio** 是一个面向 B 站音乐内容的桌面播放器。

它可以将 B 站视频、多 P 视频、收藏夹和用户自建歌单统一整理成可播放的音乐曲目集合，并提供播放队列、我喜欢、UP 主主页、悬浮歌词和规则推荐等功能。

构建与测试入口见 [项目说明](bilibili-radio/README.md)，当前配置与历史记录见 [文档索引](bilibili-radio/doc/README.md)。GitHub Actions 自动测试和验证 Windows 安装包；仅推送匹配版本的 `v*` 标签时发布无签名安装包到 Release。

## Features

* 搜索 B 站视频并播放音频
* 支持 B 站多 P 视频展开为独立曲目
* 支持播放队列、我喜欢、最近播放
* 支持本地歌单 / Track Collection 管理
* 支持 B 站收藏夹浏览与搜索
* 支持 UP 主主页、稿件列表、时间排序和热度排序
* 支持悬浮歌词、歌词字号与显示区域调整
* 支持音频音质选择：

  * 自动
  * 64K
  * 132K
  * 192K
  * Dolby
  * Hi-Res
* 支持倍速播放，并尽量保持变速不变调
* 支持私人标签
* 支持基于规则的首页推荐

## Track Collection

Bilibili Radio 内部使用统一的曲目集合模型。

```ts
interface Collection {
  id: string
  title: string
  cover?: string

  sourceType:
    | 'user-created'
    | 'bilibili-multipage'
    | 'bilibili-favorite'

  sourceBvid?: string | null
  tracks: Track[]
}
```

多 P 视频不会被当成一个包含多个分 P 的“虚假单曲”播放，而是会被展开为多个独立 `Track`。

```ts
interface Track {
  id: string
  bvid: string
  cid: number

  title: string
  cover?: string

  owner?: string
  ownerMid?: number | null

  duration?: number

  startTime?: number
  endTime?: number
}
```

通过统一的 `Track` 模型，可以支持：

* B 站单 P 视频
* B 站多 P 中的某一个 P
* 一个 P 中的时间片段
* 多个 BV 混合组成的虚拟专辑
* 用户自定义歌单

播放层最终只消费：

```text
Collection -> Track[]
Queue      -> Track[]
```

从而避免针对单 P、多 P、收藏夹等来源分别维护不同的播放逻辑。

## Recommendation


## Player

播放器支持：

* 播放 / 暂停
* 上一首 / 下一首
* 循环播放
* 随机播放
* 播放队列
* 音质选择
* 倍速播放
* 悬浮歌词控制

多 P 视频加入播放队列时，会先展开为多个独立曲目：

```text
BV Multi-Page Video
        ↓
P1 -> Track
P2 -> Track
P3 -> Track
...
        ↓
    Play Queue
```

因此切换分 P 本质上和切换普通歌曲一致。

## Audio Quality

当前音频流支持以下偏好：

| 音质     | Bilibili Audio ID |
| ------ | ----------------: |
| 64K    |           `30216` |
| 132K   |           `30232` |
| 192K   |           `30280` |
| Dolby  |           `30250` |
| Hi-Res |           `30251` |

同时支持：

```text
Auto
```

作为默认音质策略。

如果用户选择的音质在当前视频中不可用，播放器会自动回退到可用音频流。

Dolby 和 Hi-Res 只有在 B 站接口实际返回对应音频流时才可用。


## Playback Speed

播放器支持倍速播放。

底层使用浏览器音频播放能力调整 `playbackRate`，并尽量启用 `preservesPitch`，降低倍速播放时产生明显音调变化的问题。

## Desktop Lyrics

悬浮歌词支持：

* 显示当前歌词
* 播放 / 暂停
* 上一首 / 下一首
* 字号调整
* 显示区域随字号缩放
* 锁定 / 解锁位置
* 关闭悬浮歌词

悬浮歌词中的播放控制会同步到主播放器，而不是创建新的独立播放器实例，避免多个音频实例同时播放。

## UP Profile

点击歌曲中的 UP 主名称，可以进入对应的 UP 主主页。

主页主要展示：

* UP 主头像
* UP 主名称
* 简介
* 稿件列表

稿件支持：

* 按发布时间排序
* 按热度排序
* 分页 / 滚动加载

UP 主页面只保留与播放器内容浏览相关的信息，不尝试复刻完整的 B 站个人空间。

## Private Tags

用户可以给歌曲添加私人标签，用于本地整理和推荐。

标签支持：

* 预设标签
* 自定义标签
* 最多 4 个字

例如：

```text
治愈
燃
通勤
写代码
```

私人标签只用于本地内容管理和推荐逻辑。

## Development

### 安装依赖

```bash
npm install
```

### Frontend Type Check

```bash
npm run type-check
```

### Build Frontend

```bash
npm run build
```

### Run Backend Tests

```bash
python -m unittest discover -s tests
```

### Build Desktop App

```bash
npm run desktop:build
```

## 技术栈

* Vue 3
* TypeScript
* Pinia
* Tauri
* Python
* Flask
* SQLite

## Notes
* release里有安装包。
* Bilibili Radio 仅用于个人音乐播放与内容整理。
* 音频流和视频信息来自 B 站接口返回结果。
* Dolby 和 Hi-Res 只有在接口实际返回对应音频流时才可用。

