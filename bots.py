"""
Игровой Telegram-бот: регистрация, профиль, работа, ежедневный бонус,
казино, недвижимость, картинки на каждом экране.
Python 3.12, aiogram 3.x, aiosqlite

Установка:
    pip install aiogram aiosqlite

Структура проекта:
    bots.py      - этот файл
    tokens.py    - BOT_TOKEN = "твой_токен"
    media/       - картинки (какие нужны, видно в консоли при запуске)
"""

import asyncio
import logging
import math
import random
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    InputMediaPhoto,
    LabeledPrice,
    KeyboardButton,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
)
import os

from aiohttp import web


def _load_token() -> str:
    """Токен берётся из переменной окружения BOT_TOKEN, а если её нет, из tokens.py."""
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        try:
            from tokens import BOT_TOKEN as file_token
            token = (file_token or "").strip()
        except ImportError:
            token = ""
    if not token:
        sys.exit(
            "Не найден токен бота. Задай переменную окружения BOT_TOKEN "
            "или создай файл tokens.py со строкой BOT_TOKEN = \"твой_токен\""
        )
    return token


BOT_TOKEN = _load_token()

# ===========================================================================
# НАСТРОЙКИ
# ===========================================================================

# Путь к базе. На сервере можно задать переменной DB_PATH (например /data/game.db)
DB_PATH = os.getenv("DB_PATH", "game.db")

# Папка media ищется рядом с файлом bots.py (а не там, откуда запущен пайчарм)
BASE_DIR = Path(sys.argv[0]).resolve().parent
MEDIA_DIR = BASE_DIR / "media"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

START_BALANCE = 1000  # стартовый баланс в долларах

NICK_REGEX = re.compile(r"^[A-Za-zА-Яа-яЁё0-9_]{3,16}$")

# ---------------------------------------------------------------------------
# Картинки экранов.
# Нет файла в папке media - бот просто покажет текст, ничего не сломается.
# Хочешь картинку для нового экрана: допиши сюда строку и используй
# photo=MEDIA["имя"] там, где отправляешь или редактируешь сообщение.
# ---------------------------------------------------------------------------
MEDIA = {
    # основные экраны
    "welcome": MEDIA_DIR / "welcome.jpg",         # приветствие и ввод ника
    "profile": MEDIA_DIR / "avatar_default.jpg",  # профиль без личного фото
    "work": MEDIA_DIR / "work.jpg",               # меню работы
    "bonus": MEDIA_DIR / "bonus.jpg",             # ежедневный бонус
    # казино (у каждой игры своя картинка ниже, в CASINO_GAMES)
    "casino": MEDIA_DIR / "casino.jpg",           # главное меню казино
    "casino_win": MEDIA_DIR / "casino_win.jpg",   # результат: выиграл
    "casino_lose": MEDIA_DIR / "casino_lose.jpg", # результат: проиграл
    # недвижимость (у каждого дома своя картинка ниже, в HOUSES)
    "property": MEDIA_DIR / "property.jpg",       # меню недвижимости и "Мои дома"
    # банк
    "bank": MEDIA_DIR / "bank.jpg",
    # карта, гараж и сумка (у локаций, магазинов и машин свои картинки ниже)
    "map": MEDIA_DIR / "map.jpg",         # карта и список локаций
    "garage": MEDIA_DIR / "garage.jpg",   # гараж
    "bag": MEDIA_DIR / "bag.jpg",         # сумка с покупками
    "donate": MEDIA_DIR / "donate.jpg",   # донат
    "style": MEDIA_DIR / "style.jpg",     # стиль: значки, титулы, темы
}

# ---------------------------------------------------------------------------
# Бонус
# ---------------------------------------------------------------------------
# Первый день BONUS_BASE, каждый следующий +BONUS_STEP, максимум BONUS_MAX.
BONUS_BASE = 100
BONUS_STEP = 50
BONUS_MAX = 500
BONUS_COOLDOWN = 24 * 3600        # бонус раз в 24 часа
BONUS_STREAK_WINDOW = 48 * 3600   # не забрал за 48 часов - серия сбросится

BONUS_FLAVOR = [
    "Ламар подкинул долю с очередного дела",
    "Старый знакомый вернул должок с процентами",
    "Нашёл конверт за сиденьем угнанной тачки",
    "Инвестиция в акции оправдалась, ты в плюсе",
]

# ---------------------------------------------------------------------------
# Банк
# ---------------------------------------------------------------------------
BANK_RATE_PER_HOUR = 0.001      # 0.1% в час, это около 2.4% в сутки
BANK_MAX_DEPOSIT = 1_000_000    # максимум на вкладе, проценты считаются до этой суммы
BANK_AMOUNTS = [100, 500, 1_000, 5_000, 10_000, 50_000]  # кнопки пополнения и снятия

# ---------------------------------------------------------------------------
# Работа
# ---------------------------------------------------------------------------
# Шанс, что на смене тебя заметят копы, и какую долю заработка заберут
WANTED_CHANCE = 0.10
WANTED_FINE_SHARE = 0.30

#   pay      - (минимум, максимум) заработка в долларах
#   cooldown - сколько секунд отдыхать после смены
#   flavor   - случайные фразы после смены
#   photo    - картинка после смены (нет файла - берётся MEDIA["work"])
JOBS = {
    "courier": {
        "title": "🛵 Курьер по Вайнвуду",
        "pay": (20, 50),
        "cooldown": 60,
        "flavor": [
            "Развёз заказы по холмам Вайнвуда",
            "Обогнал полицейский патруль на скутере",
            "Клиент на вилле оставил щедрые чаевые",
        ],
        "photo": MEDIA_DIR / "job_courier.jpg",
    },
    "loader": {
        "title": "📦 Грузчик в порту",
        "pay": (50, 120),
        "cooldown": 180,
        "flavor": [
            "Разгрузил контейнеры в порту Лос-Сантоса",
            "Кран чуть не уронил груз, но ты вовремя увернулся",
            "Бригадир доволен сменой, спросил только про сроки",
        ],
        "photo": MEDIA_DIR / "job_loader.jpg",
    },
    "mechanic": {
        "title": "🔧 Механик в тюнинг-ателье",
        "pay": (80, 180),
        "cooldown": 240,
        "flavor": [
            "Прокачал спорткар для богатого клиента",
            "Поставил нитро и покрасил тачку в неон",
            "Клиент забрал машину и сказал не задавать вопросов",
        ],
        "photo": MEDIA_DIR / "job_mechanic.jpg",
    },
    "taxi": {
        "title": "🚕 Таксист в Лос-Сантосе",
        "pay": (100, 250),
        "cooldown": 300,
        "flavor": [
            "Довёз туриста до пляжа Веспуччи",
            "Подбросил пассажира к обсерватории Галилео",
            "Пассажир просил ехать быстрее и не смотреть в зеркало",
        ],
        "photo": MEDIA_DIR / "job_taxi.jpg",
    },
    "pilot": {
        "title": "🚁 Пилот вертолёта",
        "pay": (400, 900),
        "cooldown": 600,
        "flavor": [
            "Облетел Лос-Сантос с туристами на борту",
            "Перевёз важного клиента на крышу небоскрёба",
            "Посадил вертолёт на площадку, не задев ни одной антенны",
        ],
        "photo": MEDIA_DIR / "job_pilot.jpg",
    },
    "armored": {
        "title": "🚛 Инкассатор",
        "pay": (1000, 2200),
        "cooldown": 1200,
        "flavor": [
            "Доставил сумки с наличными в хранилище банка",
            "Отбил налёт на бронированный фургон",
            "Рейс прошёл тихо, только охрана нервничала",
        ],
        "photo": MEDIA_DIR / "job_armored.jpg",
    },
}

# ---------------------------------------------------------------------------
# Казино
# ---------------------------------------------------------------------------
# Слоты: (символ, вес выпадения, множитель за три в ряд).
# Чем больше вес, тем чаще символ. Два одинаковых - возвращается половина ставки.
SLOT_SYMBOLS = [
    ("🍒", 30, 7),
    ("🍋", 25, 11),
    ("🔔", 20, 17),
    ("🍀", 12, 35),
    ("💎", 8, 70),
    ("7️⃣", 5, 170),
]

RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

COIN_CHOICES = {"h": "🦅 Орёл", "t": "🪙 Решка"}
COIN_WIN_CHANCE = 0.45  # шанс угадать (было 50%)

ROULETTE_CHOICES = {
    "r": "🔴 Красное",
    "b": "⚫ Чёрное",
    "z": "🟢 Зеро (0, 00)",
    "d1": "1-12",
    "d2": "13-24",
    "d3": "25-36",
}

# Игры, где перед броском надо ещё и выбрать вариант
GAME_CHOICES = {"coin": COIN_CHOICES, "roulette": ROULETTE_CHOICES}

#   bets  - доступные ставки (у каждой игры свои)
#   photo - картинка игры
CASINO_GAMES = {
    "slots": {
        "title": "🎰 Слоты",
        "bets": [10, 50, 100, 500],
        "photo": MEDIA_DIR / "casino_slots.jpg",
        "rules": (
            "Крути барабаны. Три одинаковых символа: "
            + ", ".join(f"{s} x{m}" for s, _, m in SLOT_SYMBOLS)
            + ". Два одинаковых: вернётся половина ставки."
        ),
    },
    "dice": {
        "title": "🎲 Кости",
        "bets": [50, 100, 500, 1000],
        "photo": MEDIA_DIR / "casino_dice.jpg",
        "rules": (
            "Ты и крупье бросаете по две кости, у кого сумма больше, тот победил. "
            "Победа: x2, при ничьей выигрывает крупье."
        ),
    },
    "coin": {
        "title": "🪙 Монетка",
        "bets": [20, 100, 500, 2000],
        "photo": MEDIA_DIR / "casino_coin.jpg",
        "rules": f"Выбери орла или решку. Шанс угадать {int(COIN_WIN_CHANCE * 100)}%, выигрыш x2.",
    },
    "roulette": {
        "title": "🎡 Рулетка",
        "bets": [100, 500, 1000, 5000],
        "photo": MEDIA_DIR / "casino_roulette.jpg",
        "rules": (
            "Выбери, на что ставить: красное или чёрное x2, "
            "дюжина (1-12, 13-24, 25-36) x3, зеро (0 или 00) x17. "
            "Рулетка американская: на колесе есть и 0, и 00."
        ),
    },
}

# ---------------------------------------------------------------------------
# Недвижимость: (название, цена в долларах, описание). Номер = порядок в списке.
# Картинка дома: media/house_01.jpg, house_02.jpg и так далее.
# ---------------------------------------------------------------------------
_HOUSE_DATA = [
    ("Хижина в Сэнди-Шорс", 2_500, "Крыша есть, остальное достроишь сам"),
    ("Трейлер в Грейпсиде", 4_000, "Тихо, пыльно, и соседи только коровы"),
    ("Номер в мотеле Палето", 6_000, "Дешёвый ночлег с видом на парковку"),
    ("Домик у озера Аламо", 9_000, "Рыбалка прямо с крыльца"),
    ("Квартира в Стробери", 15_000, "Недорого и недалеко от центра"),
    ("Бунгало в Дэвисе", 22_000, "Свой дворик и громкий район"),
    ("Квартира в Дель-Перро", 35_000, "Два шага до пирса"),
    ("Дом у пляжа Веспуччи", 55_000, "Пальмы, роллеры и закаты каждый вечер"),
    ("Лофт в даунтауне", 80_000, "Высокие потолки и вид на небоскрёбы"),
    ("Коттедж в Палето-Бэй", 110_000, "Тишина, лес и свежий воздух"),
    ("Дом в Мирор-Парке", 150_000, "Модный район с видом на озеро"),
    ("Таунхаус в Вайнвуде", 210_000, "Рядом знаменитые бульвары"),
    ("Вилла в Ричмане", 300_000, "Забор повыше и охрана у ворот"),
    ("Дом на холмах Вайнвуда", 420_000, "Вид на весь город с высоты"),
    ("Пентхаус в даунтауне", 600_000, "Лифт прямо в квартиру"),
    ("Ранчо в округе Блэйн", 800_000, "Простор, лошади и вертолётная площадка"),
    ("Особняк в Рокфорд-Хиллз", 1_100_000, "Бассейн, повар и очень много комнат"),
    ("Вилла в Пасифик-Блаффс", 1_600_000, "Собственный выход к океану"),
    ("Резиденция в Бэнхэм-Каньоне", 2_500_000, "Закрытая территория и личный пирс"),
    ("Мега-особняк в Тонгва-Хиллз", 5_000_000, "Вершина мечты любого жителя Лос-Сантоса"),
]

HOUSES = [
    {
        "id": i,
        "title": title,
        "price": price,
        "desc": desc,
        "photo": MEDIA_DIR / f"house_{i:02d}.jpg",
    }
    for i, (title, price, desc) in enumerate(_HOUSE_DATA, start=1)
]
HOUSES_BY_ID = {h["id"]: h for h in HOUSES}

# ---------------------------------------------------------------------------
# Локации, такси и машины
# ---------------------------------------------------------------------------
START_LOCATION = "downtown"  # здесь появляется новый игрок
TAXI_BASE_FARE = 15          # посадка в такси, $
TAXI_FARE_PER_KM = 6         # такси, $ за километр
FUEL_PER_KM = 1.5            # бензин на своей машине, $ за километр
SECONDS_PER_KM = 8           # сколько секунд занимает километр на такси
TAXI_SPEED = 150             # скорость такси (км/ч), с ней сравниваются машины
ROAD_FACTOR = 1.3            # дороги длиннее, чем по прямой
MIN_TRAVEL_SECONDS = 10      # самая короткая поездка на такси

# (id, название, описание, x, y). x и y - примерные координаты на карте в километрах,
# по ним считается расстояние между локациями.
_LOCATION_DATA = [
    ("downtown", "Даунтаун Лос-Сантоса", "Небоскрёбы, офисы и деловой центр города", 0.0, -0.8),
    ("vinewood", "Вайнвуд", "Бульвары, клубы и неоновые вывески", 0.5, 0.3),
    ("rockford", "Рокфорд-Хиллз", "Дорогие бутики и особняки за высокими заборами", -0.9, -0.2),
    ("vespucci", "Веспуччи-Бич", "Пляж, скейтеры и закаты над океаном", -1.3, -1.6),
    ("davis", "Дэвис", "Шумный район с автосалонами и гаражами", 0.1, -1.8),
    ("mirror", "Мирор-Парк", "Модный район у озера", 1.2, -0.5),
    ("port", "Порт Лос-Сантоса", "Контейнеры, краны и склады", 0.8, -2.9),
    ("airport", "Аэропорт LSIA", "Самолёты, такси и вечная суета", -1.3, -2.9),
    ("sandy", "Сэнди-Шорс", "Пыльный городок посреди пустыни", 1.9, 3.7),
    ("grapeseed", "Грейпсид", "Ферма, поля и тихая жизнь", 1.7, 4.9),
    ("paleto", "Палето-Бэй", "Спокойный городок на севере у моря", -0.2, 6.3),
]
# картинка локации: media/loc_<id>.jpg, например loc_vinewood.jpg
LOCATIONS = {
    lid: {
        "id": lid,
        "title": title,
        "desc": desc,
        "x": x,
        "y": y,
        "photo": MEDIA_DIR / f"loc_{lid}.jpg",
    }
    for lid, title, desc, x, y in _LOCATION_DATA
}

