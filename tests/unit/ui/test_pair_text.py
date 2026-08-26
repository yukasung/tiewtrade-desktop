from tiewtrade.ui.pair_text import pair_text


def test_a_market_reads_as_the_two_assets_it_trades() -> None:
    assert pair_text("BTCUSDT") == "BTC/USDT"
    assert pair_text("ETHUSDT") == "ETH/USDT"
    assert pair_text("BNBUSDT") == "BNB/USDT"


def test_a_symbol_that_is_not_quoted_in_the_quote_asset_is_left_alone() -> None:
    """Splitting at a guess would invent a pair that was never traded."""
    assert pair_text("BTCTHB") == "BTCTHB"
    assert pair_text("USDT") == "USDT"
    assert pair_text("") == ""
