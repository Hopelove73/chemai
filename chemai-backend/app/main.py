"""
ChemAI Main Application
基于FastAPI的高中化学AI辅助教学工具后端服务
"""
import asyncio
import logging
import os

# 日志级别：默认 WARNING = 与「没有配置」时的行为一致；排查链路问题时设
# CHEMAI_LOG_LEVEL=INFO 才放行 agent 层的 INFO 留痕。此前全仓库没有一处
# basicConfig，root 停在默认 WARNING —— agent 层那些 logger.info（含
# 「下发工具清单」这条）**一条都没打出来过**，排查时只能靠读代码推
# （2026-10-17 查 2.11 时发现）。
logging.basicConfig(level=os.getenv("CHEMAI_LOG_LEVEL", "WARNING"))

# 禁用 ChromaDB posthog telemetry (修复版本兼容性问题)
try:
    import posthog
    _original_capture = posthog.capture
    posthog.capture = lambda *args, **kwargs: None
except ImportError:
    pass

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from app.core.config import settings

# LangGraph SqliteStore (long-term memory)
_store = None
_store_ctx = None  # async context manager holder


async def get_store():
    """Get the SqliteStore singleton (lazily initialized via async context manager)."""
    global _store, _store_ctx
    if _store is None:
        from langgraph.store.sqlite.aio import AsyncSqliteStore
        db_path = settings.STORE_DB_PATH
        _store_ctx = AsyncSqliteStore.from_conn_string(db_path)
        _store = await _store_ctx.__aenter__()
        await _store.setup()
    return _store

# ── 化学式渲染归前端显示层（ChemMd / lib/chem-format）──────────────────
# 这里曾挂着一个改写所有 application/json 响应的中间件：它把「大写字母 + 数字」
# 换成 Unicode 下标，于是 ISO 时间戳 `2026-09-24T16:09` 变成 `T₁₆`、`->` 变成
# ` → `、连续空格被压缩。数据在传输路上被改成了另一种东西，前端 Date.parse 拿到
# NaN（待确认页「已等待」显示 — 就是它）。2026-09-25 浏览器走查发现后摘掉。
from app.middleware.auth import PermissionChecker  # noqa: E402

# API路由
from app.api import (  # noqa: E402
    ocr,
    exam,
    question,
    diagnosis,
    report,
    practice,
    panel,
    auth,
    user,
    class_api,
    exam_bank,
    memory,
    school,
    grade,
    teacher_application,
    log,
    parent,
    review,
    warning,
    analytics,
    integration,
    notification,
    knowledge,
    workspace_api,
    break_glass,
    artifacts,
    admin_overview,
    kb_admin,
    account_center,
    rag_ops,
    roles,
)
from app.api.practice_api import wrong_topic  # noqa: E402
from app.api import smart_practice  # noqa: E402
from app.api import knowledge_map  # noqa: E402

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="高中化学AI辅助教学工具 - 教师端+学生端API服务"
)

# 服务器进程不开 record：磁带会把请求原文落盘（对话内容原样，图像已换成摘要），而模式来自
# 进程环境 —— `.env` 里一行 `CHEMAI_LLM_MODE=record` 加一个目录，就会让开发/生产逐请求写盘。
# "只从评估录"（ADR-0023 决策 7）需要一个代码着力点：录制由评估入口显式开，服务器只允许
# live 与 replay（replay 只读磁带，拿它跑一次离线复现是安全的）。守卫自己读 `.env`，因为
# `import app.main` 并不拉起 `app/config.py`（那才是本仓读 .env 的地方），挂在下面这行却看不见
# 它的话，挡住的只有"命令行显式 export"那一半。
from app.services.llm_transport import refuse_recording_in_server  # noqa: E402

refuse_recording_in_server()

# 获取前端文件目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

