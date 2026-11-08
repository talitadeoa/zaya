from __future__ import annotations

import pytest
from textual.widgets import Input

from zora.interface.textual_app import ZoraTextualApp


@pytest.mark.asyncio
async def test_app_textual_responde_sem_erro():
    async def answer(text: str, sink) -> str:
        sink.token(f"eco: {text}")
        return f"eco: {text}"

    app = ZoraTextualApp(answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        field = app.query_one("#input", Input)
        field.value = "oi"
        await pilot.press("enter")
        await pilot.pause()
    assert True
