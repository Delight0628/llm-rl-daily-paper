"""视频生成模块：调用 vibe-motion（Remotion 渲染引擎，见 config.json video.app_dir）
把论文讲解草稿渲染为竖屏 MP4 视频 + 封面图：
  - 逐段中文配音（edge-tts 神经语音，离线回退 Windows SAPI），画面对齐语音时长
  - 论文原图穿插：按图注关键词优选「创新框架图 / 技术原理图」，并支持自绘框架图
  - 图片三段式叙事：图前铺垫 → 图片展示（带图源标注）→ 图后逐块拆解
  - 术语速查只保留论文特有术语（ban-list 过滤通识词）

讲解脚本（推荐）：outputs/YYYY-MM-DD/scripts/<arxiv_id>.json
  {"title_zh": "中文标题",
   "sections": [
     {"kind": "primer|problem|insight|method|result|caveat|terms",
      "heading": "...", "bullets": ["..."], "items": [{"term","explain"}],
      "narration": "配音全文（缺省用要点拼接）"},
     {"kind": "figure", "heading": "...", "caption": "Fig.x ...",
      "figure": "auto:1 | self:pipeline | self:landscape | self:scenarios",
      "pre":  {"heading": "图前铺垫", "bullets": [...]},
      "post": {"heading": "图后拆解", "bullets": [...]},
      "narration_pre": "...", "narration": "...", "narration_post": "..."}
   ]}
  若未提供脚本，则用 explain.build_draft 的结构化草稿自动组装分镜。"""

import json
import os
import re
import shutil
import subprocess
from urllib.parse import urljoin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)

VCFG = CFG.get("video", {})
APP_DIR = VCFG.get("app_dir", "E:\\vibe-motion\\paper-digest")
NODE = VCFG.get("node", "node")
COMPOSITION_ID = VCFG.get("composition_id", "Motion30fps")
WIDTH = int(VCFG.get("width", 1080))
HEIGHT = int(VCFG.get("height", 1920))
BRAND_TEXT = VCFG.get("brand_text", "RL × LLM × 多智能体 · 每日论文精读")
TTS = VCFG.get("tts", {})
TTS_VOICE = TTS.get("voice", "zh-CN-XiaoxiaoNeural")
TTS_RATE = TTS.get("rate", "+8%")
TTS_ENGINE = TTS.get("engine", "edge-tts")   # edge-tts | sapi
FIGURES_MAX = int(VCFG.get("figures_max", 4))
TAIL_SECONDS = float(VCFG.get("tail_seconds", 1.4))
MAX_SEGMENT_SECONDS = 60.0                   # 讲透为准，单段最长 60s
RENDER_SCALE = VCFG.get("render_scale", 0.667)  # 输出缩放（0.667 → 720x1280，控制飞书上传体积）
CRF = int(VCFG.get("crf", 28))                  # H.264 质量系数，越小越清晰
PYTHON = CFG.get("python", "python")

OUTRO_LINES = [
    "关注我 · RL × LLM × 多智能体论文视频精读",
    "每天 08:00 自动推送",
]

# 术语速查 ban-list：通识词不进「论文特有术语」模块（仅匹配术语名本身）
TERM_BAN_RE = re.compile(
    r"^(强化学习|reinforcement\s*learning|马尔可夫决策过程|MDP|零样本|zero.?shot|"
    r"少样本|few.?shot|微调|fine.?tuning|对齐|多智能体|multi.?agent|GRPO|PPO|RLHF|"
    r"监督微调|SFT|奖励模型|reward.?model|基准测试|大语言模型|思维链|"
    r"LLM.?as.?a.?judge|提示词工程|智能体|大模型)$",
    re.I,
)

STAT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(个百分点|分|%|％|倍|万|亿)")
FIG_BLOCK_RE = re.compile(r"<figure[^>]*>(.*?)</figure>", re.S)
IMG_TAG_RE = re.compile(r'<img[^>]+src="([^"]+)"[^>]*>')
CAPTION_RE = re.compile(r"<figcaption[^>]*>(.*?)</figcaption>", re.S)
FIGSIZE_RE = re.compile(r'width="(\d+)"[^>]*height="(\d+)"')
TAG_RE = re.compile(r"<[^>]+>")