# CORS配置（生产环境通过 CHEMAI_CORS_ORIGINS 环境变量设置白名单）
ALLOWED_ORIGINS = os.getenv("CHEMAI_CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS.split(",") if ALLOWED_ORIGINS != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 无需认证的路径
# 仅保留「登录本身」与「探活」。其余全部要求有效 token（ADR-0009）。
PUBLIC_PREFIXES = [
    # ⚠️ 逐条列出，不再整段放行 /api/auth/ —— 原来整段白名单让 logout/me 也免鉴权，
    # 而它们本来就在拿到 token 之后调用。只有「发生在拿到 token 之前」的才留在这里。
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/apply",     # 教师入驻申请（含 GET /apply/{app_id} 查状态）
    # 家长登录/注册发生在拿到 token 之前，必须放行。
    # 注意：/api/parent/ 的其余端点（children/bind/report/send-bind-code…）一律要求认证。
    "/api/parent/login",
    "/api/parent/register",
    # LTI 1.3 规范要求**平台无鉴权**抓取工具的 JWKS 来验签 —— 这个端点
    # 设计上就该公开（装的本来就是公钥）。4.7 的其余 15 个端点都限管理员。
    "/api/integration/lti/.well-known/jwks.json",
    "/health",
]

# API 文档会把全部端点结构暴露给任何人 —— 仅开发环境开放
if settings.DEBUG:
    PUBLIC_PREFIXES += ["/docs", "/redoc", "/openapi.json"]
PUBLIC_EXTENSIONS = {".html", ".js", ".css", ".png", ".ico", ".svg", ".woff2"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # 非 API 路径跳过
    if not path.startswith("/api/"):
        return await call_next(request)
    # 白名单前缀跳过（如 /api/auth/、/api/agent/ 及其子路径）
    if any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return await call_next(request)
    # 静态文件跳过
    if any(path.endswith(ext) for ext in PUBLIC_EXTENSIONS):
        return await call_next(request)

    from app.middleware.auth import verify_token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "未携带认证token"})
    token = auth_header.replace("Bearer ", "")
    payload = verify_token(token)
    if not payload:
        return JSONResponse(status_code=401, content={"detail": "token无效或已过期"})
    return await call_next(request)