# ---------------------------------------------------------------------------
# Магазины. items: (ключ, название, цена в долларах).
# У автосалонов вместо items стоит dealer=True, а машины берутся из списка CARS.
# Картинка магазина: media/shop_<id>.jpg (нет файла - покажется картинка локации).
# ---------------------------------------------------------------------------
SHOPS = [
    {
        "id": "mart_dt", "loc": "downtown", "emoji": "🏪", "title": "Магазин 24/7",
        "desc": "Круглосуточный магазин на углу",
        "items": [("water", "Бутылка воды", 3), ("sandwich", "Сэндвич", 7),
                  ("energy", "Энергетик", 5), ("coffee", "Кофе", 4)],
    },
    {
        "id": "pdm", "loc": "downtown", "emoji": "🚘", "title": "Premium Deluxe Motorsport",
        "desc": "Спорткары и маслкары для тех, кто любит скорость", "dealer": True,
    },
    {
        "id": "burger_vw", "loc": "vinewood", "emoji": "🍔", "title": "Бургер-Шот",
        "desc": "Быстрая еда прямо на бульваре",
        "items": [("burger", "Бургер", 9), ("fries", "Картошка фри", 5),
                  ("cola", "Кола", 3), ("shake", "Молочный коктейль", 6)],
    },
    {
        "id": "electro_vw", "loc": "vinewood", "emoji": "📱", "title": "Электроника «Либерти»",
        "desc": "Телефоны, камеры и всё для блогеров",
        "items": [("phone", "Смартфон", 650), ("headphones", "Наушники", 120),
                  ("camera", "Фотоаппарат", 480), ("speaker", "Колонка", 90)],
    },
    {
        "id": "ponsonbys", "loc": "rockford", "emoji": "👔", "title": "Понсонбис",
        "desc": "Дорогая одежда для тех, кто не смотрит на ценники",
        "items": [("suit", "Костюм", 1800), ("coat", "Пальто", 1200),
                  ("shoes", "Туфли", 650), ("glasses", "Солнечные очки", 350)],
    },
    {
        "id": "vangelico", "loc": "rockford", "emoji": "💎", "title": "Ванджелико",
        "desc": "Ювелирный магазин с охраной на каждом шагу",
        "items": [("chain", "Золотая цепочка", 4500), ("ring", "Кольцо с бриллиантом", 12000),
                  ("watch", "Швейцарские часы", 25000), ("necklace", "Колье", 38000)],
    },
    {
        "id": "legend", "loc": "rockford", "emoji": "🚘", "title": "Автосалон «Легенд»",
        "desc": "Люксовые внедорожники и суперкары", "dealer": True,
    },
    {
        "id": "surf_vp", "loc": "vespucci", "emoji": "🏄", "title": "Сёрф-шоп",
        "desc": "Всё для пляжа и волн",
        "items": [("board", "Доска для сёрфинга", 420), ("wetsuit", "Гидрокостюм", 180),
                  ("sandals", "Шлёпанцы", 25), ("sunscreen", "Крем от загара", 12)],
    },
    {
        "id": "cluck_vp", "loc": "vespucci", "emoji": "🍗", "title": "Клакин Белл",
        "desc": "Курица во всех видах",
        "items": [("bucket", "Ведро курицы", 14), ("wrap", "Ролл", 8),
                  ("nuggets", "Наггетсы", 6), ("soda", "Газировка", 3)],
    },
    {
        "id": "simeon", "loc": "davis", "emoji": "🚘", "title": "Автосалон «Симеон»",
        "desc": "Подержанные тачки и классика по честным ценам", "dealer": True,
    },
    {
        "id": "binco_dv", "loc": "davis", "emoji": "👕", "title": "Бинко",
        "desc": "Одежда подешевле",
        "items": [("tshirt", "Футболка", 15), ("jeans", "Джинсы", 35),
                  ("sneakers", "Кроссовки", 45), ("cap", "Кепка", 12)],
    },
    {
        "id": "parts_dv", "loc": "davis", "emoji": "🔧", "title": "Автозапчасти",
        "desc": "Масло, шины и всё для ремонта",
        "items": [("oil", "Моторное масло", 25), ("tires", "Комплект шин", 320),
                  ("battery", "Аккумулятор", 140), ("wax", "Полироль", 18)],
    },
    {
        "id": "coffee_mp", "loc": "mirror", "emoji": "☕", "title": "Кофейня у озера",
        "desc": "Уютное место для модных людей",
        "items": [("latte", "Латте", 5), ("croissant", "Круассан", 4),
                  ("cake", "Чизкейк", 6), ("beans", "Зёрна кофе", 18)],
    },
    {
        "id": "vinyl_mp", "loc": "mirror", "emoji": "🎵", "title": "Магазин винила",
        "desc": "Пластинки, гитары и звуковое железо",
        "items": [("record", "Виниловая пластинка", 25), ("guitar", "Гитара", 380),
                  ("amp", "Комбоусилитель", 220), ("poster", "Постер", 12)],
    },
    {
        "id": "diner_port", "loc": "port", "emoji": "🍳", "title": "Закусочная «Докер»",
        "desc": "Сытный завтрак для портовых рабочих",
        "items": [("breakfast", "Завтрак", 9), ("coffee", "Кофе", 3),
                  ("burger", "Бургер", 8), ("pie", "Пирог", 5)],
    },
    {
        "id": "gear_port", "loc": "port", "emoji": "🦺", "title": "Портовая лавка",
        "desc": "Спецодежда и инструменты",
        "items": [("gloves", "Перчатки", 15), ("helmet", "Каска", 45),
                  ("boots", "Рабочие ботинки", 90), ("flashlight", "Фонарик", 20)],
    },
    {
        "id": "duty_ap", "loc": "airport", "emoji": "🛍", "title": "Дьюти-фри",
        "desc": "Подарки и сувениры перед вылетом",
        "items": [("perfume", "Духи", 85), ("chocolate", "Шоколад", 12),
                  ("luggage", "Чемодан", 240), ("glasses", "Солнечные очки", 120)],
    },
    {
        "id": "mart_sd", "loc": "sandy", "emoji": "🏪", "title": "Магазин 24/7",
        "desc": "Единственный магазин на всю округу",
        "items": [("water", "Бутылка воды", 3), ("jerky", "Вяленое мясо", 6),
                  ("hat", "Ковбойская шляпа", 25), ("sunscreen", "Крем от загара", 9)],
    },
    {
        "id": "sandy_auto", "loc": "sandy", "emoji": "🚘", "title": "Пикапы Сэнди-Шорс",
        "desc": "Пикапы и внедорожники для бездорожья", "dealer": True,
    },
    {
        "id": "farm_gs", "loc": "grapeseed", "emoji": "🌾", "title": "Ферма Грейпсида",
        "desc": "Свежие продукты прямо с фермы",
        "items": [("eggs", "Яйца", 5), ("milk", "Молоко", 4),
                  ("honey", "Мёд", 12), ("cheese", "Сыр", 14)],
    },
    {
        "id": "fish_pl", "loc": "paleto", "emoji": "🎣", "title": "Рыбацкая лавка",
        "desc": "Снасти и наживка для любителей рыбалки",
        "items": [("rod", "Удочка", 85), ("lure", "Блесна", 12),
                  ("bait", "Наживка", 6), ("hat", "Панама", 18)],
    },
    {
        "id": "wood_pl", "loc": "paleto", "emoji": "🪓", "title": "Лесопилка",
        "desc": "Инструменты и одежда для жизни в лесу",
        "items": [("axe", "Топор", 45), ("boots", "Сапоги", 80),
                  ("jacket", "Куртка", 120), ("tent", "Палатка", 160)],
    },
]
for _shop in SHOPS:
    _shop["photo"] = MEDIA_DIR / f"shop_{_shop['id']}.jpg"
SHOPS_BY_ID = {sh["id"]: sh for sh in SHOPS}

# все товары: "магазин:ключ" -> (магазин, название, цена)
ITEMS_BY_KEY = {
    f"{sh['id']}:{key}": (sh, name, price)
    for sh in SHOPS
    for key, name, price in sh.get("items", [])
}

# ---------------------------------------------------------------------------
# Каталог машин (американские). Номер = порядок в списке.
# (название, цена в $, макс. скорость км/ч, кузов, описание, салон)
# Скорость влияет на время поездки на своей машине.
# Картинка: media/car_01.jpg, car_02.jpg и так далее.
# ---------------------------------------------------------------------------
_CAR_DATA = [
    # Автосалон «Симеон» (Дэвис): подержанные и маслкары
    ("Ford Crown Victoria", 6_000, 180, "Седан", "Бывшая полицейская лошадка, надёжная как молоток", "simeon"),
    ("Chrysler 300", 38_000, 210, "Седан", "Солидный седан для делового человека", "simeon"),
    ("Chevrolet Impala 1967", 45_000, 190, "Классика", "Легенда американских дорог", "simeon"),
    ("Dodge Charger R/T", 52_000, 235, "Маслкар", "Рёв V8 на каждом светофоре", "simeon"),
    ("Ford Mustang GT", 58_000, 250, "Маслкар", "Самый узнаваемый пони на дороге", "simeon"),
    ("Chevrolet Camaro SS", 62_000, 250, "Маслкар", "Вечный соперник Мустанга", "simeon"),
    ("Pontiac Firebird Trans Am", 75_000, 200, "Классика", "Звезда старых боевиков", "simeon"),
    # Пикапы Сэнди-Шорс: пикапы и внедорожники
    ("Chevrolet Silverado", 18_000, 175, "Пикап", "Рабочая лошадка для любой дороги", "sandy_auto"),
    ("Jeep Wrangler", 24_000, 160, "Внедорожник", "Пустыня ему нипочём", "sandy_auto"),
    ("Ford F-150", 32_000, 190, "Пикап", "Самый популярный пикап страны", "sandy_auto"),
    ("Ford Bronco", 70_000, 180, "Внедорожник", "Возвращение легенды бездорожья", "sandy_auto"),
    ("Ram 1500 TRX", 85_000, 190, "Пикап", "Пикап с мотором как у маслкара", "sandy_auto"),
    ("Tesla Cybertruck", 95_000, 180, "Пикап", "Выглядит так, будто приехал из будущего", "sandy_auto"),
    # Premium Deluxe Motorsport (Даунтаун): спорт и маслкары
    ("Dodge Challenger Hellcat", 85_000, 320, "Маслкар", "Больше 700 лошадей под капотом", "pdm"),
    ("Tesla Model S Plaid", 110_000, 322, "Электрокар", "Тихий, но разгоняется быстрее многих суперкаров", "pdm"),
    ("Ford Shelby GT500", 130_000, 290, "Маслкар", "Мустанг на стероидах", "pdm"),
    ("Chevrolet Corvette Stingray", 140_000, 310, "Спорткар", "Американский ответ европейским суперкарам", "pdm"),
    ("Dodge Viper ACR", 190_000, 285, "Спорткар", "Двигатель V10 и характер змеи", "pdm"),
    ("Shelby Cobra 427", 450_000, 260, "Раритет", "Коллекционная легенда 60-х", "pdm"),
    # Автосалон «Легенд» (Рокфорд-Хиллз): люкс и суперкары
    ("Cadillac Escalade", 95_000, 200, "Люксовый внедорожник", "Любимая машина звёзд и охраны", "legend"),
    ("Lincoln Navigator", 100_000, 210, "Люксовый внедорожник", "Представительский класс по-американски", "legend"),
    ("Chevrolet Corvette ZR1", 300_000, 340, "Суперкар", "Самый яростный Корвет с наддувом", "legend"),
    ("Ford GT", 500_000, 350, "Суперкар", "Гоночные гены в дорожной версии", "legend"),
    ("SSC Tuatara", 2_000_000, 450, "Гиперкар", "Рекордсмен скорости родом из Америки", "legend"),
    ("Hennessey Venom F5", 2_100_000, 480, "Гиперкар", "Техасский гиперкар для тех, кому мало скорости", "legend"),
]
CARS = [
    {
        "id": i,
        "title": title,
        "price": price,
        "speed": speed,
        "body": body,
        "desc": desc,
        "dealer": dealer,
        "photo": MEDIA_DIR / f"car_{i:02d}.jpg",
    }
    for i, (title, price, speed, body, desc, dealer) in enumerate(_CAR_DATA, start=1)
]
CARS_BY_ID = {c["id"]: c for c in CARS}


def shops_in(loc_id: str) -> list[dict]:
    return [sh for sh in SHOPS if sh["loc"] == loc_id]


def cars_of(dealer_id: str) -> list[dict]:
    """Машины одного автосалона от дешёвых к дорогим."""
    return sorted((c for c in CARS if c["dealer"] == dealer_id), key=lambda c: c["price"])


def distance_km(a: dict, b: dict) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"]) * ROAD_FACTOR


def taxi_trip(km: float) -> tuple[int, int]:
    """(стоимость такси в $, секунд в пути)"""
    fare = round(TAXI_BASE_FARE + km * TAXI_FARE_PER_KM)
    seconds = max(MIN_TRAVEL_SECONDS, int(km * SECONDS_PER_KM))
    return fare, seconds


def car_trip(km: float, car: dict) -> tuple[int, int]:
    """(бензин в $, секунд в пути). Чем быстрее машина, тем короче дорога."""
    fuel = max(1, round(km * FUEL_PER_KM))
    seconds = max(MIN_TRAVEL_SECONDS // 2, int(km * SECONDS_PER_KM * TAXI_SPEED / car["speed"]))
    return fuel, seconds


def is_traveling(player: dict) -> bool:
    return bool(player["travel_to"]) and player["travel_until"] > int(time.time())


def current_location(player: dict) -> dict:
    return LOCATIONS.get(player["location"], LOCATIONS[START_LOCATION])


def active_car(player: dict) -> dict | None:
    return CARS_BY_ID.get(player["car_id"])


def location_label(player: dict) -> str:
    if is_traveling(player):
        return f"в пути, едешь в {LOCATIONS[player['travel_to']]['title']}"
    return current_location(player)["title"]


def car_label(player: dict) -> str:
    car = active_car(player)
    return car["title"] if car else "нет"


# ---------------------------------------------------------------------------
# Сытость и вода
# ---------------------------------------------------------------------------
SATIETY_DECAY_PER_HOUR = 4     # сытость падает на 4 пункта в час (с 100 до 0 за 25 часов)
HYDRATION_DECAY_PER_HOUR = 6   # вода падает на 6 пунктов в час (с 100 до 0 за ~17 часов)
NEEDS_LOW = 20                 # ниже этого персонаж голоден или хочет пить
NEEDS_GOOD = 70                # от этого и выше персонаж сыт и бодр
HUNGRY_PENALTY = 0.75          # голоден или хочет пить: зарплата -25%
WELLFED_BONUS = 1.10           # сыт и напился: зарплата +10%

# Что можно съесть или выпить: ключ товара -> (сытость, вода, сколько секунд
# отдыха после смены убрать). Максимум сытости и воды: 100.
CONSUMABLES = {
    "water": (0, 35, 0),
    "sandwich": (25, 5, 0),
    "energy": (0, 15, 120),
    "coffee": (0, 12, 60),
    "burger": (35, 0, 0),
    "fries": (15, 0, 0),
    "cola": (3, 20, 0),
    "shake": (10, 15, 0),
    "bucket": (60, 0, 0),
    "wrap": (30, 0, 0),
    "nuggets": (20, 0, 0),
    "soda": (2, 20, 0),
    "latte": (8, 15, 60),
    "croissant": (15, 0, 0),
    "cake": (20, 0, 0),
    "breakfast": (50, 10, 0),
    "pie": (20, 0, 0),
    "chocolate": (10, 0, 30),
    "jerky": (20, 0, 0),
    "eggs": (10, 0, 0),
    "milk": (8, 20, 0),
    "honey": (10, 0, 0),
    "cheese": (20, 0, 0),
}

# название товара по ключу (для сообщений)
CONSUMABLE_NAMES = {}
for _key, (_sh, _name, _price) in ITEMS_BY_KEY.items():
    _short = _key.split(":", 1)[1]
    if _short in CONSUMABLES:
        CONSUMABLE_NAMES.setdefault(_short, _name)


def project_needs(satiety: float, hydration: float, ts: int, now: int) -> tuple[float, float, int]:
    """Сытость и вода с учётом прошедшего времени. Вернёт (сытость, вода, now)."""
    if ts <= 0:
        return satiety, hydration, now
    hours = max(0, now - ts) / 3600
    return (
        max(0.0, satiety - SATIETY_DECAY_PER_HOUR * hours),
        max(0.0, hydration - HYDRATION_DECAY_PER_HOUR * hours),
        now,
    )


def needs_multiplier(satiety: float, hydration: float) -> float:
    """Во сколько раз меняется зарплата из-за голода и жажды."""
    low = min(satiety, hydration)
    if low < NEEDS_LOW:
        return HUNGRY_PENALTY
    if low >= NEEDS_GOOD:
        return WELLFED_BONUS
    return 1.0


def mult_note(mult: float) -> str:
    if mult == 1.0:
        return ""
    percent = round((mult - 1) * 100)
    if mult < 1:
        return f"🥵 Голод или жажда: {percent}% к оплате"
    return f"😋 Сыт и напился: +{percent}% к оплате"


def needs_note(player: dict) -> str:
    return mult_note(needs_multiplier(player["satiety"], player["hydration"]))


def bar(value: float, size: int = 10) -> str:
    filled = round(max(0.0, min(100.0, value)) / 100 * size)
    return "█" * filled + "░" * (size - filled)


def effect_label(key: str) -> str:
    sat, hyd, rest = CONSUMABLES[key]
    parts = []
    if sat:
        parts.append(f"+{sat}🍔")
    if hyd:
        parts.append(f"+{hyd}💧")
    if rest:
        parts.append(f"⚡-{rest}с")
    return " ".join(parts)


def aggregate_inventory(inventory: dict[str, int]) -> list[dict]:
    """Объединяет одинаковые товары из разных магазинов (бургер из двух мест = одна строка)."""
    agg: dict[tuple[str, str], dict] = {}
    for item_key, qty in inventory.items():
        info = ITEMS_BY_KEY.get(item_key)
        if not info:
            continue
        _, name, price = info
        short = item_key.split(":", 1)[1]
        entry = agg.setdefault(
            (short, name), {"key": short, "name": name, "qty": 0, "value": 0}
        )
        entry["qty"] += qty
        entry["value"] += price * qty
    return sorted(agg.values(), key=lambda e: e["name"])


# ---------------------------------------------------------------------------
# Донат: звёзды Telegram, оплата картой, VIP и косметика
# ---------------------------------------------------------------------------
ADMIN_IDS: list[int] = [
    int(x) for x in os.getenv("ADMIN_IDS", "6103793904").replace(" ", "").split(",") if x
]  # твой Telegram id (можно несколько через запятую): нужен для /give, /refund, /backup
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "@gitdad")  # куда писать по вопросам оплаты
CASINO_ENABLED = True            # False закроет казино, если решишь не мешать его с донатом

# Оплата картой идёт ВНЕ Telegram: по ссылке на платёжную страницу (Boosty, ЮMoney,
# CloudTips и т.п.). Товар после проверки платежа выдаёшь ты командой /give.
CARD_PAY_URL = os.getenv("CARD_PAY_URL", "https://t.me/tribute/app?startapp=dRzj")  # ссылка на оплату картой. Пусто = кнопка скрыта
CARD_CURRENCY = os.getenv("CARD_CURRENCY", "€")  # валюта цены при оплате картой
CARD_PRICE_PER_STAR = 1.5        # цена одной звезды при оплате картой
CARD_CLAIM_COOLDOWN = 120        # раз в сколько секунд игрок может нажать «Я оплатил»

FIRST_PURCHASE_BONUS = 0.5       # первая покупка игровых денег: +50% бонусом

# VIP-статус: бонусы, пока он активен
VIP_PAY_MULT = 1.25              # зарплата +25%
VIP_COOLDOWN_MULT = 0.8          # отдых после смены короче на 20%
VIP_BONUS_MULT = 2               # ежедневный бонус x2

# Категории косметики: (эмодзи, название, колонка в базе, одно слово)
COSMETIC_CATEGORIES = {
    "badge": ("🏷", "Значки", "eq_badge", "Значок"),
    "title": ("📜", "Титулы", "eq_title", "Титул"),
    "theme": ("🖼", "Темы профиля", "eq_theme", "Тема"),
    "effect": ("🎆", "Эффекты выигрыша", "eq_effect", "Эффект"),
}

# Косметика продаётся только за донат.
#   badge  - значок рядом с ником        (icon)
#   title  - титул под ником             (name)
#   theme  - картинка и рамка профиля    (photo, frame = (верх, низ))
#   effect - украшение при выигрыше      (line)
COSMETICS = {
    "b_fire": {"cat": "badge", "name": "🔥 Огонь", "icon": "🔥", "stars": 30},
    "b_skull": {"cat": "badge", "name": "💀 Череп", "icon": "💀", "stars": 30},
    "b_eagle": {"cat": "badge", "name": "🦅 Орёл", "icon": "🦅", "stars": 40},
    "b_diamond": {"cat": "badge", "name": "💎 Бриллиант", "icon": "💎", "stars": 60},
    "b_crown": {"cat": "badge", "name": "👑 Корона", "icon": "👑", "stars": 100},
    "t_boss": {"cat": "title", "name": "Босс Лос-Сантоса", "stars": 50},
    "t_legend": {"cat": "title", "name": "Легенда Вайнвуда", "stars": 50},
    "t_racer": {"cat": "title", "name": "Король дорог", "stars": 50},
    "t_tycoon": {"cat": "title", "name": "Магнат", "stars": 80},
    "t_ghost": {"cat": "title", "name": "Призрак ночного города", "stars": 80},
    "th_gold": {
        "cat": "theme", "name": "Золотая", "stars": 120,
        "photo": MEDIA_DIR / "theme_gold.jpg",
        "frame": ("✦━━━━━━━━━━━━━━✦", "✦━━━━━━━━━━━━━━✦"),
    },
    "th_neon": {
        "cat": "theme", "name": "Неон", "stars": 120,
        "photo": MEDIA_DIR / "theme_neon.jpg",
        "frame": ("▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰", "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰"),
    },
    "th_noir": {
        "cat": "theme", "name": "Нуар", "stars": 120,
        "photo": MEDIA_DIR / "theme_noir.jpg",
        "frame": ("┏━━━━━━━━━━━━━━┓", "┗━━━━━━━━━━━━━━┛"),
    },
    "e_fire": {"cat": "effect", "name": "Фейерверк", "stars": 60, "line": "🎆🎇🎆🎇🎆🎇🎆"},
    "e_gold": {"cat": "effect", "name": "Золотой дождь", "stars": 60, "line": "💰🪙💰🪙💰🪙💰"},
}

