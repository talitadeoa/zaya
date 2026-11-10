from __future__ import annotations

from zaya.config import Config, write_default_config


def test_defaults(isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml", env={"ZAYA_DATA_DIR": str(isolated_env / "data")}
    )
    assert cfg.opencode.model == "deepseek/deepseek-flash"
    assert cfg.opencode.agent == "build"
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
[opencode]
model = "openai/gpt-x"
server_password = "segredo"

[tts]
provider = "say"
""",
        encoding="utf-8",
    )
    env = {
        "ZAYA_OPENCODE_MODEL": "deepseek/deepseek-v4-pro",
        "ZAYA_TTS_PROVIDER": "edge",
        "ZAYA_SECOND_BRAIN_PATH": str(isolated_env / "sb"),
        "ZAYA_SB_AUTO_CONTEXT": "false",
        "ZAYA_DATA_DIR": str(isolated_env / "data2"),
    }
    cfg = Config.load(config_path=config_path, env=env)
    assert cfg.opencode.model == "deepseek/deepseek-v4-pro"
    assert cfg.tts.provider == "edge"
    assert cfg.secondbrain.path == str(isolated_env / "sb")
    assert cfg.secondbrain.auto_context is False
    assert cfg.opencode.server_password == "segredo"
    assert cfg.public_dict()["opencode"]["server_password"] == "***"


def test_env_types_are_inferred_from_model(isolated_env):
    env = {
        "ZAYA_UI": "terminal",  # string, antes era tratado como bool
        "ZAYA_AUDIO_SILENCE_MS": "1500",
        "ZAYA_SB_AUTO_CONTEXT": "yes",
        "ZAYA_FAST": "off",
        "ZAYA_DATA_DIR": str(isolated_env / "data"),
    }
    cfg = Config.load(config_path=isolated_env / "missing.toml", env=env)
    assert cfg.interface.ui == "terminal"
    assert cfg.audio.silence_ms == 1500
    assert cfg.secondbrain.auto_context is True
    assert cfg.interface.fast is False


def test_write_default_config(tmp_path):
    target = tmp_path / "cfg" / "config.toml"
    written = write_default_config(target)
    assert written == target
    text = written.read_text(encoding="utf-8")
    assert "[opencode]" in text
    assert "deepseek/deepseek-flash" in text

    written.write_text("# alterado\n", encoding="utf-8")
    write_default_config(target)
    assert written.read_text(encoding="utf-8") == "# alterado\n"
    write_default_config(target, force=True)
    assert "deepseek/deepseek-flash" in written.read_text(encoding="utf-8")
