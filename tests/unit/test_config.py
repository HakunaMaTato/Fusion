from pathlib import Path

import pytest

from app.config import DEFAULT_SCORING_PATH, Settings, load_scoring_config


def test_chains_env_var_splits_on_comma(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAINS", "solana,base,bnb")
    monkeypatch.setenv("NANSEN_API_KEY", "test-key")

    settings = Settings(_env_file=None)

    assert settings.chains == ["solana", "base", "bnb"]


def test_chains_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAINS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.chains == ["solana", "base", "bnb"]


def test_scoring_config_loads_regardless_of_the_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    load_scoring_config.cache_clear()
    try:
        cfg = load_scoring_config()
    finally:
        load_scoring_config.cache_clear()

    assert DEFAULT_SCORING_PATH.is_absolute() and DEFAULT_SCORING_PATH.exists()
    assert cfg.scoring.green_min == 70
