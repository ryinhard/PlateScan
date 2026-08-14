"""集中管理環境變數的設定物件。

所有後端敏感金鑰（Gemini Key, LINE Token, Admin Sheet ID 等）皆須從此處讀取，
禁止在程式碼其他位置直接 hardcode 或另行呼叫 os.getenv()。
"""

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """應用程式設定，從 .env 檔案與環境變數載入。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # --- LINE Bot 憑證（M1 必要） ---
    line_channel_secret: str
    line_channel_access_token: str

    # --- Telegram Bot 憑證（M1 必要） ---
    telegram_bot_token: str
    telegram_webhook_secret: str

    # --- 以下欄位 M1 尚未使用，先宣告為 Optional，待對應里程碑實作時再收緊為必要 ---

    # M4（Gemini Vision 解析）使用
    gemini_api_key: Optional[str] = None

    # M2/M5（Google Sheets 讀寫）使用。
    # 注意：此變數同時也會被 Google client library（google-auth 等）隱性自動讀取，
    # 這裡重複宣告是為了在 config.py 中維持單一事實來源與文件化用途，並非衝突。
    google_application_credentials: Optional[str] = None

    # M2（管理用 Google Sheet，users & buffer 工作表）使用
    admin_sheet_id: Optional[str] = None

    # M8（「圖表」/「分析」指令組出 PWA 連結）使用，GitHub Pages 公開網址，不含結尾斜線
    web_base_url: Optional[str] = None


settings = Settings()
