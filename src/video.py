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
CONCURRENCY = int(VCFG.get("concurrency", 2))   # 渲染并发：长视频并发过高会导致 Chrome 内存溢出
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
GRAPHIC_RE = re.compile(r'<(?:img|object)\b[^>]*?(?:src|data)="([^"]+)"[^>]*>', re.I | re.S)
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

def _svg_to_png(svg_path, png_path, scale=2.5):
    """把 SVG 原图渲染成 PNG（pymupdf），失败返回 False。"""
    try:
        import fitz
        with open(svg_path, "rb") as f:
            data = f.read()
        doc = fitz.open(stream=data, filetype="svg")
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale))
        doc.close()
        pix.save(png_path)
        return os.path.exists(png_path) and os.path.getsize(png_path) > 5000
    except Exception:
        return False


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
    for m in CAPTION_RE.finditer(html):
        cap_text = TAG_RE.sub(" ", m.group(1))
        cap_text = re.sub(r"\s+", " ", cap_text).strip()
        if not cap_text or cap_text.lower().startswith("table"):
            continue
        # 图形资源位于图注之前：取最近一个（兼容 <img>/<object> 与嵌套子图结构）
        seg = html[max(0, m.start() - 4000):m.start()]
        graphics = [g for g in GRAPHIC_RE.finditer(seg)
                    if not g.group(1).startswith("data:")]
        if not graphics:
            continue
        im = graphics[-1]
        src = im.group(1)
        if "/static/" in src or "logo" in src:
            continue
        cap_show = cap_text[:110]
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
        cands.append({"score": score, "url": urljoin(page, src), "caption": cap_show})
    cands.sort(key=lambda d: -d["score"])

    out = []
    seen_srcs = set()
    for cand in cands:
        if len(out) >= max_count:
            break
        if cand["url"] in seen_srcs:
            continue
        seen_srcs.add(cand["url"])
        ext = os.path.splitext(cand["url"])[1].split("?")[0] or ".png"
        dst = os.path.join(figs_dir, f"fig{len(out) + 1}{ext}")
        p = subprocess.run(
            ["curl.exe", "-sL", "--retry", "2", "--max-time", "60",
             "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", cand["url"], "-o", dst],
            capture_output=True, timeout=120)
        if p.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 5000:
            continue
        if ext.lower() == ".svg":
            # arXiv HTML 版部分原图为 SVG（<object> 标记），渲染成 PNG 供视频使用
            png = os.path.splitext(dst)[0] + ".png"
            if not _svg_to_png(dst, png):
                continue
            os.remove(dst)
            dst = png
        out.append({"path": dst,
                    "caption": cand["caption"] or f"论文原图 {len(out) + 1}",
                    "source": "图源 · arXiv 原文（创新框架/技术原理图）"})

    if not out:
        # arXiv HTML 不可用时，从论文 PDF 提取嵌入图（框架图/原理图）
        try:
            import pdf_figs
            pdf_out = pdf_figs.figures_from_arxiv_pdf(arxiv_id, figs_dir, max_count=max_count)
            for p in pdf_out:
                out.append({"path": p,
                            "caption": "论文原图 · PDF 嵌入图",
                            "source": "图源 · 论文 PDF 提取"})
            if out:
                print(f"    [fig] HTML 版不可用，已从 PDF 提取 {len(out)} 张原图")
        except Exception as ex:
            print(f"    [warn] PDF 提图失败: {ex}")
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


CAPTION_STRONG_RE = re.compile(r"(?<=[。！？!?；;])")
CAPTION_SOFT_RE = re.compile(r"(?<=[，、,：:])")
CAPTION_MAX_LEN = 20


def _visual_len(text):
    """视觉宽度：中文按 1 计，ASCII 按 0.55 计。"""
    n = 0.0
    for ch in text:
        n += 0.55 if ord(ch) < 128 else 1.0
    return n


