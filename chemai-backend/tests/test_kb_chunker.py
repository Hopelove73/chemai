"""KB-T9 切块器（纯函数 seam）：四层切块的参数与行为钉。

参数来自设计 §2（qiuqiu 实测值）：MAX 600 / TARGET 450 / MIN 100，
句末标点切句，组间重叠 1-2 句；指纹 = sha256(source_path\\n+title_path\\n+body)。
"""
from app.services.kb_ingest.chunker import (CHUNK_LIMITS, fingerprint,
                                             parse_doc)


DOC = """---
title: 测试文档
doc_type: concept
module: 必修一
theme: T2
kp_ids: [M1-T2-KP06]
source: 测试
tags: [t1]
equations: []
prerequisites: []
---

# 测试文档

导语部分，应该并入第一个小节。

## 第一节

短小节。

## 第二节

""" + "。" .join(["这一句比较长用来撑长度"] * 40) + "。"

DOC_LONG_SECTION = """---
title: 长文档
doc_type: concept
module: 必修一
theme: T2
kp_ids: [M1-T2-KP06]
source: 测试
tags: []
equations: []
prerequisites: []
---

# 长文档

## 很长的一节

""" + "。".join(f"第{i}句话讲一个完整的化学事实，长度适中便于切分实验" for i in range(30)) + "。"


def test_limits_match_design():
    assert CHUNK_LIMITS == {"MIN_CHARS": 100, "TARGET_MAX": 450, "MAX_CHARS": 600}


def test_parse_doc_without_meta_extracts_m2_codes():
    """meta 缺省的兜底解析也必须认出必修二码——M2 文档不能静默丢 kp_ids。"""
    doc = parse_doc("必修二/测试文档.md",
                    DOC.replace("module: 必修一", "module: 必修二")
                       .replace("M1-T2-KP06", "M2-T4-KP12"))
    assert doc.meta["module"] == "必修二"
    assert doc.meta["kp_ids"] == ["M2-T4-KP12"]


def test_parse_splits_h2_and_merges_preface():
    doc = parse_doc("必修一/测试文档.md", DOC)
    assert doc.chunks, "至少切出一块"
    first = doc.chunks[0]
    assert first.section_title == "第一节"
    assert "导语部分" in first.body, "首节前导语要并入第一块"
    assert first.title_path == "测试文档 › 第一节"


def test_title_path_is_doc_title_when_single_section():
    single = DOC.split("## 第一节")[0] + "全文只有一节内容。"
    doc = parse_doc("必修一/单节.md", single)
    assert len(doc.chunks) == 1
    assert doc.chunks[0].title_path == "测试文档", "单节文档 title_path = 纯标题"


def test_long_section_splits_with_overlap_and_limits():
    doc = parse_doc("必修一/长文档.md", DOC_LONG_SECTION)
    bodies = [c.body for c in doc.chunks if c.section_title == "很长的一节"]
    assert len(bodies) >= 2, "超长小节必须切多块"
    for b in bodies:
        assert len(b) <= CHUNK_LIMITS["MAX_CHARS"], f"块超上限: {len(b)}"
    # 组间重叠：第二块以第一块的末句开头
    if len(bodies) >= 2:
        last_sentence = bodies[0].split("。")[-2] + "。"
        assert bodies[1].startswith(last_sentence), "组间重叠 = 上一组末句带入下一组"


def test_fingerprint_stable_and_content_sensitive():
    doc = parse_doc("必修一/测试文档.md", DOC)
    fp = doc.chunks[0].content_fingerprint
    assert fp == fingerprint("必修一/测试文档.md", doc.chunks[0].title_path, doc.chunks[0].body)
    doc2 = parse_doc("必修一/测试文档.md", DOC.replace("短小节", "短小节！"))
    assert doc2.chunks[0].content_fingerprint != fp, "正文变了指纹必须变"


def test_chunk_no_sequential_and_source_path_kept():
    doc = parse_doc("必修一/测试文档.md", DOC)
    assert [c.chunk_no for c in doc.chunks] == list(range(1, len(doc.chunks) + 1))
    assert all(c.source_path == "必修一/测试文档.md" for c in doc.chunks)