FRAMEWORK_KW = ("framework", "overview", "architecture", "teaser", "illustrat")
METHOD_KW = ("approach", "method", "paradigm", "pipeline", "system", "design")
DOWNRANK_KW = ("ablation", "appendix", "supplement", "qualitative", "case study",
               "attention map", "t-sne", "error", "failure", "recovery", "curve",
               "training", "improvement")
FIG_NUM_RE = re.compile(r"figure\s*(\d+)", re.I)

SELF_FIGS = {
    "self:pipeline": ("_pipeline.png", "自绘 · 技术路线梳理"),
    "self:landscape": ("_landscape.png", "自绘 · 交叉领域定位"),
    "self:scenarios": ("_scenarios.png", "自绘 · 应用场景全景"),
}


def _clean_link(link):
    return (link or "").replace("https://", "").replace("http://", "")


def _clean_tts_text(text):
    text = re.sub(r"https?://\S+", "链接见简介", text or "")
    text = re.sub(r"[*_#`>|]", "", text)
    return text.strip()


def _audio_duration(path):
    if path.endswith(".wav"):
        import wave
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    from mutagen import File as MutagenFile
    a = MutagenFile(path)
    if a is None or not a.info:
        raise RuntimeError(f"无法读取音频时长: {path}")
    return float(a.info.length)


# ---------------- TTS 配音 ----------------

def _tts_edge(text, out_mp3):
    cmd = [PYTHON, "-m", "edge_tts", "--voice", TTS_VOICE,
           "--rate", TTS_RATE, "--text", text, "--write-media", out_mp3]
    p = subprocess.run(cmd, timeout=180, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode == 0 and os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 500


def _tts_sapi(text, out_wav):
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$v = $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo } | "
        "Where-Object { $_.Name -match 'Huihui|Yaoyao|Chinese|zh-CN' } | Select-Object -First 1; "
        "if ($v) { $s.SelectVoice($v.Name) }; "
        f"$s.SetOutputToWaveFile('{out_wav}'); "
        f"$s.Speak([Console]::In.ReadToEnd()); $s.Dispose()"
    )
    p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       input=text, timeout=180, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode == 0 and os.path.exists(out_wav) and os.path.getsize(out_wav) > 1000


def synth_narration(text, out_base):
    """合成配音，返回 (文件路径, 时长秒)。优先 edge-tts，失败回退 SAPI。"""
    text = _clean_tts_text(text)
    if not text:
        return None, 0.0
    if TTS_ENGINE != "sapi":
        mp3 = out_base + ".mp3"
        try:
            if _tts_edge(text, mp3):
                return mp3, _audio_duration(mp3)
        except Exception as e:
            print(f"    [tts] edge-tts 失败，回退 SAPI: {e}")
    wav = out_base + ".wav"
    if _tts_sapi(text, wav):
        return wav, _audio_duration(wav)
    print(f"    [tts] 配音失败，跳过：{text[:24]}...")
    return None, 0.0


# ---------------- 论文原图抓取（按图注优选框架图） ----------------

