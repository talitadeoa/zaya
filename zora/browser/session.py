"""Automacao de navegador com Playwright e perfil persistente (cookies/logins)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BrowserError(RuntimeError):
    pass


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    return True


class BrowserSession:
    def __init__(
        self,
        profile_dir: str | Path,
        headless: bool = True,
        timeout_ms: int = 30000,
    ):
        self.profile_dir = Path(profile_dir).expanduser()
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None

    async def __aenter__(self) -> BrowserSession:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    @property
    def page(self) -> Any:
        if self._page is None:
            raise BrowserError("navegador nao iniciado")
        return self._page

    async def start(self) -> None:
        if not playwright_available():
            raise BrowserError(
                "Playwright nao instalado. Rode: "
                ".venv/bin/pip install playwright && .venv/bin/playwright install chromium"
            )
        from playwright.async_api import async_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._pw = await async_playwright().start()
            self._context = await self._pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
            )
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else await self._context.new_page()
            )
            self._page.set_default_timeout(self.timeout_ms)
        except Exception as exc:
            await self.close()
            raise BrowserError(f"nao foi possivel abrir o navegador: {exc}") from exc

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> str:
        await self.page.goto(url, wait_until=wait_until)
        return await self.text()

    async def title(self) -> str:
        return await self.page.title()

    async def text(self) -> str:
        return await self.page.inner_text("body")

    async def html(self) -> str:
        return await self.page.content()

    async def screenshot(self, path: str | Path, full_page: bool = True) -> Path:
        out = Path(path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(out), full_page=full_page)
        return out

    async def click(self, selector: str) -> None:
        await self.page.click(selector)

    async def fill(self, selector: str, value: str) -> None:
        await self.page.fill(selector, value)

    async def press(self, selector: str, key: str) -> None:
        await self.page.press(selector, key)

    async def close(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception as exc:
                logger.debug("falha ao fechar o contexto do navegador: %s", exc)
            self._context = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception as exc:
                logger.debug("falha ao encerrar o Playwright: %s", exc)
            self._pw = None
        self._page = None
