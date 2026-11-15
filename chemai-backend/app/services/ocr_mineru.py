"""OCR Provider —— 三个后端（paddle / mineru / baidu）一条注册表。

两条链都从这里要文字：

    provider = get_ocr_provider()
    text = provider.extract_text(image_bytes, suffix=".pdf")   # 老师上传的文档/图片解析
    result = provider.extract(image_path)                      # 答题卡批处理（含结构化）

`extract()` 返回 {"student_id", "student_name", "answers", "raw_text"}（答题卡语义），
它建立在 `extract_text()` 之上 —— 每个引擎只需要实现"把这张图里的字读出来"这一件事。

之所以要有这条共用层：老师上传那条以前绕过注册表直接打百度教育接口，于是"没配 BAIDU_OCR_*
就没有 OCR"，而答题卡那条用本地 paddle 一直好好的。（它还漏 await 了那个 async 方法，
所以那条链当时是每次必抛 `'coroutine' object has no attribute 'get'`，见 ADR-0025。）
"""
import contextlib
import os
import re
import tempfile
from pathlib import Path

from app.config import config

# 图片类扩展名：只有图片能喂给"手写文字识别"那一类接口；PDF 一类的输入要么交给按后缀挑
# 解析器的本地引擎，要么在这里说清该换谁，而不是让它伪装成一张 .png
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


@contextlib.contextmanager
def _as_temp_file(data: bytes, suffix: str):
    """给只认文件路径的引擎一个带正确后缀的临时文件（后缀决定它按哪种格式解）。"""
    with tempfile.NamedTemporaryFile(suffix=suffix or ".png", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── 答题卡文本结构化提取（工单 002，原型 .scratch/ocr-grading-fix/prototype/answer_extraction.py）──
# 学号/姓名：容忍全角/半角冒号与空格，支持 "学号202610101姓名 张三" 连写
_ID_RE = re.compile(r"学号[：:\s]*(\d{6,12})")
# 姓名：容忍全角/半角冒号与空格；懒惰匹配 + 边界前瞻，防止吞掉紧随的学号/班级/考号标签
# （楷体实测 "姓名：张伟学号：2026101035" 被贪婪匹配成 张伟学号，故不用 [\u4e00-\u9fff]{2,4} 裸贪婪）
_NAME_RE = re.compile(r"姓名[：:\s]*([\u4e00-\u9fff]{2,4}?)(?=学|班|考|[：:\s]|$)")
# 兜底：通用 8-10 位纯数字（含 0/O 混淆时匹配失败 → 留空待 LLM/老师确认）
_ID_GENERIC_RE = re.compile(r"(?<!\d)(\d{8,10})(?!\d)")
# 表头行：含学号/姓名/班级/考号/学校等标签的行，作答提取时跳过
# （楷体实测 "二、非选择题（请写出答案）" 粘到 q8 作答上，须整行跳过 → 补小节标题/提示词）
_HEADER_RE = re.compile(r"学号|姓名|班级|考号|学校|考场|座位|选择题|非选择题|答题卡|注意事项|请在|作答")
# 题号识别双规则：锚点（数字+分隔符，含全角）最可靠；choice 连写（数字紧跟 A-E）挡 2H2/O2 化学式
_ITEM_ANCHOR_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*[\.\、\)）．]")
_ITEM_CHOICE_RE = re.compile(r"(?<![\d.])(\d{1,2})(?=[A-Ea-e]{1,4}(?![A-Za-z0-9]))")


def _is_choice(raw: str) -> bool:
    a = raw.strip().upper()
    return bool(a) and len(a) <= 4 and all(ch in "ABCDEFGH" for ch in a)