def fetch_figures(arxiv_id, figs_dir, max_count=None):
    """抓取 arXiv HTML 版论文原图，按图注关键词优选创新框架图/技术原理图。
    返回 [{"path", "caption", "source"}]，并写 meta.json 缓存。"""
    max_count = max_count or FIGURES_MAX
    base_id = arxiv_id.split("v")[0]
    page = f"https://arxiv.org/html/{arxiv_id}"
    html_path = os.path.join(figs_dir, "_page.html")
    os.makedirs(figs_dir, exist_ok=True)

    html = ""
    for url in (page, f"https://arxiv.org/html/{base_id}"):
        p = subprocess.run(
            ["curl.exe", "-sL", "--retry", "2", "--max-time", "45",
             "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", url, "-o", html_path],
            capture_output=True, timeout=90)
        if p.returncode == 0 and os.path.exists(html_path) and os.path.getsize(html_path) > 20000:
            with open(html_path, encoding="utf-8", errors="replace") as f:
                html = f.read()
            break
    if os.path.exists(html_path):
        os.remove(html_path)
    if not html:
        return []

    cands = []
    for block in FIG_BLOCK_RE.findall(html):
        im = IMG_TAG_RE.search(block)
        if not im:
            continue
        src = im.group(1)
        if "/static/" in src or "logo" in src or src.endswith(".svg"):
            continue
        cap_m = CAPTION_RE.search(block)
        cap_text = TAG_RE.sub(" ", cap_m.group(1)) if cap_m else ""
        cap_text = re.sub(r"\s+", " ", cap_text).strip()[:110]
        sz = FIGSIZE_RE.search(im.group(0))
        area = int(sz.group(1)) * int(sz.group(2)) if sz else 0
        low = cap_text.lower()
        num_m = FIG_NUM_RE.search(cap_text)
        fig_num = int(num_m.group(1)) if num_m else 99
        score = min(area / 100000.0, 5.0)
        score += sum(8 for kw in FRAMEWORK_KW if kw in low)
        score += sum(3 for kw in METHOD_KW if kw in low)
        score -= sum(6 for kw in DOWNRANK_KW if kw in low)
        score += 3.0 if fig_num == 1 else (1.5 if fig_num == 2 else 0.0)
        if cap_text:
            score += 1.5
        cands.append({"score": score, "url": urljoin(page, src), "caption": cap_text})
    cands.sort(key=lambda d: -d["score"])

    out = []
    for i, cand in enumerate(cands[:max_count]):
        ext = os.path.splitext(cand["url"])[1].split("?")[0] or ".png"
        dst = os.path.join(figs_dir, f"fig{i + 1}{ext}")
        p = subprocess.run(
            ["curl.exe", "-sL", "--retry", "2", "--max-time", "60",
             "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", cand["url"], "-o", dst],
            capture_output=True, timeout=120)
        if p.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 5000:
            out.append({"path": dst,
                        "caption": cand["caption"] or f"论文原图 {i + 1}",
                        "source": "图源 · arXiv 原文（创新框架/技术原理图）"})
    return out


