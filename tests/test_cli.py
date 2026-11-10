"""Caracteriza o parser e os módulos extraídos da CLI."""

from __future__ import annotations

from zaya.cli_parser import build_parser


def test_parser_ask_command():
    args = build_parser().parse_args(["ask", "resuma", "o", "dia", "--no-voice"])
    assert args.command == "ask"
    assert args.message == ["resuma", "o", "dia"]
    assert args.no_voice is True


def test_parser_whatsapp_serve_defaults():
    args = build_parser().parse_args(["whatsapp", "serve", "--port", "9000"])
    assert args.command == "whatsapp"
    assert args.whatsapp_command == "serve"
    assert args.port == 9000
    assert args.host is None


def test_parser_top_level_flags():
    args = build_parser().parse_args(["--ui", "textual", "--verbose"])
    assert args.ui == "textual"
    assert args.verbose is True
    assert args.command is None


def test_textual_modules_expose_public_api():
    from zaya.interface.textual_app import _HELP, TextualSink, ZayaTextualApp
    from zaya.interface.textual_render import _welcome_art, card
    from zaya.interface.textual_screens import CommandPalette, SessionPicker

    assert ZayaTextualApp and TextualSink and _HELP
    assert card and _welcome_art
    assert CommandPalette and SessionPicker


def test_welcome_art_wordmark_cabe_na_largura():
    from zaya.interface.textual_render import _welcome_art

    art = _welcome_art(78)
    assert art
    assert len(art) <= 8
    largura = max(sum(len(texto) for texto, _ in segments) for segments in art)
    assert largura <= 78
