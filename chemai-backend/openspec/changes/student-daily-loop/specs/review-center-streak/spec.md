# Review Center Streak

今日复习中心：消费已建成但零调用的 `/api/review/*` 全家桶（CONTEXT.md：到期卡浮现于学生「今日复习」）；复习连击（Review Streak）作为唯一游戏化资产；FSRS 与官方生态对齐。

## ADDED Requirements

### REQ-RCS-001: Due Queue Review Flow
学生按到期队列完成复习。

**Acceptance:**
- 复习中心页：到期卡列表（`GET /api/review/student/{id}/due`）+ 到期计数徽章（底栏，`GET .../count`）
- 复习作答：重做原题自动判定为主信号；客观题用选项按钮，主观题用 ChemComposer（依赖 answer-composer）；自评（忘了/模糊/记得）兜底——`POST /api/review/submit`
- 复习历史可回看（`GET /api/review/history/{id}`）
- 旧的「复习中心占位页」（只读错题列表）被替换，与错题本不再重复

### REQ-RCS-002: Review Streak
每日清卡产生连击资产。

**Acceptance:**
- `student_streak` 表：`{student_id, current, best, freeze_count, last_active_date, repair_used_week}`（freeze ≤ 2）
- 规则纯函数（`streak_service` 内，scheduler 与 API 共用）：当日清完全部到期卡 → current+1、best 取 max；漏天 → 有冻结自动消耗 1 张（连击不断），无冻结连击清零
- 冻结卡发放为常量规则：每周（ISO 周）首次清空当日全部到期卡奖励 1 张，上限 2；补签修复窗口每周 1 次（漏天 48h 内清卡回连）
- 时区：Asia/Shanghai 自然日，服务端计算，不信任客户端时钟
- 表驱动单测：清卡 / 漏天有冻结 / 漏天无冻结 / 修复窗口四分支 + 边界（周界、上限）

### REQ-RCS-003: Official FSRS Alignment
自研调度与 py-fsrs 官方库对齐。

**Acceptance:**
- 自研 FSRS 调度经接口适配层调用 py-fsrs（MIT）；现有调度测试全绿
- `scripts/calibrate_fsrs.py`：离线读真实答题日志 → fsrs-optimizer 拟合权重 → 参数入库；适配层读库参数；**不在线训练**
- 校准前后复习间隔对比在脚本输出中可读（供验收）

### REQ-RCS-004: Due Badge in Tab Bar
到期数常驻可达。

**Acceptance:**
- 底栏「复习」tab 徽章显示到期数（tabs 单一真源的 badge 挂点）；数字与 `/count` 一致；0 时隐藏

### REQ-RCS-005: Anki Export (optional)
错题卡可导出 Anki。

**Acceptance:**
- `GET /api/review/student/{id}/anki-deck`（allow_self）经 genanki 产出 `.apkg`，仅含该生自己的卡（ScopeGuard）
- 排批 1 尾，可单独砍掉不影响其余验收
