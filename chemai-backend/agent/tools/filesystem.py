"""
Filesystem tools — read / write / edit / find / grep, confined to a workspace.

Workspace（2026-08 设计定稿，参考 DSH/WorkBuddy）：
  - 工作区 = 老师的主权文件夹，不是程序目录。默认 ~/ChemAI-Workspace，
    可经 set_workspace_root 按对话覆盖（老师自定义路径），env CHEMAI_WORKSPACE
    优先（评估环境用）。
  - 首次创建自动生成骨架：README.md + 学生资料/教案备课/试卷真题/AI生成/临时。
  - write 到 AI生成/ 自动按日期归档（AI生成/YYYY-MM-DD/…）。

访问控制（前端开关 + 按次审批，DSH danger-full-access 模式）：
  - off       — 全部文件工具拒绝
  - workspace — 仅工作区（默认）：区内直接读写；区外每次按次审批
  - full      — 完全访问（DSH danger-full-access 等价档）：全路径直接放行，
    区外不再弹确认门；破坏性工具（assign/delete）仍由 guard 拦截、
    老师在确认卡上批准后确定性重放（见 agent/guard.py）
  敏感文件（.env/密钥/证书）在任何档位下永远拒绝。
"""
import contextvars
import json
import os
import re
from datetime import datetime
from pathlib import Path

WORKSPACE_ENV = "CHEMAI_WORKSPACE"
DEFAULT_WORKSPACE = "~/ChemAI-Workspace"

MAX_FIND_RESULTS = 200
MAX_GREP_MATCHES = 100

# 每请求文件上下文（contextvars，任务级隔离）。生产单进程无 worker，SSE 请求
# 跑在同一事件循环的不同 task，进程级全局会跨请求串（工作区被换/越界被放行）。
# 与身份上下文 agent/tools/context.py 对齐同一接缝（grilling 2026-09-22 决策 B）。
# 档位：off 关闭 | workspace 工作区（默认，区外按次审批）| full 完全访问。
_access_level_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "chemai_file_access_level", default="workspace")
# 按次审批：放行一次工作区外访问（批准后由 guard 置位，_resolve 消费即失效）。
_elevate_once_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "chemai_file_elevate_once", default=False)
# 老师自定义工作区路径（按对话下发）。
_ws_override_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "chemai_file_ws_override", default="")
# bash 会话工作目录（相对工作区；同一请求内的多条命令之间持久，锚定工作区内）。
# 这里存的是**盒子里的键**而不是 ContextVar 本身：LangGraph 的 ToolNode 每轮工具
# 调用都 `asyncio.gather` 出新的子 task，子 task 只继承上下文的副本，`_var.set()`
# 传不回父上下文——直接放字符串的话，`cd` 写下的目录会在下一轮工具调用时消失
# （回归测试 tests/test_session_cwd_across_tool_tasks.py）。盒子对象本身由
# set_file_access_level 在每个请求入口换新，所以跨请求/跨会话依旧互不可见。
_session_cwd_box_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "chemai_session_cwd_box", default=None)
_CWD_KEY = "rel"

_SKELETON_DIRS = ["学生资料", "教案备课", "试卷真题", "AI生成", "临时"]
_SKELETON_README = (
    "# ChemAI 教师工作区\n\n"
    "这是 AI 助教的文件工作区（老师的本地文件夹）。\n\n"
    "目录约定：\n"
    "- 学生资料/：上传的试卷、答题卡、名单等学生材料\n"
    "- 教案备课/：教案、课件与备课笔记\n"
    "- 试卷真题/：真题与试卷存档\n"
    "- AI生成/：AI 生成的试卷、诊断报告、学习计划（自动按日期归档）\n"
    "- 临时/：中转与草稿文件\n\n"
    "AI 使用规则：读写默认锚定本工作区；访问工作区外需老师批准；"
    "生成物请写入 AI生成/ 对应日期目录。\n"
)

_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def set_file_access_level(level: str) -> str:
    """Set the per-conversation file access level. Unknown values fall back
    to workspace (default-deny). 对话边界：清除残留的一次性放行与 bash 会话 cwd。
    档位：off 关闭 | workspace 工作区（默认，区外按次审批）| full 完全访问
    （区外直接放行，敏感文件仍拒绝；2026-08 对齐 DSH danger-full-access）。"""
    _access_level_var.set(level if level in ("off", "workspace", "full") else "workspace")
    _elevate_once_var.set(False)
    _session_cwd_box_var.set({_CWD_KEY: ""})
    return _access_level_var.get()


