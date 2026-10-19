"""主流程编排（三阶段）：
  prepare — 检索筛选 → 去重 → 讲解草稿 → 生成配图 → 上传配图 → 落盘 prepared json
  video   — 调用 vibe-motion 把讲解草稿渲染成竖屏视频 + 封面（可叠加 AI 视频脚本）
  push    — 逐篇推送讲解视频 + 一条总览消息至飞书（无视频时回退图文 markdown）

深度中文讲解由定时会话中的 AI 基于 prepared 数据撰写（视频脚本/日报），
实现 检索→解析→出片→推送 全链路无人工干预。"""

import json
import os
import re
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import retrieval
import explain
import figures
import push
import video

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


def _latest_prepared_drafts():
    """取最近一期 prepared 的论文草稿（检索全空时兜底复用）。"""
    sdir = os.path.join(ROOT, "state")
    files = sorted((f for f in os.listdir(sdir) if f.startswith("prepared_")), reverse=True)
    for f in files:
        if f == f"prepared_{date.today().isoformat()}.json":
            continue
        with open(os.path.join(sdir, f), encoding="utf-8") as fp:
            papers = json.load(fp).get("papers", [])
        if papers:
            return f, papers
    return None, []


def cmd_prepare():
    """阶段一：检索、去重、草稿、配图、上传，输出 prepared json。
    去重后 0 篇新增时复用窗口内候选；检索全空时复用最近一期论文，
    保证每日日报不断档（复用模式跳过配图生成）。"""
    today = date.today().isoformat()
    print(f"== [prepare] RL 论文日报 {today} ==")

    papers = retrieval.retrieve()
    print(f"[1/3] 检索命中 {len(papers)} 篇候选")

    processed = load_processed()
    new_papers = [p for p in papers if p["arxiv_id"] not in processed]
    print(f"[2/3] 去重后待解析 {len(new_papers)} 篇")

    reused = False
    if not new_papers and papers:
        print("[fallback] 去重后 0 篇新增，复用窗口内已处理论文产出日报")
        new_papers = papers
        reused = True
    if not new_papers:
        prev_name, prev_papers = _latest_prepared_drafts()
        if not prev_papers:
            print("[error] 检索无候选且无可复用历史论文，无法产出日报", file=sys.stderr)
            return 2
        print(f"[fallback] 检索无候选，复用 {prev_name} 的论文产出日报")
        prepared = {"date": today,
                    "out_dir": os.path.relpath(os.path.join(ROOT, "outputs", today), ROOT).replace("\\", "/"),
                    "papers": prev_papers, "reused": True}
        path = prepared_path(today)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prepared, f, ensure_ascii=False, indent=2)
        print(f"[3/3] prepared 已写入 {path}（复用 {len(prev_papers)} 篇）")
        return 0

    out_dir = os.path.join(ROOT, "outputs", today)
    os.makedirs(out_dir, exist_ok=True)
    drafts = []
    for p in new_papers:
        d = explain.build_draft(p)
        if reused:
            d["figures_local"], d["figures_key"] = [], []
        else:
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
                "papers": drafts, "reused": reused}
    path = prepared_path(today)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prepared, f, ensure_ascii=False, indent=2)
    print(f"[3/3] prepared 已写入 {path}（{'复用' if reused else '新增'} {len(drafts)} 篇）")
    return 0


def cmd_video(day=None, force=False):
    """阶段二：调用 vibe-motion 渲染论文讲解视频（含配音与原图）。"""
    day = day or date.today().isoformat()
    print(f"== [video] 渲染论文讲解视频 {day} ==")
    results = video.render_day(day, force=force)
    print(f"[video] 完成 {len(results)} 条视频 → outputs/{day}/videos.json")
    return 0


def _overview_markdown(day):
    """从 prepared 草稿组装视频日报的总览消息（标题 + 链接清单）。"""
    pp = prepared_path(day)
    papers = []
    if os.path.exists(pp):
        with open(pp, encoding="utf-8") as f:
            papers = json.load(f).get("papers", [])
    lines = [f"# 论文视频日报 | RL × LLM × 多智能体 ({day})",
             "",
             f"今日精选 **{len(papers)}** 篇论文，逐篇讲解视频见下方消息：", ""]
    for i, d in enumerate(papers, 1):
        lines.append(f"{i}. [{d['title']}]({d['link']})")
    return "\n".join(lines)


def cmd_push(md_path, day=None):
    """阶段三：推送至飞书。有讲解视频则逐篇推视频 + 总览消息，
    否则回退为图文 markdown 推送。"""
    day = day or date.today().isoformat()
    manifest = os.path.join(ROOT, "outputs", day, "videos.json")
    videos = []
    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as f:
            videos = json.load(f).get("videos", [])

    if videos:
        res = push.send_digest(_overview_markdown(day), day)
        print(f"[push] 总览消息 message_id={res.get('message_id')}")
        for v in videos:
            res = push.send_video(v["video"], v["cover"], day, v["arxiv_id"])
            print(f"[push] 视频已推送 {v['arxiv_id']} message_id={res.get('message_id')}")
    elif md_path:
        with open(md_path, encoding="utf-8") as f:
            md = f.read()
        res = push.send_digest(md, day)
        print(f"[push] 已推送 message_id={res.get('message_id')}")
    else:
        print(f"[push] 无视频也无 digest markdown，跳过（{day}）", file=sys.stderr)
        return 2

    processed = load_processed()
    pp = prepared_path(day)
    if os.path.exists(pp):
        with open(pp, encoding="utf-8") as f:
            processed |= {d["arxiv_id"] for d in json.load(f)["papers"]}
        save_processed(processed)
    return 0


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "prepare":
        return cmd_prepare()
    if sys.argv[1] == "video":
        args = [a for a in sys.argv[2:] if a != "--force"]
        return cmd_video(args[0] if args else None, force="--force" in sys.argv[2:])
    if sys.argv[1] == "push":
        args = sys.argv[2:]
        md_path, day = None, None
        if args and re.fullmatch(r"\d{4}-\d{2}-\d{2}", args[0]):
            day = args[0]
        elif args:
            md_path = args[0]
            day = args[1] if len(args) > 1 else None
        return cmd_push(md_path, day)
    print(f"unknown command: {sys.argv[1]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