# 注册API路由（优先级最高）
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(
    user.router, prefix="/api/users", tags=["用户管理"],
    # 基线：员工角色都能读学生；本人角色（student/parent）无 student:read → 403
    dependencies=[Depends(PermissionChecker("student", "read"))],
)
app.include_router(
    class_api.router, prefix="/api/classes", tags=["班级管理"],
    # 基线：员工角色都能读班级（含 subject_lead / teacher）；本人角色 403
    dependencies=[Depends(PermissionChecker("class", "read"))],
)
app.include_router(
    ocr.router, prefix="/api/ocr", tags=["OCR识别"],
    # 基线：员工角色可读 OCR（学生/家长无 ocr:read → 403）
    dependencies=[Depends(PermissionChecker("ocr", "read"))],
)
app.include_router(
    exam.router, prefix="/api/exam", tags=["考试管理"],
    # 基线：员工角色可读考试（学生/家长无 exam:read → 403）
    dependencies=[Depends(PermissionChecker("exam", "read"))],
)
app.include_router(
    question.router, prefix="/api/question", tags=["题目管理"],
    # 基线：员工角色可读题目（学生/家长无 question:read → 403）
    dependencies=[Depends(PermissionChecker("question", "read"))],
)
app.include_router(diagnosis.router, prefix="/api/diagnosis", tags=["障碍诊断"])
app.include_router(report.router, prefix="/api/report", tags=["报告生成"])
# ⚠️ 这里**不能**用 router 基线：/api/practice 下有学生自助端点，
# 基线总是执行，会把 allow_self 里的学生挡在端点之前（4.5b 踩过同一个坑）。
# 故全部逐端点声明。
app.include_router(practice.router, prefix="/api/practice", tags=["自适应练习"])
from app.api import practice_wrong  # noqa: E402
app.include_router(practice_wrong.router, prefix="/api/practice", tags=["错题复习"])
app.include_router(
    panel.router, prefix="/api/panel", tags=["学情面板"],
    # 基线：员工角色可读学情；学生/家长无 analysis:read → 403
    dependencies=[Depends(PermissionChecker("analysis", "read"))],
)
app.include_router(
    exam_bank.router, prefix="/api/exam-bank", tags=["真题库管理"],
    # 基线：员工角色可读真题库（学生/家长无 exam_bank:read → 403）
    dependencies=[Depends(PermissionChecker("exam_bank", "read"))],
)
app.include_router(
    memory.router, prefix="/api/memory", tags=["学情记忆"],
    # 基线：员工角色可读学情记忆；学生/家长无 memory:read → 403
    # ⚠️ POST /diagnosis-complete 是写操作，但矩阵里 memory 只有 read
    #    （见 auth.py 的 CONTENT_PERMISSIONS 与 tasks.md 的语义偏松记录）
    dependencies=[Depends(PermissionChecker("memory", "read"))],
)
app.include_router(school.router, prefix="/api/school", tags=["学校设置"])
# 管理端概览（admin-console-p0）：端点自带 student:create 门，无 router 基线
app.include_router(admin_overview.router, tags=["管理端"])
# KB 内容治理（kb-review-ui）：逐端点声明（read/update 两种门），无 router 基线
app.include_router(kb_admin.router, tags=["KB内容治理"])
# 账号中心（account-centers）：逐端点声明（student:update 门）
app.include_router(account_center.router, tags=["账号中心"])
# RAG 运营（rag-observability / retrieval-test / eval-regression）：逐端点声明
app.include_router(rag_ops.router, tags=["RAG运营"])
# 角色与权限（role-visibility）：只读派生
app.include_router(roles.router, tags=["角色与权限"])
# 运行时开关（runtime-settings）：逐端点声明（settings:read/update 仅 admin）。
# 别名导入 —— `settings` 已被 config 单例占用（line 27），直接 import 会遮蔽它。
from app.api import settings as settings_api  # noqa: E402
app.include_router(settings_api.router, tags=["运行时开关"])
app.include_router(grade.router, prefix="/api/grades", tags=["年级管理"])
app.include_router(teacher_application.router, prefix="/api/teacher-applications", tags=["教师入驻"])
app.include_router(log.router, prefix="/api/logs", tags=["操作日志"])
app.include_router(parent.router, prefix="/api/parent", tags=["家长端"])
# 同上：/api/review 是学生自助入口，逐端点声明
app.include_router(review.router, prefix="/api/review", tags=["间隔复习"])
app.include_router(
    warning.router, prefix="/api/warning", tags=["学情预警"],
    # 基线：员工角色可读预警（学生/家长无 warning:read → 403）
    dependencies=[Depends(PermissionChecker("warning", "read"))],
)
# ⚠️ 同 /api/practice：analytics 下有学生/家长自助看板（dashboard/student|parent、
# student/{id}/personal-stats，靠 allow_self + ScopeGuard 收窄），router 基线总是执行，
# 会把本人角色挡在端点之前（他们是矩阵外的角色，没有 analysis:read）。故逐端点声明。
app.include_router(analytics.router, prefix="/api/analytics", tags=["数据可视化"])
app.include_router(
    break_glass.router, prefix="/api/break-glass", tags=["平台提权"],
    # ADR-0011：只有 super_admin 能开提权（admin 也没有这个资源键）
)
app.include_router(integration.router, prefix="/api/integration", tags=["LMS集成"])
from app.api import data_import  # noqa: E402
app.include_router(data_import.router)
app.include_router(notification.router, prefix="/api/notification", tags=["通知推送"])
app.include_router(wrong_topic.router, prefix="/api/practice/wrong-topic", tags=["错题强化"])
app.include_router(smart_practice.router, prefix="/api/practice", tags=["智能练习 (CAT)"])
app.include_router(knowledge_map.router, prefix="/api/knowledge-map", tags=["知识地图 (S6)"])
app.include_router(
    knowledge.router, prefix="/api/knowledge", tags=["知识卡片"],
    # 知识点卡片是纯读内容；学生/家长无 knowledge:read → 403
    dependencies=[Depends(PermissionChecker("knowledge", "read"))],
)

