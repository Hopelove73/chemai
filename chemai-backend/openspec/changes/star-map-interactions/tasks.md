# Tasks: star-map-interactions

## 1. 纯函数与测试
- [x] 1.1 `prereqClosure`（反向 BFS + 直接后继 + 环防御）
- [x] 1.2 `matchStars`（子串/编码匹配、前缀优先、上限 8）
- [x] 1.3 `isGhost`（册显隐 + 错误率下限 + 无样本）
- [x] 1.4 vitest 覆盖以上

## 2. 前端交互（顺序 = 实施顺序）
- [x] 2.1 #6 减弱动效：巡航初始值读 matchMedia
- [x] 2.2 #2 关系高亮：visRef 通道 + onBeforeRender 透明度 + 连线淡出 + 粒子
- [x] 2.3 #1 搜索框 + 飞行定位 + selectStar 统一入口
- [x] 2.4 #3 图例切换 + 错误率滑条 + 幽灵态
- [x] 2.5 #4 CSS2D 标签（Group 包装 + 显示规则 + 距离淡出 + pointer-events none）
- [x] 2.6 #5 加载坐标 → fx/fy/fz 固定；首次收敛 POST 缺失坐标

## 3. 后端
- [x] 3.1 `kp_layout` 表
- [x] 3.2 `GET/POST /api/knowledge-map/layout`（只插入不覆盖）
- [x] 3.3 pytest：插入/不覆盖/读取/权限/校验

## 4. 验收
- [x] 4.1 验证台：每项截图 + 引擎状态断言（高亮透明度、相机飞行距离、幽灵数量、标签可见集、二次加载坐标一致）
- [x] 4.2 pytest 229 passed / vitest 22+10 passed / tsc clean / build ok
- [x] 4.3 实测记录：布局收敛自动上报 161 坐标入库；二次加载坐标固定；近景标签 119 个可见；系统减弱动效下巡航默认暂停
