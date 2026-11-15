# ADR-0025: 老师上传的文档解析并入答题卡那张 OCR 注册表

**Date**: 2026-03-27
**Status**: Accepted（用户裁定：「这个应该统一一下解决路径吧」）

## Context

改动前有两张 OCR 通路，各自打不同的引擎，互不知情：

- 答题卡批处理：`app/services/ocr_mineru.py` 的注册表（paddle / mineru / baidu），默认本地
  PP-OCRv6，不需要任何密钥（ADR-0004）。
- 老师上传文档/图片：`app/services/document_parse_service.py` 的 `_parse_image` 绕过注册表，
  直接调 `OCRService.recognize_answer_sheet` 打百度教育接口 —— 没配 `BAIDU_OCR_*` 就等于这台
  机器"没有 OCR"，尽管批处理那条一直好用。

本机 2026-03-27 实测，这条上传链坏得更彻底，而且是三层叠在一起：

1. `recognize_answer_sheet` 是 `async`，而同步的 `parse_document` 路径没 await 它 —— 任何一次
   图片上传都抛 `AttributeError: 'coroutine' object has no attribute 'get'`。**也就是说"图片解析
   只能靠百度"这个前提从来没被验证过：它一次都没成功跑到百度那一步。**
2. 端点 `POST /api/ocr/parse/document` 返回的是 `{success, provider, result, ...}`，而唯一的
   调用方 `frontend/src/pages/teacher/ExamPage.tsx` 读的是顶层 `d.text || d.content` —— 那两个
   字段后端从来没返回过。就算第 1 层修好，老师看到的还是"解析完成，未提取到文本"。
3. 端点是 `async def`，里面跑的却是同步 CPU 活（本地 paddle 单张实测 7~12s）。以前第 1 层秒抛，
   没人碰到这件事；修好之后它变成"一次上传把整个事件循环按住十几秒"。

同一件"漏 await"的事在别处还有六处，是一条 AST 扫描就能抓出来的形状：`app/api/ocr.py` 的
`/recognize`、`/recognize/base64`、批量识别、`/preview`，`app/api/question.py` 的
`import_questions_from_ocr`，以及 `chem_skills/chemistry_parser/handler.py` 的
`_parse_image_for_questions` —— 全都在 `async def`（或同步调用异步）里写
`result = ocr_service.recognize_answer_sheet(...)`，下一句 `result.get("success")`。
这一次一并改掉：前五处补 `await`，第六处不再自己抄一份解析流程，改为调用本服务
（它那份副本连"OCR 成功"的判定都和主路不一致）。

留了一条 AST 绊线（`tests/test_unawaited_async_calls.py`，带正向对照）：`OCRService` 的 async
方法被**当结果用**就是错，交给 `create_task` / `gather` 调度不是。扫描根表覆盖
`app/ agent/ chem_skills/ evals/` —— 上一版只扫 `app/ agent/`，而第 6 处恰恰在 `chem_skills/`，
真题导入因此坏了很久都没人看见。

## Decision

1. **引擎层只实现一件事：`extract_text(image_bytes) -> str`**。三个后端各自实现它；"文本 →
   学号/姓名/作答"的结构化留在 `BaseOCRProvider.extract()` 里由三家共享一份。以前三家各抄一遍
   结构化 + 各写一遍 try/except，那是同一份判据的三个副本。
2. **文档解析走同一张注册表**，选哪个后端仍由 `OCR_SHEET_PROVIDER` 决定（默认 paddle）。
   于是"没配百度就没有 OCR"这句话不再是事实，`check_services_status` 也不需要为两条路各报
   一个引擎了。
3. **降级判据换成"读出来的文字够不够一条题"**：`MIN_OCR_CHARS = 20` 个非空白字符。旧代码要求
   `confidence >= 0.85` 才算成功，而本地 paddle 这条路没有可信的置信度数字 —— 假装有，结果要么
   永远走兜底、要么永远不走；但退化成"非空即成功"同样坏：paddle 对糊图也会偶尔认出一个 `1`，
   那样视觉兜底永远不会被触发。引擎抛异常时接住、落到视觉、并把原因随响应带回（`error`），
   不让一次坏图崩掉整次上传。
4. **正文一律放 `result.md_content`**（PDF/DOCX/PPTX 本来就这样），前端改成读这个字段，并把
   `success: false` 的原因显出来。契约只留一份。
5. **同步解析丢进 `asyncio.to_thread`**（决策 1 修好之后这条才成立）。
6. `ocr_document` 这个状态字段留着，但它的内容变成"同一个引擎 + 上传那条独有的视觉兜底"。
   两条路一旦又被报成两个引擎，就是分叉回来了 —— 测试盯着这一点。

## Consequences

- 不配任何百度密钥的机器现在能解析图片。本机实测：生成的答题卡 → `provider: "ocr:paddle"`、
  首张 12.4s 出文字（含模型加载）、之后 2.7s，`extract()` 走同一份文本还能抽办学号
  202610101 与两题作答。
- 上传解析第一次有了"服务是否被按住"的可执行判据：`test_uploading_a_picture_does_not_freeze_the_event_loop`
  在决策 5 缺失时报 0 次心跳。写这条测试的当场它就抓到了一个 `NameError`（`asyncio` 没 import），
  因为端点那个 `except Exception` 会把它变成一句 `解析异常: name 'asyncio' is not defined`。
- 命名债：`OCR_SHEET_PROVIDER` 里的 "SHEET" 现在名不副实 —— 它决定的是两条路用哪个 OCR 引擎。
  没改名：这个键写在 ADR-0004、openspec 与在役机器的 `.env` 里，改名会让已接受的记录对不上。
  下次真要动它时一起改。
- 没修的：`ocr_service.OCRService` 还在，仍被答题卡 HTTP 端点与 `ocr_scheduler` 的视觉复核用着；
  这次只把"图片 → 文字"这一件事收进注册表。它内部那 12 个百度 API 端点是另一条产品线
  （批改切题、题目识别），不在这条缝上。
- 百度仍然是可选后端（`OCR_SHEET_PROVIDER=baidu`），但不再是任何路径的必经项。
