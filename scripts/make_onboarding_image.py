"""產生新手引導的「如何取得 Google Sheet 連結」圖片（一次性工具，非後端執行期程式碼）。

把 `scripts/onboarding_src/` 底下三張手機版 Google Sheets 操作截圖，合併成一張帶
步驟標註的說明圖，輸出到 `web/images/copy-sheet-link.jpg`。`web/` 由 GitHub Actions
發布到 GitHub Pages，因此輸出檔會有公開 HTTPS 網址，可直接給 LINE image message 的
`originalContentUrl` 與 Telegram `sendPhoto` 使用（Bot 送圖片訊息要求圖片位於公開網址）。

版面：① 工具列入口為橫幅（原圖為寬扁的工具列截圖）置於上方橫跨整寬，
② ③ 兩張直式選單並排於下方——三張長寬比差異大，硬要並排會非常不平衡。

來源截圖解析度偏低（238x80 / 228x328 / 132x253），以 LANCZOS 放大 SCALE 倍後文字尚可辨識，
但在手機上放大到聊天氣泡寬度仍略糊。日後若補拍高解析度截圖，直接替換 onboarding_src/
底下的三個檔案重跑本腳本即可，不需改動版面程式碼。

使用方式：
    python scripts/make_onboarding_image.py --dry-run   # 只輸出預覽圖到 scripts/，不動 web/
    python scripts/make_onboarding_image.py              # 正式輸出到 web/images/
"""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _ROOT / "scripts" / "onboarding_src"
_OUTPUT_PATH = _ROOT / "web" / "images" / "copy-sheet-link.jpg"
_PREVIEW_PATH = _ROOT / "scripts" / "onboarding_preview.jpg"

# 原始截圖放大倍率：來源解析度低，放大後才有空間繪製清晰的步驟標註文字。
SCALE = 3

_BG_COLOR = "#FFFFFF"
_TEXT_COLOR = "#2B2B2B"
_ACCENT_COLOR = "#C0392B"  # 步驟說明用色，與截圖內既有的紅色標註框一致
_BORDER_COLOR = "#D5D5D5"

_PAD = 40
_GAP = 50
_TITLE_HEIGHT = 96
_CAPTION_HEIGHT = 62

_TITLE = "如何取得 Google Sheet 連結"
# 註：說明文字刻意不使用「⋮」字元，微軟正黑體缺此字會顯示為豆腐方框。
_STEPS = [
    ("step1_toolbar.jpg", "① 開啟你的 Google 試算表，點右上角的三點選單"),
    ("step2_menu.jpg", "②「共用與匯出」"),
    ("step3_copylink.jpg", "③「複製連結」"),
]

# 與 scripts/setup_line_richmenu.py 相同的字型探測策略。
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msjh.ttc",
    "C:/Windows/Fonts/mingliu.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise FileNotFoundError("找不到可用的中文字型，請修改 _FONT_CANDIDATES 加入本機字型路徑")


def _load_screenshot(name: str) -> Image.Image:
    image = Image.open(_SRC_DIR / name).convert("RGB")
    return image.resize((image.width * SCALE, image.height * SCALE), Image.LANCZOS)


def generate_image() -> Image.Image:
    """合併三張截圖為一張帶步驟標註的說明圖。"""
    top = _load_screenshot(_STEPS[0][0])
    left = _load_screenshot(_STEPS[1][0])
    right = _load_screenshot(_STEPS[2][0])

    bottom_row_width = left.width + _GAP + right.width
    total_width = _PAD * 2 + max(top.width, bottom_row_width)
    total_height = (
        _TITLE_HEIGHT
        + _CAPTION_HEIGHT + top.height + _GAP
        + _CAPTION_HEIGHT + max(left.height, right.height)
        + _PAD
    )

    canvas = Image.new("RGB", (total_width, total_height), _BG_COLOR)
    draw = ImageDraw.Draw(canvas)

    def draw_centered(text: str, font: ImageFont.FreeTypeFont, center_x: float, y: float, fill: str) -> None:
        width = draw.textbbox((0, 0), text, font=font)[2]
        draw.text((center_x - width / 2, y), text, font=font, fill=fill)

    def paste_with_border(image: Image.Image, x: int, y: int) -> None:
        canvas.paste(image, (x, y))
        draw.rectangle(
            [x, y, x + image.width, y + image.height], outline=_BORDER_COLOR, width=2
        )

    draw_centered(_TITLE, _load_font(48), total_width / 2, 24, _TEXT_COLOR)

    caption_font = _load_font(30)

    top_y = _TITLE_HEIGHT
    draw_centered(_STEPS[0][1], caption_font, total_width / 2, top_y + 10, _ACCENT_COLOR)
    paste_with_border(top, int((total_width - top.width) / 2), top_y + _CAPTION_HEIGHT)

    bottom_y = top_y + _CAPTION_HEIGHT + top.height + _GAP
    left_x = int((total_width - bottom_row_width) / 2)
    right_x = left_x + left.width + _GAP
    draw_centered(_STEPS[1][1], caption_font, left_x + left.width / 2, bottom_y + 10, _ACCENT_COLOR)
    draw_centered(_STEPS[2][1], caption_font, right_x + right.width / 2, bottom_y + 10, _ACCENT_COLOR)
    paste_with_border(left, left_x, bottom_y + _CAPTION_HEIGHT)
    paste_with_border(right, right_x, bottom_y + _CAPTION_HEIGHT)

    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只輸出預覽圖到 scripts/onboarding_preview.jpg，不寫入 web/images/",
    )
    args = parser.parse_args()

    image = generate_image()
    output_path = _PREVIEW_PATH if args.dry_run else _OUTPUT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, quality=92)
    print(f"已輸出 {output_path}（{image.width}x{image.height}）")


if __name__ == "__main__":
    main()
