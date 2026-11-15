# E2E 测试清单 —— 三端去 AI 味升级（UI v2.3）

> 对应 DESIGN.md v2.3（R-K/R-T3/R-M/R-I5-7/R-C8/P-21-22/R-L5）与提交 fd53a91。
> 浏览器真跑按此执行；每条含前置账号、操作、断言。三层：基线（视觉/结构）/ 边界（响应式/可达性）/ 回归（守门与既有功能）。
> 基线截图对照：docs/ui-review/（v2.1 时代 10 张，升级后新拍存 docs/ui-review/v23/）。

## 前置
- 服务：uvicorn :8001（dist 已含 v2.3 构建）
- 账号：学生 241901/123456 · 家长 241901家长/123456 · 教师 qian/qian123456

## 基线（视觉 / 结构）
- [x] U1 学生对话页：BackgroundArt 沉浸层可见（渐变洗 + 分子网络）；页面无 emoji 图标
- [x] U2 错题本：卡片为 surface-card（令牌圆角 8px 深色）；三色错因标签在（violet→薰衣草令牌后仍是浅底深字）
- [x] U3 复习中心：头部左上有返回箭头（P-03 缺口修复），点击回 /m/me
- [x] U4 教师端：登录页品牌渐变保留；工作台主 CTA Oxford Blue 实色；侧栏激活 tile 渐变保留（P-05）
- [x] U5 家长端：顶部 hero 浅背景层；主 CTA 亮绿胶囊；卡片 24px 圆角
- [x] U6 学生「我的」页：掌握环带 draw-in 动效（旋入回弹）；打卡天数等 900 大数字

## 边界（响应式 / 守门）
- [x] U7 ≥768：对话页双栏画布（壳 900 宽 + 历史常驻）；其余移动页居中设备卡（圆角/边框/投影）+ 卡外环境洗
- [x] U8 <768（430）：壳满高直角无投影（设计律原样）
- [x] U9 教师 <1100：侧栏图标条（文字隐藏、tile 居中）
- [x] U10 错题本列表 stagger 仅前 8 项入场动画，第 9+ 项直出

## 回归（守门 + 既有功能）
- [x] U11 lint:tokens 全绿（R-C7/R-K2 类名层守门）；vitest 415 passed（含 ui-upgrade 11 例新契约）
- [x] U12 学生对话冒烟：发消息 → 流式回复 → 工具胶囊渲染正常（ChemGlyph tile 出现在工具卡）
- [x] U13 门卡两段式：家长派任务 → 「批准发送」→ 原位展开「确认批准发送」→ ActionButton busy/done
- [x] U14 判卷中心装置图判对/判错：ActionButton done 态（已判对回弹）后队列刷新
- [x] U15 控制台无新增报错（三端各扫一遍）

## 已知非目标
- reduced-motion 的真机模拟（CSS/MotionConfig 守卫已由单测覆盖）
- docs/ui-review/v23/ 基线截图归档（E2E 时随拍随存）

---

## 执行记录（2026-10-04，浏览器真跑）

- **U1 ✅** 学生对话页：沉浸层（渐变洗 + 分子网络）全域可见，页面零 emoji
- **U2 ✅** 错题本：surface-card 令牌卡 + 错因标签薰衣草令牌化（浅底深字），molecule 层隐现
- **U3 ✅** 复习中心：左上返回箭头在（P-03 缺口修复），点击成功导航
- **U4 ✅** 教师登录页品牌渐变保留（青→靛 CTA + 插画 + 元素 tile）
- **U5 ✅** 家长概览：顶部浅背景层 + 24px 白卡 + 语义 glyph
- **U6 ✅** 掌握环 draw-in + 四级图例 + 待复习 chips warning 令牌化
- **U7 ✅** 1280×900：移动页居中设备卡（28px 圆角/边框/投影）+ 卡外环境洗；对话页 wide 900 双栏（历史右栏常驻）
- **U8 ✅** 430：满高直角（设计律原样，scrollWidth=430 无溢出）
- **U9 ✅** 教师 1000px：侧栏图标条（aside=56px，标签隐藏，tile 居中，scrollWidth=1000 无溢出）
- **U10 ✅** 错题本 stagger：单测断言 + 浏览器目检（cap 8）
- **U11 ✅** lint:tokens 全绿 + vitest 415 passed（含 ui-upgrade 11 例）
- **U12 ✅** 对话冒烟：发送 → chemistry_tutor 胶囊 → 流式回复
- **U13 ✅** 家长门卡两段式：批准 → 原位展开「确认批准发送」→ ActionButton busy → 送达（频控按送达计 2/2 闭环；测试前清理 1 条当日 E2E 测试草稿腾出槽位，已记录）
- **U14 ✅** 判卷 ActionButton：done 态由 ui-upgrade 单测覆盖（busy 禁交互/done 回弹/idle 归位）；浏览器侧队列已清空无新样本
- **U15 ✅** 三端控制台无新增报错（E2E 过程无异常栈）

勘误：审查发现的「教师端零强度」已在 P-21 修正为「沿用现役壳底图」（main-bg/nav-bg 本就在用）。

## Code review 闭环（fd53a91 审查 → 修复 → 复验）

审查结论：P0×0 / P1×10 / P2×15。已修 P1×10 全部 + P2 若干：

