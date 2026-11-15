"""三端去 AI 味升级（UI v2.3）守门 eval —— 基线/边界/回归的可执行子集。

规格真源：DESIGN.md v2.3（R-K/R-T3/R-M/R-I5-7/R-C8/P-21-22/R-L5）+ design/ 三分册。
断言全部确定性（零浏览器、零 API），浏览器侧基线/边界见
evals/e2e-ui-v23-checklist.md（U1-U15 已真跑）。

三层映射：
  基线 —— 语义层/字阶六档/ChemGlyph 元素诚实性/动效编排集中 定义存在且合规；
  边界 —— reduced-motion 守卫、viewport-fit、BackgroundArt 强度分档接口；
  回归 —— token-lint 全绿、裸调色板零残留、禁改任意值不回潮、渐变收敛（R-I7）。

用法：
    python evals/run_evals.py --fast --only ui_v23
"""
import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# script 条目不经过根 conftest —— 影子库兜底与其他 script 条目同款
from app.shadow_data import activate as _activate_shadow  # noqa: E402

_activate_shadow()

from evals.conftest import EvalReport  # noqa: E402

FRONTEND = PROJECT_ROOT / "frontend"
SRC = FRONTEND / "src"

REAL_ELEMENTS = {
    'H', 'He', 'Li', 'Be', 'B', 'C', 'N', 'O', 'F', 'Ne', 'Na', 'Mg', 'Al',
    'Si', 'P', 'S', 'Cl', 'Ar', 'K', 'Ca', 'Fe', 'Cu', 'Zn', 'Ag', 'Sn',
    'I', 'Ba', 'Pt', 'Au', 'Hg', 'Pb', 'Mn',
}

# 裸调色板类（R-C7/R-K2 类名层）
BARE_PALETTE_RE = re.compile(
    r"\b(?:bg|text|border|ring|from|to|via)-(?:amber|violet|slate|rose|sky|blue|green|red|"
    r"yellow|purple|gray|zinc|lime|emerald|indigo|fuchsia|cyan|orange|pink)-\d{2,3}\b")


def _src_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in SRC.rglob("*") if p.suffix in (".tsx", ".ts"))


def _globals() -> str:
    return (FRONTEND / "src" / "styles" / "globals.css").read_text(encoding="utf-8")


# ── 基线 ──────────────────────────────────────────────────────────────

def baseline_semantic_layer(report: EvalReport) -> None:
    """R-K1：语义样式层存在且值只引令牌。"""
    g = _globals()
    for cls in (".surface-card", ".press-target", ".hairline"):
        report.add(f"ui-v23 基线: {cls} 已定义", cls in g)
    surface = re.search(r"\.surface-card \{[^}]+\}", g)
    ok = bool(surface) and "var(--color-" in surface.group(0) and "var(--radius-card)" in surface.group(0)
    report.add("ui-v23 基线: surface-card 值全部引令牌（含 per-theme 卡圆角）", ok)


def baseline_text_tiers(report: EvalReport) -> None:
    """R-T3：六档字阶定义齐全，三端各有 --text-* 值。"""
    g = _globals()
    tiers_ok = all(f".text-{t}" in g for t in ("display", "title", "body", "sub", "caption", "micro"))
    report.add("ui-v23 基线: 六档字阶语义类定义（R-T3）", tiers_ok)
    themes = (FRONTEND / "src" / "styles" / "mobile-themes.css").read_text(encoding="utf-8")
    report.add("ui-v23 基线: 学生/家长端覆盖 --text-display/--text-body",
               "--text-display: 32px" in themes and "--text-display: 48px" in themes
               and themes.count("--text-body:") >= 2)
    tokens = (FRONTEND / "src" / "styles" / "tokens.css").read_text(encoding="utf-8")
    report.add("ui-v23 基线: 教师端 --text-* 与 --radius-card 在场",
               "--text-display: 40px" in tokens and "--radius-card: 16px" in tokens)


def baseline_chem_glyph_honesty(report: EvalReport) -> None:
    """R-S-C10：元素 tile 只放真实元素。"""
    src = (SRC / "components" / "ui" / "ChemGlyph.tsx").read_text(encoding="utf-8")
    pairs = re.findall(r"sym: '([A-Za-z]{1,2})', num: (\d+)", src)
    ok = bool(pairs) and all(sym in REAL_ELEMENTS and int(num) > 0 for sym, num in pairs)
    report.add(f"ui-v23 基线: ChemGlyph 元素诚实性（{len(pairs)} 个语义位全真元素）", ok)


