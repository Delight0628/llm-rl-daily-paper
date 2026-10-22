"""OpenCLI 封装层：把 luobolibrary（萝卜图书馆）站点与内置论文检索 adapter 变成可编程接口。

用途：
  1. 站点 CLI 化（adapter 位于 ~/.opencli/clis/luobo/，源码在 E:\\OpenCLI\\clis\\luobo/）
     - luobo account  账号/会员状态
     - luobo dbs      下载与检索入口清单
     - luobo entry    解析某入口的真实地址
     - luobo search   走站点综合检索入口
  2. 稳定元数据来源（OpenCLI 内置 adapter，避开 export.arxiv.org 的 429 限流）
     - openalex search / openalex work（含摘要 + 开放获取 PDF 直链）
"""

import hashlib
import json
import os
import re
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)

OCFG = CFG.get("opencli", {})
NODE = CFG.get("video", {}).get("node", "node")
OPENCLI_JS = OCFG.get("js", "E:\\OpenCLI\\dist\\src\\main.js")
OPENCLI_GLOBAL_DIR = OCFG.get("global_dir", "E:\\rea\\npm-global")
OPENCLI_ENABLED = bool(OCFG.get("enabled", True))
OPENCLI_TIMEOUT = int(OCFG.get("timeout", 120))

LUOBO = CFG.get("luobo", {})
LUOBO_USERNAME = os.environ.get("LUOBO_USERNAME") or LUOBO.get("username", "")
LUOBO_PASSWORD = os.environ.get("LUOBO_PASSWORD") or LUOBO.get("password", "")

# adapter 权威副本（随本项目版本管理），运行时同步到 OpenCLI 的用户 adapter 目录
ADAPTERS_DIR = os.path.join(ROOT, "src", "adapters")
USER_CLIS_DIR = os.path.join(os.path.expanduser("~"), ".opencli", "clis")
CLI_BIN = "opencli.cmd" if os.name == "nt" else "opencli"

_adapters_synced = False


class OpenCLIError(RuntimeError):
    pass


def _file_hash(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def ensure_adapters(force=False):
    """把项目内 src/adapters/<site>/*.js 同步到 ~/.opencli/clis/<site>/。
    OpenCLI 升级、重装或清理用户目录后，命令可自愈恢复，不必手工重装。"""
    global _adapters_synced
    if _adapters_synced and not force:
        return
    _adapters_synced = True
    if not os.path.isdir(ADAPTERS_DIR):
        return
    for site in sorted(os.listdir(ADAPTERS_DIR)):
        src_dir = os.path.join(ADAPTERS_DIR, site)
        if not os.path.isdir(src_dir):
            continue
        dst_dir = os.path.join(USER_CLIS_DIR, site)
        os.makedirs(dst_dir, exist_ok=True)
        for name in sorted(os.listdir(src_dir)):
            if not name.endswith(".js"):
                continue
            src = os.path.join(src_dir, name)
            dst = os.path.join(dst_dir, name)
            if not os.path.exists(dst) or _file_hash(src) != _file_hash(dst):
                shutil.copy2(src, dst)


def _cli_prefix():
    """解析 opencli 调用方式：全局命令优先，其次 node + dist 入口。"""
    on_path = shutil.which("opencli")
    if on_path:
        return [on_path]
    local = os.path.join(OPENCLI_GLOBAL_DIR, CLI_BIN)
    if os.path.exists(local):
        return [local]
    if os.path.exists(OPENCLI_JS):
        return ([NODE] if os.path.isabs(NODE) else ["node"]) + [OPENCLI_JS]
    raise OpenCLIError(
        f"未找到 opencli：全局命令不在 PATH，且 {OPENCLI_JS} 不存在。"
        "请先在 E:\\OpenCLI 执行 npm install 与 npm run build。"
    )


def run(args, timeout=None):
    """执行 opencli 命令，返回 data 字段（失败抛 OpenCLIError）。"""
    if not OPENCLI_ENABLED:
        raise OpenCLIError("OpenCLI 未启用（config.json opencli.enabled=false）")
    ensure_adapters()

    env = dict(os.environ)
    node_dir = os.path.dirname(NODE) if os.path.isabs(NODE) else ""
    if node_dir and os.path.isdir(node_dir):
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
    if LUOBO_USERNAME:
        env["LUOBO_USERNAME"] = LUOBO_USERNAME
    if LUOBO_PASSWORD:
        env["LUOBO_PASSWORD"] = LUOBO_PASSWORD

    cmd = _cli_prefix() + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout or OPENCLI_TIMEOUT, env=env)
    out = (p.stdout or "").strip()
    try:
        payload = json.loads(out)
    except Exception:
        raise OpenCLIError(f"OpenCLI 输出非 JSON (rc={p.returncode}): {out[:300]}")
    if not payload.get("ok"):
        err = payload.get("error", {})
        raise OpenCLIError(f"{err.get('message', 'unknown')} ({err.get('code', '')})")
    return payload.get("data")


