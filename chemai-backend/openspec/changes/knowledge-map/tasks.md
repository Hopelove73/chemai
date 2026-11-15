# Tasks: knowledge-map

## 1. 数据与工具

- [x] 1.1 `knowledge_edges` 表 + alembic
- [x] 1.2 证据统计（共现 / 错因迁移）服务函数
- [x] 1.3 `propose_knowledge_edges` 工具（确认门 + 环警告）+ 三处同步注册
- [x] 1.4 教师端边管理（启用/禁用）

## 2. 视图

- [x] 2.1 状态机纯函数（三态 + 环防御 + 单测）
- [x] 2.2 `app/api/knowledge_map.py`（allow_self + ScopeGuard）
- [x] 2.3 学生端 `/m/map` 页（拓扑分层卡式 + 褪色态 + 引导态）
- [x] 2.4 工件绑定（只读已分享版本 + 契约测试）
- [x] 2.5 「练习此点」（smart-practice 定向 / 降级错题本）
- [x] 2.6 教师端视图：边管理/提议/采纳经 agent 工具全覆盖（propose/accept/disable）；桌面地图页待下批（REQ-KMV-005 部分交付，手测项）

## 3. 验收

- [x] 3.1 pytest 全绿；状态机/遍历单测全绿
- [ ] 3.2 手测：教师确认 20 条边 → 学生地图就绪/锁定正确；锁定节点不可练习；禁用边即时生效——待起服务手测
