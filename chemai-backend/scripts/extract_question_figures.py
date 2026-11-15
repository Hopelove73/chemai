"""真题按题抽图（KB4c 真题库）：从解析卷 PDF 的嵌入图中为每道题抽取配图。

原理：
  · 逐页找题号起始行（original_number + "."/．/、）定位题目边界；
  · 题目区域 = 该页题号 y 以下到页底（续题到下一题号行为止）；
  · 区域内取面积最大的嵌入图（过滤 <40x40pt 的碎片），按 clip 渲染存
    figures/q_{exam_id}.jpg，回写 page_image；
  · 已有具体题图（q_ 开头或 hash.jpg 等非 page_XX.png 页图）的题不覆盖；
  · 区域内找不到够大的图 → 清空 page_image（整页回退图比无图更误导）。

用法：python scripts/extract_question_figures.py <卷JSON> <源PDF>
"""
import json
import re
import sys
from pathlib import Path

import fitz

MIN_W, MIN_H = 40, 40  # pt；小于此视为装饰/公式碎片
MIN_AREA = 6000        # pt²；过滤公式条（如 115x40 的方程式图）


def norm_num(original_number: str) -> str:
    m = re.search(r"\d+", original_number or "")
    return m.group() if m else ""


def main(paper_json: str, pdf_path: str, force: bool = False) -> int:
    base = Path(paper_json).parent
    fig_dir = base / "figures"
    doc = fitz.open(pdf_path)
    data = json.loads(Path(paper_json).read_text(encoding="utf-8"))

    # 1) 逐页扫描题号行位置：{(page_idx, y): original_number}
    starts = []  # (page_idx, y, num)
    for pi, page in enumerate(doc):
        for b in page.get_text("blocks"):
            text = (b[4] or "").strip()
            m = re.match(r"^(\d{1,2})\s*[\.．、]", text)
            if m:
                starts.append((pi, b[1], m.group(1)))
    print(f"[INFO] 识别题号行 {len(starts)} 个")

    # 2) 每题区域内的最大嵌入图
    stats = {"kept": 0, "kept_page_fallback": 0, "cleared": 0, "skipped_specific": 0, "notfound": 0}
    for q in data["questions"]:
        if force:
            cur0 = q.get("page_image") or ""
            if cur0.startswith("figures/q_"):
                (fig_dir / Path(cur0).name).unlink(missing_ok=True)
            q["page_image"] = ""
        num = norm_num(q.get("original_number", ""))
        cur = q.get("page_image") or ""
        if cur and not force and not re.match(r"^page_\d+\.png$", Path(cur).name):
            stats["skipped_specific"] += 1  # 已有按题图（q_/hash）不覆盖
            continue
        # 定位该题起始
        pos = next(((pi, y) for pi, y, n in starts if n == num), None)
        if pos is None:
            stats["notfound"] += 1
            continue
        pi0, y0 = pos
        # 下一题号行
        idx = starts.index((pi0, y0, num))
        nxt = starts[idx + 1] if idx + 1 < len(starts) else None
        # 收集区域内候选图
        best = None  # (area, page_idx, bbox)
        for pi in range(pi0, len(doc)):
            if nxt and pi > nxt[0]:
                break
            page = doc[pi]
            y_top = y0 + 5 if pi == pi0 else 0
            y_bot = nxt[1] - 5 if (nxt and pi == nxt[0]) else page.rect.height
            # 候选：位图 bbox + 矢量绘图簇（装置图/表格/坐标图多为矢量）
            rects = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
            for d in page.get_drawings():
                rects.append(fitz.Rect(d["rect"]))
            rects = [r for r in rects if r.width >= 12 and r.height >= 8]
            # 近邻合并成簇（扩 8pt 迭代归并）
            changed = True
            while changed:
                changed = False
                out = []
                for r in rects:
                    merged = False
                    for i, o in enumerate(out):
                        if fitz.Rect(o.x0 - 8, o.y0 - 8, o.x1 + 8, o.y1 + 8).intersects(r):
                            out[i] = fitz.Rect(min(o.x0, r.x0), min(o.y0, r.y0),
                                               max(o.x1, r.x1), max(o.y1, r.y1))
                            merged = True
                            changed = True
                            break
                    if not merged:
                        out.append(r)
                rects = out
            for bx in rects:
                if bx.width < MIN_W or bx.height < MIN_H or bx.width * bx.height < MIN_AREA:
                    continue
                if bx.get_area() > 0.6 * page.rect.get_area():
                    continue  # 页框/整页背景
                if bx.y1 <= y_top or bx.y0 >= y_bot:
                    continue
                area = bx.get_area()
                if best is None or area > best[0]:
                    best = (area, pi, bx)
        if best is None:
            if cur and not force:  # 原整页回退图清掉（force 时已在前面清空）
                q["page_image"] = ""
                stats["cleared"] += 1
            else:
                stats["notfound"] += 1
            continue
        area, pi, bx = best
        exam_id = q["exam_id"]
        out = fig_dir / f"q_{exam_id}.jpg"
        pad = 4
        clip = fitz.Rect(max(0, bx.x0 - pad), max(0, bx.y0 - pad),
                          min(doc[pi].rect.x1, bx.x1 + pad), min(doc[pi].rect.y1, bx.y1 + pad))
        pix = doc[pi].get_pixmap(clip=clip, dpi=150)
        pix.save(str(out))
        q["page_image"] = f"figures/{out.name}"
        stats["kept" if not cur else "kept_page_fallback"] += 1
        print(f"[OK] {exam_id} <- {out.name} ({bx.width:.0f}x{bx.height:.0f}pt, page {pi+1})")

    Path(paper_json).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[DONE] {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], "--force" in sys.argv))
