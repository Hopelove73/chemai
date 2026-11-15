# DESIGN.md — ChemAI 智辅化学 · AI 可执行设计规格（v2 · 三端分册制）

> **v2 合成来源**：v1（35号+38号，见 `design-upgrade/归档/`）× 三端融合提案（Academic Catalyst × Linear/Spotify/Wise，2026-09-14 产品定案）。
> **使用方式**：AI 生成或修改任何 ChemAI 界面代码前，**必读总册（本文件）+ 按目标端读对应分册**。分册参数优先于本册通用描述；两册都未覆盖的，查第四段缺口表，**禁止自行发明设计参数**。

## ① 三端索引（改哪端代码 → 读哪册）

| 端 | 分册 | 温度 | 模式 | 主操作色 | AI 声音 |
|----|------|------|------|---------|---------|
| 教师 Web（7 页，React） | [design/教师端.md](design/教师端.md) | 冷峻精密（Linear） | 浅色氛围工作台（品牌底图 + 玻璃卡），**无深色区域** | Oxford Blue `#002147`（v2.3 定案重开：主 CTA 实色；品牌渐变收敛至登录页品牌区唯一一处） | Teal `#0d7377`（浅底原值） |
| 学生移动（6 页，SPA /m/） | [design/学生端.md](design/学生端.md) | 活力深色（Spotify） | 深色 | 亮青 `#2dd4bf` | 同主色（单彩槽） |
| 家长移动（2 页，SPA /m/） | [design/家长端.md](design/家长端.md) | 温暖可信（Wise） | 浅色（鼠尾草底） | 亮绿 `#9fe870` | Teal（浅底变体） |

工程事实：教师端换肤走 `frontend/src/styles/tokens.css` + `AntdProvider.tsx`；学生/家长页已并入主 SPA（[ADR-0017](docs/adr/0017-mobile-into-react-spa.md)）。

## ② 全局规则（跨端 MUST/NEVER，端内细则见分册）

### 化学渲染
- **R-H1** 化学方程式 MUST 用 KaTeX + mhchem（`\ce{}`）。
- **R-H2** 纯文本化学式 MUST 转上下标（H2O → H₂O、Fe^3+ → Fe³⁺）。
- **R-H3**（2026-10-07 增）有机结构唯一约定 = 行内 `[SMILES:...]` 标记（出题/变式/学生 persona prompt 强制输出；service 端 `structure_render.py` RDKit+教材白名单渲染正式题库，前端 ChemMd→smiles-drawer 渲染聊天/变式/题干，解析失败回退 SMILES 原文）。NEVER 引入第二种结构标记（围栏/图片语法等）；工件（自包含 HTML）不消费本标记——其渲染库不进工件 bundle。

### 字体
- **R-T1** 化学式/代码/数字数据 MUST JetBrains Mono 等宽；KPI 数字加 `tabular-nums`。
- **R-T2**（修订）正文 MUST 系统栈（PingFang SC / Microsoft YaHei / Noto Sans SC），零 Web 字体；**display 层允许自托管子集化 Web 字体**（Noto Sans SC 900/700、Inter 600/900）；NEVER 使用 CDN Google Fonts。Cormorant Garamond 已退役。
- **R-T3**（v2.2 增）六档字阶制：display / title / body / secondary / caption / micro，各端取值见分册（唯一事实来源）；语义类 `.text-tier-*` 由 @theme `--text-*` 令牌驱动（R-K1 接缝），NEVER 新增 `text-[Npx]` 任意值字号。**micro 合法域**仅限元素 tile 序号、署名/时间戳、图注、表头；NEVER 用于正文、列表主信息、按钮。**行高双档**：做题/化学式渲染 1.9、信息流/管理正文 1.6、标题 1.25（`--leading-*` 令牌）。JetBrains Mono MUST `@fontsource` 子集真加载（R-T1 的执行前提，当前缺失）。

