"""四层切块（T9）：结构（H2）→ 长度修整 → 标题路径 → 账目字段。

参数为 qiuqiu 实测值（设计 §2）：MAX 600 / TARGET 450 / MIN 100；
句末标点切句，组间重叠 = 上一组末句带入下一组（约 15%）；
指纹 = sha256(source_path \n title_path \n body)，幂等与修订替换都认它。
上下文增强（LLM 位置句）是离线可选项，本期 v1 只用确定性标题路径前缀。
"""
import hashlib
import re
from dataclasses import dataclass, field

MIN_CHARS = 100
TARGET_MAX = 450
MAX_CHARS = 600
CHUNK_LIMITS = {"MIN_CHARS": MIN_CHARS, "TARGET_MAX": TARGET_MAX, "MAX_CHARS": MAX_CHARS}
_CODE = re.compile(r"M\d-[A-Z0-9-]+")


@dataclass
class ChunkDraft:
    source_path: str
    title: str
    section_title: str
    title_path: str
    section_no: int
    part_no: int
    chunk_no: int
    body: str
    content_fingerprint: str
    module: str = ""
    doc_type: str = ""
    theme: str = ""
    kp_ids: list = field(default_factory=list)


@dataclass
class ParsedDoc:
    source_path: str
    meta: dict
    chunks: list


def parse_doc(source_path: str, markdown: str, meta: dict | None = None) -> ParsedDoc:
    """切正文。meta 缺省时自行解析 frontmatter 的展示字段（校验在 validator）。"""
    if meta is None:
        m = re.match(r"^---\n([\s\S]*?)\n---\n?", markdown)
        raw = {}
        if m:
            for line in m.group(1).split("\n"):
                kv = re.match(r"^([a-z_]+):\s*(.*)$", line.strip())
                if kv:
                    raw[kv.group(1)] = kv.group(2).strip()
        meta = {
            "title": raw.get("title", ""), "module": raw.get("module", ""),
            "doc_type": raw.get("doc_type", ""), "theme": raw.get("theme", ""),
            "kp_ids": _CODE.findall(raw.get("kp_ids", "")),
        }
    clean = markdown.replace("﻿", "").replace("\r\n", "\n")
    body = re.sub(r"^---\n[\s\S]*?\n---\n?", "", clean).strip()
    h1 = re.match(r"^#\s+(.+)\n?", body)
    if h1:
        body = body[h1.end():].strip()

    title = meta["title"]
    chunks: list = []
    chunk_no = 0
    section_no = 0
    for section in _split_sections(body, title):
        section_no += 1
        title_path = title if section["title"] == title else f"{title} › {section['title']}"
        for part_no, part in enumerate(_split_by_length(section["text"]), start=1):
            chunk_no += 1
            chunks.append(ChunkDraft(
                source_path=source_path, title=title,
                section_title=section["title"], title_path=title_path,
                section_no=section_no, part_no=part_no, chunk_no=chunk_no,
                body=part, content_fingerprint=fingerprint(source_path, title_path, part),
                module=meta.get("module", ""), doc_type=meta.get("doc_type", ""),
                theme=meta.get("theme", ""), kp_ids=meta.get("kp_ids", []),
            ))
    return ParsedDoc(source_path=source_path, meta=meta, chunks=chunks)


def fingerprint(source_path: str, title_path: str, body: str) -> str:
    return hashlib.sha256(f"{source_path}\n{title_path}\n{body}".encode("utf-8")).hexdigest()


def _split_sections(body: str, title: str = "正文") -> list:
    heads = list(re.finditer(r"^##\s+(.+)$", body, re.M))
    if not heads:
        # 无 H2：整篇一节，节名 = 文档题 → title_path 退化为纯标题（设计 §2）
        return [{"title": title, "text": body}] if body.strip() else []
    out = []
    preface = body[:heads[0].start()].strip()
    for i, h in enumerate(heads):
        start = h.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        text = body[start:end].strip()
        if i == 0 and preface:
            text = f"{preface}\n\n{text}"
        if text:
            out.append({"title": h.group(1).strip(), "text": text})
    return out


def _split_by_length(body: str) -> list:
    if len(body) <= MAX_CHARS:
        return [body]
    count = max(1, -(-len(body) // TARGET_MAX))
    target = -(-len(body) // count)
    parts, current = [], ""
    for sentence in _sentences(body):
        piece = sentence
        while len(piece) > MAX_CHARS:  # 单句超长硬切
            parts.append(current + piece[:MAX_CHARS - len(current)] if current else piece[:MAX_CHARS])
            piece = piece[MAX_CHARS - (len(current) if current else 0):]
            current = ""
        if current and len(current) + len(piece) > MAX_CHARS:
            parts.append(current)
            current = piece
        elif current and len(current) >= target and len(parts) < count - 1:
            parts.append(current)
            current = piece
        else:
            current += piece
    if current:
        parts.append(current)
    # 组间重叠：上一组末句带入下一组开头（每块脱离原文可独立读）
    for i in range(1, len(parts)):
        prev_tail = parts[i - 1].split("。")[-2] + "。" if "。" in parts[i - 1] else ""
        if prev_tail and not parts[i].startswith(prev_tail) \
                and len(prev_tail) + len(parts[i]) <= MAX_CHARS:
            parts[i] = prev_tail + parts[i]
    return parts


def _sentences(text: str) -> list:
    found = re.findall(r"[^。！？；\n]+[。！？；]?", text)
    return [s.strip() for s in found if s.strip()]
