"""LINE Rich Menu 設定腳本（一次性工具，非後端執行期程式碼）。

功能：
1. 用 Pillow 產生 2 橫行 × 3 格的陽春文字版選單圖（2500x1686，LINE Rich Menu full 尺寸）。
2. 呼叫 LINE Rich Menu API 建立選單、上傳圖片、設為所有使用者的預設選單。

只放 6 個高頻指令（ok／今日／圖表／原始表單／綁定／說明），修正、設定目標、目標、
取消等低頻/進階指令改用打字或 slash 指令即可，不佔選單格位。full 尺寸（1686 高）
每格接近正方形，字體可以放更大；預設收合（selected=False），只顯示輸入框上方的
「指令選單」拉桿，避免一進聊天室就展開佔掉手機畫面約一半。

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
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings  # noqa: E402

WIDTH, HEIGHT = 2500, 1686
COLS, ROWS = 3, 2
# 邊界用等分計算（而非固定 WIDTH // COLS），避免除不盡時最後一欄/列比其他格明顯寬/高一截。
_COL_BOUNDARIES = [round(i * WIDTH / COLS) for i in range(COLS + 1)]
_ROW_BOUNDARIES = [round(i * HEIGHT / ROWS) for i in range(ROWS + 1)]

# 依序嘗試找可用的中文字型，找不到時請自行修改加入本機字型路徑。
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msjh.ttc",
    "C:/Windows/Fonts/mingliu.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]

# 對應 app.core.dispatcher 的指令別名，三元組為 (顯示標籤, 副標, 實際送出的訊息文字)。
# 多數按鈕的顯示標籤與送出文字相同；「綁定」是唯一例外——顯示短標籤「綁定」維持排版，
# 但實際送出完整的「綁定 {Sheet ID}」樣板文字，讓 _handle_set() 的佔位符判斷接住並
# 導向引導訊息（見 app/core/dispatcher.py 的 _is_placeholder()），按鈕文字同時兼作語法示範。
# 指令本身若缺少參數，dispatcher 會回覆格式說明，等同於一個輕量的操作導引。
# 「原始表單」是「連結」指令的別名（見 dispatcher._EXACT_COMMAND_ALIASES），
# 選用這個按鈕文字是為了跟「圖表」（PWA 視覺化儀表板）明確區分開來。
_BUTTONS = [
    ("ok", "結束辨識", "ok"),
    ("今日", "查詢累計", "今日"),
    ("圖表", "儀表板連結", "圖表"),
    ("原始表單", "Sheet 原始檔", "原始表單"),
    ("綁定", "綁定 Sheet", "綁定 {Sheet ID}"),
    ("說明", "指令列表", "說明"),
]

_BG_COLORS = ["#F4F1EA", "#E8E2D5"]  # 交錯棋盤格底色，方便肉眼區分格子
_TEXT_COLOR = "#2B2B2B"
_BORDER_COLOR = "#C9C2B2"


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise FileNotFoundError("找不到可用的中文字型，請修改 _FONT_CANDIDATES 加入本機字型路徑")


def generate_image() -> Image.Image:
    """產生 2 橫行 × 3 格陽春文字版選單圖，之後可直接替換為美術設計圖（維持同樣的按鈕座標即可）。"""
    image = Image.new("RGB", (WIDTH, HEIGHT), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    label_font = _load_font(110)
    sub_font = _load_font(48)

    for idx, (label, subtitle, _action_text) in enumerate(_BUTTONS):
        row, col = divmod(idx, COLS)
        x0, x1 = _COL_BOUNDARIES[col], _COL_BOUNDARIES[col + 1]
        y0, y1 = _ROW_BOUNDARIES[row], _ROW_BOUNDARIES[row + 1]
        cell_w, cell_h = x1 - x0, y1 - y0

        draw.rectangle([x0, y0, x1, y1], fill=_BG_COLORS[idx % 2], outline=_BORDER_COLOR, width=4)

        label_bbox = draw.textbbox((0, 0), label, font=label_font)
        label_w, label_h = label_bbox[2] - label_bbox[0], label_bbox[3] - label_bbox[1]
        draw.text(
            (x0 + (cell_w - label_w) / 2, y0 + cell_h / 2 - label_h * 1.1),
            label,
            font=label_font,
            fill=_TEXT_COLOR,
        )

        sub_bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
        sub_w = sub_bbox[2] - sub_bbox[0]
        draw.text(
            (x0 + (cell_w - sub_w) / 2, y0 + cell_h / 2 + label_h * 0.3),
            subtitle,
            font=sub_font,
            fill=_TEXT_COLOR,
        )

    return image


def _build_areas() -> list[dict]:
    areas = []
    for idx, (_label, _subtitle, action_text) in enumerate(_BUTTONS):
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
        image.save(buffer, format="PNG")
        upload_resp = client.post(
            f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
            headers={**_headers(), "Content-Type": "image/png"},
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

    image = generate_image()

    if args.dry_run:
        preview_path = Path(__file__).resolve().parent / "richmenu_preview.png"
        image.save(preview_path)
        print(f"已產生預覽圖：{preview_path}（未呼叫任何 LINE API）")
        return

    rich_menu_id = create_and_publish(image)
    print(f"已建立並設為預設 Rich Menu，richMenuId={rich_menu_id}")


if __name__ == "__main__":
    main()
