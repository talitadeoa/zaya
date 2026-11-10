"""Comandos locais da CLI: config, index, search, note e doctor."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from zaya.audio.recorder import Recorder
from zaya.config import Config, write_default_config
from zaya.interface.terminal import TerminalUI
from zaya.opencode.client import OpenCodeClient, OpenCodeError, load_provider_models
from zaya.secondbrain.index import SecondBrainIndex
from zaya.secondbrain.notes import SecondBrain


def _config_command(cfg: Config, ui: TerminalUI, init: bool, force: bool) -> None:
    if init:
        path = write_default_config(Path(cfg.config_file) if cfg.config_file else None, force=force)
        ui.success(f"config criado em {path}")
        return
    ui.info(f"config: {cfg.config_file or '(nenhum arquivo; usando defaults)'}")
    ui.info(f"dados: {cfg.resolved_data_dir()}")
    ui.console.print(json.dumps(cfg.public_dict(), indent=2, ensure_ascii=False))


def _index_command(cfg: Config, ui: TerminalUI, force: bool) -> None:
    index = SecondBrainIndex(cfg.secondbrain, cfg.index_path())
    brain = SecondBrain(cfg.secondbrain, index)
    try:
        ui.info(f"indexando {brain.root} ...")
        stats = index.reindex(force=force)
        ui.success(json.dumps(stats, ensure_ascii=False))
    finally:
        index.close()


def _ensure_indexed(index: SecondBrainIndex, cfg: Config) -> None:
    root = cfg.second_brain_path()
    if index.count() == 0 and root.exists():
        index.reindex()


def _search_command(cfg: Config, ui: TerminalUI, query: str) -> None:
    index = SecondBrainIndex(cfg.secondbrain, cfg.index_path())
    try:
        _ensure_indexed(index, cfg)
        hits = index.search(query, limit=8)
        ui.search_results(hits)
    finally:
        index.close()


def _note_command(cfg: Config, ui: TerminalUI, name: str) -> None:
    index = SecondBrainIndex(cfg.secondbrain, cfg.index_path())
    brain = SecondBrain(cfg.secondbrain, index)
    try:
        _ensure_indexed(index, cfg)
        ui.print_note(brain.read_note(name))
    finally:
        index.close()


async def _doctor(cfg: Config, ui: TerminalUI, verbose: bool) -> None:
    console = ui.console
    console.print(f"[bold magenta]ZAYA doctor[/] · dados em {cfg.resolved_data_dir()}")

    py_ok = sys.version_info[:2] == (3, 12)
    ui.check(
        py_ok,
        f"Python {sys.version_info.major}.{sys.version_info.minor}",
        "" if py_ok else "recomendado 3.12 (wheels de faster-whisper/ctranslate2)",
    )

    cfg_path = Path(cfg.config_file) if cfg.config_file else None
    ui.check(
        bool(cfg_path and cfg_path.is_file()),
        "arquivo de config",
        str(cfg_path or "usando defaults"),
    )
    ui.check(cfg.resolved_data_dir().is_dir(), "diretório de dados", str(cfg.resolved_data_dir()))

    binary = shutil.which("opencode")
    ui.check(bool(binary), "binário opencode", binary or "não encontrado")

    client = OpenCodeClient(cfg, cfg.project_directory())
    try:
        try:
            await client.ensure_server()
            health = await client.health()
            ui.check(bool(health), "servidor OpenCode", f"{client.endpoint} · {health}")
            info = await client.path_info()
            if info:
                same = Path(info.get("directory", "")).resolve() == cfg.project_directory()
                ui.check(same, "diretório do servidor", info.get("directory", "?"))
            try:
                models = await load_provider_models(client)
                configured = cfg.opencode.model
                ui.check(
                    configured in models,
                    f"modelo {configured}",
                    "disponível" if configured in models else "não listado",
                )
                if verbose and models:
                    sample = sorted(m for m in models if "deepseek" in m)
                    if sample:
                        console.print(f"  modelos deepseek: {', '.join(sample)}")
            except OpenCodeError as exc:
                ui.check(False, "lista de modelos", str(exc))
        except OpenCodeError as exc:
            ui.check(False, "servidor OpenCode", str(exc))
    finally:
        await client.aclose()

    ui.check(bool(shutil.which("ffmpeg")), "ffmpeg", shutil.which("ffmpeg") or "não encontrado")
    ui.check(bool(shutil.which("afplay")), "afplay", shutil.which("afplay") or "não encontrado")
    ui.check(bool(shutil.which("say")), "say", shutil.which("say") or "não encontrado")

    audio_ok, audio_msg = Recorder.available()
    ui.check(audio_ok, "microfone (sounddevice/PortAudio)", audio_msg)

    try:
        import faster_whisper  # noqa: F401

        ui.check(True, "faster-whisper", "importável")
    except Exception as exc:
        ui.check(False, "faster-whisper", str(exc))

    try:
        import edge_tts  # noqa: F401

        ui.check(True, "edge-tts", "importável")
    except Exception as exc:
        ui.check(False, "edge-tts", str(exc))

    root = cfg.second_brain_path()
    ui.check(root.exists(), "Second Brain", str(root) if root.exists() else f"não existe: {root}")
    if root.exists():
        index = SecondBrainIndex(cfg.secondbrain, cfg.index_path())
        try:
            ui.check(True, "índice do Second Brain", f"{index.count()} nota(s) indexada(s)")
        finally:
            index.close()
