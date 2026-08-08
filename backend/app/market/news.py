from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings


SYMBOL_ALIASES = {
    "BTCUSDT": ("BTC", "Bitcoin"),
    "ETHUSDT": ("ETH", "Ethereum"),
    "SOLUSDT": ("SOL", "Solana"),
    "XRPUSDT": ("XRP", "Ripple"),
    "BNBUSDT": ("BNB", "Binance Coin"),
    "DOGEUSDT": ("DOGE", "Dogecoin"),
    "TRXUSDT": ("TRX", "Tron"),
    "ZECUSDT": ("ZEC", "Zcash"),
}

BULLISH_KEYWORDS = {
    "approval": 2.0,
    "approved": 2.0,
    "etf": 1.4,
    "inflow": 1.3,
    "listing": 1.6,
    "launch": 1.2,
    "partnership": 1.3,
    "adoption": 1.4,
    "upgrade": 1.0,
    "accumulation": 1.1,
    "record high": 1.5,
    "rate cut": 1.0,
}

BEARISH_KEYWORDS = {
    "hack": -2.5,
    "exploit": -2.5,
    "delisting": -2.2,
    "lawsuit": -1.8,
    "ban": -1.8,
    "outage": -1.3,
    "investigation": -1.3,
    "liquidation": -1.1,
    "crash": -1.5,
    "sell-off": -1.4,
}

SOURCE_RELIABILITY = {
    "binance.com": 1.25,
    "gate.com": 1.25,
    "sec.gov": 1.2,
    "federalreserve.gov": 1.2,
    "coindesk.com": 0.9,
    "cointelegraph.com": 0.8,
}
OFFICIAL_SOURCE_THRESHOLD = 1.15
EVENT_SIMILARITY_THRESHOLD = 0.55
TITLE_STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "is", "of", "on", "or", "the", "to", "with",
    "after", "amid", "crypto", "cryptocurrency", "market", "markets", "price", "says",
}


