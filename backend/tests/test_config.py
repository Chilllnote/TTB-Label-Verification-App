import pytest

from app import config


def test_dotenv_only_setting_prefers_dotenv_over_process_env(monkeypatch, tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("OPENAI_VISION_MODEL=from-dotenv\n")
    monkeypatch.setattr(config, "DOTENV_PATH", dotenv_path)
    monkeypatch.setenv("OPENAI_VISION_MODEL", "from-process-env")

    assert config.runtime_setting("OPENAI_VISION_MODEL") == "from-dotenv"


def test_dotenv_only_setting_requires_value_when_dotenv_exists(monkeypatch, tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("")
    monkeypatch.setattr(config, "DOTENV_PATH", dotenv_path)
    monkeypatch.setenv("OPENAI_IMAGE_DETAIL", "high")

    with pytest.raises(ValueError, match="OPENAI_IMAGE_DETAIL must be set"):
        config.runtime_setting("OPENAI_IMAGE_DETAIL")


def test_dotenv_only_setting_uses_process_env_when_dotenv_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DOTENV_PATH", tmp_path / "missing.env")
    monkeypatch.setenv("BATCH_CONCURRENCY", "4")

    assert config.runtime_int("BATCH_CONCURRENCY", 1, 5) == 4


def test_runtime_setting_requires_process_env_when_dotenv_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DOTENV_PATH", tmp_path / "missing.env")
    monkeypatch.delenv("OPENAI_TIMEOUT_SECONDS", raising=False)

    with pytest.raises(ValueError, match="OPENAI_TIMEOUT_SECONDS must be set"):
        config.runtime_setting("OPENAI_TIMEOUT_SECONDS")


def test_runtime_int_rejects_invalid_value(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DOTENV_PATH", tmp_path / "missing.env")
    monkeypatch.setenv("BATCH_CONCURRENCY", "not-a-number")

    with pytest.raises(ValueError, match="BATCH_CONCURRENCY must be an integer"):
        config.runtime_int("BATCH_CONCURRENCY", 1, 5)


def test_runtime_float_rejects_out_of_range_value(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DOTENV_PATH", tmp_path / "missing.env")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "0.5")

    with pytest.raises(ValueError, match="OPENAI_TIMEOUT_SECONDS must be between"):
        config.runtime_float("OPENAI_TIMEOUT_SECONDS", 1.0, 30.0)
