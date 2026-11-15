# Design: integration-persistence

## Context

`app/api/integration.py` 交互骨架完整（16 端点）但数据层为占位：LMS 配置不保存（:103）、密钥硬编码空列表（:286）与内存字典（Webhook Service，重启即丢）、日志无表（:349）、动态 JWKS TODO（:264）。投递机制已存在于 `app/services/lms_connector.py`（`trigger_webhook_event`），缺持久化与留痕。前端 IntegrationPage 的 PlaceholderNotice 已如实声明占位语义。

## Goals / Non-Goals

**Goals:**
- 配置/密钥/Webhook 三类数据重启不丢；投递全留痕
- 密钥哈希落库 + 校验依赖就绪（不含消费端点）
- 占位横幅与 `placeholder` 标记协议解除

**Non-Goals:**
- 动态 JWKS（LTI 实装时处理）
- API key 认证的业务端点接入（等真实 LMS 消费方）
- Webhook 事件面扩张、重试队列/退避（首版投递一次 + 留痕，失败可见）

## Decisions

**D1 — 三张表 + 迁移走 `app/utils/migrate.py` 增量惯例**（与 kb_review_records/settings 同口径）：`integration_api_keys`（name/secret_hash/permissions/status/timestamps）、`integration_webhooks`（event_type/url/secret/enabled/timestamps）、`integration_logs`（类型 config/delivery、event_type、url、结果、耗时、detail JSON、时间）。

**D2 — 密钥只存哈希，明文一次展示。** 签发生成 `ck_<random>`，SHA-256 哈希落库；校验依赖做常量时间比对。列表返回掩码（前 6 后 4）。备选加密可逆存储——否决：校验只需哈希，可逆存储扩大泄露面。

**D3 — Webhook 服务从内存字典改为「内存缓存 + 表持久化」的读写穿透**（写落表、启动时从表加载），保持 `register/trigger` 服务接口不变，调用方（含测试端点的临时注册）零改动。测试端点的「临时注册并触发」语义保留，但注册同样落表——测试投递记录进日志，注册可由管理员随后删除。

**D4 — 投递留痕挂在 `trigger_webhook_event` 与测试端点的结果聚合处**，同步写 `integration_logs`；不引入异步投递队列（首版投递一次，失败在日志可见）。

**D5 — 校验依赖交付为 `verify_api_key` FastAPI dependency + 服务函数**，本 change 不接到任何业务端点；单元测试钉住行为（身份/权限比对/已删除失效）。这是「钥匙先配好、门等 LMS 实装时开」——避免无消费者的认证面暴露。

**D6 — 占位协议解除**：integration.py 各端点移除占位返回特征（`placeholder` 标记不再下发）；IntegrationPage 删 PlaceholderNotice；ops-dashboard 集成健康卡按其 spec 的占位判定自动转真，本 change 不改其代码。

## Risks / Trade-offs

- [Webhook secret 明文落库] → 投递需要原文签名，必须可逆；表不脱敏则风险等同 env。缓解：secret 列不进任何列表响应、日志不落 secret；后续可换信封加密（实施说明里留口）。
- [投递留痕的写入放大（每次事件多一跳写）] → 投递本身是网络 IO（10s 级），一次 insert 可忽略；日志表按时间增长，查询端点强制分页。
- [密钥哈希无盐（SHA-256(key)）] → 密钥本身是高熵随机串（非口令），无盐哈希在此场景可接受（防拖库只读明文，不防彩虹表——高熵输入无意义）；设计说明留档。
- [与 ops-dashboard 的占位判定耦合] → 判定基于端点 `placeholder` 标记，本 change 移除标记即解耦，双方无代码依赖。

## Migration Plan

1. 迁移（三表）→ 服务改造（读写穿透）→ 端点转正 → 前端移除占位横幅。
2. 回滚：revert 后回到占位行为；三表留存无害。内存字典时代的既有 Webhook 注册本就随重启丢失，无数据迁移负担。

## Open Questions

- 无。
