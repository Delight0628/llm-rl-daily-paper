"""通俗讲解模块：基于论文摘要做结构化拆解（问题/方法/结论/术语速查），
生成中文讲解草稿。定时会话中的 AI 可在此草稿基础上进一步润色为口语化讲解。"""

import json
import re

ROOT = __file__.rsplit("\\src\\", 1)[0]
with open(ROOT + "\\config.json", encoding="utf-8") as f:
    CFG = json.load(f)["retrieval"]

# 术语表：英文术语 → 通俗中文解释（讲解配图共用知识点）
GLOSSARY = {
    "reinforcement learning": "强化学习：让模型在‘尝试—获得反馈—调整’中学会把任务做得更好",
    "rlhf": "RLHF：用人类偏好当奖励信号来训练模型，让它说的话更贴合人的期望",
    "ppo": "PPO：一种稳健的策略优化算法，训练时既要有进步又不能跑偏太远",
    "grpo": "GRPO：简化版策略优化，用组内多次采样的相对好坏代替单独训练价值模型",
    "reward model": "奖励模型：给模型输出打分的‘评委’，把模糊的好坏偏好变成可计算的分数",
    "policy": "策略：模型的‘行事规则’，输入情境输出该采取什么行动",
    "multi-agent": "多智能体：多个 AI 分工协作，像一个小组共同完成复杂任务",
    "llm agent": "LLM 智能体：以大模型为大脑，能自主规划、调工具、连续行动的 AI",
    "chain-of-thought": "思维链：让模型一步步写出推理过程再给答案，像打草稿一样提升正确率",
    "alignment": "对齐：让 AI 的行为和人类的真实意图、价值观保持一致",
    "exploration": "探索：不只用熟悉的做法，还愿意尝试新策略以发现更优解",
    "collaboration": "协作：智能体之间交换信息、互相配合以完成单个 AI 难以完成的任务",
    "self-improvement": "自我提升：模型通过自身产生的反馈迭代改进，而非仅依赖外部数据",
    "reasoning": "推理：模型进行多步逻辑思考、分析和得出结论的能力",
    "fine-tuning": "微调：在通用大模型基础上用特定数据继续训练，使其适配具体场景",
    "benchmark": "基准测试：统一的‘考卷’，用来横向比较不同方法的真实水平",
    "zero-shot": "零样本：不给示例，直接让模型完成没见过的新任务",
    "curriculum": "课程学习：由易到难安排训练任务，让模型循序渐进地掌握技能",
    "planning": "规划：先想清楚步骤和目标，再按计划执行行动",
    "communication": "通信：智能体之间传递消息、共享观察与意图的机制",
}

_CUES = {
    "问题": ["however", "despite", "challenge", "problem", "limitation", "bottleneck",
             "remain difficult", "lack of", "difficult to", "struggle"],
    "方法": ["we propose", "we present", "we introduce", "our approach", "our method",
             "framework", "algorithm", "we develop", "we design", "we train"],
    "结论": ["experiments show", "results show", "we find", "achieve", "outperform",
             "state-of-the-art", "improve", "demonstrate", "superior"],
}


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _pick(sentences, cues, limit=2):
    picked = []
    for s in sentences:
        low = s.lower()
        if any(c in low for c in cues):
            picked.append(s)
        if len(picked) >= limit:
            break
    if not picked:
        picked = sentences[:limit]
    return picked


def _hits(text):
    low = text.lower()
    return [k for k in GLOSSARY if k in low]


def build_draft(paper):
    """生成结构化讲解草稿（dict）。"""
    sents = _sentences(paper["abstract"])
    terms = _hits(paper["title"] + " " + paper["abstract"])
    return {
        "arxiv_id": paper["arxiv_id"],
        "title": paper["title"],
        "link": paper["link"],
        "authors": ", ".join(paper["authors"][:6]) + (" 等" if len(paper["authors"]) > 6 else ""),
        "published": paper["published"][:10],
        "venue": paper.get("venue_text", "").strip() or "arXiv 预印本",
        "top_venue_hits": paper.get("top_venue_hits", []),
        "score": paper.get("score", 0),
        "problem": _pick(sents, _CUES["问题"]),
        "method": _pick(sents, _CUES["方法"]),
        "result": _pick(sents, _CUES["结论"]),
        "terms": [{"term": t, "explain": GLOSSARY[t]} for t in terms[:6]],
        "abstract": paper["abstract"],
    }


def to_markdown(drafts, fig_map, date_str):
    """组装成一份日报 markdown（每篇附三张配图引用）。"""
    lines = [
        f"# 每日论文精读 | RL × LLM × 多智能体 ({date_str})",
        "",
        f"今日从全网公开学术资源中筛选出 **{len(drafts)}** 篇高质量交叉领域论文，逐篇通俗解读如下。",
        "",
    ]
    for i, d in enumerate(drafts, 1):
        venue = d["venue"]
        badge = f" | 🏆 命中顶会/顶刊: {', '.join(d['top_venue_hits'])}" if d["top_venue_hits"] else ""
        lines += [
            f"## {i}. {d['title']}",
            "",
            f"- **来源**: {venue}{badge} | {d['published']} | 综合评分 {d['score']}",
            f"- **作者**: {d['authors']}",
            f"- **原文**: {d['link']}",
            "",
            "### 一句话说人话",
            "",
            f"> {' '.join(d['problem'])} 本文针对这一痛点给出新的解决思路：{' '.join(d['method'])}",
            "",
            "**论文想解决的问题**",
            "",
            *[f"- {s}" for s in d["problem"]],
            "",
            "**核心做法**",
            "",
            *[f"- {s}" for s in d["method"]],
            "",
            "**主要结论**",
            "",
            *[f"- {s}" for s in d["result"]],
            "",
        ]
        if d["terms"]:
            lines += ["**关键知识点速查**", ""]
            lines += [f"- **{t['term']}**：{t['explain']}" for t in d["terms"]]
            lines += [""]
        imgs = fig_map.get(d["arxiv_id"], [])
        for label, img in zip(["交叉领域定位", "技术逻辑流程", "应用场景全景"], imgs):
            lines.append(f"![{label}]({img})")
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    demo = {
        "arxiv_id": "2501.00001", "title": "Demo RL LLM multi-agent",
        "link": "https://arxiv.org/abs/2501.00001", "authors": ["A", "B"],
        "published": "2026-10-13", "venue_text": "NeurIPS 2025",
        "abstract": "Large language model agents struggle with long-horizon planning. "
                    "We propose a multi-agent reinforcement learning framework. "
                    "Experiments show our method achieves state-of-the-art results.",
        "score": 95, "top_venue_hits": ["NeurIPS"],
    }
    print(json.dumps(build_draft(demo), ensure_ascii=False, indent=2))
