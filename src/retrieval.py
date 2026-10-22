"""检索与筛选模块：从 arXiv 全量检索 RL × LLM × 多智能体交叉领域论文，
结合顶会/顶刊关键词打分，筛选高质量目标论文。"""

import html as html_mod
import json
import os
import re
import time
import urllib.parse

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

ROOT = __file__.rsplit("\\src\\", 1)[0]
with open(ROOT + "\\config.json", encoding="utf-8") as f:
    CFG = json.load(f)["retrieval"]

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def _fetch_arxiv(query, max_results, retries=4):
    """拉取 arXiv API，返回原始 XML 文本。429 限流尊重 Retry-After，指数退避。"""
    url = (
        "http://export.arxiv.org/api/query?search_query="
        + urllib.parse.quote(query)
        + f"&start=0&max_results={max_results}"
        + "&sortBy=submittedDate&sortOrder=descending"
    )
    for i in range(retries):
        try:
            r = requests.get(url, headers={"User-Agent": "rl-daily-digest/1.0"}, timeout=45)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After") or 10 * (i + 1))
                print(f"[warn] arXiv 429 限流，{wait}s 后重试（第 {i + 1}/{retries} 次）")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.text
        except requests.exceptions.RequestException:
            if i == retries - 1:
                raise
            time.sleep(10 * (i + 1))


def _parse_entries(xml_text, cutoff):
    """解析 Atom XML，只保留 cutoff 之后提交的论文。"""
    root = ET.fromstring(xml_text)
    entries = []
    for e in root.iter(ATOM + "entry"):
        published = e.find(ATOM + "published")
        pub_dt = datetime.fromisoformat(published.text.replace("Z", "+00:00"))
        if pub_dt < cutoff:
            continue
        title = re.sub(r"\s+", " ", (e.find(ATOM + "title").text or "").strip())
        summary = re.sub(r"\s+", " ", (e.find(ATOM + "summary").text or "").strip())
        authors = [a.find(ATOM + "name").text for a in e.findall(ATOM + "author")]
        link = ""
        for l in e.findall(ATOM + "link"):
            if l.get("title") == "pdf" or l.get("type") == "application/pdf":
                link = l.get("href")
        arxiv_id = (e.find(ATOM + "id").text or "").split("/abs/")[-1]
        cats = [c.get("term") for c in e.findall(ATOM + "category")]
        # journal_ref / comment 常携带会议期刊信息（如 "NeurIPS 2025"）
        journal_ref = e.find(ARXIV_NS + "journal_ref")
        comment = e.find(ARXIV_NS + "comment")
        venue_haystack = " ".join(
            filter(None, [
                journal_ref.text if journal_ref is not None else "",
                comment.text if comment is not None else "",
            ])
        )
        entries.append({
            "arxiv_id": arxiv_id,
            "title": title,
            "abstract": summary,
            "authors": authors,
            "link": link or f"https://arxiv.org/abs/{arxiv_id}",
            "published": pub_dt.isoformat(),
            "categories": cats,
            "venue_text": venue_haystack,
        })
    return entries


def _match_topics(text):
    """返回命中的三大主题集合。"""
    t = text.lower()
    hit = set()
    for topic, kws in CFG["required_topics"].items():
        if any(kw in t for kw in kws):
            hit.add(topic)
    return hit


def score(entry):
    """交叉性 + 顶会顶刊 加权打分。"""
    text = f"{entry['title']} {entry['abstract']} {entry['venue_text']}"
    topics = _match_topics(text)
    if not {"rl", "llm"} <= topics:
        return 0.0, topics  # 必须同时涉及 RL 与 LLM
    if not topics & {"multi_agent"}:
        return 0.0, topics  # 必须涉及多智能体 / 智能体
    s = 75.0  # RL×LLM×多智能体 三交叉命中基础分
    # 顶会顶刊加权
    venue = entry["venue_text"].lower()
    top_hits = [v for v in CFG["venue_keywords"] if v.lower() in venue or v.lower() in text.lower()]
    entry["top_venue_hits"] = top_hits
    if top_hits:
        s += 20 + min(len(top_hits), 3) * 5
    # 新近性加权（7 天内提交）
    try:
        pub = datetime.fromisoformat(entry["published"])
    except Exception:
        pub = datetime.now(timezone.utc)
    age = (datetime.now(timezone.utc) - pub).total_seconds() / 86400
    if age <= 7:
        s += 10
    return s, topics