class BaseOCRProvider:
    """一个 OCR 后端。子类只需要实现 `extract_text`（把这张图里的字读出来）。"""

    # 空文本时给老师看的话，各家引擎自己报（"未识别到文本"要能看出是谁没识别到）
    ENGINE_LABEL = "OCR"

    def extract_text(self, image_bytes: bytes, suffix: str = ".png") -> str:
        """把这张图（或这一页 PDF）里的字读出来。

        `suffix` 是给 MinerU 这类只吃文件路径的引擎用的：答题卡批处理收上来的"图片"可能是
        PDF（`/ocr/sheets` 保留上传时的扩展名，聊天页也接受 .pdf），扩展名一丢，引擎就按错的
        类型去解，读回来的是一片空白。
        """
        raise NotImplementedError

    def extract(self, image_path: str) -> dict:
        """答题卡语义：读文字 → 抽学号/姓名 → 按作答切题。

        异常一律吞成 `{"error": ...}` 是调度器要的：它跑在线程池里，一张卡失败不能把整批
        带走。`extract_text` 本身不吞 —— 文档解析那条链需要知道是引擎坏了还是图是空的。
        """
        try:
            with open(image_path, "rb") as f:
                text = self.extract_text(f.read(), suffix=Path(image_path).suffix or ".png")
            if not text.strip():
                return {
                    "student_id": "", "student_name": "", "answers": [],
                    "raw_text": "", "error": f"{self.ENGINE_LABEL} 未识别到文本",
                }
            sid, name = self._extract_student(text)
            return {
                "student_id": sid,
                "student_name": name,
                "answers": self._extract_answers(text),
                "raw_text": text[:2000],
            }
        except Exception as e:
            return {
                "student_id": "", "student_name": "", "answers": [],
                "raw_text": "", "error": str(e),
            }

    def _extract_student(self, text: str) -> tuple:
        """从 OCR 文本提取学号/姓名（三个 provider 共享的一份，工单 002 验证口径）。"""
        sid, name = "", ""
        # 容忍全角/半角冒号与空格（[：:\s]*），支持 "学号202610101姓名 张三" 连写
        id_match = re.search(_ID_RE, text)
        if id_match:
            sid = id_match.group(1)
        name_match = re.search(_NAME_RE, text)
        if name_match:
            name = name_match.group(1)
        # Fallback: scan for generic id pattern（8-10 位纯数字，含 0/O 混淆时匹配失败 → 留空待 LLM/老师确认）
        if not sid:
            id_generic = re.search(_ID_GENERIC_RE, text)
            if id_generic:
                sid = id_generic.group(1)
        return sid, name

    def _extract_answers(self, text: str) -> list:
        # 跳过含学号/姓名/班级/考号等标签的表头行，其余行拼接为答案区
        body_lines = [ln for ln in text.splitlines() if not _HEADER_RE.search(ln)]
        body = "\n".join(body_lines)
        # 题号识别：锚点规则（数字+分隔符，含全角）+ choice 连写规则（数字紧跟 A-E 字母）
        matches = sorted(
            list(_ITEM_ANCHOR_RE.finditer(body)) + list(_ITEM_CHOICE_RE.finditer(body)),
            key=lambda m: m.start(),
        )
        answers = []
        for i, m in enumerate(matches):
            q_num = int(m.group(1))
            end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
            raw = body[m.end():end].strip()
            if not raw:
                continue
            if _is_choice(raw):
                answers.append({"q_number": q_num, "type": "choice", "answer": raw.upper(), "confidence": 0.7})
            else:
                answers.append({"q_number": q_num, "type": "free", "answer": raw, "confidence": 0.5})
        return answers


# ── PP-OCRv6_small（onnxruntime）——答题卡唯一 OCR 引擎（ADR-0004）──
_paddle_ocr = None


def _get_paddle_ocr():
    """模块级单例，避免每任务重载模型（模型下载缓存在 .paddlex/official_models）。"""
    global _paddle_ocr
    if _paddle_ocr is None:
        from paddleocr import PaddleOCR
        _paddle_ocr = PaddleOCR(
            text_detection_model_name="PP-OCRv6_small_det",
            text_recognition_model_name="PP-OCRv6_small_rec",
            engine="onnxruntime",
        )
    return _paddle_ocr


def _group_lines(boxes, texts, y_tol=12):
    """box=[xmin,ymin,xmax,ymax]；按 y 中心聚行、行内按 x 排序，拼成文本行（原型 samples/test_ppocrv6.py）。"""
    items = []
    for box, text in zip(boxes, texts):
        xmin, ymin, xmax, ymax = float(box[0]), float(box[1]), float(box[2]), float(box[3])
        items.append(((ymin + ymax) / 2, (xmin + xmax) / 2, text))
    items.sort(key=lambda t: (t[0], t[1]))
    lines, cur, last_y = [], [], None
    for cy, cx, text in items:
        if last_y is None or abs(cy - last_y) <= y_tol:
            cur.append((cx, text))
        else:
            lines.append([t for t in sorted(cur, key=lambda t: t[0])])
            cur = [(cx, text)]
        last_y = cy
    if cur:
        lines.append([t for t in sorted(cur, key=lambda t: t[0])])
    return ["".join(t[1] for t in line) for line in lines]


class PaddleOCRProvider(BaseOCRProvider):
    """PP-OCRv6_small (onnxruntime) — 免费/本地/开源，含手写训练，CPU 单张 ~7s。

    真实潦草手写准确率未在本机验证（用户无样本存档），低置信/漏检由 a4 视觉复核兜底。
    """

    ENGINE_LABEL = "PP-OCRv6"

    def extract_text(self, image_bytes: bytes, suffix: str = ".png") -> str:
        ocr = _get_paddle_ocr()
        # 走临时文件而不是自己解码：paddle 按后缀挑解码器，图片与 PDF 都能读，而这里不需要
        # 为此再拖一个 cv2 + numpy 的解码分支进来。
        with _as_temp_file(image_bytes, suffix) as path:
            res = ocr.predict(input=path)[0]
        return "\n".join(_group_lines(list(res["rec_boxes"]), list(res["rec_texts"])))


