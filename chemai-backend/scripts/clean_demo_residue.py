# -*- coding: utf-8 -*-
"""E2 清理：① 演示库里的测试通知 ② t-* 测试会话遗留的 open 门（置 superseded，不删行）。

用法：python scripts/clean_demo_residue.py            # dry-run
      python scripts/clean_demo_residue.py --apply    # 真跑
"""
import argparse
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.models.database import get_db, AgentGate, ParentNotification

TEST_TITLES = ("INFO验证", "e2e 验证通知")
ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()

db = next(get_db())
try:
    notes = (db.query(ParentNotification)
             .filter((ParentNotification.title.in_(TEST_TITLES))
                     | (ParentNotification.title.like("三端联动通知%"))).all())
    open_gates = (db.query(AgentGate)
                  .filter(AgentGate.status == "open",
                          AgentGate.conversation_id.like("t-%")).all())
    print(f"测试通知 {len(notes)} 条；t-* 会话里 open 门 {len(open_gates)} 扇")
    for g in open_gates[:10]:
        print("   gate", g.gate_id, g.kind, g.tool_name)
    if args.apply:
        for n in notes:
            db.delete(n)
        for g in open_gates:
            g.status = "superseded"
        db.commit()
        print(f"已删除测试通知 {len(notes)} 条；已收敛 open 门 {len(open_gates)} 扇")
    else:
        print("（dry-run，未改动；加 --apply 生效）")
finally:
    db.close()