"""Runtime da CLI: monta o app, roda as interfaces e o modo watch."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from zaya.audio.recorder import Recorder
from zaya.config import Config
from zaya.controller.orchestrator import Orchestrator
from zaya.interface.terminal import TerminalUI
from zaya.logging_setup import setup_logging
from zaya.memory.store import MemoryStore
from zaya.opencode.client import OpenCodeClient
from zaya.secondbrain.index import SecondBrainIndex
from zaya.secondbrain.notes import SecondBrain
from zaya.stt.faster_whisper import WhisperTranscriber
from zaya.tts.manager import TTSManager


def _build_app(cfg: Config, verbose: bool, voice: bool = True, quiet: bool = False):
    logger = setup_logging(cfg.log_dir(), verbose=verbose)
    ui = TerminalUI(quiet=quiet)
    store = MemoryStore(cfg.db_path())
    index = SecondBrainIndex(cfg.secondbrain, cfg.index_path())
    brain = SecondBrain(cfg.secondbrain, index)
    if not voice:
        cfg.tts.enabled = False
    tts = TTSManager(cfg.tts, logger, cfg.resolved_data_dir() / "tmp")
    transcriber = WhisperTranscriber(cfg.stt)
    recorder = Recorder(cfg.audio)
    client = OpenCodeClient(cfg, cfg.project_directory())

    orchestrator = Orchestrator(
        cfg=cfg,
        logger=logger,
        ui=ui,
        store=store,
        index=index,
        brain=brain,
        tts=tts,
        transcriber=transcriber,
        recorder=recorder,
        client=client,
    )
    return orchestrator, store, index


async def _run_app(cfg: Config, verbose: bool) -> None:
    if cfg.interface.ui == "textual":
        await _run_textual(cfg, verbose)
        return
    orchestrator, store, index = _build_app(cfg, verbose)
    try:
        await orchestrator.start()
    finally:
        store.close()
        index.close()
    if orchestrator.restart_requested:
        _reexec()


async def _run_textual(cfg: Config, verbose: bool) -> None:
    from zaya.interface.textual_app import ZayaTextualApp

    orchestrator, store, index = _build_app(cfg, verbose, quiet=True)
    await orchestrator.prepare()

    async def answer(text: str, sink) -> str:
        return await orchestrator.answer_stream(text, sink)

    async def cleanup() -> None:
        await orchestrator.aclose()

    app = ZayaTextualApp(
        answer, cleanup=cleanup, subtitle=cfg.opencode.model, voice=orchestrator
    )
    result = None
    try:
        result = await app.run_async()
    finally:
        store.close()
        index.close()
    if result == 42:
        _reexec()


def _reexec() -> None:
    os.execv(
        sys.executable,
        [sys.executable, "-m", "zaya", *sys.argv[1:]],
    )


def _code_snapshot() -> dict[str, float]:
    root = Path(__file__).resolve().parent
    snapshot: dict[str, float] = {}
    for path in root.rglob("*.py"):
        try:
            snapshot[str(path)] = path.stat().st_mtime
        except OSError:
            continue
    return snapshot


def _watch_mode() -> None:
    """Roda a Zaya e reinicia o processo quando o código-fonte muda."""
    args = [arg for arg in sys.argv[1:] if arg != "--watch"]
    cmd = [sys.executable, "-m", "zaya", *args]
    child = subprocess.Popen(cmd)
    snapshot = _code_snapshot()
    try:
        while True:
            time.sleep(1.0)
            code = child.poll()
            if code is not None:
                if code == 0:
                    return
                print(f"[watch] processo saiu com código {code}; reiniciando…")
                child = subprocess.Popen(cmd)
                snapshot = _code_snapshot()
                continue
            current = _code_snapshot()
            if current != snapshot:
                time.sleep(1.0)
                print("[watch] código alterado; reiniciando…")
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                child = subprocess.Popen(cmd)
                snapshot = _code_snapshot()
    except KeyboardInterrupt:
        pass
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()


async def _ask_command(cfg: Config, args, verbose: bool, no_voice: bool) -> None:
    orchestrator, store, index = _build_app(cfg, verbose, voice=not no_voice)
    try:
        await orchestrator.run_once(" ".join(args.message))
    finally:
        store.close()
        index.close()
