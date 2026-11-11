from __future__ import annotations

import os

from zaya.config import Config, write_default_config


def test_defaults(isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml", env={"ZAYA_DATA_DIR": str(isolated_env / "data")}
    )
    assert cfg.agent.provider == "deepseek"
    assert cfg.agent.model == "deepseek-flash"
    assert cfg.agent.model_slug() == "deepseek/deepseek-flash"
    assert cfg.stt.language == "pt"
    assert cfg.stt.confirm_transcript is False
    assert cfg.audio.continuous is True
    assert cfg.audio.silence_ms == 5000
    assert cfg.audio.max_seconds == 0.0
    assert cfg.stt.partial_transcription is True
    assert cfg.tts.provider == "edge"
    assert cfg.secondbrain.confirm_create is False
    assert cfg.secondbrain.require_confirmation is True
    assert cfg.interface.concise is True
    assert cfg.proactive.enabled is True
    assert cfg.proactive.quiet_hours == "23:00-08:00"
    assert cfg.secondbrain.path.endswith("SecondBrain")
    assert cfg.preferences_path().name == "preferences.md"
    assert cfg.preferences_path().parent == cfg.second_brain_path()
    assert cfg.resolved_data_dir() == (isolated_env / "data").resolve()


def test_toml_and_env_precedence(isolated_env):
    config_path = isolated_env / "config.toml"
    config_path.write_text(
        """
[agent]
provider = "openai"
model = "gpt-x"
api_key = "segredo"

[tts]
provider = "say"
""",
        encoding="utf-8",
    )
    env = {
        "ZAYA_AGENT_MODEL": "gpt-y",
        "ZAYA_TTS_PROVIDER": "edge",
        "ZAYA_SECOND_BRAIN_PATH": str(isolated_env / "sb"),
        "ZAYA_SB_AUTO_CONTEXT": "false",
        "ZAYA_DATA_DIR": str(isolated_env / "data2"),
    }
    cfg = Config.load(config_path=config_path, env=env)
    assert cfg.agent.provider == "openai"
    assert cfg.agent.model == "gpt-y"
    assert cfg.tts.provider == "edge"
    assert cfg.secondbrain.path == str(isolated_env / "sb")
    assert cfg.secondbrain.auto_context is False
    assert cfg.public_dict()["agent"]["api_key"] == "***"


def test_config_antiga_migra_para_agent(isolated_env):
    config_path = isolated_env / "legacy.toml"
    config_path.write_text(
        """
[opencode]
model = "deepseek/deepseek-flash"
auto_approve = true
directory = "/tmp/proj"
""",
        encoding="utf-8",
    )
    cfg = Config.load(config_path=config_path, env={"ZAYA_DATA_DIR": str(isolated_env / "data")})
    assert cfg.agent.provider == "deepseek"
    assert cfg.agent.model == "deepseek-flash"
    assert cfg.agent.auto_approve is True
    assert cfg.agent.directory == "/tmp/proj"


def test_chave_do_ambiente_chega_ao_agente(isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml",
        env={
            "ZAYA_DATA_DIR": str(isolated_env / "data"),
            "DEEPSEEK_API_KEY": "sk-teste",
        },
    )
    assert cfg.agent.api_key == "sk-teste"
    assert cfg.public_dict()["agent"]["api_key"] == "***"


def test_env_types_are_inferred_from_model(isolated_env):
    env = {
        "ZAYA_UI": "terminal",  # string, antes era tratado como bool
        "ZAYA_UI_DASHBOARD_COLLAPSED": "false",
        "ZAYA_TELEGRAM_AUTO_APPROVE": "true",
        "ZAYA_AUDIO_SILENCE_MS": "1500",
        "ZAYA_SB_AUTO_CONTEXT": "yes",
        "ZAYA_FAST": "off",
        "ZAYA_DATA_DIR": str(isolated_env / "data"),
    }
    cfg = Config.load(config_path=isolated_env / "missing.toml", env=env)
    assert cfg.interface.ui == "terminal"
    assert cfg.interface.dashboard_collapsed is False
    assert cfg.telegram.auto_approve is True
    assert cfg.audio.silence_ms == 1500
    assert cfg.secondbrain.auto_context is True
    assert cfg.interface.fast is False


def test_dotenv_fica_visivel_para_subprocessos(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "ZAYA_TELEGRAM_BOT_TOKEN=abc123\nZAYA_TELEGRAM_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ZAYA_DOTENV", str(dotenv))
    monkeypatch.setenv("ZAYA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ZAYA_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ZAYA_TELEGRAM_ENABLED", raising=False)
    try:
        cfg = Config.load(config_path=tmp_path / "missing.toml")
        assert cfg.telegram.bot_token == "abc123"
        assert os.environ["ZAYA_TELEGRAM_BOT_TOKEN"] == "abc123"
        assert os.environ["ZAYA_TELEGRAM_ENABLED"] == "true"
    finally:
        os.environ.pop("ZAYA_TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("ZAYA_TELEGRAM_ENABLED", None)


def test_write_default_config(tmp_path):
    target = tmp_path / "cfg" / "config.toml"
    written = write_default_config(target)
    assert written == target
    text = written.read_text(encoding="utf-8")
    assert "[agent]" in text
    assert "deepseek-flash" in text

    written.write_text("# alterado\n", encoding="utf-8")
    write_default_config(target)
    assert written.read_text(encoding="utf-8") == "# alterado\n"
    write_default_config(target, force=True)
    assert "deepseek-flash" in written.read_text(encoding="utf-8")