def _hard_cut(text, max_len):
    """按视觉宽度硬切一段（用于没有标点的超长句）。"""
    out, acc = "", 0.0
    for ch in text:
        w = 0.55 if ord(ch) < 128 else 1.0
        if acc + w > max_len and out:
            break
        out += ch
        acc += w
    return out or text[:1]


def _split_caption_lines(text, max_len=CAPTION_MAX_LEN):
    """把口播文本切成适合竖屏阅读的短句：优先句号，其次逗号，最后硬切。"""
    text = re.sub(r"[ \t\r\n]+", " ", (text or "").strip())
    if not text:
        return []
    lines = []
    for part in (p.strip() for p in CAPTION_STRONG_RE.split(text)):
        if not part:
            continue
        if _visual_len(part) <= max_len:
            lines.append(part)
            continue
        buf = ""
        for sub in (s.strip() for s in CAPTION_SOFT_RE.split(part)):
            if not sub:
                continue
            while _visual_len(sub) > max_len:
                if buf:
                    lines.append(buf)
                    buf = ""
                cut = _hard_cut(sub, max_len)
                lines.append(cut)
                sub = sub[len(cut):]
            if _visual_len(buf + sub) <= max_len:
                buf += sub
            else:
                if buf:
                    lines.append(buf)
                buf = sub
        if buf:
            lines.append(buf)
    return [l for l in lines if l]


def build_captions(text, duration):
    """按音频时长生成字幕条：字符权重分配时间，短句不闪烁。"""
    lines = _split_caption_lines(text)
    if not lines or duration <= 0:
        return []
    weights = [max(1.0, _visual_len(l)) for l in lines]
    total = sum(weights)
    span = max(0.8, float(duration))
    # 单句最短 0.9s：若按权重分配过短，则合并相邻短句
    merged, buf, buf_w = [], None, 0.0
    for line, w in zip(lines, weights):
        d = span * w / total
        if buf is not None and d < 0.9 and buf_w + d < span * 0.45:
            buf = f"{buf}{line}"
            buf_w += d
            continue
        if buf is not None:
            merged.append((buf, buf_w))
        buf, buf_w = line, d
    if buf is not None:
        merged.append((buf, buf_w))
    if not merged:
        return []

    total_w = sum(w for _, w in merged) or 1.0
    cues, t = [], 0.0
    for line, w in merged:
        d = span * w / total_w
        cues.append({"start": round(t, 2), "end": round(t + d, 2), "text": line})
        t += d
    if cues:
        cues[-1]["end"] = round(span, 2)
    return cues


