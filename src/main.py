"""主流程编排（两阶段）：
  prepare — 检索筛选 → 去重 → 讲解草稿 → 生成配图 → 上传配图 → 落盘 prepared json
  push    — 读取已写好的日报 markdown，推送至飞书

深度中文讲解由定时会话中的 AI 基于 prepared 数据撰写后调用 push 推送，
实现 检索→解析→配图→推送 全链路无人工干预。"""

import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import retrieval
import explain
import figures
import push

STATE_FILE = os.path.join(ROOT, "state", "processed_ids.json")


def load_processed():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_processed(ids):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=2)


def prepared_path(day):
    return os.path.join(ROOT, "state", f"prepared_{day}.json")


def cmd_prepare():
    """阶段一：检索、去重、草稿、配图、上传，输出 prepared json。"""
    today = date.today().isoformat()
    print(f"== [prepare] RL 论文日报 {today} ==")

    papers = retrieval.retrieve()
    print(f"[1/3] 检索命中 {len(papers)} 篇候选")

    processed = load_processed()
    papers = [p for p in papers if p["arxiv_id"] not in processed]
    print(f"[2/3] 去重后待解析 {len(papers)} 篇")

    out_dir = os.path.join(ROOT, "outputs", today)
    os.makedirs(out_dir, exist_ok=True)
    drafts = []
    for p in papers:
        d = explain.build_draft(p)
        local_paths = figures.generate(p, out_dir)
        d["figures_local"] = [os.path.relpath(x, ROOT).replace("\\", "/") for x in local_paths]
        try:
            d["figures_key"] = [push.upload_image(r) for r in d["figures_local"]]
            print(f"  配图已上传: {p['arxiv_id']} ({len(d['figures_key'])} 张)")
        except Exception as ex:
            d["figures_key"] = []
            print(f"  [warn] 配图上传失败 {p['arxiv_id']}: {ex}")
        drafts.append(d)

    prepared = {"date": today, "out_dir": os.path.relpath(out_dir, ROOT).replace("\\", "/"),
                "papers": drafts}
    path = prepared_path(today)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prepared, f, ensure_ascii=False, indent=2)
    print(f"[3/3] prepared 已写入 {path}")
    return 0


def cmd_push(md_path, day=None):
    """阶段二：推送日报 markdown（含 img_ 配图引用）至飞书。"""
    day = day or date.today().isoformat()
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    res = push.send_digest(md, day)
    processed = load_processed()
    pp = prepared_path(day)
    if os.path.exists(pp):
        with open(pp, encoding="utf-8") as f:
            processed |= {d["arxiv_id"] for d in json.load(f)["papers"]}
        save_processed(processed)
    print(f"[push] 已推送 message_id={res.get('message_id')}")
    return 0


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "prepare":
        return cmd_prepare()
    if sys.argv[1] == "push":
        if len(sys.argv) < 3:
            print("usage: main.py push <digest.md> [YYYY-MM-DD]", file=sys.stderr)
            return 2
        return cmd_push(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    print(f"unknown command: {sys.argv[1]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
