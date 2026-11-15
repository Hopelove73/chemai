"""工件服务 —— Artifact 实体与版本链（能力升级批次 A1）。

三个口径（spec：Artifact API 接缝 / ADR-0027）：

- **文件真源在盘上**：默认工作区根（`CHEMAI_WORKSPACE` 或 `~/ChemAI-Workspace`）下的
  `AI生成/artifacts/<日期>/`。库表只存索引（路径、seq、url）。取数走
  `/api/artifacts/...`（鉴权 + 归属/分享收窄；A5 审查拆掉了无鉴权 /static 侧门）
  与保存逻辑共用 `artifact_base_dir()`，url 与盘上位置永不漂移。对话级工作区覆盖
  （filesystem.set_workspace_root）**不影响工件**——工件是老师的跨会话资产，不走
  per-conversation 覆盖。
- **快照式版本链**：每次保存追加 seq+1 的版本行并写新文件，旧文件永不覆盖。
- **归属收窄**：所有读路径按 owner_teacher_id 过滤，别人的工件 = 不存在（None/404）。

SSE 只传引用：tool_result 载荷有 8000 字符截断（langgraph_sse_v2._json_safe_payload），
全文由前端 fetch（带 token）转 blob 渲染——img/iframe 子资源带不上 header。
"""
import os
import secrets
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.database import Artifact, ArtifactVersion

# 工件类型 → 文件扩展名。前端渲染器注册表按 kind 分发（animation/slides → iframe，
# structure → img，doc → iframe（自包含 HTML，打印友好），table → antd Table），
# 两侧契约测试钉住 kind 集合。
ALLOWED_KINDS = {
    "animation": ".html",
    "slides": ".html",
    "doc": ".html",  # 文档类（讲评稿/教案/评语）= 自包含 HTML，非 markdown
    "structure": ".png",
    "table": ".json",
}

CONTENT_TYPES = {
    "animation": "text/html",
    "slides": "text/html",
    "doc": "text/html",
    "structure": "image/png",
    "table": "application/json",
}

WORKSPACE_ENV = "CHEMAI_WORKSPACE"  # 与 agent/tools/filesystem.py 同名同义
DEFAULT_WORKSPACE = "~/ChemAI-Workspace"
# 版本文件 API 路径（鉴权）。url 字段是「该版本」的规范引用；
# 浏览器渲染一律经前端 fetch→blob，不走裸 url。
VERSION_API_PREFIX = "/api/artifacts"


def artifact_base_dir() -> Path:
    """工件落盘根。只认默认工作区（env 或 ~），不认对话级覆盖。"""
    root = os.environ.get(WORKSPACE_ENV) or DEFAULT_WORKSPACE
    return Path(os.path.expanduser(root)) / "AI生成" / "artifacts"


def save_artifact(db: Session, *, owner_teacher_id: str, kind: str, title: str,
                  content, conversation_id: str | None = None,
                  note: str | None = None, artifact_id: str | None = None,
                  source_skill: str | None = None) -> dict:
    """保存一次工件 = 写一个新版本文件 + 追加一条版本行。

    artifact_id 为空时新建工件（v1）；给定则追版本（须为本人工件，否则 LookupError）。
    source_skill 记录产出技能（版本续接按 owner+kind+skill+title 隔离）。
    返回 dict 即工具结果里的 `artifact` 引用（adapter 抽成 SSE artifact 事件的形状）。
    """
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"未知工件类型: {kind!r}（允许: {sorted(ALLOWED_KINDS)}）")
    if not isinstance(content, (str, bytes)) or not content:
        raise ValueError("工件内容为空")

    now = datetime.utcnow()
    if artifact_id is None:
        artifact_id = f"art_{int(time.time() * 1000):x}_{secrets.token_hex(4)}"
        art = Artifact(id=artifact_id, owner_teacher_id=owner_teacher_id, kind=kind,
                       source_skill=source_skill,
                       title=(title or "")[:255], conversation_id=conversation_id,
                       current_version=0, created_at=now, updated_at=now)
        db.add(art)
        seq, created = 1, True
    else:
        art = (db.query(Artifact)
               .filter(Artifact.id == artifact_id,
                       Artifact.owner_teacher_id == owner_teacher_id)
               .first())
        if art is None:
            raise LookupError(f"工件不存在或不属于你: {artifact_id}")
        if kind != art.kind:
            raise ValueError(f"工件类型不可变: {art.kind} → {kind}")
        seq, created = art.current_version + 1, False
    art.updated_at = now
    if title:
        art.title = title[:255]

    payload = content.encode("utf-8") if isinstance(content, str) else content
    # 并发追版本（bug 狩猎 H1）：两个请求同时算出 seq=N+1 时，O_EXCL 让恰好
    # 一个落文件成功，另一个吃 FileExistsError——捕获后重读最新版本重算 seq
    # 重试（有界），把竞态消化成干净的成功/冲突，而非裸 OSError 500。
    import time as _time
    for _attempt in range(6):
        if _attempt:
            _time.sleep(0.02 * _attempt)  # 让胜者的 commit 先落地再重读
        rel = Path(now.strftime("%Y-%m-%d")) / f"{artifact_id}_v{seq}{ALLOWED_KINDS[kind]}"
        full = artifact_base_dir() / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(full), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            db.rollback()
            art = (db.query(Artifact)
                   .filter(Artifact.id == artifact_id,
                           Artifact.owner_teacher_id == owner_teacher_id)
                   .first())
            if art is None:
                raise LookupError(f"工件不存在或不属于你: {artifact_id}")
            seq = art.current_version + 1
            continue
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)

        url = f"{VERSION_API_PREFIX}/{artifact_id}/v/{seq}"
        db.add(ArtifactVersion(artifact_id=artifact_id, seq=seq, file_path=str(full),
                               url=url, note=note, created_at=now))
        art.current_version = seq
        try:
            db.commit()
        except Exception:
            db.rollback()
            full.unlink(missing_ok=True)  # 版本行没落库，刚写的快照一并撤掉
            raise
        return {"artifact_id": artifact_id, "kind": kind, "title": art.title,
                "version_seq": seq, "url": url, "file_path": str(full),
                "created": created}

    raise RuntimeError(f"工件 {artifact_id} 并发保存冲突超过重试上限，请重试")


