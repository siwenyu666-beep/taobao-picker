#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成桌面图标：五金采购助手（蓝底+购物袋+五字）"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

OUT = Path(__file__).resolve().parent / "assets"
OUT.mkdir(exist_ok=True)

BLUE = (27, 110, 243, 255)
WHITE = (255, 255, 255, 255)
FONT = "C:/Windows/Fonts/msyhbd.ttc"


def draw(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size // 8
    # 背景圆角矩形
    d.rounded_rectangle([size*0.04, size*0.04, size*0.96, size*0.96], radius=r, fill=BLUE)
    # 白色购物袋
    bag_w, bag_h = size * 0.56, size * 0.52
    x0, y0 = (size - bag_w) / 2, size * 0.30
    d.rounded_rectangle([x0, y0, x0 + bag_w, y0 + bag_h], radius=size*0.06, fill=WHITE)
    # 提手
    d.arc([x0 + bag_w*0.20, y0 - size*0.16, x0 + bag_w*0.80, y0 + size*0.10],
          start=180, end=360, fill=WHITE, width=int(size*0.06))
    # 袋上"五"字
    try:
        font = ImageFont.truetype(FONT, int(size * 0.30))
        d.text((size*0.5, y0 + bag_h*0.52), "五", font=font, fill=BLUE, anchor="mm")
    except Exception:
        pass
    return img


if __name__ == "__main__":
    sizes = [512, 256, 192, 128, 64, 48, 32, 16]
    imgs = [draw(s) for s in sizes]
    imgs[0].save(OUT / "assistant.ico", sizes=[(s, s) for s in sizes],
                 append_images=imgs[1:])
    imgs[1].save(OUT / "assistant.png")          # 256
    draw(192).save(OUT / "icon-192.png")          # PWA 图标
    draw(512).save(OUT / "icon-512.png")          # PWA 图标
    print(f"图标已生成: {OUT / 'assistant.ico'} + PWA 图标 192/512")
