from __future__ import annotations

from zaya.interface.terminal import TerminalUI


def test_terminal_quiet_recusa_por_padrao():
    ui = TerminalUI(quiet=True)
    assert ui.permission("write", ["a.txt"]) == "reject"


def test_terminal_delega_para_a_interface_grafica():
    ui = TerminalUI(quiet=True)
    ui.permission_callback = lambda tool, resources: "always"
    assert ui.permission("write", ["a.txt"]) == "always"
