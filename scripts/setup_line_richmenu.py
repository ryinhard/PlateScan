"""LINE Rich Menu 設定腳本（一次性工具，非後端執行期程式碼）。

功能：
1. 讀取 `scripts/richmenu_src/menu.jpg`（美術設計圖，2 橫行 × 3 格）並縮放至
   LINE Rich Menu full 尺寸（2500x1686）。
2. 呼叫 LINE Rich Menu API 建立選單、上傳圖片、設為所有使用者的預設選單。

只放 6 個高頻指令（ok／今日／圖表／連結／綁定／說明），修正、設定目標、目標、
取消等低頻/進階指令改用打字或 slash 指令即可，不佔選單格位。full 尺寸（1686 高）
每格接近正方形；預設收合（selected=False），只顯示輸入框上方的「指令選單」拉桿，
避免一進聊天室就展開佔掉手機畫面約一半。

使用方式：
    python scripts/setup_line_richmenu.py --dry-run   # 只產生預覽圖，不呼叫任何 LINE API
    python scripts/setup_line_richmenu.py              # 正式建立並設為預設選單

注意：不加 --dry-run 執行時，會直接修改「正式 LINE Bot 的預設選單」，
立即影響所有既有使用者看到的畫面，執行前請確認 .env 內的
LINE_CHANNEL_ACCESS_TOKEN 對應的是預期的正式頻道。
"""

import argparse
import io
import sys
from pathlib import Path

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings  # noqa: E402

WIDTH, HEIGHT = 2500, 1686
COLS, ROWS = 3, 2
# 邊界用等分計算（而非固定 WIDTH // COLS），避免除不盡時最後一欄/列比其他格明顯寬/高一截。
_COL_BOUNDARIES = [round(i * WIDTH / COLS) for i in range(COLS + 1)]
_ROW_BOUNDARIES = [round(i * HEIGHT / ROWS) for i in range(ROWS + 1)]

_SOURCE_IMAGE_PATH = Path(__file__).resolve().parent / "richmenu_src" / "menu.jpg"

# 對應 app.core.dispatcher 的指令別名：實際點擊按鈕後送出的訊息文字，由左到右、
# 由上到下對應 scripts/richmenu_src/menu.jpg 六宮格畫面上依序印的
# 「OK／今日營養／圖表」「記錄表單／綁定／指令」。
# 「今日營養」「綁定」畫面標籤與送出文字不同（分別對應「今日」與樣板文字
# 「綁定 {Sheet ID}」——後者讓 _handle_set() 的佔位符判斷接住並導向引導訊息，
# 見 app/core/dispatcher.py 的 _is_placeholder()）；「記錄表單」「指令」則已在
# dispatcher._EXACT_COMMAND_ALIASES 新增對應別名，讓送出文字與畫面標籤完全一致。
_ACTION_TEXTS = [
    "ok",
    "今日",
    "圖表",
    "記錄表單",
    "綁定 {Sheet ID}",
    "指令",
]


def load_image() -> Image.Image:
    """讀取美術設計圖並縮放至 LINE Rich Menu full 尺寸（2500x1686）。"""
    image = Image.open(_SOURCE_IMAGE_PATH).convert("RGB")
    return image.resize((WIDTH, HEIGHT), Image.LANCZOS)


def _build_areas() -> list[dict]:
    areas = []
    for idx, action_text in enumerate(_ACTION_TEXTS):
        row, col = divmod(idx, COLS)
        x0, x1 = _COL_BOUNDARIES[col], _COL_BOUNDARIES[col + 1]
        y0, y1 = _ROW_BOUNDARIES[row], _ROW_BOUNDARIES[row + 1]
        areas.append(
            {
                "bounds": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
                "action": {"type": "message", "text": action_text},
            }
        )
    return areas


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.line_channel_access_token}"}


def create_and_publish(image: Image.Image) -> str:
    """建立 Rich Menu、上傳圖片、設為所有使用者的預設選單，回傳 richMenuId。"""
    rich_menu_body = {
        "size": {"width": WIDTH, "height": HEIGHT},
        # 預設收合：full 版型展開後約佔手機畫面一半，一進聊天室就展開會遮住對話紀錄，
        # 改為只顯示輸入框上方的「指令選單」拉桿，使用者點一下才展開。
        "selected": False,
        "name": "PlateScan 主選單",
        "chatBarText": "指令選單",
        "areas": _build_areas(),
    }

    with httpx.Client(timeout=15.0) as client:
        create_resp = client.post(
            "https://api.line.me/v2/bot/richmenu",
            headers={**_headers(), "Content-Type": "application/json"},
            json=rich_menu_body,
        )
        create_resp.raise_for_status()
        rich_menu_id = create_resp.json()["richMenuId"]

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90)
        upload_resp = client.post(
            f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
            headers={**_headers(), "Content-Type": "image/jpeg"},
            content=buffer.getvalue(),
        )
        upload_resp.raise_for_status()

        default_resp = client.post(
            f"https://api.line.me/v2/bot/user/all/richmenu/{rich_menu_id}",
            headers=_headers(),
        )
        default_resp.raise_for_status()

    return rich_menu_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只產生預覽圖片存到 scripts/richmenu_preview.png，不呼叫 LINE API",
    )
    args = parser.parse_args()

    image = load_image()

    if args.dry_run:
        preview_path = Path(__file__).resolve().parent / "richmenu_preview.png"
        image.save(preview_path)
        print(f"已產生預覽圖：{preview_path}（未呼叫任何 LINE API）")
        return

    rich_menu_id = create_and_publish(image)
    print(f"已建立並設為預設 Rich Menu，richMenuId={rich_menu_id}")


if __name__ == "__main__":
    main()
