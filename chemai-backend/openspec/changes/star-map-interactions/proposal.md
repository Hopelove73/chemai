## Why

教师端班级星图（star-map-teacher）已交付渲染与下钻；对标 Obsidian Graph、Neo4j Bloom、3d-force-graph 官方示例的差距分析（docs/星图差距分析_20261101.md）显示，短板集中在日常交互的三件事：**找得到、看得懂关系、筛得出**。本变更交付差距报告 P1 的全部 6 项（2026-11-01 grill 全按推荐定案）。

## What Changes

- **#6 减弱动效合规**：`prefers-reduced-motion: reduce` 时巡航默认关闭（手动开关仍可用）——补 DESIGN.md R-M1 在三维画布上的存量缺口
- **#2 关系高亮**：选中星时高亮**整条上游先修链（全部祖先）+ 直接后继**，其余星与连线淡出；高亮连线上粒子从前置流向后续
- **#1 搜索 + 飞行定位**：画布工具条内搜索框（全屏可用），按名称/编码模糊匹配；命中后镜头飞到星前，自动选中、打开下钻面板、巡航自动暂停
- **#3 筛选**：图例可点击切换各册显隐；错误率下限滑条（只看错误率 ≥ X%）；被筛掉的星淡成幽灵（保留空间参照，不重排）
- **#4 常驻标签**：暗星（薄弱）、选中星（及其高亮链）、镜头附近的星显示名称，远景淡出；HTML 叠加层（CSS2DRenderer），不受 bloom 影响
- **#5 布局持久化**：后端全校一份知识点坐标（布局只取决于知识点与先修边，与班级无关）；首次收敛后补齐缺失坐标、**只插入不覆盖**；已存坐标的星固定，新增知识点在本册锚点附近落位

## Capabilities

### New Capabilities
- `star-map-interactions`: 星图交互层（搜索定位、关系高亮、筛选、标签、减弱动效、布局持久化）

### Modified Capabilities
- 无（class-star-map 的编码、下钻、出题跳转保持不变）

## Impact

- **新增**: `kp_layout` 表（create_all 建表，零迁移）；`GET/POST /api/knowledge-map/layout`；`StarMap` 工具条搜索框与筛选控件
- **修改**: `app/models/database.py`, `app/api/knowledge_map.py`, `frontend/src/components/teacher/StarMap.tsx`, `frontend/src/lib/starMap.ts`（纯函数：先修闭包、搜索匹配、筛选判定）
- **依赖**: 无新增（标签用 three 自带 CSS2DRenderer）
- **测试**: 纯函数 vitest（先修闭包/匹配/筛选）；layout 端点 pytest（插入不覆盖/权限/校验）；验证台截图 + 引擎状态断言
