"""漏 await 的扫描：拿到协程票据当结果用，坏法是"每次必抛"，而且是静默的。

本机 2026-09-27 抓到七处，全在 OCR 这一片。`OCRService` 的方法全是 `async`，
而调用方写成
`result = ocr_service.recognize_answer_sheet(...)`，下一行 `result.get("success")` 就抛
`AttributeError: 'coroutine' object has no attribute 'get'`。其中五处在端点里被
`except Exception` 吞成一句"解析失败"，第六处（文档解析）让图片上传从来没成功过一次。
ADR-0025。
"""
import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[1]
SERVICE = BACKEND / "app/services/ocr_service.py"


def _async_ocr_methods() -> set:
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    return {n.name for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef)
            for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and not n.name.startswith("_")}


def _receiver_is_an_ocr_service(node: ast.AST) -> bool:
    """`ocr_service.foo()` / `self.ocr_service.foo()` 这类接收者。

    只认这个形状是为了不误伤同名方法 —— `grade` 既是 `OCRService` 的 async 方法，也是
    `grading_center` 的同步函数。
    """
    if isinstance(node, ast.Name):
        return node.id == "ocr_service"
    if isinstance(node, ast.Attribute):
        return node.attr == "ocr_service"
    return False


def _coroutine_consumers(tree: ast.AST) -> set:
    """`asyncio.create_task(svc.foo())` 这类是**故意**拿协程，不算漏 await。

    漏 await 的特征是"把协程当结果用"（下一句 `.get()`），交给事件循环调度不是。
    """
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in {"create_task", "gather", "ensure_future", "wait_for", "shield",
                    "run_coroutine_threadsafe"}:
            for arg in node.args:
                ids.add(id(arg))
    return ids


def _unawaited_calls(path: pathlib.Path, methods: set) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    ok = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Await)}
    ok |= _coroutine_consumers(tree)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in methods and _receiver_is_an_ocr_service(node.func.value) \
                and id(node) not in ok:
            try:
                where = path.relative_to(BACKEND)
            except ValueError:      # 正向对照用的是临时目录里的样例文件
                where = path.name
            bad.append(f"{where}:{node.lineno}: {node.func.attr}")
    return bad


def test_no_method_of_ocr_service_is_called_without_await():
    methods = _async_ocr_methods()
    assert "recognize_answer_sheet" in methods, "扫描对象本身变了，这条绊线就白设了"

    offenders = []
    # chem_skills 是被 app 直接 import 的一方源码（不是外部库），漏了它就等于给
    # "异步方法当结果用"留了一条不被任何检查覆盖的路 —— 真题导入就是这么坏的。
    roots = [BACKEND / "app", BACKEND / "agent", BACKEND / "chem_skills",
             BACKEND / "evals"]
    scanned = 0
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if path == SERVICE:
                continue
            scanned += 1
            offenders += _unawaited_calls(path, methods)
    assert scanned > 130, f"只扫到 {scanned} 个文件，扫描根目录没配对"
    assert not offenders, "async 方法漏 await 拿到的是协程，下一句 .get() 必抛：" + "; ".join(offenders)


def test_the_scanner_actually_finds_one(tmp_path):
    """正向对照：扫不到文件与没有违规是两回事（这条仓库里别的绊线也这么自证）。"""
    bad = tmp_path / "app" / "api" / "bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "from app.services.ocr_service import ocr_service\n"
        "async def handler(data):\n"
        "    result = ocr_service.recognize_answer_sheet(data)\n"
        "    return result.get('success')\n", encoding="utf-8")
    methods = _async_ocr_methods()
    hits = _unawaited_calls(bad, methods)
    assert len(hits) == 1 and "recognize_answer_sheet" in hits[0], hits
    # 同一个调用交给事件循环调度就不是违规 —— 判据是"当结果用"，不是"没写 await"
    good = tmp_path / "app" / "api" / "good.py"
    good.write_text(
        "import asyncio\nfrom app.services.ocr_service import ocr_service\n"
        "async def handler(data):\n"
        "    asyncio.create_task(ocr_service.grade(data))\n", encoding="utf-8")
    assert _unawaited_calls(good, methods) == []