# Товары: stars - цена в звёздах, money - игровые доллары, vip_days - дней VIP,
# cosmetics - какие вещи из COSMETICS идут в комплекте
DONATE_PRODUCTS = {
    "pack_start": {"title": "Стартовый набор", "stars": 150, "money": 50_000,
                   "vip_days": 7, "cosmetics": ["b_fire"]},
    "pack_boss": {"title": "Набор босса", "stars": 600, "money": 200_000,
                  "vip_days": 30, "cosmetics": ["b_crown", "t_boss", "th_gold"]},
    "m1": {"title": "Кошелёк", "stars": 50, "money": 10_000, "vip_days": 0},
    "m2": {"title": "Пачка налички", "stars": 100, "money": 22_000, "vip_days": 0},
    "m3": {"title": "Чемодан", "stars": 250, "money": 60_000, "vip_days": 0},
    "m4": {"title": "Сейф", "stars": 500, "money": 130_000, "vip_days": 0},
    "m5": {"title": "Хранилище", "stars": 1000, "money": 280_000, "vip_days": 0},
    "vip7": {"title": "VIP на 7 дней", "stars": 100, "money": 0, "vip_days": 7},
    "vip30": {"title": "VIP на 30 дней", "stars": 300, "money": 0, "vip_days": 30},
}
DONATE_MENU = list(DONATE_PRODUCTS)  # что показывать в меню доната (косметика - в «Стиле»)

# каждая вещь косметики тоже продаётся отдельно
for _cid, _item in COSMETICS.items():
    DONATE_PRODUCTS[_cid] = {
        "title": _item["name"], "stars": _item["stars"],
        "money": 0, "vip_days": 0, "cosmetics": [_cid],
    }
for _prod in DONATE_PRODUCTS.values():
    _prod.setdefault("cosmetics", [])

PAYSUPPORT_TEXT = (
    "💳 Проблемы с оплатой\n\n"
    "Если оплата прошла, а товар не пришёл, или произошла ошибка, напиши "
    f"{SUPPORT_CONTACT} и укажи номер платежа (он был в сообщении после покупки). "
    "Разберёмся и при необходимости вернём деньги или звёзды."
)
SUPPORT_TEXT = f"🆘 Поддержка\n\nПо любым вопросам про игру пиши {SUPPORT_CONTACT}"
TERMS_TEXT = (
    "📄 Условия донатов\n\n"
    "1. Донат добровольный. За звёзды Telegram или оплату картой ты получаешь игровые "
    "деньги, VIP-статус и украшения профиля.\n"
    "2. Это цифровые товары внутри игры. Их нельзя обменять на реальные деньги.\n"
    "3. Если оплата прошла, а товар не выдан, или была ошибка, напиши /paysupport. "
    "Мы разберёмся и вернём деньги или звёзды.\n"
    "4. При возврате выданные за платёж игровые деньги, дни VIP и украшения списываются.\n"
    "5. Цены, наборы и правила игры могут меняться."
)

# заявки «Я оплатил картой»: игрок -> время последней заявки
CARD_CLAIMS: dict[int, float] = {}


def card_price(product: dict) -> int:
    return max(1, round(product["stars"] * CARD_PRICE_PER_STAR))


def is_vip(player: dict) -> bool:
    return player["vip_until"] > int(time.time())


def fmt_vip_left(seconds: int) -> str:
    days, rest = divmod(max(0, int(seconds)), 86400)
    hours, rest = divmod(rest, 3600)
    return f"{days} д {hours} ч" if days else f"{hours} ч {rest // 60} мин"


def vip_line(player: dict) -> str:
    if not is_vip(player):
        return ""
    return f"👑 VIP: осталось {fmt_vip_left(player['vip_until'] - int(time.time()))}\n"


def vip_perks() -> str:
    pay = round((VIP_PAY_MULT - 1) * 100)
    rest = round((1 - VIP_COOLDOWN_MULT) * 100)
    return f"+{pay}% к зарплате, бонус дня x{VIP_BONUS_MULT}, отдых после смены короче на {rest}%"


def equipped(player: dict, cat: str) -> dict | None:
    """Надетая вещь косметики этой категории (или None)."""
    return COSMETICS.get(player.get(COSMETIC_CATEGORIES[cat][2], ""))


def nick_label(player: dict) -> str:
    badge = equipped(player, "badge")
    return f"{badge['icon']} {player['nickname']}" if badge else player["nickname"]


def title_line(player: dict) -> str:
    title = equipped(player, "title")
    return f"Титул: {title['name']}\n" if title else ""


def profile_header(player: dict) -> str:
    theme = equipped(player, "theme")
    if theme:
        top, bottom = theme["frame"]
        return f"{top}\n👤 Профиль\n{bottom}"
    return "👤 Профиль"


def theme_photo(player: dict):
    theme = equipped(player, "theme")
    return theme["photo"] if theme else None


def effect_line(player: dict) -> str:
    effect = equipped(player, "effect")
    return effect["line"] if effect else ""


def product_lines(product: dict, first_bonus: bool = False) -> list[str]:
    """Что входит в товар (для описания счёта и экрана товара)."""
    lines = []
    if product["money"]:
        text = f"💵 {money(product['money'])} $"
        if first_bonus:
            bonus = int(product["money"] * FIRST_PURCHASE_BONUS)
            text += f" + {money(bonus)} $ бонус за первую покупку"
        lines.append(text)
    if product["vip_days"]:
        lines.append(f"👑 VIP на {product['vip_days']} дн.: {vip_perks()}")
    for cid in product["cosmetics"]:
        item = COSMETICS[cid]
        emoji, _, _, single = COSMETIC_CATEGORIES[item["cat"]]
        lines.append(f"{emoji} {single}: {item['name']}")
    return lines


def product_description(product: dict) -> str:
    """Описание в счёте (у Telegram лимит 255 символов)."""
    return "; ".join(product_lines(product))[:250]


router = Router()

# Кэш file_id: после первой отправки картинка хранится на серверах телеграма,
# и дальше бот отправляет её мгновенно. Заменил картинку - перезапусти бота.
FILE_IDS: dict[str, str] = {}


# ===========================================================================
# СОСТОЯНИЯ
# ===========================================================================

class Registration(StatesGroup):
    nickname = State()


# ===========================================================================
# ФОРМАТИРОВАНИЕ
# ===========================================================================

def money(value: int) -> str:
    """1234567 -> '1 234 567'"""
    return f"{value:,}".replace(",", " ")


def fmt_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, sec = divmod(rest, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {sec} сек"
    return f"{sec} сек"


# ===========================================================================
# БАЗА ДАННЫХ
# ===========================================================================

async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                tg_id      INTEGER PRIMARY KEY,
                nickname   TEXT UNIQUE NOT NULL,
                balance    INTEGER NOT NULL DEFAULT 0,
                avatar     TEXT,
                work_until INTEGER NOT NULL DEFAULT 0,
                streak     INTEGER NOT NULL DEFAULT 0,
                bonus_ts   INTEGER NOT NULL DEFAULT 0,
                bank       REAL NOT NULL DEFAULT 0,
                bank_ts    INTEGER NOT NULL DEFAULT 0,
                bank_earned REAL NOT NULL DEFAULT 0,
                location   TEXT NOT NULL DEFAULT 'downtown',
                travel_to  TEXT,
                travel_until INTEGER NOT NULL DEFAULT 0,
                car_id     INTEGER NOT NULL DEFAULT 0,
                satiety    REAL NOT NULL DEFAULT 100,
                hydration  REAL NOT NULL DEFAULT 100,
                needs_ts   INTEGER NOT NULL DEFAULT 0,
                vip_until  INTEGER NOT NULL DEFAULT 0,
                eq_badge   TEXT NOT NULL DEFAULT '',
                eq_title   TEXT NOT NULL DEFAULT '',
                eq_theme   TEXT NOT NULL DEFAULT '',
                eq_effect  TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # купленные дома: один игрок не может купить один дом дважды
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS properties (
                tg_id    INTEGER NOT NULL,
                house_id INTEGER NOT NULL,
                PRIMARY KEY (tg_id, house_id)
            )
            """
        )
        # платежи (номер платежа уникален, чтобы не выдать товар дважды)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                charge_id TEXT PRIMARY KEY,
                tg_id     INTEGER NOT NULL,
                product   TEXT NOT NULL,
                stars     INTEGER NOT NULL,
                money     INTEGER NOT NULL DEFAULT 0,
                vip_days  INTEGER NOT NULL DEFAULT 0,
                cosmetics TEXT NOT NULL DEFAULT '',
                method    TEXT NOT NULL DEFAULT 'stars',
                ts        INTEGER NOT NULL,
                refunded  INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # купленная косметика
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS cosmetics (
                tg_id   INTEGER NOT NULL,
                item_id TEXT NOT NULL,
                PRIMARY KEY (tg_id, item_id)
            )
            """
        )
        # купленные машины
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS cars (
                tg_id  INTEGER NOT NULL,
                car_id INTEGER NOT NULL,
                PRIMARY KEY (tg_id, car_id)
            )
            """
        )
        # сумка: вещи из магазинов и их количество
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                tg_id    INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                qty      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (tg_id, item_key)
            )
            """
        )
        # миграция: если база создана старой версией бота, добавляем колонки
        new_columns = {
            "work_until": "INTEGER NOT NULL DEFAULT 0",
            "streak": "INTEGER NOT NULL DEFAULT 0",
            "bonus_ts": "INTEGER NOT NULL DEFAULT 0",
            "bank": "REAL NOT NULL DEFAULT 0",
            "bank_ts": "INTEGER NOT NULL DEFAULT 0",
            "bank_earned": "REAL NOT NULL DEFAULT 0",
            "location": "TEXT NOT NULL DEFAULT 'downtown'",
            "travel_to": "TEXT",
            "travel_until": "INTEGER NOT NULL DEFAULT 0",
            "car_id": "INTEGER NOT NULL DEFAULT 0",
            "satiety": "REAL NOT NULL DEFAULT 100",
            "hydration": "REAL NOT NULL DEFAULT 100",
            "needs_ts": "INTEGER NOT NULL DEFAULT 0",
            "vip_until": "INTEGER NOT NULL DEFAULT 0",
            "eq_badge": "TEXT NOT NULL DEFAULT ''",
            "eq_title": "TEXT NOT NULL DEFAULT ''",
            "eq_theme": "TEXT NOT NULL DEFAULT ''",
            "eq_effect": "TEXT NOT NULL DEFAULT ''",
        }
        async with db.execute("PRAGMA table_info(players)") as cur:
            existing = [row[1] for row in await cur.fetchall()]
        for name, definition in new_columns.items():
            if name not in existing:
                await db.execute(f"ALTER TABLE players ADD COLUMN {name} {definition}")
        await db.commit()


async def get_player(tg_id: int) -> dict | None:
    """Игрок + список id его домов в поле houses."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None

        player = dict(row)
        async with db.execute(
            "SELECT house_id FROM properties WHERE tg_id = ?", (tg_id,)
        ) as cur:
            player["houses"] = [r["house_id"] for r in await cur.fetchall()]

        # проценты банка, накопившиеся к этому моменту
        bank, bank_ts, interest = accrue_interest(
            player["bank"], player["bank_ts"], int(time.time())
        )
        player["bank"] = bank
        player["bank_ts"] = bank_ts
        player["bank_earned"] += interest

        async with db.execute(
            "SELECT car_id FROM cars WHERE tg_id = ?", (tg_id,)
        ) as cur:
            player["cars"] = [r["car_id"] for r in await cur.fetchall()]

        async with db.execute(
            "SELECT item_id FROM cosmetics WHERE tg_id = ?", (tg_id,)
        ) as cur:
            player["owned"] = {r["item_id"] for r in await cur.fetchall()}
        async with db.execute(
            "SELECT COUNT(*) AS n FROM payments WHERE tg_id = ? AND money > 0 AND refunded = 0",
            (tg_id,),
        ) as cur:
            player["paid_count"] = (await cur.fetchone())["n"]

        # если время в пути вышло, персонаж приезжает на место
        if player["travel_to"] and int(time.time()) >= player["travel_until"]:
            await db.execute(
                """
                UPDATE players
                SET location = travel_to, travel_to = NULL, travel_until = 0
                WHERE tg_id = ? AND travel_to = ?
                """,
                (tg_id, player["travel_to"]),
            )
            await db.commit()
            player["location"] = player["travel_to"]
            player["travel_to"] = None
            player["travel_until"] = 0

        # сытость и вода падают со временем (в базу пишем только при еде)
        now = int(time.time())
        if player["needs_ts"] <= 0:
            await db.execute(
                "UPDATE players SET needs_ts = ? WHERE tg_id = ? AND needs_ts <= 0",
                (now, tg_id),
            )
            await db.commit()
        player["satiety"], player["hydration"], _ = project_needs(
            player["satiety"], player["hydration"], player["needs_ts"], now
        )
        return player


async def create_player(tg_id: int, nickname: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO players (tg_id, nickname, balance) VALUES (?, ?, ?)",
            (tg_id, nickname, START_BALANCE),
        )
        await db.commit()


NEEDS_LOCK = asyncio.Lock()


async def use_item(tg_id: int, key: str) -> tuple[str, float, float, int]:
    """
    Съесть или выпить товар из сумки.
    Вернёт (статус, +сытость, +вода, на сколько секунд сокращён отдых).
    Статус: 'ok', 'none' (нет в сумке) или 'useless' (сейчас это ничего не даст).
    """
    sat_gain, hyd_gain, rest = CONSUMABLES[key]
    now = int(time.time())

    async with NEEDS_LOCK, aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT satiety, hydration, needs_ts, work_until FROM players WHERE tg_id = ?",
            (tg_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return "none", 0.0, 0.0, 0

        async with db.execute(
            "SELECT item_key, qty FROM inventory WHERE tg_id = ? AND qty > 0", (tg_id,)
        ) as cur:
            stock = [(k, q) for k, q in await cur.fetchall() if k.split(":", 1)[1] == key]
        if not stock:
            return "none", 0.0, 0.0, 0

        sat, hyd, _ = project_needs(row[0], row[1], row[2], now)
        new_sat = min(100.0, sat + sat_gain)
        new_hyd = min(100.0, hyd + hyd_gain)
        rest_cut = min(rest, max(0, row[3] - now))
        if new_sat - sat < 1 and new_hyd - hyd < 1 and rest_cut == 0:
            return "useless", 0.0, 0.0, 0

        item_key = max(stock, key=lambda s: s[1])[0]
        cur = await db.execute(
            "UPDATE inventory SET qty = qty - 1 WHERE tg_id = ? AND item_key = ? AND qty > 0",
            (tg_id, item_key),
        )
        if cur.rowcount == 0:
            await db.rollback()
            return "none", 0.0, 0.0, 0

        await db.execute(
            """
            UPDATE players
            SET satiety = ?, hydration = ?, needs_ts = ?, work_until = work_until - ?
            WHERE tg_id = ?
            """,
            (new_sat, new_hyd, now, rest_cut, tg_id),
        )
        await db.commit()
        return "ok", new_sat - sat, new_hyd - hyd, rest_cut


async def try_work(tg_id: int, job_key: str) -> tuple[int | None, int, int, float, bool]:
    """
    Отправляет игрока на смену.
    Успех: (заработок, штраф_от_копов, 0, множитель_от_сытости, есть_ли_vip)
    Ещё отдыхает: (None, 0, сколько_секунд_осталось, 1.0, False)
    Голод и жажда режут оплату, сытость и вода дают бонус, VIP добавляет ещё.
    """
    job = JOBS[job_key]
    now = int(time.time())

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT satiety, hydration, needs_ts, vip_until FROM players WHERE tg_id = ?",
            (tg_id,),
        ) as cur:
            row = await cur.fetchone()
        mult, vip = 1.0, False
        if row:
            sat, hyd, _ = project_needs(row[0], row[1], row[2], now)
            mult = needs_multiplier(sat, hyd)
            vip = row[3] > now

        total = mult * (VIP_PAY_MULT if vip else 1.0)
        pay = max(1, int(random.randint(*job["pay"]) * total))
        fine = int(pay * WANTED_FINE_SHARE) if random.random() < WANTED_CHANCE else 0
        cooldown = int(job["cooldown"] * (VIP_COOLDOWN_MULT if vip else 1.0))

        # один атомарный запрос: обновится только если отдых закончился
        cur = await db.execute(
            """
            UPDATE players
            SET balance = balance + ?, work_until = ?
            WHERE tg_id = ? AND work_until <= ?
            """,
            (pay - fine, now + cooldown, tg_id, now),
        )
        await db.commit()

        if cur.rowcount == 1:
            return pay, fine, 0, mult, vip

        async with db.execute(
            "SELECT work_until FROM players WHERE tg_id = ?", (tg_id,)
        ) as c:
            left_row = await c.fetchone()
        left = max(0, (left_row[0] if left_row else now) - now)
        return None, 0, left, 1.0, False


async def claim_bonus(tg_id: int) -> tuple[int | None, int, int, bool]:
    """
    Забирает ежедневный бонус (раз в 24 часа после прошлого).
    Успех: (сумма, серия_дней, 0, есть_ли_vip)
    Ещё рано: (None, 0, сколько_секунд_ждать, False)
    """
    now = int(time.time())

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT bonus_ts, streak, vip_until FROM players WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None, 0, 0, False

        bonus_ts, streak, vip_until = row
        passed = now - bonus_ts

        if bonus_ts and passed < BONUS_COOLDOWN:
            return None, 0, BONUS_COOLDOWN - passed, False

        # серия продолжается, если с прошлого бонуса прошло меньше 48 часов
        streak = streak + 1 if bonus_ts and passed < BONUS_STREAK_WINDOW else 1
        bonus = min(BONUS_BASE + (streak - 1) * BONUS_STEP, BONUS_MAX)
        vip = vip_until > now
        if vip:
            bonus *= VIP_BONUS_MULT

        cur = await db.execute(
            """
            UPDATE players
            SET balance = balance + ?, bonus_ts = ?, streak = ?
            WHERE tg_id = ? AND bonus_ts = ?
            """,
            (bonus, now, streak, tg_id, bonus_ts),
        )
        await db.commit()

        if cur.rowcount != 1:
            return None, 0, BONUS_COOLDOWN, False
        return bonus, streak, 0, vip


COSMETIC_COLUMNS = {cat: info[2] for cat, info in COSMETIC_CATEGORIES.items()}


async def record_payment(
    tg_id: int, charge_id: str, product_id: str, product: dict, method: str = "stars"
) -> tuple[str, int]:
    """
    Записывает платёж и выдаёт товар одной операцией.
    Вернёт (статус, выдано_денег). Статус: 'ok', 'duplicate' (этот платёж уже
    обработан) или 'no_player'. За первую покупку денег добавляется бонус.
    """
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        granted = product["money"]
        if granted:
            async with db.execute(
                "SELECT COUNT(*) FROM payments WHERE tg_id = ? AND money > 0 AND refunded = 0",
                (tg_id,),
            ) as c:
                (paid,) = await c.fetchone()
            if paid == 0:
                granted += int(granted * FIRST_PURCHASE_BONUS)

        cur = await db.execute(
            """
            INSERT OR IGNORE INTO payments
                (charge_id, tg_id, product, stars, money, vip_days, cosmetics, method, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (charge_id, tg_id, product_id, product["stars"], granted, product["vip_days"],
             ",".join(product["cosmetics"]), method, now),
        )
        if cur.rowcount == 0:
            await db.rollback()
            return "duplicate", 0

        cur = await db.execute(
            """
            UPDATE players
            SET balance = balance + ?,
                vip_until = CASE WHEN ? > 0 THEN MAX(vip_until, ?) + ? ELSE vip_until END
            WHERE tg_id = ?
            """,
            (granted, product["vip_days"], now, product["vip_days"] * 86400, tg_id),
        )
        if cur.rowcount == 0:
            await db.rollback()
            return "no_player", 0

        for cid in product["cosmetics"]:
            await db.execute(
                "INSERT OR IGNORE INTO cosmetics (tg_id, item_id) VALUES (?, ?)", (tg_id, cid)
            )
            # новая вещь сразу надевается, если слот этой категории свободен
            col = COSMETIC_COLUMNS[COSMETICS[cid]["cat"]]
            await db.execute(
                f"UPDATE players SET {col} = ? WHERE tg_id = ? AND {col} = ''", (cid, tg_id)
            )

        await db.commit()
        return "ok", granted


