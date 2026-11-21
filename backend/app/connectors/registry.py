"""Maps `connector:` names in sources.yaml to classes."""

from __future__ import annotations

from app.config.loader import SourceConfig
from app.connectors.base import Connector
from app.connectors.demo import DemoConnector
from app.connectors.json_api import (
    FinancialTimesConnector,
    JsonApiConnector,
    ReutersConnectConnector,
)
from app.connectors.rss import RssConnector
from app.connectors.scrapling_connector import ScraplingConnector

CONNECTORS: dict[str, type[Connector]] = {
    "demo": DemoConnector,
    "rss": RssConnector,
    "json_api": JsonApiConnector,
    "reuters_connect": ReutersConnectConnector,
    "ft": FinancialTimesConnector,
    "scrapling": ScraplingConnector,
}


def build_connector(config: SourceConfig) -> Connector:
    return CONNECTORS[config.connector](config)
