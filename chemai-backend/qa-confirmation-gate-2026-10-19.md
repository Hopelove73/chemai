# 确认门显示修复与三端验收记录

日期：2026-10-19（北京时间）。当前状态：代码修复完成；本地影子测试服务已启动；真实三端验收被内置浏览器的站点访问授权阻塞，尚未执行。

## 版本

- 当前分支 `codex/1018`，HEAD `61480648f8b3107cd96e5e6c59474523c7e74026`。
- 提交时间 `2026-10-19 01:34:55 +08:00`，主题 `no-mistakes(review): fix(gate): bound-step projection and behavior-based entry tests`。
- 本地 `improve` 与 HEAD 相同，是按提交时间排序的最新本地版本。
- 权限更新后已实时 `git ls-remote` 并 `git fetch origin main --no-tags`：远端 HEAD/main 为 `3bcd5e4a2d7ccb1d8b6cfb2580253a1bdfa1abdf`（2026-09-14 14:49:52 +08:00，`style: ruff 自动修复（管线修复引入的残留）`）。本地 HEAD 相对它领先 196、落后 0；远端未返回 improve/codex/1018 同名分支。
- 本次未提交、未推送。用户自行删除的 14 个 UI组件文件保持原状。

## 已修复

1. 门拦下且未执行的工具调用显示中性“已暂缓 · 等待确认”，保留批准后实际执行的独立记录；真实错误仍显示失败。
2. 任务规划工具卡显示每步的未开始、执行中、等待确认、修订、完成、失败、放弃和未知状态；流事件同步，历史刷新保留最新完成计划快照。
3. 活动计划身份跨新回合保留，避免 A→B→A 重规划后的进度误改 B。
4. 验证期间发现异步按钮会产生未处理 Promise 异常，两个调用方吞错还会误显示“已发送/已判定”。现已明确失败回调，失败显示错误、回到可重试状态。
5. 学生通知界面两处 11px 字号改为同值语义令牌，恢复 token lint。

## 已执行验证

- 门等待、规划进度、历史恢复、门循环和 reducer 专项：71/71。
- 异步按钮及其调用方：14/14，原实现回归失败、修复后通过，无未处理拒绝。
- 最终全量：56 个测试文件、453 项测试全部通过，退出码 0（`npx vitest run --maxWorkers=2`）。
- `npm run typecheck`、`npm run lint:tokens`、`npm run build`、`git diff --check` 通过。
- 全量日志：`frontend/qa-vitest-final.log`；构建日志：`frontend/qa-build-final.log`。
- 保留警告：search-results 测试 worker 退出超时；断言通过，未查明退出延迟原因。构建仍提示已有的大 chunk。
- 权限更新后父线程现有 Python 3.11 环境可用：计划恢复专项 11/11，历史结构/门决定循环/重启阻挡/门绑定/步骤绑定/待确认门接口专项 72/72，通过且退出码 0。测试使用项目影子数据基座和内存 SQLite。
- 后端日志：`.scratch/qa-plan-restore-2026-10-19.log`、`.scratch/qa-gate-regression-2026-10-19.log`。有已有依赖与弃用警告。

## 当前内置浏览器阻塞

用户更新权限后，父线程可正常执行 shell、Python 与联网；已在 `http://127.0.0.1:8001` 启动真实应用，首页 HTTP 200，标题“智辅化学 · 教师工作台”。服务调用 `app.shadow_data.activate()`，主库、checkpoints、store、memory、Chroma 均使用临时副本，`NOTIFY_EXTERNAL_GATEWAYS=false`。原始演示数据库没有改动。

内置浏览器 `cua.createBrowserTab("iab", "http://127.0.0.1:8001/#/login", ...)` 首次返回用户拒绝站点访问。用户回复已允许后重试，返回：

> A saved user permission setting blocks this action. A saved permission or managed policy denies access.

工具明确禁止通过其它主机别名、端口、浏览器表面、原始 CDP 或间接调用绕过。未尝试此类绕行。

只读 inventory 证实内置浏览器已连接，只有一个 `about:blank` 标签；公开接口与附带故障文档没有提供清除站点拒绝设置的方法。尚未核实桌面端具体设置入口，因此不向用户虚构操作路径。

## 自动审批故障

真实 computer-use 的 `sky.list_apps()` 未执行，自动审批返回：

> Automatic approval review failed: 503 Service Unavailable: No available channel for model gpt-5.6-luna under the current group (distributor)
> https://api.codexzh.com/v1/responses

主模型配置为 `gpt-6.1-sol`，provider 为 `codexzh`，不是主模型被切换。项目配置虽有 `danger-full-access`，当前会话实际策略仍为 workspace-write / auto_review。

已检查 Codex CLI `0.163.0-alpha.10.2` 的帮助、已安装二进制和生成的协议 schema：审批路由支持 user/auto_review/guardian_subagent，但未找到受支持的审批模型配置字段。`review_model` 的存在不能证明它用于审批；未猜测配置键、未禁用审批、未修改安全策略或模型缓存。

修复需要渠道管理员恢复 distributor 分组中的 gpt-5.6-luna 可用渠道，或提供受支持且有效的审批模型映射。当前无该服务的管理权限。官方配置文档访问也返回 403，结论依安装版本的本地证据。

此前诊断临时目录 `D:\化学\qa-approval-diagnostic-schema` 已在权限更新后核验绝对路径、文件类型和链接后清理。

## 恢复后的待验收任务

使用真实 computer-use，不用 API 或 mock 冒充 UI 验收：

1. 教师查找周翔 241901，读真实薄弱点，生成三天、每天两题的个性化方案。
2. 检查审批卡内容、打回修订、批准、放弃；批准前无派发，批准后恰好一次；逐步状态和刷新恢复正确。
3. 学生查看并决定计划，完成针对性练习；教师查实际进度。
4. 生成并同步学情给绑定家长，在家长实际消息/报告界面核对收到的内容、对象和版本。
5. 按实际绑定能力覆盖教师 46、学生 13、家长 13 项 Agent 功能及权限边界，记录逐项结果。整班分发、正式成绩、公共知识边等改动使用隔离测试数据。

截至本记录：没有操作任何真实三端界面，没有产生业务分发，也没有宣称三端通过。
