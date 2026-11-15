# Star Map Interactions

教师端星图交互层：搜索定位、关系高亮、筛选、常驻标签、减弱动效、布局持久化。

## ADDED Requirements

### REQ-SMI-001: Reduced Motion
尊重系统「减弱动效」偏好。

**Acceptance:**
- `prefers-reduced-motion: reduce` 时巡航默认关闭；手动开启/暂停仍可用

### REQ-SMI-002: Prerequisite Highlight
选中星时读懂先修关系。

**Acceptance:**
- 高亮集合 = 选中星 + 全部上游祖先（沿 active 先修边反向闭包）+ 直接后继；其余星与连线淡出
- 高亮连线上粒子沿「前置 → 后续」方向流动；取消选中后全部复原
- 状态切换不重建星体 sprite（无闪烁）

### REQ-SMI-003: Search and Fly-to
在星图里找到知识点。

**Acceptance:**
- 画布工具条内搜索框（全屏可用），按名称子串或编码匹配，下拉最多 8 条
- 选中结果：镜头动画飞到星前、自动选中并打开下钻面板、巡航自动暂停（与点星同一路径）

### REQ-SMI-004: Filters
筛选出关心的星。

**Acceptance:**
- 图例 6 册可点击切换显隐；错误率下限滑条（0–100%）
- 被筛掉的星呈幽灵态（保留位置与空间参照），端点为幽灵的连线淡出；筛选不触发重新布局

### REQ-SMI-005: Persistent Labels
不悬停也知道星是什么。

**Acceptance:**
- 暗星、选中星、高亮链上的星、镜头附近的星显示名称；远景淡出；幽灵星不显示
- 标签为 HTML 叠加层，不受 bloom 影响，且不拦截画布交互

### REQ-SMI-006: Persistent Layout
布局稳定，老师可靠空间记忆找星。

**Acceptance:**
- 后端保存一份全校共享的知识点坐标；`POST` 只插入缺失、不覆盖已有
- 已有坐标的星位置固定；新增知识点在本册锚点附近落位
- 首次加载补齐坐标后，后续加载布局完全一致
