# RL × LLM × 多智能体 每日论文自动精读系统

每日定时自动检索全网公开学术资源，筛选强化学习（RL）与大语言模型、多智能体交叉领域的高质量顶会/顶刊论文，进行通俗化深度解析并生成辅助讲解配图，通过飞书推送到手机端，全链路自动化运行、无需人工干预。

## 功能特性

- **每日自动检索**：从 arXiv 等公开学术源检索近 3 天新论文，覆盖 NeurIPS、ICML、ICLR、ACL、EMNLP、AAMAS、JMLR、TPAMI 等顶会顶刊
- **智能筛选打分**：强制要求 RL × LLM × 多智能体三交叉主题命中，顶会顶刊关键词加权，取 Top 5
- **深度通俗讲解**：结构化拆解（痛点 / 核心做法 / 主要结论 / 术语速查），由 AI 撰写面向非专业读者的中文讲解脚本
- **视频化出片**：调用 vibe-motion（Remotion 渲染引擎）把讲解脚本渲染成竖屏 MP4 视频 + 封面图，论文解析从图文升级为视频
- **逐段中文配音**：edge-tts 神经语音（可配 Windows SAPI 离线回退），逐分镜自动配音并对齐画面时长
- **同步口播字幕**：把配音文本按标点切成短句，按音频时长与字符权重分配时间轴，画面底部字幕条随语音同步淡入淡出；不依赖 TTS 服务的词级时间戳，离线也能生成
- **论文原图穿插**：抓取 arXiv HTML 版论文原图，Ken Burns 运镜 + 动态虚线框展示，原图内嵌到对应分镜
- **代码化动效**：粒子场、代码芯片漂浮、kinetic 标题逐字弹入、数字跳动、评分环、要点卡片擦入、涟漪片尾
- **飞书推送**：总览消息 + 每篇论文讲解视频直推个人飞书，手机端实时触达
- **跨日去重**：已解析论文 ID 持久化，避免重复推送；消息按内容哈希幂等，防止重复发送

## 系统架构

```
每日 08:00 (Asia/Shanghai) Trae Schedule 定时任务触发
        │
        ▼
┌─────────────────────────────────────────────┐
│  阶段一 prepare（确定性脚本）                  │
│  src/main.py prepare                        │
│    1. retrieval.py  arXiv 检索 + 主题过滤     │
│                     + 顶会打分 → Top 5       │
│    2. 读 state/processed_ids.json 跨日去重    │
│    3. explain.py    术语表 + 结构化讲解草稿     │
│    4. figures.py    matplotlib 生成 3 张附图   │
│    5. push.py       附图上传飞书获取 image_key  │
│    → 写 state/prepared_YYYY-MM-DD.json      │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│  阶段二 AI 深度解析（定时会话执行）              │
│    读 prepared JSON，撰写中文深度讲解脚本        │
│    → 写 outputs/YYYY-MM-DD/scripts/         │
│      <arxiv_id>.json                        │
│    故事弧：科普→痛点→核心思想→方法→结果→        │
│    意义与局限→术语速查                          │
│    图片段带 pre/post 图前图后详细解释；          │
│    术语速查只收论文特有概念（禁收通识词）         │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│  阶段三 video（确定性脚本）                    │
│  src/main.py video YYYY-MM-DD               │
│    抓取 arXiv 原图（图注优选框架/原理图）+       │
│    逐段 edge-tts 配音（画面对齐语音）+           │
│    口播字幕时间轴（标点分句 + 字符权重）+         │
│    图片三段式叙事（图前铺垫→展示→图后拆解）      │
│    调用 vibe-motion (E:\vibe-motion\         │
│    paper-digest, Remotion) 渲染竖屏 MP4       │
│    + 封面图 → outputs/YYYY-MM-DD/*.mp4       │
│    → 写 outputs/YYYY-MM-DD/videos.json      │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│  阶段四 push（确定性脚本）                     │
│  src/main.py push YYYY-MM-DD                │
│    lark-cli 以用户身份推送总览 + 逐篇讲解视频    │
│    更新 state/processed_ids.json            │
│    （无视频时回退 push <digest_push.md> 图文）  │
└─────────────────────────────────────────────┘
```

## 目录结构

