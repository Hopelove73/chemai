"""make_release_zip.py — 打包项目源码（含演示数据）为桌面 zip，开箱可用。

清单：chemai-backend 全部源码 + 演示库 chemai.db + 前端构建产物 dist +
数据与向量索引；剔除 node_modules / 真实 .env / 缓存与运行时垃圾；
自动生成含**每包随机 CHEMAI_JWT_SECRET** 的 .env（auth.py 的 fail-closed
启动守卫要求它；随机生成随包分发是安全的，真实开发者密钥不进包）；
zip 根放 快速上手.md（演示账号表自动从根 README 提取）。

用法：
    python scripts/make_release_zip.py                 # 桌面 ChemAI-源码-<日期>.zip
    python scripts/make_release_zip.py out.zip

打包前记得跑 `python scripts/db_checkpoint.py` 把 WAL 落盘（活库纪律）。
"""
import os
import secrets
import sys
import zipfile
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

EXCLUDE_DIRS = {"node_modules", "__pycache__", ".pytest_cache", ".venv",
                ".vite", ".ruff_cache", ".mypy_cache", "htmlcov", ".idea", ".vscode"}


def iter_backend():
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            fp = Path(root) / f
            if fp.name == ".env" or fp.suffix in (".pyc", ".pyo", ".log"):
                continue
            yield fp


def quickstart_text() -> str:
    """从根 README 提取演示账号表作为快速上手；找不到时给最小版。"""
    readme = REPO / "README.md"
    text = readme.read_text(encoding="utf-8") if readme.exists() else ""
    if "### 演示账号" in text:
        i = text.index("### 演示账号")
        j = text.index("##", i + 4)
        return "# 智辅化学 · 快速上手\n\n" + text[i:j].replace("### ", "## ")
    return "# 智辅化学 · 快速上手\n\n见 chemai-backend/README.md（账号/启动/评测）。\n"


def main(out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for fp in sorted(iter_backend()):
            z.write(fp, "ChemAI/chemai-backend/" + fp.relative_to(BACKEND).as_posix())
            n += 1
        # 开箱 .env：模板 + 每包随机 JWT secret（随机生成随包分发是安全的；
        # 真实开发者密钥不进包）。收件人要启用 AI 功能只需再填 DEEPSEEK_API_KEY。
        env = (BACKEND / ".env.example").read_text(encoding="utf-8").replace(
            "CHEMAI_JWT_SECRET=paste_a_generated_random_secret_here",
            "CHEMAI_JWT_SECRET=" + secrets.token_urlsafe(48))
        z.writestr("ChemAI/chemai-backend/.env", env)
        n += 1
        z.writestr("ChemAI/快速上手.md", quickstart_text())
        n += 1
        z.write(REPO / "README.md", "ChemAI/README.md")
        n += 1
    print(f"files={n} zip={out.stat().st_size / 1e6:.0f}MB -> {out}")


if __name__ == "__main__":
    default = Path.home() / "Desktop" / f"ChemAI-源码-{date.today().isoformat()}.zip"
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else default)
