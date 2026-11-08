from __future__ import annotations

from pathlib import Path

import pytest

from zora.config import Config, SecondBrainConfig
from zora.secondbrain.index import SecondBrainIndex
from zora.secondbrain.notes import SecondBrain


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Isola config/dados do ambiente real do usuario."""
    monkeypatch.setenv("ZORA_DOTENV", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("ZORA_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture
def cfg(tmp_path, isolated_env):
    return Config.load(
        config_path=tmp_path / "nao-existe.toml", env={"ZORA_DATA_DIR": str(tmp_path / "data")}
    )


@pytest.fixture
def brain(tmp_path):
    root = tmp_path / "brain"
    (root / "Inbox").mkdir(parents=True)
    cfg = SecondBrainConfig(path=str(root), default_folder="Inbox")
    index = SecondBrainIndex(cfg, tmp_path / "sb.db")
    instance = SecondBrain(cfg, index)
    yield instance
    index.close()


def write_note(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
