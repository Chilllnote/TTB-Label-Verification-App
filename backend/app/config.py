"""Runtime configuration helpers."""

import os
from pathlib import Path

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOTENV_PATH = PROJECT_ROOT / ".env"

DOTENV_ONLY_SETTINGS = {
    "OPENAI_VISION_MODEL",
    "OPENAI_TIMEOUT_SECONDS",
    "OPENAI_IMAGE_DETAIL",
    "PREPROCESS_MAX_DIMENSION",
    "PREPROCESS_JPEG_QUALITY",
    "PREPROCESS_GRAYSCALE",
    "PREPROCESS_THRESHOLD",
    "PREPROCESS_CONTRAST",
    "BATCH_CONCURRENCY",
}


def runtime_setting(name: str) -> str:
    """Read runtime tuning from .env when present, otherwise process env.

    The listed tuning settings are intentionally sourced from the project .env
    file for local runs. Railway does not receive .env, so deployed containers
    still read these values from service environment variables.
    """
    if DOTENV_PATH.exists() and name in DOTENV_ONLY_SETTINGS:
        value = dotenv_values(DOTENV_PATH).get(name)
        if value is None or not str(value).strip():
            raise ValueError(f"{name} must be set in {DOTENV_PATH}")
        return str(value)

    value = os.getenv(name)
    if value is None or not str(value).strip():
        raise ValueError(f"{name} must be set")
    return str(value)


def runtime_float(name: str, minimum: float, maximum: float) -> float:
    raw_value = runtime_setting(name)
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc

    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def runtime_int(name: str, minimum: int, maximum: int) -> int:
    raw_value = runtime_setting(name)
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def runtime_bool(name: str) -> bool:
    raw_value = runtime_setting(name).strip().lower()
    if raw_value in {"true", "1", "yes", "on"}:
        return True
    if raw_value in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def runtime_choice(name: str, allowed: set[str]) -> str:
    value = runtime_setting(name).strip().lower()
    if value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {allowed_values}")
    return value