# bash 会话工作目录（pi agent session-cwd 同款：同一请求内持久，锚定工作区内）


def _session_cwd_box() -> dict:
    """当前请求的会话 cwd 盒子。盒子对象跨工具子 task 共享，所以写入看得见。

    没走请求入口（单测/评估直接调工具，或未来新增端点漏注入）时就地建一个：
    它只活在当前上下文里，最坏退化成「cd 不跨调用持久」，不会跨会话串目录。"""
    box = _session_cwd_box_var.get()
    if box is None:
        box = {_CWD_KEY: ""}
        _session_cwd_box_var.set(box)
    return box


def set_session_cwd(rel: str):
    """设置 bash 会话 cwd（相对工作区）。越界或不存在返回 None，空串回到工作区根。"""
    box = _session_cwd_box()
    if not rel:
        box[_CWD_KEY] = ""
        return ""
    root = workspace_root()
    target = (root / rel).resolve()
    if not _is_inside(target, root) or not target.is_dir():
        return None
    cwd = target.relative_to(root).as_posix()
    box[_CWD_KEY] = cwd
    return cwd


def session_cwd_rel() -> str:
    return _session_cwd_box().get(_CWD_KEY, "")


def bash_cwd() -> Path:
    """bash 执行的当前目录（工作区内会话 cwd）。"""
    root = workspace_root()
    rel = session_cwd_rel()
    return (root / rel) if rel else root


def file_access_level() -> str:
    return _access_level_var.get()


def set_elevate_once():
    """放行下一次工作区外访问（按次审批，批准后由 guard 层调用）。"""
    _elevate_once_var.set(True)


def _consume_elevation() -> bool:
    granted = _elevate_once_var.get()
    _elevate_once_var.set(False)
    return granted


def set_workspace_root(path: str):
    """Override the workspace root for the current conversation (teacher-chosen
    folder). Empty string resets to the default."""
    if not path:
        _ws_override_var.set("")
        return None
    override = str(Path(path).expanduser())
    _ws_override_var.set(override)
    return override


def _ensure_skeleton(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "README.md").exists():
        for d in _SKELETON_DIRS:
            (root / d).mkdir(exist_ok=True)
        (root / "README.md").write_text(_SKELETON_README, encoding="utf-8")


def workspace_root() -> Path:
    override = _ws_override_var.get()
    if override:
        root = Path(override).resolve()
    else:
        default = os.environ.get(WORKSPACE_ENV) or DEFAULT_WORKSPACE
        root = Path(default).expanduser().resolve()
    _ensure_skeleton(root)
    return root


def _is_inside(resolved: Path, root: Path) -> bool:
    try:
        resolved.relative_to(root)
        return True
    except ValueError:
        return False


_ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".default", ".orig")


def _is_dotenv(low: str) -> bool:
    """.env 本体 / .env.local 等点分变体 / prod.env 后缀形式都算敏感，
    但放行 .env.example 这类可共享模板——否则 W3 会误伤仓库里的公开样例。"""
    if not (low == ".env" or low.endswith(".env") or ".env." in low):
        return False
    return not low.endswith(_ENV_TEMPLATE_SUFFIXES)


_SENSITIVE_BASENAMES = {".htpasswd", ".netrc", ".pgpass"}


def _is_private_key(low: str) -> bool:
    """OpenSSH 私钥的**形状**：无扩展名、id_ 开头（id_rsa / id_ed25519 / id_ecdsa
    / id_dsa…）。按算法名列举过——名单会随新算法漂，形状不会。公钥带 .pub，
    带扩展名的普通文件（如 id_名单.csv）不误伤。"""
    return low.startswith("id_") and "." not in low


def _is_sensitive(name: str) -> bool:
    low = name.lower()
    return (
        _is_dotenv(low)
        or low.endswith((".pem", ".key", ".p12", ".pfx"))
        or "secret" in low
        or "credential" in low  # 单数即覆盖 credential/credentials
        or low in _SENSITIVE_BASENAMES
        or _is_private_key(low)
    )


