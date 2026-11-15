# Proposal: integration-persistence

## Why

LMS 集成端是**展示性占位**（`app/api/integration.py` 8 处 TODO + 前端 PlaceholderNotice 如实声明）：API 密钥硬编码空列表、不校验不落库；Webhook 注册存进程内存**重启即丢**；调用日志无存储可查；LMS 配置改完不保存。集成页的交互骨架已通过验收，管理员配好的东西一刷新就没——这不是缺功能，是数据层缺席。依据 `docs/管理后台建设方案_20261104.md` P2 第 4 项。

## What Changes

- **新增** 三张表：`integration_api_keys`（密钥哈希落库、明文仅签发时展示一次）、`integration_webhooks`（注册持久化 + 启停）、`integration_logs`（投递与配置动作留痕）
- **修改** 端点转正（门不变，`integration` 资源仅 admin）：LMS 配置 GET/POST 真实读写；API 密钥签发/列表（掩码）/删除走表；Webhook 注册/列表/删除持久化、重启不丢；`GET /integration/logs` 读表
- **新增** Webhook 投递留痕：`lms_connector.trigger_webhook_event` 与测试投递的结果（成功/失败/耗时）写 `integration_logs`；**既有触发点不变**，本 change 不扩张事件接入面
- **新增** API 密钥校验路径作为可复用依赖（哈希验证 + 权限清单比对），单元测试覆盖；**但本 change 不新增以 API key 认证的消费端点**（真接 LMS 时开放，与 rbac HANDOFF 的 LTI 备注一致）
- **移除** IntegrationPage 的 PlaceholderNotice（占位语义消失）；`ops-dashboard` 集成健康卡的占位标注随之转为真实数据
- **非目标**：动态 JWKS（真做 LTI 时处理，TODO 保留）；Webhook 事件面扩张（哪些业务事件触发投递维持现状）；密钥轮换策略（首版只有签发/删除）

## Capabilities

### New Capabilities
- `integration-persistence`: LMS 集成数据层——配置/密钥/Webhook 注册持久化、投递留痕、密钥哈希与校验依赖

### Modified Capabilities
- 无

## Impact

- **新增文件**: 三张表迁移, 密钥哈希/校验服务, 投递留痕写入
- **修改文件**: `app/api/integration.py`（8 处 TODO 消除）, `app/services/lms_connector.py`（投递结果落日志）, `frontend/src/pages/teacher/IntegrationPage.tsx`（移除占位横幅）
- **测试**: 持久化往返 pytest（配置/密钥/Webhook 重启语义用新建 store 实例模拟）、密钥只展示一次与哈希存储、投递留痕（成功/失败两态）、权限回归（矩阵全绿）、vitest 占位横幅移除后的页面状态
- **依赖**: P0（集成页已有菜单承载）；`ops-dashboard` 的集成健康卡消费本 change 的真实数据（其占位协议按端点 `placeholder` 标记解除）