```
.
├── config.json              # 检索词、顶会列表、主题词表、推送目标、视频引擎路径
├── src/
│   ├── main.py              # 主流程编排（prepare / video / push 多阶段）
│   ├── retrieval.py         # arXiv 检索 + 三交叉过滤 + 顶会打分
│   ├── explain.py           # 讲解草稿生成 + 术语通俗解释表
│   ├── figures.py           # matplotlib 三张辅助附图生成
│   ├── video.py             # 调用 vibe-motion 渲染论文讲解视频 + 封面
│   ├── luobo.py             # OpenCLI 封装：luobo 站点 CLI + OpenAlex 稳定元数据源
│   ├── adapters/            # OpenCLI adapter 权威副本（自愈同步到 ~/.opencli/clis）
│   ├── pdf_figs.py          # 论文 PDF 原图提取（HTML 版不可用时的兜底）
│   └── push.py              # 配图上传 + lark-cli 飞书图文/视频推送
├── outputs/YYYY-MM-DD/      # 每日产物：配图 PNG、scripts/ 讲解脚本、*.mp4 视频
├── state/                   # processed_ids.json 跨日去重
│                            # prepared_YYYY-MM-DD.json 中间数据
├── requirements.txt
└── （视频引擎）E:\vibe-motion\paper-digest   # vibe-motion Remotion 工程
```

## 快速开始

### 环境依赖

- Python 3.11+（解释器路径可在 config.json 修改）
- Node.js 22+（视频渲染引擎 vibe-motion / Remotion 依赖）
- lark-cli ≥ 1.0.94（已授权 user 身份，配图上传与消息推送依赖它）
- vibe-motion 视频引擎：`E:\vibe-motion\paper-digest`（Remotion 工程，含无头浏览器）
- Trae Schedule 定时任务（cron 触发，时区 Asia/Shanghai）

```powershell
pip install -r requirements.txt
```

### 手动运行

```powershell
# 阶段一：检索 → 筛选 → 草稿 → 配图 → 上传
python src\main.py prepare

# 阶段二：基于 state\prepared_YYYY-MM-DD.json 撰写中文讲解脚本
#        → outputs\YYYY-MM-DD\scripts\<arxiv_id>.json

# 阶段三：调用 vibe-motion 渲染论文讲解视频（1080x1920 MP4 + 封面）
python src\main.py video YYYY-MM-DD

# 阶段四：推送总览 + 讲解视频至飞书
python src\main.py push YYYY-MM-DD
```

### 配置说明（config.json）

| 字段 | 说明 |
|------|------|
| `retrieval.window_days` | 检索时间窗口（天），默认 3 |
| `retrieval.max_papers` | 每日最多解析论文数，默认 5 |
| `retrieval.queries` | arXiv 检索式 |
| `retrieval.venue_keywords` | 顶会顶刊加分关键词 |
| `retrieval.required_topics` | RL / LLM / 多智能体 三主题判定词表 |
| `push.target_user_id` | 飞书收件人 open_id |
| `video.app_dir` | vibe-motion 视频引擎工程路径 |
| `video.node` | Node.js 解释器绝对路径 |
| `video.width` / `video.height` | 视频分辨率，默认 1080×1920 竖屏 |
| `video.render_scale` / `video.crf` | 输出缩放与压缩质量（控制飞书上传体积） |
| `video.tts.voice` | 配音音色（edge-tts 神经语音，如 zh-CN-XiaoxiaoNeural） |
| `python` | Python 解释器绝对路径 |
| `opencli.js` | 本地 OpenCLI 运行时入口（E:\OpenCLI\dist\src\main.js） |
| `luobo.username` / `luobo.password` | 萝卜图书馆站点账号（环境变量 LUOBO_USERNAME / LUOBO_PASSWORD 优先） |

## 论文来源与 OpenCLI 集成

检索走**三级来源链**，任一级可用即停，全部不可用时复用历史论文：

| 级别 | 来源 | 说明 |
|---|---|---|
| 一级 | arXiv API（export.arxiv.org） | 主源；429 自动重试（尊重 Retry-After）+ 6 小时查询缓存 |
| 二级 | OpenAlex（经 OpenCLI `papers recent`） | 无速率限制；按发布日期过滤 + 摘要还原 + 开放获取 PDF 直链 |
| 三级 | arxiv.org 网页版分类最近投稿 | 列表页取候选 → abs 页补摘要（150s 时间预算） |

**萝卜图书馆（luobolibrary.cn）已 CLI 化**，并提供 OpenAlex 时间窗检索：

| 命令 | 作用 |
|---|---|
| `luobo account` | 账号/会员状态（当前：中英文会员 · 永久权限） |
| `luobo dbs` | 下载/检索入口清单（知网 / 维普 / 万方 / IEEE / Springer / 标准下载等） |
| `luobo entry` | 解析某入口的真实地址（`Auth.php` → `rk.php` → 跟随跳转） |
| `luobo search` | 站点综合检索入口 |
| `papers recent` | OpenAlex 按发布日期取论文（含摘要还原、开放获取 PDF 直链） |

