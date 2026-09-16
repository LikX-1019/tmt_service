"""拼多多网页客服连接器导出。"""

from app.integrations.pdd.base import (
    BrowserMessage,
    ConnectorError,
    ConnectorStatus,
    ConnectorStatusSnapshot,
    CustomerServiceConnector,
    SendReceipt,
    SendUncertainError,
)
from app.integrations.pdd.playwright_connector import PddPlaywrightConnector

__all__ = [
    "BrowserMessage",
    "ConnectorError",
    "ConnectorStatus",
    "ConnectorStatusSnapshot",
    "CustomerServiceConnector",
    "PddPlaywrightConnector",
    "SendReceipt",
    "SendUncertainError",
]