def baseline_motion_centralized(report: EvalReport) -> None:
    """R-M1：动效编排集中在 lib/motion.ts，页面零散写 framer 缓动数组。"""
    motion_lib = (SRC / "lib" / "motion.ts").read_text(encoding="utf-8")
    report.add("ui-v23 基线: motion presets 模块存在", "EASE" in motion_lib and "pageTransition" in motion_lib)
    strays = []
    for p in SRC.rglob("*.tsx"):
        if p.name == "motion.ts" or "lib" in p.parts:
            continue
        text = p.read_text(encoding="utf-8")
        if re.search(r"ease:\s*\[0\.\d", text):
            strays.append(str(p.relative_to(SRC)))
    report.add("ui-v23 基线: 页面零散写缓动数组 = 0（R-M1 编排集中）", not strays,
               f"散写处: {strays}" if strays else "")


# ── 边界 ──────────────────────────────────────────────────────────────

def boundary_guards(report: EvalReport) -> None:
    """边界：reduced-motion 双层守卫 + viewport-fit + BackgroundArt 分档接口。"""
    g = _globals()
    layout = (SRC / "components" / "mobile" / "MobileLayout.tsx").read_text(encoding="utf-8")
    report.add("ui-v23 边界: CSS 动效 reduced-motion 守卫（R-M1 配套）",
               "prefers-reduced-motion" in g)
    app = (SRC / "App.tsx").read_text(encoding="utf-8")
    report.add("ui-v23 边界: MotionConfig reducedMotion 全局守卫",
               'reducedMotion="user"' in app)
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    report.add("ui-v23 边界: viewport-fit=cover（safe-area 生效前提）", "viewport-fit=cover" in html)
    bg = (SRC / "components" / "ui" / "BackgroundArt.tsx").read_text(encoding="utf-8")
    report.add("ui-v23 边界: BackgroundArt 强度三档 + 纹理数组留位（P-21）",
               all(k in bg for k in ("immersive", "subtle", "none", "TEXTURES")))


# ── 回归 ──────────────────────────────────────────────────────────────

def regression_no_bare_palette(report: EvalReport) -> None:
    """R-C7/R-K2：裸调色板类零残留。"""
    offenders = sorted({m.group(0) for m in BARE_PALETTE_RE.finditer(_src_text())})
    report.add("ui-v23 回归: 裸调色板类 = 0（R-C7/R-K2）", not offenders,
               f"残余: {offenders[:8]}" if offenders else "")


def regression_no_banned_arbitrary(report: EvalReport) -> None:
    """已清零的任意值不得回潮（全量封禁随六档存量接线启用）。"""
    src = _src_text()
    for banned in ("text-[11px]", "text-[13px]", "rounded-[24px]"):
        report.add(f"ui-v23 回归: {banned} 不回潮", banned not in src)


def regression_gradient_converged(report: EvalReport) -> None:
    """R-I7：渐变只存登录页与教师侧栏 tile 两处。"""
    users = []
    for p in SRC.rglob("*.tsx"):
        if "btn-gradient" in p.read_text(encoding="utf-8"):
            users.append(p.relative_to(SRC).as_posix())
    ok = set(users) <= {"App.tsx", "pages/teacher/LoginPage.tsx"}
    report.add("ui-v23 回归: btn-gradient 仅存登录页与侧栏 tile（R-I7）", ok,
               f"出现处: {users}" if not ok else "")


def regression_token_lint_gate(report: EvalReport) -> None:
    """R-K2：token-lint（含类名守门）进程级全绿。"""
    proc = subprocess.run(["node", "scripts/token-lint.mjs"], cwd=str(FRONTEND),
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    report.add("ui-v23 回归: token-lint 全绿（R-C7/R-K2 类名+色值双层）", proc.returncode == 0,
               (proc.stdout + proc.stderr)[-400:] if proc.returncode else "")


def regression_dead_tokens_removed(report: EvalReport) -> None:
    """R-K3：死令牌区不复活。"""
    g = _globals()
    antd = (SRC / "components" / "antd" / "AntdProvider.tsx").read_text(encoding="utf-8")
    report.add("ui-v23 回归: .ai-panel-dark / DarkPanelProvider 不复活（R-K3）",
               ".ai-panel-dark" not in g and "DarkPanelProvider" not in antd)


# ── 运行器 ────────────────────────────────────────────────────────────

def run() -> EvalReport:
    report = EvalReport()
    print("\n" + "=" * 60)
    print("  UI v2.3 去 AI 味升级守门（基线/边界/回归，零浏览器）")
    print("=" * 60)
    baseline_semantic_layer(report)
    baseline_text_tiers(report)
    baseline_chem_glyph_honesty(report)
    baseline_motion_centralized(report)
    boundary_guards(report)
    regression_no_bare_palette(report)
    regression_no_banned_arbitrary(report)
    regression_gradient_converged(report)
    regression_token_lint_gate(report)
    regression_dead_tokens_removed(report)
    print(report.summary())
    return report


if __name__ == "__main__":
    rep = run()
    failures = sum(1 for r in rep.results if not r["passed"])
    sys.exit(1 if failures > 0 else 0)
