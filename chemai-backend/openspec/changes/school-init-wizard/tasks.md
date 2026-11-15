# Tasks: school-init-wizard

## 1. 向导骨架与步骤裁剪

- [x] 1.1 `AdminSchoolPage` 扩展为向导结构：四步（学校信息/名册/任教/成绩）+ 步骤条，按 `can("school:update")`/`can("class:create")`/`can("teacher:create")`/`can("exam:create")` 显隐，verify：vitest（admin 四步全见 / academic_admin 无任教步 / 无能力步骤不阻塞其他步）+ `npm run lint:tokens`

## 2. 解析与预览

- [x] 2.1 轻量 CSV 解析（引号/转义/换行处理 + BOM/分隔符探测 + 手动切换分隔符）；上传区按 P-12 upload-zone 拖拽虚线框样式，verify：vitest（引号内逗号/换行、GBK BOM、分号分隔三种样本）
- [x] 2.2 预览校验：行数/列映射（请求模型字段中文名提示）/缺列与空必填标示/坏行剔除开关（默认整批阻断），verify：vitest（坏行标示、剔除后提交行数正确、缺列阻断）

## 3. 提交与结果

- [x] 3.1 三步导入提交（JSON rows 同形载荷；名册分批提交汇总摘要；任教/成绩直提），verify：vitest（分批计数汇总）+ 手动一批真实名册导入与 StudentsPage 数据一致
- [x] 3.2 结果摘要渲染端点返回的新增/跳过/失败明细（跳过标注「已存在」），verify：vitest + 手动重复导入验证幂等反馈
- [x] 3.3 学校信息步骤接 P0 表单：未初始化建校（POST）成功后解锁后续步骤，verify：vitest 状态流转 + 手动

## 4. 验收（集成检查）

- [x] 4.1 e2e 手动：新库建校 → 粘贴名册导入 → 任教导入（admin）→ 成绩导入 → ClassPage/DiagnosisPage 数据可见；academic_admin 走一遍验证步骤裁剪，verify：流程走通
- [x] 4.2 全量回归：`npm run build` + `npm run lint:tokens`、`python -m pytest tests/ -q`（导入端点零改动应全绿），verify：零新增失败 + 令牌守门零违规