# 文档解析
app.include_router(
    workspace_api.router,  # 工作区目录浏览器（前缀在 router 内）
    dependencies=[Depends(PermissionChecker("workspace", "read"))],
)

# Answer sheet OCR + grading
from app.api import ocr_sheets  # noqa: E402
app.include_router(
    ocr_sheets.router, prefix="/api/ocr", tags=["答题卡批改"],
    dependencies=[Depends(PermissionChecker("ocr", "read"))],
)

# ChemAgent (LangGraph ReAct agent)
from agent.channel.langgraph_channel import router as langgraph_router  # noqa: E402
app.include_router(langgraph_router, prefix="/api/agent", tags=["AI Agent (LangGraph)"])

# 确认门 4a 只读列表（权限门在路由内：agent:read，五种员工角色全下发、只见自己名下）
from app.api import gates as gates_api  # noqa: E402
app.include_router(gates_api.router, prefix="/api/agent", tags=["确认门 (Agent Gate)"])

# ── Conversation management routes (registered directly on app) ──
from agent.channel.conversation import (  # noqa: E402
    list_conversations,
    get_conversation_history,
    new_conversation,
    delete_conversation,
)
app.get("/api/agent/chat/conversations", tags=["对话管理"])(list_conversations)
app.get("/api/agent/chat/history/{thread_id}", tags=["对话管理"])(get_conversation_history)
app.post("/api/agent/chat/new", tags=["对话管理"])(new_conversation)
app.delete("/api/agent/chat/conversations/{thread_id}", tags=["对话管理"])(delete_conversation)


def _nocache_file(path, **kw):
    """返回文件并禁用缓存"""
    from starlette.responses import FileResponse as _FR
    resp = _FR(path, **kw)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.get("/")
async def root():
    # 优先提供 React 教师端构建产物（dist），回退源码 index.html
    dist_html = os.path.join(FRONTEND_DIR, "dist", "index.html")
    if os.path.exists(dist_html):
        return _nocache_file(dist_html)
    html_file = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(html_file):
        return _nocache_file(html_file)
    return RedirectResponse(url="/login.html")

@app.get("/favicon.png")
async def serve_favicon():
    dist_favicon = os.path.join(FRONTEND_DIR, "dist", "favicon.png")
    if os.path.exists(dist_favicon):
        return FileResponse(dist_favicon, media_type="image/png")
    return RedirectResponse(url="/")

@app.get("/m")
async def mobile_root():
    # ADR-0001：移动端并入 React SPA，旧 /m 重定向到 SPA hash 路由
    return RedirectResponse(url="/#/m")


# 旧静态移动页 → SPA 路由 映射（ADR-0001 URL 兼容；e2e 与收藏链接不断链）
_MOBILE_SPA_MAP = {
    "index.html": "/#/m",
    "practice.html": "/#/m/practice",
    "wrong.html": "/#/m/wrong",
    "review.html": "/#/m/review",
    "report.html": "/#/m/me",
    "login.html": "/#/m/login",
    "parent.html": "/#/m/parent",
    "parent-login.html": "/#/m/parent-login",
    "design-v1.html": "/#/m",
}


# 真题题库图片静态文件（兼容目录名带数字前缀：01_全国卷 ↔ 全国卷，
# 与 exam_bank.py 的 region 归一化 re.sub(r"^\d+_", "", entry) 保持一致）
import os as _os  # noqa: E402
import re as _re  # noqa: E402
from pathlib import Path as _Path  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
_figures_dir = _os.path.join(_os.path.dirname(__file__), "..", "data", "exam_questions")

