# Proposal: runtime-settings

## Why

后端有一批行为旗子只能靠环境变量在**启动时**决定：`MINERU_ENABLED`（MinerU 解析开关）、`OCR_SHEET_PROVIDER`（答题卡识别引擎 paddle/baidu）、`OCR_VISION_REVIEW_ENABLED`（识别结果视觉复核）。切换任何一个都要改 env + 重启后端——课堂上/学期中不可行，而这类切换恰恰是运营常态（识别引擎故障时临时换引擎、控制视觉复核成本）。qiuqiu/teaching-assistant 均以「settings 表 + 白名单热切换（≤5s 生效）」解决同款问题（qiuqiu ADR-0006 体系）。依据 `docs/管理后台建设方案_20261104.md` P2 第 5 项。

## What Changes

- **新增** 资源键 `settings`（`read`/`update`，仅 admin，super_admin 经镜像继承）——与消费端点同批落地
- **新增** 管理菜单 `admin_settings`（gate `settings:update`）与 `/admin/settings` 页面
- **新增** `settings` 表 + RuntimeSettings 服务：**白名单键集**（初始仅上述 3 个既有旗子）、类型校验（布尔/枚举）、短 TTL 缓存（约 5s）读侧热生效；未覆写的键回落 env 种子默认值
- **修改** 三个消费点（ocr_service / document_parse_service 等）从「模块加载时读 env 单例」改为「每次调用经 RuntimeSettings 读取」——行为默认值不变
- **新增** 端点：`GET /api/settings`（`settings:read`）、`PUT /api/settings/{key}`（`settings:update`，校验 + 写审计含旧值→新值）
- **非目标**：不做通用 KV 配置存储（白名单外键一律拒绝）；不做凭据类配置的界面化（API key 等敏感值永不入此表）；不做旗子注册框架（新增可切换旗子 = 代码改动 + 白名单登记，是有意的摩擦）

## Capabilities

### New Capabilities
- `runtime-settings`: 运行时开关——白名单旗子的界面热切换、env 默认回落、修改审计

### Modified Capabilities
- 无

## Impact

- **新增文件**: settings 表迁移, RuntimeSettings 服务, `app/api/settings.py`, `frontend/src/pages/admin/AdminSettingsPage.tsx`
- **修改文件**: `app/middleware/auth.py`（新资源键 + MENUS）, `app/main.py`, OCR/解析服务的旗子消费点, `frontend/src/App.tsx`
- **测试**: 角色断言（admin 可改 / teacher·academic_admin 403）、白名单外键拒绝、类型/枚举校验、热生效时序（改后 ≤5s 新请求用新值）、消费点行为回归（默认值不变）、审计用例
- **依赖**: P0（管理壳）
