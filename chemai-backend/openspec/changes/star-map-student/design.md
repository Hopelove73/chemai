# Design: star-map-student

## 1. 键空间（先定这个，否则星星与边对不上）

库内事实（2026-11-04 核验）：

| 数据 | 键 | 例 |
|------|----|----|
| `questions.knowledge_points` | 自由文本标签 | `"化学与STSE"`、`"氧化还原"` |
| `kp_tag_map.tag_text → kp_id` | 标签 → 词典码 | `"氧化还原反应" → "M1-T2-KP06"` |
| `KnowledgePoint.kp_id` | 词典码 | `M1-T2-KP01` |
| `KnowledgeEdge.from_kp/to_kp` | 基本是词典码（277 端点中 264 个是码；11 个历史中文名端点由前端丢弃） | `M1-T2-KP06` |
| `KPLayout.kp_id` | 词典码 | `M1-T2-KP01` |
| 现有学生 `mastery/states` | **标签空间**（`mastery_for_student` 直接用标签） | `"化学与STSE"` |

结论：学生星图 MUST 在 **kp_id 词典码空间**构建，与教师端、布局、先修边同构。现有标签空间的 `mastery/states/layers` 保持原样（服务气泡图/列表），新增的 `stars[]` 走映射后的码空间。**不在本变更迁移现有字段**。

## 2. 数据契约

`GET /api/knowledge-map/student/{student_id}` 响应**只增不改**：

```jsonc
{
  "success": true,
  "student_id": "...",
  "states":  { "标签": "mastered|ready|locked" },     // 既有（标签空间）
  "mastery": { "标签": 0.0 },                          // 既有（标签空间）
  "layers":  [["标签"]],                               // 既有
  "edges":   [{ "from": "kp", "to": "kp" }],           // 既有（码空间，仅 active）
  "bindings": [{ "kp": "...", "artifact_id": "..." }],
  "faded": ["标签"],                                   // 既有（标签空间）
  "cold_start": false,
  "stars": [{                                          // 新增（码空间）
    "kp_id": "M1-T2-KP06", "name": "氧化还原反应", "module": "必修一",
    "mastery": 0.73,                                   // float | null（BKT，样本 <3 为 null）
    "answers": 42,                                     // 整数，无作答为 0
    "retrievability": 0.81,                            // float | null（无有效卡为 null）
    "state": "ready", "faded": false,
    "practice_tags": ["氧化还原反应"]                  // 新增：kp_id 反查 kp_tag_map 的标签
  }],
  "layout": { "kp_id": [x, y, z] }                     // 新增（全校共享坐标）
}
```

- `stars` 覆盖 `_graph_base_nodes(db)` 的**全部知识点**（161 个码）：未作答的也出现，作为"未探索的星"。
- `practice_tags`：`kp_id → kp_tag_map.tag_text` 的反向映射（不筛 status，与 `kb_error_rate` 读取口径一致）。**不是每个 KP 都有**：截至 2026-11-04，库内 160 行映射只覆盖 69 个码，92 个码没有标签——这些星仍可展示，但"练习此点"没有可匹配的题，按钮必须隐藏。
- `layout`：服务端直接读 `KPLayout` 全表。**不放宽 `/api/knowledge-map/layout` 的权限**（学生无 `question:read`，且布局不是本人数据，`allow_self` 不适用）。
- 现有字段语义与值 MUST 不变；旧前端忽略新字段仍正常工作。

## 3. 标签 → 码聚合（`app/services/student_kp_stats.py`）

```python
def canonical_kp_by_content(conn) -> dict[str, str]:
    """content -> kp_id。与 kb_error_rate 同口径：按 content 去重取
    MIN(question_id) 规范实例；一题多标签取首个能在 kp_tag_map 命中的标签；
    未命中任何词典码的题不参与聚合。"""
```

实现方式：把 `kb_error_rate.error_rates_for_kps` 内联的映射段抽成公开函数 `canonical_kp_by_content`（行为零变化），新服务直接复用，不复制一套口径。

