"""KB4b 动工前验证（计划第三节修订口径）：家族题目按技能桶分类 × 错误集中度。

口径：内容级去重（独立题），错误率按 student_answers.is_correct。
两个家族：
  氧化还原族 = 标签 {氧化还原反应, 氧化剂与还原剂, 电子转移, 氧化性强弱比较,
                    氧化剂还原剂判断, 氧化剂与还原剂判断}
  化学平衡族 = 标签 {化学平衡, 勒夏特列原理, 平衡图像, 平衡常数, 等效平衡,
                    化学平衡常数计算, 化学平衡常数, 平衡移动, 平衡状态判断, ...}
技能桶按题面关键词分（分不进的落 other，保留人工抽查空间）。
"""
import collections
import json
import sqlite3

db = sqlite3.connect("chemai.db")

REDOX_TAGS = {"氧化还原反应", "氧化剂与还原剂", "电子转移", "氧化性强弱比较",
              "氧化剂还原剂判断", "氧化剂与还原剂判断", "氧化还原方程式"}
EQUIL_TAGS = {"化学平衡", "勒夏特列原理", "平衡图像", "平衡常数", "等效平衡",
              "化学平衡常数计算", "化学平衡常数", "平衡移动", "化学平衡移动",
              "平衡状态判断", "平衡状态的判断", "化学平衡状态的判断",
              "转化率", "化学平衡常数K", "压强对化学平衡的影响",
              "浓度对化学平衡的影响", "恒温恒压条件下化学平衡移动"}

REDOX_BUCKETS = [
    ("配平", ["配平", "系数", "守恒配平"]),
    ("电子守恒计算", ["电子", "转移电子", "得失电子", "n(电", "摩尔电子"]),
    ("陌生情境书写", ["信息", "陌生", "写出方程", "书写方程", "用离子方程式表示"]),
    ("强弱比较/判断", ["氧化性", "还原性", "强弱", "能否发生", "判断能否"]),
    ("概念辨析", ["定义", "本质", "特征", "什么是", "下列说法"]),
]
EQUIL_BUCKETS = [
    ("K与转化率计算", ["K", "平衡常数", "转化率", "计算"]),
    ("图像", ["图像", "如图", "曲线"]),
    ("移动方向", ["移动", "勒夏特列", "升高温度", "增大压强", "减小压强", "增大浓度"]),
    ("状态判断", ["平衡状态", "是否达到", "判断该反应", "一定", "可能"]),
]


def bucket_of(text, buckets):
    for name, kws in buckets:
        if any(k in text for k in kws):
            return name
    return "other"


rows = db.execute(
    "SELECT question_id, content, knowledge_points FROM questions "
    "WHERE knowledge_points IS NOT NULL AND json_valid(knowledge_points)").fetchall()
fam_q: dict[str, dict[str, set]] = {"redox": collections.defaultdict(set),
                                    "equil": collections.defaultdict(set)}
qid_content: dict[int, str] = {}
for qid, content, raw in rows:
    qid_content[qid] = content
    tags = {t.strip() for t in json.loads(raw) if isinstance(t, str)}
    if tags & REDOX_TAGS:
        fam_q["redox"][bucket_of(content, REDOX_BUCKETS)].add(content)
    if tags & EQUIL_TAGS:
        fam_q["equil"][bucket_of(content, EQUIL_BUCKETS)].add(content)

# 作答聚合（content → [ans, wrong]；按内容去重口径，同一题多学生作答合并）
ans = collections.defaultdict(lambda: [0, 0])
for correct, qid in db.execute(
        "SELECT sa.is_correct, q.question_id FROM student_answers sa "
        "JOIN questions q ON q.question_id = sa.question_id"):
    c = qid_content.get(qid)
    if c is None:
        continue
    ans[c][0] += 1
    ans[c][1] += 0 if correct else 1

for fam, title in (("redox", "氧化还原族"), ("equil", "化学平衡族")):
    print(f"=== {title} 按技能桶（独立题 / 作答 / 错误率） ===")
    allq = set()
    for b, qs in sorted(fam_q[fam].items(), key=lambda x: -len(x[1])):
        a = sum(ans[q][0] for q in qs)
        w = sum(ans[q][1] for q in qs)
        allq |= qs
        print(f"  {b:<12} {len(qs):>4} 道  {a:>6} 次  {w / a if a else 0:>5.0%}"
              f"   题例: {list(qs)[:2]}")
    tot_a = sum(ans[q][0] for q in allq)
    tot_w = sum(ans[q][1] for q in allq)
    print(f"  {'合计':<12} {len(allq):>4} 道  {tot_a:>6} 次  {tot_w / tot_a if tot_a else 0:>5.0%}")
    # 错误集中度：top20% 题目承接的错误占比
    errs = sorted((ans[q][1] for q in allq if ans[q][0]), reverse=True)
    tot_e = sum(errs)
    if tot_e:
        k = max(1, int(len(errs) * 0.2))
        print(f"  错误集中度：错误最多的 20% 题目（{k} 道）承接了 {sum(errs[:k]) / tot_e:.0%} 的错误")
    print()
