from __future__ import annotations

from zora.config import Config, write_default_config


def test_defaults(isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml", env={"ZORA_DATA_DIR": str(isolated_env / "data")}
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
        "ZORA_OPENCODE_MODEL": "deepseek/deepseek-v4-pro",
        "ZORA_TTS_PROVIDER": "edge",
        "ZORA_SECOND_BRAIN_PATH": str(isolated_env / "sb"),
        "ZORA_SB_AUTO_CONTEXT": "false",
        "ZORA_DATA_DIR": str(isolated_env / "data2"),
    }
    cfg = Config.load(config_path=config_path, env=env)
    assert cfg.opencode.model == "deepseek/deepseek-v4-pro"
    assert cfg.tts.provider == "edge"
    assert cfg.secondbrain.path == str(isolated_env / "sb")
    assert cfg.secondbrain.auto_context is False
    assert cfg.opencode.server_password == "segredo"
    assert cfg.public_dict()["opencode"]["server_password"] == "***"


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