class MinerUProvider(BaseOCRProvider):
    """MinerU local OCR — development provider. Handwriting support is limited.

    真实手写答题卡实测（2026-08-08）：零文本，手写不在 MinerU 模型分布内。
    仅适合打印/印刷体；手写场景用 BaiduOCRProvider。
    """

    ENGINE_LABEL = "MinerU"

    # 有界子进程超时：防挂死 scheduler 的 worker 线程（封装默认 3600s 太长）。
    # 本机 CPU-only 实测 hybrid 单张 >10min 仍未完成，1800s 是"尽量跑完"与"不永久挂"的折中；
    # pipeline 计时实验/真实答题卡实测后可能回调（见工单「答题卡文本结构化提取」）。
    PARSE_TIMEOUT_SECONDS = 1800

    def extract_text(self, image_bytes: bytes, suffix: str = ".png") -> str:
        # 复用现有 MinerU 客户端封装（parse_by_cli 走 subprocess，进程隔离，可被 timeout 杀掉）。
        # 后缀交给 `_as_temp_file`：MinerU 按扩展名挑解析器，写死 .png 会把 PDF 当图片解，
        # 读回来是一片空 —— 而答题卡批处理收上来的"图片"里就有 PDF。
        from chem_skills.chemistry_parser.mineru_client import get_mineru_client

        with _as_temp_file(image_bytes, suffix) as tmp_path:
            result = get_mineru_client().parse_by_cli(
                file_path=tmp_path, lang="ch", timeout=self.PARSE_TIMEOUT_SECONDS)
        if not result.success:
            raise RuntimeError(result.error or "MinerU parse failed")
        return result.md_content or ""


class BaiduOCRProvider(BaseOCRProvider):
    """Baidu 手写 OCR — production provider（用户 2026-08-08 决策：手写场景接回百度，MinerU 只服务打印体）。

    scheduler 走 APScheduler 线程池，worker 线程无运行中的事件循环，
    故可在此同步方法内用 asyncio.run 调 async 的 get_token()。
    """

    # 百度「手写文字识别」API：面向中文手写，返回逐字/逐行 words_result
    HANDWRITING_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/handwriting"

    ENGINE_LABEL = "百度手写 OCR"

    def extract_text(self, image_bytes: bytes, suffix: str = ".png") -> str:
        if suffix.lower() not in _IMAGE_SUFFIXES:
            # 百度「手写文字识别」只收图片。以前 PDF 走到这里也是失败，但失败原因是百度回一句
            # `image format error` —— 换成本地引擎就能读的输入，不该被这一家绑住。
            raise ValueError(
                f"百度手写 OCR 只接受图片，收到 {suffix}：把 OCR_SHEET_PROVIDER 换成 paddle 或 mineru")
        return self._baidu_handwriting(image_bytes)

    def _baidu_handwriting(self, image_bytes: bytes) -> str:
        import asyncio
        import base64
        from urllib import parse

        import httpx

        from app.services.baidu_auth import get_token

        token = asyncio.run(get_token())
        body = parse.urlencode({
            "image": base64.b64encode(image_bytes).decode("utf-8"),
        }).encode("utf-8")
        resp = httpx.post(
            f"{self.HANDWRITING_URL}?access_token={token}",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error_code"):
            raise RuntimeError(f"百度 OCR {data['error_code']}: {data.get('error_msg')}")
        words_result = data.get("words_result", [])
        return "\n".join(item.get("words", "") for item in words_result)


_providers = {
    "paddle": PaddleOCRProvider,
    "mineru": MinerUProvider,
    "baidu": BaiduOCRProvider,
}


def _sheet_provider_name() -> str:
    """OCR 引擎选择：运行时开关热读（runtime-settings），env 值为种子默认。

    开关服务不可用（表未迁移/进程外场景）时回落 env 配置 —— 行为与改造前一致。
    """
    try:
        from app.services.runtime_settings import get_setting
        return str(get_setting("OCR_SHEET_PROVIDER"))
    except Exception:
        return config.OCR_SHEET_PROVIDER


def get_ocr_provider() -> BaseOCRProvider:
    provider_cls = _providers.get(_sheet_provider_name(), MinerUProvider)
    return provider_cls()


def resolve_sheet_provider_name() -> str:
    """`get_ocr_provider()` 实际会挑中的那个后端名 —— 认不出的值回退 mineru。

    健康检查要按这个名字判可用性，不能各数各的：两处对"默认是谁"的理解一旦分叉，
    检查就会对一个正常工作的系统报"缺配置"（`document_parse_service` 就干过这件事）。
    """
    name = _sheet_provider_name()
    return name if name in _providers else "mineru"