### 色彩
- **R-C2** Teal `#0d7377` 专属 AI 声音（AI 气泡/AI 按钮/AI 生成标记），三端一致；深色端（学生端）用其亮变体 `#2dd4bf`。教师端自 v2.1 起无深色区域，AI 一律用浅底原值。NEVER 用于系统命令控件。
- **R-C7** NEVER 使用分册色板之外的颜色；新增先改分册。
- **R-C8**（v2.3 增）NEVER 引入「知识域色」类多色相语义；知识点/知识域的区分靠形状语言（元素 tile、键线拓扑、空间分区），不靠色相。学生端单彩槽纪律（分册 R-S-C1）优先。
- **R-C9**（star-map-teacher 增）深底画布唯一例外：星图（/kmap 图谱视图）画布区域允许整块深底（`--star-canvas-*` 令牌定义在 StarMap 组件作用域，深空渐变底 + 辉光青 `#2dd4bf`），是教师端「无深色区域」红线的**唯一显式例外**；NEVER 劫持全局 data-theme、NEVER 扩散到其他页区或组件；新增深底需求必须先修订本条。星图编码：色相 = 册/专题（6 色分类编码，显式豁免 R-C8 的禁色相条款——此处色相编码的是「册」这一分类维度而非语义状态），亮度 = 掌握度，大小 = 作答量；NEVER 引入第七种色相或用色相表达好/坏。
- 语义状态一律「浅/深底 + 深/亮字」标签式（R-C6），NEVER 大面积纯色填充传状态。

### 令牌纪律（token discipline，v2.2 增）
- **R-K1** 容器/卡片 MUST 走 `ui/Card` 组件或全局语义类（`.surface-card` / `.hairline` / `.press-target`，定义于 globals.css `@layer components`，值只引令牌）；NEVER 出现第三种内联配方。语义类经 data-theme 自动换肤，三端共用同一 interface。
- **R-K2** `npm run lint:tokens` 扩展为全量守门（裸调色板色 / 第三种配方 / 非令牌圆角 / 非令牌字号），挂 CI 门禁违规即红；本地保留作开发期快查。
- **R-K3** 已废止样式代码随手删：`.ai-panel-dark` 作用域与 `DarkPanelProvider`（v2.1 废止深嵌板后遗留，零引用）不得复活；深色氛围需求一律走新的独立接缝，不劫持全局令牌。

### 几何
- **R-R1** 圆角梯子 8 / 12 / 16 / 24 / 9999（24 档为家长端卡片/按钮专用；学生端按钮与输入直接 9999 胶囊，见分册）。
- **R-R2** 阴影哲学按端：教师端以 1px 边框代阴影；学生端深底必须重阴影；家长端表面对比代阴影——细节值以分册为准。

### 状态与交互
- **R-S1** 每页 MUST 覆盖三态：加载（骨架屏）/空态/错误（描述+重试）。
- **R-S2** 空态 MUST 给出下一步行动，NEVER 只写"暂无数据"。
- **R-I1** 发消息后输入框清空并禁用；**R-I2** 审批类操作出确认卡片；**R-I3** Toast 3 秒自动消失。
- **R-I4** 跨页面跳转 MUST 走 Agent 桥接协议（SSE navigate/populate/action → 前端桥接存储 → 目标页自动配置）。
- **R-I5**（v2.3 增）门卡三值决定中「批准」（不可逆方向）MUST 行内两段式：点击后按钮原位展开微确认行，NEVER 直接直发；打回（必附意见）与放弃保持单击。
- **R-I6**（v2.3 增）不可逆/异步动作（学生发送、教师判卷保存、门卡决定等）MUST 复用 `useAsyncAction` + `ActionButton` 行为 module（idle→busy→done/error 编排：busy 禁交互、done 打勾回弹 --duration-slow、error 震动反馈）；NEVER 页内手写该状态机。`.press-target`（C1 语义层）只承担它的按压皮肤。
- **R-I7**（v2.3 增）教师端主 CTA 用 Oxford Blue 实色 + 按压下沉（`.press-target`）；`.btn-gradient` 收敛至登录页品牌区唯一一处，NEVER 进入端内功能按钮。