async def get_payment(charge_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM payments WHERE charge_id = ?", (charge_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def mark_refunded(charge_id: str) -> bool:
    """После возврата списывает выданные деньги, дни VIP и косметику."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE payments SET refunded = 1 WHERE charge_id = ? AND refunded = 0",
            (charge_id,),
        )
        if cur.rowcount == 0:
            await db.rollback()
            return False

        async with db.execute(
            "SELECT tg_id, money, vip_days, cosmetics FROM payments WHERE charge_id = ?",
            (charge_id,),
        ) as c:
            tg_id, amount, days, cosmetics = await c.fetchone()

        await db.execute(
            """
            UPDATE players
            SET balance = MAX(0, balance - ?), vip_until = MAX(0, vip_until - ?)
            WHERE tg_id = ?
            """,
            (amount, days * 86400, tg_id),
        )
        for cid in filter(None, cosmetics.split(",")):
            item = COSMETICS.get(cid)
            if not item:
                continue
            col = COSMETIC_COLUMNS[item["cat"]]
            await db.execute(
                "DELETE FROM cosmetics WHERE tg_id = ? AND item_id = ?", (tg_id, cid)
            )
            await db.execute(
                f"UPDATE players SET {col} = '' WHERE tg_id = ? AND {col} = ?", (tg_id, cid)
            )
        await db.commit()
        return True


async def set_equipped(tg_id: int, cat: str, item_id: str) -> bool:
    """Надевает вещь (item_id) или снимает категорию (пустая строка). Только свои вещи."""
    col = COSMETIC_COLUMNS[cat]
    async with aiosqlite.connect(DB_PATH) as db:
        if item_id:
            async with db.execute(
                "SELECT 1 FROM cosmetics WHERE tg_id = ? AND item_id = ?", (tg_id, item_id)
            ) as cur:
                if not await cur.fetchone():
                    return False
        await db.execute(f"UPDATE players SET {col} = ? WHERE tg_id = ?", (item_id, tg_id))
        await db.commit()
        return True


async def apply_bet(tg_id: int, bet: int, payout: int) -> bool:
    """
    Проводит ставку одним запросом: списывает ставку и начисляет выплату.
    Вернёт False, если у игрока не хватило денег на ставку.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE players
            SET balance = balance + ?
            WHERE tg_id = ? AND balance >= ?
            """,
            (payout - bet, tg_id, bet),
        )
        await db.commit()
        return cur.rowcount == 1


async def buy_house(tg_id: int, house: dict) -> str:
    """Покупка дома. Вернёт 'ok', 'owned' (уже куплен) или 'poor' (не хватает денег)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO properties (tg_id, house_id) VALUES (?, ?)",
            (tg_id, house["id"]),
        )
        if cur.rowcount == 0:
            await db.rollback()
            return "owned"

        cur = await db.execute(
            """
            UPDATE players
            SET balance = balance - ?
            WHERE tg_id = ? AND balance >= ?
            """,
            (house["price"], tg_id, house["price"]),
        )
        if cur.rowcount == 0:
            await db.rollback()
            return "poor"

        await db.commit()
        return "ok"


# Один замок на все операции со вкладом, чтобы быстрые нажатия не ломали сумму
BANK_LOCK = asyncio.Lock()


def accrue_interest(bank: float, bank_ts: int, now: int) -> tuple[float, int, float]:
    """
    Начисляет проценты за полные прошедшие часы (сложный процент).
    Проценты считаются максимум с BANK_MAX_DEPOSIT.
    Вернёт (вклад, время_последнего_начисления, сколько_начислено).
    """
    if bank <= 0:
        return 0.0, now, 0.0
    hours = (now - bank_ts) // 3600
    if hours <= 0:
        return bank, bank_ts, 0.0
    base = min(bank, BANK_MAX_DEPOSIT)
    interest = base * ((1 + BANK_RATE_PER_HOUR) ** hours - 1)
    return bank + interest, bank_ts + hours * 3600, interest


async def bank_deposit(tg_id: int, amount: int) -> str:
    """Кладёт деньги на вклад. Вернёт 'ok', 'poor' (нет денег) или 'limit' (лимит вклада)."""
    now = int(time.time())
    async with BANK_LOCK, aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT balance, bank, bank_ts FROM players WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return "poor"

        balance, bank, bank_ts = row
        if amount > balance:
            return "poor"

        bank, bank_ts, interest = accrue_interest(bank, bank_ts, now)
        if bank + amount > BANK_MAX_DEPOSIT:
            return "limit"

        cur = await db.execute(
            """
            UPDATE players
            SET balance = balance - ?, bank = ?, bank_ts = ?, bank_earned = bank_earned + ?
            WHERE tg_id = ? AND balance >= ?
            """,
            (amount, bank + amount, bank_ts, interest, tg_id, amount),
        )
        await db.commit()
        return "ok" if cur.rowcount == 1 else "poor"


async def bank_withdraw(tg_id: int, amount: int) -> str:
    """Снимает деньги со вклада. Вернёт 'ok' или 'poor' (на вкладе меньше)."""
    now = int(time.time())
    async with BANK_LOCK, aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT bank, bank_ts FROM players WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return "poor"

        bank, bank_ts, interest = accrue_interest(row[0], row[1], now)
        if amount > int(bank):
            return "poor"

        new_bank = bank - amount
        if new_bank < 1:  # копейки меньше доллара сгорают
            new_bank = 0.0

        await db.execute(
            """
            UPDATE players
            SET balance = balance + ?, bank = ?, bank_ts = ?, bank_earned = bank_earned + ?
            WHERE tg_id = ?
            """,
            (amount, new_bank, bank_ts, interest, tg_id),
        )
        await db.commit()
        return "ok"


async def buy_item(tg_id: int, item_key: str, price: int) -> bool:
    """Покупка товара в магазине: списывает деньги и кладёт вещь в сумку."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE players SET balance = balance - ? WHERE tg_id = ? AND balance >= ?",
            (price, tg_id, price),
        )
        if cur.rowcount == 0:
            await db.rollback()
            return False

        await db.execute(
            """
            INSERT INTO inventory (tg_id, item_key, qty) VALUES (?, ?, 1)
            ON CONFLICT(tg_id, item_key) DO UPDATE SET qty = qty + 1
            """,
            (tg_id, item_key),
        )
        await db.commit()
        return True


async def get_inventory(tg_id: int) -> dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT item_key, qty FROM inventory WHERE tg_id = ? AND qty > 0", (tg_id,)
        ) as cur:
            return {row[0]: row[1] for row in await cur.fetchall()}


async def buy_car(tg_id: int, car: dict) -> str:
    """Покупка машины. Вернёт 'ok', 'owned' (уже есть) или 'poor' (не хватает денег)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO cars (tg_id, car_id) VALUES (?, ?)",
            (tg_id, car["id"]),
        )
        if cur.rowcount == 0:
            await db.rollback()
            return "owned"

        cur = await db.execute(
            "UPDATE players SET balance = balance - ? WHERE tg_id = ? AND balance >= ?",
            (car["price"], tg_id, car["price"]),
        )
        if cur.rowcount == 0:
            await db.rollback()
            return "poor"

        # первая купленная машина сразу становится основной
        await db.execute(
            "UPDATE players SET car_id = ? WHERE tg_id = ? AND car_id = 0",
            (car["id"], tg_id),
        )
        await db.commit()
        return "ok"


async def set_active_car(tg_id: int, car_id: int) -> None:
    """Выбирает основную машину (только из тех, что есть в гараже)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE players SET car_id = ?
            WHERE tg_id = ? AND EXISTS (
                SELECT 1 FROM cars WHERE tg_id = ? AND car_id = ?
            )
            """,
            (car_id, tg_id, tg_id, car_id),
        )
        await db.commit()


async def start_travel(tg_id: int, origin: str, dest: str, cost: int, seconds: int) -> bool:
    """Отправляет персонажа в путь и списывает стоимость поездки."""
    arrive = int(time.time()) + seconds
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE players
            SET balance = balance - ?, travel_to = ?, travel_until = ?
            WHERE tg_id = ? AND location = ? AND travel_to IS NULL AND balance >= ?
            """,
            (cost, dest, arrive, tg_id, origin, cost),
        )
        await db.commit()
        return cur.rowcount == 1


def active_streak(player: dict) -> int:
    """Серия считается живой, если с прошлого бонуса прошло меньше 48 часов."""
    bonus_ts = player["bonus_ts"]
    if bonus_ts and int(time.time()) - bonus_ts < BONUS_STREAK_WINDOW:
        return player["streak"]
    return 0


# ===========================================================================
# ИГРЫ КАЗИНО (чистая логика: возвращают выплату и текст)
# ===========================================================================

def play_slots(bet: int) -> tuple[int, str]:
    reels = random.choices(SLOT_SYMBOLS, weights=[s[1] for s in SLOT_SYMBOLS], k=3)
    symbols = [r[0] for r in reels]
    line = f"[ {' | '.join(symbols)} ]"

    if symbols[0] == symbols[1] == symbols[2]:
        mult = reels[0][2]
        head = "💥 ДЖЕКПОТ!" if mult >= 50 else "🎉 Три в ряд!"
        return bet * mult, f"{line}\n{head} x{mult}"
    if len(set(symbols)) == 2:
        return bet // 2, f"{line}\nДве одинаковые, вернулась половина ставки"
    return 0, f"{line}\nНе повезло"


def play_dice(bet: int) -> tuple[int, str]:
    you = (random.randint(1, 6), random.randint(1, 6))
    dealer = (random.randint(1, 6), random.randint(1, 6))
    you_sum, dealer_sum = sum(you), sum(dealer)

    text = (
        f"Ты: 🎲 {you[0]} + {you[1]} = {you_sum}\n"
        f"Крупье: 🎲 {dealer[0]} + {dealer[1]} = {dealer_sum}\n"
    )
    if you_sum > dealer_sum:
        return bet * 2, text + "🎉 Ты выиграл!"
    if you_sum == dealer_sum:
        return 0, text + "Ничья, при ничьей выигрывает крупье"
    return 0, text + "Крупье забрал ставку"


def play_coin(bet: int, choice: str) -> tuple[int, str]:
    win = random.random() < COIN_WIN_CHANCE
    other = "t" if choice == "h" else "h"
    result = choice if win else other

    body = f"Выпало: {COIN_CHOICES[result]}\nТвоя ставка: {COIN_CHOICES[choice]}"
    if win:
        return bet * 2, body + "\n🎉 Угадал!"
    return 0, body + "\nНе угадал"


def play_roulette(bet: int, choice: str) -> tuple[int, str]:
    # американская рулетка: 0 и 00 (в коде 37), всего 38 ячеек
    number = random.randint(0, 37)
    label = "00" if number == 37 else str(number)
    if number in (0, 37):
        color = "🟢"
    elif number in RED_NUMBERS:
        color = "🔴"
    else:
        color = "⚫"

    mult = 0
    if choice == "r" and color == "🔴":
        mult = 2
    elif choice == "b" and color == "⚫":
        mult = 2
    elif choice == "z" and number in (0, 37):
        mult = 17
    elif choice == "d1" and 1 <= number <= 12:
        mult = 3
    elif choice == "d2" and 13 <= number <= 24:
        mult = 3
    elif choice == "d3" and 25 <= number <= 36:
        mult = 3

    body = (
        f"Шарик остановился на {color} {label}\n"
        f"Твоя ставка: {ROULETTE_CHOICES[choice]}"
    )
    return bet * mult, body + ("\n🎉 Угадал!" if mult else "\nНе угадал")


def play_game(game_key: str, bet: int, choice: str | None) -> tuple[int, str]:
    if game_key == "slots":
        return play_slots(bet)
    if game_key == "dice":
        return play_dice(bet)
    if game_key == "coin":
        return play_coin(bet, choice)
    return play_roulette(bet, choice)


# ===========================================================================
# КЛАВИАТУРЫ
# ===========================================================================

BTN_PROFILE = "👤 Профиль"
BTN_WORK = "💼 Работа"
BTN_BONUS = "🎁 Бонус"
BTN_CASINO = "🎰 Казино"
BTN_PROPERTY = "🏠 Имущество"
BTN_BANK = "🏦 Банк"
BTN_MAP = "🗺 Локации"
BTN_GARAGE = "🚗 Гараж"
BTN_BAG = "🎒 Инвентарь"
BTN_DONATE = "💎 Донат"


def inline(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Короткая запись клавиатуры: список рядов из пар (текст, callback_data)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=text, url=data)
                if data.startswith("http")
                else InlineKeyboardButton(text=text, callback_data=data)
                for text, data in row
            ]
            for row in rows
        ]
    )


def main_menu() -> ReplyKeyboardMarkup:
    """Нижнее меню, которое всегда висит у пользователя."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_WORK)],
            [KeyboardButton(text=BTN_CASINO), KeyboardButton(text=BTN_PROPERTY)],
            [KeyboardButton(text=BTN_BANK), KeyboardButton(text=BTN_BONUS)],
            [KeyboardButton(text=BTN_MAP), KeyboardButton(text=BTN_GARAGE)],
            [KeyboardButton(text=BTN_BAG), KeyboardButton(text=BTN_DONATE)],
        ],
        resize_keyboard=True,
    )


def profile_keyboard() -> InlineKeyboardMarkup:
    return inline([
        [("💼 Работа", "menu:work"), ("🎁 Бонус", "menu:bonus")],
        [("🎰 Казино", "menu:casino"), ("🏠 Имущество", "menu:property")],
        [("🏦 Банк", "menu:bank"), ("🚗 Гараж", "menu:garage")],
        [("🗺 Локации", "menu:map"), ("🎒 Инвентарь", "menu:bag")],
        [("💎 Донат", "menu:donate"), ("✨ Стиль", "menu:style")],
    ])


def work_keyboard() -> InlineKeyboardMarkup:
    rows = [[(job["title"], f"work:{key}")] for key, job in JOBS.items()]
    rows.append([("⬅️ Профиль", "menu:profile")])
    return inline(rows)


def bonus_keyboard() -> InlineKeyboardMarkup:
    return inline([[("⬅️ Профиль", "menu:profile"), ("💼 Работа", "menu:work")]])


def casino_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [[(game["title"], f"casino:{key}")] for key, game in CASINO_GAMES.items()]
    rows.append([("⬅️ Профиль", "menu:profile")])
    return inline(rows)


def bets_keyboard(game_key: str) -> InlineKeyboardMarkup:
    bets = CASINO_GAMES[game_key]["bets"]
    buttons = [(f"{money(b)} $", f"casino:{game_key}:{b}") for b in bets]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([("⬅️ Казино", "menu:casino")])
    return inline(rows)


def choice_keyboard(game_key: str, bet: int) -> InlineKeyboardMarkup:
    choices = GAME_CHOICES[game_key]
    buttons = [(name, f"casino:{game_key}:{bet}:{code}") for code, name in choices.items()]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([("⬅️ Сменить ставку", f"casino:{game_key}")])
    return inline(rows)


def result_keyboard(game_key: str, bet: int, choice: str | None) -> InlineKeyboardMarkup:
    repeat = f"casino:{game_key}:{bet}" + (f":{choice}" if choice else "")
    return inline([
        [(f"🔁 Ещё раз ({money(bet)} $)", repeat)],
        [("💵 Сменить ставку", f"casino:{game_key}"), ("🎰 Казино", "menu:casino")],
    ])


def property_menu_keyboard() -> InlineKeyboardMarkup:
    return inline([
        [("🏘 Каталог домов", "house:view:1")],
        [("📋 Мои дома", "house:mine")],
        [("⬅️ Профиль", "menu:profile")],
    ])


def house_keyboard(house: dict, owned: list[int]) -> InlineKeyboardMarkup:
    total = len(HOUSES)
    prev_id = house["id"] - 1 if house["id"] > 1 else total
    next_id = house["id"] + 1 if house["id"] < total else 1

    if house["id"] in owned:
        action = ("✅ Уже твой", "house:noop")
    else:
        action = (f"💰 Купить за {money(house['price'])} $", f"house:buy:{house['id']}")

    return inline([
        [("⬅️", f"house:view:{prev_id}"), (f"{house['id']}/{total}", "house:noop"), ("➡️", f"house:view:{next_id}")],
        [action],
        [("📋 Мои дома", "house:mine"), ("🏠 Меню", "menu:property")],
    ])


def mine_keyboard() -> InlineKeyboardMarkup:
    return inline([
        [("🏘 Каталог домов", "house:view:1")],
        [("⬅️ Недвижимость", "menu:property")],
    ])


