from __future__ import annotations

from datetime import date

from zaya.briefing.market import MarketRow, market_due, market_message
from zaya.finance.quotes import Quote

DAY = date(2026, 11, 9)  # segunda-feira


def test_market_due_respeita_dias_e_janela():
    days = [0, 2, 4]
    assert market_due("11:59", "12:00", "18:00", 0, days) is False
    assert market_due("12:00", "12:00", "18:00", 0, days) is True
    assert market_due("12:00", "12:00", "18:00", 1, days) is False
    assert market_due("17:59", "12:00", "18:00", 2, days) is True
    assert market_due("18:00", "12:00", "18:00", 4, days) is False


def test_market_message_formata_ativos_brasileiros_e_externos():
    rows = [
        MarketRow(
            "BB",
            "BBAS3.SA",
            Quote("BBAS3.SA", 21.3, 21.0, "BRL", 1.428),
        ),
        MarketRow(
            "Nvidia",
            "NVDA",
            Quote("NVDA", 185.5, 190.0, "USD", -2.368),
        ),
        MarketRow(
            "Ibovespa",
            "^BVSP",
            Quote("^BVSP", 128450.0, 129000.0, "BRL", -0.426),
        ),
    ]

    text = market_message(DAY, rows)

    assert text.startswith("Bolsa — segunda, 09/11")
    assert "BB (BBAS3.SA): R$ 21,30 (+1,43%)" in text
    assert "Nvidia (NVDA): US$ 185,50 (-2,37%)" in text
    assert "Ibovespa (^BVSP): 128.450,00 pts (-0,43%)" in text
    assert "Melhor: BB +1,43%" in text
    assert "Pior: Nvidia -2,37%" in text


def test_market_message_mostra_ativos_sem_cotacao():
    rows = [MarketRow("Tesla", "TSLA", None)]

    text = market_message(DAY, rows)

    assert "Tesla (TSLA): cotação indisponível" in text
    assert "Melhor:" not in text


def test_market_message_com_carteira_vazia():
    text = market_message(DAY, [])

    assert "Carteira vazia" in text
    assert "/carteira add" in text
