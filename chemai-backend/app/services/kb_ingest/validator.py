"""frontmatter 校验（T9）：九字段齐全 + 枚举合法 + 引用落词典 + 不自指。

结构错误抛 ValidationError —— 摄取当场拒，坏文档不进向量库。
kp_ids/prerequisites 按**逗号分隔列表**解析（不用 M1 前缀正则挑拣——
非法引用如「不存在的码」必须在落库前炸出来，静默丢弃等于没校验）。
"""
import re

REQUIRED = ("title", "doc_type", "module", "theme", "kp_ids",
            "source", "tags", "equations", "prerequisites")
DOC_TYPES = {"concept", "reference_table"}


class ValidationError(ValueError):
    pass


def _parse_list(raw: str) -> list:
    """'[A, B]' / 'A, B' / '[]' → ['A', 'B'] / []。剥引号与空白。"""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [p.strip().strip("'\"") for p in raw.split(",") if p.strip()]


def parse_frontmatter(markdown: str) -> dict:
    m = re.match(r"^---\n([\s\S]*?)\n---\n?", markdown)
    if not m:
        raise ValidationError("缺少 frontmatter")
    # 行解析 + 块列表：`key:` 单行值，或 `key:` 换行后 `  - item` 的 YAML 块列表
    # （equations 常多行，一行一个带引号的方程式）
    lines = m.group(1).split("\n")
    fm: dict = {}
    block_key = None
    for line in lines:
        stripped = line.strip()
        block_item = re.match(r"^-\s*(.*)$", stripped)
        if block_item and block_key:
            if not isinstance(fm.get(block_key), list):
                fm[block_key] = []  # key: 行先置了空串，块项来时要换成列表
            # 剥 YAML 标量引号（方程式写作 - "..."，引号不属于内容）
            fm[block_key].append(block_item.group(1).strip().strip('"').strip("'").strip())
            continue
        kv = re.match(r"^([a-z_]+):\s*(.*)$", stripped)
        if kv:
            block_key = kv.group(1)
            fm[block_key] = kv.group(2).strip()
    missing = [k for k in REQUIRED if k not in fm or fm[k] == ""]
    if missing:
        raise ValidationError(f"缺少字段 {missing}")
    if fm["doc_type"] not in DOC_TYPES:
        raise ValidationError(f"doc_type 不支持：{fm['doc_type']}")
    for k in ("kp_ids", "prerequisites", "tags", "equations"):
        if isinstance(fm[k], str):
            fm[k] = _parse_list(fm[k])
    return fm


def validate_doc(source_path: str, markdown: str, known_codes: set) -> dict:
    """解析 + 校验，返回 meta（kp_ids/prerequisites 为干净的列表）。"""
    fm = parse_frontmatter(markdown)
    if not fm["kp_ids"]:
        raise ValidationError("kp_ids 不能为空——文档必须挂到至少一个知识点")
    bad = [c for c in fm["kp_ids"] + fm["prerequisites"] if c not in known_codes]
    if bad:
        raise ValidationError(f"引用词典中不存在的知识点 {bad}")
    if set(fm["kp_ids"]) & set(fm["prerequisites"]):
        raise ValidationError("prerequisites 不得包含自身 kp_ids")
    fm["title"] = fm["title"].strip("'\"")
    return fm
