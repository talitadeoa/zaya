"""Construção do parser de argumentos da CLI."""

from __future__ import annotations

import argparse

from zora import __version__


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
        help="abre a interface textual (padrão)",
    )
    parser.add_argument(
        "-old",
        "--old",
        action="store_true",
        help="abre a interface antiga (terminal)",
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