def bank_keyboard() -> InlineKeyboardMarkup:
    return inline([
        [("➕ Положить", "bank:dep"), ("➖ Снять", "bank:wd")],
        [("⬅️ Профиль", "menu:profile")],
    ])


def bank_amounts_keyboard(deposit: bool) -> InlineKeyboardMarkup:
    kind = "dep" if deposit else "wd"
    buttons = [(f"{money(a)} $", f"bank:{kind}:{a}") for a in BANK_AMOUNTS]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([("Всё", f"bank:{kind}:all")])
    rows.append([("⬅️ Банк", "menu:bank")])
    return inline(rows)


def map_keyboard(player: dict) -> InlineKeyboardMarkup:
    rows = [
        [(f"{shop['emoji']} {shop['title']}", f"shop:open:{shop['id']}")]
        for shop in shops_in(player["location"])
    ]
    rows.append([("🚗 Поехать в другую локацию", "map:go")])
    rows.append([("🎒 Инвентарь", "menu:bag"), ("⬅️ Профиль", "menu:profile")])
    return inline(rows)


def travel_keyboard() -> InlineKeyboardMarkup:
    return inline([
        [("🔄 Проверить", "menu:map")],
        [("⬅️ Профиль", "menu:profile")],
    ])


def destinations_keyboard(player: dict) -> InlineKeyboardMarkup:
    origin = current_location(player)
    others = sorted(
        (loc for loc in LOCATIONS.values() if loc["id"] != origin["id"]),
        key=lambda loc: distance_km(origin, loc),
    )
    rows = [
        [(f"{loc['title']} · {distance_km(origin, loc):.1f} км", f"map:to:{loc['id']}")]
        for loc in others
    ]
    rows.append([("⬅️ Назад", "menu:map")])
    return inline(rows)


def route_keyboard(player: dict, dest: dict) -> InlineKeyboardMarkup:
    km = distance_km(current_location(player), dest)
    fare, _ = taxi_trip(km)
    rows = [[(f"🚕 Такси за {money(fare)} $", f"map:ride:{dest['id']}:taxi")]]

    car = active_car(player)
    if car:
        fuel, _ = car_trip(km, car)
        rows.append([(f"🚗 {car['title']} ({money(fuel)} $)", f"map:ride:{dest['id']}:car")])

    rows.append([("⬅️ Другой маршрут", "map:go")])
    return inline(rows)


def shop_keyboard(shop: dict) -> InlineKeyboardMarkup:
    rows = [
        [(f"{name} · {money(price)} $", f"shop:buy:{shop['id']}:{key}")]
        for key, name, price in shop["items"]
    ]
    rows.append([("⬅️ Локация", "menu:map")])
    return inline(rows)


def car_keyboard(car: dict, owned: list[int]) -> InlineKeyboardMarkup:
    lineup = cars_of(car["dealer"])
    pos = next(i for i, c in enumerate(lineup) if c["id"] == car["id"])
    prev_car = lineup[pos - 1]
    next_car = lineup[(pos + 1) % len(lineup)]

    if car["id"] in owned:
        action = ("✅ Уже в гараже", "house:noop")
    else:
        action = (f"💰 Купить за {money(car['price'])} $", f"car:buy:{car['id']}")

    return inline([
        [
            ("⬅️", f"car:view:{prev_car['id']}"),
            (f"{pos + 1}/{len(lineup)}", "house:noop"),
            ("➡️", f"car:view:{next_car['id']}"),
        ],
        [action],
        [("🚪 Выйти из салона", "menu:map")],
    ])


def garage_keyboard(player: dict) -> InlineKeyboardMarkup:
    rows = [
        [(f"🔑 Взять: {CARS_BY_ID[cid]['title']}", f"car:use:{cid}")]
        for cid in player["cars"]
        if cid in CARS_BY_ID and cid != player["car_id"]
    ]
    rows.append([("⬅️ Профиль", "menu:profile")])
    return inline(rows)


def inventory_keyboard(inventory: dict[str, int]) -> InlineKeyboardMarkup:
    rows = [
        [(f"{i['name']} x{i['qty']} · {effect_label(i['key'])}", f"bag:use:{i['key']}")]
        for i in aggregate_inventory(inventory)
        if i["key"] in CONSUMABLES
    ]
    rows.append([("🗺 Локации", "menu:map"), ("⬅️ Профиль", "menu:profile")])
    return inline(rows)


def donate_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for pid in DONATE_MENU:
        prod = DONATE_PRODUCTS[pid]
        if pid.startswith("pack_"):
            label = f"🔥 {prod['title']} · ⭐ {prod['stars']}"
        elif prod["money"]:
            label = f"💵 {money(prod['money'])} $ · ⭐ {prod['stars']}"
        else:
            label = f"👑 VIP {prod['vip_days']} дн. · ⭐ {prod['stars']}"
        rows.append([(label, f"donate:item:{pid}")])
    rows.append([("✨ Стиль", "menu:style"), ("⬅️ Профиль", "menu:profile")])
    return inline(rows)


def product_keyboard(pid: str) -> InlineKeyboardMarkup:
    rows = [[("⭐ Оплатить звёздами", f"donate:buy:{pid}")]]
    if CARD_PAY_URL:
        rows.append([("💳 Оплатить картой", f"card:info:{pid}")])
    rows.append([("⬅️ Назад", "menu:donate")])
    return inline(rows)


def card_keyboard(pid: str) -> InlineKeyboardMarkup:
    return inline([
        [("🔗 Перейти к оплате", CARD_PAY_URL)],
        [("✅ Я оплатил", f"card:paid:{pid}")],
        [("⬅️ Назад", f"donate:item:{pid}")],
    ])


def style_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [(f"{emoji} {plural}", f"style:cat:{cat}")]
        for cat, (emoji, plural, _, _) in COSMETIC_CATEGORIES.items()
    ]
    rows.append([("💎 Донат", "menu:donate"), ("⬅️ Профиль", "menu:profile")])
    return inline(rows)


def style_cat_keyboard(player: dict, cat: str) -> InlineKeyboardMarkup:
    col = COSMETIC_CATEGORIES[cat][2]
    rows = []
    for cid, item in COSMETICS.items():
        if item["cat"] != cat:
            continue
        if player[col] == cid:
            rows.append([(f"✅ {item['name']} (снять)", f"style:off:{cat}")])
        elif cid in player["owned"]:
            rows.append([(item["name"], f"style:eq:{cid}")])
        else:
            rows.append([(f"🔒 {item['name']} · ⭐ {item['stars']}", f"donate:item:{cid}")])
    rows.append([("⬅️ Стиль", "menu:style")])
    return inline(rows)


# ===========================================================================
# КАРТИНКИ
# ===========================================================================

def _find_file(path: Path) -> Path | None:
    """
    Ищет картинку. Сначала точное имя, потом такое же имя с другим
    расширением (png, jpeg, webp) или другим регистром букв.
    """
    if not path.suffix:
        return None
    if path.exists():
        return path
    if not path.parent.exists():
        return None
    stem = path.stem.lower() + "."
    for f in path.parent.iterdir():
        if (
            f.is_file()
            and f.name.lower().startswith(stem)
            and f.suffix.lower() in IMAGE_EXTS
        ):
            return f
    return None


def pick_photo(*candidates):
    """Первая из картинок, которая реально лежит в папке media (или None)."""
    for c in candidates:
        if c and _find_file(Path(c)):
            return c
    return None


def _resolve_media(media: str | Path | None):
    """
    Превращает путь в то, что понимает телеграм:
    - уже отправленная картинка -> file_id из кэша
    - файл есть в папке -> FSInputFile
    - строка без расширения -> считаем что это file_id
    - иначе None (отправим просто текст)
    """
    if not media:
        return None
    key = str(media)
    if key in FILE_IDS:
        return FILE_IDS[key]
    path = Path(media)
    found = _find_file(path)
    if found:
        return FSInputFile(found)
    if isinstance(media, str) and not path.suffix:
        return media
    return None


def _remember(media: str | Path | None, sent) -> None:
    """Запоминает file_id отправленной картинки, чтобы не грузить её снова."""
    if not media or not isinstance(sent, Message) or not sent.photo:
        return
    if _find_file(Path(media)):
        FILE_IDS[str(media)] = sent.photo[-1].file_id


def profile_photo(player: dict):
    return player["avatar"] or pick_photo(theme_photo(player)) or MEDIA["profile"]


def job_photo(job: dict):
    return pick_photo(job["photo"], MEDIA["work"])


def house_photo(house: dict):
    return pick_photo(house["photo"], MEDIA["property"])


def casino_photo(game: dict, net: int):
    """Картинка результата: победа, проигрыш или обычная картинка игры."""
    if net > 0:
        return pick_photo(MEDIA["casino_win"], game["photo"], MEDIA["casino"])
    if net < 0:
        return pick_photo(MEDIA["casino_lose"], game["photo"], MEDIA["casino"])
    return pick_photo(game["photo"], MEDIA["casino"])


def location_photo(loc: dict):
    return pick_photo(loc["photo"], MEDIA["map"])


def shop_photo(shop: dict):
    return pick_photo(shop["photo"], LOCATIONS[shop["loc"]]["photo"], MEDIA["map"])


def car_photo(car: dict):
    dealer = SHOPS_BY_ID[car["dealer"]]
    return pick_photo(
        car["photo"], dealer["photo"], LOCATIONS[dealer["loc"]]["photo"], MEDIA["garage"]
    )


async def send_card(
    message: Message,
    text: str,
    photo: str | Path | None = None,
    reply_markup=None,
) -> None:
    """Текст + картинка. Нет файла - уйдёт просто текст."""
    photo_file = _resolve_media(photo)
    if photo_file:
        sent = await message.answer_photo(photo_file, caption=text, reply_markup=reply_markup)
        _remember(photo, sent)
    else:
        await message.answer(text, reply_markup=reply_markup)


async def edit_card(
    call: CallbackQuery,
    text: str,
    reply_markup=None,
    photo: str | Path | None = None,
) -> None:
    """
    Меняет сообщение с кнопками: текст и картинку.
    Если старое сообщение было без картинки, а новая есть - телеграм не даёт
    превратить текст в картинку, поэтому старое удаляется и шлётся новое.
    """
    msg = call.message
    new_photo = _resolve_media(photo)
    has_media = bool(msg.photo or msg.video or msg.animation)

    try:
        if new_photo and has_media:
            sent = await msg.edit_media(
                InputMediaPhoto(media=new_photo, caption=text),
                reply_markup=reply_markup,
            )
            _remember(photo, sent)
        elif new_photo:
            await msg.delete()
            sent = await msg.answer_photo(new_photo, caption=text, reply_markup=reply_markup)
            _remember(photo, sent)
        elif has_media:
            await msg.edit_caption(caption=text, reply_markup=reply_markup)
        else:
            await msg.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


def check_media() -> None:
    """Печатает в консоль, сколько картинок бот нашёл, и сохраняет список недостающих."""
    expected = (
        list(MEDIA.values())
        + [job["photo"] for job in JOBS.values()]
        + [game["photo"] for game in CASINO_GAMES.values()]
        + [house["photo"] for house in HOUSES]
        + [loc["photo"] for loc in LOCATIONS.values()]
        + [shop["photo"] for shop in SHOPS]
        + [car["photo"] for car in CARS]
        + [item["photo"] for item in COSMETICS.values() if "photo" in item]
    )
    missing = [p for p in expected if _find_file(p) is None]

    print("=" * 50)
    print(f"Бот ищет картинки в папке: {MEDIA_DIR}")
    print(f"Найдено {len(expected) - len(missing)} из {len(expected)}")
    if missing:
        shown = ", ".join(p.name for p in missing[:10])
        more = f" и ещё {len(missing) - 10}" if len(missing) > 10 else ""
        print(f"Не хватает: {shown}{more}")
        try:
            (BASE_DIR / "missing_media.txt").write_text(
                "\n".join(p.name for p in missing), encoding="utf-8"
            )
            print("Полный список лежит в файле missing_media.txt рядом с bots.py")
        except OSError:
            pass
    print("=" * 50)


# ===========================================================================
# ТЕКСТЫ
# ===========================================================================

def owned_houses(player: dict) -> list[dict]:
    houses = [HOUSES_BY_ID[i] for i in player["houses"] if i in HOUSES_BY_ID]
    return sorted(houses, key=lambda h: h["price"])


def profile_text(player: dict) -> str:
    owned = owned_houses(player)
    if owned:
        value = sum(h["price"] for h in owned)
        prop = f"{len(owned)} шт. на {money(value)} $"
    else:
        prop = "пока нет"

    return (
        f"{profile_header(player)}\n\n"
        f"Ник: {nick_label(player)}\n"
        f"{title_line(player)}"
        f"Баланс: {money(player['balance'])} $\n"
        f"В банке: {money(int(player['bank']))} $\n"
        f"🍔 Сытость: {bar(player['satiety'])} {int(player['satiety'])}%\n"
        f"💧 Вода: {bar(player['hydration'])} {int(player['hydration'])}%\n"
        f"Серия бонусов: {active_streak(player)} дн.\n"
        f"{vip_line(player)}"
        f"Локация: {location_label(player)}\n"
        f"Машина: {car_label(player)}\n"
        f"Имущество: {prop}"
    )


def work_text(player: dict) -> str:
    left = player["work_until"] - int(time.time())
    status = f"😴 Отдыхаешь ещё {fmt_time(left)}" if left > 0 else "✅ Готов к работе"

    lines = ["💼 Работа в Лос-Сантосе\n", status]
    note = needs_note(player)
    if note:
        lines.append(note)
    lines.append("")
    for job in JOBS.values():
        low, high = job["pay"]
        lines.append(f"{job['title']}: {low}-{high} $ (отдых {fmt_time(job['cooldown'])})")
    lines.append("\nВыбери работу кнопкой ниже. Но помни, копы не дремлют ⭐")
    return "\n".join(lines)


async def bonus_message(tg_id: int) -> str:
    """Пытается выдать бонус и возвращает текст-результат."""
    bonus, streak, left, vip = await claim_bonus(tg_id)

    if bonus is None:
        return (
            "🎁 Ежедневный бонус\n\n"
            "Свою долю ты уже забрал.\n"
            f"Следующий бонус через {fmt_time(left)}"
        )

    next_bonus = min(BONUS_BASE + streak * BONUS_STEP, BONUS_MAX)
    if vip:
        next_bonus *= VIP_BONUS_MULT
    return (
        "🎁 Ежедневный бонус\n\n"
        f"{random.choice(BONUS_FLAVOR)}: +{bonus} $\n"
        f"Серия: {streak} дн. подряд\n"
        + (f"👑 VIP: бонус x{VIP_BONUS_MULT}\n" if vip else "")
        + "\n"
        f"Следующий бонус через 24 ч, он будет +{next_bonus} $.\n"
        "Если не заберёшь его в течение 48 ч, серия сбросится"
    )


def casino_text(player: dict) -> str:
    lines = ["🎰 Казино «Даймонд»", "", f"Баланс: {money(player['balance'])} $", ""]
    for game in CASINO_GAMES.values():
        low, high = game["bets"][0], game["bets"][-1]
        lines.append(f"{game['title']}: ставки {money(low)}-{money(high)} $")
    lines += ["", "Выбери игру. Удача не вечна, играй с умом 🎲"]
    return "\n".join(lines)


def game_text(game: dict, player: dict) -> str:
    return (
        f"{game['title']}\n\n"
        f"Баланс: {money(player['balance'])} $\n\n"
        f"{game['rules']}\n\n"
        "Выбери ставку:"
    )


def casino_result_text(
    game: dict, bet: int, payout: int, body: str, balance: int, effect: str = ""
) -> str:
    net = payout - bet
    if net > 0:
        status = f"✅ Выигрыш: +{money(net)} $"
        if effect:
            status = f"{effect}\n{status}\n{effect}"
    elif net == 0:
        status = "➖ Ставка вернулась"
    else:
        status = f"❌ Проигрыш: -{money(-net)} $"

    return (
        f"{game['title']}\n"
        f"Ставка: {money(bet)} $\n\n"
        f"{body}\n\n"
        f"{status}\n"
        f"Баланс: {money(balance)} $"
    )


def location_text(player: dict) -> str:
    loc = current_location(player)
    lines = [f"📍 {loc['title']}", "", loc["desc"], "", "Здесь можно зайти:"]
    for shop in shops_in(loc["id"]):
        lines.append(f"{shop['emoji']} {shop['title']}")
    lines += [
        "",
        f"Баланс: {money(player['balance'])} $",
        f"Машина: {car_label(player)}",
    ]
    return "\n".join(lines)


def travel_text(player: dict) -> str:
    dest = LOCATIONS[player["travel_to"]]
    left = player["travel_until"] - int(time.time())
    return (
        "🚖 Ты в пути\n\n"
        f"Куда: {dest['title']}\n"
        f"Прибудешь через {fmt_time(left)}"
    )


def route_text(player: dict, dest: dict) -> str:
    origin = current_location(player)
    km = distance_km(origin, dest)
    fare, taxi_sec = taxi_trip(km)

    lines = [
        "🛣 Маршрут",
        "",
        f"Откуда: {origin['title']}",
        f"Куда: {dest['title']}",
        f"Расстояние: {km:.1f} км",
        "",
        f"🚕 Такси: {money(fare)} $, в пути {fmt_time(taxi_sec)}",
    ]
    car = active_car(player)
    if car:
        fuel, car_sec = car_trip(km, car)
        lines.append(f"🚗 {car['title']}: бензин {money(fuel)} $, в пути {fmt_time(car_sec)}")
    else:
        lines.append("🚗 Своей машины пока нет, её можно купить в автосалоне")
    lines += ["", f"Баланс: {money(player['balance'])} $"]
    return "\n".join(lines)


def shop_text(shop: dict, player: dict, note: str | None = None) -> str:
    lines = [f"{shop['emoji']} {shop['title']}", ""]
    if note:
        lines += [note, ""]
    lines += [shop["desc"], ""]
    for _, name, price in shop["items"]:
        lines.append(f"{name}: {money(price)} $")
    lines += ["", f"Баланс: {money(player['balance'])} $"]
    return "\n".join(lines)


def car_text(car: dict, player: dict, bought_now: bool = False) -> str:
    if bought_now:
        status = "🎉 Поздравляем с покупкой!"
    elif car["id"] in player["cars"]:
        status = "✅ Уже в гараже"
    else:
        status = "В продаже"

    return (
        f"🚘 {car['title']}\n\n"
        f"{car['body']}\n"
        f"Макс. скорость: {car['speed']} км/ч\n"
        f"Цена: {money(car['price'])} $\n"
        f"{car['desc']}\n\n"
        f"{status}\n"
        f"Баланс: {money(player['balance'])} $"
    )


