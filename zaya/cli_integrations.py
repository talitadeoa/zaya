"""Comandos de integração: WhatsApp, Telegram, Spotify e navegador."""

from __future__ import annotations

import asyncio
import logging

from zaya.browser.session import BrowserError, BrowserSession, playwright_available
from zaya.cli_runtime import _build_app
from zaya.config import Config
from zaya.interface.terminal import TerminalUI
from zaya.spotify.controller import SpotifyCommand, SpotifyError
from zaya.spotify.controller import execute as execute_spotify
from zaya.telegram.client import TelegramClient
from zaya.telegram.service import TelegramService
from zaya.whatsapp.client import WhatsAppClient
from zaya.whatsapp.webhook import IncomingMessage, WebhookServer


async def _whatsapp_command(cfg: Config, args, ui: TerminalUI) -> None:
    client = WhatsAppClient(cfg.whatsapp)
    try:
        if not client.enabled:
            ui.error("WhatsApp desativado ou sem credenciais.")
            ui.hint(
                "defina ZAYA_WHATSAPP_ENABLED=true, ZAYA_WHATSAPP_PHONE_NUMBER_ID, "
                "ZAYA_WHATSAPP_ACCESS_TOKEN e ZAYA_WHATSAPP_RECIPIENT em ~/.config/zaya/.env"
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
                "defina ZAYA_TELEGRAM_ENABLED=true e ZAYA_TELEGRAM_BOT_TOKEN em ~/.config/zaya/.env"
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
        logging.getLogger("zaya.telegram"),
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
        ui.error("defina ZAYA_WHATSAPP_VERIFY_TOKEN para validar o webhook.")
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
