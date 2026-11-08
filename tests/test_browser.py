from __future__ import annotations

import pytest

import zora.browser.session as browser_session
from zora.browser.session import BrowserError, BrowserSession, playwright_available
from zora.config import Config


def test_playwright_available_retorna_bool():
    assert isinstance(playwright_available(), bool)


def test_perfil_padrao_do_navegador():
    assert Config().browser_profile_dir().name == "browser"


@pytest.mark.asyncio
async def test_erro_claro_sem_playwright(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_session, "playwright_available", lambda: False)
    session = BrowserSession(tmp_path / "perfil")
    with pytest.raises(BrowserError):
        await session.start()