LISTING_CATEGORIES = ("cs.LG", "cs.CL", "cs.AI", "cs.MA")
LISTING_URL = "https://arxiv.org/list/{cs}/recent"
ABS_URL = "https://arxiv.org/abs/{aid}"
CACHE_FILE = os.path.join(ROOT, "state", "arxiv_cache.json")
CACHE_TTL_SECONDS = 6 * 3600
TITLE_HINT = ("agent", "multi-agent", "reinforcement", "policy", "reward",
              "language model", "llm", "tool", "actor", "critic", "rlhf",
              "planning", "reasoning", "trajectory")
MAX_ABS_FETCH = 25
FALLBACK_BUDGET_SECONDS = 150  # 兜底阶段总时长上限，防止拖垮每日链路


def _load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception as ex:
        print(f"[warn] 缓存写入失败: {ex}")


def _strip_tags(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _parse_date(raw):
    """把 citation_date（2026/10/16 或 2026-10-16）统一成 ISO 字符串。"""
    raw = (raw or "").strip()
    if not raw:
        return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", raw)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d, tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


def _cls(name):
    """匹配 arxiv 列表页的单/双引号 class 属性。"""
    return rf"class=['\"]{name}[^'\"]*['\"]"


def _fetch_listing(cs, cutoff):
    """兜底数据源第一步：抓 arxiv.org 网页版分类最近投稿列表（仅元信息，无摘要）。"""
    url = LISTING_URL.format(cs=cs)
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; rl-daily-digest/1.0)"},
            timeout=45)
        r.raise_for_status()
    except Exception as ex:
        print(f"[warn] listing failed {cs}: {ex}")
        return []

    html = r.text
    published = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    entries = []
    for m in re.finditer(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", html, re.S):
        dt, dd = m.group(1), m.group(2)
        idm = re.search(r"/abs/([0-9]{4}\.[0-9]{4,5})(v\d+)?", dt)
        if not idm:
            continue
        aid = idm.group(1) + (idm.group(2) or "")
        tm = re.search(rf"<div {_cls('list-title')}[^>]*>(.*?)</div>", dd, re.S)
        title = _strip_tags(tm.group(1)) if tm else ""
        title = re.sub(r"^Title:\s*", "", title)
        am = re.search(rf"<div {_cls('list-authors')}[^>]*>(.*?)</div>", dd, re.S)
        authors = []
        if am:
            au = re.sub(r"^Authors?:\s*", "", _strip_tags(am.group(1)))
            authors = [a.strip() for a in au.split(",") if a.strip()]
        sm = re.search(rf"<div {_cls('list-subjects')}[^>]*>(.*?)</div>", dd, re.S)
        cats = re.findall(r"\(([a-z-]+\.[A-Z]{2})\)", sm.group(1)) if sm else []
        cm = re.search(rf"<div {_cls('list-comments')}[^>]*>(.*?)</div>", dd, re.S)
        venue_text = _strip_tags(cm.group(1)) if cm else ""
        venue_text = re.sub(r"^Comments:\s*", "", venue_text)
        if not title:
            continue
        entries.append({
            "arxiv_id": aid,
            "title": title,
            "abstract": "",
            "authors": authors,
            "link": f"https://arxiv.org/abs/{aid}",
            "published": published,
            "categories": cats,
            "venue_text": venue_text,
        })
    return entries


def _fetch_abs_meta(arxiv_id):
    """兜底数据源第二步：从 arxiv.org/abs 页补齐摘要（citation_abstract meta）。"""
    try:
        r = requests.get(
            ABS_URL.format(aid=arxiv_id),
            headers={"User-Agent": "Mozilla/5.0 (compatible; rl-daily-digest/1.0)"},
            timeout=20)
        r.raise_for_status()
    except Exception:
        return None
    html = r.text
    am = re.search(r'<meta\s+name="citation_abstract"\s+content="([^"]*)"', html, re.I)
    if not am:
        return None
    abstract = html_mod.unescape(am.group(1)).strip()
    dm = re.search(r'<meta\s+name="citation_date"\s+content="([^"]*)"', html, re.I)
    published = _parse_date(dm.group(1)) if dm else (
        datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    km = re.search(r'<meta\s+name="citation_arxiv_id"\s+content="([^"]*)"', html, re.I)
    return {
        "abstract": re.sub(r"\s+", " ", abstract),
        "published": published,
        "arxiv_id": km.group(1) if km else arxiv_id,
    }


def _retrieve_openalex():
    """第二级来源：papers/OpenAlex（经 OpenCLI），按发布日期过滤 + 摘要还原，无速率限制。
    只保留窗口内、且被 arXiv 收录的论文，保证下游（去重/原图/视频）与主链路一致。"""
    try:
        import luobo
    except Exception as ex:
        print(f"[warn] OpenCLI 模块不可用: {ex}")
        return []
    days = int(CFG.get("window_days", 3))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    upper = datetime.now(timezone.utc) + timedelta(days=1)
    out = []
    seen = set()
    for q in CFG["queries"]:
        try:
            metas = luobo.recent_paper_metadata(q, days=days, limit=20)
        except Exception as ex:
            print(f"[warn] OpenAlex 兜底失败: {ex}")
            continue
        for m in metas:
            aid = (m.get("arxiv_id") or "").strip()
            if not aid or aid in seen:
                continue
            pub = m.get("published") or ""
            try:
                pub_dt = datetime.fromisoformat(pub)
                if pub_dt < cutoff or pub_dt > upper:
                    continue
            except Exception:
                continue
            seen.add(aid)
            out.append(m)
    return out


def retrieve():
    """主入口：检索 → 筛选 → 打分 → 取 Top N。
    三级来源链：arXiv API（6h 查询缓存）→ OpenAlex（OpenCLI）→ arxiv.org 网页版列表。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=CFG["window_days"])
    seen, papers = {}, {}
    cache = _load_cache()
    now = time.time()
    api_entries = 0

    for q in CFG["queries"]:
        cached = cache.get(q)
        if cached and now - cached.get("ts", 0) < CACHE_TTL_SECONDS:
            print(f"[cache] 命中缓存，跳过请求: {q[:46]}")
            for e in cached.get("entries", []):
                if e["arxiv_id"] not in seen:
                    seen[e["arxiv_id"]] = True
                    papers[e["arxiv_id"]] = e
                    api_entries += 1
            continue
        try:
            xml_text = _fetch_arxiv(q, CFG["max_results_per_query"])
            entries = _parse_entries(xml_text, cutoff)
            cache[q] = {"ts": now, "entries": entries}
            for e in entries:
                if e["arxiv_id"] not in seen:
                    seen[e["arxiv_id"]] = True
                    papers[e["arxiv_id"]] = e
                    api_entries += 1
        except Exception as ex:
            print(f"[warn] query failed: {q}: {ex}")
            continue
        time.sleep(3)  # arXiv API 礼貌间隔
    _save_cache(cache)

    if api_entries == 0:
        print("[fallback] arXiv API 无可用结果，尝试 OpenAlex（OpenCLI）兜底")
        oa = _retrieve_openalex()
        for e in oa:
            if e["arxiv_id"] and e["arxiv_id"] not in seen:
                seen[e["arxiv_id"]] = True
                papers[e["arxiv_id"]] = e
                api_entries += 1
        print(f"[fallback] OpenAlex 补齐 {api_entries} 篇 arXiv 论文")

    scored = _score_papers(papers)

    if not scored:
        print("[fallback] 仍无命中，改用 arxiv.org 网页版最近投稿兜底")
        pool = []
        for cs in LISTING_CATEGORIES:
            for e in _fetch_listing(cs, cutoff):
                if e["arxiv_id"] in seen:
                    continue
                low = e["title"].lower()
                strength = (sum(2 for k in ("multi-agent", "reinforcement learning",
                                           "rlhf", "agentic", "llm agent") if k in low)
                            + sum(1 for k in TITLE_HINT if k in low))
                if strength == 0:
                    continue
                e["_strength"] = strength
                pool.append(e)
        pool.sort(key=lambda x: -x["_strength"])
        fetched = 0
        deadline = time.time() + FALLBACK_BUDGET_SECONDS
        for e in pool:
            if fetched >= MAX_ABS_FETCH or time.time() > deadline:
                break
            meta = _fetch_abs_meta(e["arxiv_id"])
            if not meta or not meta.get("abstract"):
                continue
            fetched += 1
            e.update({k: v for k, v in meta.items() if k != "arxiv_id"})
            e.pop("_strength", None)
            seen[e["arxiv_id"]] = True
            papers[e["arxiv_id"]] = e
            time.sleep(0.6)
        print(f"[fallback] 网页版兜底补齐 {fetched} 篇摘要（候选池 {len(pool)} 篇）")
        scored = _score_papers(papers)

    return scored[: CFG["max_papers"]]


def _score_papers(papers):
    """主题三交叉过滤 + 打分排序。"""
    scored = []
    for p in papers.values():
        s, topics = score(p)
        if s > 0:
            p["score"] = round(s, 1)
            p["topics"] = sorted(topics)
            scored.append(p)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


if __name__ == "__main__":
    result = retrieve()
    print(json.dumps(result, ensure_ascii=False, indent=2))
