"""
历年真题库管理服务
负责: 加载/查询/标注历年真题
"""
import json
import os
import re
from typing import List, Dict, Optional
from app.core.config import settings
from app.models.historical_exam import HistoricalQuestion, ExamPaper


class _SearchResult(list):
    """A list subclass carrying keyword_count metadata for the web search fallback."""
    keyword_count: int = 0


class ExamBankService:
    """
    历年真题库服务
    基于PRD: 覆盖全国卷2022-2024 + 湖南卷2022-2024
    """

    def __init__(self):
        self.questions: List[HistoricalQuestion] = []
        self.papers: Dict[str, ExamPaper] = {}
        self._load_exam_bank()

    def _load_exam_bank(self):
        """从 {region}/{year}/{paper_name}.json 目录结构加载真题"""
        exam_dir = settings.EXAM_QUESTIONS_PATH

        loaded = 0
        for entry in os.listdir(exam_dir):
            region_path = os.path.join(exam_dir, entry)
            if not os.path.isdir(region_path):
                continue
            # entry is the region name in Chinese: "全国卷", "湖南卷"
            # 剥离排序前缀（如 "01_全国卷" → "全国卷"），前缀仅用于目录置顶
            region = re.sub(r"^\d+_", "", entry)
            for year_str in os.listdir(region_path):
                year_path = os.path.join(region_path, year_str)
                if not os.path.isdir(year_path):
                    continue
                try:
                    year = int(year_str)
                except ValueError:
                    continue
                for f in os.listdir(year_path):
                    if not f.endswith(".json"):
                        continue
                    file_path = os.path.join(year_path, f)
                    self._load_from_file(file_path, region, year)
                    loaded += 1

        # 兼容旧 national/hunan 目录结构
        if loaded == 0:
            for d in ["national", "hunan"]:
                dp = os.path.join(exam_dir, d)
                if not os.path.isdir(dp):
                    continue
                region = "全国卷" if d == "national" else "湖南卷"
                for f in os.listdir(dp):
                    if not f.endswith(".json"):
                        continue
                    try:
                        year = int(f.replace(".json", ""))
                    except ValueError:
                        continue
                    self._load_from_file(os.path.join(dp, f), region, year)
                    loaded += 1

        print(f"[ExamBank] 从 {loaded} 个文件加载了 {len(self.questions)} 道真题")

    def _load_from_file(self, file_path: str, region: str, year: int):
        """从JSON文件加载"""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            questions = [HistoricalQuestion(**q) for q in data.get("questions", [])]
            self.questions.extend(questions)

            # 构建试卷索引
            paper_id = f"{region}{year}"
            self.papers[paper_id] = ExamPaper(
                paper_id=paper_id,
                source=f"{region}{year}",
                year=year,
                region=region,
                paper_name=data.get("paper_name", ""),
                subject="化学",
                total_score=data.get("total_score", 100),
                question_count=len(questions),
                questions=questions
            )

    def search_questions(
        self,
        source: Optional[str] = None,
        year: Optional[int] = None,
        knowledge_point: Optional[str] = None,
        difficulty: Optional[str] = None,
        keyword: Optional[str] = None,
        region: Optional[str] = None,
        limit: int = 1000,
        use_vector: bool = False,
    ) -> List[HistoricalQuestion]:
        """
        查询真题
        先用结构化过滤（关键词/知识点/年份），结果不足时用向量搜索扩展
        """
        results = list(self.questions)

        if source:
            results = [q for q in results if source in q.source]

        if region:
            results = [q for q in results if q.region == region]

        if year:
            results = [q for q in results if q.year == year]

        # Step 1: Keyword matching — knowledge_points only (content/answer too noisy)
        search_terms = []
        if knowledge_point:
            search_terms.append(knowledge_point.lower())
        if keyword:
            search_terms.append(keyword.lower())
            if len(keyword) > 2:
                import re
                _stop_words = {"题目", "真题", "高考", "一道", "搜索", "找一下", "有没有", "关于"}
                split = [t.strip() for t in re.split(r'[的与和、，,+\s]+', keyword) if len(t.strip()) >= 2]
                search_terms.extend([t for t in split if t not in _stop_words])

        if search_terms:
            results = [q for q in results
                      if any(any(t in (kp or "").lower() for kp in q.knowledge_points)
                             for t in search_terms)]

        if difficulty:
            results = [q for q in results if q.difficulty == difficulty]

        # Relevance count for web search trigger: only count questions where
        # the FULL keyword/phrase matches in knowledge_points (not split terms like "氧化")
        keyword_count = 0
        if keyword:
            kw_lower = keyword.lower()
            keyword_count = sum(1 for q in results
                              if kw_lower in q.content.lower()
                              or any(kw_lower in (kp or "").lower() for kp in q.knowledge_points))

        # Step 2: If keyword found too few, fill gaps via vector search
        #
        # 向量检索只是**补足关键词结果的缺口**，不能成为致命依赖：本机没有
        # EMBEDDING_CACHE_DIR / 连不上 huggingface 时 vector_search 会抛
        # "embedding 模型此前加载失败"，2026-10 实测这让「搜真题」「联网搜索」
        # 整轮对话直接 400 中止（关键词结果明明已经有了）。这里降级为纯关键词。
        if use_vector and keyword and len(results) < limit:
            from app.services.vector_search import vector_search_service
            try:
                candidates = vector_search_service.search_similar(
                    query_text=keyword, knowledge_points=[], difficulty="", limit=20
                )
            except Exception as _e:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).warning(
                    "vector search unavailable, keyword-only results: %s", _e)
                candidates = []
            existing_ids = {q.exam_id for q in results}
            need = limit - len(results)
            extra_original_ids = []
            for c in candidates:
                chunk_id = c.get("exam_id", "")       # e.g. "nat_2020_t1::kp-0"
                sim = c.get("similarity", 0)
                # Extract original exam_id from chunked ID
                orig_id = chunk_id.split("::")[0] if "::" in chunk_id else chunk_id
                if chunk_id and sim >= 0.6 and orig_id not in existing_ids and orig_id not in extra_original_ids:
                    extra_original_ids.append(orig_id)
                if len(extra_original_ids) >= need:
                    break
            if extra_original_ids:
                extra = [q for q in self.questions if q.exam_id in extra_original_ids]
                results = results + extra

        # Preserve keyword-only count for web search fallback logic
        truncated = results[:limit]
        if not isinstance(truncated, _SearchResult):
            truncated = _SearchResult(truncated)
        truncated.keyword_count = keyword_count
        return truncated

    def get_by_exam_id(self, exam_id: str) -> Optional[HistoricalQuestion]:
        """根据ID获取题目"""
        for q in self.questions:
            if q.exam_id == exam_id:
                return q
        return None

    def get_reference(self, exam_id: str, db=None) -> Optional[Dict]:
        """按题 id 取一条能塞进提示词的参考题：先看内存列表，落空再查 `historical_exams` 表。

        内存里那份冷启动只有 JSON 真题 12 条，而向量库覆盖的是表里的全量（本机实测：从库里抽
        200 条 id，能在内存列表里查到的只有 12 个）。只认内存的话，向量检索命中了也进不了
        提示词 —— 和 ADR-0024 决策 6 是同一件事：表才是权威。

        `db` 由调用方传（出题端点本来就有请求级 session），不传才自己开一个：端点把 session
        传下来，测试才管得住读的到底是哪个库。
        """
        q = self.get_by_exam_id(exam_id)
        if q is not None:
            return {
                "exam_id": q.exam_id, "content": q.content, "answer": q.answer,
                "knowledge_points": q.knowledge_points, "difficulty": q.difficulty,
                "source": q.source, "year": q.year, "question_number": q.question_number,
                "from": "memory",
            }

        from app.models.database import HistoricalExam, get_db

        own_session = db is None
        if own_session:
            db = next(get_db())
        try:
            row = db.query(HistoricalExam).filter(HistoricalExam.exam_id == exam_id).first()
        finally:
            if own_session:
                db.close()
        if row is None or not row.content:
            return None
        return {
            "exam_id": row.exam_id, "content": row.content, "answer": row.answer or "",
            "knowledge_points": list(row.knowledge_points or []),
            # 表里 difficulty 存 1~5（`Difficulty`），提示词那边历来写标签
            "difficulty": {1: "easy", 2: "moderate", 3: "medium", 4: "hard",
                           5: "competition"}.get(row.difficulty, "medium"),
            "source": row.source or "", "year": row.year or 0,
            "question_number": row.question_number or "",
            "from": "historical_exams",
        }

    def find_similar_questions(
        self,
        knowledge_points: List[str],
        difficulty: str,
        limit: int = 5
    ) -> List[HistoricalQuestion]:
        """查找相似题目(用于F6历年真题关联)"""
        # 按知识点匹配
        matched = []
        for q in self.questions:
            # 计算知识点匹配度
            overlap = len(set(q.knowledge_points) & set(knowledge_points))
            if overlap > 0:
                matched.append((q, overlap))

        # 按匹配度排序
        matched.sort(key=lambda x: x[1], reverse=True)

        # 过滤难度
        result = [q for q, _ in matched if q.difficulty == difficulty][:limit]

        # 如果难度匹配不足,补充其他难度
        if len(result) < limit:
            others = [q for q, _ in matched if q not in result][:limit - len(result)]
            result.extend(others)

        return result

    def get_exam_paper(self, source: str, year: int) -> Optional[ExamPaper]:
        """获取完整试卷"""
        key = f"{source}{year}"
        return self.papers.get(key)

    def get_papers_grouped(self) -> List[Dict]:
        """获取所有试卷，按地区→年份分组，用于前端树形展示

        合并文件系统目录：无数据的空年份也列出（empty=True, question_count=0），
        地区顺序按文件夹名排序（"01_全国卷" 等前缀保证排最前），年份升序（2020 在上）。
        """
        groups = []  # [{region, years: [{year, papers: [{name, count, year, region}]}]}]
        region_map = {}  # region -> year -> {source: count}
        for q in self.questions:
            r = q.region or "未知"
            y = q.year or 0
            src = q.source or ""
            if r not in region_map:
                region_map[r] = {}
            if y not in region_map[r]:
                region_map[r][y] = {}
            if src not in region_map[r][y]:
                region_map[r][y][src] = 0
            region_map[r][y][src] += 1

        # 合并文件系统中的地区/年份目录，空目录用于界面占位
        exam_dir = settings.EXAM_QUESTIONS_PATH
        region_order = []  # [(文件夹原名, 地区名)]，按原名排序保证前缀置顶
        for entry in os.listdir(exam_dir):
            region_path = os.path.join(exam_dir, entry)
            if not os.path.isdir(region_path):
                continue
            region = re.sub(r"^\d+_", "", entry)
            if region not in region_map:
                region_map[region] = {}
            for year_str in os.listdir(region_path):
                year_path = os.path.join(region_path, year_str)
                if not os.path.isdir(year_path):
                    continue
                try:
                    year = int(year_str)
                except ValueError:
                    continue
                region_map[region].setdefault(year, {})
            if (entry, region) not in region_order:
                region_order.append((entry, region))
        # 不在文件系统里的地区（如内置样例数据）排后，按地区名
        for r in region_map:
            if not any(reg == r for _, reg in region_order):
                region_order.append((r, r))
        region_order.sort(key=lambda x: x[0])

        for _, region in region_order:
            year_list = []
            for year in sorted(region_map[region].keys()):  # 升序：2020 排最上
                src_counts = region_map[region][year]
                if src_counts:
                    papers = [{"name": src, "question_count": cnt, "year": year, "region": region, "source_key": src}
                             for src, cnt in sorted(src_counts.items())]
                else:
                    # 空年份目录：占位卷，前端不显示 "0题"
                    papers = [{"name": f"{region}{year}", "question_count": 0, "year": year,
                               "region": region, "source_key": "", "empty": True}]
                year_list.append({"year": year, "papers": papers})
            groups.append({"region": region, "years": year_list})
        return groups

    def get_knowledge_point_stats(self) -> Dict[str, Dict]:
        """统计各知识点出现的频次和难度分布"""
        stats = {}
        for q in self.questions:
            for kp in q.knowledge_points:
                if kp not in stats:
                    stats[kp] = {"count": 0, "difficulties": [], "sources": []}
                stats[kp]["count"] += 1
                stats[kp]["difficulties"].append(q.difficulty)
                stats[kp]["sources"].append(q.source)

        # 计算各难度占比
        for kp, data in stats.items():
            diff_count = {}
            for d in data["difficulties"]:
                diff_count[d] = diff_count.get(d, 0) + 1
            data["difficulty_distribution"] = {
                k: v / len(data["difficulties"]) for k, v in diff_count.items()
            }

        return stats

    def add_question(self, question: HistoricalQuestion):
        """添加新题目，如果已存在则更新；同步写入 historical_exams（写入 historical_exams）"""
        # 检查是否已存在
        for i, q in enumerate(self.questions):
            if q.exam_id == question.exam_id:
                self.questions[i] = question
                self._sync_historical_row(question)
                return
        self.questions.append(question)
        self._sync_historical_row(question)

    def _sync_historical_row(self, question: HistoricalQuestion):
        """把真题条目 upsert 到主库 historical_exams，供题库检索/导入入口读取"""
        try:
            from app.models.database import HistoricalExam, Difficulty, get_db

            db_gen = get_db()
            db = next(db_gen)
            try:
                row = db.query(HistoricalExam).filter(
                    HistoricalExam.exam_id == question.exam_id
                ).first()
                if row is None:
                    row = HistoricalExam(exam_id=question.exam_id)
                    db.add(row)
                row.source = question.source
                row.year = question.year
                row.question_number = question.question_number
                row.knowledge_points = question.knowledge_points
                row.difficulty = Difficulty.coerce(question.difficulty).value
                row.discrimination = question.discrimination
                row.content = question.content
                row.answer = question.answer
                row.analysis = question.analysis
                db.commit()
            finally:
                db.close()
        except Exception as exc:  # 真题写入失败不阻塞检索主流程
            print(f"[ExamBank] 真题写入失败 {question.exam_id}: {exc}")

    def sync_all_to_db(self):
        """启动时把内存真题库全量同步到 historical_exams（幂等 upsert）"""
        for q in self.questions:
            self._sync_historical_row(q)
        return len(self.questions)

# 全局真题库服务实例
exam_bank_service = ExamBankService()