def load_or_fetch_figures(arxiv_id, figs_dir):
    meta_path = os.path.join(figs_dir, "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            return json.load(f)
    figs = fetch_figures(arxiv_id, figs_dir)
    os.makedirs(figs_dir, exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(figs, f, ensure_ascii=False, indent=2)
    return figs


def _resolve_figure(ref, figs_meta, out_dir, arxiv_id):
    """解析图引用：auto:N 取抓取原图；self:* 取自绘框架图。"""
    ref = (ref or "").strip()
    if ref.startswith("self:"):
        suffix, caption = SELF_FIGS.get(ref, ("_pipeline.png", "自绘 · 框架梳理"))
        path = os.path.join(out_dir, arxiv_id.replace(".", "-") + suffix)
        if os.path.exists(path):
            return {"path": path, "caption": caption, "source": "图源 · 本频道自绘整理"}
        return None
    if ref.startswith("auto:"):
        try:
            idx = int(ref.split(":", 1)[1]) - 1
        except ValueError:
            idx = 0
    else:
        idx = 0
    return figs_meta[idx] if 0 <= idx < len(figs_meta) else None


# ---------------- 分镜组装 ----------------

def extract_stats(bullets, limit=2):
    """从要点里抽取数字亮点，生成数据跳动分镜条目。"""
    stats = []
    for b in bullets:
        m = STAT_RE.search(b)
        if not m:
            continue
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        suffix = m.group(2).replace("％", "%")
        label = re.sub(r"\s+", "", b)[:16]
        stats.append({"value": value, "suffix": suffix, "label": label})
        if len(stats) >= limit:
            break
    return stats


def _join_narration(*parts):
    return "。".join(p.strip("。 ") for p in parts if p and p.strip("。 "))


def _bullet_narration(heading, bullets, items=None):
    parts = [heading] + list(bullets or [])
    for it in items or []:
        parts.append(f"{it.get('term', '')}，{it.get('explain', '')}")
    return _join_narration(*parts)


def _filter_terms(items):
    """术语速查只留论文特有术语，过滤通识词。"""
    kept = []
    for it in items or []:
        term = (it.get("term") or "").strip()
        if not term or TERM_BAN_RE.search(term):
            continue
        kept.append({"term": term, "explain": (it.get("explain") or "").strip()})
    return kept


def _expand_figure_block(sec, fig_meta):
    """图片段展开为三段式：图前铺垫 → 图片展示 → 图后拆解。"""
    pre = sec.get("pre") or {}
    post = sec.get("post") or {}
    pre_bullets = pre.get("bullets", [])
    post_bullets = post.get("bullets", [])

    lead = {
        "kind": "figure_lead",
        "heading": pre.get("heading", "看图之前：这张图在讲什么"),
        "bullets": pre_bullets,
        "narration": sec.get("narration_pre") or _bullet_narration(pre.get("heading", ""), pre_bullets),
    }
    show = {
        "kind": "figure",
        "heading": sec.get("heading", "论文核心图"),
        "caption": sec.get("caption") or fig_meta["caption"],
        "source": fig_meta["source"],
        "figures": [fig_meta["path"]],
        "narration": sec.get("narration") or f"就是这张图，{sec.get('caption') or fig_meta['caption']}。",
    }
    explain = {
        "kind": "figure_explain",
        "heading": post.get("heading", "图里讲了什么"),
        "bullets": post_bullets,
        "narration": sec.get("narration_post") or _bullet_narration(post.get("heading", ""), post_bullets),
    }
    return [lead, show, explain]


def build_segments(draft, script, figs_meta, out_dir):
    """组装完整分镜序列（含 figure 三段式展开与术语过滤）。"""
    arxiv_id = draft["arxiv_id"]
    sections = (script or {}).get("sections") if script else None
    scripted = sections is not None
    segments = []

    title_zh = (script or {}).get("title_zh") or draft.get("title_zh") or ""
    intro_fig = figs_meta[0] if figs_meta else None
    segments.append({
        "kind": "intro",
        "heading": draft["title"],
        "figures": [intro_fig["path"]] if intro_fig else [],
        "narration": _join_narration(
            "每日论文精读",
            f"今天这篇论文是：{title_zh or draft['title']}",
        ),
    })

    if not sections:
        sections = [
            {"kind": "problem", "heading": "痛点：论文想解决什么问题",
             "bullets": draft.get("problem", [])},
            {"kind": "method", "heading": "核心做法",
             "bullets": draft.get("method", [])},
            {"kind": "result", "heading": "主要结论",
             "bullets": draft.get("result", [])},
        ]
        if draft.get("terms"):
            sections.append({"kind": "terms", "heading": "术语速查",
                             "items": draft["terms"]})

    method_fig_used = False
    for sec in sections:
        kind = sec.get("kind", "method")
        if kind in ("intro", "outro"):
            continue

        if kind == "figure":
            fig_meta = _resolve_figure(sec.get("figure"), figs_meta, out_dir, arxiv_id)
            if fig_meta:
                segments.extend(_expand_figure_block(sec, fig_meta))
            continue

        seg = {
            "kind": kind,
            "heading": sec.get("heading", ""),
            "bullets": sec.get("bullets", []),
            "items": _filter_terms(sec.get("items")) if kind == "terms" else sec.get("items", []),
            "figures": [],
            "caption": sec.get("caption", ""),
        }
        if kind in ("problem", "method", "result"):
            seg["stats"] = extract_stats(seg["bullets"])
        if not scripted and kind == "method" and not method_fig_used and len(figs_meta) > 1:
            # 自动模式：方法段内嵌第二张原图（脚本模式由 figure 块带解释展示）
            seg["figures"] = [figs_meta[1]["path"]]
            seg["caption"] = figs_meta[1]["caption"]
            seg["source"] = figs_meta[1]["source"]
            method_fig_used = True
        if kind == "terms" and not seg["items"]:
            continue  # 过滤后没有特有术语则跳过该段
        seg["narration"] = sec.get("narration") or _bullet_narration(
            seg["heading"], seg["bullets"], seg["items"])
        segments.append(seg)

    segments.append({
        "kind": "outro",
        "heading": "每天 08:00 自动推送",
        "bullets": OUTRO_LINES + [_clean_link(draft.get("link", ""))],
        "figures": [],
        "narration": "本期就到这里，关注我，每天八点自动推送最新论文精读。",
    })
    return segments


# ---------------- 渲染主流程 ----------------

def _run_node(args, timeout=1800):
    env = dict(os.environ)
    node_dir = os.path.dirname(NODE) if os.path.isabs(NODE) else ""
    if node_dir:
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
    cmd = ([NODE] if os.path.isabs(NODE) else []) + args
    p = subprocess.run(cmd, cwd=APP_DIR, env=env, timeout=timeout,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(
            f"vibe-motion 渲染失败 (rc={p.returncode}): "
            f"{(p.stdout or '')[-500:]} {(p.stderr or '')[-500:]}")
    return p


def ensure_push_size(video_abs, max_mb=18):
    """超出飞书上传上限时用 ffmpeg 二次压缩（未来出片自动适配）。"""
    if os.path.getsize(video_abs) <= max_mb * 1024 * 1024:
        return
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        print("    [compress] 未安装 imageio-ffmpeg，跳过压缩（可能超飞书上传上限）")
        return
    tmp = video_abs + ".tmp.mp4"
    for crf, w in ((30, 640), (33, 576)):
        subprocess.run(
            [ff, "-y", "-i", video_abs, "-vf", f"scale={w}:-2",
             "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
             "-b:a", "64k", tmp],
            capture_output=True, timeout=1200)
        if os.path.exists(tmp) and os.path.getsize(tmp) <= max_mb * 1024 * 1024:
            break
    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        os.replace(tmp, video_abs)
        print(f"    [compress] 压缩至 {os.path.getsize(video_abs) / 1024 / 1024:.1f}MB 以适配飞书上传")
    elif os.path.exists(tmp):
        os.remove(tmp)


def render_paper(draft, out_dir, script=None, day=None):
    """渲染单篇论文讲解视频 + 封面图（含配音与图叙事），返回产物相对路径。"""
    arxiv_id = draft["arxiv_id"]
    os.makedirs(out_dir, exist_ok=True)
    day = day or os.path.basename(out_dir)

    # 1. 论文原图（meta.json 缓存，含图注与优选排序）
    figs_dir = os.path.join(out_dir, "figures", arxiv_id)
    print(f"    [fig] 加载/抓取论文原图 {arxiv_id} ...")
    figs_meta = load_or_fetch_figures(arxiv_id, figs_dir)
    print(f"    [fig] 可用原图 {len(figs_meta)} 张")

    # 2. 组装分镜（figure 三段式展开 + 术语过滤）并逐段配音
    segments = build_segments(draft, script, figs_meta, out_dir)
    audio_dir = os.path.join(out_dir, "audio", arxiv_id)
    os.makedirs(audio_dir, exist_ok=True)
    for i, seg in enumerate(segments):
        text = seg.pop("narration", "")
        seg["stats"] = seg.get("stats", [])
        base = os.path.join(audio_dir, f"seg{i + 1}")
        cached = next((base + ext for ext in (".mp3", ".wav") if os.path.exists(base + ext)), None)
        if cached:
            audio_path, dur = cached, _audio_duration(cached)
        else:
            audio_path, dur = synth_narration(text, base)
        if audio_path:
            seg["audio"] = os.path.basename(audio_path)
            seg["audioSeconds"] = round(dur, 2)
            seg["durationSeconds"] = round(
                min(max(dur + TAIL_SECONDS, 4.0), MAX_SEGMENT_SECONDS), 2)
        else:
            seg["audio"] = None
            seg.pop("audioSeconds", None)
            seg["durationSeconds"] = 0  # 由时间轴启发式兜底
        print(f"    [tts] seg{i + 1} {seg['kind']} {seg['durationSeconds']}s")

    # 3. 资源注入 public/（Remotion staticFile 引用）
    asset_dir = os.path.join(APP_DIR, "public", "assets", arxiv_id)
    if os.path.isdir(asset_dir):
        shutil.rmtree(asset_dir, ignore_errors=True)
    os.makedirs(asset_dir, exist_ok=True)
    for seg in segments:
        for j, fig in enumerate(seg.get("figures", [])):
            dst = os.path.join(asset_dir, os.path.basename(fig))
            shutil.copy2(fig, dst)
            seg["figures"][j] = f"assets/{arxiv_id}/{os.path.basename(fig)}"
        if seg.get("audio"):
            src = os.path.join(audio_dir, seg["audio"])
            shutil.copy2(src, os.path.join(asset_dir, seg["audio"]))
            seg["audio"] = f"assets/{arxiv_id}/{seg['audio']}"

    # 4. 写 props 并渲染
    props = {
        "videoWidth": WIDTH,
        "videoHeight": HEIGHT,
        "brandText": BRAND_TEXT,
        "paper": {
            "title": draft["title"],
            "titleZh": (script or {}).get("title_zh") or draft.get("title_zh") or "",
            "arxivId": arxiv_id,
            "venue": draft.get("venue", "arXiv 预印本"),
            "published": draft.get("published", ""),
            "authors": draft.get("authors", ""),
            "score": draft.get("score", 0),
            "link": draft.get("link", ""),
        },
        "segments": segments,
    }
    props_path = os.path.join(out_dir, f"{arxiv_id}.video.json")
    with open(props_path, "w", encoding="utf-8") as f:
        json.dump(props, f, ensure_ascii=False, indent=2)

    video_abs = os.path.join(out_dir, f"{arxiv_id}.mp4")
    cover_abs = os.path.join(out_dir, f"{arxiv_id}_cover.png")

    _run_node(["scripts/remotion-cli.mjs", "render", COMPOSITION_ID, video_abs,
               "--codec=h264", "--pixel-format=yuv420p", "--image-format=jpeg",
               f"--scale={RENDER_SCALE}", f"--crf={CRF}",
               f"--props={props_path}"])
    _run_node(["scripts/remotion-cli.mjs", "still", COMPOSITION_ID, cover_abs,
               "--frame=240", f"--props={props_path}"])
    ensure_push_size(video_abs)

    rel = lambda p: os.path.relpath(p, ROOT).replace("\\", "/")
    return {
        "arxiv_id": arxiv_id,
        "title": draft["title"],
        "video": rel(video_abs),
        "cover": rel(cover_abs),
    }


def _find_in_outputs(day, *parts):
    """在 outputs/<day>/ 与其他历史日期目录中查找已产出文件（跨日复用）。"""
    out_root = os.path.join(ROOT, "outputs")
    dirs = [os.path.join(out_root, day)]
    if os.path.isdir(out_root):
        for d in sorted(os.listdir(out_root), reverse=True):
            p_dir = os.path.join(out_root, d)
            if d != day and os.path.isdir(p_dir):
                dirs.append(p_dir)
    for d in dirs:
        p = os.path.join(d, *parts)
        if os.path.exists(p):
            return p
    return None


def render_day(day, force=False):
    """渲染某日全部论文视频，写清单 outputs/<day>/videos.json。
    已有脚本/视频/封面跨日复用（复用日零渲染成本）。"""
    prepared_path = os.path.join(ROOT, "state", f"prepared_{day}.json")
    with open(prepared_path, encoding="utf-8") as f:
        prepared = json.load(f)

    out_dir = os.path.join(ROOT, "outputs", day)
    os.makedirs(out_dir, exist_ok=True)
    rel = lambda p: os.path.relpath(p, ROOT).replace("\\", "/")
    results = []
    for draft in prepared.get("papers", []):
        arxiv_id = draft["arxiv_id"]
        video_abs = _find_in_outputs(day, f"{arxiv_id}.mp4")
        cover_abs = _find_in_outputs(day, f"{arxiv_id}_cover.png")
        if not force and video_abs and cover_abs:
            print(f"  [skip] 复用已产出视频 {arxiv_id} ({rel(video_abs)})")
            results.append({"arxiv_id": arxiv_id, "title": draft["title"],
                            "video": rel(video_abs), "cover": rel(cover_abs)})
            continue

        script = None
        script_path = _find_in_outputs(day, "scripts", f"{arxiv_id}.json")
        if script_path:
            with open(script_path, encoding="utf-8") as f:
                script = json.load(f)
            print(f"  [reuse] 复用讲解脚本 {rel(script_path)}")

        print(f"  [render] {arxiv_id} {draft['title'][:40]}...")
        results.append(render_paper(draft, out_dir, script, day))

    manifest = {"date": day, "videos": results}
    manifest_path = os.path.join(out_dir, "videos.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return results
