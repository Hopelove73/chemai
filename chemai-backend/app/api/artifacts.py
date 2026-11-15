"""工件读面 —— 老师的工件库（能力升级批次 A1，spec：Artifact API 唯一新主接缝）。

A1 只有三条只读端点：列表 / 详情 / 版本文件本体。写路径在服务层（save_artifact，
批次 A3 的技能入口调用）；A5 加两类：学生分享视图（有分享通知才放行）与
.pptx 导出。归属收窄复用 workspace 资源键——工件就是老师的工作区资产，不新增权限键。
"""
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker
from app.models.database import Artifact, ArtifactVersion, StudentNotification, get_db
from app.services import artifact_service

router = APIRouter()


@router.get("/artifacts")
def list_artifacts(
    kind: Optional[str] = Query(None, description="按工件类型筛选"),
    conversation_id: Optional[str] = Query(None, description="按产生会话筛选"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("workspace", "read")),
):
    """老师的工件库（跨会话索引，最近更新在前）。"""
    items = artifact_service.list_artifacts(
        db, current_user.get("user_id") or "",
        kind=kind, conversation_id=conversation_id, limit=limit)
    return {"items": items}


@router.get("/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: str,
    db: Session = Depends(get_db),
    # allow_self（批 2 S5b）：学生读**自己名下**的工件（表述特训报告）；
    # service 本身按 owner 过滤，self 角色越权拿不到别人的——fail-closed 保持
    current_user: dict = Depends(PermissionChecker("workspace", "read", allow_self=True)),
):
    detail = artifact_service.get_artifact(db, artifact_id, current_user.get("user_id") or "")
    if detail is None:
        raise HTTPException(status_code=404, detail="工件不存在")
    return detail


@router.get("/artifacts/{artifact_id}/v/{seq}")
def get_artifact_version_file(
    artifact_id: str,
    seq: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("workspace", "read", allow_self=True)),
):
    """版本文件本体（右栏 iframe/图片的取数源）。沙箱由前端 iframe 承担，这里只管
    归属校验与正确的 Content-Type。一次 join 同时拿路径与 kind（中途被删不 500）。"""
    resolved = artifact_service.resolve_version(db, artifact_id, seq,
                                                current_user.get("user_id") or "")
    if resolved is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    path, kind = resolved
    return FileResponse(path, media_type=artifact_service.content_type_for(kind))


@router.get("/artifacts/shared/{artifact_id}")
def get_shared_artifact(
    artifact_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("workspace", "read", allow_self=True)),
):
    """学生取回被分享的工件元数据（A5「老师分享」卡片点开全屏预览）。

    放行口径 fail-closed：学生（self 角色）必须有指向该工件的 artifact_share
    收件通知才可见；教师仍走归属校验（复用 get_artifact）。文件本体走
    /artifacts/shared/{id}/v/{seq}，同一放行口径。
    """
    user_id = current_user.get("user_id") or ""
    role = current_user.get("role") or ""
    if role in ("student", "parent"):
        shared_versions = _shared_versions(db, artifact_id, user_id)
        if not shared_versions:
            raise HTTPException(status_code=404, detail="工件不存在")
        art = db.query(Artifact).filter(Artifact.id == artifact_id).first()
    else:
        art = (db.query(Artifact)
               .filter(Artifact.id == artifact_id,
                       Artifact.owner_teacher_id == user_id).first())
    if art is None:
        raise HTTPException(status_code=404, detail="工件不存在")
    # 学生视野钉在他被分享过的版本上（P2-4）：教师批准 v1 后又生成 v2，
    # 没有新的门决策，学生不会自动看到 v2。
    current = (max(shared_versions) if role in ("student", "parent")
               else art.current_version)
    return {"artifact_id": art.id, "kind": art.kind, "title": art.title,
            "current_version": current}


