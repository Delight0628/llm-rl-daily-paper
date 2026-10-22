"""PDF 论文原图提取：arxiv.org HTML 版不可用时，从论文 PDF 中提取嵌入图片。

PDF 里的嵌入图通常就是论文的框架图/原理图/实验图，比截图更适合做视频分镜。
"""

import os
import urllib.request

UA = "Mozilla/5.0 (compatible; rl-daily-digest/1.0; +pdf-figures)"

PDF_URLS = (
    "https://arxiv.org/pdf/{aid}",
    "https://www.arxiv.org/pdf/{aid}",
)


def download_pdf(arxiv_id, dest_dir, timeout=90):
    """下载论文 PDF，返回本地路径（失败返回 None）。"""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{arxiv_id}.pdf")
    if os.path.exists(dest) and os.path.getsize(dest) > 100000:
        return dest
    for tpl in PDF_URLS:
        url = tpl.format(aid=arxiv_id)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
        except Exception:
            continue
        if len(data) > 100000 and data[:5] == b"%PDF-":
            with open(dest, "wb") as f:
                f.write(data)
            return dest
    return None


def extract_figures(pdf_path, out_dir, max_count=4, min_side=300):
    """从 PDF 中提取嵌入图片（按尺寸/页码/宽高比排序，优先框架图、原理图等大图）。"""
    try:
        import fitz  # pymupdf
    except Exception:
        return []
    os.makedirs(out_dir, exist_ok=True)
    cands = []
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return []
    try:
        for page_no in range(min(len(doc), 14)):
            try:
                images = doc[page_no].get_images(full=True)
            except Exception:
                continue
            for xref, *_rest in images:
                try:
                    info = doc.extract_image(xref)
                except Exception:
                    continue
                w, h = info.get("width", 0), info.get("height", 0)
                if min(w, h) < min_side:
                    continue
                ratio = w / float(h or 1)
                if ratio < 0.25 or ratio > 4.5:
                    continue
                data = info.get("image", b"")
                if len(data) < 15000:
                    continue
                # 排序分：面积越大越优先；正文页（≥2 页）优先；偏横向的图优先
                score = (w * h) / 1e6 + (1.5 if page_no >= 1 else 0) + (0.4 if ratio >= 1.2 else 0)
                cands.append((score, page_no, xref, info, data))
    finally:
        try:
            doc.close()
        except Exception:
            pass

    cands.sort(key=lambda c: -c[0])
    out = []
    for _score, page_no, xref, info, data in cands[:max_count]:
        ext = info.get("ext", "png")
        path = os.path.join(out_dir, f"p{page_no + 1}_{xref}.{ext}")
        with open(path, "wb") as f:
            f.write(data)
        out.append(path)
    return out


def figures_from_arxiv_pdf(arxiv_id, work_dir, max_count=4):
    """一站式：下载 PDF → 提取原图。失败返回空列表。"""
    pdf = download_pdf(arxiv_id, os.path.join(work_dir, "pdf"))
    if not pdf:
        return []
    return extract_figures(pdf, os.path.join(work_dir, "figs"), max_count=max_count)
