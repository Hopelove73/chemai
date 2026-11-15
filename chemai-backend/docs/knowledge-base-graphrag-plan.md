# 化学教材知识库 · GraphRAG 详细设计（第一期：必修一）

状态：设计定稿（grill 七项决策已确认，2026-10-27） · 供实施切片使用
决策记录：存储与检索形态见 ADR-0033

---

## 0. 范围（已定决策的落地边界）

| 决策点 | 定案 |
|---|---|
| 服务对象 | 只接 agentchat：问答/讲解（chemistry_tutor）+ 出题工具（generate_questions） |
| 语料范围 | 第一期必修一（人教版，约 50 篇知识点文档），全链路验证后再铺开其余册 |
| 语料路线 | 课标当骨架 + LLM 起草 + 老师审校 + 开放数据（Wikidata/PubChem）核事实；不引入盗版教材 |
| 存储 | 全 Chroma（`kb_chunks` collection）；知识图谱存 SQLite（复用 knowledge_points/knowledge_edges） |
| 检索形态 | 向量主导 + 图谱增强上下文；出题纯图驱动 |
| 图谱粒度 | 节点：知识点、物质/元素；边：先修、包含、关联。方程式是文档结构化字段不是节点 |
| 题库 | 不做独立语料；题目按知识点归档、统计回写图谱节点；61 种旧标签映射进知识点词典 |
| 评测 | 金问题集 ≥50 题 = 45 命中 + 5 应拒答；命中率目标 ≥80% |

## 1. 知识点词典（图谱与出题的统一主键）

**现状**：`knowledge_points` 表存在但空（列：kp_id/name/category/description/pubchem_cid/question_count/error_rate）；题库 61 种自由文本标签；`/kmap` 管理页与 `propose/accept_knowledge_edges` 技能已可用。

**做法**：
1. 按课标（2017 版 2020 修订）"必修一 → 主题 → 内容要求"结构化出知识点清单，灌入 `knowledge_points`，新增列：
   - `code`（稳定编号，如 `M1-T2-KP03`）、`theme`（主题）、`module`（册）、`order_no`（教学顺序）
2. 新表 `kp_tag_map(tag_text → kp_id, accepted_by, created_at)`：61 种题库标签逐条映射进词典（一对一或归并），老师审定后固化。此后题目归档、图谱、出题、评测全部用 `kp_id`。
3. `knowledge_edges` 加列：`edge_type`（prerequisite/contains/relates_to）、`source_doc`（提议来源文档）、`confidence`。现有 13 条 seed 行保留，标 `edge_type='relates_to'`。

**验收**：必修一知识点清单（预计 40-60 条）经老师审定；61 标签映射完成，题库历史数据按映射回填（一次性脚本）。

## 2. 语料与文档 Schema

目录：`content/kb/必修一/*.md`，frontmatter 强校验（照 qiuqiu 的 parseDoc 模式，缺字段/枚举外值拒绝入库）：

```yaml
---
title: 化学平衡的移动
doc_type: concept            # concept | reference_table
module: 必修一
theme: 化学反应限度
kp_ids: [M1-T3-KP01]         # 关联知识点词典
source: LLM起草-老师审定 2026-10
tags: [勒夏特列原理, 化学平衡]
equations:                   # 结构化字段（不切块、不进图节点）
  - "N2 + 3H2 ⇌ 2NH3（正反应放热）"
prerequisites: [M1-T2-KP02]  # 起草时的先修提议，随文档一并审
---
```

`reference_table` 类（元素周期表片段、酸碱盐溶解性表、常见离子颜色等）：一表一块，不经 LLM 增强，事实核对以 Wikidata/PubChem 为基准。

**切块（四层，参数照 qiuqiu 实测值）**：
1. 结构切分：H2 小节为块单元，列表/表格不拆；
2. 长度修整：目标 450 字、硬上限 600、下限 100；句末标点切句，组间重叠 1-2 句；
3. 上下文增强：确定性前缀（`文档题 › 小节题`）+ LLM 生成 1-2 句"本块位置与主题"（离线一次性，失败降级为只用标题路径）；嵌入输入 = 上下文+标题路径+正文，交付给模型的只取正文原文；
4. 账目：source_path / section_no / part_no / sha256 指纹（幂等：指纹同且向量在 → 跳过；文档修订 = 整篇先删后插，防孤儿块）。

## 3. 摄取管线（新 `app/services/kb_ingest/` + CLI）

```
扫描 content/kb/**/*.md → frontmatter 校验 → 四层切块
  → 嵌入（批量≤32，失败退避重试×2，失败清单不中断）
  → Chroma `kb_chunks`（document=上下文+标题路径+正文拼接；metadata=
      module/theme/doc_type/kp_ids/source_path/section_no/part_no/fingerprint/title_path）
  → 同事务语义：Chroma 无跨库事务，幂等以指纹为准（先查后写，重跑跳过）
```

- 嵌入模型：BGE-M3（1024 维，SiliconFlow，与 qiuqiu 同款、检索结论可复用）；DashScope embedding 作为可配置备选（key 已有）。
- CLI：`python -m app.services.kb_ingest --module 必修一 [--fake]`（`--fake` 确定性伪向量，离线冒烟用，照抄 qiuqiu 的做法）。
- 报告：docs/chunks/失败清单/耗时，一次跑完可复核。

## 4. 图谱抽取与审校（逐篇绑定，Q4）

文档审校包 = 知识点文档草稿 + 三元组提议 JSON：

