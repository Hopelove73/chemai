"""A2 —— 上传原图取回端点（工件面板「附件」tab 的取数路径）。

钉三件事：上传返回体带 image_url（仅图片类）、端点回原图二进制（原 mime）、
不存在的 id 一律 404。上传件目前没有归属表（任何教师在对话里可引用任何
upload_id 是现状契约），端点只做员工身份校验——学生 403。
"""
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token
from app.models.database import Base, UploadSession, get_db

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32  # 够用的假 PNG 头


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _auth(user="t-1", role="teacher"):
    return {"Authorization": f"Bearer {create_access_token(user, role)}"}


def _seed(db, sid="up-1", mime="image/png", data=PNG, name="答题卡.png"):
    db.add(UploadSession(id=sid, file_data=data, file_name=name,
                         mime_type=mime, status="previewing"))
    db.commit()


def test_image_upload_returns_image_url(client, db_session):
    """图片类上传的返回体带 image_url；这是前端收进附件 tab 的开关。"""
    resp = client.post("/api/agent/upload",
                       files={"file": ("答题卡.png", io.BytesIO(PNG), "image/png")},
                       headers=_auth())
    # OCR 可能因无凭据失败，但上传与返回体形状必须成立
    assert resp.status_code == 200
    body = resp.json()
    assert body["image_url"] == f"/api/agent/upload/{body['upload_id']}/image"
    assert body["file_name"] == "答题卡.png"


def test_pdf_upload_has_no_image_url(client, db_session):
    resp = client.post(
        "/api/agent/upload",
        files={"file": ("讲义.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
        headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["image_url"] is None


def test_image_endpoint_returns_original_bytes(client, db_session):
    _seed(db_session)
    resp = client.get("/api/agent/upload/up-1/image", headers=_auth())
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content == PNG


def test_missing_or_empty_upload_404(client, db_session):
    assert client.get("/api/agent/upload/no-such/image",
                      headers=_auth()).status_code == 404
    _seed(db_session, sid="up-empty", data=b"")
    assert client.get("/api/agent/upload/up-empty/image",
                      headers=_auth()).status_code == 404


def test_student_rejected(client, db_session):
    _seed(db_session)
    assert client.get("/api/agent/upload/up-1/image",
                      headers=_auth("s-1", "student")).status_code == 403
