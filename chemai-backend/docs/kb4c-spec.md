# KB4c Spec：缺口知识点补建（A）+ 必背考点细则挂接图谱（B）

> 立项：2026-10-28 · 父计划：`docs/knowledge-base-phase4-plan.md` · 数据依据：`content/kb/reference/`（374 清单转录 333 条 + coverage-verdicts.json 逐条判定）
> 审定结论：329/333 覆盖（98.8%），体系性缺口 = 镁及其化合物、磷及其化合物；图谱体感偏少的解法 = 节点信息密度（本 spec 方案 B），细目化（方案 C）继续挂账随题库扩容。

## 一、Context

老师查看 /kmap 图谱反馈"课程知识点偏少"。KB4c 覆盖度审计（2026-10-28）证明内容覆盖完备（329/333），真正缺口是两个家族：**镁**（Mg+N₂、Mg+CO₂、Mg+SO₂ 推断题常客，M1 钠铝铁俱全唯独无镁）与**磷**（P+Cl₂ 量比产物，六册无任何磷条目）。同时图谱节点详情信息密度低——点开知识点只有描述/文档/方程式，333 条必背考点细则（已逐条判定归属）没有入口。本 spec 落地方案 A（补缺）与方案 B（细则挂接）。

## 二、Verified Current State（2026-10-28 实测）

| 项 | 现状 | 证据 |
|---|---|---|
| 词典 | 六册 158 码（M1 42+补漏 2=… 实为 43 含电子守恒、M2 38、M3 32、M4 20、M5 18、M6 8） | `content/kb/kp_dictionary_m*.json`；`load_all_dictionaries` 合并 |
| 无镁 | M1 词典无镁条目；题库仅 2 道独立题含镁（Mg-Al 合金计算），无标签 | 题库 DISTINCT content 查询 |
| 无磷 | 六册词典无磷条目；题库 0 题 | 同上 |
| 图谱 | 270 active / proposed 0；159 知识点 | knowledge_edges |
| /kmap 弹窗 | 字段序：徽标(module/theme/错误率)→description→挂载文档→方程式素材→先修/后继 | `frontend/src/pages/teacher/KnowledgeMapAdminPage.tsx:351-440`（NodeDetailModal） |
| graph 接口 | nodes 含 kp_id/name/module/theme/order_no/description/error_rate/answers/doc/equations | `app/api/knowledge_map.py:170-186` |
| 清单数据 | 333 条转录 + 逐条归属判定（covered 条目带 kp code） | `content/kb/reference/hxzxs-374-checklist-items.json` / `coverage-verdicts.json` |
| 测试钉基线 | KB 系 74 绿；金集 (100, 8)；M2 词典钉 `== 38` | `tests/test_kb_eval.py` / `tests/test_kb_dictionary.py` |

## 三、Scope

**In**：A = 镁/磷两个 KP 的全套管线（词典+标签+语料+边+题库回填+评测）；B = 333 条细则挂接 graph 接口与 /kmap 弹窗。
**Out**：方案 C（L2 细目化，挂账随题库）；tutor 出题 hint 消费细则（hint 已重）；学生端展示细则；扫描 PDF 入库（版权+体积，留本地 reference）；coverage-verdicts 的归属改判流程（老师直接改 JSON 复核即可）。

## 四、Design（实现者零设计决策）

### A1 词典条目（append，不动既有条目）

```json
M1-T2-KP28 镁及其化合物 | T2 常见的无机物及其应用 | order_no 44 | chapter 第三章补充
  description: 镁与氧气/氮气/二氧化碳/二氧化硫的反应（2Mg+CO₂=2MgO+C、3Mg+N₂=Mg₃N₂ 都要点燃）；氧化镁与氯化镁；镁合金
M2-T2-KP13 磷及其化合物 | T2 常见的无机物及其应用 | order_no 39
  description: 白磷红磷（P₄ 分子晶体、水中切割）；磷与氯气量比产物（不足 PCl₃、充足 PCl₅）；P₂O₅ 吸水；磷酸（三元中强酸、无氧化性）
```
（实施勘误 2026-10-28：spec 初稿写的 M2-T2-KP12 已被「硅酸盐与无机非金属材料」占用，实际启用 M2-T2-KP13。）
审定边界写进词典顶层 note：镁条目不含海水提镁工业流程（M2-T5-KP02 已有）；磷不含磷酸盐溶解性规律（超纲）。**M1-T2-KP27 电子守恒的 order_no 43 不变，44 续排**。

### A2 标签映射（kp_tag_map_m1/m2.json + 导入）

`镁及其化合物 → M1-T2-KP28 (high)`、`磷及其化合物 → M2-T2-KP12 (high)`。

### A3 语料 2 篇（照 M2-M5 模板）

`content/kb/必修一/镁及其化合物.md`、`content/kb/必修二/磷及其化合物.md`。frontmatter 九字段 + 五节结构 + 常见易错点 3~5 条 + frontmatter 方程式与正文逐字一致（自检脚本把关）。source: `LLM起草-待老师审定 2026-10-28`。

### A4 提议边（.proposals.json → 导入 → 采纳）

镁：prerequisite M1-T2-KP06（氧化还原）、M1-T2-KP14（物质的量，Mg₃N₂ 计算）；relates_to 镁/氧化镁/氮化镁。
磷：prerequisite M1-T2-KP06；relates_to 白磷 P₄/五氧化二磷 P₂O₅。

### A5 题库回填（一次性脚本）

2 道含镁独立题打标签 `["镁及其化合物"]`（knowledge_points JSON 追加，audit_report 记录 `kb4c-A 回填`），脚本落 `scripts/backfill_mg_tags.py`，幂等。

### A6 评测

