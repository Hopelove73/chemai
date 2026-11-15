# Design: role-visibility

## Context

`ROLE_PERMISSIONS`（治理域 + 内容域两次合并）+ `ROLE_LABELS` + `RequestScope` 是本项目权限的全部真源，被 300+ 测试围绕。任何"UI 改权限"的方案都等于重构 RBAC，且容易让管理员把自己锁死。

管理员真实需求不是"改权限"，是"为什么我看不到/别人能看到"。

## Goals / Non-Goals

**Goals**
- 一眼看清 7 个角色各自能做什么、管到哪
- 矩阵与代码逐项一致（防漂移）

**Non-Goals**
- 不做权限编辑、自定义角色、角色分配
- 不引入数据库权限表

## Decisions

**D1 — 端点直接从 `ROLE_PERMISSIONS` / `ROLE_LABELS` 计算后返回，不落库、不缓存。**
数据量 < 100 项，实时计算零成本；落库就会出现第二份真源。

**D2 — 矩阵输出为 `{role: {"resource:action": true}}` 的扁平结构，前端按资源分组渲染。**
避免前端重复实现矩阵合并逻辑（`CONTENT_PERMISSIONS` 的 update 合并容易漏）。

**D3 — 数据范围说明文案按角色硬编码在前端常量，后端只给范围枚举值。**
"本学科任教班级"这种解释是产品语言，属于展示层。

**D4 — 人数统计在端点内一次 group by 完成。**
不逐个角色查询。

## Risks / Trade-offs

- [矩阵随代码变化但页面没更新] → 不加缓存，每次请求实时派生；测试断言"端点输出 == ROLE_PERMISSIONS"。
- [管理员误以为可以改] → 页面顶部与矩阵区各一处明示"只读，变更需发版"。

## Migration Plan

纯增量：一个端点 + 一个页面。回滚 revert。

## Open Questions

- 无