def _clean_venue(raw):
    """清洗 venue 展示文本：comments 里的 URL/页数说明不适合当出版源展示。"""
    v = (raw or "").strip()
    if not v:
        return "arXiv 预印本"
    if re.search(r"https?://|project page|\bcode\b|github", v, re.I):
        # 论文 comments 里的链接说明，提取不到真实出版源时统一标注
        return "arXiv 预印本"
    if re.search(r"\d+\s*pages?|\d+\s*figures?|\d+\s*tables?", v, re.I):
        return "arXiv 预印本"
    v = re.sub(r"\s+", " ", v).strip(" ;,，。")
    return v[:26] if len(v) > 26 else v


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


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Microsoft YaHei,{fs},&H00FFFFFF,&H00FFFFFF,&H4D020B1A,&H4D020B1A,0,0,0,0,100,100,0,0,3,{pad},0,2,44,44,{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(seconds):
    """ASS 时间格式 H:MM:SS.cc"""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(props, ass_path, width=720, height=1280):
    """从 props 生成整片 ASS 字幕：分镜内时间 + 分镜起始偏移。返回字幕条数。"""
    fs = 26 if width <= 720 else 36
    pad = 10 if width <= 720 else 14
    mv = 58 if width <= 720 else 84
    lines = [ASS_HEADER.format(w=width, h=height, fs=fs, pad=pad, mv=mv)]
    offset = 0.0
    count = 0
    for seg in props.get("segments", []):
        for cue in seg.get("captions") or []:
            start = offset + float(cue.get("start", 0))
            end = offset + float(cue.get("end", 0))
            text = str(cue.get("text", "")).replace("\n", "\\N").strip()
            if not text or end <= start:
                continue
            lines.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{text}")
            count += 1
        offset += float(seg.get("durationSeconds") or 0)
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return count


def burn_captions(video_abs, ass_path, out_abs=None):
    """用 ffmpeg/libass 把 ASS 字幕烧录进已有视频（画面不重渲染，规避内存瓶颈）。"""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(f"未安装 imageio-ffmpeg，无法烧录字幕: {exc}")
    out_abs = out_abs or (video_abs + ".sub.mp4")
    work_dir = os.path.dirname(video_abs)
    p = subprocess.run(
        [ff, "-y", "-i", os.path.basename(video_abs),
         "-vf", f"ass={os.path.basename(ass_path)}",
         "-c:v", "libx264", "-crf", "23", "-preset", "veryfast",
         "-c:a", "copy", os.path.basename(out_abs)],
        cwd=work_dir, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=3600)
    if p.returncode != 0 or not os.path.exists(out_abs):
        raise RuntimeError(f"字幕烧录失败: {(p.stderr or '')[-400:]}")
    return out_abs


def regenerate_props(day):
    """仅重建某日全部分镜 props（含字幕时间轴），不渲染视频。"""
    prepared_path = os.path.join(ROOT, "state", f"prepared_{day}.json")
    with open(prepared_path, encoding="utf-8") as f:
        prepared = json.load(f)
    out_dir = os.path.join(ROOT, "outputs", day)
    scripts_dir = os.path.join(out_dir, "scripts")
    results = []
    for draft in prepared.get("papers", []):
        script = None
        script_path = _find_in_outputs(day, "scripts", f"{draft['arxiv_id']}.json")
        if script_path:
            with open(script_path, encoding="utf-8") as f:
                script = json.load(f)
        print(f"  [props] {draft['arxiv_id']}")
        results.append(render_paper(draft, out_dir, script, day, props_only=True))
    return results


def caption_video(day, width=720, height=1280):
    """给某日已出片视频补烧录口播字幕，返回处理结果列表。"""
    manifest = os.path.join(ROOT, "outputs", day, "videos.json")
    with open(manifest, encoding="utf-8") as f:
        videos = json.load(f).get("videos", [])
    out = []
    for v in videos:
        video_abs = os.path.join(ROOT, v["video"])
        props_path = os.path.join(ROOT, "outputs", day, f"{v['arxiv_id']}.video.json")
        if not (os.path.exists(video_abs) and os.path.exists(props_path)):
            print(f"  [skip] 缺少视频或 props: {v['arxiv_id']}")
            continue
        with open(props_path, encoding="utf-8") as f:
            props = json.load(f)
        if not any(seg.get("captions") for seg in props.get("segments", [])):
            print(f"  [warn] {v['arxiv_id']} props 无字幕数据（需先重跑 video 阶段生成字幕时间轴）")
            continue
        ass_path = os.path.join(ROOT, "outputs", day, f"{v['arxiv_id']}.ass")
        n = build_ass(props, ass_path, width=width, height=height)
        print(f"  [sub] {v['arxiv_id']} 字幕 {n} 条，烧录中 ...")
        tmp = burn_captions(video_abs, ass_path)
        os.replace(tmp, video_abs)
        ensure_push_size(video_abs)
        out.append({"arxiv_id": v["arxiv_id"], "captions": n,
                    "size_mb": round(os.path.getsize(video_abs) / 1024 / 1024, 1)})
        print(f"  [sub] {v['arxiv_id']} 完成 {out[-1]['size_mb']}MB")
    return out


def _available_memory_gb():
    """返回 (可用物理内存GB, 可用提交内存GB)，用于渲染前判断并发。"""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return 0.0, 0.0
        return st.ullAvailPhys / 1024 ** 3, st.ullAvailPageFile / 1024 ** 3
    except Exception:
        return 0.0, 0.0


def pick_concurrency():
    """按当前可用内存选择渲染并发：内存紧张时降到 1，避免 Chrome 渲染 OOM。"""
    avail_phys, avail_commit = _available_memory_gb()
    if avail_phys <= 0:
        return CONCURRENCY
    if avail_phys < 1.2 or avail_commit < 1.5:
        return 1
    if avail_phys < 2.5:
        return min(CONCURRENCY, 2)
    if avail_phys < 4.0:
        return min(CONCURRENCY, 3)
    return CONCURRENCY


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


def render_paper(draft, out_dir, script=None, day=None, props_only=False):
    """渲染单篇论文讲解视频 + 封面图（含配音与图叙事），返回产物相对路径。
    props_only=True 时只生成 props（含字幕时间轴），不渲染视频。"""
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
            seg_dur = round(min(max(dur + TAIL_SECONDS, 4.0), MAX_SEGMENT_SECONDS), 2)
            seg["durationSeconds"] = seg_dur
            # 字幕时间轴不得超过分镜时长（超长语音被截断时避免字幕串段）
            seg["captions"] = build_captions(text, min(dur, seg_dur))
        else:
            seg["audio"] = None
            seg.pop("audioSeconds", None)
            seg["durationSeconds"] = 0  # 由时间轴启发式兜底
            seg["captions"] = []
        print(f"    [tts] seg{i + 1} {seg['kind']} {seg['durationSeconds']}s "
              f"字幕 {len(seg.get('captions', []))} 条")

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
            "venue": _clean_venue(draft.get("venue", "")),
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

    if props_only:
        rel = lambda p: os.path.relpath(p, ROOT).replace("\\", "/")
        return {"arxiv_id": arxiv_id, "title": draft["title"],
                "video": rel(video_abs), "cover": rel(cover_abs)}

    conc = pick_concurrency()
    avail_phys, avail_commit = _available_memory_gb()
    print(f"    [render] 可用内存 {avail_phys:.1f}GB / 提交余量 {avail_commit:.1f}GB → 并发 {conc}")

    def _render(c):
        # 长视频 + 低内存并发 1 时渲染可能超过 30 分钟，放宽到 90 分钟
        _run_node(["scripts/remotion-cli.mjs", "render", COMPOSITION_ID, video_abs,
                   "--codec=h264", "--pixel-format=yuv420p", "--image-format=jpeg",
                   f"--scale={RENDER_SCALE}", f"--crf={CRF}",
                   f"--concurrency={c}",
                   f"--props={props_path}"], timeout=5400)

    try:
        _render(conc)
    except RuntimeError as exc:
        if conc > 1 and "ran out of memory" in str(exc).lower():
            print("    [render] Chrome 内存不足，降为并发 1 重试")
            _render(1)
        else:
            raise
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


def render_day(day, force=False, on_paper=None):
    """渲染某日全部论文视频，写清单 outputs/<day>/videos.json。
    已有脚本/视频/封面跨日复用（复用日零渲染成本）。
    on_paper: 每完成一篇的回调（用于增量推送，避免等全部渲染完）。"""
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
            item = {"arxiv_id": arxiv_id, "title": draft["title"],
                    "video": rel(video_abs), "cover": rel(cover_abs)}
            results.append(item)
            if on_paper:
                on_paper(item)
            continue

        script = None
        script_path = _find_in_outputs(day, "scripts", f"{arxiv_id}.json")
        if script_path:
            with open(script_path, encoding="utf-8") as f:
                script = json.load(f)
            print(f"  [reuse] 复用讲解脚本 {rel(script_path)}")
        else:
            print(f"  [warn] {arxiv_id} 无中文讲解脚本，使用摘要草稿自动分镜（配音为英文摘要，可读性下降）")

        print(f"  [render] {arxiv_id} {draft['title'][:40]}...")
        item = render_paper(draft, out_dir, script, day)
        results.append(item)
        if on_paper:
            on_paper(item)

    manifest = {"date": day, "videos": results}
    manifest_path = os.path.join(out_dir, "videos.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return results
