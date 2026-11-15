"""FSRS 参数校准（student-daily-loop S1 / REQ-RCS-003）。

离线跑真实答题日志 → fsrs-optimizer 拟合权重 → 参数入库（app_config）。
不在线训练：校准是人工触发的离线作业，运行中的服务读库参数即可生效。

用法：
    python scripts/calibrate_fsrs.py                # 校准并打印对比
    python scripts/calibrate_fsrs.py --save         # 校准后写库（默认只打印）

依赖：pip install "fsrs[optimizer]"（重依赖 PyTorch，服务环境可不装——
本脚本只在人工校准时本地运行）。

前置（S1 事实核查）：记忆卡调度内核已用官方 py-fsrs（fsrs.Scheduler），
本脚本不替换调度，只更新 Scheduler 的权重参数。
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DB_PATH = Path(__file__).resolve().parents[1] / "chemai.db"
CONFIG_KEY = "fsrs_weights"
DEFAULT_WEIGHTS = [0.2172, 1.1771, 3.2602, 16.1507, 7.0114, 0.57, 2.0966,
                   0.0069, 1.5261, 0.112, 1.0178, 1.849, 3.9943, 0.2177, 3.1834]


def load_review_log(db_path: Path) -> list[dict]:
    """记忆卡复习日志 → optimizer 输入。

    fsrs_optimizer 期望 [(long_term_due_datetime, rating), ...] 按时间序的
    序列；这里从 review 每卡的 state/due/last_signal 反推近似信号流
    （对=3/Good，错=1/Again，记得=3，模糊=2，忘了=1），按 due 排序。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT state, due, last_signal FROM memory_cards WHERE last_signal IS NOT NULL "
        "ORDER BY due"
    ).fetchall()
    conn.close()
    rating_of = {"correct": 3, "remembered": 3, "vague": 2, "wrong": 1, "forgot": 1}
    log = []
    for r in rows:
        rating = rating_of.get((r["last_signal"] or "").lower())
        if rating is None:
            continue
        log.append({"due": r["due"], "state": r["state"], "rating": rating})
    return log


def calibrate(log: list[dict]) -> list[float] | None:
    try:
        from fsrs_optimizer import Optimizer  # type: ignore
    except ImportError:
        print("未安装 fsrs-optimizer。请先：pip install \"fsrs[optimizer]\"")
        return None
    if len(log) < 400:
        print(f"复习日志不足（{len(log)} 条 < 400）：FSRS 官方建议 ≥400 次复习再校准，"
              "本次跳过（当前使用默认权重）。")
        return None

    optimizer = Optimizer()
    optimizer.S = {  # 单卡序列喂 optimizer（ Chemical AI 场景无 deck 区分）
        "default": [(item["due"], item["rating"]) for item in log],
    }
    optimizer.train()
    weights = [float(w) for w in optimizer.w]
    print(f"校准完成：{len(log)} 条复习日志 → 新权重")
    print(json.dumps(weights))
    return weights


def compare(old: list[float], new: list[float]) -> None:
    print("\n权重对比（旧 → 新）：")
    names = ["w0 初始稳定性", "w1 失败衰减", "w2 成功增稳", "w3/4 中长期稳定",
             "w5-9 难度/奖励项", "w10-13 检索性项", "w14 同日项"]
    for i, (a, b) in enumerate(zip(old, new)):
        group = names[min(i * len(names) // len(old), len(names) - 1)]
        flag = "  ← 变化明显" if abs(a - b) > 0.5 else ""
        print(f"  w{i:<2} {a:>8.4f} → {b:<8.4f} ({group}){flag}")


def save_weights(weights: list[float]) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.execute(
        "INSERT INTO app_config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (CONFIG_KEY, json.dumps(weights)),
    )
    conn.commit()
    conn.close()
    print(f"\n已写入 {CONFIG_KEY} → app_config（服务重启后 Scheduler 读取生效）")


def main() -> None:
    parser = argparse.ArgumentParser(description="FSRS 参数校准（离线）")
    parser.add_argument("--save", action="store_true", help="校准后写入 app_config")
    parser.add_argument("--db", default=str(DB_PATH), help="数据库路径")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}")
        return

    log = load_review_log(db_path)
    print(f"载入复习日志：{len(log)} 条")
    new_weights = calibrate(log)
    if new_weights is None:
        return
    compare(DEFAULT_WEIGHTS, new_weights)
    if args.save:
        save_weights(new_weights)
    else:
        print("\n（预览模式，未写库；确认后加 --save 落库）")


if __name__ == "__main__":
    main()