def _approval_needed(path_str: str) -> dict:
    return {
        "approval_needed": True,
        "message": f"工作区外访问需要批准: {path_str}",
        "target": path_str,
    }


def _resolve(path_str: str):
    """Return (resolved_path, None) or (None, error). error 为 dict 时表示
    approval_needed（工作区外访问，待批准重放）。

    elevation（按次放行）只在真正越界时消费——区内路径不浪费放行额度。"""
    level = file_access_level()
    if level == "off":
        return None, "文件访问已关闭（当前对话设置为「关闭」档）"
    if not path_str:
        return None, "请指定路径"
    p = Path(path_str)
    root = workspace_root()
    if p.is_absolute():
        resolved = p.resolve()
        outside = not _is_inside(resolved, root)
    else:
        resolved = (root / p).resolve()
        outside = not _is_inside(resolved, root)
    if outside:
        # full 档（完全访问）：区外直接放行，不再按次审批（敏感文件仍拒绝）
        if level != "full":
            elevated = _consume_elevation()
            if not elevated:
                return None, _approval_needed(path_str)
    if _is_sensitive(resolved.name):
        return None, f"敏感文件被拒绝: {resolved.name}"
    return resolved, None


def _error_json(err):
    if isinstance(err, dict) and err.get("approval_needed"):
        return json.dumps({
            "approval_needed": True,
            "message": err["message"],
            "target": err["target"],
        }, ensure_ascii=False)
    return json.dumps({"error": err}, ensure_ascii=False)


def _rel_display(resolved: Path):
    """Relative path inside workspace (posix separators), absolute otherwise."""
    try:
        return resolved.relative_to(workspace_root()).as_posix()
    except ValueError:
        return str(resolved)


def _read_text(path: Path):
    """Return (text, None) or (None, error). utf-8 first, gbk fallback,
    binary refused.

    W3 咽喉：所有按内容读取都过这里，故敏感判定装在此处一次性覆盖
    read/grep/find——批准一个目录不再连带把它下面的 .env/私钥读进模型。"""
    if _is_sensitive(path.name):
        return None, f"敏感文件被拒绝: {path.name}"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, f"文件不存在: {path.name}"
    if b"\x00" in raw[:8192]:
        return None, f"二进制文件，拒绝读取: {path.name}"
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return None, f"无法解码（不支持编码）: {path.name}"


async def read(path: str = "", offset: int = 0, limit: int = 0) -> str:
    """读取文件 — 读取文本文件（utf-8/gbk），支持行分页。

    何时用：需要读取文件内容时——工作区内直接读；老师要求读工作区外的文件
    （桌面/其他目录）时也直接调用本工具尝试——**必须用绝对路径**（如
    C:\\Users\\Administrator\\Desktop\\xxx.md），不要未尝试就声称无法访问。
    **不知道文件的确切名称时，先用 find 列出目录拿到真实文件名，不要凭猜测拼文件名。**
    会发生什么：工作区外路径会返回「工作区外访问需要批准」——这是确认门：用一句话
    告诉老师在等确认即可，系统已登记本次调用，老师在确认卡上批准后会自动重放，
    不要重复调用本工具，也没有独立的确认工具可调。
    offset/limit 为 1-indexed 行号分页：offset 起始行（0=从头），limit 最大行数（0=全部）"""
    resolved, err = _resolve(path)
    if err:
        return _error_json(err)
    text, err = _read_text(resolved)
    if err:
        return json.dumps({"error": err}, ensure_ascii=False)
    lines = text.splitlines()
    total_lines = len(lines)
    start = max(0, offset - 1) if offset > 0 else 0
    end = (start + limit) if limit > 0 else total_lines
    shown = lines[start:end]
    return json.dumps({
        "content": "\n".join(shown),
        "path": _rel_display(resolved),
        "chars": len("\n".join(shown)),
        "line_offset": offset,
        "total_lines": total_lines,
        "truncated": end < total_lines,
    }, ensure_ascii=False)