金集 +2：kb109 镁（CO₂/氮气推断向）、kb110 磷（PCl₃/PCl₅ 量比）；`(100, 8) → (102, 8)`；三态重跑确认 0.39/0.45 仍双绿（+2 docs / ~10 chunks，漂移风险低，若漂按扫描定值并更新常量与 HANDOFF）。

### B1 细则数据文件（生成脚本，可重跑）

`scripts/build_kp_reference_notes.py`：读 `checklist-items.json` + `coverage-verdicts.json` → 输出 `content/kb/kp_reference_notes.json`：

```json
{
  "version": "kb4c-b-1",
  "generated_at": "2026-10-28",
  "source": "化学自习室《高中化学374个必备知识点》AI 转录 333 条；归属判定 content/kb/reference/coverage-verdicts.json（老师可复核改判后重跑本脚本）",
  "notes": { "M1-T2-KP06": [{"id": 21, "text": "氧化性 Cl₂>Br₂>Fe³⁺>I₂>S；还原性 I⁻>Fe²⁺>Br⁻>Cl⁻"}] }
}
```
gap 4 条按 A 的新 KP 归组（镁 3 条 → M1-T2-KP28、磷 1 条 → M2-T2-KP12）；校验：0 条无主、总数 = 333 - 判定为纯重复的占位条目。A 的 KP 未建时先跑 B 也可以：脚本对未知 kp 的条目报 warning 并暂存 `unassigned` 桶，T1 完成后重跑归位。

### B2 后端（graph 接口加字段）

`app/api/knowledge_map.py`：模块级 `_load_reference_notes()`（functools.lru_cache，读 `content/kb/kp_reference_notes.json`，文件缺失/损坏返回 `{}` 并 log warning——不炸接口）；nodes 构造循环里加 `"reference_notes": notes.get(k.kp_id, [])`。

### B3 前端（弹窗新区块）

`GraphNode` 类型加 `reference_notes: { id: number; text: string }[]`；NodeDetailModal 在「方程式素材」之后插入「必背考点细则」区块（编号列表，尾部一行小字：来源 化学自习室知识点清单 · AI 转录归属，可在 reference/coverage-verdicts.json 复核）。空数组不渲染。

## 五、Tasks（按依赖序）

| # | Task | 依赖 | 验收（pass/fail） | 预估 |
|---|---|---|---|---|
| T1 | A 词典+标签：m1/m2 append 2 条 + note 更新 + tag_map 新标签 + sync + import；更新 `test_kb_dictionary`（M2 `==38→39`、load_all sources 不变码集 +2） | — | load_all 160 码；kp_tag_map 160 行；KB 钉绿 | 0.5h |
| T2 | A 语料 2 篇 + 自检（frontmatter/五节/方程式一致/易错解析）+ bge 摄取 | T1 | 160 docs / ~816 chunks；易错三元覆盖 160/160；自检 0 失败 | 1.5h |
| T3 | A 提议边导入+采纳（沿用成环校验脚本） | T1 | active ≥274、proposed 0、0 rejected | 0.5h |
| T4 | A 题库回填 2 题镁标签（脚本幂等） | T1 | 2 题带 镁及其化合物 标签；audit_report 有批次记录；重复跑不重复打 | 0.5h |
| T5 | A 评测扩容+2 与三态复扫 | T2 | 金集 110 题 (102, 8) 双绿；阈值不动或有扫描依据；`test_kb_eval` 钉更新 | 0.5h |
| T6 | B 生成脚本 + kp_reference_notes.json | T1（新 KP 归位） | 0 条无主；notes 覆盖全部 160 KP 键空间中实际有细节的节点 | 0.5h |
| T7 | B 后端 notes 字段 + 2 钉（带 notes 节点 / 文件缺失降级） | T6 | 钉绿；graph 接口响应含 reference_notes | 0.5h |
| T8 | B 前端弹窗区块 | T7 | tsc 0 错；弹窗对有细则节点显示区块、无细则不渲染 | 0.5h |
| T9 | 回归+文档+提交：KB 系钉全绿、全量后端套件、HANDOFF + 父计划文档 〇 状态更新 | T5,T8 | 全绿；HANDOFF 状态句与 plan doc 执行状态反映 A/B 完成 | 0.5h |

依赖图：`T1 → {T2→T3→T5, T4, T6→T7→T8} → T9`。T3/T4/T6 可并行。

## 六、Testing Plan

| 层 | 内容 | 数量 |
|---|---|---|
| 数据钉 | M1 44 条/order 连续、M2 39 条、跨册不重名；tag_map 160 行 | +2 |
| 语料自检 | 2 篇 frontmatter/结构/方程式一致/易错三元（既有脚本） | 复用 |
| graph API | 节点带 reference_notes；kp_reference_notes.json 缺失降级空 | +2 |
| 前端 | tsc；弹窗区块渲染（490 vitest 沿用） | 类型级 |
| 评测 | 金集 110 题三态双绿 | 复用 |

## 七、Rollback

全部追加式：词典条目/标签映射从 JSON 删除后重跑 sync+import（update 语义回滚）；语料删除+重摄（删 chroma 文档行按 source_path）；边回 `disabled`；notes 文件删除 → 接口降级空数组、弹窗不渲染（B 天然可独立回滚）；题库回填按 audit_report 批次号反查删除标签。

## 八、Out of Scope

细目化（C）；tutor hint 消费细则；学生端展示；PDF 原件入库；coverage 归属的批量改判工具。

## 九、Related

父计划 `docs/knowledge-base-phase4-plan.md`（KB4c 定义与 〇 执行状态）；判定数据 `content/kb/reference/coverage-verdicts.json`；提交 1bffb27（清单+判定落库）。
