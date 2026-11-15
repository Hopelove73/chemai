"""解析服务健康检测：老师问"解析不了"时，模型看到的是哪一句话。

`check_parser_services` 是 agent 的一个工具，它的返回会被模型原样转述给老师。上一版
这个函数里的 OCR 那一项写死了"看百度有没有密钥"，而答题卡 OCR 从 ADR-0004 起默认是
本地 PP-OCRv6、不需要任何密钥 —— 于是一台配置完全正确的机器（三把百度密钥都不设）会被
报成"OCR 后端未配置"，模型照实让老师去申请百度密钥。这里钉住的是：**判据跟着
`OCR_SHEET_PROVIDER` 走，每个后端按它自己真正的依赖判**。
"""
import importlib
import os

import pytest

from app.config import config
from app.services.document_parse_service import DocumentParseService
from app.services.ocr_mineru import (
    BaiduOCRProvider, MinerUProvider, PaddleOCRProvider, resolve_sheet_provider_name,
)


@pytest.fixture
def svc():
    return DocumentParseService()


@pytest.fixture
def no_baidu_keys(monkeypatch):
    for name in ("BAIDU_OCR_API_KEY", "BAIDU_OCR_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("installed", [True, False])
def test_paddle_is_judgeable_without_any_key(svc, monkeypatch, no_baidu_keys, installed):
    """默认后端是本地 paddle：判它只看包在不在，密钥跟这件事无关。"""
    monkeypatch.setattr(config, "OCR_SHEET_PROVIDER", "paddle", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name: object() if name == "paddleocr" and installed else None)
    status = svc.check_services_status()["ocr"]
    assert status["provider"] == "paddle"
    assert status["available"] is installed
    if not installed:
        assert "paddleocr" in status["note"], "不可用却没说清缺什么，老师无从下手"


def test_baidu_still_demands_its_keys(svc, monkeypatch, no_baidu_keys):
    """换成百度后端时，密钥才是它的依赖 —— 判据要跟着换，不是一律说"不需要密钥"。"""
    monkeypatch.setattr(config, "OCR_SHEET_PROVIDER", "baidu", raising=False)
    off = svc.check_services_status()["ocr"]
    assert off["available"] is False and "BAIDU_OCR" in off["note"], off

    monkeypatch.setenv("BAIDU_OCR_API_KEY", "k")
    monkeypatch.setenv("BAIDU_OCR_SECRET_KEY", "s")
    on = svc.check_services_status()["ocr"]
    assert on["available"] is True and on["provider"] == "baidu", on
    # 只配一半不算配好
    monkeypatch.delenv("BAIDU_OCR_SECRET_KEY")
    assert svc.check_services_status()["ocr"]["available"] is False


def test_mineru_backend_is_judged_by_whether_the_models_are_downloaded(svc, monkeypatch):
    """MinerU 的依赖是模型文件，不是密钥：判据必须走 `_mineru_status`。"""
    monkeypatch.setattr(config, "OCR_SHEET_PROVIDER", "mineru", raising=False)
    status = {"available": False, "note": "MinerU 模型未下载：mineru-models-download -s modelscope -m all"}
    seen = []

    def _fake():
        seen.append(1)
        return status

    monkeypatch.setattr(svc, "_mineru_status", _fake)
    assert svc.check_services_status()["ocr"] == {"provider": "mineru", **status}
    # 一次状态检查只该探一回：多探一次就是多 print 一行，也多了两处结论互相矛盾的机会
    assert seen == [1], seen


def test_missing_cli_and_missing_models_are_two_different_answers(svc, monkeypatch, tmp_path):
    """以前这两种情况都印"Models not downloaded"，于是答案永远是"去下模型"。

    没装包的人照着提示去下模型，下完还是不可用 —— 报清楚缺哪一样是这条通路的唯一入口。
    """
    monkeypatch.setattr(svc, "mineru_client", None, raising=False)
    no_cli = svc._mineru_status()
    assert no_cli["available"] is False and "pip install mineru" in no_cli["note"], no_cli

    monkeypatch.setattr(svc, "mineru_client", object(), raising=False)
    monkeypatch.setattr(DocumentParseService, "_configured_models_dir",
                        staticmethod(lambda: ""), raising=False)
    no_models = svc._mineru_status()
    assert no_models["available"] is False and "mineru-models-download" in no_models["note"], no_models

    real = tmp_path / "models"
    (real / "PP-DocLayoutV2").mkdir(parents=True)
    monkeypatch.setattr(DocumentParseService, "_configured_models_dir",
                        staticmethod(lambda: str(real)), raising=False)
    ok = svc._mineru_status()
    assert ok["available"] is True and str(real) in ok["note"], ok


def test_models_dir_comes_from_miners_own_config(svc, tmp_path, monkeypatch):
    """模型在哪个目录要问 MinerU 自己的配置，不在这里数 home 下的三个缓存。

    上一版硬编码 `~/.cache/modelscope/hub` 等三个 home 路径；把模型下到别的盘
    （本机就在 D:/MinerU/models）之后，已经下好的模型会被判成"未下载"。
    """
    import mineru.utils.config_reader as cr

    filled = tmp_path / "d-drive-models"
    (filled / "unimer").mkdir(parents=True)
    empty = tmp_path / "half-installed"
    empty.mkdir()
    monkeypatch.setattr(cr, "get_local_models_dir",
                        lambda: {"pipeline": str(empty), "vlm": str(filled)})
    assert svc._configured_models_dir() == str(filled), "空目录不算就绪，有内容的那个才算"

    monkeypatch.setattr(cr, "get_local_models_dir", lambda: None)
    assert svc._configured_models_dir() == ""


def test_the_client_finds_a_pip_installed_mineru(monkeypatch, tmp_path):
    """`find_mineru_root()` 以前只认源码检出（`MinerU-master`），pip 装好了也报 not found。"""
    from chem_skills.chemistry_parser import mineru_client as mc

    site = tmp_path / "site-packages" / "mineru" / "cli"
    site.mkdir(parents=True)
    (site / "api_client.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(mc, "_installed_package_root",
                        lambda: str(tmp_path / "site-packages"))
    monkeypatch.delenv("MINERU_PATH", raising=False)
    assert mc.find_mineru_root() == str(tmp_path / "site-packages")

    # 显式 MINERU_PATH 排在最前：它今天早就存在，却没有任何代码读它
    override = tmp_path / "checkout" / "mineru" / "cli"
    override.mkdir(parents=True)
    (override / "api_client.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("MINERU_PATH", str(tmp_path / "checkout"))
    assert mc.mineru_root_candidates()[0] == str(tmp_path / "checkout")
    assert mc.find_mineru_root() == str(tmp_path / "checkout")


def test_the_name_and_the_provider_never_disagree(monkeypatch):
    """认不出的取值：状态里说的后端必须就是 `get_ocr_provider()` 真正挑中的那个。

    两处各写一遍"默认是谁"迟早分叉，分叉的表现就是"报的那个后端不是干活的那个"。
    """
    cases = {"paddle": PaddleOCRProvider, "baidu": BaiduOCRProvider,
             "mineru": MinerUProvider, "zhuping-ocr-v9": MinerUProvider}
    for name, cls in cases.items():
        monkeypatch.setattr(config, "OCR_SHEET_PROVIDER", name, raising=False)
        from app.services import ocr_mineru
        assert isinstance(ocr_mineru.get_ocr_provider(), cls), name
        assert resolve_sheet_provider_name() == (
            name if name in ("paddle", "baidu", "mineru") else "mineru"), name


def test_vision_status_reads_the_same_list_the_call_uses(svc, monkeypatch):
    """状态里的视觉后端名单，必须就是调用链要用的那一份。

    判据在自己身上又数一遍，是这台机器上正在发生的谎：只有 DeepSeek 一把 key，而
    `deepseek-flash` 本来能看图，状态却按"智谱/小米有没有 key"报"未配置 Vision 模型"。
    """
    class _Chain:
        def __init__(self, backends):
            self._backends = backends

        def vision_backends(self):
            return self._backends

    empty = _Chain([])
    monkeypatch.setattr(svc, "llm_service", empty, raising=False)
    off = svc.check_services_status()["vision"]
    assert off["available"] is False and "DEEPSEEK_MODEL" in off["note"], off

    chain = _Chain([{"name": "deepseek", "url": "u", "key": "k", "model": "deepseek-flash",
                     "thinking_off": "true"},
                    {"name": "mimo", "url": "u", "key": "k", "model": "mimo-x", "thinking_off": ""}])
    monkeypatch.setattr(svc, "llm_service", chain, raising=False)
    on = svc.check_services_status()["vision"]
    assert on["available"] is True and on["provider"] == "deepseek/mimo", on


def test_the_vision_chain_follows_which_model_is_configured(monkeypatch):
    """链的取值：DeepSeek 要看型号是否多模态，MiMo 恒在（它自带一个视觉专用型号）。"""
    from app.services.llm_service import LLMService

    s = LLMService()
    monkeypatch.setattr(s, "mimo_key", "sk-mimo", raising=False)
    monkeypatch.setattr(s, "deepseek_key", "sk-ds", raising=False)
    monkeypatch.setattr(s, "deepseek_model", "deepseek-chat", raising=False)
    assert [b["name"] for b in s.vision_backends()] == ["mimo"], "文本型号不该进视觉链"

    monkeypatch.setattr(s, "deepseek_model", "deepseek-flash", raising=False)
    assert [b["name"] for b in s.vision_backends()] == ["deepseek", "mimo"]

    monkeypatch.setattr(s, "mimo_key", "", raising=False)
    assert [b["name"] for b in s.vision_backends()] == ["deepseek"]


def test_mimo_uses_a_vision_model_not_the_text_one(monkeypatch):
    """视觉那条给 MiMo 单开一个型号名，不复用文本 provider 的 `MIMO_MODEL`。

    实测：同一张 1×1 图、同一个 OpenAI 形状（`content[].image_url` + data URI）——
    `mimo-v2.6-flash` 回 400 `image_url is invalid`，`mimo-v2.6-pro` 与 `mimo-v2.5`
    都读对颜色。用户指定的文本型号是 flash，所以不能拿它当视觉型号，也不能反过来把文本
    整片升到 pro。
    """
    from app.services.llm_service import LLMService

    s = LLMService()
    monkeypatch.setattr(s, "mimo_key", "sk-mimo", raising=False)
    monkeypatch.setattr(s, "mimo_model", "mimo-v2.6-flash", raising=False)
    monkeypatch.setattr(s, "mimo_vision_model", "mimo-v2.6-pro", raising=False)
    backend = [b for b in s.vision_backends() if b["name"] == "mimo"][0]
    assert backend["model"] == "mimo-v2.6-pro", "视觉链用了 flash，实测会 400"
    assert backend["thinking_off"] == "true", "固定 JSON 输出：MiMo 也吃 thinking=disabled"

    # 型号名清空 = 这家从视觉链上摘下来（配置项还在，只是不再当后端用）
    monkeypatch.setattr(s, "mimo_vision_model", "", raising=False)
    assert "mimo" not in [b["name"] for b in s.vision_backends()]


def test_mimo_is_not_a_vision_backend(tmp_path):
    """为什么 mimo 不在视觉链上：它只收 http 图片链接，本仓传的是本地答卷的 data URI。

    实测（mimo-v2.6-flash）：OpenAI 形状的 `image_url: data:image/png;base64,...` 直接
    `HTTP 400 Invalid request parameters`；同一张图换成公网链接才读得出内容（prompt_tokens
    从 17 涨到 106）。答题卡是学生本机上传的，没有公网链接可给。这条断言把那个形状写死在
    测试里，谁把 mimo 塞回链上，这条会告诉他原因。
    """
    from app.services.llm_service import LLMService

    s = LLMService()
    assert "mimo" not in [b["name"] for b in s.vision_backends()]
    prompt = s._get_vision_prompt("answer_sheet")
    assert "JSON" in prompt, "复核要的是固定格式，prompt 变了要看是不是换了形状"


def test_the_tool_description_does_not_name_baidu_as_the_default():
    """绊线：工具描述是模型选工具与组织回答的唯一接口（CONTEXT.md）。

    描述里写着"检测OCR（百度）"，模型就会把整套 OCR 理解成百度那一家的，然后按百度的
    缺配置去跟老师解释 —— 状态字段修对了也没用。
    """
    from agent.tools.parser import check_parser_services
    doc = check_parser_services.__doc__ or ""
    assert "OCR（百度）" not in doc and "（百度）" not in doc, doc
    assert "PP-OCRv6" in doc and "OCR_SHEET_PROVIDER" in doc, \
        "描述没跟上判据：模型无从知道默认引擎是本地 paddle"
    # ADR-0025 之后两条路共用一个引擎；描述再说"上传那条是百度、与批处理不通用"，
    # 模型就会对着一个本来能工作的系统让老师去申请百度密钥。
    assert "百度教育接口" not in doc and "不通用" not in doc, doc


def test_an_installed_mineru_is_not_used_as_the_subprocess_cwd(tmp_path):
    """pip 装的 mineru，子进程的 cwd 绝不能是 site-packages。

    `python -m mineru.cli.client` 会把 cwd 摆到 sys.path 最前面，于是 site-packages 里任何
    同名老包都会遮蔽标准库 —— 本机就是活案例：那里躺着一个 py2 时代的 `uuid.py`
    （`if not 0 <= time_low < 1<<32L`），`import click` 当场 SyntaxError，MinerU 一句都跑不
    起来。反过来，源码检出必须拿那个目录当 cwd，不然 `import mineru` 找不到。
    """
    from chem_skills.chemistry_parser import mineru_client as mc

    site = tmp_path / "site-packages" / "mineru" / "cli"
    site.mkdir(parents=True)
    for name in ("api_client.py", "client.py"):
        (site / name).write_text("", encoding="utf-8")
    installed_root = str(tmp_path / "site-packages")

    c = mc.MinerUClient(mineru_root=installed_root)
    assert c.is_source_checkout is False
    assert str(c.run_root) != installed_root, "cwd 又指回 site-packages 了"

    checkout = tmp_path / "checkout"
    (checkout / "mineru" / "cli").mkdir(parents=True)
    for name in ("api_client.py", "client.py"):
        (checkout / "mineru" / "cli" / name).write_text("", encoding="utf-8")
    (checkout / "pyproject.toml").write_text("", encoding="utf-8")
    assert str(mc.MinerUClient(mineru_root=str(checkout)).run_root) == str(checkout)


def test_document_parse_reports_the_shared_engine_plus_its_vision_safety_net(svc, monkeypatch):
    """`ocr_document` 报的是同一个引擎，外加上传那条独有的东西：读不出文本时有谁兜底。

    两条路以前真的不是一个引擎（上传那条绕过注册表直接打百度教育接口），所以这个字段当年
    必须单独存在、且必须跟 `ocr` 不一样。现在共用注册表了，它要是又变成"另一个引擎"，
    就是分叉回来了。
    """
    monkeypatch.setattr(config, "OCR_SHEET_PROVIDER", "paddle", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(svc.llm_service, "vision_backends",
                        lambda: [{"name": "deepseek"}], raising=False)
    status = svc.check_services_status()
    doc = status["ocr_document"]
    assert doc["provider"] == "paddle/deepseek", doc
    assert "paddle" in doc["note"] and "deepseek" in doc["note"], doc
    assert status["ocr"]["provider"] == "paddle"

    # 引擎坏了、视觉在：仍然 available，但主角换成视觉，且说清引擎那侧缺什么
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    no_engine = svc.check_services_status()["ocr_document"]
    assert no_engine["available"] is True and no_engine["provider"] == "deepseek", no_engine
    assert "paddleocr" in no_engine["note"], "只说没引擎不说缺什么，老师无从下手"

    # 两边都没有：available 必须为假
    monkeypatch.setattr(svc.llm_service, "vision_backends", lambda: [], raising=False)
    off = svc.check_services_status()["ocr_document"]
    assert off["available"] is False and off["provider"] == "无", off


def _patch_engine(monkeypatch, text=None, boom=None):
    """把注册表换成一个假引擎，记下它被要求读的是不是原始字节。"""
    import app.services.ocr_mineru as reg

    seen = {}

    class _Fake:
        def extract_text(self, image_bytes, suffix=".png"):
            seen["bytes"] = image_bytes
            if boom:
                raise boom
            return text

    monkeypatch.setattr(reg, "get_ocr_provider", lambda: _Fake())
    monkeypatch.setattr(reg, "resolve_sheet_provider_name", lambda: "paddle")
    return seen


def test_uploaded_images_go_through_the_registry(svc, monkeypatch):
    """上传解析要的引擎来自注册表，因此 `OCR_SHEET_PROVIDER=paddle` 对两条路都成立。"""
    seen = _patch_engine(monkeypatch, text="学号：202610101 姓名：张三 1. 钠与水反应")
    got = svc._ocr_text(b"PNGBYTES")
    assert got["provider"] == "paddle" and "学号" in got["text"], got
    assert seen["bytes"] == b"PNGBYTES", "传进去的不是原始字节，引擎拿到的是别的东西"


def test_the_image_path_does_not_touch_the_baidu_service(svc):
    """绊线：图片解析不得再回到 `OCRService`（那条是 async 的，同步路径没 await 它，每次必抛）。

    查的是调用形状而不是名字 —— 这几个函数的 docstring 里正写着旧写法叫什么。
    """
    import inspect

    for name in ("_parse_image", "_parse_image_with_ocr", "_ocr_text"):
        src = inspect.getsource(getattr(DocumentParseService, name))
        assert "self.ocr_service" not in src, name
        assert "recognize_answer_sheet(" not in src, name


def test_an_empty_image_falls_back_to_vision(svc, monkeypatch):
    _patch_engine(monkeypatch, text="   \n  ")
    monkeypatch.setattr(svc, "_try_vision_parse",
                        lambda data: {"success": True, "result": {"md_content": "视觉读出来的"}},
                        raising=False)
    got = svc.parse_document(b"BLANK", "image", source="auto")
    assert got["provider"] == "vision" and got["fallback_used"] is True, got


def test_a_broken_engine_falls_back_to_vision_and_says_why(svc, monkeypatch):
    """引擎抛异常要落到视觉，而不是把整次上传带崩 —— 但原因得留在响应里。"""
    _patch_engine(monkeypatch, boom=RuntimeError("paddleocr 未安装"))
    monkeypatch.setattr(svc, "_try_vision_parse", lambda data: {"success": False}, raising=False)
    got = svc.parse_document(b"PNG", "image", source="auto")
    assert got["success"] is False and "paddleocr" in got["error"], got


def test_extract_is_built_on_extract_text_for_every_engine():
    """三个引擎各自只实现"读字"，答题卡的结构化（学号/姓名/作答）由基类共享一份。"""
    import inspect

    from app.services.ocr_mineru import BaiduOCRProvider, MinerUProvider, PaddleOCRProvider

    for cls in (PaddleOCRProvider, MinerUProvider, BaiduOCRProvider):
        assert "def extract_text" in inspect.getsource(cls), cls.__name__
        assert "def extract(" not in inspect.getsource(cls), \
            f"{cls.__name__} 自己又写了一遍答题卡解析，两份判据迟早分叉"


def test_sheet_structuring_survives_the_unification(monkeypatch, tmp_path):
    """`extract()`（答题卡批处理用）改成建立在 `extract_text` 之上，结构化结果不能变。"""
    from app.services.ocr_mineru import PaddleOCRProvider

    prov = PaddleOCRProvider()
    card = "学号：202610101 姓名：张三\n1. A\n2. 钠与水反应"
    monkeypatch.setattr(prov, "extract_text", lambda data, suffix=".png": card)
    p = tmp_path / "card.png"
    p.write_bytes(b"PNG")
    got = prov.extract(str(p))
    assert got["student_id"] == "202610101", got
    assert [a["q_number"] for a in got["answers"]] == [1, 2], got


@pytest.mark.asyncio
async def test_uploading_a_picture_does_not_freeze_the_event_loop(monkeypatch):
    """/parse/document 是 async 端点，里面跑的却是同步的本地 OCR（单张实测 7~12s CPU）。

    不把这一步丢进线程，一次上传就把整个服务按住，这期间连健康检查都不响应。以前这条链一进
    去就抛异常（同步路径没 await 那个 async 方法），所以这个坑从来没被踩到 —— 是修好它之后
    才轮得到它。
    """
    import asyncio
    import base64
    import time

    from app.api import ocr as ocr_api
    from app.services import document_parse_service as dps

    def _slow(self, file_data, file_type="auto", source="auto"):
        time.sleep(0.3)          # 同步阻塞，模拟 paddle 读一张图
        return {"success": True, "provider": "ocr:paddle",
                "result": {"md_content": "学号：202610101"}, "fallback_used": False}

    monkeypatch.setattr(dps.DocumentParseService, "parse_document", _slow)
    monkeypatch.setattr(dps.DocumentParseService, "check_services_status",
                        lambda self: {"ocr": {}}, raising=False)

    ticks = []

    async def _ticker():
        while True:
            await asyncio.sleep(0.02)
            ticks.append(1)

    task = asyncio.create_task(_ticker())
    req = ocr_api.DocumentParseRequest(file_data=base64.b64encode(b"PNG").decode(),
                                       file_type="image", source="auto")
    await ocr_api.parse_document(req)
    during = len(ticks)
    task.cancel()

    assert during >= 3, f"解析期间事件循环只跳了 {during} 下 —— 同步 OCR 把整个服务按住了"


def test_the_ring_denominator_is_a_budget_not_the_capacity(monkeypatch):
    """圆环分母：真实容量查表，`.env` 用 `CONTEXT_WINDOW` 收紧成预算。

    换到 flash 后容量是 1M，圆环于是变成一种装饰：几千 token 永远显示 0.4%，预警条从来
    不亮。`CONTEXT_WINDOW` 一个键所有人都在用 —— 这里是把它钉成"分母真的跟着这个键走"。
    """
    table = type(config).MODEL_CONTEXT_WINDOWS
    assert table["deepseek-flash"] == 1_000_000, "表里的数字改成预算了？注释说的是 1M 不是预算"
    assert table["deepseek-chat"] == 128_000

    # 这个键必须真的用上。上一版在这里重算了一遍同样的 `or` 式子，把 config.py 那行删掉
    # 测试照样绿，等于什么都没钉。`CONTEXT_WINDOW` 是 import 期算出来的类属性，只能重载模块；
    # 显式写进 os.environ 的值不会被 `.env` 盖掉（`load_dotenv` 不覆盖已存在的键），
    # 所以 "0" 就是"这一台没设预算，按容量算"。
    original = dict(os.environ)
    import app.config as cfg_module
    try:
        os.environ["CONTEXT_WINDOW"] = "12345"
        importlib.reload(cfg_module)
        assert cfg_module.config.CONTEXT_WINDOW == 12_345, "这个键没进到分母里，圆环仍然按容量画"

        os.environ["CONTEXT_WINDOW"] = "0"
        importlib.reload(cfg_module)
        expected = table[cfg_module.config.DEEPSEEK_MODEL]
        assert cfg_module.config.CONTEXT_WINDOW == expected, "没有预算时该退回表里的容量"
    finally:
        os.environ.clear()
        os.environ.update(original)
        importlib.reload(cfg_module)


def _suffix_seen(monkeypatch, cls, file_type):
    """让注册表挑到 `cls`，然后用给定 file_type 走一次解析，返回引擎看到的后缀。"""
    import app.services.ocr_mineru as reg

    seen = {}

    class _Spy(cls):
        def extract_text(self, image_bytes, suffix=".png"):
            seen["suffix"] = suffix
            seen["bytes"] = image_bytes
            return "读出来的字" * 4

    # 三家都换成同一个 Spy：这条测的是"后缀传对了没有"，不是各家的解码实现
    monkeypatch.setattr(config, "OCR_SHEET_PROVIDER", "paddle", raising=False)
    monkeypatch.setattr(reg, "_providers", {"paddle": _Spy, "mineru": _Spy, "baidu": _Spy},
                        raising=False)
    svc = DocumentParseService()
    got = svc.parse_document(b"BYTES", file_type, source="ocr")
    assert got["success"] is True, got
    return seen


def test_a_pdf_upload_is_not_handed_to_the_engine_as_a_png(svc, monkeypatch):
    """`{"file_type": "pdf", "source": "ocr"}` 以前会被写成 .png 的临时文件。

    引擎按后缀挑解码器，于是 PDF 被当图片解，读回来一片空 —— 而这条路径的入口就是老师那个
    "上传试卷"的按钮。
    """
    from app.services.ocr_mineru import PaddleOCRProvider

    assert _suffix_seen(monkeypatch, PaddleOCRProvider, "pdf")["suffix"] == ".pdf"
    assert _suffix_seen(monkeypatch, PaddleOCRProvider, "image")["suffix"] == ".png"


def test_the_registry_keeps_the_original_suffix_of_a_batch_sheet(tmp_path, monkeypatch):
    """`extract(image_path)` 也不能把后缀丢了：答题卡批处理收上来的"图片"里就有 PDF。"""
    import app.services.ocr_mineru as reg

    seen = {}

    class _Spy(reg.BaseOCRProvider):
        def extract_text(self, image_bytes, suffix=".png"):
            seen["suffix"] = suffix
            return "学号：202610101 姓名：张三"

    p = tmp_path / "sheet.pdf"
    p.write_bytes(b"%PDF-1.4")
    assert _Spy().extract(str(p))["student_id"] == "202610101"
    assert seen["suffix"] == ".pdf", "上传的是什么就得以什么去解"


def test_baidu_says_what_to_change_instead_of_mimicking_an_image_error(monkeypatch):
    """百度手写 OCR 只收图片。让 PDF 一路混到百度那里，老师拿到的是一句 `image format error`。"""
    from app.services.ocr_mineru import BaiduOCRProvider

    called = []
    monkeypatch.setattr(BaiduOCRProvider, "_baidu_handwriting",
                        lambda self, data: called.append(1) or "", raising=False)
    with pytest.raises(ValueError, match="OCR_SHEET_PROVIDER"):
        BaiduOCRProvider().extract_text(b"%PDF-1.4", suffix=".pdf")
    assert called == [], "该在出门前就拦下来，而不是发一个注定失败的请求"


def test_a_vision_success_still_fills_the_one_text_field_the_page_reads(monkeypatch):
    """视觉那条给的是 `raw_text`，而这个服务对外只有一个正文字段：`md_content`。

    不补这一下，OCR 没读出来、视觉成功的上传在老师那边就是一张白页。
    """
    svc = DocumentParseService()
    _patch_engine(monkeypatch, boom=RuntimeError("paddleocr 未安装"))
    monkeypatch.setattr(svc.llm_service, "analyze_paper_with_vision",
                        lambda image_data, paper_type="mixed": {
                            "success": True, "raw_text": "1．下列说法正确的是", "questions": []},
                        raising=False)
    got = svc.parse_document(b"PNG", "image", source="auto")
    assert got["provider"] == "vision" and got["fallback_used"] is True, got
    assert got["result"]["md_content"] == "1．下列说法正确的是", got["result"]


def test_a_missing_tape_is_not_relabelled_as_a_missing_vision_model(monkeypatch):
    """磁带 miss 是"你没带对磁带"，不是"这台没配视觉模型"（ADR-0023 决策 6/16）。"""
    from app.services.llm_transport import CassetteMiss

    svc = DocumentParseService()
    _patch_engine(monkeypatch, text="   ")
    monkeypatch.setattr(svc.llm_service, "analyze_paper_with_vision",
                        lambda **kw: (_ for _ in ()).throw(CassetteMiss("cassette miss: 键 x")),
                        raising=False)
    with pytest.raises(CassetteMiss):
        svc.parse_document(b"PNG", "image", source="auto")


def test_the_agent_import_tool_uses_the_same_parse_path(monkeypatch):
    """`import_question_bank` 以前自己抄了一份"OCR→视觉"，还漏了 await —— 每次必抛。"""
    import inspect

    from chem_skills.chemistry_parser.handler import ParserHandler

    src = inspect.getsource(ParserHandler._parse_image_for_questions)
    assert "self.ocr_service" not in src and "recognize_answer_sheet(" not in src
    assert "parse_document" in src, "又分叉出第二份解析流程了"


def test_a_successful_ocr_does_not_warn_every_time(monkeypatch):
    """每次成功都附一句"请确认"，等于这句话从来没说过任何事。"""
    svc = DocumentParseService()
    _patch_engine(monkeypatch, text="学号：202610101 姓名：张三\n1. 钠与水反应的离子方程式")
    got = svc.parse_document(b"PNG", "image", source="auto")
    assert got["success"] is True and "warning" not in got, got


def test_a_handful_of_stray_characters_is_not_treated_as_a_read(monkeypatch):
    """paddle 对糊图也会偶尔认出一个"1"。按"非空即成功"判，视觉兜底就永远不会触发。"""
    svc = DocumentParseService()
    _patch_engine(monkeypatch, text="1. A")
    monkeypatch.setattr(svc, "_try_vision_parse",
                        lambda data: {"success": True,
                                      "result": {"md_content": "视觉读出来的题干"}},
                        raising=False)
    got = svc.parse_document(b"PNG", "image", source="auto")
    assert got["provider"] == "vision" and got["fallback_used"] is True, got
    assert "非空白字符" in got["original_ocr_error"], got