### 集成机制

1. **adapter 权威副本在本项目**：`src/adapters/{luobo,papers}/*.js`（随项目版本管理）
2. **运行时自愈同步**：`luobo.py` 每次调用前比对哈希，把 adapter 同步到
   `~/.opencli/clis/<site>/`；OpenCLI 升级/重装/清理后无需手工重装
3. **调用方式自动解析**（`config.json` → `opencli`）：
   全局 `opencli` 命令（PATH）→ `E:\rea\npm-global\opencli.cmd`
   → `node` + `E:\OpenCLI\dist\src\main.js`
4. **无需浏览器扩展**：以上 adapter 均为 `browser:false` 的纯 HTTP 实现，
   不依赖 Chrome Browser Bridge（扩展只在浏览器自动化类命令中才需要）

### 前置条件（一次性）

```powershell
cd E:\OpenCLI
npm install          # 装依赖 + postinstall（fetch-adapters / tsc 构建）
npm run build        # 编译到 dist/
npm link             # 生成全局 opencli 命令（当前在 E:\rea\npm-global）
```

### 使用

```powershell
opencli luobo dbs --type 1                      # 全局命令（PATH 已含 npm-global）
opencli papers recent "multi-agent RL LLM" --days 3

# 项目内置快捷命令
python src\main.py portals 1
python src\main.py entry 231
```

> 完整浏览器自动化（如需要浏览器登录的站点）还需安装 Chrome 扩展：
> `chrome://extensions/` → 开发者模式 → 加载已解压的扩展程序 → `E:\OpenCLI\extension`。
> 本项目的 adapter 不依赖它。

**论文原图双通道**：① arXiv HTML 版按图注优选框架图/原理图；
② HTML 不可用时自动下载论文 PDF，用 pymupdf 提取嵌入图（按面积/页码/宽高比优选框架图）。

## 容错约定

- **arXiv API 限流/超时**：单条查询自动重试 4 次（429 尊重 Retry-After，指数退避）；检索结果按查询缓存 6 小时，同日重试不再重复请求被打限流
- **API 完全不可用**：自动降级到 OpenAlex（经 OpenCLI，按发布日期过滤、带摘要），再不行则用 arxiv.org 网页版分类最近投稿列表（cs.LG / cs.CL / cs.AI / cs.MA）兜底解析
- **去重后 0 篇新增**：复用窗口内已处理论文产出日报（跨日复用已有讲解脚本与视频，零渲染成本），照常推送
- **检索完全无候选**：复用最近一期 prepared 的论文产出日报，保证每日推送不断档
- 复用日跳过配图生成与上传；推送仍走视频日报（总览 + 逐篇讲解视频）

## 定时任务

Trae Schedule 任务：**每日 08:00（Asia/Shanghai）** 自动执行全链路。
可通过 Trae Schedule 面板暂停 / 恢复 / 修改执行时间；同日重复触发由内容哈希幂等保护。

### 执行清单（定时会话按此执行，不要自行判断跳过）

```powershell
# 首选：一条命令跑完全链路（prepare → video → push）
python src\main.py daily
```

或分步执行（等价）：

```powershell
python src\main.py prepare                  # 阶段一：检索（自带缓存+兜底+复用）
# 若 prepared 中有新增论文（reused=false），AI 先撰写中文深度讲解脚本：
#   outputs\<YYYY-MM-DD>\scripts\<arxiv_id>.json
#   （title_zh + 故事弧分镜 + figure 段 pre/post + 论文特有术语）
python src\main.py video <YYYY-MM-DD>       # 阶段二：出片（复用日零渲染）
python src\main.py sub <YYYY-MM-DD>         # 可选：给已出片视频补烧录口播字幕
python src\main.py push <YYYY-MM-DD>        # 阶段三：推送（总览 + 逐篇视频）
```

**字幕说明**

- 正常出片时，字幕由 Remotion 渲染层直接合成（`SubtitleBar` + 分镜字幕时间轴），无需额外处理
- `main.py sub` 用于**已渲染好的成片补字幕**：只重建 props（不重渲染画面）+ ffmpeg/libass 烧录，
  规避「长视频 + 高并发」导致的 Chrome 内存溢出
- 字幕时间轴不依赖 TTS 词级时间戳：按标点切句 + 按音频时长与字符权重分配，离线也可生成

**硬性约定**

- 即使检索命中 0 篇、全部走复用，**也必须执行 video 与 push**，日报照常送达
- 禁止因为"没有新论文"而跳过推送；禁止因为"内容与昨日重复"而自行放弃
- 推送是否执行由脚本决定，不由会话判断

## 许可

MIT
