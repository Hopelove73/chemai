"""
统一文档解析服务
按选中的后端调度：答题卡 OCR（默认本地 PP-OCRv6，ADR-0004）、MinerU、视觉模型

策略:
- PDF文档 → MinerU优先
- 图片/试卷/答题卡 → `OCR_SHEET_PROVIDER` 指定的 OCR 后端，失败降级视觉模型
- 混合内容 → OCR + MinerU组合
"""
import os
import sys
import base64
import tempfile
from pathlib import Path
from typing import Dict, Any

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))



class DocumentParseService:
    """
    统一文档解析服务 - 自动选择OCR/MinerU/视觉模型
    """

    def __init__(self):
        from app.services.llm_service import LLMService

        self.llm_service = LLMService()
        self.mineru_client = self._try_init_mineru()

    def _try_init_mineru(self):
        """尝试初始化MinerU客户端"""
        try:
            from chem_skills.chemistry_parser.mineru_client import get_mineru_client
            return get_mineru_client()
        except Exception as e:
            print(f"[MinerU] Init failed: {e}")
            return None

    def _mineru_status(self) -> Dict[str, Any]:
        """MinerU 到底缺什么 —— 找不到 CLI 与模型没下载是两件事，以前都印成同一句。

        原来 `mineru_client is None`（没装 / 路径没找到）会一路走到那句
        "Models not downloaded"，于是给老师的答案永远是"去下载模型"，而真正要做的是装包。

        模型位置问 MinerU 自己的配置（`~/mineru.json` 里的 `models-dir`，或
        `MINERU_TOOLS_CONFIG_JSON` 指过去的那份），不在这里另数一遍 home 下的三个缓存目录 ——
        模型放哪块盘是机主的事（本机就在 D:），硬编码 home 的探测会把已经下好的模型判成
        "未下载"。这跟刚才答题卡 OCR 那个 bug 是同一个形状：判据在自己身上数了一份。
        """
        if self.mineru_client is None:
            return {"available": False,
                    "note": "未找到 MinerU：pip install mineru，或把源码检出目录写进 MINERU_PATH"}
        models_dir = self._configured_models_dir()
        if not models_dir:
            return {"available": False,
                    "note": "MinerU 模型未下载：mineru-models-download -s modelscope -m all"}
        return {"available": True, "note": f"MinerU 就绪，模型在 {models_dir}"}

    @staticmethod
    def _configured_models_dir() -> str:
        """`mineru.json` 记着、且真的存在且有内容的第一个模型目录；一个都没有就是没下模型。"""
        try:
            from mineru.utils.config_reader import get_local_models_dir
        except ImportError:
            return ""
        configured = get_local_models_dir()
        dirs = list(configured.values()) if isinstance(configured, dict) else [configured]
        for d in dirs:
            if d and os.path.isdir(str(d)) and any(os.scandir(str(d))):
                return str(d)
        return ""

    def check_services_status(self) -> Dict[str, Any]:
        """
        检测所有解析服务可用性
        """
        mineru = self._mineru_status()
        # 视觉后端的名单问 `LLMService.vision_backends()` —— 状态与调用链共用同一份。
        # 上一版这里自己数了一遍"智谱或小米有没有 key"，于是只有 DeepSeek 一台机器上
        # （deepseek-flash 本来就能看图）会告诉模型"未配置 Vision 模型"。
        backends = self.llm_service.vision_backends()
        names = "/".join(b["name"] for b in backends)

        return {
            "ocr": self._sheet_ocr_status(mineru),
            # 老师上传文档/图片这一条以前绕过注册表直接打百度教育接口，所以它得单独报；
            # 现在两条共用同一个 `OCR_SHEET_PROVIDER` 选中的后端，`ocr_document` 只多报一件事：
            # 读不出文本时有没有视觉可兜。
            "ocr_document": self._document_ocr_status(mineru),
            "mineru": mineru,
            "vision": {
                "available": bool(backends),
                "provider": names or "无",
                "note": f"多模态复核按此顺序可用：{names}" if backends else
                        "没有能看图的后端：把 DEEPSEEK_MODEL 设成多模态模型，或配小米/智谱 key",
            },
        }

    def _document_ocr_status(self, mineru: Dict[str, Any]) -> Dict[str, Any]:
        """老师上传文档/图片那条路的引擎与就绪度。

        它以前跟答题卡批处理**不是同一个**：那条按 `OCR_SHEET_PROVIDER` 走，这条写死打百度教育
        接口。分叉的害处是一句具体的谎 —— `ocr.available` 为真（本地 paddle 永远可用），而老师
        点解析其实没有 OCR。现在两条共用同一个注册表，判据就直接取 `_sheet_ocr_status` 那一份
        （一次状态检查只探一回 MinerU），这里只多报上传那条独有的东西：读不出文本时有没有视觉可兜。
        """
        engine = self._sheet_ocr_status(mineru)
        vision = [b["name"] for b in self.llm_service.vision_backends()]
        names = ([engine["provider"]] if engine["available"] else []) + vision
        if engine["available"]:
            note = f"文档解析用 {engine['provider']}" + (
                f"，读不出文本时视觉兜底：{'/'.join(vision)}" if vision
                else "，但没有能看图的视觉后端")
        elif vision:
            note = (f"{engine['note']}；这条目前只能靠视觉（{'/'.join(vision)}）读题")
        else:
            note = f"文档解析没有可用引擎：{engine['note']}，也没有能看图的视觉后端"
        return {"available": bool(names), "provider": "/".join(names) or "无", "note": note}

    def _sheet_ocr_status(self, mineru: Dict[str, Any]) -> Dict[str, Any]:
        """答题卡 OCR 那一路，按**真正被选中的那个后端**判（`resolve_sheet_provider_name`）。

        上一版写死了"看百度有没有 key"，而 ADR-0004 之后默认后端是本地 PP-OCRv6、不需要
        任何密钥。三把百度密钥都不配的正常机器上，这个函数于是回 `available: false` ——
        老师问"解析不了"，模型照实回答"OCR 后端没配，请申请百度密钥"。判据必须跟着
        `OCR_SHEET_PROVIDER` 走，每个后端按它自己的依赖判：paddle 看包在不在，baidu 看
        两把密钥，mineru 看模型下没下载。
        """
        from app.services.ocr_mineru import resolve_sheet_provider_name

        provider = resolve_sheet_provider_name()
        if provider == "baidu":
            ok = bool(os.getenv("BAIDU_OCR_API_KEY", "") and os.getenv("BAIDU_OCR_SECRET_KEY", ""))
            return {"available": ok, "provider": "baidu",
                    "note": "百度教育产品OCR" if ok
                            else "OCR_SHEET_PROVIDER=baidu，但 BAIDU_OCR_API_KEY/SECRET_KEY 没配"}
        if provider == "mineru":
            return {"provider": "mineru", **mineru}
        import importlib.util
        installed = importlib.util.find_spec("paddleocr") is not None
        return {"available": installed, "provider": "paddle",
                "note": "本地 PP-OCRv6（onnxruntime），不需要密钥"
                        if installed else "未安装 paddleocr 包：pip install paddleocr"}

    def parse_document(
        self,
        file_data: bytes,
        file_type: str,
        source: str = "auto"
    ) -> Dict[str, Any]:
        """
        自动选择解析方式

        Args:
            file_data: 文件内容(字节)
            file_type: 文件类型 ("pdf", "image", "auto")
            source: 解析源 ("ocr", "mineru", "vision", "auto")

        Returns:
            {
                "success": bool,
                "provider": str,  # "ocr", "vision", "mineru", "none"
                "result": dict,
                "fallback_used": bool,
                "error": str (如果有)
            }
        """
        # 判定文件类型
        if file_type == "auto":
            file_type = self._detect_file_type(file_data)

        # 根据source参数选择解析方式
        if source == "auto":
            if file_type == "pdf":
                return self._parse_pdf(file_data)
            elif file_type == "docx":
                return self._parse_docx(file_data)
            elif file_type == "pptx":
                return self._parse_pptx(file_data)
            else:
                return self._parse_image(file_data, file_type)
        elif source == "ocr":
            return self._parse_image_with_ocr(file_data, file_type)
        elif source == "mineru":
            if file_type == "docx":
                return self._parse_docx(file_data)
            elif file_type == "pptx":
                return self._parse_pptx(file_data)
            return self._parse_pdf(file_data)
        elif source == "vision":
            return self._parse_image_with_vision(file_data)
        else:
            return {
                "success": False,
                "provider": "none",
                "error": f"未知的解析源: {source}",
                "fallback_used": False
            }

    def _detect_file_type(self, file_data: bytes) -> str:
        """通过文件头检测文件类型"""
        # PDF 文件头: 25 50 44 46 (%PDF)
        if file_data[:4] == b'%PDF':
            return "pdf"

        # JPEG: FF D8 FF
        if file_data[:3] == b'\xFF\xD8\xFF':
            return "image"

        # PNG: 89 50 4E 47
        if file_data[:4] == b'\x89PNG':
            return "image"

        # GIF: 47 49 46 38
        if file_data[:4] == b'GIF8':
            return "image"

        # Word (.docx) / PPTX (.pptx) / Excel (.xlsx) 都是ZIP格式: 50 4B 03 04
        # 需要进一步检测子类型
        if file_data[:4] == b'PK\x03\x04':
            # 检测是否为DOCX (Word文档)
            # DOCX本质上是一个ZIP，包含 word/document.xml
            try:
                import zipfile
                import io
                with zipfile.ZipFile(io.BytesIO(file_data[:1024])) as zf:
                    names = zf.namelist()
                    if 'word/document.xml' in names:
                        return "docx"
                    elif 'ppt/presentation.xml' in names:
                        return "pptx"
                    elif 'xl/workbook.xml' in names:
                        return "xlsx"
            except Exception:
                pass

        # 默认按图片处理
        return "image"

    # 引擎读回 3 个字符也算"读出文本"的话，视觉兜底就永远不会触发：paddle 对糊图/空白页也
    # 偶尔认得出一个"1"。旧判据是 confidence >= 0.85，而本地引擎没有可信的置信度可报，
    # 于是换成一条看得见的下限 —— 宁可少给一次"成功"，也不给一次带不动后续流程的假成功。
    MIN_OCR_CHARS = 20

    def _ocr_usable(self, text: str):
        """这次 OCR 出来的文字够不够当作"读到了内容"。"""
        stripped = (text or "").strip()
        if len(stripped) >= self.MIN_OCR_CHARS:
            return True, None
        return False, (f"{self.MIN_OCR_CHARS} 个非空白字符都没读到（只有 {len(stripped)} 个），"
                       "按没读出文本处理")

    def _ocr_text(self, image_data: bytes, file_type: str = "image") -> Dict[str, Any]:
        """向 OCR provider 注册表要文字，哪个后端由 `OCR_SHEET_PROVIDER` 定（默认本地 paddle）。

        以前这里直接调 `OCRService.recognize_answer_sheet`，两件事一起坏：那是个 async 方法，
        而同步的 `parse_document` 路径没 await 它，于是每次上传都抛
        `'coroutine' object has no attribute 'get'`；而且它把百度写死成唯一引擎，没配
        `BAIDU_OCR_*` 的机器就等于"这台没有 OCR"，尽管答题卡那条用本地 paddle 一直好好的。

        `file_type` 只为一件事：把后缀交给注册表。引擎按后缀挑解码器，一律当图片递过去，
        `{"file_type": "pdf", "source": "ocr"}` 就会把 PDF 解成一片空白。
        """
        from app.services.ocr_mineru import get_ocr_provider, resolve_sheet_provider_name

        name = resolve_sheet_provider_name()
        try:
            text = get_ocr_provider().extract_text(
                image_data, suffix=".pdf" if file_type == "pdf" else ".png")
        except Exception as e:  # 引擎坏了要能落到视觉兜底，而不是把整次上传带崩
            return {"provider": name, "text": "", "error": f"{name} 读图失败：{e}"}
        usable, why = self._ocr_usable(text)
        return {"provider": name, "text": text if usable else "",
                "error": None if usable else why}

    def _ocr_result(self, ocr: Dict[str, Any]) -> Dict[str, Any]:
        """把 OCR 文本整成与 `_parse_pdf` 同一形状的内层 result。

        `md_content` 是这个服务对外的正文字段（PDF/DOCX/PPTX 都放这里）；老师上传页读的也是它。
        """
        return {"success": True, "md_content": ocr["text"], "provider": ocr["provider"]}

    def _parse_image(self, image_data: bytes, file_type: str = "image") -> Dict[str, Any]:
        """
        图片解析: OCR → 视觉模型降级

        流程:
        1. 走注册表拿文字（默认本地 PP-OCRv6，不要任何密钥）
        2. 读不出文本或引擎报错，才试视觉模型
        3. 两条都不行就把第一手的失败原因带回去
        """
        ocr = self._ocr_text(image_data, file_type)

        if ocr["text"].strip():
            return {
                "success": True,
                "provider": f"ocr:{ocr['provider']}",
                "result": self._ocr_result(ocr),
                "fallback_used": False,
            }

        # Step 2: OCR 没读出东西，尝试视觉模型
        vision_result = self._try_vision_parse(image_data)

        if vision_result.get("success"):
            return {
                "success": True,
                "provider": "vision",
                "result": vision_result.get("result"),
                "fallback_used": True,
                "original_ocr_error": ocr["error"],
            }

        # Step 3: 视觉也不可用 —— 说清楚是"引擎没读出来"还是"引擎本身坏了"，
        # 这两种情况老师要做的事不一样（换一张图 vs 去配后端）。
        return {
            "success": False,
            "provider": f"ocr:{ocr['provider']}",
            "result": {},
            "fallback_used": False,
            "error": ocr["error"],
            "warning": "OCR 与视觉模型都没能读出内容，可以在解析源里改选 vision 重试",
        }

    def _parse_image_with_ocr(self, image_data: bytes, file_type: str = "image") -> Dict[str, Any]:
        """仅使用OCR解析图片"""
        ocr = self._ocr_text(image_data, file_type)
        return {
            "success": bool(ocr["text"].strip()),
            "provider": f"ocr:{ocr['provider']}",
            "result": self._ocr_result(ocr) if ocr["text"].strip() else {},
            "fallback_used": False,
            "error": ocr["error"] if not ocr["text"].strip() else None,
        }

    def _with_md_content(self, inner: Any) -> Dict[str, Any]:
        """视觉那条产出的正文字段叫 `raw_text`，而本服务对外只有一个正文字段：`md_content`
        （PDF/DOCX/PPTX/OCR 都写它）。不补这一下，走视觉成功的上传在老师那边就是一张白页。
        """
        out = dict(inner or {})
        if not out.get("md_content"):
            out["md_content"] = out.get("raw_text") or ""
        return out

    def _try_vision_parse(self, image_data: bytes) -> Dict[str, Any]:
        """尝试使用视觉模型解析。

        磁带 miss 不放行：那是"没带对的磁带"，不是"这台没配视觉模型"。把它咽成
        `success: false` 会让一次离线重放看起来像模型坏了（ADR-0023 决策 6/16）。
        """
        from app.services.llm_transport import CassetteMiss

        try:
            b64_image = base64.b64encode(image_data).decode('utf-8')
            result = self.llm_service.analyze_paper_with_vision(
                image_data=b64_image,
                paper_type="mixed"
            )
        except CassetteMiss:
            raise
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": result.get("success", False),
                "result": self._with_md_content(result)}

    def _parse_image_with_vision(self, image_data: bytes) -> Dict[str, Any]:
        """仅使用视觉模型解析图片"""
        vision_result = self._try_vision_parse(image_data)
        return {
            "success": vision_result.get("success", False),
            "provider": "vision",
            "result": vision_result.get("result", {}),
            "fallback_used": False,
            "error": vision_result.get("error")
        }

    def _parse_pdf(self, file_data: bytes) -> Dict[str, Any]:
        """
        PDF解析: MinerU优先，不可用则原样带回它缺的那一样东西
        """
        mineru = self._mineru_status()
        if not mineru["available"]:
            return {
                "success": False,
                "provider": "none",
                # 原因来自 `_mineru_status`：没装包与没下模型要做的事不一样，这里不能猜一个
                "error": f"MinerU 不可用，无法解析 PDF：{mineru['note']}",
                "fallback_used": False,
                "mineru_available": False,
            }

        # 保存临时文件
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name

        try:
            result = self.mineru_client.parse_by_cli(
                file_path=tmp_path,
                lang="ch",
                backend="hybrid-auto-engine"
            )

            return {
                "success": result.success,
                "provider": "mineru",
                "result": {
                    "success": result.success,
                    "md_content": result.md_content,
                    "questions": result.questions or [],
                    "question_count": result.question_count,
                    "formulas": result.formulas or [],
                    "images": result.images or [],
                    "output_dir": result.output_dir,
                    "error": result.error,
                },
                "fallback_used": False,
                "error": result.error if not result.success else None,
            }
        except Exception as e:
            return {
                "success": False,
                "provider": "mineru",
                "error": f"MinerU解析失败: {str(e)}",
                "fallback_used": False
            }
        finally:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _parse_docx(self, file_data: bytes) -> Dict[str, Any]:
        """
        Word文档解析: MinerU

        支持 .docx 格式
        """
        if not self.mineru_client:
            return {
                "success": False,
                "provider": "none",
                "error": "MinerU不可用，无法解析Word文档",
                "fallback_used": False,
                "mineru_available": False
            }

        # 保存临时文件
        with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name

        try:
            result = self.mineru_client.parse_by_cli(
                file_path=tmp_path,
                lang="ch",
                backend="hybrid-auto-engine"
            )

            return {
                "success": result.success,
                "provider": "mineru",
                "result": {
                    "success": result.success,
                    "md_content": result.md_content,
                    "questions": result.questions or [],
                    "question_count": result.question_count,
                    "formulas": result.formulas or [],
                    "images": result.images or [],
                    "output_dir": result.output_dir,
                    "error": result.error
                },
                "fallback_used": False
            }
        except Exception as e:
            return {
                "success": False,
                "provider": "mineru",
                "error": f"MinerU解析失败: {str(e)}",
                "fallback_used": False
            }
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _parse_pptx(self, file_data: bytes) -> Dict[str, Any]:
        """
        PowerPoint文档解析: MinerU

        支持 .pptx 格式
        """
        if not self.mineru_client:
            return {
                "success": False,
                "provider": "none",
                "error": "MinerU不可用，无法解析PPT文档",
                "fallback_used": False,
                "mineru_available": False
            }

        # 保存临时文件
        with tempfile.NamedTemporaryFile(suffix='.pptx', delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name

        try:
            result = self.mineru_client.parse_by_cli(
                file_path=tmp_path,
                lang="ch",
                backend="hybrid-auto-engine"
            )

            return {
                "success": result.success,
                "provider": "mineru",
                "result": {
                    "success": result.success,
                    "md_content": result.md_content,
                    "questions": result.questions or [],
                    "question_count": result.question_count,
                    "formulas": result.formulas or [],
                    "images": result.images or [],
                    "output_dir": result.output_dir,
                    "error": result.error
                },
                "fallback_used": False
            }
        except Exception as e:
            return {
                "success": False,
                "provider": "mineru",
                "error": f"MinerU解析失败: {str(e)}",
                "fallback_used": False
            }
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def parse_image_chemical(self, image_data: bytes) -> Dict[str, Any]:
        """
        解析图片中的化学式和反应方程式

        使用MinerU的化学式提取能力
        """
        if not self.mineru_client:
            # 降级到OCR
            return self._parse_image_with_ocr(image_data)

        # 保存临时文件
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp.write(image_data)
            tmp_path = tmp.name

        try:
            result = self.mineru_client.parse_by_cli(
                file_path=tmp_path,
                lang="ch",
                backend="hybrid-auto-engine",
                formula_enable=True,
                table_enable=False
            )

            return {
                "success": result.success,
                "provider": "mineru",
                "formulas": result.formulas or [],
                "content": result.md_content,
                "questions": result.questions or []
            }
        except Exception as e:
            return {
                "success": False,
                "provider": "none",
                "error": str(e)
            }
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# 全局单例
_document_parse_service = None


def get_document_parse_service() -> DocumentParseService:
    """获取全局统一文档解析服务实例"""
    global _document_parse_service
    if _document_parse_service is None:
        _document_parse_service = DocumentParseService()
    return _document_parse_service