def list_artifacts(db: Session, owner_teacher_id: str, kind: str | None = None,
                   conversation_id: str | None = None, limit: int = 50) -> list[dict]:
    """老师的工件库列表（最近更新在前），别人的不可见。"""
    q = (db.query(Artifact)
         .filter(Artifact.owner_teacher_id == owner_teacher_id))
    if kind:
        q = q.filter(Artifact.kind == kind)
    if conversation_id:
        q = q.filter(Artifact.conversation_id == conversation_id)
    rows = (q.order_by(Artifact.updated_at.desc(), Artifact.id.desc())
            .limit(max(1, min(limit, 200))).all())
    return [{"artifact_id": r.id, "kind": r.kind, "title": r.title,
             "conversation_id": r.conversation_id, "current_version": r.current_version,
             "updated_at": r.updated_at.isoformat() if r.updated_at else None}
            for r in rows]


def get_artifact(db: Session, artifact_id: str, owner_teacher_id: str) -> dict | None:
    """详情含版本列表（seq 升序）；不属于本人 → None。"""
    art = (db.query(Artifact)
           .filter(Artifact.id == artifact_id,
                   Artifact.owner_teacher_id == owner_teacher_id)
           .first())
    if art is None:
        return None
    versions = (db.query(ArtifactVersion)
                .filter(ArtifactVersion.artifact_id == artifact_id)
                .order_by(ArtifactVersion.seq.asc()).all())
    return {"artifact_id": art.id, "kind": art.kind, "title": art.title,
            "conversation_id": art.conversation_id,
            "current_version": art.current_version,
            "updated_at": art.updated_at.isoformat() if art.updated_at else None,
            "versions": [{"seq": v.seq, "url": v.url, "note": v.note,
                          "created_at": v.created_at.isoformat() if v.created_at else None}
                         for v in versions]}


def resolve_version(db: Session, artifact_id: str, seq: int,
                    owner_teacher_id: str) -> tuple[Path, str] | None:
    """定位某版本文件，返回 (路径, kind)；不属于本人或版本不存在 → None。

    兜底校验：解析出的路径必须仍在工件根之下——当前写路径由服务生成不存在穿越，
    这层纵深保证将来任何写路径失误都不会升级成任意文件读。
    """
    row = (db.query(ArtifactVersion, Artifact)
           .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
           .filter(ArtifactVersion.artifact_id == artifact_id,
                   ArtifactVersion.seq == seq,
                   Artifact.owner_teacher_id == owner_teacher_id)
           .first())
    if row is None:
        return None
    version, art = row
    path = Path(version.file_path)
    base = artifact_base_dir()
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        return None
    return (path, art.kind) if path.is_file() else None


def content_type_for(kind: str) -> str:
    return CONTENT_TYPES.get(kind, "application/octet-stream")
