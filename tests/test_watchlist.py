from __future__ import annotations

from zora.finance.watchlist import Watchlist


def test_adiciona_e_persiste(tmp_path):
    path = tmp_path / "watchlist.json"
    store = Watchlist(path)
    store.add("Petrobras", "petr4")
    store.add("Apple", "AAPL")
    assert [item.ticker for item in store.all()] == ["PETR4", "AAPL"]

    again = Watchlist(path)
    assert [(item.label, item.ticker) for item in again.all()] == [
        ("Petrobras", "PETR4"),
        ("Apple", "AAPL"),
    ]


def test_atualiza_label_e_remove(tmp_path):
    store = Watchlist(tmp_path / "watchlist.json")
    store.add("Antigo", "PETR4")
    store.add("Petrobras", "PETR4")
    assert len(store.all()) == 1
    assert store.all()[0].label == "Petrobras"
    assert store.remove("petr4") is True
    assert store.all() == []
    assert store.remove("PETR4") is False
