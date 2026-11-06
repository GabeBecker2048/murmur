from murmur.sources.yahoo_screener import parse_screen
from tests.conftest import load_fixture


def test_parse_real_payload_keeps_major_exchanges_in_cap_order():
    cands = parse_screen(load_fixture("yahoo_screener.json"))
    symbols = [c.symbol for c in cands]
    assert symbols[:4] == ["AAPL", "NVDA", "MSFT", "GOOGL"]
    assert "BRK-B" in symbols and "TSM" in symbols
    assert all(c.market_cap > 0 for c in cands)
    assert cands == sorted(cands, key=lambda c: -c.market_cap)
    assert cands[0].name == "Apple Inc."


def test_parse_filters():
    payload = {
        "quotes": [
            {
                "symbol": "OTC",
                "shortName": "Pink",
                "marketCap": 1e12,
                "exchange": "PNK",
                "quoteType": "EQUITY",
            },
            {
                "symbol": "ETF",
                "shortName": "Fund",
                "marketCap": 1e12,
                "exchange": "NYQ",
                "quoteType": "ETF",
            },
            {
                "symbol": "NOCAP",
                "shortName": "x",
                "marketCap": None,
                "exchange": "NYQ",
                "quoteType": "EQUITY",
            },
            {
                "symbol": "ok",
                "shortName": "Fine",
                "marketCap": 5e9,
                "exchange": "ASE",
                "quoteType": "EQUITY",
            },
        ]
    }
    cands = parse_screen(payload)
    assert [c.symbol for c in cands] == ["OK"]
    assert cands[0].name == "Fine"
