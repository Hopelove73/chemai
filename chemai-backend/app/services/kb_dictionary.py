"""知识点词典同步器（KB1a）：课标结构化清单 → knowledge_points 表。

词典数据在 content/kb/kp_dictionary_m1.json（老师审定的唯一编辑入口）；
本模块只做两件事：
  · load_dictionary：结构校验（缺字段/重复 code/order_no 非正整数 → 拒绝），
    手改数据改坏管线在这里当场炸，而不是落库后查不出来；
  · sync_dictionary：以 kp_id=code 幂等 upsert——首跑 inserted、重跑全跳过、
    内容有变记 updated。**code 是稳定主键**：审定后不改 code，只改名称/描述。
CLI：python -m app.services.kb_dictionary content/kb/kp_dictionary_m1.json
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("code", "name", "theme", "order_no")


class DictionaryError(ValueError):
    """词典结构错误：不落库，让起草/审校侧当场修。"""


def load_dictionary(path: str) -> dict:
    """读取并校验词典 JSON。返回原数据；结构错误抛 DictionaryError。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise DictionaryError("词典缺少 items 列表")
    seen: set = set()
    for it in items:
        code = it.get("code") or "?"
        missing = [k for k in REQUIRED_FIELDS if not it.get(k)]
        if missing:
            raise DictionaryError(f"{code}: 缺少字段 {missing}")
        if it["code"] in seen:
            raise DictionaryError(f"{it['code']}: code 重复")
        if not isinstance(it["order_no"], int) or it["order_no"] <= 0:
            raise DictionaryError(f"{it['code']}: order_no 必须是正整数")
        seen.add(it["code"])
    return data


def load_all_dictionaries(directory: str) -> dict:
    """读取目录下全部 kp_dictionary_m*.json 并合并 items。

    摄取/提议导入的 known_codes 校验集必须覆盖全册——只加载 M1 会把
    `M2-T*` 引用当成未知码当场拒。跨文件 code 重复 → DictionaryError。
    返回 {"items": [...], "sources": [文件名]}。
    """
    files = sorted(Path(directory).glob("kp_dictionary_m*.json"))
    if not files:
        raise DictionaryError(f"目录下没有词典文件: {directory}")
    merged: list = []
    seen: set = set()
    for f in files:
        data = load_dictionary(str(f))
        for it in data["items"]:
            if it["code"] in seen:
                raise DictionaryError(f"{it['code']}: 跨词典文件重复（{f.name}）")
            seen.add(it["code"])
            merged.append(it)
    return {"items": merged, "sources": [f.name for f in files]}


def sync_dictionary(db, data: dict) -> dict:
    """把词典条目 upsert 进 knowledge_points（kp_id = code）。

    module 取词典顶层的 `module` 字段。幂等：无变化 → 不计 updated；
    字段有变 → updated。提交由调用方负责（与 work_unit 事务模式一致）。
    """
    from app.models.database import KnowledgePoint

    module_name = str(data.get("module") or "")
    report = {"total": len(data["items"]), "inserted": 0, "updated": 0}
    for it in data["items"]:
        row = db.query(KnowledgePoint).filter_by(kp_id=it["code"]).first()
        fields = {
            "code": it["code"],
            "name": it["name"],
            "category": it["theme"],   # 旧列沿用：category 即主题（读方兼容）
            "theme": it["theme"],
            "module": module_name,
            "order_no": it["order_no"],
            "description": it.get("description") or "",
        }
        if row is None:
            db.add(KnowledgePoint(kp_id=it["code"], **fields))
            report["inserted"] += 1
            continue
        changed = [k for k, v in fields.items() if getattr(row, k) != v]
        if changed:
            for k, v in fields.items():
                setattr(row, k, v)
            report["updated"] += 1
    return report


def main(argv: list) -> int:
    if not argv:
        print("用法: python -m app.services.kb_dictionary <词典.json>")
        return 2
    from app.models.database import get_session_factory
    data = load_dictionary(argv[0])
    db = get_session_factory()()
    try:
        report = sync_dictionary(db, data)
        db.commit()
    finally:
        db.close()
    print(f"[OK] 同步完成: 共 {report['total']} 条, "
          f"新增 {report['inserted']}, 更新 {report['updated']}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