def garage_text(player: dict, note: str | None = None) -> str:
    lines = ["🚗 Гараж", ""]
    if note:
        lines += [note, ""]

    owned = [CARS_BY_ID[c] for c in player["cars"] if c in CARS_BY_ID]
    if not owned:
        lines.append("Пока пусто. Автосалоны:")
        for shop in SHOPS:
            if shop.get("dealer"):
                lines.append(f"{shop['emoji']} {shop['title']} ({LOCATIONS[shop['loc']]['title']})")
        lines += ["", "Съезди в салон и выбери себе машину"]
    else:
        for car in sorted(owned, key=lambda c: c["price"]):
            mark = "✅" if car["id"] == player["car_id"] else "▫️"
            lines.append(f"{mark} {car['title']} ({car['speed']} км/ч)")
        lines += ["", "✅ - машина, на которой ты ездишь"]
    return "\n".join(lines)


def inventory_text(player: dict, inventory: dict[str, int], note: str | None = None) -> str:
    lines = ["🎒 Инвентарь", ""]
    if note:
        lines += [note, ""]

    sat, hyd = player["satiety"], player["hydration"]
    lines += [
        f"🍔 Сытость: {bar(sat)} {int(sat)}%",
        f"💧 Вода: {bar(hyd)} {int(hyd)}%",
    ]
    status = needs_note(player)
    if status:
        lines.append(status)
    lines.append("")

    items = aggregate_inventory(inventory)
    if not items:
        lines.append("Пока пусто. Загляни в магазины (🗺 Локации)")
        return "\n".join(lines)

    food = [i for i in items if i["key"] in CONSUMABLES]
    other = [i for i in items if i["key"] not in CONSUMABLES]
    if food:
        lines.append("Еда и напитки (нажми, чтобы съесть или выпить):")
        lines += [f"{i['name']} x{i['qty']}  {effect_label(i['key'])}" for i in food]
    if other:
        if food:
            lines.append("")
        lines.append("Вещи:")
        lines += [f"{i['name']} x{i['qty']}" for i in other]
    lines += ["", f"Общая стоимость: {money(sum(i['value'] for i in items))} $"]
    return "\n".join(lines)


def donate_text(player: dict) -> str:
    lines = [
        "💎 Донат",
        "",
        "Поддержи игру и получи бонусы. Оплата звёздами Telegram ⭐"
        + (" или картой" if CARD_PAY_URL else ""),
    ]
    if FIRST_PURCHASE_BONUS and player["paid_count"] == 0:
        lines += ["", f"🎁 Первая покупка денег: +{round(FIRST_PURCHASE_BONUS * 100)}% бонусом"]

    lines += ["", "🔥 Наборы:"]
    for pid in DONATE_MENU:
        prod = DONATE_PRODUCTS[pid]
        if pid.startswith("pack_"):
            lines.append(f"{prod['title']}: ⭐ {prod['stars']}")
    lines += ["", "💵 Игровые деньги:"]
    for pid in DONATE_MENU:
        prod = DONATE_PRODUCTS[pid]
        if prod["money"] and not pid.startswith("pack_"):
            lines.append(f"⭐ {prod['stars']} → {money(prod['money'])} $")
    lines += ["", f"👑 VIP: {vip_perks()}"]
    if is_vip(player):
        lines.append(f"Твой VIP: осталось {fmt_vip_left(player['vip_until'] - int(time.time()))}")
    lines += [
        "",
        "✨ Значки, титулы, темы профиля и эффекты выигрыша: раздел «Стиль»",
        "",
        "Донат не обязателен: всё в игре можно заработать.",
        "Проблемы с оплатой: /paysupport",
    ]
    return "\n".join(lines)


def product_text(product: dict, player: dict) -> str:
    first = bool(product["money"]) and player["paid_count"] == 0 and FIRST_PURCHASE_BONUS > 0
    lines = [f"💎 {product['title']}", ""] + product_lines(product, first)
    lines += ["", f"Цена: ⭐ {product['stars']}"]
    if CARD_PAY_URL:
        lines.append(f"Картой: {card_price(product)} {CARD_CURRENCY}")
    return "\n".join(lines)


def card_text(product: dict, player: dict) -> str:
    return (
        "💳 Оплата картой\n\n"
        f"Товар: {product['title']}\n"
        f"Сумма: {card_price(product)} {CARD_CURRENCY}\n\n"
        "1. Нажми «Перейти к оплате» и оплати картой.\n"
        f"2. В комментарии к платежу укажи свой ник: {player['nickname']}\n"
        "3. Вернись сюда и нажми «Я оплатил».\n"
        "4. Платёж проверят, и товар выдадут вручную. Это может занять некоторое время.\n\n"
        "Вопросы: /paysupport"
    )


async def success_text(tg_id: int, product: dict, granted: int, charge_id: str) -> str:
    player = await get_player(tg_id)
    lines = ["✅ Спасибо за поддержку!", ""]
    if granted:
        bonus = granted - product["money"]
        lines.append(f"Получено: +{money(granted)} $" + (f" (в том числе бонус {money(bonus)} $)" if bonus else ""))
    if product["vip_days"]:
        lines.append(
            f"👑 VIP: +{product['vip_days']} дн., осталось "
            f"{fmt_vip_left(player['vip_until'] - int(time.time()))}"
        )
    for cid in product["cosmetics"]:
        item = COSMETICS[cid]
        lines.append(f"{COSMETIC_CATEGORIES[item['cat']][0]} {item['name']}")
    if product["cosmetics"]:
        lines.append("Надеть и сменить украшения: ✨ Стиль")
    lines += [
        f"Баланс: {money(player['balance'])} $",
        "",
        f"Номер платежа: {charge_id}",
        "Сохрани его на случай вопросов",
    ]
    return "\n".join(lines)


def style_text(player: dict, note: str | None = None) -> str:
    lines = ["✨ Стиль", ""]
    if note:
        lines += [note, ""]
    lines.append("Сейчас надето:")
    for cat, (emoji, _, _, single) in COSMETIC_CATEGORIES.items():
        item = equipped(player, cat)
        lines.append(f"{emoji} {single}: {item['name'] if item else 'нет'}")
    lines += ["", "Выбери категорию. Вещи с замком 🔒 можно купить за ⭐"]
    return "\n".join(lines)


def style_cat_text(player: dict, cat: str, note: str | None = None) -> str:
    emoji, plural, col, _ = COSMETIC_CATEGORIES[cat]
    hints = {
        "badge": "Значок виден рядом с твоим ником",
        "title": "Титул пишется под ником в профиле",
        "theme": "Тема меняет картинку и рамку профиля",
        "effect": "Эффект украшает каждый твой выигрыш в казино",
    }
    lines = [f"{emoji} {plural}", "", hints[cat]]
    if note:
        lines += ["", note]
    lines.append("")
    for cid, item in COSMETICS.items():
        if item["cat"] != cat:
            continue
        if player[col] == cid:
            state = "✅ надето"
        elif cid in player["owned"]:
            state = "куплено"
        else:
            state = f"🔒 ⭐ {item['stars']}"
        lines.append(f"{item['name']}: {state}")
    return "\n".join(lines)


def bank_text(player: dict, note: str | None = None) -> str:
    bank = int(player["bank"])
    daily = ((1 + BANK_RATE_PER_HOUR) ** 24 - 1) * 100

    lines = ["🏦 Банк «Мэйз»", ""]
    if note:
        lines += [note, ""]
    lines += [
        f"На руках: {money(player['balance'])} $",
        f"На вкладе: {money(bank)} $",
        f"Проценты за всё время: +{money(int(player['bank_earned']))} $",
        "",
    ]
    if bank > 0:
        left = player["bank_ts"] + 3600 - int(time.time())
        lines.append(f"Следующее начисление через {fmt_time(left)}")
    lines += [
        f"Ставка: {BANK_RATE_PER_HOUR * 100:.1f}% в час (около {daily:.1f}% в сутки)",
        f"Максимум на вкладе: {money(BANK_MAX_DEPOSIT)} $",
    ]
    return "\n".join(lines)


def property_text(player: dict) -> str:
    low = min(h["price"] for h in HOUSES)
    high = max(h["price"] for h in HOUSES)
    return (
        "🏠 Недвижимость\n\n"
        f"В каталоге {len(HOUSES)} домов: от {money(low)} до {money(high)} $.\n"
        f"У тебя: {len(player['houses'])} из {len(HOUSES)}\n"
        f"Баланс: {money(player['balance'])} $"
    )


def house_text(house: dict, player: dict, bought_now: bool = False) -> str:
    if bought_now:
        status = "🎉 Поздравляем с покупкой!"
    elif house["id"] in player["houses"]:
        status = "✅ Уже твой"
    else:
        status = "Доступен для покупки"

    return (
        f"🏠 {house['title']}\n\n"
        f"Цена: {money(house['price'])} $\n"
        f"{house['desc']}\n\n"
        f"{status}\n"
        f"Баланс: {money(player['balance'])} $"
    )


def mine_text(player: dict) -> str:
    owned = owned_houses(player)
    if not owned:
        return "📋 Мои дома\n\nПока пусто. Загляни в каталог и выбери себе жильё"

    lines = ["📋 Мои дома", ""]
    for h in owned:
        lines.append(f"🏠 {h['title']} ({money(h['price'])} $)")
    lines += ["", f"Общая стоимость: {money(sum(h['price'] for h in owned))} $"]
    return "\n".join(lines)


ASK_NICK_TEXT = (
    "Добро пожаловать в Лос-Сантос!\n"
    "Введи никнейм для игры (3-16 символов: буквы, цифры, _)."
)


# ===========================================================================
# РЕГИСТРАЦИЯ
# ===========================================================================

