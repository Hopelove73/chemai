# ADR-0002: 百度教育 OCR 管线

**Date**: 2025-12-25
**Status**: Accepted

## 背景

ChemAI 的 OCR 由智谱 GLM-OCR 提供能力，`OCRService.recognize_answer_sheet()` 一次 fire-and-forget 调用即结束。迁移到百度教育 OCR 套件后，引入了三个能力、调用模式与失败形态各异的 API。现有架构没有多步工作流、异步轮询、状态持久化或 API 组合的概念。

## 决策

### 1. 三 API 架构

| API | 角色 | 模式 | 端点 |
|-----|------|------|------|
| `doc_analysis` | 预览 + 公式提取 + 兜底 | 同步 | `/rest/2.0/ocr/v1/doc_analysis` |
| `paper_cut_edu_vlm` | 试卷录题（主通道） | 异步 | `/rest/2.0/ocr/v1/paper_cut_edu_vlm/create_task` + `/get_result` |
| `correct_edu` | 答题卡批改（主通道） | 异步 | `/rest/2.0/ocr/v1/correct_edu/create_task` + `/get_result` |

### 2. 上传会话状态机（SQLite）

所有上传文件在 `upload_sessions` 表中跟踪，状态机：

```
UPLOADED → PREVIEWING → READY → IMPORTING → IMPORTED → DONE
                            ↘ GRADING   → GRADED   → DONE
                            ↘ DISCARDED
任意状态 → ERROR（可重试）
```

用户必须**显式选择**一个动作，系统才会调用昂贵异步 API。

### 3. 客户端轮询

异步操作立即返回 `task_id`。前端每 2 秒轮询 `GET /api/ocr/tasks/{upload_id}/status`，服务端每次轮询时代理请求百度的 `get_result`。这给前端真实的进度指示（如「正在处理第 3/22 页」），也避免长连接挂死。

### 4. API 组合与降级

| 用户动作 | 主 API | 成功时 | 失败时 |
|------------|-------------|-----------|------------|
| 录入题库 | `paper_cut_edu_vlm` | 结构化题目存 JSON + 落库 | 降级 `doc_analysis` → LLM 结构化，标记「质量降级」 |
| 批改答题卡 | `correct_edu` | 结果存 `student_submissions` | 直接报错，不自动降级（批改必须保准确率） |
| 预览 | `doc_analysis` | 展示文本 + `formula_result` | 展示原图 + 「无法识别」 |

预览步骤产出的 `formula_result`（LaTeX 化学式）作为提示传入录题结构化环节的 LLM，提升准确率。

### 5. PDF 多页策略

每批 2 页并发（遵守百度 `create_task` QPS=2 限制），一批完成后起下一批。22 页试卷约 110 秒。进度经任务状态端点回报前端。

### 6. 文件生命周期

```
上传   → /tmp/uploads/{upload_id}.{ext}
预览   → 读临时文件，调 doc_analysis
录题   → 读临时文件，调 paper_cut_edu_vlm
成功   → 移入 data/exam_questions/{region}/{year}/
         同步下载百度裁剪/增强图到同目录
失败   → 保留在 /tmp 供重试
放弃   → 24 小时后清理（定时任务）
```

录入的试卷连同配图存储：

```
data/exam_questions/{region}/{year}/
  ├── {paper_name}.json
  └── figures/
      ├── page_01.png
      ├── page_01_enhanced.png
      ├── q1_crop.png
      └── ...
```

### 7. 存储：学生提交

新增 `student_submissions` 表，把批改后的答题卡关联到考试与班级：

```
student_submissions
  submission_id   主键
  exam_id         外键 → exam_records
  class_id        外键 → classes
  student_name    字符串
  original_image  原始答题卡路径
  graded_image    批注图路径
  answers_json    [{q_number, student_answer, correct_result, reason, score}]
  total_score     int
  submitted_at    datetime
  graded_at       datetime
```

前端导航：班级 → 考试列表 → 单场考试 → 学生提交列表 → 单份提交详情（原图 + 批注图 + 逐题拆解）。

## 已考虑的备选方案

### 内存态（vs SQLite）
否决。教师可能中途关浏览器，SQLite 能在重启后存活。

### 服务端轮询（vs 客户端轮询）
否决。会阻塞 FastAPI worker 或需要后台任务基建。客户端轮询更简单，且给前端真实的进度可见性。

### 串行处理页（vs 每批 2 页并发）
否决。多页 PDF 太慢；完全并行又撞百度 QPS=2 限制。

### 仅 VLM 录题（vs VLM + doc_analysis 公式合并）
否决。doc_analysis 产出的 LaTeX 公式比 VLM 纯文本更精确，两者合并能提升下游出题质量。

## 后果

- **正面**：多步工作流带用户在环确认，避免在错误输入上烧昂贵 API 调用。
- **正面**：同步 OCR 兜底保证 API 失败时系统绝不返回空结果。
- **正面**：客户端轮询给长耗时操作真实的进度反馈。
- **负面**：三个 API、两种异步模式，实现复杂度高于之前的单次同步调用。
- **负面**：SQLite 状态机新增一张表与一步迁移。
