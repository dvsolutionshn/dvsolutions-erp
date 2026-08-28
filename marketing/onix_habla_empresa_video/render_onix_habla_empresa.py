from __future__ import annotations

import argparse
import asyncio
import math
import subprocess
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cairosvg
import edge_tts
import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ASSETS = HERE / "assets"
OUTPUT = HERE / "output"
AUDIO = OUTPUT / "audio"
OUTPUT.mkdir(parents=True, exist_ok=True)
AUDIO.mkdir(parents=True, exist_ok=True)

FPS = 24
DURATION = 86

NAVY = (4, 16, 31)
NAVY_2 = (8, 31, 55)
BLUE = (31, 82, 163)
ROYAL = (42, 74, 173)
CYAN = (65, 219, 222)
TEAL = (37, 190, 184)
GREEN = (31, 182, 126)
AMBER = (241, 164, 54)
RED = (234, 84, 95)
PURPLE = (125, 91, 219)
WHITE = (247, 251, 255)
SOFT = (184, 207, 225)
INK = (18, 37, 58)
MUTED = (89, 109, 131)
PAPER = (246, 250, 254)


@dataclass(frozen=True)
class VideoSpec:
    key: str
    width: int
    height: int
    vertical: bool


SPECS = {
    "16x9": VideoSpec("16x9", 1920, 1080, False),
    "9x16": VideoSpec("9x16", 1080, 1920, True),
}


SCENES = [
    (0.0, 4.5, "intro"),
    (4.5, 14.5, "overview"),
    (14.5, 24.5, "debt"),
    (24.5, 29.0, "slogan_search"),
    (29.0, 38.0, "comparison"),
    (38.0, 47.0, "inventory"),
    (47.0, 57.0, "payroll"),
    (57.0, 65.0, "crm"),
    (65.0, 69.5, "slogan_modules"),
    (69.5, 79.5, "priorities"),
    (79.5, 86.0, "closing"),
]


VOICE_SEGMENTS = [
    (0.4, "Habla con tu empresa.", "es-HN-CarlosNeural", "+8%"),
    (4.8, "Buenos días, Onix. ¿Cómo está mi empresa hoy?", "es-HN-CarlosNeural", "+18%"),
    (8.7, "Ventas: más doce por ciento. Cuentas por cobrar: trescientos veintisiete mil lempiras.", "es-HN-KarlaNeural", "+42%"),
    (14.8, "¿Quiénes me deben desde dos mil veinticuatro? ¿Y a quién debería cobrar primero?", "es-HN-CarlosNeural", "+24%"),
    (19.4, "Prioriza Grupo Empresarial X: noventa y cuatro mil quinientos lempiras vencidos.", "es-HN-KarlaNeural", "+42%"),
    (24.8, "No busques el dato. Pregúntalo.", "es-HN-CarlosNeural", "+8%"),
    (29.3, "¿Cómo vamos comparados con dos mil veinticinco?", "es-HN-CarlosNeural", "+22%"),
    (32.1, "Ventas: más dieciocho punto cuatro. Gastos: más once punto dos por ciento.", "es-HN-KarlaNeural", "+42%"),
    (38.3, "¿Qué productos necesito comprar?", "es-HN-CarlosNeural", "+20%"),
    (40.6, "Hay siete productos con existencia baja. Estos tres requieren atención inmediata.", "es-HN-KarlaNeural", "+28%"),
    (47.3, "¿Cuánto gastamos en planilla este mes y por qué aumentó?", "es-HN-CarlosNeural", "+25%"),
    (50.3, "Planilla: doscientos ochenta y cuatro mil. Subió por contrataciones, horas extra y prestaciones.", "es-HN-KarlaNeural", "+42%"),
    (57.3, "¿Qué oportunidades tenemos pendientes?", "es-HN-CarlosNeural", "+18%"),
    (59.9, "Ocho oportunidades por uno punto seis millones. Dos requieren seguimiento.", "es-HN-KarlaNeural", "+42%"),
    (65.3, "No recorras módulo por módulo. Onix los conecta.", "es-HN-CarlosNeural", "+12%"),
    (69.8, "Onix, analiza todo y dime qué necesita mi atención.", "es-HN-CarlosNeural", "+20%"),
    (73.4, "Prioridades: cobrar vencidos, reponer productos, seguir oportunidades y revisar gastos. Rentabilidad sobre dos mil veinticinco.", "es-HN-KarlaNeural", "+44%"),
    (80.85, "Tu empresa tiene miles de datos. Onix los convierte en respuestas. Habla con tu empresa.", "es-HN-CarlosNeural", "+36%"),
]


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def ease(value: float) -> float:
    value = clamp(value)
    return 1 - (1 - value) ** 3


def smooth(value: float) -> float:
    value = clamp(value)
    return value * value * (3 - 2 * value)


def alpha(color, amount=255):
    return tuple(color[:3]) + (int(clamp(amount / 255) * 255),)


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False):
    fonts = Path("C:/Windows/Fonts")
    candidates = ["segoeuib.ttf" if bold else "segoeui.ttf", "arialbd.ttf" if bold else "arial.ttf"]
    for name in candidates:
        path = fonts / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def tw(draw: ImageDraw.ImageDraw, text: str, fnt) -> int:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0]


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_width: int, max_lines: int | None = None):
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and tw(draw, candidate, fnt) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and tw(draw, lines[-1] + "…", fnt) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def draw_lines(draw, xy, text, fnt, fill, max_width, line_gap=10, max_lines=None):
    x, y = xy
    lines = wrap(draw, text, fnt, max_width, max_lines)
    bbox = draw.textbbox((0, 0), "Ag", font=fnt)
    line_height = bbox[3] - bbox[1] + line_gap
    for index, line in enumerate(lines):
        draw.text((x, y + index * line_height), line, font=fnt, fill=fill)
    return y + len(lines) * line_height


