"""경로·설정·투자 스타일."""
from __future__ import annotations

import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
OUTPUTS = DATA / "outputs"
HISTORY = DATA / "history"
STATIC = ROOT / "static"
TRACKER_ALL = REPO / "docs" / "data" / "all.json"

for _d in (DATA, CACHE, OUTPUTS, HISTORY):
    _d.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA / "config.json"
SECRET_KEYS = ("anthropic_key", "dart_key", "fred_key", "datago_key")

DEFAULTS = {
    "style": "balanced",
    "anthropic_key": "",
    "dart_key": "",
    "fred_key": "",
    "datago_key": "",
    # SEC는 자동 접속 시 연락처가 담긴 User-Agent를 요구한다. 본인 이메일을 넣는 것이 원칙.
    "sec_contact": "",
    "ai_model": "claude-opus-5-5",
}

# 투자 스타일: 시총 하한(한국 억원 / 미국 억달러), 업종당 종목 수, 4개 축 가중치
STYLES = {
    "balanced": {
        "label": "균형형", "kr_mcap": 2000, "us_mcap": 20, "per_sector": 12,
        "w": {"quiet": 0.30, "smart": 0.25, "rev": 0.25, "cat": 0.20},
    },
    "aggressive": {
        "label": "공격형", "kr_mcap": 500, "us_mcap": 3, "per_sector": 14,
        "w": {"quiet": 0.20, "smart": 0.25, "rev": 0.20, "cat": 0.35},
    },
    "conservative": {
        "label": "보수형", "kr_mcap": 10000, "us_mcap": 100, "per_sector": 8,
        "w": {"quiet": 0.30, "smart": 0.20, "rev": 0.35, "cat": 0.15},
    },
}

_lock = threading.Lock()


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    if cfg.get("style") not in STYLES:
        cfg["style"] = "balanced"
    return cfg


def save(patch: dict) -> dict:
    with _lock:
        cfg = load()
        for k, v in patch.items():
            if k not in DEFAULTS or not isinstance(v, str):
                continue
            v = v.strip()
            # 화면에는 키를 가려서 보내므로, 가려진 값이 그대로 돌아오면 기존 값을 유지한다
            if k in SECRET_KEYS and v.startswith("••"):
                continue
            cfg[k] = v
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        return cfg


def public(cfg: dict) -> dict:
    out = dict(cfg)
    for k in SECRET_KEYS:
        out[k] = ("••••" + cfg[k][-4:]) if cfg.get(k) else ""
    return out


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