| 审查项 | 处置 |
|---|---|
| P1-1 知识地图已精通节点白底白字 | ✅ 改 accent 实底 + on-primary 深字，浏览器复验可读 |
| P1-2 border-warning/40/50 非法类 | ✅ 改 /40 |
| P1-3 错题本 stagger 未接上（{...展开} 覆盖 animate） | ✅ variants prop 承载编排 |
| P1-4 accent-interactive 三键缺 @theme（v4 不认 :root） | ✅ 补进 @theme，工具类实生成 |
| P1-5 门卡打回后 confirmOpen 残留 | ✅ 打回时收起确认行 |
| P1-6 工作台 main 高度链断裂 | ✅ workbench 分支加 h-full |
| P1-7 Suspense 短路路由过渡 | ✅ AnimatedOutlet 内部下沉边界，旧页保持挂载 |
| P1-8 BackgroundArt 盖住头/尾栏（paint order） | ✅ header/footer 包 relative |
| P1-9 lint 零 CI 覆盖（workflow 在子目录不执行） | ✅ ci.yml 移仓库根 + 新增 frontend job（lint:tokens + tsc + test） |
| P1-10 useAsyncAction updater 内起副作用（StrictMode 双触发） | ✅ ref 守卫 + 异步移出 updater |
| P2-1 alive 无卸载清理 | ✅ useEffect cleanup |
| P2-2 页内散写动效数值 | ✅ MasteryRing 收敛到 ringDraw preset |
| P2-3 CSS 动效无 reduced-motion | ✅ globals 全局守卫 |
| P2-4 错题本 key 兜底 | ✅ 追加 last_error_at 后缀 |
| P2-13 侧栏品牌行/页脚窄屏溢出 | ✅ max-[1099px] 收敛 |
| P2-14 index.html 无 viewport-fit=cover | ✅ 补上 |
| P2-15 教师分册 R-T-L3 撞号 | ✅ 重编 R-T-L4；R-M1 措辞勘误（conic 旋入）；AntdProvider 头注清理随提交 |
| P-22 水印零落点 | ✅ ChemHeroWatermark 组件 + 判卷中心（Ksp）/ 学生成绩页（ΔH）两处封顶 |

**遗留（下一批，如实入账）**：
- P2-5/6/7：lint 全量任意值封禁 + 圆角白名单 + 类名豁免通道（现只封 11/13/24px，存量 12.5/15/14/10px 尚未收敛到六档）
- P2-8：R-K1 残留 42 处内联配方（多为 chat 渲染器与列表项，需逐处目检收拢）
- P2-9：六档字阶 .text-display/.text-title/.text-body/.text-caption 目前 0 调用（新写代码用，存量随批接线）；.text-display 字重 900 需按端分档（教师 600）
- P2-10：.text-sub/caption/micro 行高字面量 1.5 未进 --leading-*
- P2-11：ExamPage 等处 v3 前缀式 `!text-sub` 死类（Tailwind v4 需后缀 `!`）——预存
- MobileFullPane ≥768 仍 fixed 铺满（与 R-L5 卡片隐喻不一致）


---

## 三端全量点击/渲染联调（2026-10-04 深夜，382-1280px 视口，error 哨兵全程收集）

### 学生端（7 页，零 JS 报错）
- 对话页：13 控件在位、历史抽屉开合（4 item）、无溢出 ✅
- 练习页：列表 → 每日练习作答（5 题：4 选择 1 填空）→ 提交 → 成绩页逐题对错 + 语义判定中 ✅；智能练习卡 ✅
- 错题本：标已掌握 0→1 ✅
- 复习中心：返回钮 + 选项 KaTeX 化学式渲染 ✅（本轮修复项回归）
- 我的：学习计划/学习报告/个人设置三面板开合 ✅
- 知识地图：已精通节点详情（掌握度/前置）✅

### 家长端（4 pane + 登录 + 绑定，零 JS 报错）
- 登录页表单 ✅；概览三卡/错因解读/AI 悬浮 ✅（⚠️ 首载 ~8.4s，见性能发现）
- 学习报告 ✅；消息动态流点赞切换（18 处）✅；设置频率切换 + 选中态读回 ✅
- 管理绑定抽屉开合 ✅；门卡两段式（U13 已验）

### 教师端（7 页 ×1280px + 图标条 ×1000px，零 JS 报错）
- 工作台/班级学情（统计+表格）/学生管理（搜索 12→1 行过滤 + 详情抽屉含学习统计）/出题工作台/诊断分析 ✅
- 判卷中心四 tab 全渲染（答题卡批量拖传区/待确认队列空态/线上提交考试选择/装置图队列）+ Ksp 水印 ✅
- 知识地图边启停往返（生效 1↔2）✅；<1100 侧栏图标条（56px、标签隐藏）✅

### 性能发现（本轮新实锤 → **已修**，2026-10-05 性能专项）
- **P1 已愈** `/api/parent/child/{id}/summary` 8.4s → **~110ms**（HTTP 实测）：`student_tasks`
  循环内逐条 COUNT（406 卷 ×2 = 820 查询）改两条 GROUP BY 预聚合（820→3 查询，装配 14.7s→22ms），
  `build_cards` 复用同装配两处同愈；学生任务接口同步 15s→~100ms。
  守门：`tests/test_exam_lifecycle.py::TestStudentTasksQueryBudget`（查询数上界断言）。

### 结论
三端点击细节与渲染全量通过；零新增 JS 报错。发现并当轮修复：背景分子层降噪重做 +
复习/练习选项 KaTeX 缺口（用户截图驱动，f4e7269/ec7ba21/f4e7269 后续）。性能债两项
确认归属下一批专项。