class NewsCatalystService:
    async def snapshot(self, symbols: list[str], limit_per_feed: int = 20, max_age_hours: int = 24) -> dict:
        items = await self._fetch_items(limit_per_feed)
        return self.analyze_items(items, symbols, max_age_hours=max_age_hours)

    async def _fetch_items(self, limit_per_feed: int) -> list[dict]:
        items = []
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            for url in get_settings().news_rss_feeds:
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    items.extend(self._parse_rss(response.text, url)[:limit_per_feed])
                except httpx.HTTPError:
                    continue
        return items

    @classmethod
    def analyze_items(cls, items: list[dict], symbols: list[str], max_age_hours: int = 24, now: datetime | None = None) -> dict:
        current_time = now or datetime.now(timezone.utc)
        per_symbol = {
            symbol: {"score": 0.0, "decision_score": 0.0, "positive": 0, "negative": 0, "confirmed_events": 0, "confirmed_positive": 0, "confirmed_negative": 0, "headlines": []}
            for symbol in symbols
        }
        events = cls._group_events(items, symbols, current_time, max_age_hours)
        actionable_items = sum(event["item_count"] for event in events)
        confirmed_events = 0
        for event in events:
            item = event["primary"]
            text = f"{item.get('title', '')} {item.get('summary', '')}"
            score = cls._text_score(text)
            freshness = event["freshness"]
            weighted_score = score * freshness["weight"] * event["reliability"]
            decision_ready = event["official"] or event["source_count"] >= 2
            confirmed_events += int(decision_ready)
            for symbol in event["symbols"]:
                row = per_symbol[symbol]
                row["score"] += weighted_score
                row["decision_score"] += weighted_score if decision_ready else 0.0
                row["positive"] += int(weighted_score > 0)
                row["negative"] += int(weighted_score < 0)
                row["confirmed_events"] += int(decision_ready)
                row["confirmed_positive"] += int(decision_ready and weighted_score > 0)
                row["confirmed_negative"] += int(decision_ready and weighted_score < 0)
                if len(row["headlines"]) < 5:
                    row["headlines"].append({
                        "title": item.get("title"),
                        "source": item.get("source"),
                        "url": item.get("url"),
                        "published_at": item.get("published_at"),
                        "age_hours": freshness["age_hours"],
                        "actionable": freshness["actionable"],
                        "score": round(weighted_score, 2),
                        "reliability": event["reliability"],
                        "confirmations": event["source_count"],
                        "decision_ready": decision_ready,
                        "source_tier": "official" if event["official"] else ("confirmed_media" if event["source_count"] >= 2 else "unconfirmed_media"),
                    })
        ranked = []
        for symbol, row in per_symbol.items():
            score = round(row["score"], 2)
            decision_score = round(row["decision_score"], 2)
            conflict = bool(row["confirmed_positive"] and row["confirmed_negative"])
            if score >= 2:
                stance = "positive"
            elif score <= -2:
                stance = "negative"
            else:
                stance = "neutral"
            if conflict:
                decision_stance = "neutral"
            elif decision_score >= 2:
                decision_stance = "positive"
            elif decision_score <= -2:
                decision_stance = "negative"
            else:
                decision_stance = "neutral"
            ranked.append({
                **row,
                "symbol": symbol,
                "score": score,
                "stance": stance,
                "decision_score": decision_score,
                "decision_stance": decision_stance,
                "decision_conflict": conflict,
            })
        ranked.sort(key=lambda row: row["score"], reverse=True)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": get_settings().news_rss_feeds,
            "items_scanned": len(items),
            "actionable_items": actionable_items,
            "unique_events": len(events),
            "confirmed_events": confirmed_events,
            "max_age_hours": max_age_hours,
            "symbols": ranked,
            "market_bias": cls._market_bias(ranked, score_field="decision_score"),
        }

    @classmethod
    def _group_events(cls, items: list[dict], symbols: list[str], now: datetime, max_age_hours: int) -> list[dict]:
        events: list[dict] = []
        seen_exact: set[tuple[str, str]] = set()
        for item in items:
            text = f"{item.get('title', '')} {item.get('summary', '')}"
            matched_symbols = cls._symbols_in_text(text, symbols)
            score = cls._text_score(text)
            freshness = cls._freshness(item.get("published_at"), now, max_age_hours)
            if not matched_symbols or score == 0 or not freshness["actionable"]:
                continue
            host = cls._source_host(item.get("source") or item.get("url") or "")
            normalized_title = cls._normalized_title(item.get("title", ""))
            exact_key = (host, normalized_title)
            if exact_key in seen_exact:
                continue
            seen_exact.add(exact_key)
            tokens = cls._event_tokens(item.get("title", ""))
            polarity = 1 if score > 0 else -1
            event = next((candidate for candidate in events if (
                candidate["polarity"] == polarity
                and set(candidate["symbols"]) == set(matched_symbols)
                and cls._jaccard(tokens, candidate["tokens"]) >= EVENT_SIMILARITY_THRESHOLD
            )), None)
            reliability = cls._source_reliability(host)
            if event is None:
                events.append({
                    "primary": item,
                    "symbols": matched_symbols,
                    "tokens": tokens,
                    "polarity": polarity,
                    "hosts": {host},
                    "source_count": 1,
                    "item_count": 1,
                    "freshness": freshness,
                    "reliability": reliability,
                    "official": reliability >= OFFICIAL_SOURCE_THRESHOLD,
                })
                continue
            event["hosts"].add(host)
            event["source_count"] = len(event["hosts"])
            event["item_count"] += 1
            event["official"] = event["official"] or reliability >= OFFICIAL_SOURCE_THRESHOLD
            if reliability > event["reliability"]:
                event["primary"] = item
                event["freshness"] = freshness
                event["reliability"] = reliability
        return events

    @staticmethod
    def _normalized_title(title: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", title.lower()))

    @classmethod
    def _event_tokens(cls, title: str) -> set[str]:
        return {token for token in cls._normalized_title(title).split() if token not in TITLE_STOPWORDS and len(token) > 1}

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    @staticmethod
    def _source_host(value: str) -> str:
        candidate = value if "://" in value else f"https://{value}"
        return (urlparse(candidate).hostname or value).lower().removeprefix("www.")

    @staticmethod
    def _source_reliability(host: str) -> float:
        return next((weight for domain, weight in SOURCE_RELIABILITY.items() if host == domain or host.endswith(f".{domain}")), 1.0)

    @staticmethod
    def _parse_rss(xml_text: str, source: str) -> list[dict]:
        root = ET.fromstring(xml_text)
        items = []
        for item in root.findall(".//item"):
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            description = item.findtext("description") or ""
            published_at = NewsCatalystService._published_at(item)
            items.append({
                "title": title.strip(),
                "summary": description.strip(),
                "url": link.strip(),
                "source": source,
                "published_at": published_at,
            })
        return items

    @staticmethod
    def _published_at(item: ET.Element) -> str | None:
        raw = item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date")
        if not raw:
            return None
        try:
            value = parsedate_to_datetime(raw) if "," in raw else datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, IndexError):
            return None

    @staticmethod
    def _freshness(published_at: str | None, now: datetime, max_age_hours: int) -> dict:
        if not published_at:
            return {"actionable": False, "weight": 0.0, "age_hours": None}
        try:
            published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError:
            return {"actionable": False, "weight": 0.0, "age_hours": None}
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age = max(0.0, (now - published.astimezone(timezone.utc)).total_seconds() / 3600)
        if age > max_age_hours:
            return {"actionable": False, "weight": 0.0, "age_hours": round(age, 2)}
        half_life = max(max_age_hours / 2, 1)
        weight = max(0.25, 1 - age / (half_life * 2))
        return {"actionable": True, "weight": round(weight, 4), "age_hours": round(age, 2)}

    @staticmethod
    def _symbols_in_text(text: str, symbols: list[str]) -> list[str]:
        matches = []
        for symbol in symbols:
            aliases = SYMBOL_ALIASES.get(symbol, (symbol.replace("USDT", ""),))
            if any(re.search(rf"\b{re.escape(alias)}\b", text, flags=re.IGNORECASE) for alias in aliases):
                matches.append(symbol)
        return matches

    @staticmethod
    def _text_score(text: str) -> float:
        normalized = text.lower()
        score = 0.0
        for keyword, weight in BULLISH_KEYWORDS.items():
            if keyword in normalized:
                score += weight
        for keyword, weight in BEARISH_KEYWORDS.items():
            if keyword in normalized:
                score += weight
        return score

    @staticmethod
    def _market_bias(rows: list[dict], score_field: str = "score") -> str:
        total = sum(float(row[score_field]) for row in rows)
        if total >= 3:
            return "risk_on"
        if total <= -3:
            return "risk_off"
        return "neutral"
