"""Lista local de ativos acompanhados."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class WatchItem:
    label: str
    ticker: str


def default_watchlist_path() -> Path:
    env = os.environ.get("ZORA_WATCHLIST_PATH")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "zora" / "watchlist.json"


class Watchlist:
    def __init__(self, path: str | Path | None = None):
        self.path = (
            Path(path).expanduser() if path else default_watchlist_path()
        )
        self._items: list[WatchItem] = self._load()

    def _load(self) -> list[WatchItem]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("items") if isinstance(data, dict) else data
        result: list[WatchItem] = []
        for item in items or []:
            try:
                result.append(WatchItem(**item))
            except TypeError:
                continue
        return result

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"items": [asdict(item) for item in self._items]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def all(self) -> list[WatchItem]:
        return list(self._items)

    def add(self, label: str, ticker: str) -> WatchItem:
        ticker = ticker.strip().upper()
        label = (label or ticker).strip()
        if not ticker:
            raise ValueError("ticker vazio")
        for item in self._items:
            if item.ticker == ticker:
                item.label = label
                self._save()
                return item
        item = WatchItem(label=label, ticker=ticker)
        self._items.append(item)
        self._save()
        return item

    def remove(self, ticker: str) -> bool:
        ticker = ticker.strip().upper()
        before = len(self._items)
        self._items = [item for item in self._items if item.ticker != ticker]
        if len(self._items) != before:
            self._save()
            return True
        return False