async def write(path: str = "", content: str = "") -> str:
    """写入文件 — 创建/覆盖文本文件（自动建目录）。

    何时用：把生成的内容（试卷、报告、笔记）保存为文件；老师指定工作区外路径时
    也直接尝试本工具（会返回「工作区外访问需要批准」→ 说明在等确认，老师批准后
    系统自动重放本次写入）。
    会发生什么：写入 AI生成/ 下的文件自动按日期归档；工作区外需老师按次批准"""
    resolved, err = _resolve(path)
    if err:
        return _error_json(err)
    # AI生成/ 自动按日期归档（Spec: 骨架 + 按日期归档）
    root = workspace_root()
    if _is_inside(resolved, root):
        rel = resolved.relative_to(root)
        if rel.parts and rel.parts[0] == "AI生成" and (
                len(rel.parts) == 1 or not _DATE_DIR.match(rel.parts[1])):
            resolved = root / "AI生成" / datetime.now().strftime("%Y-%m-%d") / rel.name
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return json.dumps({"written": True, "path": _rel_display(resolved),
                       "chars": len(content)}, ensure_ascii=False)


async def edit(path: str = "", old_string: str = "", new_string: str = "",
               edits: str = "") -> str:
    """精确替换 — 单处（old_string/new_string）或多处（edits JSON 数组）替换。

    何时用：需要修改工作区文件的指定片段
    会发生什么（pi agent 同款语义）：每个 old_text 必须在原文件中恰好匹配 1 次，
    同一调用内的多个替换互不重叠、全部针对原文件匹配；任一条不符则报错且文件保持不变
    edits 格式：[{"old_text": "...", "new_text": "..."}]"""
    resolved, err = _resolve(path)
    if err:
        return _error_json(err)
    text, err = _read_text(resolved)
    if err:
        return json.dumps({"error": err}, ensure_ascii=False)

    # ── 多替换（edits）优先 ──
    if edits:
        try:
            edit_list = json.loads(edits)
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "edits 必须是 JSON 数组"}, ensure_ascii=False)
        if not isinstance(edit_list, list) or not edit_list:
            return json.dumps({"error": "edits 不能为空"}, ensure_ascii=False)
        pairs = []
        for e in edit_list:
            if not isinstance(e, dict):
                return json.dumps({"error": "edits 元素必须是 {old_text, new_text}"}, ensure_ascii=False)
            old_t = str(e.get("old_text", ""))
            new_t = str(e.get("new_text", ""))
            if not old_t:
                return json.dumps({"error": "edits 元素缺少 old_text"}, ensure_ascii=False)
            cnt = text.count(old_t)
            if cnt == 0:
                return json.dumps({"error": f"未找到目标片段: {old_t[:40]}（文件保持不变）"},
                                  ensure_ascii=False)
            if cnt > 1:
                return json.dumps({
                    "error": f"目标片段匹配到 {cnt} 处，请提供唯一片段: {old_t[:40]}（文件保持不变）",
                }, ensure_ascii=False)
            pairs.append((old_t, new_t))
        # 互不重叠检查（针对原文件区间）
        ranges = []
        for old_t, _ in pairs:
            start = text.find(old_t)
            ranges.append((start, start + len(old_t)))
        for i in range(len(ranges)):
            for j in range(i + 1, len(ranges)):
                if ranges[i][0] < ranges[j][1] and ranges[j][0] < ranges[i][1]:
                    return json.dumps({"error": "edits 存在重叠区间，请合并为一条（文件保持不变）"}, ensure_ascii=False)
        new_text = text
        for old_t, new_t in pairs:
            new_text = new_text.replace(old_t, new_t, 1)
        resolved.write_text(new_text, encoding="utf-8")
        return json.dumps({"edited": True, "path": _rel_display(resolved),
                           "replacements": len(pairs)}, ensure_ascii=False)

    # ── 单替换（向后兼容） ──
    if not old_string:
        return json.dumps({"error": "请提供要替换的原文片段 old_string"}, ensure_ascii=False)

    count = text.count(old_string)
    if count == 0:
        context = text[:100].replace("\n", " ")
        return json.dumps({
            "error": f"未找到目标片段（匹配 0 处），文件保持不变。文件开头上下文: {context}…",
        }, ensure_ascii=False)
    if count > 1:
        return json.dumps({
            "error": f"目标片段匹配到 {count} 处，请提供更精确的唯一片段；文件保持不变",
        }, ensure_ascii=False)

    new_text = text.replace(old_string, new_string, 1)
    resolved.write_text(new_text, encoding="utf-8")
    return json.dumps({"edited": True, "path": _rel_display(resolved),
                       "replaced_chars": len(old_string)}, ensure_ascii=False)


