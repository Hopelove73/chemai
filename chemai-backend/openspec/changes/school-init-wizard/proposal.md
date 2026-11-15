# Proposal: school-init-wizard

## Why

学校数据初始化目前是「开发者拿脚本/API 在场」的部署动作：`POST /api/school` 建校、`data_import` 三端点（名册/任教/考试成绩）灌数——API 已完备且带幂等与审计（`school-data-initialization` change 交付），但没有界面，每次部署/学期初导数都依赖开发者。管理员需要自己完成「新学期导一次成绩、补一批转学生名册」。依据 `docs/管理后台建设方案_20261104.md` P2 第 3 项。

## What Changes

- **新增** 前端部署导入向导（`/admin/school` 页扩展或独立入口），四步：学校信息（未初始化时建校，`school:create`/`school:update`）→ 学籍名册（`class:create`）→ 教学安排（`teacher:create`，仅 admin 可见）→ 考试成绩（`exam:create`）
- **新增** 客户端解析：粘贴文本或上传 CSV → 解析为行 → 预览与列校验 → 提交 JSON rows（与 `data_import` 请求模型同形）；**后端零改动**
- **新增** 结果摘要展示：复用各导入端点返回的新增/跳过计数（名册按学号去重、成绩同班同名同日期幂等——语义已在后端）
- **非目标**：不做服务端文件解析（不引 XLSX 依赖，CSV/粘贴够用；复杂 Excel 转换是用户侧事）；不做模板下载（文档给列说明即可）；不改变导入端点的幂等/审计语义

## Capabilities

### New Capabilities
- `school-init-wizard`: 学校初始化与数据导入向导——分步表单、客户端解析预览、提交与结果摘要，按步骤资源门显隐

### Modified Capabilities
- 无

## Impact

- **新增文件**: `frontend/src/pages/admin/`（向导组件）
- **修改文件**: `frontend/src/pages/admin/AdminSchoolPage.tsx`（承载向导入口）
- **测试**: vitest（解析/列校验/预览/按能力显隐步骤）、e2e 手动（粘贴一批名册 → 导入 → 结果摘要与 StudentsPage 数据一致）
- **依赖**: P0（`/admin/school` 页面已存在）
