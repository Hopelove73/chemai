## Why

教师端现有知识地图是 ECharts 力导向的「工程图」：节点挤成一团、状态靠图例猜，看完还得去学情页翻数据——教师高频问题「这周该讲什么」得不到直接回答。对标发现（大肥鱼「会遗忘的大脑」可视化、3d-force-graph 深空图谱范式）：把知识点画成**一片星域**，掌握度编码为亮度，暗星密集区一眼即教学盲区，点暗星当场出变式题——星图从「看」变「教」。知识地图 change（REQ-KMV-005）中桌面地图页本就声明待下批，本变更即该批次的形态升级。

## What Changes

- **替换** `/kmap` 页 graph 视图引擎：ECharts force → **react-force-graph-3d**（ThreeJS/WebGL，bloom 辉光 + 发光 sprite）；list 视图保留为保底
- **新增** 深底独立接缝：星图画布整页深色，作为教师端「浅色工作台」红线的显式唯一例外（DESIGN.md 增补 R-C9，独立令牌不劫持全局 theme）
- **新增** 班级维度数据：`GET /api/knowledge-map/class/{class_id}/graph`（复用 kb_error_rate 作答事件口径与去重逻辑，仅加班级过滤）+ `GET .../kp/{kp_id}/weak-students`（下钻懒加载）；节点带全班错误率/作答量
- **新增** 交互闭环：单星下钻面板（知识点档案 + 薄弱学生 + 先修上下文）→ 面板内「出变式题」跳转出题工作台并预选知识点（走既有确认门）；不做框选多星、不内嵌对话
- **新增** 按班切换（教师任教班可切，默认第一个班）；161 星按 6 册/专题聚为星系团，active 先修边画星座连线
- **编码纪律**：亮度 = 掌握度（班级错误率反转），大小 = 作答量；无样本（< MIN_ANSWERS）灰星；**不做**任何模拟的时间衰减（真 FSRS 可提取性属 star-map-student 个人粒度）

## Capabilities

### New Capabilities
- `class-star-map`: 教师端班级星图（班级图谱读侧、星编码纪律、深底接缝、下钻与出题跳转）

### Modified Capabilities
- 无（本变更交付 REQ-KMV-005 声明的桌面地图页；学生端地图视图与教师边管理不受影响）

## Impact

- **新增文件**: `frontend/src/components/teacher/StarMap.tsx`, `frontend/src/components/teacher/StarDrilldownPanel.tsx`
- **修改文件**: `app/services/kb_error_rate.py`（可选 class_id 过滤）, `app/api/knowledge_map.py`（两个只读端点）, `frontend/src/pages/teacher/KnowledgeMapAdminPage.tsx`（graph 视图换引擎）, `frontend/package.json`（+react-force-graph-3d three）, `chemai-backend/DESIGN.md`（R-C9）, 出题工作台（URL 参数预选知识点）
- **依赖**: react-force-graph-3d + three（路由级 code-split 按需加载）
- **测试**: 端点聚合 pytest（班级过滤/MIN_ANSWERS/静默降级/他班 403）；星图数据映射纯函数 vitest；手动验收清单（深底/辉光/下钻/跳转/班级切换/list 保底）