```python
def student_kp_stats(db: Session, student_id: str) -> dict[str, dict]:
    """{kp_id: {"answers": int, "mastery": float|None}}，BKT 口径与
    mastery_service 一致（MIN_SAMPLES=3），但序列按 kp_id 归并。"""
```

- 作答事件：`student_answers JOIN questions` 过滤 `student_id`，按 `content` 映射到码。
- `answers`：命中该码的作答事件数（多标签映射到同一码只计一次——首映射口径）。
- `mastery`：把 `is_correct` 非空的事件按 `answered_at` 排序成 0/1 序列 → `mastery_service.bkt_sequence(seq)`；样本 < 3 → `None`。
- `state`：`kms.resolve_states(mastery_by_code, active_edges)`，码空间。
- `faded`：FSRS 到期卡的 `question_id → content → 码`，映射后标记。
- `practice_tags`：单独一次 `SELECT kp_id, tag_text FROM kp_tag_map ORDER BY tag_text` 建立反向表；每星取全部标签（去重、字典序）。**定向练习入口用它，不用 `name`**——`start_session` 是按 `q.knowledge_points` 的标签精确匹配题目池的，传 `kp_id` 或 `name` 会得到"这些知识点下暂无带答案的题目"。

## 4. FSRS 可提取性

`app/services/memory_card_service.py` 新增（FSRS 知识留在该服务）：

```python
def retrievability_by_kp(db: Session, student_id: str,
                         kp_by_content: dict[str, str]) -> dict[str, float]:
    """按 kp_id 聚合 FSRS 可提取性：卡片 retrievability 的算术均值。
    只统计 last_review 非空且 stability > 0 的卡；无有效卡的码不出现
    （调用方按 None = 未复习，而不是 0 = 已遗忘）。"""
```

- `MemoryCard JOIN Question` 取该学生全部卡（不 limit——`due_cards(limit=100)` 只服务褪色集合）。
- 单卡：`_scheduler().get_card_retrievability(_card_from_row(row), now_utc)`。
- 题 → 码用 `kp_by_content[question.content]`；映射不到的题不计。
- `last_review is None` 或 `stability <= 0` → 跳过（避免除零与"新卡=0"的误读）。

## 5. 学生星图编码

| 维度 | 教师端（现状） | 学生端（新） |
|------|----------------|--------------|
| 色相 | 册 | 册（相同 6 色） |
| 亮度 | 班级错误率反转 | **个人掌握度**：`mastery=null → BRIGHTNESS_FLOOR`；否则 `BRIGHTNESS_FLOOR + (1-BRIGHTNESS_FLOOR) * mastery` |
| 大小 | 班级作答量 | **个人作答次数**（复用 `starSize`） |
| 星型 | 错误率 ≥30% 暗星 + 四芒 | `mastery === null` 或 `mastery < 0.7` → 暗星 + 四芒；`>= 0.7` → 亮星 |
| 灰星 | 无可信样本 | **作答次数 = 0**（未探索） |
| 闪烁 | 无 | **周期 = 1.5 + 6.5 × retrievability 秒**；`retrievability=null` 或 `answers=0` 不闪 |

纯函数（`lib/starMap.ts`）：

```ts
export function studentStarKind(answers: number, mastery: number | null): StarKind
export function studentStarBrightness(mastery: number | null): number
export function twinklePeriod(retrievability: number | null | undefined, answers: number): number | null
export function starPhase(kpId: string): number   // 稳定哈希 → [0, 2π)
```

- `twinklePeriod`：r=0 → 1.5s（急促，快忘），r=1 → 8s（缓慢，稳）；`answers=0` 或 `r=null` → null。
- 闪烁在 `onBeforeRender` 内用 `performance.now()` 计算，**仅当组件收到 `twinklePeriodOf` 访问器时启用**；教师端不传，行为不变。
- 闪烁 alpha 系数限制在 0.70–1.00，永不超过基准透明度（不放大 bloom）。

## 6. 组件共享（最小抽取）

`components/star/StarMap.tsx` 新增可选 props，**默认值 = 现有教师行为**：

