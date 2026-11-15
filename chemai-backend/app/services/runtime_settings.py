"""运行时开关（runtime-settings）—— 白名单旗子热切换，短 TTL 缓存。

设计要点（kb-ingest-ui 同款的纪律来自 qiuqiu ADR-0006 体系）：
- **白名单封闭**：只有「行为类 + 运营会想切 + 非凭据」的旗子才准入。
  新增可切换旗子 = 代码改动 + 在这里登记（有意的摩擦，防无治理配置存储）。
- **热生效**：修改写表 + 失效进程内缓存；读取走「缓存 → 表覆写 → env 种子默认」，
  TTL 约 5 秒（高频调用不做每请求查表）。
- **恢复默认 = 删覆写行**：env 以后被改时回落语义仍然正确（不冻结旧值）。
- 凭据类配置（API key/密钥）永不入此表。
"""
import threading
import time
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.database import RuntimeSettingRow

TTL_SECONDS = 5.0

# ── 白名单（唯一登记处）──────────────────────────────────────
# 判据：行为类（切换改变请求期行为）、运营想切（故障降级/成本控制）、非凭据。
WHITELIST: dict[str, dict[str, Any]] = {
    "OCR_SHEET_PROVIDER": {
        "type": "enum",
        "choices": ["paddle", "mineru", "baidu"],
        # 面向管理员的说明：这一屏必须让不懂技术的人看懂，否则等于没做
        "label": "答题卡识别引擎",
        "description": "批改答题卡时用哪个文字识别（OCR）服务。本地引擎免费但要装依赖；"
                       "复杂版式用 MinerU 更稳；百度云准确率高但需要配置密钥、按次计费。",
        "impact": "切换后新上传的答题卡用新引擎识别；已经识别完的批改结果不受影响。",
        "choice_labels": {
            "paddle": "本地 PaddleOCR（免费，需装依赖）",
            "mineru": "MinerU（复杂版式更强）",
            "baidu": "百度云 OCR（准确率高，需密钥、按次计费）",
        },
    },
    "OCR_VISION_REVIEW_ENABLED": {
        "type": "bool",
        "label": "识别结果视觉复核",
        "description": "当文字识别结果置信度低或有漏检时，再让视觉模型把答案核对一遍。"
                       "更准确，但每次复核都会多一次模型调用（有成本与延迟）。",
        "impact": "关闭后识别结果不再自动复核，个别错判可能漏过；已处理的结果不变。",
        "choice_labels": {"true": "开启（更准，有额外调用成本）", "false": "关闭（更快，可能漏判）"},
    },
}

_cache: dict[str, str] = {}
_cache_at: float = 0.0
_cache_guard = threading.Lock()
# db=None 时的会话工厂（测试注入点：默认主库；测试换内存库防写穿）
_session_factory = None


def _open_session() -> Session:
    from app.models.database import get_session_factory
    factory = _session_factory or get_session_factory
    return factory()()


def _env_default(key: str) -> str:
    """env 种子默认（从 app.config 的旗子单例读 —— 这三个旗子定义在那，保持单一语义）。"""
    from app.config import config
    return str(getattr(config, key))


def _coerce(key: str, raw: str) -> Any:
    spec = WHITELIST[key]
    if spec["type"] == "bool":
        return raw.lower() not in ("0", "false", "no")
    return raw


def invalidate() -> None:
    """测试与写路径共用的缓存失效。"""
    global _cache, _cache_at
    with _cache_guard:
        _cache = {}
        _cache_at = 0.0


def _overrides(db: Session) -> dict[str, str]:
    global _cache, _cache_at
    now = time.monotonic()
    with _cache_guard:
        if _cache and now - _cache_at < TTL_SECONDS:
            return _cache
    rows = {r.key: r.value for r in db.query(RuntimeSettingRow).all()}
    with _cache_guard:
        _cache = rows
        _cache_at = now
    return rows


def get_setting(key: str, db: Optional[Session] = None) -> Any:
    """读生效值：覆写 → env 默认。白名单外抛 ValueError。"""
    if key not in WHITELIST:
        raise ValueError(f"'{key}' 不在可切换白名单内")
    own = db
    close = False
    if own is None:
        own = _open_session()
        close = True
    try:
        raw = _overrides(own).get(key)
        return _coerce(key, raw) if raw is not None else _coerce(key, _env_default(key))
    finally:
        if close:
            own.close()


def validate_value(key: str, value: Any) -> str:
    """白名单 + 类型/枚举校验；返回规范化后的存储字符串。非法抛 ValueError。"""
    if key not in WHITELIST:
        raise ValueError(f"'{key}' 不在可切换白名单内")
    spec = WHITELIST[key]
    if spec["type"] == "bool":
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, str) and value.lower() in ("1", "true", "yes", "0", "false", "no"):
            return "1" if value.lower() in ("1", "true", "yes") else "0"
        raise ValueError(f"'{key}' 需要布尔值")
    # enum
    if value not in spec["choices"]:
        raise ValueError(f"'{key}' 合法取值：{spec['choices']}")
    return str(value)


def set_setting(key: str, value: Any, db: Session, operator: str = "") -> str:
    """覆写一个键（校验 + 条件写 + 失效缓存）。返回规范化存储值。"""
    stored = validate_value(key, value)
    row = db.get(RuntimeSettingRow, key)
    if row is None:
        row = RuntimeSettingRow(key=key)
        db.add(row)
    row.value = stored
    row.updated_by = operator
    row.updated_at = __import__("datetime").datetime.utcnow()
    db.commit()
    invalidate()
    return stored


def reset_setting(key: str, db: Session) -> None:
    """恢复默认 = 删覆写行（env 以后被改也回落正确）。"""
    if key not in WHITELIST:
        raise ValueError(f"'{key}' 不在可切换白名单内")
    row = db.get(RuntimeSettingRow, key)
    if row is not None:
        db.delete(row)
        db.commit()
    invalidate()


def all_settings(db: Session) -> list[dict]:
    """管理页列表：键、类型、生效值、默认值、是否覆写。"""
    overrides = _overrides(db)
    out = []
    for key, spec in WHITELIST.items():
        raw = overrides.get(key)
        out.append({
            "key": key,
            "type": spec["type"],
            "choices": spec.get("choices"),
            # 人话说明（管理页直接渲染；缺了这些字段的旧版本会退化成只显示 key）
            "label": spec.get("label") or key,
            "description": spec.get("description") or "",
            "impact": spec.get("impact") or "",
            "choice_labels": spec.get("choice_labels") or {},
            "effective": _coerce(key, raw) if raw is not None else _coerce(key, _env_default(key)),
            "default": _coerce(key, _env_default(key)),
            "overridden": raw is not None,
        })
    return out
