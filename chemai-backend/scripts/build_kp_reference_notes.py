"""KB4c-B：374 清单考点细则 → kp_reference_notes.json（按 KP 归组）。

输入：content/kb/reference/hxzxs-374-checklist-items.json（333 条转录）
      content/kb/reference/coverage-verdicts.json（逐条归属判定）
输出：content/kb/kp_reference_notes.json —— graph 接口按 kp 取用。

规则：
  · covered/partial 条目归到判定的 kp；gap 条目归到本批新建 KP
    （M1-T2-KP28 镁 / M2-T2-KP13 磷）；
  · 归属 kp 不在当前词典（词典回滚等）→ 计入 unassigned 桶并 warning，不丢数据；
  · 幂等可重跑（老师改判 coverage-verdicts.json 后重跑即生效）。
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REF = ROOT / "content" / "kb" / "reference"
OUT = ROOT / "content" / "kb" / "kp_reference_notes.json"
# gap 条目在 coverage-verdicts.json 只有 topic 没有 kp，按 topic 归组到
# KB4c 新建的缺口 KP（新增缺口家族时：词典 + 本表 + verdicts 三处同步）
GAP_TOPIC_KP = {"镁及其化合物": "M1-T2-KP28", "磷及其化合物": "M2-T2-KP13"}


def main() -> int:
    items = json.loads((REF / "hxzxs-374-checklist-items.json").read_text(encoding="utf-8"))
    verdicts = json.loads((REF / "coverage-verdicts.json").read_text(encoding="utf-8"))
    from app.services.kb_dictionary import load_all_dictionaries
    known = {it["code"] for it in load_all_dictionaries(str(ROOT / "content" / "kb"))["items"]}

    notes: dict[str, list] = {}
    unassigned: list[dict] = []
    assigned: set[int] = set()
    for num_str, entry in sorted(items.items(), key=lambda kv: int(kv[0])):
        num = int(num_str)
        if num in assigned:
            continue
        v = verdicts.get(num_str) or {}
        kp = v.get("kp")
        if not kp and v.get("v") == "gap" and v.get("topic") in GAP_TOPIC_KP:
            kp = GAP_TOPIC_KP[v["topic"]]  # gap 条目：按 topic 归组到缺口家族新 KP
        if kp and kp not in known:
            print(f"[WARN] 条目 {num} 归属 {kp} 不在词典 → unassigned")
            unassigned.append({"id": num, "text": entry, "verdict_kp": kp})
            assigned.add(num)
            continue
        if not kp:
            unassigned.append({"id": num, "text": entry, "verdict_kp": None})
            assigned.add(num)
            continue
        notes.setdefault(kp, []).append({"id": num, "text": entry})
        assigned.add(num)

    out = {
        "version": "kb4c-b-2",
        "generated_at": date.today().isoformat(),
        "source": ("化学自习室《高中化学374个必备知识点》AI 视觉转录 333 条"
                   "（hxzxs-374-checklist-items.json）；归属判定 coverage-verdicts.json"
                   "（老师可复核改判后重跑本脚本）"),
        "notes": notes,
        "unassigned": unassigned,
        "stats": {
            "total_items": len(items),
            "assigned": sum(len(v) for v in notes.values()),
            "unassigned": len(unassigned),
            "kps_with_notes": len(notes),
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[OK] {OUT.name}: assigned {out['stats']['assigned']} / "
          f"unassigned {out['stats']['unassigned']}，覆盖 {len(notes)} 个 KP")
    return 0 if not unassigned else 1


if __name__ == "__main__":
    sys.exit(main())