# Стоит первым, чтобы любой текст в момент ввода ника считался ником
@router.message(StateFilter(Registration.nickname), F.text, ~F.text.startswith("/"))
async def process_nickname(message: Message, state: FSMContext) -> None:
    nick = message.text.strip()

    if not NICK_REGEX.match(nick):
        await message.answer(
            "Некорректный ник. Нужно 3-16 символов: буквы, цифры или _. Попробуй ещё раз."
        )
        return

    try:
        await create_player(message.from_user.id, nick)
    except aiosqlite.IntegrityError:
        await message.answer("Этот ник уже занят, придумай другой.")
        return

    await state.clear()
    await send_card(
        message,
        f"Готово! Твой ник: {nick}\nПользуйся меню внизу 👇",
        photo=MEDIA["welcome"],
        reply_markup=main_menu(),
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    player = await get_player(message.from_user.id)
    if player:
        await send_card(
            message,
            f"С возвращением в Лос-Сантос, {player['nickname']}!",
            photo=MEDIA["welcome"],
            reply_markup=main_menu(),
        )
        return

    await state.set_state(Registration.nickname)
    await send_card(message, ASK_NICK_TEXT, photo=MEDIA["welcome"])


async def ask_registration(message: Message, state: FSMContext) -> None:
    await state.set_state(Registration.nickname)
    await send_card(message, ASK_NICK_TEXT, photo=MEDIA["welcome"])


# ===========================================================================
# ПРОФИЛЬ
# ===========================================================================

PROFILE_TRIGGERS = {"б", "баланс", "профиль", BTN_PROFILE.lower()}
WORK_TRIGGERS = {"работа", BTN_WORK.lower()}
BONUS_TRIGGERS = {"бонус", BTN_BONUS.lower()}
CASINO_TRIGGERS = {"казино", BTN_CASINO.lower()}
PROPERTY_TRIGGERS = {"имущество", "недвижимость", BTN_PROPERTY.lower()}
BANK_TRIGGERS = {"банк", BTN_BANK.lower()}
MAP_TRIGGERS = {"локации", "карта", "города", BTN_MAP.lower()}
GARAGE_TRIGGERS = {"гараж", BTN_GARAGE.lower()}
BAG_TRIGGERS = {"инвентарь", "сумка", "еда", BTN_BAG.lower()}
DONATE_TRIGGERS = {"донат", BTN_DONATE.lower()}
STYLE_TRIGGERS = {"стиль", "гардероб"}


@router.message(F.text.func(lambda t: t.strip().lower() in PROFILE_TRIGGERS))
async def show_profile(message: Message, state: FSMContext) -> None:
    player = await get_player(message.from_user.id)
    if not player:
        await ask_registration(message, state)
        return

    await send_card(
        message,
        profile_text(player),
        photo=profile_photo(player),
        reply_markup=profile_keyboard(),
    )


@router.callback_query(F.data == "menu:profile")
async def cb_profile(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    await edit_card(
        call,
        profile_text(player),
        profile_keyboard(),
        photo=profile_photo(player),
    )
    await call.answer()


# ===========================================================================
# РАБОТА
# ===========================================================================

@router.message(F.text.func(lambda t: t.strip().lower() in WORK_TRIGGERS))
async def show_work(message: Message, state: FSMContext) -> None:
    player = await get_player(message.from_user.id)
    if not player:
        await ask_registration(message, state)
        return

    await send_card(
        message,
        work_text(player),
        photo=MEDIA["work"],
        reply_markup=work_keyboard(),
    )


@router.callback_query(F.data == "menu:work")
async def cb_work_menu(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    await edit_card(call, work_text(player), work_keyboard(), photo=MEDIA["work"])
    await call.answer()


@router.callback_query(F.data.startswith("work:"))
async def cb_do_work(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    job_key = call.data.split(":", 1)[1]
    job = JOBS.get(job_key)
    if not job:
        await call.answer("Такой работы нет", show_alert=True)
        return

    pay, fine, left, mult, vip = await try_work(call.from_user.id, job_key)

    if pay is None:
        await call.answer(f"Ты устал, отдохни ещё {fmt_time(left)}", show_alert=True)
        return

    player = await get_player(call.from_user.id)
    lines = [
        job["title"],
        "",
        random.choice(job["flavor"]),
        f"Заработано: {money(pay)} $",
    ]
    note = mult_note(mult)
    if note:
        lines.append(note)
    if vip:
        lines.append(f"👑 VIP: +{round((VIP_PAY_MULT - 1) * 100)}% к оплате")
    if fine:
        lines.append(f"⭐ Тебя заметили копы, пришлось откупиться: -{money(fine)} $")
    lines += [
        f"Баланс: {money(player['balance'])} $",
        "",
        f"Следующая смена через "
        f"{fmt_time(int(job['cooldown'] * (VIP_COOLDOWN_MULT if vip else 1.0)))}",
    ]

    await edit_card(call, "\n".join(lines), work_keyboard(), photo=job_photo(job))
    await call.answer(f"+{pay - fine} $")


# ===========================================================================
# ЕЖЕДНЕВНЫЙ БОНУС
# ===========================================================================

@router.message(F.text.func(lambda t: t.strip().lower() in BONUS_TRIGGERS))
async def show_bonus(message: Message, state: FSMContext) -> None:
    player = await get_player(message.from_user.id)
    if not player:
        await ask_registration(message, state)
        return

    text = await bonus_message(message.from_user.id)
    await send_card(message, text, photo=MEDIA["bonus"], reply_markup=bonus_keyboard())


@router.callback_query(F.data == "menu:bonus")
async def cb_bonus(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    text = await bonus_message(call.from_user.id)
    await edit_card(call, text, bonus_keyboard(), photo=MEDIA["bonus"])
    await call.answer()


# ===========================================================================
# КАЗИНО
# ===========================================================================

@router.message(F.text.func(lambda t: t.strip().lower() in CASINO_TRIGGERS))
async def show_casino(message: Message, state: FSMContext) -> None:
    if not CASINO_ENABLED:
        await message.answer("Казино сейчас закрыто")
        return
    player = await get_player(message.from_user.id)
    if not player:
        await ask_registration(message, state)
        return

    await send_card(
        message,
        casino_text(player),
        photo=MEDIA["casino"],
        reply_markup=casino_menu_keyboard(),
    )


@router.callback_query(F.data == "menu:casino")
async def cb_casino_menu(call: CallbackQuery) -> None:
    if not CASINO_ENABLED:
        await call.answer("Казино сейчас закрыто", show_alert=True)
        return
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    await edit_card(call, casino_text(player), casino_menu_keyboard(), photo=MEDIA["casino"])
    await call.answer()


@router.callback_query(F.data.startswith("casino:"))
async def cb_casino(call: CallbackQuery) -> None:
    """
    Формат callback_data:
      casino:игра              - выбор ставки
      casino:игра:ставка       - для монетки и рулетки выбор варианта, для остальных сразу игра
      casino:игра:ставка:выбор - игра
    """
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    if not CASINO_ENABLED:
        await call.answer("Казино сейчас закрыто", show_alert=True)
        return

    parts = call.data.split(":")
    game_key = parts[1]
    game = CASINO_GAMES.get(game_key)
    if not game:
        await call.answer("Такой игры нет", show_alert=True)
        return
    choices = GAME_CHOICES.get(game_key)

    # экран выбора ставки
    if len(parts) == 2:
        await edit_card(
            call,
            game_text(game, player),
            bets_keyboard(game_key),
            photo=pick_photo(game["photo"], MEDIA["casino"]),
        )
        await call.answer()
        return

    try:
        bet = int(parts[2])
    except ValueError:
        await call.answer("Некорректная ставка", show_alert=True)
        return
    if bet not in game["bets"]:
        await call.answer("Такой ставки нет", show_alert=True)
        return

    if player["balance"] < bet:
        await call.answer(
            f"Не хватает денег на ставку {money(bet)} $. Баланс: {money(player['balance'])} $",
            show_alert=True,
        )
        return

    choice = parts[3] if len(parts) > 3 else None

    # у монетки и рулетки нужен ещё выбор варианта
    if choices:
        if choice is None:
            await edit_card(
                call,
                f"{game['title']}\nСтавка: {money(bet)} $\n\nНа что ставишь?",
                choice_keyboard(game_key, bet),
                photo=pick_photo(game["photo"], MEDIA["casino"]),
            )
            await call.answer()
            return
        if choice not in choices:
            await call.answer("Такого варианта нет", show_alert=True)
            return
    else:
        choice = None

    payout, body = play_game(game_key, bet, choice)
    if not await apply_bet(call.from_user.id, bet, payout):
        await call.answer("Не хватает денег на ставку", show_alert=True)
        return

    player = await get_player(call.from_user.id)
    text = casino_result_text(
        game, bet, payout, body, player["balance"], effect_line(player)
    )
    await edit_card(
        call,
        text,
        result_keyboard(game_key, bet, choice),
        photo=casino_photo(game, payout - bet),
    )
    await call.answer()


# ===========================================================================
# НЕДВИЖИМОСТЬ
# ===========================================================================

@router.message(F.text.func(lambda t: t.strip().lower() in PROPERTY_TRIGGERS))
async def show_property(message: Message, state: FSMContext) -> None:
    player = await get_player(message.from_user.id)
    if not player:
        await ask_registration(message, state)
        return

    await send_card(
        message,
        property_text(player),
        photo=MEDIA["property"],
        reply_markup=property_menu_keyboard(),
    )


@router.callback_query(F.data == "menu:property")
async def cb_property_menu(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    await edit_card(
        call, property_text(player), property_menu_keyboard(), photo=MEDIA["property"]
    )
    await call.answer()


@router.callback_query(F.data == "house:noop")
async def cb_house_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data == "house:mine")
async def cb_house_mine(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    await edit_card(call, mine_text(player), mine_keyboard(), photo=MEDIA["property"])
    await call.answer()


@router.callback_query(F.data.startswith("house:view:"))
async def cb_house_view(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    try:
        house = HOUSES_BY_ID.get(int(call.data.split(":")[2]))
    except ValueError:
        house = None
    if not house:
        await call.answer("Такого дома нет", show_alert=True)
        return

    await edit_card(
        call,
        house_text(house, player),
        house_keyboard(house, player["houses"]),
        photo=house_photo(house),
    )
    await call.answer()


@router.callback_query(F.data.startswith("house:buy:"))
async def cb_house_buy(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    try:
        house = HOUSES_BY_ID.get(int(call.data.split(":")[2]))
    except ValueError:
        house = None
    if not house:
        await call.answer("Такого дома нет", show_alert=True)
        return

    result = await buy_house(call.from_user.id, house)

    if result == "owned":
        await call.answer("Этот дом уже твой", show_alert=True)
        return
    if result == "poor":
        lack = max(0, house["price"] - player["balance"])
        await call.answer(f"Не хватает {money(lack)} $", show_alert=True)
        return

    player = await get_player(call.from_user.id)
    await edit_card(
        call,
        house_text(house, player, bought_now=True),
        house_keyboard(house, player["houses"]),
        photo=house_photo(house),
    )
    await call.answer("Дом куплен!")


# ===========================================================================
# БАНК
# ===========================================================================

@router.message(F.text.func(lambda t: t.strip().lower() in BANK_TRIGGERS))
async def show_bank(message: Message, state: FSMContext) -> None:
    player = await get_player(message.from_user.id)
    if not player:
        await ask_registration(message, state)
        return

    await send_card(
        message,
        bank_text(player),
        photo=MEDIA["bank"],
        reply_markup=bank_keyboard(),
    )


@router.callback_query(F.data == "menu:bank")
async def cb_bank_menu(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    await edit_card(call, bank_text(player), bank_keyboard(), photo=MEDIA["bank"])
    await call.answer()


@router.callback_query(F.data.in_({"bank:dep", "bank:wd"}))
async def cb_bank_choose(call: CallbackQuery) -> None:
    """Экран выбора суммы: положить или снять."""
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    deposit = call.data == "bank:dep"
    note = "Сколько положить на вклад?" if deposit else "Сколько снять со вклада?"
    await edit_card(
        call,
        bank_text(player, note),
        bank_amounts_keyboard(deposit),
        photo=MEDIA["bank"],
    )
    await call.answer()


@router.callback_query(F.data.regexp(r"^bank:(dep|wd):"))
async def cb_bank_action(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    _, kind, raw = call.data.split(":")
    deposit = kind == "dep"

    if raw == "all":
        if deposit:
            amount = min(player["balance"], BANK_MAX_DEPOSIT - int(player["bank"]))
        else:
            amount = int(player["bank"])
    else:
        try:
            amount = int(raw)
        except ValueError:
            await call.answer("Некорректная сумма", show_alert=True)
            return

    if amount <= 0:
        text = "Нечего класть на вклад" if deposit else "На вкладе пусто"
        await call.answer(text, show_alert=True)
        return

    if deposit:
        result = await bank_deposit(call.from_user.id, amount)
    else:
        result = await bank_withdraw(call.from_user.id, amount)

    if result == "poor":
        text = "Не хватает денег на руках" if deposit else "На вкладе нет столько"
        await call.answer(text, show_alert=True)
        return
    if result == "limit":
        await call.answer(
            f"Максимум на вкладе: {money(BANK_MAX_DEPOSIT)} $", show_alert=True
        )
        return

    player = await get_player(call.from_user.id)
    note = (
        f"✅ Положено {money(amount)} $" if deposit else f"✅ Снято {money(amount)} $"
    )
    await edit_card(
        call,
        bank_text(player, note),
        bank_amounts_keyboard(deposit),
        photo=MEDIA["bank"],
    )
    await call.answer()


# ===========================================================================
# ЛОКАЦИИ, ТАКСИ, МАГАЗИНЫ, МАШИНЫ
# ===========================================================================

TASKS: set = set()  # чтобы фоновые задачи не пропадали раньше времени


async def get_ready_player(call: CallbackQuery, loc: str | None = None) -> dict | None:
    """
    Игрок, который сейчас может действовать: зарегистрирован, не в пути
    и (если указано) находится в нужной локации. Иначе покажет всплывающее окно.
    """
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return None
    if is_traveling(player):
        left = player["travel_until"] - int(time.time())
        await call.answer(f"Ты в пути, прибудешь через {fmt_time(left)}", show_alert=True)
        return None
    if loc and player["location"] != loc:
        await call.answer(
            f"Это в другой локации: {LOCATIONS[loc]['title']}. Сначала съезди туда.",
            show_alert=True,
        )
        return None
    return player


def map_screen(player: dict):
    """Текст, клавиатура и картинка для экрана локации (или поездки)."""
    if is_traveling(player):
        dest = LOCATIONS[player["travel_to"]]
        return travel_text(player), travel_keyboard(), location_photo(dest)
    return location_text(player), map_keyboard(player), location_photo(current_location(player))


async def notify_arrival(bot: Bot, chat_id: int, tg_id: int, delay: int) -> None:
    """Через delay секунд пишет, что персонаж приехал."""
    await asyncio.sleep(delay + 1)
    player = await get_player(tg_id)  # здесь персонаж и "приезжает" в базе
    if not player or is_traveling(player):
        return

    loc = current_location(player)
    text = f"📍 Ты прибыл: {loc['title']}\n\n{loc['desc']}"
    keyboard = inline([[("🗺 Открыть локацию", "menu:map")]])
    photo = location_photo(loc)
    media = _resolve_media(photo)

    try:
        if media:
            sent = await bot.send_photo(chat_id, media, caption=text, reply_markup=keyboard)
            _remember(photo, sent)
        else:
            await bot.send_message(chat_id, text, reply_markup=keyboard)
    except Exception:
        logging.exception("Не удалось отправить сообщение о прибытии")


@router.message(F.text.func(lambda t: t.strip().lower() in MAP_TRIGGERS))
async def show_map(message: Message, state: FSMContext) -> None:
    player = await get_player(message.from_user.id)
    if not player:
        await ask_registration(message, state)
        return

    text, keyboard, photo = map_screen(player)
    await send_card(message, text, photo=photo, reply_markup=keyboard)


@router.callback_query(F.data == "menu:map")
async def cb_map(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    text, keyboard, photo = map_screen(player)
    await edit_card(call, text, keyboard, photo=photo)
    await call.answer()


@router.callback_query(F.data == "map:go")
async def cb_map_go(call: CallbackQuery) -> None:
    player = await get_ready_player(call)
    if not player:
        return

    origin = current_location(player)
    text = (
        "🚗 Куда едем?\n\n"
        f"Ты сейчас: {origin['title']}\n"
        f"Баланс: {money(player['balance'])} $\n\n"
        "Список от ближних мест к дальним"
    )
    await edit_card(call, text, destinations_keyboard(player), photo=MEDIA["map"])
    await call.answer()


@router.callback_query(F.data.startswith("map:to:"))
async def cb_map_route(call: CallbackQuery) -> None:
    player = await get_ready_player(call)
    if not player:
        return

    dest = LOCATIONS.get(call.data.split(":")[2])
    if not dest or dest["id"] == player["location"]:
        await call.answer("Ты уже здесь или такого места нет", show_alert=True)
        return

    await edit_card(
        call,
        route_text(player, dest),
        route_keyboard(player, dest),
        photo=location_photo(dest),
    )
    await call.answer()


@router.callback_query(F.data.startswith("map:ride:"))
async def cb_map_ride(call: CallbackQuery) -> None:
    player = await get_ready_player(call)
    if not player:
        return

    _, _, dest_id, mode = call.data.split(":")
    origin = current_location(player)
    dest = LOCATIONS.get(dest_id)
    if not dest or dest["id"] == origin["id"]:
        await call.answer("Ты уже здесь или такого места нет", show_alert=True)
        return

    km = distance_km(origin, dest)
    if mode == "taxi":
        cost, seconds = taxi_trip(km)
        how = "🚕 Едешь на такси"
    elif mode == "car":
        car = active_car(player)
        if not car:
            await call.answer("У тебя нет машины, купи её в автосалоне", show_alert=True)
            return
        cost, seconds = car_trip(km, car)
        how = f"🚗 Едешь на {car['title']}"
    else:
        await call.answer("Неизвестный способ", show_alert=True)
        return

    if player["balance"] < cost:
        await call.answer(
            f"Не хватает денег: нужно {money(cost)} $, у тебя {money(player['balance'])} $",
            show_alert=True,
        )
        return

    if not await start_travel(call.from_user.id, origin["id"], dest["id"], cost, seconds):
        await call.answer("Не получилось выехать, попробуй ещё раз", show_alert=True)
        return

    task = asyncio.create_task(
        notify_arrival(call.bot, call.message.chat.id, call.from_user.id, seconds)
    )
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)

    text = (
        f"{how}\n\n"
        f"Маршрут: {origin['title']} → {dest['title']}\n"
        f"Расстояние: {km:.1f} км\n"
        f"Стоимость: {money(cost)} $\n\n"
        f"Прибудешь через {fmt_time(seconds)}"
    )
    await edit_card(call, text, travel_keyboard(), photo=location_photo(dest))
    await call.answer()


# --- магазины -----------------------------------------------------------------

@router.callback_query(F.data.startswith("shop:open:"))
async def cb_shop_open(call: CallbackQuery) -> None:
    shop = SHOPS_BY_ID.get(call.data.split(":")[2])
    if not shop:
        await call.answer("Такого магазина нет", show_alert=True)
        return

    player = await get_ready_player(call, shop["loc"])
    if not player:
        return

    if shop.get("dealer"):
        car = cars_of(shop["id"])[0]
        await edit_card(
            call,
            car_text(car, player),
            car_keyboard(car, player["cars"]),
            photo=car_photo(car),
        )
    else:
        await edit_card(
            call, shop_text(shop, player), shop_keyboard(shop), photo=shop_photo(shop)
        )
    await call.answer()


@router.callback_query(F.data.startswith("shop:buy:"))
async def cb_shop_buy(call: CallbackQuery) -> None:
    _, _, shop_id, key = call.data.split(":")
    item = ITEMS_BY_KEY.get(f"{shop_id}:{key}")
    if not item:
        await call.answer("Такого товара нет", show_alert=True)
        return
    shop, name, price = item

    player = await get_ready_player(call, shop["loc"])
    if not player:
        return

    if player["balance"] < price:
        await call.answer(f"Не хватает {money(price - player['balance'])} $", show_alert=True)
        return
    if not await buy_item(call.from_user.id, f"{shop_id}:{key}", price):
        await call.answer("Не хватает денег", show_alert=True)
        return

    player = await get_player(call.from_user.id)
    await edit_card(
        call,
        shop_text(shop, player, note=f"✅ Куплено: {name}"),
        shop_keyboard(shop),
        photo=shop_photo(shop),
    )
    await call.answer(f"-{money(price)} $")


# --- автосалоны ---------------------------------------------------------------

@router.callback_query(F.data.startswith("car:view:"))
async def cb_car_view(call: CallbackQuery) -> None:
    try:
        car = CARS_BY_ID.get(int(call.data.split(":")[2]))
    except ValueError:
        car = None
    if not car:
        await call.answer("Такой машины нет", show_alert=True)
        return

    player = await get_ready_player(call, SHOPS_BY_ID[car["dealer"]]["loc"])
    if not player:
        return

    await edit_card(
        call,
        car_text(car, player),
        car_keyboard(car, player["cars"]),
        photo=car_photo(car),
    )
    await call.answer()


@router.callback_query(F.data.startswith("car:buy:"))
async def cb_car_buy(call: CallbackQuery) -> None:
    try:
        car = CARS_BY_ID.get(int(call.data.split(":")[2]))
    except ValueError:
        car = None
    if not car:
        await call.answer("Такой машины нет", show_alert=True)
        return

    player = await get_ready_player(call, SHOPS_BY_ID[car["dealer"]]["loc"])
    if not player:
        return

    result = await buy_car(call.from_user.id, car)
    if result == "owned":
        await call.answer("Эта машина уже в твоём гараже", show_alert=True)
        return
    if result == "poor":
        lack = max(0, car["price"] - player["balance"])
        await call.answer(f"Не хватает {money(lack)} $", show_alert=True)
        return

    player = await get_player(call.from_user.id)
    await edit_card(
        call,
        car_text(car, player, bought_now=True),
        car_keyboard(car, player["cars"]),
        photo=car_photo(car),
    )
    await call.answer("Машина куплена!")


# --- гараж и сумка ------------------------------------------------------------

def garage_photo(player: dict):
    car = active_car(player)
    return car_photo(car) if car else pick_photo(MEDIA["garage"])


@router.message(F.text.func(lambda t: t.strip().lower() in GARAGE_TRIGGERS))
async def show_garage(message: Message, state: FSMContext) -> None:
    player = await get_player(message.from_user.id)
    if not player:
        await ask_registration(message, state)
        return

    await send_card(
        message,
        garage_text(player),
        photo=garage_photo(player),
        reply_markup=garage_keyboard(player),
    )


@router.callback_query(F.data == "menu:garage")
async def cb_garage(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    await edit_card(
        call, garage_text(player), garage_keyboard(player), photo=garage_photo(player)
    )
    await call.answer()


@router.callback_query(F.data.startswith("car:use:"))
async def cb_car_use(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    try:
        car_id = int(call.data.split(":")[2])
    except ValueError:
        car_id = 0
    if car_id not in player["cars"] or car_id not in CARS_BY_ID:
        await call.answer("Этой машины нет в гараже", show_alert=True)
        return

    await set_active_car(call.from_user.id, car_id)
    player = await get_player(call.from_user.id)
    note = f"🔑 Теперь ты ездишь на {CARS_BY_ID[car_id]['title']}"
    await edit_card(
        call,
        garage_text(player, note),
        garage_keyboard(player),
        photo=garage_photo(player),
    )
    await call.answer()


# ===========================================================================
# ИНВЕНТАРЬ: ЕДА И ВОДА
# ===========================================================================

async def render_inventory(call: CallbackQuery, note: str | None = None) -> None:
    player = await get_player(call.from_user.id)
    inventory = await get_inventory(call.from_user.id)
    await edit_card(
        call,
        inventory_text(player, inventory, note),
        inventory_keyboard(inventory),
        photo=MEDIA["bag"],
    )


@router.message(F.text.func(lambda t: t.strip().lower() in BAG_TRIGGERS))
async def show_inventory(message: Message, state: FSMContext) -> None:
    player = await get_player(message.from_user.id)
    if not player:
        await ask_registration(message, state)
        return

    inventory = await get_inventory(message.from_user.id)
    await send_card(
        message,
        inventory_text(player, inventory),
        photo=MEDIA["bag"],
        reply_markup=inventory_keyboard(inventory),
    )


@router.callback_query(F.data.in_({"menu:bag", "map:bag"}))
async def cb_inventory(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    await render_inventory(call)
    await call.answer()


@router.callback_query(F.data.startswith("bag:use:"))
async def cb_bag_use(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    key = call.data.split(":")[2]
    if key not in CONSUMABLES:
        await call.answer("Это нельзя съесть или выпить", show_alert=True)
        return

    status, d_sat, d_hyd, d_rest = await use_item(call.from_user.id, key)

    if status == "none":
        await render_inventory(call)  # список мог устареть
        await call.answer("Этого уже нет в сумке", show_alert=True)
        return
    if status == "useless":
        await call.answer("Сейчас это ни к чему: ты не голоден и не хочешь пить", show_alert=True)
        return

    parts = []
    if d_sat >= 1:
        parts.append(f"+{int(d_sat)}🍔")
    if d_hyd >= 1:
        parts.append(f"+{int(d_hyd)}💧")
    if d_rest:
        parts.append(f"отдых короче на {d_rest} с")
    verb = "Выпил" if d_hyd >= d_sat else "Съел"
    note = f"😋 {verb}: {CONSUMABLE_NAMES[key]} ({', '.join(parts)})"

    await render_inventory(call, note)
    await call.answer()


# ===========================================================================
# ДОНАТ: ЗВЁЗДЫ TELEGRAM, ОПЛАТА КАРТОЙ, СТИЛЬ
# ===========================================================================

@router.message(F.text.func(lambda t: t.strip().lower() in DONATE_TRIGGERS))
async def show_donate(message: Message, state: FSMContext) -> None:
    player = await get_player(message.from_user.id)
    if not player:
        await ask_registration(message, state)
        return

    await send_card(
        message,
        donate_text(player),
        photo=MEDIA["donate"],
        reply_markup=donate_keyboard(),
    )


@router.message(Command("donate"))
async def cmd_donate(message: Message, state: FSMContext) -> None:
    await show_donate(message, state)


@router.callback_query(F.data == "menu:donate")
async def cb_donate_menu(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    await edit_card(call, donate_text(player), donate_keyboard(), photo=MEDIA["donate"])
    await call.answer()


@router.callback_query(F.data.startswith("donate:item:"))
async def cb_donate_item(call: CallbackQuery) -> None:
    """Карточка товара: что входит и как оплатить."""
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    pid = call.data.split(":")[2]
    product = DONATE_PRODUCTS.get(pid)
    if not product:
        await call.answer("Такого товара нет", show_alert=True)
        return

    # у косметики с темой показываем её картинку
    photo = MEDIA["donate"]
    for cid in product["cosmetics"]:
        if COSMETICS[cid].get("photo"):
            photo = pick_photo(COSMETICS[cid]["photo"], MEDIA["donate"])
    await edit_card(call, product_text(product, player), product_keyboard(pid), photo=photo)
    await call.answer()


@router.callback_query(F.data.startswith("donate:buy:"))
async def cb_donate_buy(call: CallbackQuery) -> None:
    """Выставляет счёт в звёздах. Товар выдаётся после оплаты."""
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    product_id = call.data.split(":")[2]
    product = DONATE_PRODUCTS.get(product_id)
    if not product:
        await call.answer("Такого товара нет", show_alert=True)
        return

    await call.bot.send_invoice(
        chat_id=call.message.chat.id,
        title=product["title"],
        description=product_description(product),
        payload=product_id,
        provider_token="",  # для звёзд токен провайдера не нужен
        currency="XTR",
        prices=[LabeledPrice(label=product["title"], amount=product["stars"])],
    )
    await call.answer("Счёт отправлен ниже 👇")


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    """Telegram спрашивает, можно ли принять платёж. Отвечать надо быстро."""
    product = DONATE_PRODUCTS.get(query.invoice_payload)
    if not product or query.currency != "XTR" or query.total_amount != product["stars"]:
        await query.answer(ok=False, error_message="Товар недоступен, попробуй ещё раз")
        return
    if not await get_player(query.from_user.id):
        await query.answer(ok=False, error_message="Сначала напиши боту /start")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    pay = message.successful_payment
    tg_id = message.from_user.id
    charge_id = pay.telegram_payment_charge_id
    product = DONATE_PRODUCTS.get(pay.invoice_payload)

    if product is None or pay.currency != "XTR" or pay.total_amount != product["stars"]:
        logging.error("Непонятный платёж от %s: %s", tg_id, pay)
        await message.answer(
            "Платёж получен, но товар не распознан. Напиши /paysupport и укажи номер платежа:\n"
            f"{charge_id}"
        )
        return

    status, granted = await record_payment(tg_id, charge_id, pay.invoice_payload, product)
    if status == "duplicate":
        return  # уже выдали раньше, второй раз не начисляем
    if status == "no_player":
        await message.bot.refund_star_payment(user_id=tg_id, telegram_payment_charge_id=charge_id)
        await message.answer("Не нашёл твой аккаунт в игре, звёзды возвращены. Напиши /start.")
        return

    backup_soon(message.bot, "платёж")
    await message.answer(
        await success_text(tg_id, product, granted, charge_id),
        reply_markup=inline([[("✨ Стиль", "menu:style"), ("⬅️ Профиль", "menu:profile")]]),
    )


# --- оплата картой (вне Telegram, выдаёт админ вручную) ----------------------------

@router.callback_query(F.data.startswith("card:info:"))
async def cb_card_info(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    pid = call.data.split(":")[2]
    product = DONATE_PRODUCTS.get(pid)
    if not product or not CARD_PAY_URL:
        await call.answer("Оплата картой сейчас недоступна", show_alert=True)
        return

    await edit_card(
        call, card_text(product, player), card_keyboard(pid), photo=MEDIA["donate"]
    )
    await call.answer()


@router.callback_query(F.data.startswith("card:paid:"))
async def cb_card_paid(call: CallbackQuery) -> None:
    """Игрок говорит, что оплатил. Админу приходит заявка, он проверяет платёж и жмёт /give."""
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    pid = call.data.split(":")[2]
    product = DONATE_PRODUCTS.get(pid)
    if not product:
        await call.answer("Такого товара нет", show_alert=True)
        return

    now = time.time()
    if now - CARD_CLAIMS.get(call.from_user.id, 0) < CARD_CLAIM_COOLDOWN:
        await call.answer("Заявка уже отправлена, подожди немного", show_alert=True)
        return
    CARD_CLAIMS[call.from_user.id] = now

    if not ADMIN_IDS:
        await call.answer(f"Напиши в поддержку: {SUPPORT_CONTACT}", show_alert=True)
        return

    request = (
        "💳 Заявка: оплата картой\n\n"
        f"Игрок: {player['nickname']} (id {call.from_user.id})\n"
        f"Товар: {product['title']} ({pid})\n"
        f"Сумма: {card_price(product)} {CARD_CURRENCY}\n\n"
        f"Проверь платёж у себя. Если пришёл, выдай товар:\n/give {call.from_user.id} {pid}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await call.bot.send_message(admin_id, request)
        except Exception:
            logging.exception("Не удалось отправить заявку админу %s", admin_id)

    await edit_card(
        call,
        "✅ Заявка отправлена\n\n"
        "Платёж проверят, и товар придёт сюда сообщением. "
        f"Если долго нет ответа, пиши {SUPPORT_CONTACT}",
        inline([[("⬅️ В меню доната", "menu:donate")]]),
        photo=MEDIA["donate"],
    )
    await call.answer()


@router.message(Command("give"))
async def cmd_give(message: Message) -> None:
    """Только для админа: /give <id игрока> <товар>. Выдаёт товар за оплату картой."""
    if message.from_user.id not in ADMIN_IDS:
        await deny_not_admin(message)
        return

    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit():
        await message.answer(
            "Используй: /give ID_ИГРОКА ТОВАР\nТовары: " + ", ".join(DONATE_PRODUCTS)
        )
        return

    tg_id, pid = int(parts[1]), parts[2]
    product = DONATE_PRODUCTS.get(pid)
    if not product:
        await message.answer("Нет такого товара. Доступны: " + ", ".join(DONATE_PRODUCTS))
        return

    charge_id = f"card-{int(time.time())}-{tg_id}"
    status, granted = await record_payment(tg_id, charge_id, pid, product, method="card")
    if status != "ok":
        await message.answer("Не нашёл игрока с таким id в игре")
        return

    backup_soon(message.bot, "выдача /give")
    try:
        await message.bot.send_message(tg_id, await success_text(tg_id, product, granted, charge_id))
    except Exception:
        logging.exception("Не удалось написать игроку %s", tg_id)
    await message.answer(f"✅ Выдано игроку {tg_id}: {product['title']}\nНомер: {charge_id}")


# --- команды, которые Telegram требует от ботов с оплатой ----------------------------

@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message) -> None:
    await message.answer(PAYSUPPORT_TEXT)


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    await message.answer(SUPPORT_TEXT)


@router.message(Command("terms"))
async def cmd_terms(message: Message) -> None:
    await message.answer(TERMS_TEXT)


@router.message(Command("refund"))
async def cmd_refund(message: Message) -> None:
    """Только для админа: /refund <номер платежа>. Возвращает деньги и списывает выданное."""
    if message.from_user.id not in ADMIN_IDS:
        await deny_not_admin(message)
        return

    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Используй: /refund НОМЕР_ПЛАТЕЖА")
        return

    charge_id = parts[1]
    info = await get_payment(charge_id)
    if not info:
        await message.answer("Такого платежа нет в базе")
        return
    if info["refunded"]:
        await message.answer("Этот платёж уже возвращён")
        return

    if info["method"] == "stars":
        try:
            await message.bot.refund_star_payment(
                user_id=info["tg_id"], telegram_payment_charge_id=charge_id
            )
        except Exception as e:
            await message.answer(f"Telegram не вернул звёзды: {e}")
            return
        result = f"Звёзды возвращены игроку {info['tg_id']} ({info['stars']} ⭐)."
    else:
        result = "Это оплата картой: деньги верни игроку сам, у себя в банке."

    await mark_refunded(charge_id)
    await message.answer(f"✅ {result}\nВыданные деньги, дни VIP и украшения списаны.")


# --- стиль: значки, титулы, темы, эффекты -----------------------------------------------

@router.message(F.text.func(lambda t: t.strip().lower() in STYLE_TRIGGERS))
async def show_style(message: Message, state: FSMContext) -> None:
    player = await get_player(message.from_user.id)
    if not player:
        await ask_registration(message, state)
        return

    await send_card(
        message,
        style_text(player),
        photo=MEDIA["style"],
        reply_markup=style_keyboard(),
    )


@router.callback_query(F.data == "menu:style")
async def cb_style_menu(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    await edit_card(call, style_text(player), style_keyboard(), photo=MEDIA["style"])
    await call.answer()


@router.callback_query(F.data.startswith("style:cat:"))
async def cb_style_cat(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    cat = call.data.split(":")[2]
    if cat not in COSMETIC_CATEGORIES:
        await call.answer("Такой категории нет", show_alert=True)
        return

    await edit_card(
        call, style_cat_text(player, cat), style_cat_keyboard(player, cat), photo=MEDIA["style"]
    )
    await call.answer()


@router.callback_query(F.data.startswith("style:eq:") | F.data.startswith("style:off:"))
async def cb_style_equip(call: CallbackQuery) -> None:
    player = await get_player(call.from_user.id)
    if not player:
        await call.answer("Сначала напиши /start", show_alert=True)
        return

    _, action, target = call.data.split(":")
    if action == "off":
        cat, item_id = target, ""
        if cat not in COSMETIC_CATEGORIES:
            await call.answer("Такой категории нет", show_alert=True)
            return
        note = "Снято"
    else:
        item = COSMETICS.get(target)
        if not item:
            await call.answer("Такой вещи нет", show_alert=True)
            return
        cat, item_id = item["cat"], target
        note = f"✅ Надето: {item['name']}"

    if not await set_equipped(call.from_user.id, cat, item_id):
        await call.answer("Эта вещь не куплена", show_alert=True)
        return

    player = await get_player(call.from_user.id)
    await edit_card(
        call,
        style_cat_text(player, cat, note),
        style_cat_keyboard(player, cat),
        photo=profile_photo(player) if cat == "theme" else MEDIA["style"],
    )
    await call.answer()


# ===========================================================================
# ХРАНЕНИЕ БАЗЫ В TELEGRAM И ЗДОРОВЬЕ СЕРВЕРА
# ===========================================================================
# На бесплатных хостингах диск временный: при перезапуске game.db пропадает.
# Поэтому бот сам кладёт копию базы в закреплённое сообщение в личке с админом
# (одно и то же сообщение, файл в нём заменяется) и при старте без базы
# забирает её оттуда. Нужно один раз написать боту /start с аккаунта админа.

# По умолчанию включено только на хостинге (там задан PORT). Дома, в пайчарме, выключено,
# чтобы твоя тестовая база случайно не затёрла копию с сервера. Принудительно: BACKUP_ENABLED=1
BACKUP_ENABLED = os.getenv("BACKUP_ENABLED", "1" if os.getenv("PORT") else "0") != "0"
BACKUP_CHAT_ID = int(os.getenv("BACKUP_CHAT_ID", "0") or 0) or (ADMIN_IDS[0] if ADMIN_IDS else 0)
BACKUP_MIN_INTERVAL = 180        # не чаще раза в 3 минуты, если игроки что-то делают
BOT_VERSION = "2026-10-04-b"     # по ней видно, какая версия кода сейчас запущена
BACKUP_MARK = "#gamedb"          # по этой метке бот узнаёт своё сообщение с базой
BACKUP = {"message_id": 0, "dirty": False, "last": 0.0}
BACKUP_LOCK = asyncio.Lock()


class MarkDirty(BaseMiddleware):
    """После любого действия игрока помечает базу как изменённую."""

    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        finally:
            BACKUP["dirty"] = True


def _snapshot_sync(dest: Path) -> None:
    """Безопасная копия базы, даже если в неё сейчас пишут."""
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _db_stats(path: Path) -> tuple[int, int]:
    """(игроков, платежей) в файле базы."""
    con = sqlite3.connect(path)
    try:
        players = con.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        try:
            payments = con.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        except sqlite3.Error:
            payments = 0
        return players, payments
    finally:
        con.close()


def _valid_db(path: Path) -> bool:
    try:
        con = sqlite3.connect(path)
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return False
            con.execute("SELECT COUNT(*) FROM players").fetchone()
            return True
        finally:
            con.close()
    except sqlite3.Error:
        return False


async def push_backup(bot: Bot, reason: str = "") -> bool:
    """Заменяет файл базы в закреплённом сообщении (или создаёт его в первый раз)."""
    if not BACKUP_ENABLED or not BACKUP_CHAT_ID or not Path(DB_PATH).exists():
        return False

    async with BACKUP_LOCK:
        tmp = Path(tempfile.gettempdir()) / "game_backup.db"
        try:
            await asyncio.to_thread(_snapshot_sync, tmp)
            players, payments = await asyncio.to_thread(_db_stats, tmp)
            if players == 0:
                return False  # пустую базу не сохраняем, чтобы не затереть хорошую копию

            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            caption = f"{BACKUP_MARK} {stamp} UTC\nИгроков: {players}, платежей: {payments}"
            if reason:
                caption += f"\n({reason})"

            if BACKUP["message_id"]:
                try:
                    await bot.edit_message_media(
                        chat_id=BACKUP_CHAT_ID,
                        message_id=BACKUP["message_id"],
                        media=InputMediaDocument(
                            media=FSInputFile(tmp, filename="game.db"), caption=caption
                        ),
                    )
                    BACKUP["last"] = time.time()
                    return True
                except TelegramBadRequest:
                    logging.warning("Сообщение с базой не удалось изменить, отправлю новое")
                    BACKUP["message_id"] = 0

            sent = await bot.send_document(
                BACKUP_CHAT_ID, FSInputFile(tmp, filename="game.db"), caption=caption
            )
            BACKUP["message_id"] = sent.message_id
            try:
                await bot.pin_chat_message(
                    BACKUP_CHAT_ID, sent.message_id, disable_notification=True
                )
            except Exception:
                logging.exception("Не удалось закрепить сообщение с базой")
            BACKUP["last"] = time.time()
            return True
        except Exception:
            logging.exception("Не удалось сделать бэкап")
            return False
        finally:
            tmp.unlink(missing_ok=True)


def backup_soon(bot: Bot, reason: str) -> None:
    """Сохранить базу прямо сейчас, не задерживая ответ игроку (например, после оплаты)."""
    task = asyncio.create_task(push_backup(bot, reason))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)


async def backup_loop(bot: Bot) -> None:
    """Раз в полминуты смотрит, были ли изменения, и сохраняет базу не чаще BACKUP_MIN_INTERVAL."""
    while True:
        await asyncio.sleep(30)
        if BACKUP["dirty"] and time.time() - BACKUP["last"] >= BACKUP_MIN_INTERVAL:
            BACKUP["dirty"] = False
            if not await push_backup(bot):
                BACKUP["dirty"] = True


async def restore_from_telegram(bot: Bot) -> str:
    """
    Забирает базу из закреплённого сообщения.
    Вернёт 'restored', 'none' (копии нет, можно начинать с чистой базы) или 'error'.
    """
    if not BACKUP_ENABLED or not BACKUP_CHAT_ID:
        return "none"
    try:
        chat = await bot.get_chat(BACKUP_CHAT_ID)
    except TelegramBadRequest:
        return "none"  # админ ещё ни разу не писал боту, копии быть не может
    except Exception:
        logging.exception("Не удалось связаться с Telegram за базой")
        return "error"

    pinned = chat.pinned_message
    if not pinned or not pinned.document or not (pinned.caption or "").startswith(BACKUP_MARK):
        return "none"

    tmp = Path(tempfile.gettempdir()) / "game_restore.db"
    try:
        await bot.download(pinned.document, destination=tmp)
        if not await asyncio.to_thread(_valid_db, tmp):
            logging.error("Файл базы из Telegram повреждён")
            return "error"
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), DB_PATH)
        BACKUP["message_id"] = pinned.message_id
        return "restored"
    except Exception:
        logging.exception("Не удалось восстановить базу")
        return "error"
    finally:
        tmp.unlink(missing_ok=True)


async def deny_not_admin(message: Message) -> None:
    await message.answer(
        "⛔ Эта команда только для админа.\n"
        f"Твой Telegram id: {message.from_user.id}\n"
        "Если админ это ты, впиши этот id в ADMIN_IDS (переменная на сервере или строка в коде)."
    )


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    """Показывает твой Telegram id и состояние бота. Удобно, чтобы проверить настройки."""
    is_admin = message.from_user.id in ADMIN_IDS
    await message.answer(
        f"Твой Telegram id: {message.from_user.id}\n"
        f"Админ: {'да' if is_admin else 'нет'}\n"
        f"Версия бота: {BOT_VERSION}\n"
        f"Бэкап в Telegram: {'вкл' if BACKUP_ENABLED else 'выкл'}"
    )


@router.message(Command("backup"))
async def cmd_backup(message: Message) -> None:
    """Только для админа: сохранить базу в Telegram прямо сейчас."""
    if message.from_user.id not in ADMIN_IDS:
        await deny_not_admin(message)
        return
    ok = await push_backup(message.bot, "вручную")
    await message.answer("✅ База сохранена в закреплённое сообщение" if ok else "❌ Не получилось, смотри логи")


@router.message(F.document & F.caption.startswith("/restore"))
async def cmd_restore(message: Message) -> None:
    """Только для админа: пришли файл game.db с подписью /restore, и база заменится."""
    if message.from_user.id not in ADMIN_IDS:
        await deny_not_admin(message)
        return

    tmp = Path(tempfile.gettempdir()) / "game_upload.db"
    try:
        await message.bot.download(message.document, destination=tmp)
        if not await asyncio.to_thread(_valid_db, tmp):
            await message.answer("❌ Это не похоже на базу игры, ничего не менял")
            return
        players, payments = await asyncio.to_thread(_db_stats, tmp)
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), DB_PATH)
        await init_db()  # на случай, если база старой версии
        await message.answer(f"✅ База заменена. Игроков: {players}, платежей: {payments}")
    finally:
        tmp.unlink(missing_ok=True)


async def start_health_server():
    """
    Маленький веб-сервер для хостингов, которым нужен открытый порт (переменная PORT).
    По адресу / отвечает «ok»: его можно пинговать, чтобы бесплатный хостинг не засыпал.
    """
    port = int(os.getenv("PORT", "0") or 0)
    if not port:
        return None

    async def alive(request):
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", alive)
    app.router.add_get("/health", alive)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logging.info("Веб-сервер для проверки здоровья слушает порт %s", port)
    return runner


# ===========================================================================
# ЗАПУСК
# ===========================================================================

async def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.info(
        "Запуск бота. Версия: %s. Админы: %s. Бэкап в Telegram: %s. Файл базы: %s",
        BOT_VERSION, ADMIN_IDS, "вкл" if BACKUP_ENABLED else "выкл", DB_PATH,
    )

    MEDIA_DIR.mkdir(exist_ok=True)
    check_media()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    dp.update.outer_middleware(MarkDirty())

    # Порт открываем первым делом: хостинги ждут его при старте
    health_runner = await start_health_server()

    # Нет базы (например, на бесплатном хостинге после перезапуска): забираем копию из Telegram
    if not Path(DB_PATH).exists():
        result = "none"
        for attempt in range(5):
            result = await restore_from_telegram(bot)
            if result != "error":
                break
            await asyncio.sleep(5)
        if result == "error":
            # Лучше упасть и перезапуститься, чем начать с пустой базой и затереть копию
            raise SystemExit("Не удалось забрать базу из Telegram, останавливаюсь")
        logging.info(
            "База восстановлена из Telegram" if result == "restored"
            else "Копии базы нет, начинаю с чистой"
        )

    await init_db()

    backup_task = asyncio.create_task(backup_loop(bot))
    try:
        # Не сбрасываем накопившиеся апдейты: среди них могут быть оплаты, пока бот спал
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot)
    finally:
        backup_task.cancel()
        await push_backup(bot, "остановка бота")
        if health_runner:
            await health_runner.cleanup()
        await bot.session.close()


# Запуск без проверки имени модуля, чтобы не зависеть от подчёркиваний
asyncio.run(main())