@router.get("/artifacts/shared/{artifact_id}/v/{seq}")
def get_shared_artifact_file(
    artifact_id: str,
    seq: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("workspace", "read", allow_self=True)),
):
    """学生取回被分享的版本文件本体（放行口径同上，且 seq 必须是分享过的版本）。"""
    user_id = current_user.get("user_id") or ""
    role = current_user.get("role") or ""
    if role in ("student", "parent"):
        shared_versions = _shared_versions(db, artifact_id, user_id)
        if not shared_versions or seq not in shared_versions:
            raise HTTPException(status_code=404, detail="版本不存在")
        art = db.query(Artifact).filter(Artifact.id == artifact_id).first()
        row = (db.query(ArtifactVersion)
               .filter(ArtifactVersion.artifact_id == artifact_id,
                       ArtifactVersion.seq == seq).first())
        if art is None or row is None or not _is_under_base(row.file_path):
            raise HTTPException(status_code=404, detail="版本不存在")
        return FileResponse(Path(row.file_path),
                            media_type=artifact_service.content_type_for(art.kind))
    resolved = artifact_service.resolve_version(db, artifact_id, seq, user_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    path, kind = resolved
    return FileResponse(path, media_type=artifact_service.content_type_for(kind))


def _shared_versions(db: Session, artifact_id: str, student_id: str) -> set[int]:
    """分享记录 = 收件通知（type=artifact_share，content JSON 带 artifact_id）。

    返回分享给该学生的**版本集合**（每版本一次门决策，P2-4）；空集 = 不可见。
    content 解析 fail-closed：非 dict / 坏 JSON / artifact_id 不匹配一律跳过
    （P2-5：通用通知端点可自报 content，这里只认结构完整且 id 全等的记录）。"""
    rows = (db.query(StudentNotification)
            .filter(StudentNotification.student_id == student_id,
                    StudentNotification.type == "artifact_share").all())
    versions: set[int] = set()
    for n in rows:
        try:
            payload = json.loads(n.content or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("artifact_id") != artifact_id:
            continue
        try:
            versions.add(int(payload.get("version") or 1))
        except (TypeError, ValueError):
            versions.add(1)
    return versions


def _is_under_base(file_path: str) -> bool:
    from pathlib import Path
    base = artifact_service.artifact_base_dir().resolve()
    try:
        Path(file_path).resolve().relative_to(base)
        return True
    except ValueError:
        return False


@router.get("/artifacts/{artifact_id}/export")
def export_artifact_pptx(
    artifact_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("workspace", "read")),
):
    """导出 .pptx（A5 二等能力）：把自包含 HTML 的文本结构抽成大纲 deck——
    标题页 + 每个区块一页（教学动画/课件/文档均适用；Canvas 动画帧不可导出，
    deck 保留的是结构与文字）。经确认门与否：导出是给老师自己的文件，不过门
    （R6 只管对外分发）。"""
    import io

    from pptx import Presentation
    from pptx.util import Pt

    user_id = current_user.get("user_id") or ""
    art = (db.query(Artifact)
           .filter(Artifact.id == artifact_id,
                   Artifact.owner_teacher_id == user_id).first())
    if art is None:
        raise HTTPException(status_code=404, detail="工件不存在")
    # 大纲 deck 只对文字型工件有意义（P2-6）：结构图/表格没有可抽的结构
    if art.kind not in ("slides", "doc"):
        raise HTTPException(status_code=400, detail=f"该工件类型（{art.kind}）不支持导出 PPT")

    resolved = artifact_service.resolve_version(db, artifact_id, art.current_version, user_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    path, _kind = resolved
    html = path.read_text(encoding="utf-8")

    prs = Presentation()
    title_layout = prs.slide_layouts[0]
    bullet_layout = prs.slide_layouts[1]

    def _add(prs_title, bullets):
        slide = prs.slides.add_slide(bullet_layout)
        slide.shapes.title.text = prs_title or ""
        body = slide.placeholders[1].text_frame
        body.clear()
        for i, line in enumerate(bullets[:8]):
            para = body.paragraphs[0] if i == 0 else body.add_paragraph()
            para.text = line
            para.font.size = Pt(18)

    sections = _extract_sections(html)
    cover = prs.slides.add_slide(title_layout)
    cover.shapes.title.text = art.title or "未命名工件"
    cover.placeholders[1].text = f"ChemAI 导出 · {_now_label()}"

    if not sections:
        slide = prs.slides.add_slide(bullet_layout)
        slide.shapes.title.text = art.title or "内容"
        body = slide.placeholders[1].text_frame
        body.text = "（该工件以交互画面为主，文本结构较少——请在 ChemAI 工件面板中查看完整效果）"
    for sec_title, lines in sections:
        _add(sec_title, lines)

    buf = io.BytesIO()
    prs.save(buf)
    from fastapi.responses import Response as _Resp
    # 文件名只用 artifact_id：Content-Disposition header 是 latin-1，
    # 中文标题进不去（标题在 PPT 封面页里，不损失信息）
    return _Resp(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{artifact_id}.pptx"'})


def _extract_sections(html: str) -> list[tuple[str, list[str]]]:
    """从自包含 HTML 抽「区块标题 + 文字要点」——只认 h1/h2/h3 与 li/p 文本，
    不引解析库（标准库 re，够用即可：导出是大纲不是复刻）。"""
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    # 标题行启发：原文里 h1/h2/h3 的内容单独成行且较短
    heads = re.findall(r"<h[123][^>]*>([\s\S]*?)</h[123]>", html, re.I)
    head_set = {re.sub(r"<[^>]+>|\s+", "", h) for h in heads}
    sections: list[tuple[str, list[str]]] = []
    cur_title, cur_lines = "", []
    for ln in lines:
        if re.sub(r"<[^>]+>|\s+", "", ln) in head_set and ln != cur_title:
            if cur_title or cur_lines:
                sections.append((cur_title, cur_lines))
            cur_title, cur_lines = ln, []
        else:
            cur_lines.append(ln)
    if cur_title or cur_lines:
        sections.append((cur_title, cur_lines))
    return [(t, [x for x in ls if len(x) > 1]) for t, ls in sections if t or ls][:12]


def _now_label() -> str:
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%d")
