# ADR-0022: 化学式渲染只在显示层做，API 响应按原样传输

**Date**: 2026-03-25
**Status**: Accepted（2026-03-25 待确认页浏览器走查发现的缺陷；用户裁定「摘掉中间件」）

## Context

`app/middleware/chem_render.py` 挂在应用上，对**所有** `application/json` 响应递归调用
`app/utils/chem_render.render_chem_deep`，把「大写字母 + 数字」改成 Unicode 下标
（`Fe2O3` → `Fe₂O₃`），另外还把 `->` 换成 ` → `、压缩连续空格、去掉首尾空白。
它的模块注释写着「铁律：全项目所有化学式统一经过此模块渲染」。

这条铁律把格式化放错了层，代价在 2026-03-25 的浏览器走查里实测到：

- `GET /api/agent/gates/pending` 的 `last_opened_at` 从 `2026-09-24T16:09:25` 变成
  `2026-09-24T₁₆:09:25`（响应字节 `e2 82 81` = U+2081）。库里存的是干净的
  `2026-03-24 16:09:25.799484`，是响应在出门前被改了。前端 `Date.parse` 得 `NaN`，
  待确认页的「已等待」整列显示 `—`。
- 命中规则的不是化学式，而是任何「字母紧跟数字」的机器值：ISO 时间戳的 `T16`、
  路径与文件名、`H1`/`C1` 这类 id、含大写+数字的 token 片段。
- 空格压缩与 `->` 替换会改写老师输入的内容本身（题干、代码片段、URL）。

范围是全站：当时 232 个端点的 JSON 响应都过这一层。

## Decision

**数据层存原样，显示层负责渲染。** 具体三条：

1. 摘掉 `ChemRenderMiddleware`（`app/main.py` 的注册与 import 一并删），并删掉只有它在用的
   `app/utils/chem_render.py`。API 响应里的字符串与库里存的、与 service 返回的**逐字节相同**。
2. 化学式渲染由前端承担，且**这条已经在跑**：`frontend/src/components/ChemMd.tsx`、
   `lib/chem-format.ts`、`lib/chem-markdown.ts` 已接在 ChatPage、ExamPage、OptionsRow、
   QuestionMd/AnswerMd、tool-renderers、移动端 ChatPanel/PracticePage/ReviewCenterPage 上。
   本次删除不影响任何展示位，因为展示位本来就有自己的渲染器。
3. 新加显示位要显示化学式时，接前端渲染器，不要退回"后端顺手改一下响应"。

## Consequences

- 时间戳、id、路径、token 在传输路上不再被改写。这一条是安全/正确性收益，不是风格偏好：
  被改写的值是**老师看不见差异**的那种（`T16` 与 `T₁₆` 在屏幕上几乎同形）。
- 后端返回的题目文本会以纯文本形式出现（`H2SO4`），显示端必须经渲染器。已有覆盖见上；
  新页面漏接的表现是"公式没下标"，属可见的样式缺陷，不再是静默的数据损坏。
- 如果哪天要在服务端生成带下标的文本（例如导出 PDF），那是**输出格式**，在导出路径上显式
  转换，仍然不碰 API 响应。
- 回归凭据：`tests/test_question_smiles.py` 里对 `SO₂` 的断言来自模型桩返回的文本本身，
  不依赖中间件；摘除后全量测试为凭。浏览器走查的那一条（待确认页「已等待」列）是这次的发现路径，
  记进 `docs/designs/gate-product-closure.md` 的走查小节。