async def find(pattern: str = "*", path: str = "") -> str:
    """查找文件 — 按名称模式（支持 * 通配符）查找文件。

    何时用：需要定位文件时。默认查工作区；**老师提到桌面等工作区外的位置时，
    传 path=那个目录的绝对路径**（如 C:\\Users\\Administrator\\Desktop）——会返回
    「工作区外访问需要批准」，这是确认门：用一句话告诉老师在等确认即可，系统已登记
    本次调用，老师在确认卡上批准后会自动重放，不要重复调用本工具。
    **不知道文件的确切名称时，先用本工具列出目录看真实文件名，不要凭猜测拼文件名。**
    会发生什么：返回路径列表（工作区内为相对路径，区外为绝对路径），最多 200 条"""
    import fnmatch
    if file_access_level() == "off":
        return json.dumps({"error": "文件访问已关闭（当前对话设置为「关闭」档）"}, ensure_ascii=False)
    root = workspace_root()
    if path:
        base, err = _resolve(path)
        if err:
            return _error_json(err)
        if not base.exists():
            return json.dumps({"error": f"路径不存在: {path}"}, ensure_ascii=False)
    else:
        base = root
    matches = []
    skipped_sensitive = 0
    for p in ([base] if base.is_file() else base.rglob("*")):
        if not p.is_file():
            continue
        if _is_sensitive(p.name):
            skipped_sensitive += 1  # W3：连敏感文件名都不枚举给模型
            continue
        rel_str = _rel_display(p)
        if fnmatch.fnmatch(rel_str, pattern) or fnmatch.fnmatch(p.name, pattern):
            matches.append(rel_str)
        if len(matches) >= MAX_FIND_RESULTS:
            break
    out = {"matches": matches, "count": len(matches)}
    if skipped_sensitive:
        out["skipped_sensitive"] = skipped_sensitive
    return json.dumps(out, ensure_ascii=False)


async def grep(pattern: str = "", path: str = "") -> str:
    """文本搜索 — 在工作区（或指定目录）内的文本文件中按内容搜索。

    何时用：需要按内容定位信息。默认搜工作区；**老师提到桌面等工作区外的位置时，
    传 path=那个目录的绝对路径**——会返回「工作区外访问需要批准」，这是确认门：
    用一句话告诉老师在等确认即可，系统已登记本次调用，老师在确认卡上批准后会自动
    重放，不要重复调用本工具。
    会发生什么：返回 文件/行号/行内容 匹配列表，最多 100 条；二进制文件自动跳过"""
    if file_access_level() == "off":
        return json.dumps({"error": "文件访问已关闭（当前对话设置为「关闭」档）"}, ensure_ascii=False)
    if not pattern:
        return json.dumps({"error": "请提供搜索模式 pattern"}, ensure_ascii=False)
    root = workspace_root()
    if path:
        target, err = _resolve(path)
        if err:
            return _error_json(err)
        base = target
    else:
        base = root

    files = [base] if base.is_file() else list(base.rglob("*"))
    matches = []
    skipped_sensitive = 0
    for p in files:
        if not p.is_file():
            continue
        if _is_sensitive(p.name):
            skipped_sensitive += 1  # _read_text 已拒读，这里只为「跳过 N 个」计数可见
            continue
        try:
            text, err = _read_text(p)
        except (OSError, UnicodeDecodeError):
            continue
        if err:
            continue  # binary / undecodable -> skip silently
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern in line:
                matches.append({"file": _rel_display(p),
                                "line": lineno, "text": line[:200]})
                if len(matches) >= MAX_GREP_MATCHES:
                    out = {"matches": matches, "count": len(matches),
                           "truncated": True}
                    if skipped_sensitive:
                        out["skipped_sensitive"] = skipped_sensitive
                    return json.dumps(out, ensure_ascii=False)
    out = {"matches": matches, "count": len(matches)}
    if skipped_sensitive:
        out["skipped_sensitive"] = skipped_sensitive
    return json.dumps(out, ensure_ascii=False)
