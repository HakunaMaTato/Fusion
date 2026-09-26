import pytest

from app.config import Settings


def test_chains_env_var_splits_on_comma(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAINS", "solana,base,bnb")
    monkeypatch.setenv("NANSEN_API_KEY", "test-key")

    settings = Settings()

    assert settings.chains == ["solana", "base", "bnb"]


def test_chains_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAINS", raising=False)

    settings = Settings()

    assert settings.chains == ["solana", "base", "bnb"]