```ts
interface StarMapDisplayProps {
  kindOf?: (n: StarNode) => StarKind              // 默认 starKind(n.class_error_rate)
  brightnessOf?: (n: StarNode) => number          // 默认 starBrightness(n.class_error_rate)
  sizeOf?: (n: StarNode) => number                // 默认 starSize(n.class_answers)
  tooltipHtml?: (n: StarNode) => string           // 默认现有教师提示
  twinklePeriodOf?: (n: StarNode) => number | null// 默认 undefined = 不闪
  showErrorFilter?: boolean                       // 默认 true
  legendText?: string                             // 默认现有文案
  height?: number                                 // 默认 640
  quality?: 'full' | 'eco'                        // 默认 'full'
}
```

- 教师页（`KnowledgeMapAdminPage`）只改导入路径，**不传任何新 prop** → 行为零变化。
- 学生页传入学生访问器与 `showErrorFilter={false}`、`quality="eco"`、`legendText="越暗 = 掌握越低 · 闪得越快 = 越该复习"`。
- `StarDrilldownPanel` 留在 `components/teacher/`（教师专属），学生详情复用既有 `MobileFullPane`。

## 7. 页面结构

```
KnowledgeMapPage
├─ 视图切换：[星图] [地图]        ← 新增，默认「星图」
├─ 星图视图
│   └─ StarMap (eco, height≈520, 学生访问器, layout 来自响应)
├─ 地图视图（现状）
│   ├─ KpBubbleMap
│   └─ 分层卡式列表
└─ 详情面板（两种视图共用，既有 MobileFullPane）
    └─ 新增两行：可提取性 X% / 作答 N 次（有数据时）
```

- 学生页**不调用** `POST /api/knowledge-map/layout`（学生无 `question:update`，且布局由教师端首访补齐）；`onLayoutSettled` 不传。
- 点星 → `setSelected(kp_id)` → 既有详情面板；展示名用 `stars[].name`。
- **「练习此点」的入参是 `practice_tags[0]`，不是 `kp_id`、也不是 `name`**：`start_session` 用 `kp_set & set(q.knowledge_points)` 匹配题目池，只认标签。`practice_tags` 为空时按钮 MUST 隐藏并提示"该知识点暂无定向练习"。
- 现有详情面板/列表仍服务标签空间；星图选择的码必须先转成 `name` 再进面板。这是本变更唯一的 UI 适配点，必须在 tasks 里显式覆盖。

## 8. eco 模式

`quality="eco"` 时：

| 项 | full | eco |
|----|------|-----|
| BloomPass | 开 | **不创建**（省全屏后处理） |
| 星尘 | 1400 / 520 / 72 | 700 / 260 / 36 |
| 天空球分段 | 32 × 16 | 16 × 8 |
| 渲染像素比 | 设备默认 | `min(dpr, 1.5)` |
| 闪烁 | 开 | 开（CPU ≤160 颗，成本可忽略） |
| reduced-motion | 冻结 | 冻结 |

学生页固定 `eco`（`/m` 即移动端布局）；prop 保留给后续桌面学生端复用。

## 9. 访问与设计规范

- 学生端点既有 `PermissionChecker("analysis", "read", allow_self=True)` + `ScopeGuard("student_id", kind="student")`：**权限不变**，只加返回字段。
- 页面只用 `getMobileUser().id`，不接收任意 student_id 参数。
- `DESIGN.md` R-C9 修订：在"唯一显式例外"后追加一句——学生知识地图页（`/m/knowledge-map`）的星图卡片同属该例外，深底仅限于该卡片画布内，仍 NEVER 进全局 `data-theme`、NEVER 扩散到同页其他区域。

## 10. 回滚

- 后端：`stars`/`layout` 为新增字段，旧前端忽略即可；回滚只删字段与新服务。
- 前端：视图切换默认值可一行改回「地图」；`components/star/StarMap.tsx` 为移动文件，回滚即改回导入路径。
- R-C9 修订单独一行，可独立回退。

## 11. 不做

- 教师端视觉改动（另两个 change）
- 现有学生气泡图/列表的键空间迁移（保留标签空间；数据治理另立 change）
- 学生桌面端专属布局
- 2D 降级开关（eco 已覆盖性能诉求）
- 家长端星图
