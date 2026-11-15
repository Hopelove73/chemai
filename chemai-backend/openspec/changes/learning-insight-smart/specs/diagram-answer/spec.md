# Diagram Answer

实验装置图作答（X2 最小闭环）：画布作答 → 附件落库 → 教师人工判卷。

## ADDED Requirements

### REQ-DGM-001: Canvas Input
实验题可用画布作答。

**Acceptance:**
- ChemComposer 扩展三 tab 输入形态：文本 / 公式 / 画布（按题意可组合提交）
- Excalidraw（MIT）懒加载（进入画布 tab 才 import，不进主包）；430px 与桌面 WebView2 均可用
- 器材元件库 v1 入 `frontend/src/assets/chem-lab-library.excalidrawlib`（酒精灯/试管/烧杯/锥形瓶/铁架台/导管/集气瓶/量筒/天平/酒精喷灯/水槽/玻璃棒等 12–15 件）

### REQ-DGM-002: Attachment Landing
作答图落库（Data Landing）。

**Acceptance:**
- PNG 存 `data/answer_attachments/{student_id}/{practice_id}/{question_id}.png`；`student_answers.attachments`（JSON 路径数组）随提交写入
- 提交后刷新不丢图；教师判卷中心按附件引用可见原图

### REQ-DGM-003: Teacher-first Grading
一期只存不自动判。

**Acceptance:**
- 装置图题走 Grading Center 既有 `PENDING_SEMANTIC` 行 + 附件引用；教师人工判定后回流学生成绩页（与成绩确认门语义一致）
- 多模态 LLM 视觉判留二期接口位（附件含图片路径，升级不迁移数据）
- 排除重申：tldraw / ChemDoodle / Ketcher 移动端不引入
