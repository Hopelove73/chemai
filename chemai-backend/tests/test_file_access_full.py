"""文件访问三档（2026-08 对齐 DSH）：off 关闭 / workspace 工作区(默认,区外按次审批)
/ full 完全访问(区外直接放行,敏感文件仍拒绝)。

测试自建工作区与区外文件（均在 tmp_path 下），不依赖机器上的任何真实路径。
"""
import asyncio
import json

import pytest

from agent.tools.filesystem import (
    read, find, grep, set_file_access_level, set_workspace_root,
)


def _run(coro):
    async def go():
        return await coro

    return asyncio.run(go())


def _read(path):
    return json.loads(_run(read(path=str(path))))


@pytest.fixture(autouse=True)
def workspace(tmp_path):
    """把工作区钉在 tmp_path/ws，区内文件放 tmp_path/inside，区外文件放 tmp_path/outside。

    这样"工作区外"是结构上确定的，不依赖 ~/ChemAI-Workspace 或环境变量。
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    set_workspace_root(str(ws))
    yield ws
    set_file_access_level("workspace")
    set_workspace_root("")


@pytest.fixture
def outside_file(tmp_path):
    """工作区外的一个普通文件（tmp_path/outside 与工作区 tmp_path/ws 平级）。"""
    d = tmp_path / "outside"
    d.mkdir(exist_ok=True)
    f = d / "sample.md"
    f.write_text("这是工作区外的内容", encoding="utf-8")
    return f


def test_full_mode_reads_outside_without_approval(outside_file):
    set_file_access_level("full")
    data = _read(outside_file)
    assert "approval_needed" not in data, f"full 档不应弹审批: {str(data)[:200]}"
    assert data.get("content") == "这是工作区外的内容"


def test_workspace_mode_still_requires_approval_outside(outside_file):
    set_file_access_level("workspace")
    data = _read(outside_file)
    assert data.get("approval_needed") is True


def test_off_mode_rejects_everything(outside_file):
    set_file_access_level("off")
    data = _read(outside_file)
    assert "error" in data
    assert "关闭" in str(data["error"])


def test_full_mode_still_rejects_sensitive_files(tmp_path):
    d = tmp_path / "outside"
    d.mkdir(exist_ok=True)
    env_file = d / ".env"
    env_file.write_text("SECRET=1", encoding="utf-8")

    set_file_access_level("full")
    data = _read(env_file)
    assert "error" in data
    assert "敏感文件" in str(data["error"])


def test_inside_workspace_needs_no_approval(workspace):
    """区内文件在默认 workspace 档下直接可读，不弹审批。"""
    f = workspace / "note.md"
    f.write_text("工作区内的内容", encoding="utf-8")

    set_file_access_level("workspace")
    data = _read(f)
    assert "approval_needed" not in data, f"区内不应弹审批: {str(data)[:200]}"
    assert data.get("content") == "工作区内的内容"


def test_set_level_accepts_full_and_falls_back_on_unknown():
    assert set_file_access_level("full") == "full"
    assert set_file_access_level("bogus") == "workspace"
    set_file_access_level("workspace")


# ── W3：批准目录遍历不再把嵌套密钥读进模型 ────────────────────────

def _grep(pattern, path):
    return json.loads(_run(grep(pattern=pattern, path=str(path))))


def _find(pattern, path):
    return json.loads(_run(find(pattern=pattern, path=str(path))))


def test_grep_does_not_read_nested_secrets(workspace):
    (workspace / "note.md").write_text("盐类水解 score: 90\n", encoding="utf-8")
    (workspace / "app.py").write_text("token_name = SECRET_TOKEN\n", encoding="utf-8")
    (workspace / ".env").write_text("SECRET_TOKEN=s3cr3t-value\n", encoding="utf-8")
    set_file_access_level("workspace")

    data = _grep("SECRET_TOKEN", workspace)
    assert data.get("skipped_sensitive", 0) >= 1
    for m in data["matches"]:
        assert not m["file"].endswith(".env"), "grep 结果不得含 .env"
        assert "s3cr3t-value" not in m["text"], "密钥值不得泄进上下文"
    # 普通文件照常命中，未因一个 .env 让整次搜索失败
    assert any(m["file"].endswith("app.py") for m in data["matches"])


def test_find_does_not_surface_secret_filenames(workspace):
    (workspace / "note.md").write_text("hi\n", encoding="utf-8")
    (workspace / "id_rsa").write_text("PRIVATE KEY\n", encoding="utf-8")
    set_file_access_level("workspace")

    data = _find("*", workspace)
    assert not any("id_rsa" in n for n in data["matches"]), "密钥文件名不得枚举给模型"
    assert data.get("skipped_sensitive", 0) >= 1
    assert any(n.endswith("note.md") for n in data["matches"])


def test_read_direct_env_still_rejected(workspace):
    env_file = workspace / ".env"
    env_file.write_text("SECRET=1", encoding="utf-8")
    set_file_access_level("workspace")
    data = _read(env_file)
    assert "敏感文件" in str(data.get("error", ""))


def test_read_rejects_dotted_env_variants(workspace):
    """.env.local / prod.env 等变体也必须被咽喉拦下（旧名单只 endswith .env 会漏）。"""
    set_file_access_level("full")
    for nm in (".env.local", "prod.env", ".env.production"):
        f = workspace / nm
        f.write_text("DB_PASSWORD=hunter2", encoding="utf-8")
        data = _read(f)
        assert "敏感文件" in str(data.get("error", "")), (nm, data)


def test_read_rejects_singular_credential(workspace):
    """单数 credential（旧名单只匹配复数 credentials）也要拒读。"""
    set_file_access_level("full")
    f = workspace / "my-credential.txt"
    f.write_text("api_token=abc", encoding="utf-8")
    data = _read(f)
    assert "敏感文件" in str(data.get("error", "")), data


def test_dotenv_templates_remain_readable(workspace):
    """.env.example / .env.sample 等是共享模板，不得被敏感名单误伤（W3 不伤可用性）。"""
    set_file_access_level("full")
    for nm in (".env.example", ".env.sample", ".env.template", ".env.dist"):
        f = workspace / nm
        f.write_text("PUBLIC=template\n", encoding="utf-8")
        data = _read(f)
        assert "敏感文件" not in str(data.get("error", "")), (nm, data)
        assert data.get("content", "").strip() == "PUBLIC=template", (nm, data)


def test_read_rejects_every_private_key_and_cred_store(workspace):
    """私钥名单按算法名写就只会漏：id_ed25519 / id_ecdsa / id_dsa 与 .netrc、
    .pgpass（curl / psql 的凭据文件）曾在 full 档下直接读进模型。"""
    set_file_access_level("full")
    for nm in ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", ".netrc", ".pgpass", ".htpasswd"):
        f = workspace / nm
        f.write_text("PRIVATE KEY MATERIAL\n", encoding="utf-8")
        data = _read(f)
        assert "敏感文件" in str(data.get("error", "")), (nm, data)


def test_private_key_rule_does_not_swallow_ordinary_files(workspace):
    """收紧的代价不能是误伤：公钥（带 .pub）与带扩展名的 id_ 文件是普通资料。"""
    set_file_access_level("full")
    for nm in ("id_ed25519.pub", "id_名单.csv"):
        f = workspace / nm
        f.write_text("public\n", encoding="utf-8")
        data = _read(f)
        assert "敏感文件" not in str(data.get("error", "")), (nm, data)
        assert data.get("content", "").strip() == "public", (nm, data)
