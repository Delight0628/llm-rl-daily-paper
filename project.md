# 项目进展与运维手册（project.md）

> 每次对话开始前先阅读本文件；重大变更后同步更新。

## 一、项目目标

每日自动检索 **RL × LLM × 多智能体** 交叉领域论文，生成**中文深度讲解视频**（配音 + 口播字幕 + 论文原图），
经飞书推送到个人手机端，全链路无人工干预。

## 二、当前架构

```
prepare（检索）→ AI 撰写讲解脚本 → video（出片）→ push（飞书推送）
```

| 阶段 | 入口 | 说明 |
|---|---|---|
| 检索 | `python src\main.py prepare` | 三级来源链，产出 `state/prepared_<日期>.json` |
| 脚本 | AI 撰写 | `outputs/<日期>/scripts/<arxiv_id>.json`（故事弧分镜 + 图前后解释 + 特有术语） |
| 出片 | `python src\main.py video <日期>` | Remotion 渲染竖屏 MP4（1080×1920 → 输出 720×1280），含配音与字幕 |
| 补字幕 | `python src\main.py sub <日期>` | 仅给**已出片**视频烧录字幕（ffmpeg/libass），不重渲染 |
| 推送 | `python src\main.py push <日期>` | 1 条总览 + 逐篇视频；`--tag xxx` 用于重发 |
| 一键 | `python src\main.py daily` | prepare → 逐篇渲染**并立即推送** → 去重收尾 |

### 检索三级来源链（[retrieval.py](src/retrieval.py)）

1. arXiv API（export.arxiv.org）：429 尊重 `Retry-After`，重试 4 次，查询结果缓存 6 小时
2. OpenAlex（经 OpenCLI `papers recent`）：按发布日期过滤 + 摘要还原，无速率限制
3. arxiv.org 网页版分类最近投稿：列表页取候选 → abs 页补摘要（150s 预算）

三级全空时复用最近一期 prepared 的论文，保证日报不断档。

## 三、外部依赖

| 组件 | 位置 | 用途 |
|---|---|---|
| vibe-motion（Remotion） | `E:\vibe-motion\paper-digest` | 视频渲染引擎，`motion/Scene.jsx` 为场景实现 |
| OpenCLI | `E:\OpenCLI`（全局命令 `E:\rea\npm-global\opencli.cmd`） | 站点 CLI 化 + OpenAlex 元数据 |
| OpenCLI adapter（权威副本） | `src/adapters/{luobo,papers}` | 自动同步到 `~/.opencli/clis/`，重装后可自愈 |
| edge-tts | Python 包 | 中文神经语音配音（服务不可用时回退 Windows SAPI） |
| imageio-ffmpeg | Python 包 | 压缩、字幕烧录、抽帧 |
| pymupdf==1.24.14 | Python 包 | PDF 原图提取（2.0 版本导入即崩，锁定此版本） |
| lark-cli | 系统命令 | 飞书消息/视频/封面推送 |

## 四、关键设计决策

- **字幕**：不依赖 TTS 词级时间戳。按标点切句（≤20 视觉字符）+ 按音频时长与字符权重分配；
  正常出片由 Remotion 合成，`sub` 命令用于成片补录
- **图片叙事**：每张图三段式（图前铺垫 → 展示带图源 → 图后逐块拆解）；
  选图按图注关键词优选框架图/原理图，HTML 不可用时从 PDF 提取嵌入图
- **术语速查**：ban-list 过滤通识词（强化学习/MDP/零样本等），只留论文特有概念
- **去重**：`processed_ids.json` 统一去除 arXiv 版本后缀（`v1`/`v2`），避免带/不带版本号导致重复推送
- **增量推送**：`daily` 每渲染完一篇立即推送，不必等全部出片（长任务尾延迟曾导致漏推）
- **幂等**：飞书消息按内容/ID 哈希幂等；重发时用 `--tag` 区分键

## 五、已知问题与风险

| 问题 | 影响 | 现状 |
|---|---|---|
| 单篇视频约 10~12 分钟（19 分镜） | 渲染约 40~60 分钟/篇，4 篇近 2.5 小时 | 已改增量推送；**待决策是否缩短单篇时长** |
| 机器内存仅 7.8GB（可用约 1.4GB） | Remotion 高并发会 OOM | `video.concurrency` 默认 2，并做可用内存自适应 |
| edge-tts 服务偶发不可用（NoAudioReceived） | 新配音可能失败 | 已配音有缓存；失败回退 SAPI |
| luobo「学术搜索」页纯前端渲染 | `luobo search` 解析不到结果 | 仅返回检索 URL；需 Chrome 扩展才能渲染解析 |
| 定时会话可能"分步执行 + 后台等待"导致漏推 | 用户收不到日报 | 提示词已强调单条 `daily`；daily 已内置增量推送兜底 |

## 六、常用运维命令

```powershell
# 全链路（推荐，定时任务只用这一条）
python src\main.py daily

# 分步
python src\main.py prepare
python src\main.py video 2026-10-22 [--force]
python src\main.py sub 2026-10-22
python src\main.py push 2026-10-22 [--tag v2]

# 站点入口（萝卜图书馆）
python src\main.py portals 1
python src\main.py entry 231
```

产物结构：`outputs/<日期>/` 下含 `*.mp4`、`*_cover.png`、`*.video.json`（分镜与字幕时间轴）、
`*.ass`（补录字幕）、`scripts/`（讲解脚本）、`figures/`（论文原图）、`audio/`（分段配音）、`videos.json`（推送清单）。

## 七、变更记录（近期）

- 2026-10-17：接入 vibe-motion，图文日报升级为视频；加配音、论文原图、代码化动效
- 2026-10-18：修复 arXiv 429 与"0 篇不推送"导致的断更；加复用兜底
- 2026-10-20：新增 `daily` 一键链路；定时任务提示词改为硬性执行清单
- 2026-10-21：接入 OpenCLI（luobo 站点 CLI 化 + OpenAlex 来源）；PDF 原图提图；口播字幕
- 2026-10-22：修复去重版本后缀不一致；`daily` 改为**逐篇增量推送**；补送当日 4 篇
- 2026-10-22：`retrieval.max_papers` 改为 **1**（常态每天 1 篇）；定时任务提示词精简为单条 `daily` 执行
