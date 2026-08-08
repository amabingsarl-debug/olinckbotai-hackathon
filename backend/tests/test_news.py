from datetime import datetime, timedelta, timezone

from app.market.news import NewsCatalystService


def test_news_catalyst_scores_symbol_specific_headlines():
    items = [
        {
            "title": "Bitcoin ETF approval drives record inflow",
            "summary": "",
            "source": "test",
            "url": "https://example.test/btc",
            "published_at": datetime(2026, 7, 2, 12, tzinfo=timezone.utc).isoformat(),
        },
        {
            "title": "Ethereum exploit investigation follows outage",
            "summary": "",
            "source": "test",
            "url": "https://example.test/eth",
            "published_at": datetime(2026, 7, 2, 12, tzinfo=timezone.utc).isoformat(),
        },
    ]
    snapshot = NewsCatalystService.analyze_items(
        items,
        ["BTCUSDT", "ETHUSDT"],
        now=datetime(2026, 7, 2, 13, tzinfo=timezone.utc),
    )
    btc = next(row for row in snapshot["symbols"] if row["symbol"] == "BTCUSDT")
    eth = next(row for row in snapshot["symbols"] if row["symbol"] == "ETHUSDT")
    assert btc["stance"] == "positive"
    assert eth["stance"] == "negative"
    assert btc["score"] > eth["score"]


def test_news_catalyst_ignores_unmatched_assets():
    snapshot = NewsCatalystService.analyze_items(
        [{"title": "Gold market adoption rises", "summary": "", "source": "test", "url": ""}],
        ["BTCUSDT"],
    )
    btc = snapshot["symbols"][0]
    assert btc["stance"] == "neutral"
    assert btc["score"] == 0.0


def test_news_catalyst_ignores_stale_or_undated_items_for_trading_weight():
    now = datetime(2026, 7, 2, 13, tzinfo=timezone.utc)
    items = [
        {
            "title": "Bitcoin hack investigation triggers sell-off",
            "summary": "",
            "source": "test",
            "url": "https://example.test/stale",
            "published_at": (now - timedelta(hours=72)).isoformat(),
        },
        {
            "title": "Bitcoin ETF approval drives inflow",
            "summary": "",
            "source": "test",
            "url": "https://example.test/undated",
        },
    ]
    snapshot = NewsCatalystService.analyze_items(items, ["BTCUSDT"], now=now, max_age_hours=24)
    btc = snapshot["symbols"][0]
    assert btc["stance"] == "neutral"
    assert btc["score"] == 0.0
    assert snapshot["actionable_items"] == 0


def test_news_catalyst_weights_recent_items_more_than_old_items():
    now = datetime(2026, 7, 2, 13, tzinfo=timezone.utc)
    items = [
        {
            "title": "Bitcoin ETF approval drives inflow",
            "summary": "",
            "source": "test",
            "url": "https://example.test/recent",
            "published_at": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "title": "Bitcoin lawsuit investigation",
            "summary": "",
            "source": "test",
            "url": "https://example.test/old",
            "published_at": (now - timedelta(hours=20)).isoformat(),
        },
    ]
    snapshot = NewsCatalystService.analyze_items(items, ["BTCUSDT"], now=now, max_age_hours=24)
    btc = snapshot["symbols"][0]
    assert btc["score"] > 0
    assert snapshot["actionable_items"] == 2


def test_news_catalyst_deduplicates_same_source_headline():
    now = datetime(2026, 7, 2, 13, tzinfo=timezone.utc)
    item = {
        "title": "Bitcoin hack triggers market sell-off",
        "summary": "",
        "source": "https://www.coindesk.com/rss",
        "url": "https://www.coindesk.com/duplicate",
        "published_at": (now - timedelta(hours=1)).isoformat(),
    }
    snapshot = NewsCatalystService.analyze_items([item, item.copy()], ["BTCUSDT"], now=now)
    btc = snapshot["symbols"][0]
    assert snapshot["unique_events"] == 1
    assert snapshot["actionable_items"] == 1
    assert len(btc["headlines"]) == 1
    assert btc["decision_stance"] == "neutral"


def test_news_catalyst_requires_independent_confirmation_for_media_reports():
    now = datetime(2026, 7, 2, 13, tzinfo=timezone.utc)
    items = [
        {
            "title": "Bitcoin hack triggers major sell-off",
            "summary": "",
            "source": "https://www.coindesk.com/rss",
            "url": "https://www.coindesk.com/btc-hack",
            "published_at": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "title": "Major Bitcoin hack triggers sell-off",
            "summary": "",
            "source": "https://cointelegraph.com/rss",
            "url": "https://cointelegraph.com/btc-hack",
            "published_at": (now - timedelta(hours=2)).isoformat(),
        },
    ]
    snapshot = NewsCatalystService.analyze_items(items, ["BTCUSDT"], now=now)
    btc = snapshot["symbols"][0]
    assert snapshot["unique_events"] == 1
    assert snapshot["confirmed_events"] == 1
    assert btc["decision_stance"] == "negative"
    assert btc["headlines"][0]["confirmations"] == 2


def test_news_catalyst_accepts_single_official_source():
    now = datetime(2026, 7, 2, 13, tzinfo=timezone.utc)
    snapshot = NewsCatalystService.analyze_items(
        [{
            "title": "Bitcoin listing launch approved",
            "summary": "",
            "source": "https://www.binance.com/en/support/announcement",
            "url": "https://www.binance.com/announcement",
            "published_at": (now - timedelta(hours=1)).isoformat(),
        }],
        ["BTCUSDT"],
        now=now,
    )
    btc = snapshot["symbols"][0]
    assert snapshot["confirmed_events"] == 1
    assert btc["decision_stance"] == "positive"


def test_news_catalyst_neutralizes_confirmed_conflicting_events():
    now = datetime(2026, 7, 2, 13, tzinfo=timezone.utc)
    items = [
        {
            "title": "Bitcoin ETF approval drives record inflow",
            "summary": "",
            "source": "https://www.binance.com/announcement",
            "url": "https://www.binance.com/positive",
            "published_at": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "title": "Bitcoin major hack triggers crash and sell-off",
            "summary": "",
            "source": "https://www.coindesk.com/rss",
            "url": "https://www.coindesk.com/negative",
            "published_at": (now - timedelta(minutes=50)).isoformat(),
        },
        {
            "title": "Major Bitcoin hack triggers crash sell-off",
            "summary": "",
            "source": "https://cointelegraph.com/rss",
            "url": "https://cointelegraph.com/negative",
            "published_at": (now - timedelta(minutes=45)).isoformat(),
        },
    ]

    snapshot = NewsCatalystService.analyze_items(items, ["BTCUSDT"], now=now)
    btc = snapshot["symbols"][0]

    assert btc["decision_conflict"] is True
    assert btc["decision_stance"] == "neutral"