```json
{"edges": [
  {"edge_type": "prerequisite", "from_kp": "M1-T2-KP02", "to_kp": "M1-T3-KP01",
   "reason": "先掌握反应限度概念才能理解平衡移动"},
  {"edge_type": "relates_to", "kp": "M1-T3-KP01", "substance": "NH3", "role": "生成物"}
]}
```

- 物质节点命名规范：中文名为主键语义、化学式为别名（`NH3 / 氨气`）；物质表新表 `kb_substances(substance_id, name, formula, aliases_json)`。
- 老师**审文档时顺手批三元组**，一篇定稿 = 文本入库 + 图谱落库同时完成；落库走现有 `propose → /kmap 接受` 队列（`knowledge_edges.status: proposed → accepted`）。
- `prerequisites` frontmatter 字段与提议边同源：审校通过即写 `edge_type='prerequisite'`。

## 5. 检索与接线（Q5）

### 5.1 问答（向量主导 + 图增强）

```
chemistry_tutor 组 prompt 前：
  kb = Chroma.query(问题, top4, where={module:"必修一"} 可选)
  若命中块带 kp_ids：
    图增强 = 先修链（上溯≤2跳：先修概念清单）
           + kp 的 equations 字段（来自知识点文档 frontmatter 汇总）
    拼进 prompt 的「参考资料」段（标注来源路径）
  600ms 预算：超时降级为纯向量 top4；kb 未建库 → 该环节整体跳过（不阻塞回合）
```

- 接入点：`agent/tools/` 新增 `kb_context.py`（纯函数组装），chemistry_tutor 的 prompt 构造处调用；知识命中与来源写入回合日志（照 ADR-0004 精神：审计不上前端）。
- **无命中阈值**：向量距离全超阈值 → 不拼资料段，模型按系统提示词直说"这个我暂时答不准"——防幻觉做成机制。

### 5.2 出题（纯图驱动，generate_questions 增强）

```
generate_questions(kp_id, quantity, difficulty…) 现有参数不动，内部增强：
  先修链深度 → 难度阶梯（考先修的题为易，考叶子知识点的题为难）
  kp 的 equations / 关联物质 → 题目素材池
  题库错误率（经 kp_tag_map 归到 kp）→ 易错点提示进 prompt（陷阱选项来源）
产出题目按 kp_ids 归档（现状已有），统计回写 knowledge_points.question_count/error_rate
```

## 6. 评测（≥50 题 = 45 命中 + 5 应拒答）

- `evals/kb-questions.jsonl`：`{id, question, expected:[source_path…], answer, kp_id}`。45 道命中题的覆盖结构：**必修一每个主题至少 2 题**（保证骨架全覆盖），高频错题知识点（诊断数据里的化学平衡/氧化还原/离子反应等重灾区）**每点 2-3 题**（保证信号量），题型含概念解释、方程式应用、实验辨析三类。
- 5 道应拒答题 = 必修一范围外（有机推断、工业流程深水区），正确行为是明确说"超出当前知识库"。
- CLI：`python -m evals.kb_eval`（向量召回命中率、应拒答正确率、平均延迟三指标 + 逐题清单）；阈值：命中 ≥80%（36/45）、应拒答 ≥4/5。
- 集合随语料铺开同步扩容：必修二/选择性必修各加 ≥15 题，始终保持"每主题 ≥2 题"的下限。
- 回归时机：每批语料入库后、切块参数变更后、嵌入模型更换后必跑。

## 7. 里程碑（切片顺序）

| # | 切片 | 产出 | 验收 |
|---|---|---|---|
| KB1a | 知识点词典 | 课标结构化清单（必修一 40-60 条）+ 61 标签映射表 | 老师审定；映射回填脚本跑通 |
| KB1b | 语料第一批 | 必修一 50 篇文档（LLM 起草 + 老师逐篇审校，含三元组提议） | 每篇 frontmatter 校验通过 |
| KB1c | 摄取管线 | kb_ingest CLI + Chroma collection | 50 篇全部入库可查；重跑幂等；--fake 冒烟 |
| KB1d | 图谱落库 | 逐篇审校的边进 knowledge_edges/substances | /kmap 可见必修一图谱；先修链无环 |
| KB1e | 检索接线 | kb_context.py + tutor prompt 挂钩 + generate_questions 增强 | 真机问答引用资料；出题按知识点走先修链 |
| KB1f | 评测回归 | kb_eval + 20 题集 | 命中 ≥12/15 且应拒答 ≥4/5 |

KB1a 与 KB1b 并行启动（b 是关键路径上唯一依赖人的环节）；c/d/e 依赖 a+b 的前 10 篇即可开工（不必等 50 篇全齐）。

## 8. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| LLM 起草的化学事实出错且审校漏过 | 知识库权威性受损 | 结构化事实（物性/方程式）以 Wikidata/PubChem 核对；文档 frontmatter 记 `source` 与审校人；评测应拒答题常驻 |
| 61 标签映射歧义（如"仪器使用"跨主题） | 归档与出题口径漂移 | 映射表由老师逐条审定；允许多对一；映射后旧标签只读 |
| 嵌入模型中文化学术语效果差 | 语义召回漏 | 语料小（几千块）允许全量重嵌换模型，切块/检索零改动（qiuqiu 同款退路） |
| 图谱先修链有环 | 出题难度阶梯死循环 | 落库时环检测（拒绝成环的 prerequisite 提议） |
| 审校进度停滞 | 关键路径卡死 | 50 篇拆 5 批×10 篇，每批审完即入库生效（不必全齐再上）；c/d/e 提前并行 |
