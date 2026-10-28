"""Input validation, stable identities and reproducible time handling."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("时间必须包含时区，例如 2026-10-29T18:00:00+08:00")
    return dt


def validate_request(raw: dict) -> tuple[dict, list[str]]:
    if not isinstance(raw, dict):
        raise ValueError("输入必须是 JSON 对象")
    allowed = {"request_id", "theme", "budget", "deadline", "preferences"}
    extra = set(raw) - allowed
    if extra:
        raise ValueError("未知输入字段: " + ", ".join(sorted(extra)))
    request_id = raw.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
        raise ValueError("request_id 必须为 1–64 位字母、数字、下划线或连字符")
    missing = [key for key in ("theme", "budget", "deadline") if raw.get(key) in (None, "")]
    theme = raw.get("theme")
    if theme is not None and (not isinstance(theme, str) or len(theme) > 300):
        raise ValueError("theme 必须是最多 300 字的字符串")
    if isinstance(theme, str) and not theme.strip() and "theme" not in missing:
        missing.append("theme")
    budget = raw.get("budget")
    if "budget" not in missing and (isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or budget < 0 or budget > 1000000):
        raise ValueError("budget 必须是 0–1000000 之间的有限数字")
    if "deadline" not in missing:
        if not isinstance(raw["deadline"], str):
            raise ValueError("deadline 必须是带时区的 ISO 8601 字符串")
        parse_time(raw["deadline"])
    if not isinstance(raw.get("preferences", ""), str) or len(raw.get("preferences", "")) > 1000:
        raise ValueError("preferences 必须是最多 1000 字的字符串")
    return dict(raw), missing