### 动效（v2.2 增）
- **R-M1** 动效引擎统一为 framer-motion（JS 驱动，覆盖国产 WebView）；一切编排 MUST 经 motion module 的 transition presets（集中定义，值从令牌取：`--duration-*` / 缓动曲线表），NEVER 页内散写数值。签名瞬间四件，逐一独立可下线：① 路由过渡（AnimatePresence + location key）② 列表 stagger（variants，cap 8 项）③ 掌握环 draw-in（conic 旋入回弹）④ AI 思考 orbs（已有，保留）。`MotionConfig reducedMotion="user"` 全局守卫——reduced-motion 一处生效全体。
- **R-M2** CSS 微过渡（hover/按压/展开）仍走 `--duration-fast/normal` + `--ease-out`；新增令牌 `--duration-slow 400ms`、`--ease-spring cubic-bezier(0.34,1.56,0.64,1)`、`--ease-draw cubic-bezier(0.65,0,0.35,1)`。CSS 与 framer 两层共用同一套令牌值。

### 布局
- **R-L2** 移动端按 430×900 视口设计；**R-L4** 底部 Tab 56px。教师端三栏/侧边栏规则见教师端分册（R-T-L2）。
- **R-L5**（v2.3 增）移动壳两档：<768 视口维持 430 壳原样；≥768 时仅 AI 助教对话页双栏画布化（ChatPanel 与工件/面板并置，同教师端 ChatPage 心智），其余移动页居中 430 卡 + P-21 环境层。430 内的版式律（字阶/圆角/间距）两档共用，NEVER 因宽屏破例。

## ③ 跨端组件模式（P-01~P-20）

> 每端变体细节在对应分册"组件变体要点"。新页面遇到同类需求 MUST 复用，禁止另起炉灶。

| 编号 | 模式 | 端 | 分册变体差异 |
|------|------|----|------------|
| P-01 | 对话气泡 chat-bubble | 三端 | 教师浅区 Teal 浅底（深嵌板变体随 v2.1 废止）；学生 accent 胶囊；家长 Teal 浅底 24px 圆角 |
| P-02 | 状态徽章 status-badge | 三端 | 教师保持浅底深字（定案）；家长 positive-pale 胶囊 |
| P-03 | 顶部标题栏 top-bar | 移动端 | 学生取消实底顶栏融入 canvas；家长保留 |
| P-04 | 底部 Tab 导航 bottom-tabbar | 移动端 | 激活色：学生 accent / 家长亮绿 |
| P-05 | 侧边栏 sidebar | 教师 | `nav-bg` 浅色纹理底图 + 深字 232px，激活项 `primary-soft` 底 + 元素符号 tile 渐变（v2.1 定案；Oxford Blue 实底已废止，见 `design/教师端.md` R-T-L2） |
| P-06 | KPI 卡 kpi-card | 三端 | 教师 mono 数字；学生 surface 卡 900；家长 48px/900 + 涨跌徽章 |
| P-07 | 学生卡+详情抽屉 student-card/drawer | 教师 | 480px 右滑 + 遮罩 |
| P-08 | 弹窗 modal | 三端 | 圆角：教师/学生 16、家长 24 |
| P-09 | 通知 toast | 三端 | 右上浮出 3 秒（R-I3） |
| P-10 | 骨架屏 skeleton | 三端 | 深端用 surface-interactive 闪烁 |
| P-11 | 空状态 empty-state | 三端 | 烧杯图标 + 行动引导（R-S2） |
| P-12 | 上传区域 upload-zone | 教师 | 拖拽虚线框，悬停高亮 |
| P-13 | 进度条 progress-bar | 三端 | OCR/上传/掌握度 |
| P-14 | 题目卡片 question-card | 教师+学生 | 审核徽章/陷阱提示/操作按钮 |
| P-15 | 审核标记 audit-mark | 教师 | 四维安全审核（绿✓/黄⚠/红✗） |
| P-16 | 考试卡片 exam-card | 教师 | 状态标签 + 操作菜单 |
| P-17 | 数据表格 data-table | 教师 | OCR 结果表，失败行重试 |
| P-18 | 搜索筛选栏 search-filter-bar | 教师 | 输入框 + 筛选 chip |
| P-19 | 分页控件 pagination | 教师+学生 | 页码高亮 |
| P-20 | 手风琴折叠列表 accordion-list | 教师+学生 | 错题本/障碍诊断分组 |
| P-21 | 背景艺术层 background-art | 三端（强度分档） | 三层叠：层1 外供纹理图 / 层2 persona 渐变洗 / 层3 自绘分子网络 SVG。强度：学生端沉浸（全域 canvas）、家长端浅（顶部 hero 与空态）、教师端沿用现役壳底图（main-bg/nav-bg 即层 1 现任实例，v2.3 勘误——此前误记「教师端零」），不叠新层。接口按数组留位（每端可多张）。2026-10-05 用户素材接线：学生端深色霓虹线稿（screen 混合，图底与沉浸底同源）/ 家长端浅色水彩（顶部对位 hero）；两登录页全幅铺图（student-login / parent-login）为 P-21 登录位。独立接缝，NEVER 劫持全局令牌 |
| P-22 | 化学式水印 chem-hero | 教师 + 学生各一处封顶 | 判卷中心 / 学生成绩页各一枚 KaTeX 花体巨字水印（`text-primary/[0.05]` 律，同登录 H₂SO₄）；NEVER 进列表/表单 |

