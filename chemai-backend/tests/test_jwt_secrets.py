"""JWT 密钥的三条新规则：缺了就拒绝启动、公开默认值永不被接受、旧密钥只在轮换名单里参与验签。"""
import pytest

from app.middleware import auth


def _reload_with(monkeypatch, mapping):
    """按给定的环境变量重跑一次密钥装载（它读的是 import 期环境）。

    `load_dotenv` 换成假动作，否则这台机器的 `.env` 会参与结论 —— 那正是这套测试基座
    一直在防的事。
    """
    import dotenv
    for k, v in mapping.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    return auth._load_jwt_secrets()


def test_a_missing_secret_refuses_to_start(monkeypatch):
    """没有密钥就 raise，不再静默用公开默认值签票。

    静默回退的坏处不是"弱一点"：签出来的 token 与正常 token 长得一样，没人会发现整个部署
    其实一直在用一把源码里看得见的密钥。
    """
    with pytest.raises(RuntimeError, match="CHEMAI_JWT_SECRET 未设置"):
        _reload_with(monkeypatch, {"CHEMAI_JWT_SECRET": None,
                                   "CHEMAI_JWT_SECRETS_PREVIOUS": ""})


def test_the_public_default_is_refused_as_a_current_secret(monkeypatch):
    """照 `.env.example` 抄成原样也必须被拦住 —— 那个值在仓库里是公开的。"""
    with pytest.raises(RuntimeError, match="公开默认值"):
        _reload_with(monkeypatch, {"CHEMAI_JWT_SECRET": auth.INSECURE_DEFAULT_SECRET,
                                   "CHEMAI_JWT_SECRETS_PREVIOUS": ""})


def test_the_leaked_default_never_enters_the_rotation_list(monkeypatch):
    """轮换名单是给"我们自己换过的旧密钥"用的，泄露过的默认值进来就等于把伪造窗口重开。"""
    _, previous = _reload_with(
        monkeypatch, {"CHEMAI_JWT_SECRET": "s-2026-current",
                      "CHEMAI_JWT_SECRETS_PREVIOUS": "s-old," + auth.INSECURE_DEFAULT_SECRET})
    assert previous == ["s-old"], previous


def test_a_token_signed_with_an_old_key_still_verifies_only_during_the_window(monkeypatch):
    """旧 token 在窗口内可用（不用把所有人踢下线），名单清空后立刻失效。"""
    old = auth.create_jwt({"user_id": "u1", "role": "teacher", "type": "access"},
                          "s-old", expires_in=None)
    monkeypatch.setattr(auth, "SECRET_KEY", "s-current", raising=False)
    monkeypatch.setattr(auth, "PREVIOUS_SECRETS", ["s-old"], raising=False)
    assert auth.verify_signed(old) is not None, "轮换窗口没起作用"

    monkeypatch.setattr(auth, "PREVIOUS_SECRETS", [], raising=False)
    assert auth.verify_signed(old) is None, "窗口关了还认旧密钥，等于没换密钥"

    # 陌生密钥签的一律不认（防的是"换个密钥就以为能进"）
    forged = auth.create_jwt({"user_id": "u2", "type": "access"}, "someone-else")
    monkeypatch.setattr(auth, "PREVIOUS_SECRETS", ["s-old"], raising=False)
    assert auth.verify_signed(forged) is None


def test_signing_always_uses_the_current_key(monkeypatch):
    """轮换期内签出来的新票只认当前密钥 —— 否则旧密钥泄露后能无限续期。"""
    monkeypatch.setattr(auth, "SECRET_KEY", "s-current", raising=False)
    monkeypatch.setattr(auth, "PREVIOUS_SECRETS", ["s-old"], raising=False)
    fresh = auth.create_access_token("u1", "teacher", None)
    only_old = auth.verify_jwt(fresh, "s-old", verify_exp=True)
    assert only_old is None, "新票被旧密钥签验成功，轮换就变成永久有效"
    assert auth.verify_token(fresh) is not None
