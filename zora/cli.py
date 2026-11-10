"""CLI da Zaia: `zora`, `zora doctor`, `zora config`, `zora index`, ..."""

from __future__ import annotations

import asyncio

from zora.cli_commands import (
    _config_command,
    _doctor,
    _index_command,
    _note_command,
    _search_command,
)
from zora.cli_integrations import (
    _browser_command,
    _spotify_command,
    _telegram_command,
    _whatsapp_command,
)
from zora.cli_parser import build_parser
from zora.cli_runtime import _ask_command, _run_app, _watch_mode
from zora.config import Config
from zora.interface.terminal import TerminalUI
from zora.opencode.client import OpenCodeError


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
    if args.old:
        cfg.interface.ui = "terminal"
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
