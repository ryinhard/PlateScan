"""LINE / Telegram adapter 共用的最小共用模組。"""

from pydantic import BaseModel


class WebhookAck(BaseModel):
    """Webhook 接收成功後的統一回應格式。"""

    status: str
    platform: str
