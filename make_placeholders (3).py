"""
Создаёт картинки-заглушки для всех экранов бота, чтобы на каждом экране была картинка.

Как пользоваться:
    1. pip install pillow
    2. положи этот файл рядом с bots.py и запусти его (в пайчарме: правой кнопкой, Run)
    3. перезапусти бота

Уже существующие картинки он НЕ трогает, создаёт только недостающие.
Захочешь свою картинку: просто положи файл с тем же именем в папку media
(старую заглушку удали или перезапиши) и перезапусти бота.
"""

import ast
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(sys.argv[0]).resolve().parent
MEDIA = BASE / "media"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
W, H = 1000, 560

# Шрифты с русскими буквами. Если ничего не нашлось, положи любой .ttf
# рядом со скриптом и назови его font.ttf
BOLD_FONTS = [
    BASE / "font.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
REGULAR_FONTS = [
    BASE / "font.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def find_font(candidates) -> str:
    for c in candidates:
        if Path(c).exists():
            return str(c)
    sys.exit(
        "Не нашёл шрифт с русскими буквами.\n"
        "Положи любой файл шрифта .ttf рядом со скриптом, назови его font.ttf и запусти снова."
    )


BOLD = find_font(BOLD_FONTS)
REGULAR = find_font(REGULAR_FONTS)

# Цвета: по категории несколько пар (верх, низ), чтобы картинки были разные
PALETTES = {
    "main": [("#1e3c72", "#2a5298"), ("#0f2027", "#2c5364")],
    "theme": [("#b8860b", "#3b2a05"), ("#c2009b", "#12063a"), ("#0a0a0a", "#4a4a4a")],
    "casino": [("#42275a", "#734b6d"), ("#7b1e1e", "#1f1c18"), ("#141e30", "#243b55")],
    "job": [("#614385", "#516395"), ("#134e5e", "#3b7a57")],
    "location": [("#0f2027", "#2c5364"), ("#1d4350", "#a43931"), ("#355c7d", "#6c5b7b"),
                 ("#134e5e", "#3b7a57"), ("#283c86", "#45a247"), ("#232526", "#414345")],
    "shop": [("#8a3b12", "#4b1248"), ("#1f4037", "#99f2c8"), ("#3a1c71", "#d76d77"),
             ("#1a2980", "#26d0ce")],
    "car": [("#232526", "#414345"), ("#3a1c71", "#a3475a"), ("#1f1c2c", "#6e6a8c"),
            ("#0f2027", "#203a43")],
    "house": [("#2c3e50", "#4ca1af"), ("#1a2a6c", "#8a3a3a"), ("#134e5e", "#3b7a57"),
              ("#3a3a52", "#5f7a8a")],
}
LABELS = {
    "main": "ИГРА", "casino": "КАЗИНО", "job": "РАБОТА", "location": "ЛОКАЦИЯ",
    "shop": "МАГАЗИН", "car": "АВТОМОБИЛЬ", "house": "НЕДВИЖИМОСТЬ", "theme": "ТЕМА ПРОФИЛЯ",
}

# Постоянные экраны: имя файла -> (заголовок, подпись, категория)
FIXED = {
    "welcome.jpg": ("Добро пожаловать в Лос-Сантос", "Начни новую жизнь", "main"),
    "avatar_default.jpg": ("Профиль", "Твой персонаж", "main"),
    "work.jpg": ("Работа", "Заработай на жизнь", "main"),
    "bonus.jpg": ("Ежедневный бонус", "Заходи каждый день", "main"),
    "bank.jpg": ("Банк «Мэйз»", "Вклады под проценты", "main"),
    "donate.jpg": ("Донат", "Поддержи игру", "main"),
    "property.jpg": ("Недвижимость", "Найди себе дом", "main"),
    "map.jpg": ("Карта Лос-Сантоса", "Куда поедем?", "main"),
    "garage.jpg": ("Гараж", "Твои машины", "main"),
    "bag.jpg": ("Сумка", "Твои покупки", "main"),
    "casino.jpg": ("Казино «Даймонд»", "Испытай удачу", "casino"),
    "casino_win.jpg": ("Выигрыш!", "Удача на твоей стороне", "casino"),
    "casino_lose.jpg": ("Не повезло", "В следующий раз получится", "casino"),
    "casino_slots.jpg": ("Слоты", "Три в ряд", "casino"),
    "casino_dice.jpg": ("Кости", "Побей крупье", "casino"),
    "casino_coin.jpg": ("Монетка", "Орёл или решка", "casino"),
    "casino_roulette.jpg": ("Рулетка", "Красное, чёрное или зеро", "casino"),
    "theme_gold.jpg": ("Золотая тема", "Профиль в золоте", "theme"),
    "theme_neon.jpg": ("Неоновая тема", "Профиль в неоне", "theme"),
    "theme_noir.jpg": ("Тема «Нуар»", "Чёрно-белое кино", "theme"),
    "job_courier.jpg": ("Курьер по Вайнвуду", "Быстрая доставка", "job"),
    "job_loader.jpg": ("Грузчик в порту", "Тяжёлая работа", "job"),
    "job_mechanic.jpg": ("Механик", "Тюнинг-ателье", "job"),
    "job_taxi.jpg": ("Таксист", "Лос-Сантос по ночам", "job"),
    "job_pilot.jpg": ("Пилот вертолёта", "Над городом", "job"),
    "job_armored.jpg": ("Инкассатор", "Бронированный фургон", "job"),
}


def money(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def find_bot_file() -> Path:
    """
    Ищет файл бота рядом со скриптом. Подойдёт любое имя вида bots*.py,
    например "bots (3).py", главное чтобы это была свежая версия.
    """
    if not (BASE / "tokens.py").exists():
        sys.exit(
            f"Скрипт лежит не в папке проекта: {BASE}\n"
            "Перетащи make_placeholders.py в папку проекта (туда, где лежат bots.py и tokens.py) "
            "и запусти оттуда."
        )

    candidates = sorted(BASE.glob("bots*.py"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        sys.exit(f"Не нашёл файл бота (bots.py) в папке {BASE}")
    for path in candidates:
        if "_LOCATION_DATA" in path.read_text(encoding="utf-8", errors="ignore"):
            return path
    sys.exit(
        "Файл бота в папке есть, но это старая версия без локаций и магазинов: "
        + ", ".join(p.name for p in candidates)
        + "\nЗамени его свежим bots.py."
    )


def load_data() -> dict:
    """Достаёт списки домов, машин, локаций и магазинов прямо из файла бота."""
    BOT_FILE = find_bot_file()
    print(f"Беру данные из: {BOT_FILE.name}")
    tree = ast.parse(BOT_FILE.read_text(encoding="utf-8"))
    wanted = {"_LOCATION_DATA", "_HOUSE_DATA", "_CAR_DATA", "SHOPS"}
    found = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in wanted
        ):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    missing = wanted - set(found)
    if missing:
        sys.exit(f"В bots.py не нашёл: {', '.join(sorted(missing))}. Нужна свежая версия bots.py.")
    return found


def build_cards() -> list[tuple[str, str, str, str]]:
    """Список всех картинок: (файл, заголовок, подпись, категория)."""
    data = load_data()
    cards = [(name, t, s, c) for name, (t, s, c) in FIXED.items()]

    loc_titles = {}
    for lid, title, desc, _x, _y in data["_LOCATION_DATA"]:
        loc_titles[lid] = title
        cards.append((f"loc_{lid}.jpg", title, desc, "location"))

    for shop in data["SHOPS"]:
        sub = loc_titles.get(shop["loc"], "")
        cards.append((f"shop_{shop['id']}.jpg", shop["title"], sub, "shop"))

    for i, (title, price, _desc) in enumerate(
        [(t, p, d) for t, p, d in data["_HOUSE_DATA"]], start=1
    ):
        cards.append((f"house_{i:02d}.jpg", title, f"{money(price)} $", "house"))

    for i, (title, price, speed, body, _desc, _dealer) in enumerate(data["_CAR_DATA"], start=1):
        cards.append((f"car_{i:02d}.jpg", title, f"{body} · {speed} км/ч · {money(price)} $", "car"))

    return cards


def has_image(filename: str) -> bool:
    """Есть ли уже такая картинка (с любым расширением и регистром букв)."""
    stem = Path(filename).stem.lower() + "."
    if not MEDIA.exists():
        return False
    return any(
        f.is_file() and f.name.lower().startswith(stem) and f.suffix.lower() in IMAGE_EXTS
        for f in MEDIA.iterdir()
    )


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def make_card(filename: str, title: str, subtitle: str, category: str, index: int) -> None:
    top, bottom = (hex_to_rgb(c) for c in PALETTES[category][index % len(PALETTES[category])])

    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):  # плавный градиент сверху вниз
        t = y / (H - 1)
        draw.line(
            [(0, y), (W, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )

    # полупрозрачные круги для красоты
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse((-160, -200, 380, 340), fill=(255, 255, 255, 22))
    od.ellipse((W - 380, H - 300, W + 140, H + 220), fill=(255, 255, 255, 18))
    od.rectangle((60, 118, 66, H - 118), fill=(255, 255, 255, 90))  # линия слева
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img)

    label_font = ImageFont.truetype(BOLD, 28)
    draw.text((90, 60), LABELS[category], font=label_font, fill=(255, 255, 255, 190))

    # заголовок: подбираем самый крупный размер, который помещается
    max_w, max_h = W - 180, 250
    for size in range(92, 31, -4):
        font = ImageFont.truetype(BOLD, size)
        lines = wrap(draw, title, font, max_w)
        line_h = int(size * 1.15)
        if len(lines) <= 3 and len(lines) * line_h <= max_h and all(
            draw.textlength(l, font=font) <= max_w for l in lines
        ):
            break

    y = 140
    for line in lines:
        draw.text((92, y + 3), line, font=font, fill=(0, 0, 0, 120))  # тень
        draw.text((90, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h

    if subtitle:
        sub_font = ImageFont.truetype(REGULAR, 34)
        y += 18
        for line in wrap(draw, subtitle, sub_font, max_w)[:2]:
            draw.text((90, y), line, font=sub_font, fill=(255, 255, 255, 215))
            y += 44

    foot = ImageFont.truetype(BOLD, 24)
    draw.text((90, H - 70), "ЛОС-САНТОС", font=foot, fill=(255, 255, 255, 120))

    img.convert("RGB").save(MEDIA / filename, "JPEG", quality=88)


def main() -> None:
    MEDIA.mkdir(exist_ok=True)
    cards = build_cards()

    made = skipped = 0
    counters: dict[str, int] = {}
    for filename, title, subtitle, category in cards:
        index = counters.get(category, 0)
        counters[category] = index + 1
        if has_image(filename):
            skipped += 1
            continue
        make_card(filename, title, subtitle, category, index)
        made += 1

    print(f"Готово. Создано заглушек: {made}, пропущено (уже есть твои картинки): {skipped}")
    print(f"Папка: {MEDIA}")
    print("Теперь перезапусти бота.")


main()
