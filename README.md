# RL × LLM × 多智能体 每日论文自动精读系统

每日定时自动检索全网公开学术资源，筛选强化学习（RL）与大语言模型、多智能体交叉领域的高质量顶会/顶刊论文，进行通俗化深度解析并生成辅助讲解配图，通过飞书推送到手机端，全链路自动化运行、无需人工干预。

## 功能特性

- **每日自动检索**：从 arXiv 等公开学术源检索近 3 天新论文，覆盖 NeurIPS、ICML、ICLR、ACL、EMNLP、AAMAS、JMLR、TPAMI 等顶会顶刊
- **智能筛选打分**：强制要求 RL × LLM × 多智能体三交叉主题命中，顶会顶刊关键词加权，取 Top 5
- **深度通俗讲解**：结构化拆解（痛点 / 核心做法 / 主要结论 / 术语速查），由 AI 撰写面向非专业读者的中文讲解
- **自动配图**：每篇论文自动生成三张讲解附图——交叉领域定位图、技术逻辑流程图、应用场景全景图
- **飞书推送**：配图上传飞书，日报以富文本消息直推个人飞书，手机端实时触达
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
│    读 prepared JSON，撰写中文通俗日报           │
│    → 写 outputs/YYYY-MM-DD/digest_push.md   │
│      （配图以 ![标题](img_xxx) 内嵌）          │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│  阶段三 push（确定性脚本）                     │
│  src/main.py push <digest_push.md>          │
│    lark-cli 以用户身份推送到个人飞书             │
│    更新 state/processed_ids.json            │
└─────────────────────────────────────────────┘
```

## 目录结构

```
.
├── config.json              # 检索词、顶会列表、主题词表、推送目标、解释器路径
├── src/
│   ├── main.py              # 主流程编排（prepare / push 两阶段）
│   ├── retrieval.py         # arXiv 检索 + 三交叉过滤 + 顶会打分
│   ├── explain.py           # 讲解草稿生成 + 术语通俗解释表
│   ├── figures.py           # matplotlib 三张辅助附图生成
│   └── push.py              # 配图上传 + lark-cli 飞书推送
├── outputs/YYYY-MM-DD/      # 每日产物：配图 PNG + 存档日报 digest.md
├── state/                   # processed_ids.json 跨日去重
│                            # prepared_YYYY-MM-DD.json 中间数据
└── requirements.txt
```

## 快速开始

### 环境依赖

- Python 3.13（`D:\Miniforge3\python.exe`，可在 config.json 修改）
- lark-cli ≥ 1.0.94（已授权 user 身份，配图上传与消息推送依赖它）
- Trae Schedule 定时任务（cron 触发，时区 Asia/Shanghai）

```powershell
pip install -r requirements.txt
```

### 手动运行

```powershell
# 阶段一：检索 → 筛选 → 草稿 → 配图 → 上传
D:\Miniforge3\python.exe src\main.py prepare

# 阶段二：基于 state\prepared_YYYY-MM-DD.json
#        写中文日报 → outputs\YYYY-MM-DD\digest_push.md

# 阶段三：推送至飞书
D:\Miniforge3\python.exe src\main.py push outputs\YYYY-MM-DD\digest_push.md YYYY-MM-DD
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
| `python` | Python 解释器绝对路径 |

## 定时任务

Trae Schedule 任务：**每日 08:00（Asia/Shanghai）** 自动执行全链路。
可通过 Trae Schedule 面板暂停 / 恢复 / 修改执行时间；同日重复触发由内容哈希幂等保护。

## 许可

MIT
