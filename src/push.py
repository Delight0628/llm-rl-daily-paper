"""IM 推送模块：上传配图到飞书获取 image_key，组装 markdown 日报，
通过 lark-cli 以 user 身份直接推送给用户手机端。"""

import json
import os
import subprocess

ROOT = __file__.rsplit("\\src\\", 1)[0]
with open(ROOT + "\\config.json", encoding="utf-8") as f:
    CFG = json.load(f)


def _run(cmd, timeout=120):
    """执行命令，解析 lark-cli JSON 输出契约（ok==true 才算成功）。"""
    env = dict(os.environ, LARKSUITE_CLI_NO_UPDATE_NOTIFIER="1",
               LARKSUITE_CLI_NO_SKILLS_NOTIFIER="1")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace", env=env, cwd=ROOT)
    out = (p.stdout or "").strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"lark-cli 输出非 JSON (rc={p.returncode}): {out[:300]} {p.stderr[:300]}")
    if not data.get("ok"):
        raise RuntimeError(f"lark-cli 失败: {json.dumps(data.get('error'), ensure_ascii=False)[:500]}")
    return data


def upload_image(rel_path):
    """上传本地配图（相对路径），返回 image_key。"""
    data = _run(["lark-cli", "im", "images", "create",
                 "--data", '{"image_type":"message"}', "--file", rel_path])
    return data["data"]["image_key"]


def send_digest(md_text, date_str):
    """推送日报 markdown 文本（内含 img_ 引用）到用户飞书。"""
    import hashlib
    digest = hashlib.md5(md_text.encode("utf-8")).hexdigest()[:10]
    key = f"{CFG['push']['idempotency_prefix']}-{date_str}-{digest}"[:50]
    data = _run([
        "lark-cli", "im", "+messages-send",
        "--user-id", CFG["push"]["target_user_id"],
        "--as", CFG["push"]["identity"],
        "--markdown", md_text,
        "--idempotency-key", key,
    ], timeout=180)
    return data["data"]


if __name__ == "__main__":
    print(_run(["lark-cli", "whoami"]))
