# Proposal: break-glass-workbench

## Why

super_admin 的跨校数据访问依赖 break-glass 限时提权（ADR-0011），后端三个端点（发起/查生效/提前结束）已完整实现且自带纪律（理由必填、限时上限、不叠开、全程审计），但 super_admin 没有任何界面入口——提权要靠 API 直调，与「用完即掉、留痕可查」的设计意图之间隔着命令行。P0 概览页对未提权超管只有空态提示，没有去路。依据 `docs/管理后台建设方案_20261104.md` P2 第 2 项。

## What Changes

- **新增** 管理菜单 `break_glass`（gate `break_glass:read`，仅 super_admin 经矩阵可见）
- **新增** 前端页面 `/admin/break-glass`（纯前端 change，**后端零改动**）：
  - 当前提权状态卡：生效中（理由/剩余秒数）或空态
  - 发起提权：理由必填 + 时长选择（受后端 `MAX_MINUTES` 上限约束，超限由后端 400 提示）
  - 提前结束：确认流（用完即掉）
- **非目标**：不做全部提权历史的跨用户审计视图（审计已落操作日志，可在 `/admin/logs` 看；提权会话是 per-user 语义，端点本就只返回本人会话）；不改提权后端逻辑

## Capabilities

### New Capabilities
- `break-glass-ops`: super_admin 提权工作台——状态查看、限时提权发起、提前结束，全部复用既有端点与审计

### Modified Capabilities
- 无

## Impact

- **新增文件**: `frontend/src/pages/admin/BreakGlassPage.tsx`
- **修改文件**: `app/middleware/auth.py`（MENUS 加 1 项）, `frontend/src/App.tsx`（菜单注册 + 路由）
- **测试**: 菜单下发断言（super_admin 可见 / admin·teacher 不可见）、vitest（理由必填校验、倒计时、revoke 确认流）、e2e 手动（发起 → 数据可见 → 到期失效/提前结束）
- **依赖**: P0（管理壳）
