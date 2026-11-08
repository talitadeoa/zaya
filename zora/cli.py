"""CLI da Zora: `zora`, `zora doctor`, `zora config`, `zora index`, ..."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from zora import __version__
from zora.audio.recorder import Recorder
from zora.browser.session import BrowserError, BrowserSession, playwright_available
from zora.config import Config, write_default_config
from zora.controller.orchestrator import Orchestrator
from zora.interface.terminal import TerminalUI
from zora.logging_setup import setup_logging
from zora.memory.store import MemoryStore
from zora.opencode.client import OpenCodeClient, OpenCodeError, load_provider_models
from zora.secondbrain.index import SecondBrainIndex
from zora.secondbrain.notes import SecondBrain
from zora.spotify.controller import SpotifyCommand, SpotifyError, execute as execute_spotify
from zora.stt.faster_whisper import WhisperTranscriber
from zora.telegram.client import TelegramClient
from zora.telegram.service import TelegramService
from zora.tts.manager import TTSManager
from zora.whatsapp.client import WhatsAppClient
from zora.whatsapp.webhook import IncomingMessage, WebhookServer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zora",
        description="Zora: assistente pessoal de voz integrada ao OpenCode e ao Second Brain.",
    )
    parser.add_argument("--config", help="caminho do config TOML", default=None)
    parser.add_argument("--verbose", action="store_true", help="logs também no terminal")
    parser.add_argument(
        "--ui",
        choices=["terminal", "textual"],
        help="interface: terminal (padrão) ou textual",
    )
    parser.add_argument(
        "-tui",
        "--tui",
        action="store_true",
        help="atalho para abrir a interface textual",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="reinicia sozinha quando arquivos .py da Zora mudam",
    )
    parser.add_argument("--version", action="version", version=f"zora {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="diagnostica ambiente, servidor, voz e Second Brain")

    config_parser = sub.add_parser("config", help="mostra ou cria a configuração")
    config_parser.add_argument("--init", action="store_true", help="cria config padrão")
    config_parser.add_argument("--force", action="store_true", help="sobrescreve config")

    index_parser = sub.add_parser("index", help="reindexa o Second Brain")
    index_parser.add_argument("--force", action="store_true", help="reindexa tudo")

    search_parser = sub.add_parser("search", help="pesquisa no Second Brain")
    search_parser.add_argument("query", nargs="+")

    note_parser = sub.add_parser("note", help="lê uma nota do Second Brain")
    note_parser.add_argument("name", nargs="+")

    ask_parser = sub.add_parser("ask", help="executa uma instrução única e encerra")
    ask_parser.add_argument("message", nargs="+")
    ask_parser.add_argument("--no-voice", action="store_true", help="não fala a resposta")

    whatsapp_parser = sub.add_parser("whatsapp", help="integração com o WhatsApp Cloud API")
    whatsapp_sub = whatsapp_parser.add_subparsers(dest="whatsapp_command")
    wa_send = whatsapp_sub.add_parser("send", help="envia uma mensagem de texto")
    wa_send.add_argument("message", nargs="+")
    wa_send.add_argument("--to", help="número de destino (padrão: whatsapp.recipient)")
    wa_audio = whatsapp_sub.add_parser("audio", help="envia um arquivo de áudio")
    wa_audio.add_argument("path")
    wa_audio.add_argument("--to", help="número de destino (padrão: whatsapp.recipient)")
    wa_serve = whatsapp_sub.add_parser("serve", help="sobe o webhook para receber mensagens")
    wa_serve.add_argument("--host")
    wa_serve.add_argument("--port", type=int)

    telegram_parser = sub.add_parser("telegram", help="integração com o Telegram")
    telegram_sub = telegram_parser.add_subparsers(dest="telegram_command")
    tg_send = telegram_sub.add_parser("send", help="envia uma mensagem de texto")
    tg_send.add_argument("message", nargs="+")
    tg_send.add_argument("--chat", help="chat de destino (padrão: telegram.allowed_chat_id)")
    tg_audio = telegram_sub.add_parser("audio", help="envia um arquivo de áudio")
    tg_audio.add_argument("path")
    tg_audio.add_argument("--chat", help="chat de destino")
    tg_audio.add_argument("--voice", action="store_true", help="envia como mensagem de voz")
    tg_updates = telegram_sub.add_parser("updates", help="busca atualizações uma vez")
    tg_updates.add_argument("--offset", type=int)
    tg_updates.add_argument("--timeout", type=int)
    tg_poll = telegram_sub.add_parser("poll", help="fica ouvindo mensagens (long polling)")
    tg_poll.add_argument("--echo", action="store_true", help="responde ecoando o texto")
    telegram_sub.add_parser("serve", help="escuta e executa as mensagens na Zora")
    telegram_sub.add_parser("me", help="mostra dados do bot")

    spotify_parser = sub.add_parser("spotify", help="controla o Spotify no macOS")
    spotify_sub = spotify_parser.add_subparsers(dest="spotify_command")
    for name in ("status", "toggle", "play", "pause", "next", "prev"):
        spotify_sub.add_parser(name)
    spotify_volume = spotify_sub.add_parser("volume", help="define o volume (0-100)")
    spotify_volume.add_argument("value", type=int)

    browser_parser = sub.add_parser("browser", help="automação de navegador (Playwright)")
    browser_sub = browser_parser.add_subparsers(dest="browser_command")
    for name in ("open", "text", "html"):
        item = browser_sub.add_parser(name)
        item.add_argument("url")
        item.add_argument("--headless", action="store_true")
        item.add_argument("--headed", action="store_true")
    browser_shot = browser_sub.add_parser("screenshot")
    browser_shot.add_argument("url")
    browser_shot.add_argument("path")
    browser_shot.add_argument("--headless", action="store_true")
    browser_shot.add_argument("--headed", action="store_true")
    browser_login = browser_sub.add_parser("login", help="abre o navegador para login persistente")
    browser_login.add_argument("url")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.watch:
        _watch_mode()
        return
    cfg = Config.load(args.config)
    if args.ui:
        cfg.interface.ui = args.ui
    if args.tui:
        cfg.interface.ui = "textual"
    ui = TerminalUI()

    try:
        if args.command == "doctor":
            asyncio.run(_doctor(cfg, ui, args.verbose))
        elif args.command == "config":
            _config_command(cfg, ui, args.init, args.force)
        elif args.command == "index":
            _index_command(cfg, ui, args.force)
        elif args.command == "search":
            _search_command(cfg, ui, " ".join(args.query))
        elif args.command == "note":
            _note_command(cfg, ui, " ".join(args.name))
        elif args.command == "ask":
            asyncio.run(_ask_command(cfg, args, args.verbose, args.no_voice))
        elif args.command == "whatsapp":
            asyncio.run(_whatsapp_command(cfg, args, ui))
        elif args.command == "telegram":
            asyncio.run(_telegram_command(cfg, args, ui))
        elif args.command == "spotify":
            _spotify_command(args, ui)
        elif args.command == "browser":
            asyncio.run(_browser_command(cfg, args, ui))
        else:
            asyncio.run(_run_app(cfg, args.verbose))
    except KeyboardInterrupt:
        ui.info("até logo.")
    except OpenCodeError as exc:
        ui.error(str(exc))
        raise SystemExit(1) from None
    except Exception as exc:  # pragma: no cover - fallback amigavel
        if args.verbose:
            raise
        ui.error(f"{type(exc).__name__}: {exc}")
        ui.hint("rode com --verbose para detalhes ou `zora doctor`")
        raise SystemExit(1) from None


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
    from zora.interface.textual_app import ZoraTextualApp

    orchestrator, store, index = _build_app(cfg, verbose, quiet=True)
    await orchestrator.prepare()

    async def answer(text: str, sink) -> str:
        return await orchestrator.answer_stream(text, sink)

    async def cleanup() -> None:
        await orchestrator.aclose()

    app = ZoraTextualApp(
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
        [sys.executable, "-m", "zora", *sys.argv[1:]],
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
    """Roda a Zora e reinicia o processo quando o código-fonte muda."""
    args = [arg for arg in sys.argv[1:] if arg != "--watch"]
    cmd = [sys.executable, "-m", "zora", *args]
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


async def _whatsapp_command(cfg: Config, args, ui: TerminalUI) -> None:
    client = WhatsAppClient(cfg.whatsapp)
    try:
        if not client.enabled:
            ui.error("WhatsApp desativado ou sem credenciais.")
            ui.hint(
                "defina ZORA_WHATSAPP_ENABLED=true, ZORA_WHATSAPP_PHONE_NUMBER_ID, "
                "ZORA_WHATSAPP_ACCESS_TOKEN e ZORA_WHATSAPP_RECIPIENT em ~/.config/zora/.env"
            )
            return
        if args.whatsapp_command in {None, "status"}:
            ui.info(f"WhatsApp ativo · número de envio: {cfg.whatsapp.phone_number_id}")
            ui.info(f"destinatário padrão: {cfg.whatsapp.recipient or '(não definido)'}")
        elif args.whatsapp_command == "send":
            result = await client.send_text(" ".join(args.message), to=args.to)
            ui.success(f"mensagem enviada: {_message_id(result)}")
        elif args.whatsapp_command == "audio":
            result = await client.send_audio(args.path, to=args.to)
            ui.success(f"áudio enviado: {_message_id(result)}")
        elif args.whatsapp_command == "serve":
            _serve_whatsapp(cfg, ui, args)
    finally:
        await client.aclose()


def _message_id(result: dict) -> str:
    messages = result.get("messages") or []
    if messages and isinstance(messages[0], dict):
        return str(messages[0].get("id") or "?")
    return "?"


async def _telegram_command(cfg: Config, args, ui: TerminalUI) -> None:
    client = TelegramClient(cfg.telegram)
    try:
        if not client.enabled:
            ui.error("Telegram desativado ou sem token.")
            ui.hint(
                "defina ZORA_TELEGRAM_ENABLED=true e ZORA_TELEGRAM_BOT_TOKEN em ~/.config/zora/.env"
            )
            return
        command = args.telegram_command
        if command in {None, "me"}:
            me = await client.get_me()
            ui.info(f"bot: @{me.get('username', '?')} ({me.get('first_name', '')})")
        elif command == "send":
            result = await client.send_message(" ".join(args.message), chat_id=args.chat)
            ui.success(f"mensagem enviada: {result.get('message_id', '?')}")
        elif command == "audio":
            result = await client.send_audio(
                args.path, chat_id=args.chat, as_voice=args.voice
            )
            ui.success(f"áudio enviado: {result.get('message_id', '?')}")
        elif command == "updates":
            updates = await client.get_updates(offset=args.offset, timeout=args.timeout)
            for update in updates:
                ui.info(_telegram_summary(update))
        elif command == "poll":
            await _telegram_poll(client, ui, echo=args.echo)
        elif command == "serve":
            await _telegram_serve(cfg, ui, args, client)
    finally:
        await client.aclose()


def _telegram_summary(update: dict) -> str:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = message.get("text") or message.get("caption") or ""
    if message.get("voice"):
        kind = "voz"
    elif message.get("audio"):
        kind = "áudio"
    else:
        kind = "texto"
    who = chat.get("first_name") or chat.get("title") or chat.get("username") or "?"
    return f"chat {chat.get('id', '?')} · {who}: [{kind}] {text}"


async def _telegram_poll(client: TelegramClient, ui: TerminalUI, echo: bool = False) -> None:
    offset: int | None = None
    ui.info("ouvindo o Telegram (Ctrl+C para parar)…")
    while True:
        updates = await client.get_updates(offset=offset)
        for update in updates:
            offset = int(update.get("update_id", 0)) + 1
            message = update.get("message") or {}
            chat_id = (message.get("chat") or {}).get("id")
            text = message.get("text") or ""
            if text or message.get("voice") or message.get("audio"):
                ui.info(_telegram_summary(update))
            if echo and text and chat_id is not None:
                await client.send_message(f"recebi: {text}", chat_id=chat_id)
        await asyncio.sleep(0.1)


async def _telegram_serve(cfg: Config, ui: TerminalUI, args, client: TelegramClient) -> None:
    orchestrator, store, index = _build_app(cfg, args.verbose, voice=True, quiet=True)
    service = TelegramService(
        cfg,
        orchestrator,
        client,
        orchestrator.transcriber,
        ui,
        logging.getLogger("zora.telegram"),
    )
    try:
        await service.run()
    finally:
        await orchestrator.aclose()
        store.close()
        index.close()


def _spotify_command(args, ui: TerminalUI) -> None:
    actions = {
        "status": "status",
        "toggle": "toggle",
        "play": "play",
        "pause": "pause",
        "next": "next",
        "prev": "previous",
    }
    name = args.spotify_command or "status"
    command = (
        SpotifyCommand("volume_set", int(args.value))
        if name == "volume"
        else SpotifyCommand(actions.get(name, "status"))
    )
    try:
        ui.success(execute_spotify(command))
    except SpotifyError as exc:
        ui.error(str(exc))
        if "nao instalado" in str(exc):
            ui.hint("instale com: brew install --cask spotify")


async def _browser_command(cfg: Config, args, ui: TerminalUI) -> None:
    if not playwright_available():
        ui.error("Playwright nao instalado.")
        ui.hint(
            ".venv/bin/pip install playwright && .venv/bin/playwright install chromium"
        )
        return
    headless = cfg.browser.headless
    if args.browser_command == "login":
        headless = False
    elif getattr(args, "headed", False):
        headless = False
    elif getattr(args, "headless", False):
        headless = True
    session = BrowserSession(
        cfg.browser_profile_dir(),
        headless=headless,
        timeout_ms=cfg.browser.timeout_ms,
    )
    try:
        async with session:
            if args.browser_command == "login":
                await session.goto(args.url)
                ui.info("Faça login na janela do navegador.")
                ui.hint("quando terminar, volte aqui e pressione Enter para salvar a sessão")
                await asyncio.to_thread(input)
                ui.success("sessão salva no perfil do navegador")
            elif args.browser_command == "open":
                await session.goto(args.url)
                ui.success(f"{await session.title()} — {args.url}")
            elif args.browser_command == "text":
                text = await session.goto(args.url)
                ui.console.print(text)
            elif args.browser_command == "html":
                await session.goto(args.url)
                ui.console.print(await session.html())
            elif args.browser_command == "screenshot":
                await session.goto(args.url)
                path = await session.screenshot(args.path)
                ui.success(f"captura salva em {path}")
    except BrowserError as exc:
        ui.error(str(exc))


def _serve_whatsapp(cfg: Config, ui: TerminalUI, args) -> None:
    if not cfg.whatsapp.verify_token:
        ui.error("defina ZORA_WHATSAPP_VERIFY_TOKEN para validar o webhook.")
        return
    host = args.host or cfg.whatsapp.webhook_host
    port = args.port or cfg.whatsapp.webhook_port

    def on_message(message: IncomingMessage) -> None:
        body = message.text or message.media_id
        print(f"[whatsapp] {message.kind} de {message.sender}: {body}")

    server = WebhookServer(cfg.whatsapp.verify_token, on_message, host, port)
    ui.info(f"webhook escutando em http://{host}:{port}/webhook")
    ui.hint("exponha essa porta com um túnel HTTPS (ex.: ngrok/cloudflared) para a Meta.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


# --------------------------------------------------------------------- commands
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
    console.print(f"[bold magenta]ZORA doctor[/] · dados em {cfg.resolved_data_dir()}")

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
