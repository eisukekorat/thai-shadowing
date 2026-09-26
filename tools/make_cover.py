# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=10"]
# ///
"""番組アートワーク cover.png（1400×1400）を作る。Mac で1回だけ実行: uv run tools/make_cover.py"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SIZE = 1400
img = Image.new("RGB", (SIZE, SIZE), "#0f2a3f")
d = ImageDraw.Draw(img)
for i in range(0, SIZE, 70):  # 控えめな斜め縞
    d.line([(i, 0), (0, i)], fill="#123552", width=2)
    d.line([(SIZE, i), (i, SIZE)], fill="#123552", width=2)

def font(candidates, size):
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except OSError:
            continue
    return ImageFont.load_default()

thai = font(["/System/Library/Fonts/Supplemental/Ayuthaya.ttf", "/System/Library/Fonts/Thonburi.ttc", "C:/Windows/Fonts/leelawui.ttf"], 300)
jp = font(["/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc", "C:/Windows/Fonts/meiryob.ttc"], 150)
small = font(["/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc", "C:/Windows/Fonts/meiryo.ttc"], 70)

d.text((SIZE / 2, 470), "พูดได้", font=thai, fill="#ffd166", anchor="mm")
d.text((SIZE / 2, 800), "瞬間タイ作文", font=jp, fill="white", anchor="mm")
d.text((SIZE / 2, 960), "聞いて・言って・シャドーイング", font=small, fill="#bcd5ea", anchor="mm")
d.rounded_rectangle([250, 1080, 1150, 1180], radius=50, outline="#ffd166", width=6)
d.text((SIZE / 2, 1130), "日本語 → 3秒 → タイ語 ×2", font=small, fill="#ffd166", anchor="mm")
img.save(ROOT / "cover.png", optimize=True)
print("cover.png", (ROOT / "cover.png").stat().st_size // 1024, "KB")
