"""检索与筛选模块：从 arXiv 全量检索 RL × LLM × 多智能体交叉领域论文，
结合顶会/顶刊关键词打分，筛选高质量目标论文。"""

import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

ROOT = __file__.rsplit("\\src\\", 1)[0]
with open(ROOT + "\\config.json", encoding="utf-8") as f:
    CFG = json.load(f)["retrieval"]

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def _fetch_arxiv(query, max_results, retries=3):
    """拉取 arXiv API，返回原始 XML 文本。"""
    url = (
        "http://export.arxiv.org/api/query?search_query="
        + urllib.parse.quote(query)
        + f"&start=0&max_results={max_results}"
        + "&sortBy=submittedDate&sortOrder=descending"
    )
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "rl-daily-digest/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(3 * (i + 1))


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
    pub = datetime.fromisoformat(entry["published"])
    age = (datetime.now(timezone.utc) - pub).total_seconds() / 86400
    if age <= 7:
        s += 10
    return s, topics


def retrieve():
    """主入口：检索 → 筛选 → 打分 → 取 Top N。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=CFG["window_days"])
    seen, papers = {}, {}
    for q in CFG["queries"]:
        try:
            xml_text = _fetch_arxiv(q, CFG["max_results_per_query"])
        except Exception as ex:
            print(f"[warn] query failed: {q}: {ex}")
            continue
        for entry in _parse_entries(xml_text, cutoff):
            if entry["arxiv_id"] not in seen:
                seen[entry["arxiv_id"]] = True
                papers[entry["arxiv_id"]] = entry
        time.sleep(3)  # arXiv API 礼貌间隔

    scored = []
    for p in papers.values():
        s, topics = score(p)
        if s > 0:
            p["score"] = round(s, 1)
            p["topics"] = sorted(topics)
            scored.append(p)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: CFG["max_papers"]]


if __name__ == "__main__":
    result = retrieve()
    print(json.dumps(result, ensure_ascii=False, indent=2))
