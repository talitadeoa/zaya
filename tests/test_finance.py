from __future__ import annotations

import httpx
import pytest

from zora.finance.quotes import Quote, fetch_quote, format_quote, normalize_ticker


def test_normaliza_ticker():
    assert normalize_ticker("petr4") == "PETR4.SA"
    assert normalize_ticker("PETR4") == "PETR4.SA"
    assert normalize_ticker("PETR4.SA") == "PETR4.SA"
    assert normalize_ticker("AAPL") == "AAPL"
    assert normalize_ticker("^BVSP") == "^BVSP"


def test_format_quote():
    quote = Quote("PETR4.SA", 40.0, 39.0, "BRL", 2.564)
    text = format_quote(quote)
    assert "PETR4.SA" in text
    assert "40.00" in text
    assert "+2.56%" in text


@pytest.mark.asyncio
async def test_fetch_quote_parseia_yahoo():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "symbol": "PETR4.SA",
                                "regularMarketPrice": 40.0,
                                "currency": "BRL",
                            },
                            "indicators": {"quote": [{"close": [38.0, 39.0, 40.0]}]},
                        }
                    ]
                }
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    quote = await fetch_quote("PETR4", http=http)
    await http.aclose()
    assert quote.symbol == "PETR4.SA"
    assert quote.price == 40.0
    assert quote.change_pct == pytest.approx(2.5641, rel=1e-3)