@app.get("/static/figures/{path:path}")
async def serve_exam_figure(path: str):
    if not path or ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400)
    base = _Path(_figures_dir)
    direct = base / path
    if direct.is_file():
        return FileResponse(direct)
    # 兜底：目录名与 URL 地区名带数字前缀错位（URL 用归一化名 全国卷，目录为 01_全国卷）
    parts = path.split("/")
    if parts and parts[0]:
        stripped = _re.sub(r"^\d+_", "", parts[0])
        if stripped != parts[0]:
            # URL 带前缀（01_全国卷）→ 目录不带前缀（全国卷）
            alt = base / stripped / "/".join(parts[1:])
            if alt.is_file():
                return FileResponse(alt)
        # URL 不带前缀（全国卷）→ 目录带前缀（01_全国卷）
        for entry in _os.listdir(base) if base.is_dir() else []:
            if _re.sub(r"^\d+_", "", entry) == parts[0]:
                alt = base / entry / "/".join(parts[1:])
                if alt.is_file():
                    return FileResponse(alt)
                break
    raise HTTPException(status_code=404)

# 复杂结构图（RDKit 渲染，ADR-0005 H1）
from app.services.structure_render import IMG_DIR as _qimg_dir  # noqa: E402
_os.makedirs(_qimg_dir, exist_ok=True)
app.mount("/static/question_images", StaticFiles(directory=_qimg_dir), name="question_images")

# 工件文件**没有** /static 侧门（A5 审查 P1）：img/iframe 子资源带不上
# Authorization header 的正确解法是前端 fetch→blob（ArtifactPanel/ArtifactsPage
# 已实现），不是开一条免鉴权路由——否则「分发必过门」在文件真源面前不成立。
# 全部取数走 /api/artifacts/...（workspace:read + 归属/分享收窄）。
from app.services.artifact_service import artifact_base_dir as _art_dir  # noqa: E402

_os.makedirs(_art_dir(), exist_ok=True)

app.include_router(artifacts.router, prefix="/api", tags=["工件"])

@app.get("/health")
async def health_check():
    """探活。向量这一层的健康一并报出来：它坏了原本 /health 仍然是 "healthy"，
    而老师的相似题检索已经在退回关键词匹配（工单 VS1）。count() 是本地 sqlite 读一个数，
    仍然丢进线程，不在事件循环上等它。"""
    from app.services.vector_search import vector_search_service

    return {"status": "healthy",
            "vector": await asyncio.to_thread(vector_search_service.layer_status)}


# 静态文件路由（精确匹配，避免被动态路由捕获）
@app.get("/app.js")
async def serve_app_js():
    js_file = os.path.join(FRONTEND_DIR, "app.js")
    if os.path.exists(js_file):
        return FileResponse(js_file, media_type="application/javascript")
    return RedirectResponse(url="/")


@app.get("/components/{file}")
async def serve_component(file: str):
    """前端组件静态文件"""
    file_path = os.path.join(FRONTEND_DIR, "components", file)
    if os.path.exists(file_path):
        ext = file.rsplit(".", 1)[-1] if "." in file else ""
        media_types = {"js": "application/javascript", "css": "text/css"}
        return FileResponse(file_path, media_type=media_types.get(ext))
    return RedirectResponse(url="/")

@app.get("/styles.css")
async def serve_css():
    css_file = os.path.join(FRONTEND_DIR, "styles.css")
    if os.path.exists(css_file):
        return FileResponse(css_file, media_type="text/css")
    return RedirectResponse(url="/")


@app.get("/common.css")
async def serve_common_css():
    css_file = os.path.join(FRONTEND_DIR, "common.css")
    if os.path.exists(css_file):
        return FileResponse(css_file, media_type="text/css")
    return RedirectResponse(url="/")


@app.get("/login.html")
async def serve_login_html():
    html_file = os.path.join(FRONTEND_DIR, "login.html")
    if os.path.exists(html_file):
        return _nocache_file(html_file)
    return RedirectResponse(url="/")


@app.get("/index.html")
async def serve_index_html():
    html_file = os.path.join(FRONTEND_DIR, "index.html")
    if not os.path.exists(html_file):
        html_file = os.path.join(FRONTEND_DIR, "login.html")
    return FileResponse(html_file)