## ④ 整体约束与缺口表

### 整体约束
1. 改界面代码前必读：总册 + 目标端分册；冲突时分册优先。
2. 设计系统变更 MUST 同步修订对应文档（总册或分册），并更新 `design-upgrade/README.md` 决议日志。
3. 页面口径：教师 8（侧栏外显 6 + 待确认队列 `/pending` 1 + 内联出题工作台 1）· 学生 6 · 家长 2 = 16 条目。
   （待确认队列 2026-09-24 决议新增、2026-09-25 已实现：有独立路由与可分享地址，但入口挂在
   Agent 工作台顶栏那颗带数量的按钮上，不占侧栏一格。见
   `docs/designs/gate-product-closure.md` D1–D19 与「实现期落定（Wave 3）」第 10 条。）
4. 跨页面数据传递一律走桥接协议（R-I4）。

### 缺口表（原 v1 GAP 处置）

| 编号 | 缺口 | 处置（v2） |
|------|------|-----------|
| GAP-01 | 间距尺度 | **已填**：教师/家长 4px 基数八档；学生 8px 基数（分册） |
| GAP-02 | 渐变按钮色值 | **v2.1 复活**：教师端主操作用品牌渐变 `--gradient-primary`（学术青 `#0d7377` → 靛蓝 `#2563eb`，135deg）；总册 v2 初版"渐变废弃"口径已被 2026-09-14 晚用户验收否决，见 `design/教师端.md` |
| GAP-03 | 字号/字重/行高 | **已填**：三端 scale 见分册 |
| GAP-04 | 深色模式 | 教师端**无任何深色区域**（v2.1：深色 AI 嵌板与登录页深色 moment 均被用户验收否决，登录页用品牌插画）；学生端即深色；家长端浅色 |
| GAP-05 | 平板学生/家长端 | 沿用 v1：复用移动布局加宽内边距 |
| GAP-06 | 外部通知渠道样式 | 沿用 v1：仅站内 Toast + 家长消息 Tab |
| GAP-07 | 多主题切换 | 不支持；换肤即改分册参数 |

---

*v2 由三端融合提案（design-upgrade/提案-*.md，2026-09-14 定案）升格而来；v1 单册版归档于 design-upgrade/归档/。源设计分析文件（教师端-Linear 等）仅为灵感出处，NEVER 直接引用其色值——一切以本册与分册为准。*
