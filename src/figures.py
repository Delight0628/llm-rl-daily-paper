"""配图模块：为每篇论文生成辅助讲解附图（PNG）。
图1 交叉领域定位图、图2 技术逻辑流程图、图3 应用场景象限图。"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Circle
import numpy as np
import os

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

C_RL, C_LLM, C_MA = "#4C6EF5", "#12B886", "#F59F00"
C_BG, C_TEXT = "#FFFFFF", "#343A40"


def _shorten(text, n=40):
    return text if len(text) <= n else text[: n - 1] + "…"


def fig_landscape(paper, out_path):
    """图1：RL / LLM / 多智能体 三圆交叉定位图，标注论文立足点。"""
    fig, ax = plt.subplots(figsize=(7.2, 5.2), dpi=150)
    fig.patch.set_facecolor(C_BG)
    circles = [
        (0.0, 0.35, C_RL, "强化学习 RL"),
        (1.0, 0.35, C_LLM, "大语言模型 LLM"),
        (0.5, -0.45, C_MA, "多智能体 Multi-Agent"),
    ]
    for x, y, c, label in circles:
        ax.add_patch(Circle((x, y), 1.05, alpha=0.22, facecolor=c, edgecolor=c, lw=2))
        dy = -1.45 if y < 0 else 1.30  # 下方圆的标签放圆外下方，避免压边
        ax.text(x, y + dy, label, ha="center", fontsize=12, fontweight="bold", color=c)
    # 论文立足点（三圆中心）
    ax.plot(0.5, 0.08, marker="*", markersize=26, color="#E03131", zorder=5)
    ax.annotate(
        _shorten(paper["title"], 46),
        xy=(0.5, 0.08), xytext=(0.5, -1.75),
        ha="center", fontsize=9.5, color=C_TEXT,
        bbox=dict(boxstyle="round,pad=0.45", fc="#FFF3BF", ec="#F59F00", lw=1.4),
        arrowprops=dict(arrowstyle="->", color="#E03131", lw=1.6),
    )
    ax.set_xlim(-1.4, 2.4); ax.set_ylim(-2.3, 1.8)
    ax.axis("off")
    ax.set_title("论文交叉领域定位", fontsize=14, fontweight="bold", color=C_TEXT, pad=10)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=C_BG, bbox_inches="tight")
    plt.close(fig)


def fig_pipeline(paper, out_path):
    """图2：RL × LLM × 多智能体 典型技术逻辑流程图。"""
    fig, ax = plt.subplots(figsize=(9.2, 4.2), dpi=150)
    fig.patch.set_facecolor(C_BG)
    stages = [
        ("多智能体\n任务环境", "#D0BFFF", "#7048E8"),
        ("LLM 智能体\n策略 / 推理", "#B2F2BB", "#2F9E44"),
        ("协作 / 通信\n消息交互", "#FFEC99", "#E8590C"),
        ("奖励信号\n评估反馈", "#A5D8FF", "#1971C2"),
        ("策略更新\nRL 优化", "#FFC9C9", "#E03131"),
    ]
    w, h, gap = 1.5, 1.05, 0.45
    total = len(stages) * w + (len(stages) - 1) * gap
    x0 = (9.0 - total) / 2
    for i, (label, fc, ec) in enumerate(stages):
        x = x0 + i * (w + gap)
        ax.add_patch(FancyBboxPatch((x, 1.0), w, h, boxstyle="round,pad=0.06",
                                    facecolor=fc, edgecolor=ec, lw=2))
        ax.text(x + w / 2, 1.0 + h / 2, label, ha="center", va="center",
                fontsize=10.5, fontweight="bold", color=C_TEXT)
        if i < len(stages) - 1:
            ax.add_patch(FancyArrowPatch((x + w, 1.0 + h / 2), (x + w + gap, 1.0 + h / 2),
                                         arrowstyle="-|>", mutation_scale=22,
                                         color="#495057", lw=2))
    # 回环：奖励 → 智能体（学习反馈）
    ax.add_patch(FancyArrowPatch(
        (x0 + 3 * (w + gap) + w / 2, 1.0), (x0 + 1 * (w + gap) + w / 2, 1.0),
        connectionstyle="arc3,rad=0.42", arrowstyle="-|>",
        mutation_scale=20, color="#1971C2", lw=1.8, linestyle="--"))
    ax.text(total / 2 + x0, 0.28, "奖励反馈驱动的策略迭代（核心：LLM 输出被 RL 信号持续优化）",
            ha="center", fontsize=10, color="#1971C2")
    ax.set_xlim(-0.3, 9.0); ax.set_ylim(0, 2.5)
    ax.axis("off")
    ax.set_title(f"技术逻辑：{_shorten(paper['title'], 52)}",
                 fontsize=12.5, fontweight="bold", color=C_TEXT, pad=8)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=C_BG, bbox_inches="tight")
    plt.close(fig)


def fig_scenarios(paper, out_path):
    """图3：应用场景象限图（落地难度 × 通用性）。"""
    fig, ax = plt.subplots(figsize=(6.8, 5.2), dpi=150)
    fig.patch.set_facecolor(C_BG)
    scenarios = [
        ("代码 / 工具\n智能体", 0.62, 0.78, C_LLM),
        ("多智能体\n协作决策", 0.74, 0.62, C_MA),
        ("对话对齐\n(RLHF)", 0.36, 0.85, C_RL),
        ("自动化科研\n(AI Scientist)", 0.86, 0.45, "#E64980"),
        ("具身 / 游戏\n多智能体", 0.80, 0.30, "#7048E8"),
        ("推理能力\n自我提升", 0.44, 0.70, "#F76707"),
    ]
    ax.axhline(0.5, color="#CED4DA", lw=1.2); ax.axvline(0.5, color="#CED4DA", lw=1.2)
    for name, x, y, c in scenarios:
        ax.scatter([x], [y], s=560, color=c, alpha=0.75, edgecolors="white", lw=2, zorder=3)
        ax.text(x, y, name, ha="center", va="center", fontsize=8.4,
                color="white", fontweight="bold", zorder=4)
    ax.set_xlim(0.1, 1.0); ax.set_ylim(0.1, 1.0)
    ax.set_xlabel("→ 落地难度", fontsize=11); ax.set_ylabel("→ 通用性 / 影响面", fontsize=11)
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_color("#ADB5BD")
    ax.set_title("应用场景全景（本论文潜在落地方向）", fontsize=13,
                 fontweight="bold", color=C_TEXT, pad=10)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=C_BG, bbox_inches="tight")
    plt.close(fig)


def generate(paper, out_dir):
    """为单篇论文生成 3 张附图，返回 PNG 路径列表。"""
    os.makedirs(out_dir, exist_ok=True)
    key = paper["arxiv_id"].replace("/", "_").replace(".", "-")
    paths = []
    for fname, fn in [("landscape", fig_landscape), ("pipeline", fig_pipeline),
                      ("scenarios", fig_scenarios)]:
        p = os.path.join(out_dir, f"{key}_{fname}.png")
        fn(paper, p)
        paths.append(p)
    return paths


if __name__ == "__main__":
    demo = {"title": "Demo: RL meets LLM Agents", "arxiv_id": "2501.00001"}
    print(generate(demo, os.path.join(os.path.dirname(__file__), "..", "outputs", "_demo")))