# 前端静态资源精确路由（必须在兜底 /{page} 之前）
@app.get("/design-system.css")
async def serve_design_css():
    return FileResponse(os.path.join(FRONTEND_DIR, "design-system.css"))

@app.get("/js/{filename}")
async def serve_js(filename: str):
    fp = os.path.join(FRONTEND_DIR, "js", filename)
    if os.path.isfile(fp):
        return FileResponse(fp)
    raise HTTPException(status_code=404)

@app.get("/pages/{filename}")
async def serve_pages(filename: str):
    fp = os.path.join(FRONTEND_DIR, "pages", filename)
    if os.path.isfile(fp):
        return FileResponse(fp)

@app.get("/m/{filename}")
async def serve_mobile(filename: str):
    # ADR-0001：旧静态移动页已退役，按映射表重定向到 SPA hash 路由
    target = _MOBILE_SPA_MAP.get(filename)
    if target:
        return RedirectResponse(url=target)
    return RedirectResponse(url="/#/m")

# React 教师端构建产物静态资源（方向 A 现代学术蓝；纯增量，不影响现有路由）
_dist_assets = os.path.join(FRONTEND_DIR, "dist", "assets")
if os.path.isdir(_dist_assets):
    app.mount("/assets", StaticFiles(directory=_dist_assets), name="assets")

# 旧教师端归档只读访问（2026 归档到 frontend/legacy/teacher/，供对照/回滚）
_legacy_dir = os.path.join(FRONTEND_DIR, "legacy")
if os.path.isdir(_legacy_dir):
    app.mount("/legacy", StaticFiles(directory=_legacy_dir), name="legacy")

# HTML页面路由（兜底 — 必须放在所有精确路由之后）
@app.get("/{page}")
async def serve_page(page: str):
    if ".." in page or page.startswith("/"):
        return RedirectResponse(url="/")
    # 如果已经是完整文件名（如 teacher.html），直接用
    if page.endswith(".html"):
        candidates = [
            os.path.join(FRONTEND_DIR, page),
            os.path.join(FRONTEND_DIR, "pages", page),
        ]
    else:
        candidates = [
            os.path.join(FRONTEND_DIR, f"{page}.html"),
            os.path.join(FRONTEND_DIR, "pages", f"{page}.html"),
        ]
    for html_file in candidates:
        if os.path.exists(html_file):
            return _nocache_file(html_file)
    return RedirectResponse(url="/")


async def build_vector_index_in_background():
    """启动期的向量索引重建，跑在后台线程里。

    分成模块级函数是为了这条能被单独测：这活儿坏了以前只 print 一行，而"先删库再写"的中途
    失败会留下一个空库，`/health` 那边却仍然报 available=true（工单 VS1）。

    必须整体丢进 `asyncio.to_thread`：`_get_embedding` 现在是同步的本地模型推理（冷加载
    实测 20~99s、之后每条 ~250ms），放在事件循环上等于把整个服务按住（2026-08 那次
    "启动 40 秒浏览器打不开"就是这么来的）。
    """
    from app.services.vector_search import get_text_encoder, vector_search_service

    try:
        # 先预热编码器：索引是最新的时 `build_index_from_exam_bank` 直接跳过、不加载模型，
        # 所以这一步不能省 —— 否则那几十秒会落在老师第一次搜索的那个请求上。
        await asyncio.to_thread(get_text_encoder)
        count = await asyncio.to_thread(vector_search_service.build_index_from_exam_bank)
        print(f"Vector index built (background): {count} questions")
    except Exception as e:  # noqa: BLE001
        vector_search_service.note_failure(
            "启动重建索引", f"{type(e).__name__}: {e}（向量库可能是空的）")
        print(f"Vector index build skipped: {e}")