# ---------------- luobo 站点能力 ----------------

def luobo_account():
    """账号会员状态：{member_type, expiration, cardtime}。"""
    return run(["luobo", "account"])


def luobo_portals(entry_type=0, keyword=""):
    """下载/检索入口清单。entry_type=1 只看论文类入口。"""
    args = ["luobo", "dbs"]
    if entry_type:
        args += ["--type", str(entry_type)]
    if keyword:
        args += ["--keyword", keyword]
    return run(args)


def luobo_entry(entry_id=None, name=None):
    """解析某入口的真实地址：{id, name, url, via}。"""
    args = ["luobo", "entry"]
    if entry_id is not None:
        args += ["--id", str(entry_id)]
    if name:
        args += ["--name", name]
    return run(args)


def luobo_search(query, limit=10):
    """经站点综合检索入口检索论文。"""
    return run(["luobo", "search", query, "--limit", str(limit)])


# ---------------- 稳定元数据来源（OpenAlex） ----------------

def openalex_search(query, limit=10):
    """OpenAlex 检索（无速率限制），返回 [{id,title,year,venue,doi,url}]。"""
    return run(["openalex", "search", query, "--limit", str(limit)])


def openalex_work(work_id):
    """OpenAlex 详情：含 abstract / openAccessUrl / doi。"""
    data = run(["openalex", "work", str(work_id)])
    if isinstance(data, list):
        return data[0] if data else {}
    return data


def _iso_date(raw):
    """OpenAlex date 形如 2026-03-14，统一成带时区的 ISO 字符串。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if len(raw) == 10:
        return raw + "T00:00:00+00:00"
    return raw


def openalex_paper_metadata(query, limit=5):
    """检索 + 补摘要，返回可直接用于主题打分的元数据列表。"""
    works = openalex_search(query, limit=max(limit, 5))
    out = []
    for w in works[:limit]:
        try:
            detail = openalex_work(w.get("id", ""))
        except OpenCLIError:
            detail = {}
        if not detail:
            continue
        doi = (detail.get("doi") or w.get("doi") or "")
        arxiv_id = ""
        if "arxiv" in doi.lower():
            arxiv_id = doi.lower().split("arxiv.")[-1].strip()
        out.append({
            "arxiv_id": arxiv_id,
            "openalex_id": w.get("id", ""),
            "title": detail.get("title", ""),
            "abstract": detail.get("abstract", ""),
            "authors": [a.strip() for a in (detail.get("authors") or "").split(",") if a.strip()],
            "link": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else (detail.get("openAccessUrl") or w.get("url", "")),
            "pdf": detail.get("openAccessUrl") or "",
            "doi": doi,
            "published": _iso_date(detail.get("date")),
            "categories": [],
            "venue_text": detail.get("venue", ""),
        })
    return out


# ---------------- 带时间窗的近 N 日论文（papers adapter） ----------------

def papers_recent(query, days=3, limit=20):
    """OpenAlex 按发布日期过滤的近 N 日论文（含摘要、作者、开放获取 PDF 直链）。"""
    return run(["papers", "recent", query, "--days", str(days), "--limit", str(limit)])


ARXIV_URL_RE = r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?"


def _arxiv_id_of(doi, url, pdf, venue):
    """从 DOI / URL / PDF 直链 / 出版源里提取 arXiv 编号（多来源兜底）。"""
    doi = (doi or "").lower()
    if "arxiv" in doi:
        tail = doi.split("arxiv.")[-1].strip()
        m = re.match(r"([0-9]{4}\.[0-9]{4,5})", tail)
        if m:
            return m.group(1)
    for candidate in (url or "", pdf or ""):
        m = re.search(ARXIV_URL_RE, candidate or "", re.I)
        if m:
            return m.group(1)
    if "arxiv" in (venue or "").lower():
        return ""
    return ""


def recent_paper_metadata(query, days=3, limit=20):
    """把 papers recent 结果映射为检索模块的论文条目结构。"""
    rows = papers_recent(query, days=days, limit=limit)
    out = []
    for r in rows:
        arxiv_id = _arxiv_id_of(r.get("doi"), r.get("url"), r.get("pdf"), r.get("venue"))
        out.append({
            "arxiv_id": arxiv_id,
            "openalex_id": r.get("id", ""),
            "title": r.get("title", ""),
            "abstract": r.get("abstract", ""),
            "authors": r.get("authors") or [],
            "link": (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else r.get("url", "")),
            "pdf": r.get("pdf", ""),
            "doi": r.get("doi", ""),
            "published": _iso_date(r.get("published")),
            "categories": [],
            "venue_text": r.get("venue", ""),
        })
    return out