def rounded_shadow(size, radius=30, fill=(255, 255, 255, 255), outline=None, shadow_alpha=80):
    width, height = size
    pad = 38
    canvas = Image.new("RGBA", (width + pad * 2, height + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow, "RGBA")
    sd.rounded_rectangle((pad + 8, pad + 12, pad + width + 8, pad + height + 12), radius=radius, fill=(0, 7, 20, shadow_alpha))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    canvas.alpha_composite(shadow)
    d = ImageDraw.Draw(canvas, "RGBA")
    d.rounded_rectangle((pad, pad, pad + width, pad + height), radius=radius, fill=fill, outline=outline, width=2 if outline else 1)
    return canvas, d, pad


@lru_cache(maxsize=4)
def onix_logo(size: int):
    png = ASSETS / f"onix-{size}.png"
    source = ROOT / "marketing" / "dv_erp_launch_video" / "assets" / "onix-agent-logo.svg"
    if not png.exists():
        cairosvg.svg2png(url=str(source), write_to=str(png), output_width=size, output_height=size)
    return Image.open(png).convert("RGBA")


@lru_cache(maxsize=2)
def dv_logo(max_width: int):
    image = Image.open(ROOT / "core" / "static" / "core" / "img" / "dv-solutions-brand.png").convert("RGBA")
    image.thumbnail((max_width, int(max_width * 0.45)), Image.LANCZOS)
    return image


@lru_cache(maxsize=2)
def real_interface(spec_key: str):
    path = ASSETS / f"onix-real-interface-{spec_key}.png"
    return Image.open(path).convert("RGBA")


def cover(image: Image.Image, width: int, height: int, zoom=1.0):
    scale = max(width / image.width, height / image.height) * zoom
    resized = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def base(spec: VideoSpec, sec: float, darker=0):
    w, h = spec.width, spec.height
    img = Image.new("RGBA", (w, h), NAVY + (255,))
    d = ImageDraw.Draw(img, "RGBA")
    for y in range(h):
        p = y / max(1, h - 1)
        color = (
            int(NAVY[0] * (1 - p) + NAVY_2[0] * p),
            int(NAVY[1] * (1 - p) + NAVY_2[1] * p),
            int(NAVY[2] * (1 - p) + NAVY_2[2] * p),
            255,
        )
        d.line((0, y, w, y), fill=color)
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, "RGBA")
    pulse = (math.sin(sec * 0.65) + 1) / 2
    r = int(min(w, h) * 0.55)
    gd.ellipse((w - r, -r // 2, w + r // 2, r), fill=CYAN + (24 + int(10 * pulse),))
    gd.ellipse((-r // 2, h - r, r, h + r // 2), fill=PURPLE + (25 + int(9 * pulse),))
    glow = glow.filter(ImageFilter.GaussianBlur(max(45, int(min(w, h) * 0.055))))
    img.alpha_composite(glow)

    grid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    g = ImageDraw.Draw(grid, "RGBA")
    gap = 88 if not spec.vertical else 72
    shift = int((sec * 18) % gap)
    for x in range(-gap + shift, w + gap, gap):
        g.line((x, 0, x, h), fill=(96, 210, 224, 8), width=1)
    for y in range(-gap + shift, h + gap, gap):
        g.line((0, y, w, y), fill=(96, 210, 224, 8), width=1)
    for i in range(16):
        x = int((i * w / 15 + sec * (14 + i % 3)) % (w + 30)) - 15
        y = int((110 + i * 137) % h)
        g.ellipse((x - 3, y - 3, x + 3, y + 3), fill=CYAN + (55,))
    img.alpha_composite(grid)
    if darker:
        img.alpha_composite(Image.new("RGBA", (w, h), (0, 3, 12, darker)))
    return img


def paste_with_opacity(canvas, layer, xy, opacity_value=1.0):
    if opacity_value <= 0:
        return
    if opacity_value < 0.999:
        layer = layer.copy()
        a = layer.getchannel("A").point(lambda value: int(value * opacity_value))
        layer.putalpha(a)
    canvas.alpha_composite(layer, (int(xy[0]), int(xy[1])))


def brand_header(img: Image.Image, spec: VideoSpec, sec: float, label="INTELIGENCIA EMPRESARIAL"):
    w = spec.width
    y = 34 if not spec.vertical else 44
    size = 70 if not spec.vertical else 82
    img.alpha_composite(onix_logo(size), (42, y - 8))
    d = ImageDraw.Draw(img, "RGBA")
    f_name = font(29 if not spec.vertical else 31, True)
    f_micro = font(14 if not spec.vertical else 16, True)
    d.text((126 if not spec.vertical else 142, y), "ONIX", font=f_name, fill=WHITE + (255,))
    d.text((126 if not spec.vertical else 142, y + 40), label, font=f_micro, fill=CYAN + (255,))
    if not spec.vertical:
        logo = dv_logo(205)
        img.alpha_composite(logo, (w - logo.width - 45, 28))
    else:
        d.text((w - 245, y + 18), "DV SOLUTIONS ERP", font=f_micro, fill=SOFT + (235,))


def scene_intro(spec: VideoSpec, local: float, sec: float):
    source = real_interface(spec.key)
    zoom = 1.0 + 0.025 * ease(local / 4.5)
    img = cover(source, spec.width, spec.height, zoom)
    img = img.filter(ImageFilter.GaussianBlur(0.45))
    shade = Image.new("RGBA", img.size, (2, 10, 24, 145))
    img.alpha_composite(shade)
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, "RGBA")
    beam_x = int(-300 + (local / 4.5) * (spec.width + 600))
    gd.polygon([(beam_x - 160, 0), (beam_x + 80, 0), (beam_x + 420, spec.height), (beam_x + 180, spec.height)], fill=CYAN + (32,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(45)))
    p = ease(local / 0.8)
    card_w = int(spec.width * (0.76 if not spec.vertical else 0.88))
    card_h = 340 if not spec.vertical else 560
    card, d, pad = rounded_shadow((card_w, card_h), radius=40, fill=(5, 23, 44, 232), outline=CYAN + (75,), shadow_alpha=130)
    title_size = 94 if not spec.vertical else 98
    sub_size = 47 if not spec.vertical else 48
    d.text((pad + 50, pad + 55), "HABLA", font=font(title_size, True), fill=WHITE + (255,))
    d.text((pad + 50, pad + 55 + title_size), "CON TU EMPRESA.", font=font(title_size, True), fill=CYAN + (255,))
    d.line((pad + 52, pad + 270 if not spec.vertical else pad + 330, pad + card_w - 52, pad + 270 if not spec.vertical else pad + 330), fill=CYAN + (95,), width=2)
    d.text((pad + 52, pad + 286 if not spec.vertical else pad + 370), "ONIX  ·  DV SOLUTIONS ERP", font=font(sub_size, True), fill=SOFT + (255,))
    x = (spec.width - card.width) // 2
    y = (spec.height - card.height) // 2 + int(36 * (1 - p))
    paste_with_opacity(img, card, (x, y), p)
    return img


def chat_layout(spec: VideoSpec):
    if spec.vertical:
        return {
            "chat": (45, 180, 990, 985),
            "insight": (45, 1210, 990, 625),
            "title_y": 128,
        }
    return {
        "chat": (60, 142, 1120, 865),
        "insight": (1215, 142, 645, 865),
        "title_y": 112,
    }


def draw_chat_panel(img, spec, local, question, answer, context="CONTEXTO CONECTADO", compact=False):
    x, y, width, height = chat_layout(spec)["chat"]
    layer, d, pad = rounded_shadow((width, height), radius=34, fill=PAPER + (252,), outline=(196, 222, 232, 210), shadow_alpha=105)
    ox, oy = pad, pad
    header_h = 112 if not spec.vertical else 128
    d.rounded_rectangle((ox, oy, ox + width, oy + header_h), radius=34, fill=(6, 27, 50, 255))
    d.rectangle((ox, oy + header_h - 35, ox + width, oy + header_h), fill=(6, 27, 50, 255))
    logo_size = 58 if not spec.vertical else 70
    layer.alpha_composite(onix_logo(logo_size), (ox + 24, oy + 24))
    d.text((ox + 100 if not spec.vertical else ox + 112, oy + 24), "Onix", font=font(27 if not spec.vertical else 31, True), fill=WHITE + (255,))
    d.ellipse((ox + 101 if not spec.vertical else ox + 113, oy + 68, ox + 115 if not spec.vertical else ox + 129, oy + 82), fill=GREEN + (255,))
    d.text((ox + 128 if not spec.vertical else ox + 143, oy + 61), "IA conectada  ·  empresa activa", font=font(16 if not spec.vertical else 18), fill=(154, 235, 215, 255))
    d.rounded_rectangle((ox + width - 280, oy + 30, ox + width - 25, oy + 76), radius=20, fill=(255, 255, 255, 16), outline=(255, 255, 255, 28), width=1)
    d.text((ox + width - 254, oy + 42), context, font=font(13 if not spec.vertical else 15, True), fill=SOFT + (245,))

    user_p = ease((local - 0.35) / 0.5)
    type_p = clamp((local - 0.15) / 1.15)
    response_p = clamp((local - 2.45) / 2.65)
    question_visible = question[: max(1, int(len(question) * type_p))]
    input_y = oy + height - (92 if not spec.vertical else 108)
    d.rounded_rectangle((ox + 28, input_y, ox + width - 28, oy + height - 25), radius=26, fill=(237, 245, 250, 255), outline=(183, 216, 225, 255), width=2)
    d.text((ox + 57, input_y + 20), question_visible, font=font(18 if not spec.vertical else 20), fill=MUTED + (255,))
    caret_x = min(ox + width - 95, ox + 58 + tw(d, question_visible, font(18 if not spec.vertical else 20)))
    if int(local * 3) % 2 == 0 and type_p < 1:
        d.line((caret_x, input_y + 18, caret_x, input_y + 48), fill=CYAN + (255,), width=3)

    bubble_margin = 38
    user_width = int(width * (0.72 if not spec.vertical else 0.82))
    user_x = ox + width - user_width - bubble_margin
    user_y = oy + header_h + 32
    question_font = font(20 if not spec.vertical else 23)
    q_lines = wrap(d, question, question_font, user_width - 58, 3)
    q_height = 64 + len(q_lines) * (29 if not spec.vertical else 34)
    d.rounded_rectangle((user_x, user_y, user_x + user_width, user_y + q_height), radius=27, fill=(31, 68, 124, int(255 * user_p)))
    if user_p > 0:
        d.text((user_x + 28, user_y + 18), "Tú", font=font(17 if not spec.vertical else 19, True), fill=WHITE + (int(255 * user_p),))
        for i, line in enumerate(q_lines):
            d.text((user_x + 28, user_y + 52 + i * (29 if not spec.vertical else 34)), line, font=question_font, fill=WHITE + (int(255 * user_p),))

    answer_x = ox + bubble_margin
    answer_y = user_y + q_height + 26
    answer_width = int(width * (0.84 if not spec.vertical else 0.90))
    answer_font = font(18 if not spec.vertical else 21)
    a_lines = wrap(d, answer, answer_font, answer_width - 75, 7 if spec.vertical else 6)
    line_h = 29 if not spec.vertical else 34
    answer_height = 78 + len(a_lines) * line_h
    processing = 1.55 < local < 2.45
    if local >= 1.5:
        d.rounded_rectangle((answer_x, answer_y, answer_x + answer_width, answer_y + answer_height), radius=27, fill=(255, 255, 255, 255), outline=(188, 216, 225, 255), width=2)
        layer.alpha_composite(onix_logo(44 if not spec.vertical else 50), (answer_x + 20, answer_y + 16))
        d.text((answer_x + 78, answer_y + 25), "Onix", font=font(18 if not spec.vertical else 21, True), fill=INK + (255,))
        if processing:
            for i in range(3):
                radius = 6 + int(2 * math.sin(local * 9 + i * 1.7))
                cx = answer_x + 88 + i * 28
                cy = answer_y + 85
                d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=TEAL + (255,))
            d.text((answer_x + 190, answer_y + 73), "Analizando la empresa…", font=font(16 if not spec.vertical else 18), fill=MUTED + (255,))
        elif local >= 2.45:
            chars = max(1, int(len(answer) * response_p))
            partial = answer[:chars]
            partial_lines = wrap(d, partial, answer_font, answer_width - 75, 7 if spec.vertical else 6)
            for i, line in enumerate(partial_lines):
                d.text((answer_x + 28, answer_y + 76 + i * line_h), line, font=answer_font, fill=INK + (255,))
            if response_p < 1 and int(local * 4) % 2 == 0:
                last = partial_lines[-1] if partial_lines else ""
                cx = answer_x + 28 + tw(d, last, answer_font)
                cy = answer_y + 76 + (len(partial_lines) - 1) * line_h
                d.rectangle((cx + 3, cy + 2, cx + 6, cy + line_h - 4), fill=TEAL + (255,))

    panel_p = ease(local / 0.45)
    paste_with_opacity(img, layer, (x - pad, y - pad + int(30 * (1 - panel_p))), panel_p)
    return response_p


def insight_panel(img, spec, local, title, renderer):
    x, y, width, height = chat_layout(spec)["insight"]
    p = ease((local - 3.7) / 0.65)
    layer, d, pad = rounded_shadow((width, height), radius=34, fill=(7, 28, 51, 246), outline=CYAN + (65,), shadow_alpha=120)
    ox, oy = pad, pad
    d.text((ox + 32, oy + 28), "RESPUESTA VISUAL", font=font(14 if not spec.vertical else 16, True), fill=CYAN + (255,))
    title_font = font(28 if not spec.vertical else 31, True)
    d.text((ox + 32, oy + 58), title, font=title_font, fill=WHITE + (255,))
    d.line((ox + 32, oy + 108, ox + width - 32, oy + 108), fill=(255, 255, 255, 24), width=2)
    renderer(layer, d, (ox, oy), width, height, spec, local)
    paste_with_opacity(img, layer, (x - pad + int(35 * (1 - p)), y - pad), p)


def metric_card(d, rect, label, value, accent, note=""):
    x1, y1, x2, y2 = rect
    d.rounded_rectangle(rect, radius=22, fill=(15, 48, 78, 255), outline=(255, 255, 255, 25), width=1)
    d.rounded_rectangle((x1, y1, x1 + 8, y2), radius=4, fill=accent + (255,))
    d.text((x1 + 25, y1 + 18), label.upper(), font=font(14, True), fill=SOFT + (255,))
    d.text((x1 + 25, y1 + 49), value, font=font(31, True), fill=WHITE + (255,))
    if note:
        d.text((x1 + 25, y2 - 29), note, font=font(14), fill=accent + (255,))


def insight_overview(layer, d, origin, width, height, spec, local):
    ox, oy = origin
    top = oy + 132
    if spec.vertical:
        card_w = (width - 86) // 2
        metric_card(d, (ox + 28, top, ox + 28 + card_w, top + 126), "Ventas agosto", "L 1,284,500", GREEN, "+12% vs. julio")
        metric_card(d, (ox + 58 + card_w, top, ox + width - 28, top + 126), "Pendiente", "L 327,450", RED, "4 facturas vencidas")
        chart_top = top + 160
    else:
        metric_card(d, (ox + 28, top, ox + width - 28, top + 132), "Ventas agosto", "L 1,284,500", GREEN, "+12% vs. mes anterior")
        metric_card(d, (ox + 28, top + 155, ox + width - 28, top + 287), "Cuentas por cobrar", "L 327,450", RED, "4 clientes vencidos")
        chart_top = top + 330
    chart_bottom = oy + height - 65
    d.text((ox + 35, chart_top), "TENDENCIA DE VENTAS", font=font(14, True), fill=SOFT + (255,))
    left, right = ox + 38, ox + width - 38
    top_y, bottom_y = chart_top + 45, chart_bottom
    for i in range(4):
        yy = top_y + i * (bottom_y - top_y) / 3
        d.line((left, yy, right, yy), fill=(255, 255, 255, 18), width=1)
    points = []
    values = [0.42, 0.51, 0.48, 0.64, 0.69, 0.76, 0.88]
    progress = ease((local - 4.0) / 1.2)
    visible = max(2, int(1 + progress * (len(values) - 1)))
    for i, value in enumerate(values[:visible]):
        xx = left + i * (right - left) / (len(values) - 1)
        yy = bottom_y - value * (bottom_y - top_y)
        points.append((xx, yy))
    if len(points) > 1:
        d.line(points, fill=CYAN + (255,), width=5, joint="curve")
        for xx, yy in points:
            d.ellipse((xx - 6, yy - 6, xx + 6, yy + 6), fill=WHITE + (255,), outline=CYAN + (255,), width=3)


def insight_debt(layer, d, origin, width, height, spec, local):
    ox, oy = origin
    start_y = oy + 132
    rows = [
        ("Grupo Empresarial X", "FAC-0241", "614 días", "L 94,500", RED),
        ("Comercial del Norte", "FAC-0318", "482 días", "L 61,850", AMBER),
        ("Inversiones Copán", "FAC-0397", "391 días", "L 38,200", AMBER),
    ]
    cols = [0.04, 0.51, 0.68, 0.84]
    headers = ["CLIENTE", "FACTURA", "ANTIGÜEDAD", "SALDO"]
    for col, header in zip(cols, headers):
        d.text((ox + int(width * col), start_y), header, font=font(12 if not spec.vertical else 14, True), fill=SOFT + (255,))
    row_h = 102 if spec.vertical else 147
    for i, (client, invoice, age, amount, color) in enumerate(rows):
        p = ease((local - 4.0 - i * 0.15) / 0.45)
        y = start_y + 40 + i * row_h
        d.rounded_rectangle((ox + 22, y, ox + width - 22, y + row_h - 16), radius=20, fill=(15, 48, 78, int(245 * p)), outline=(255, 255, 255, int(24 * p)), width=1)
        d.rounded_rectangle((ox + 22, y, ox + 30, y + row_h - 16), radius=4, fill=color + (int(255 * p),))
        client_font = font(16 if not spec.vertical else 18, True)
        client_lines = wrap(d, client, client_font, int(width * 0.43), 2)
        for j, line in enumerate(client_lines):
            d.text((ox + int(width * cols[0]), y + 25 + j * 24), line, font=client_font, fill=WHITE + (int(255 * p),))
        d.text((ox + int(width * cols[1]), y + 34), invoice, font=font(15), fill=SOFT + (int(255 * p),))
        d.text((ox + int(width * cols[2]), y + 34), age, font=font(15), fill=color + (int(255 * p),))
        d.text((ox + int(width * cols[3]), y + 29), amount, font=font(19, True), fill=WHITE + (int(255 * p),))
    note_y = start_y + 40 + len(rows) * row_h + 12
    d.rounded_rectangle((ox + 22, note_y, ox + width - 22, min(oy + height - 28, note_y + 118)), radius=20, fill=(50, 26, 42, 255), outline=RED + (65,), width=2)
    d.text((ox + 48, note_y + 20), "PRIORIDAD RECOMENDADA", font=font(13, True), fill=RED + (255,))
    d.text((ox + 48, note_y + 51), "Cobrar primero a Grupo Empresarial X", font=font(18, True), fill=WHITE + (255,))


def insight_comparison(layer, d, origin, width, height, spec, local):
    ox, oy = origin
    top = oy + 145
    items = [
        ("VENTAS ACUMULADAS", 0.63, 0.81, "+18.4%", GREEN),
        ("GASTOS ADMINISTRATIVOS", 0.46, 0.59, "+11.2%", AMBER),
    ]
    group_h = 160 if spec.vertical else 260
    for idx, (label, old, new, delta, color) in enumerate(items):
        y = top + idx * group_h
        d.text((ox + 34, y), label, font=font(15, True), fill=SOFT + (255,))
        d.text((ox + width - 125, y - 2), delta, font=font(22, True), fill=color + (255,))
        bar_left, bar_right = ox + 88, ox + width - 40
        d.text((ox + 34, y + 55), "2025", font=font(15, True), fill=SOFT + (255,))
        d.rounded_rectangle((bar_left, y + 54, bar_right, y + 91), radius=17, fill=(255, 255, 255, 18))
        d.rounded_rectangle((bar_left, y + 54, bar_left + int((bar_right - bar_left) * old), y + 91), radius=17, fill=(111, 136, 158, 255))
        d.text((ox + 34, y + 117), "2026", font=font(15, True), fill=WHITE + (255,))
        d.rounded_rectangle((bar_left, y + 116, bar_right, y + 153), radius=17, fill=(255, 255, 255, 18))
        prog = ease((local - 4.0 - idx * 0.18) / 0.8)
        d.rounded_rectangle((bar_left, y + 116, bar_left + int((bar_right - bar_left) * new * prog), y + 153), radius=17, fill=color + (255,))
    d.rounded_rectangle((ox + 30, oy + height - 150, ox + width - 30, oy + height - 30), radius=24, fill=(11, 61, 68, 255), outline=GREEN + (65,), width=2)
    d.text((ox + 58, oy + height - 122), "RENTABILIDAD", font=font(14, True), fill=GREEN + (255,))
    d.text((ox + 58, oy + height - 82), "Por encima del mismo período de 2025", font=font(20, True), fill=WHITE + (255,))


def insight_inventory(layer, d, origin, width, height, spec, local):
    ox, oy = origin
    top = oy + 132
    products = [
        ("Tóner HP 85A", "2 unidades", "mínimo 12", RED),
        ("Papel térmico 80mm", "5 rollos", "mínimo 30", RED),
        ("Router empresarial AX", "1 unidad", "mínimo 6", AMBER),
    ]
    row_h = 125 if spec.vertical else 165
    for idx, (name, stock, minimum, color) in enumerate(products):
        p = ease((local - 4.0 - idx * 0.16) / 0.45)
        y = top + idx * row_h
        d.rounded_rectangle((ox + 26, y, ox + width - 26, y + row_h - 18), radius=23, fill=(15, 48, 78, int(245 * p)), outline=(255, 255, 255, int(24 * p)), width=1)
        d.ellipse((ox + 50, y + 39, ox + 94, y + 83), fill=color + (int(255 * p),))
        d.text((ox + 116, y + 24), name, font=font(19, True), fill=WHITE + (int(255 * p),))
        d.text((ox + 116, y + 62), f"Existencia: {stock}", font=font(16), fill=color + (int(255 * p),))
        d.text((ox + 116, y + 94), minimum, font=font(14), fill=SOFT + (int(255 * p),))
        bar_left, bar_right = ox + width - 175, ox + width - 48
        d.rounded_rectangle((bar_left, y + 48, bar_right, y + 68), radius=10, fill=(255, 255, 255, 18))
        d.rounded_rectangle((bar_left, y + 48, bar_left + int((bar_right - bar_left) * (0.16 + idx * 0.07)), y + 68), radius=10, fill=color + (255,))
    d.text((ox + 34, oy + height - 78), "7 productos con existencia baja  ·  3 críticos", font=font(18, True), fill=AMBER + (255,))


def insight_payroll(layer, d, origin, width, height, spec, local):
    ox, oy = origin
    top = oy + 132
    metric_card(d, (ox + 26, top, ox + width - 26, top + 136), "Planilla agosto", "L 284,300", AMBER, "+ L 21,400 vs. julio")
    reasons = [
        ("Nuevas contrataciones", "L 10,800", 0.50, CYAN),
        ("Horas adicionales", "L 6,200", 0.29, PURPLE),
        ("Prestaciones", "L 4,400", 0.21, GREEN),
    ]
    y = top + (160 if spec.vertical else 185)
    for idx, (label, amount, ratio, color) in enumerate(reasons):
        d.text((ox + 34, y), label, font=font(16, True), fill=WHITE + (255,))
        d.text((ox + width - 150, y), amount, font=font(16, True), fill=color + (255,))
        d.rounded_rectangle((ox + 34, y + 42, ox + width - 34, y + 70), radius=14, fill=(255, 255, 255, 18))
        p = ease((local - 4.0 - idx * 0.15) / 0.7)
        d.rounded_rectangle((ox + 34, y + 42, ox + 34 + int((width - 68) * ratio * p), y + 70), radius=14, fill=color + (255,))
        y += 80 if spec.vertical else 140
    note_top = oy + height - (95 if spec.vertical else 122)
    d.rounded_rectangle((ox + 28, note_top, ox + width - 28, oy + height - 30), radius=22, fill=(43, 34, 67, 255), outline=PURPLE + (65,), width=2)
    d.text((ox + 54, note_top + 30), "Variación explicada y trazable", font=font(19, True), fill=WHITE + (255,))


def insight_crm(layer, d, origin, width, height, spec, local):
    ox, oy = origin
    top = oy + 132
    metric_card(d, (ox + 26, top, ox + width - 26, top + 136), "Oportunidades abiertas", "8  ·  L 1.6 millones", GREEN, "2 requieren seguimiento")
    stages = [
        ("Prospectos", 8, 1.0, CYAN),
        ("Propuesta enviada", 5, 0.73, BLUE),
        ("Negociación", 3, 0.50, PURPLE),
        ("Seguimiento esta semana", 2, 0.34, AMBER),
    ]
    y = top + (168 if spec.vertical else 185)
    for idx, (label, count, ratio, color) in enumerate(stages):
        p = ease((local - 4.0 - idx * 0.12) / 0.55)
        bar_width = int((width - 90) * ratio * p)
        x = ox + (width - bar_width) // 2
        d.rounded_rectangle((x, y, x + bar_width, y + 72), radius=21, fill=color + (220,), outline=WHITE + (25,), width=1)
        if bar_width > 230:
            d.text((x + 24, y + 20), label, font=font(16, True), fill=WHITE + (255,))
            d.text((x + bar_width - 55, y + 18), str(count), font=font(22, True), fill=WHITE + (255,))
        y += 75 if spec.vertical else 108


CONVERSATIONS = {
    "overview": (
        "Buenos días ONIX, ¿cómo está mi empresa hoy?",
        "Buenos días. Tus ventas de agosto alcanzan L 1,284,500, un 12% más que el mes anterior. Sin embargo, tienes L 327,450 pendientes de cobro y 4 clientes con facturas vencidas.",
        "Resumen ejecutivo",
        insight_overview,
    ),
    "debt": (
        "¿Quiénes me deben desde 2024? ¿Cuál debería cobrar primero?",
        "Encontré 3 clientes con saldos originados en 2024. Te recomiendo priorizar Grupo Empresarial X: tiene L 94,500 vencidos y es actualmente tu mayor saldo en mora.",
        "Cartera vencida desde 2024",
        insight_debt,
    ),
    "comparison": (
        "¿Cómo vamos comparados con 2025?",
        "Las ventas acumuladas de 2026 han crecido 18.4%, pero tus gastos administrativos aumentaron 11.2%. La rentabilidad continúa por encima del mismo período de 2025.",
        "2026 comparado con 2025",
        insight_comparison,
    ),
    "inventory": (
        "¿Qué productos necesito comprar?",
        "Hay 7 productos con existencia baja. Estos 3 requieren atención inmediata para evitar quedarte sin inventario.",
        "Inventario que requiere atención",
        insight_inventory,
    ),
    "payroll": (
        "¿Cuánto gastamos en planilla este mes? ¿Por qué aumentó?",
        "La planilla fue de L 284,300. Aumentó L 21,400 respecto al mes anterior, principalmente por nuevas contrataciones, horas adicionales y prestaciones pagadas.",
        "Explicación de la planilla",
        insight_payroll,
    ),
    "crm": (
        "¿Qué oportunidades tenemos pendientes?",
        "CRM registra 8 oportunidades abiertas con un valor potencial de L 1.6 millones. Dos requieren seguimiento esta semana.",
        "Embudo comercial",
        insight_crm,
    ),
}


def scene_conversation(spec, local, sec, key):
    img = base(spec, sec)
    brand_header(img, spec, sec, "HABLA CON TU EMPRESA")
    question, answer, insight_title, renderer = CONVERSATIONS[key]
    draw_chat_panel(img, spec, local, question, answer)
    insight_panel(img, spec, local, insight_title, renderer)
    return img


def scene_slogan(spec, local, sec, kind):
    source = real_interface(spec.key)
    crop = cover(source, spec.width, spec.height, 1.04 + local * 0.004)
    crop = crop.filter(ImageFilter.GaussianBlur(2.2))
    crop.alpha_composite(Image.new("RGBA", crop.size, (2, 10, 24, 190)))
    d = ImageDraw.Draw(crop, "RGBA")
    if kind == "search":
        lines = ["NO BUSQUES EL DATO.", "PREGÚNTALO."]
    else:
        lines = ["NO RECORRAS MÓDULO POR MÓDULO.", "ONIX LOS CONECTA."]
    title_size = 70 if not spec.vertical else 64
    line_gap = 105 if not spec.vertical else 94
    total_h = len(lines) * line_gap
    start_y = (spec.height - total_h) // 2
    p = ease(local / 0.65)
    for i, line in enumerate(lines):
        fitted_size = title_size
        fnt = font(fitted_size, True)
        while fitted_size > 34 and tw(d, line, fnt) > spec.width * 0.88:
            fitted_size -= 2
            fnt = font(fitted_size, True)
        x = (spec.width - tw(d, line, fnt)) // 2
        color = CYAN if i == len(lines) - 1 else WHITE
        d.text((x + int(42 * (1 - p) * (-1 if i % 2 == 0 else 1)), start_y + i * line_gap), line, font=fnt, fill=color + (int(255 * p),))
    d.line((spec.width * 0.19, start_y - 50, spec.width * 0.81, start_y - 50), fill=CYAN + (int(85 * p),), width=2)
    return crop


def insight_priorities(layer, d, origin, width, height, spec, local):
    ox, oy = origin
    start_y = oy + 126
    priorities = [
        ("1", "Cobrar L 327,450 vencidos", RED),
        ("2", "Reponer 3 productos críticos", AMBER),
        ("3", "Seguir 2 oportunidades comerciales", CYAN),
        ("4", "Revisar el aumento de gastos", PURPLE),
        ("5", "Rentabilidad sobre 2025", GREEN),
    ]
    row_h = 88 if spec.vertical else 126
    for idx, (number, label, color) in enumerate(priorities):
        p = ease((local - 3.5 - idx * 0.18) / 0.45)
        y = start_y + idx * row_h
        d.rounded_rectangle((ox + 24, y, ox + width - 24, y + row_h - 16), radius=22, fill=(15, 48, 78, int(245 * p)), outline=(255, 255, 255, int(24 * p)), width=1)
        d.ellipse((ox + 43, y + 22, ox + 95, y + 74), fill=color + (int(255 * p),))
        d.text((ox + 62, y + 31), number, font=font(20, True), fill=WHITE + (int(255 * p),))
        d.text((ox + 119, y + 29), label, font=font(17 if not spec.vertical else 19, True), fill=WHITE + (int(255 * p),))
    d.text((ox + 32, oy + height - 47), "5 prioridades · 8 módulos conectados", font=font(16, True), fill=CYAN + (255,))


def module_network(img, spec, local):
    modules = ["Ventas", "Facturación", "CxC", "Bancos", "Inventario", "Contabilidad", "RRHH", "CRM"]
    d = ImageDraw.Draw(img, "RGBA")
    if spec.vertical:
        center = (spec.width // 2, 1135)
        radius_x, radius_y = 380, 170
        logo_size = 108
    else:
        center = (960, 112)
        radius_x, radius_y = 700, 55
        logo_size = 72
    p = ease(local / 1.3)
    for idx, name in enumerate(modules):
        angle = math.pi * 2 * idx / len(modules) - math.pi / 2
        x = center[0] + math.cos(angle) * radius_x
        y = center[1] + math.sin(angle) * radius_y
        d.line((center[0], center[1], x, y), fill=CYAN + (int(55 * p),), width=2)
        fnt = font(15 if not spec.vertical else 18, True)
        box_w = tw(d, name, fnt) + 34
        d.rounded_rectangle((x - box_w / 2, y - 22, x + box_w / 2, y + 22), radius=18, fill=(11, 49, 75, int(235 * p)), outline=CYAN + (int(65 * p),), width=1)
        d.text((x - tw(d, name, fnt) / 2, y - 10), name, font=fnt, fill=WHITE + (int(255 * p),))
    img.alpha_composite(onix_logo(logo_size), (int(center[0] - logo_size / 2), int(center[1] - logo_size / 2)))


def scene_priorities(spec, local, sec):
    img = base(spec, sec, darker=10)
    brand_header(img, spec, sec, "ANÁLISIS INTEGRAL")
    module_network(img, spec, local)
    q = "ONIX, analiza todo y dime qué necesita mi atención."
    a = "Estas son tus prioridades de hoy. Conecté ventas, facturación, cobros, bancos, inventario, contabilidad, RRHH y CRM para ordenar lo más importante."
    draw_chat_panel(img, spec, local, q, a, context="8 MÓDULOS CONECTADOS", compact=True)
    insight_panel(img, spec, local, "Prioridades de hoy", insight_priorities)
    return img


def scene_closing(spec, local, sec):
    img = base(spec, sec, darker=55)
    d = ImageDraw.Draw(img, "RGBA")
    p = ease(local / 0.75)
    logo_size = 240 if not spec.vertical else 270
    logo = onix_logo(logo_size)
    logo_x = (spec.width - logo_size) // 2
    logo_y = 105 if not spec.vertical else 220
    paste_with_opacity(img, logo, (logo_x, logo_y + int(35 * (1 - p))), p)
    lines = [
        ("TU EMPRESA TIENE MILES DE DATOS.", WHITE),
        ("ONIX LOS CONVIERTE EN RESPUESTAS.", CYAN),
    ]
    f_size = 58 if not spec.vertical else 54
    y = 405 if not spec.vertical else 575
    for idx, (line, color) in enumerate(lines):
        fnt = font(f_size, True)
        x = (spec.width - tw(d, line, fnt)) // 2
        d.text((x, y + idx * (f_size + 30)), line, font=fnt, fill=color + (int(255 * p),))
    divider_y = y + 2 * (f_size + 30) + 20
    d.line((spec.width * 0.2, divider_y, spec.width * 0.8, divider_y), fill=CYAN + (int(80 * p),), width=2)
    onix_f = font(76 if not spec.vertical else 82, True)
    talk_f = font(34 if not spec.vertical else 39, True)
    onix_y = divider_y + 36
    d.text(((spec.width - tw(d, "ONIX", onix_f)) // 2, onix_y), "ONIX", font=onix_f, fill=WHITE + (int(255 * p),))
    d.text(((spec.width - tw(d, "HABLA CON TU EMPRESA.", talk_f)) // 2, onix_y + 94), "HABLA CON TU EMPRESA.", font=talk_f, fill=CYAN + (int(255 * p),))
    footer_y = onix_y + (210 if not spec.vertical else 250)
    d.text(((spec.width - tw(d, "DV SOLUTIONS ERP", font(29, True))) // 2, footer_y), "DV SOLUTIONS ERP", font=font(29, True), fill=WHITE + (int(255 * p),))
    d.text(((spec.width - tw(d, "EL SISTEMA SE ADAPTA A TI.", font(22, True))) // 2, footer_y + 48), "EL SISTEMA SE ADAPTA A TI.", font=font(22, True), fill=SOFT + (int(255 * p),))
    return img


def transition(img: Image.Image, local: float):
    if local >= 0.42:
        return img
    p = ease(local / 0.42)
    overlay = Image.new("RGBA", img.size, (2, 18, 35, int(75 * (1 - p))))
    beam = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(beam, "RGBA")
    x = int(-240 + p * (img.width + 480))
    d.polygon([(x - 120, 0), (x + 50, 0), (x + 260, img.height), (x + 90, img.height)], fill=CYAN + (95,))
    img.alpha_composite(beam.filter(ImageFilter.GaussianBlur(35)))
    img.alpha_composite(overlay)
    return img


def frame(spec: VideoSpec, sec: float):
    for start, end, name in SCENES:
        if start <= sec < end:
            local = sec - start
            if name == "intro":
                img = scene_intro(spec, local, sec)
            elif name in CONVERSATIONS:
                img = scene_conversation(spec, local, sec, name)
            elif name == "slogan_search":
                img = scene_slogan(spec, local, sec, "search")
            elif name == "slogan_modules":
                img = scene_slogan(spec, local, sec, "modules")
            elif name == "priorities":
                img = scene_priorities(spec, local, sec)
            else:
                img = scene_closing(spec, local, sec)
            transition(img, local)
            return img.convert("RGB")
    return scene_closing(spec, DURATION - 79.5, sec).convert("RGB")


async def create_voiceovers():
    results = []
    for index, (start, text, voice, rate) in enumerate(VOICE_SEGMENTS):
        path = AUDIO / f"voice_{index:02d}.mp3"
        if not path.exists():
            communicator = edge_tts.Communicate(text, voice, rate=rate, pitch="-2Hz", volume="+8%")
            await communicator.save(str(path))
        results.append((start, path))
    return results


def create_music(path: Path):
    sample_rate = 44100
    total = int(DURATION * sample_rate)
    timeline = np.arange(total, dtype=np.float64) / sample_rate
    music = np.zeros(total, dtype=np.float64)
    chords = [(65.41, 98.00, 130.81), (73.42, 110.00, 146.83), (82.41, 123.47, 164.81), (55.00, 82.41, 110.00)]
    beat = 0.75
    for index in range(int(math.ceil(DURATION / beat))):
        start = int(index * beat * sample_rate)
        end = min(total, int((index + 1) * beat * sample_rate))
        lt = np.arange(end - start, dtype=np.float64) / sample_rate
        chord = chords[(index // 4) % len(chords)]
        env = np.minimum(lt / 0.16, 1) * np.minimum((beat - lt) / 0.22, 1)
        pad = sum(np.sin(2 * np.pi * f * lt) for f in chord) / len(chord)
        pulse = np.sin(2 * np.pi * chord[0] * 2 * lt) * (0.55 + 0.45 * np.sin(2 * np.pi * 2 * lt))
        music[start:end] += (pad * 0.105 + pulse * 0.025) * env
        if index % 2 == 0:
            hit_end = min(end, start + int(0.14 * sample_rate))
            ht = np.arange(hit_end - start) / sample_rate
            music[start:hit_end] += np.sin(2 * np.pi * (65 - 28 * ht) * ht) * np.exp(-24 * ht) * 0.14
    rng = np.random.default_rng(2026)
    for moment in [0, 4.5, 14.5, 24.5, 29, 38, 47, 57, 65, 69.5, 79.5]:
        start = int(moment * sample_rate)
        end = min(total, start + int(0.85 * sample_rate))
        t = np.arange(end - start) / sample_rate
        music[start:end] += np.sin(2 * np.pi * (52 + 95 * t) * t) * np.exp(-5 * t) * 0.16
        noise = rng.normal(0, 1, end - start) * np.exp(-10 * t) * 0.018
        music[start:end] += noise
    for moment in [5.8, 16.2, 30.4, 39.3, 48.4, 58.4, 71.0]:
        start = int(moment * sample_rate)
        end = min(total, start + int(0.18 * sample_rate))
        t = np.arange(end - start) / sample_rate
        music[start:end] += np.sin(2 * np.pi * (880 + 240 * t) * t) * np.exp(-19 * t) * 0.055
    fade = int(1.5 * sample_rate)
    music[:fade] *= np.linspace(0, 1, fade)
    music[-fade:] *= np.linspace(1, 0, fade)
    pcm = np.int16(np.clip(np.column_stack([music, music]), -1, 1) * 32767)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def mux(ffmpeg: Path, silent: Path, voices, music: Path, final: Path):
    command = [str(ffmpeg), "-y", "-i", str(silent), "-i", str(music)]
    for _, path in voices:
        command.extend(["-i", str(path)])
    filters = ["[1:a]volume=0.22[music]"]
    labels = []
    for input_index, (start, _) in enumerate(voices, start=2):
        delay = int(start * 1000)
        label = f"v{input_index}"
        filters.append(f"[{input_index}:a]adelay={delay}|{delay},volume=1.36[{label}]")
        labels.append(f"[{label}]")
    filters.append(f"[music]{''.join(labels)}amix=inputs={1 + len(labels)}:duration=longest:normalize=0,alimiter=limit=0.96[outa]")
    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", "0:v:0", "-map", "[outa]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
        "-t", str(DURATION), "-movflags", "+faststart", str(final),
    ])
    subprocess.run(command, check=True)


def render_spec(spec: VideoSpec, voices, music: Path):
    import imageio_ffmpeg

    silent = OUTPUT / f"ONIX-Habla-Con-Tu-Empresa-{spec.key}-silent.mp4"
    final = OUTPUT / f"ONIX-Habla-Con-Tu-Empresa-{spec.key}.mp4"
    writer = imageio.get_writer(
        silent,
        fps=FPS,
        codec="libx264",
        quality=8,
        macro_block_size=None,
        ffmpeg_params=["-pix_fmt", "yuv420p", "-preset", "medium", "-movflags", "+faststart"],
    )
    try:
        for index in range(FPS * DURATION):
            writer.append_data(np.asarray(frame(spec, index / FPS)))
            if index % (FPS * 5) == 0:
                print(f"Render {spec.key}: {index / FPS:05.1f}s / {DURATION}s", flush=True)
    finally:
        writer.close()
    mux(Path(imageio_ffmpeg.get_ffmpeg_exe()), silent, voices, music, final)
    return final


def create_previews(spec: VideoSpec):
    times = [2.2, 10.5, 20.5, 26.7, 34.0, 43.0, 52.0, 61.0, 67.2, 76.0, 83.0]
    thumbs = []
    for index, sec in enumerate(times):
        image = frame(spec, sec)
        path = OUTPUT / f"preview-{spec.key}-{index + 1:02d}-{int(sec):02d}s.jpg"
        image.save(path, quality=92)
        thumb_w = 480 if not spec.vertical else 270
        thumb_h = int(thumb_w * spec.height / spec.width)
        thumbs.append(image.resize((thumb_w, thumb_h), Image.LANCZOS))
    cols = 4 if not spec.vertical else 3
    rows = math.ceil(len(thumbs) / cols)
    gap = 16
    twidth = thumbs[0].width
    theight = thumbs[0].height
    sheet = Image.new("RGB", (cols * twidth + (cols + 1) * gap, rows * theight + (rows + 1) * gap), NAVY)
    for idx, thumb in enumerate(thumbs):
        x = gap + (idx % cols) * (twidth + gap)
        y = gap + (idx // cols) * (theight + gap)
        sheet.paste(thumb, (x, y))
    path = OUTPUT / f"storyboard-{spec.key}.jpg"
    sheet.save(path, quality=91)
    return path


def main():
    parser = argparse.ArgumentParser(description="Render comercial cinematográfico ONIX: Habla con tu empresa")
    parser.add_argument("--format", choices=["16x9", "9x16", "all"], default="all")
    parser.add_argument("--preview-only", action="store_true")
    args = parser.parse_args()

    selected = list(SPECS.values()) if args.format == "all" else [SPECS[args.format]]
    for spec in selected:
        preview = create_previews(spec)
        print(f"Storyboard {spec.key}: {preview}")
    if args.preview_only:
        return

    voices = asyncio.run(create_voiceovers())
    music = AUDIO / "onix_habla_empresa_cinematic.wav"
    if not music.exists():
        create_music(music)
    for spec in selected:
        final = render_spec(spec, voices, music)
        print(f"Video final {spec.key}: {final}")


if __name__ == "__main__":
    main()