@app.on_event("startup")
async def startup_event():
    # Initialize SqliteStore (long-term memory)
    global _store
    _store = await get_store()
    print("SqliteStore initialized (chemai_store.db)")
    from app.models.database import engine, Base
    Base.metadata.create_all(bind=engine)
    print("数据库表已创建/验证")

    # kb-ingest-ui：进程异常退出留下的摄取任务标 interrupted（读时判定无残留）
    try:
        from app.services.kb_jobs import mark_interrupted_on_startup
        n = mark_interrupted_on_startup(engine)
        if n:
            print(f"KB 摄取任务：{n} 个非终结态已标 interrupted")
    except Exception as e:  # 表可能尚未迁移（新库首启走 create_all 已建表）；失败不阻塞启动
        print(f"KB job startup marking skipped: {e}")

    # 确认门的启动期幂等 DDL（T19）：create_all 只建缺失的表，不给存量表加列，
    # 老库缺 agent_gates.last_opened_at 时第一个开门请求才 500。失败即早停——
    # 带病对外服务比启动即失败更难诊断。其余 DDL 仍是 CLI-only
    # （python -m app.utils.migrate），这里是唯一例外。
    from app.utils.migrate import (migrate_agent_gates_last_opened,
                                   migrate_agent_gates_plan_step,
                                   migrate_agent_gates_replay_payload)
    try:
        migrate_agent_gates_last_opened(engine)
        # 门绑定的计划步号（gate-lifecycle 批次 1）：ORM 已映射，老库缺列时
        # 第一个 agent_gates 查询就 no such column。
        migrate_agent_gates_plan_step(engine)
        # 重放载荷指纹两列（gate-lifecycle 2.6）：同理，且**尤其**要挂这里 ——
        # 2026-10-17 实测漏挂的代价：只把它加进 `migrate()`（CLI 路径）时，
        # 影子库启动不跑 DDL，`open_gate` 当场 no such column: args_hash，
        # `register_gate_entity` 全线失败 ⇒ 一扇门都开不出来、卡片全没有，
        # 端到端直接退化成"模型反复重试工具"。所以两处都得挂，见下面 migrate() 里那句。
        migrate_agent_gates_replay_payload(engine)
    except Exception as e:
        raise RuntimeError(
            f"确认门表结构迁移失败，服务拒绝启动（库可能被锁或磁盘只读）。"
            f"原因：{e}"
        ) from e

    # 外发通道中国化（ADR-0029 批⑦）：同款启动期幂等 DDL——老库缺
    # parents.notify_channel/notify_webhook 时设置页保存即 500
    from app.utils.migrate import migrate_parent_notify_channel
    try:
        migrate_parent_notify_channel(engine)
    except Exception as e:
        raise RuntimeError(
            f"外发通道表结构迁移失败，服务拒绝启动（库可能被锁或磁盘只读）。"
            f"原因：{e}"
        ) from e

    # 确认门启动扫描（gate-lifecycle 2.7b）：仍停在 executing 的门 = 上次崩溃留下的
    # 残留（claim 与 finish 永远在同一次请求、同一个进程里），落成 in_doubt 等老师
    # 裁定。必须在**任何读门的代码之前**跑，否则那张「结果未知」卡要等到下次重启才出现。
    # 失败不拒启：门数据原样未动，下次启动重扫即可（与下面记忆卡迁移同一口径）。
    try:
        from app.services.gate_service import gate_service as _gs
        from app.services.work_unit import new_work_unit as _uw
        with _uw() as _db:
            _stuck = _gs.sweep_stuck_executing(_db)
        if _stuck:
            print(f"确认门启动扫描：{_stuck} 扇停在 executing 的门落为 in_doubt（结果未知）")
    except Exception as e:  # noqa: BLE001
        print(f"确认门启动扫描跳过（下次启动重试）：{e}")

    # 存量艾宾浩斯 ReviewTask → 记忆卡一次性迁移（批次 B，幂等：迁移完的行标
    # status='migrated'）。失败不拒绝启动——旧数据还在原表，下轮启动重试。
    try:
        from app.services.memory_card_service import migrate_all_legacy
        from sqlalchemy.orm import Session as _Session
        _s = _Session(bind=engine)
        try:
            n = migrate_all_legacy(_s)
            if n:
                print(f"记忆卡迁移：{n} 条 ReviewTask 已迁移")
        finally:
            _s.close()
    except Exception as e:  # noqa: BLE001
        print(f"记忆卡迁移跳过（下次启动重试）：{e}")

    # IRT 标定冷启动（批次 C）：全量重算题目标定（一条 GROUP BY，秒级）。
    # 失败不拒启——选题服务按数据门槛自动降级回难度先验。
    try:
        from app.services.assessment_service import calibrate as _calibrate
        from sqlalchemy.orm import Session as _Session2
        _s2 = _Session2(bind=engine)
        try:
            n = _calibrate(_s2)
            if n:
                print(f"IRT 标定：{n} 道题已标定")
        finally:
            _s2.close()
    except Exception as e:  # noqa: BLE001
        print(f"IRT 标定跳过（下次启动重试）：{e}")

    # 批次 C 审查 P2-12：student_answers.student_id 无索引，逐生查询全表扫——
    # SQLite 支持幂等 CREATE INDEX IF NOT EXISTS
    try:
        from sqlalchemy import text as _text
        with engine.connect() as _conn:
            _conn.execute(_text(
                "CREATE INDEX IF NOT EXISTS idx_student_answers_student "
                "ON student_answers (student_id)"))
            _conn.commit()
    except Exception as e:  # noqa: BLE001
        print(f"作答索引创建跳过：{e}")

    # Debug: print all agent routes
    print("=== Registered agent routes ===")
    for route in app.routes:
        if hasattr(route, 'routes'):
            for sr in route.routes:
                sp = getattr(sr, 'path', None)
                sm = getattr(sr, 'methods', None)
                if sp and '/agent/' in sp:
                    print(f"  {sm} {sp}")
    print("=== End agent routes ===")

    from app.utils.init_db import init_database
    init_database()

    # 真题库全量写入 historical_exams（幂等 upsert，检索/导入入口的数据源）
    try:
        from app.services.exam_bank import exam_bank_service
        synced = exam_bank_service.sync_all_to_db()
        print(f"[ExamBank] historical_exams 同步完成: {synced} 条")
    except Exception as e:
        print(f"[ExamBank] historical_exams 同步失败: {e}")

    # 注册 Agent Skills
    try:
        from agent.skills_init import register_all_skills
        register_all_skills()
    except Exception as e:
        print(f"Skills 注册失败: {e}")

    # 构建向量索引 —— 后台线程（2026-08 修复：启动被 chroma 嵌入重建阻塞 40+ 秒，
    # 期间 uvicorn 不接客，浏览器打开 localhost 显示"无法访问"）。
    # 必须用 to_thread：_get_embedding 是同步的本地模型推理（CPU 上冷加载 ~99s、
    # 之后每条 ~250ms），直接 create_task 会阻塞事件循环，服务器同样无法响应。
    try:
        asyncio.create_task(build_vector_index_in_background())
    except Exception as e:  # noqa: BLE001
        from app.services.vector_search import vector_search_service as _vss

        _vss.note_failure("启动重建索引", f"{type(e).__name__}: {e}（后台任务根本没排上）")
        print(f"Vector index task create failed: {e}")

    # 启动调度器
    try:
        from app.scheduler import init_scheduler
        init_scheduler(app)
    except ImportError as e:
        print(f"调度器启动失败 (APScheduler可能未安装): {e}")


@app.on_event("shutdown")
async def shutdown_event():
    # Close SqliteStore
    global _store, _store_ctx
    if _store_ctx is not None:
        try:
            await _store_ctx.__aexit__(None, None, None)
            _store = None
            _store_ctx = None
            print("SqliteStore closed")
        except Exception as e:
            print(f"SqliteStore close failed: {e}")

    # 关闭调度器
    try:
        from app.scheduler import shutdown_scheduler
        shutdown_scheduler()
    except Exception as e:
        print(f"调度器关闭失败: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
