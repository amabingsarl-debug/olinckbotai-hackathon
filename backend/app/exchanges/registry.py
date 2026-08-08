from app.exchanges.base import ExchangeClient
from app.exchanges.binance import BinanceSpotClient
from app.exchanges.gateio import GateIoSpotClient


def get_exchange(name: str) -> ExchangeClient:
    clients = {
        "binance": BinanceSpotClient,
        "gateio": GateIoSpotClient,
    }
    try:
        return clients[name.lower()]()
    except KeyError as exc:
        raise ValueError(f"Unsupported exchange: {name}") from exc
