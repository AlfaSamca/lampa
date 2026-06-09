import asyncio
import calendar
from datetime import datetime, timedelta
from aiogram.types import FSInputFile
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, \
    ReplyKeyboardRemove, BotCommand, BotCommandScopeChat  # Добавь BotCommandScopeChat и BotCommand
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import os
from dotenv import load_dotenv

# Загружаем переменные из файла .env
load_dotenv()

# ---------------- CONFIG ----------------
# Теперь данные берутся из окружения.
# Если переменная не найдена, вернется None или дефолтное значение.
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 8607101615  # ID должен быть числомint(os.getenv("ADMIN_ID"))
SMM_ID = 440536095
DB_NAME = os.getenv("DB_NAME", "lampa.sqlite")

# Остальные настройки можно оставить как есть, так как они не секретные
MENU_URL = "https://menusa.app/11f147d08be313bb8dcc55efc6664fa5"
TOTAL_TABLES = 23

FLOORS = {
    "floor_1": {
        "name": "🛋 Первый этаж (основной зал)",
        "photo": "AgACAgIAAxkBAAIEdGoa0qFRz0wwxyJkO92KFg_67eK3AAKIFmsbl43YSIwJB61T65ggAQADAgADeQADOwQ"
    },
    "floor_2": {
        "name": "✨ Второй этаж (уютная зона)",
        "photo": "AgACAgIAAxkBAAIEe2oa0rvXp2vugO2yWTuEDO1ulVynAAKCFmsbl43YSFtcodLBYoPbAQADAgADeQADOwQ"
    }
}
EVENT_TEXT = """
<b>ВЕЧЕРИНКА-ОТКРЫТИЕ ЛАМПЫ💫</b>

🗓️ в эту субботу 30 мая
🕑 с 18:00 и до утра
📍 Октябрьская 23

Крутая программа в концепции «современное ЭТНО» с активностями, розыгрышами, фуршетом из наших вкуснейших блюд, тематическими фотозонами и многим другим🔥

Мы пригласили ведущего, фотографа, крутых белорусских диджеев, чтобы вы незабываемо провели время❤️
"""

EVENT_VIDEO_ID = "BAACAgIAAxkBAAIEcmoa0mkjs9qkoad9a-wQSvzjsJOPAALUmwACGpjQSMjTL8-8iQ_qOwQ"  # file_id видео
TABLES = {
    # Первый этаж
    "11": {"floor": "floor_1", "capacity": 2},
    "12": {"floor": "floor_1", "capacity": 2},
    "13": {"floor": "floor_1", "capacity": 2},
    "14": {"floor": "floor_1", "capacity": 2},
    "15": {"floor": "floor_1", "capacity": 2},
    "16": {"floor": "floor_1", "capacity": 2},
    "17": {"floor": "floor_1", "capacity": 2},
    "18": {"floor": "floor_1", "capacity": 5},  # 4-5 гостей
    # Второй этаж
    "21": {"floor": "floor_2", "capacity": 2},
    "22": {"floor": "floor_2", "capacity": 5},  # 4-5 гостей
    "23": {"floor": "floor_2", "capacity": 5},  # 4-5 гостей
    "24": {"floor": "floor_2", "capacity": 5},  # 4-5 гостей
    "25": {"floor": "floor_2", "capacity": 8},  # 6-8 гостей
}
if not BOT_TOKEN:
    exit("Ошибка: токен бота не найден! Проверь файл .env")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
booking_lock = asyncio.Lock()
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

# Настраиваем хранилище задач.
# Используем ту же базу, что и для броней, но через протокол sqlite:///
jobstores = {
    'default': SQLAlchemyJobStore(url=f'sqlite:///{DB_NAME}')
}

# Инициализируем планировщик с этим хранилищем
scheduler = AsyncIOScheduler(jobstores=jobstores)


# ------------------Base---------------------
class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row

    async def close(self):
        if self.conn:
            await self.conn.close()
            print("=== Соединение с SQLite успешно разорвано ===")

    async def execute(self, query, params=None):
        """Для простых операций INSERT/UPDATE/DELETE, где не нужен ID"""
        async with self.conn.execute(query, params or ()) as cursor:
            await self.conn.commit()
            return cursor

    async def fetchall(self, query, params=None):
        """Безопасное получение списка строк"""
        async with self.conn.execute(query, params or ()) as cursor:
            return await cursor.fetchall()

    async def fetchone(self, query, params=None):
        """Безопасное получение одной строки"""
        async with self.conn.execute(query, params or ()) as cursor:
            return await cursor.fetchone()

    async def add_guest(self, user_id: int, username: str):
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        query = """
            INSERT INTO guests (user_id, username, first_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username
        """
        await self.execute(query, (user_id, username, now))

    async def update_guest_phone(self, user_id: int, phone: str):
        query = "UPDATE guests SET phone = ? WHERE user_id = ?"
        await self.execute(query, (phone, user_id))


# Создаем объект БД
db_manager = Database(DB_NAME)


# ---------------- FSM (Состояния) ----------------
# ---------------- FSM (Состояния) ----------------
class Booking(StatesGroup):
    date = State()
    time = State()
    floor = State()
    table = State()
    guests = State()
    name = State()
    phone = State()
    comment = State()
    feedback = State()
    admin_info = State()

    # ответ на отзыв
    reply_feedback = State()


# ---------------- DB INIT ----------------
# ---------------- DB INIT ----------------
async def init_db():
    await db_manager.conn.execute('''CREATE TABLE IF NOT EXISTS guests (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            phone TEXT,
            first_seen TEXT
        )''')
    await db_manager.conn.execute('''CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            time TEXT,
            guests INTEGER,
            name TEXT,
            phone TEXT,
            comment TEXT,
            wishes TEXT,
            zone TEXT,
            status TEXT DEFAULT 'pending'
        )''')

    # Добавление новых колонок для покросс-платформенной совместимости со старой БД
    try:
        await db_manager.conn.execute("ALTER TABLE bookings ADD COLUMN table_id TEXT")
    except:
        pass
    try:
        await db_manager.conn.execute("ALTER TABLE bookings ADD COLUMN floor_id TEXT")
    except:
        pass
    try:
        await db_manager.conn.execute("ALTER TABLE bookings ADD COLUMN status TEXT DEFAULT 'pending'")
    except:
        pass

    await db_manager.conn.execute('''
    CREATE TABLE IF NOT EXISTS feedbacks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        feedback_text TEXT,
        created_at TEXT,
        status TEXT DEFAULT 'new'
    )
    ''')
    await db_manager.conn.commit()


# ---------------- LOGIC ----------------
def get_duration(dt: datetime):
    return timedelta(hours=1, minutes=30) if 12 <= dt.hour < 18 else timedelta(hours=3)


async def get_busy_tables(date: str, time: str):
    try:
        # Парсим время начала планируемой брони
        start = datetime.strptime(f"{date} {time}", "%d.%m.%Y %H:%M")
    except ValueError:
        return list(TABLES.keys())  # Если дата/время кривые, блокируем вообще все столы от греха подальше

    # Если время уже прошло, бронировать нельзя — отдаем все столы как занятые
    if start < datetime.now():
        return list(TABLES.keys())

    # Вычисляем время окончания планируемой брони
    duration = get_duration(start)
    end = start + duration

    # Раньше мы брали guests и zone, а теперь берем только time и table_id
    rows = await db_manager.fetchall(
        """
        SELECT time, table_id
        FROM bookings
        WHERE date = ?
        AND status IN ('pending', 'confirmed')
        """,
        (date,)
    )

    busy_tables = []

    # Проверяем каждую существующую бронь на этот день
    for r_time, r_table_id in rows:
        try:
            if not r_table_id:
                continue

            existing_start = datetime.strptime(f"{date} {r_time}", "%d.%m.%Y %H:%M")
            existing_end = existing_start + get_duration(existing_start)

            # Проверка на пересечение интервалов (нахлёст времени)
            if not (end <= existing_start or start >= existing_end):
                # Если время пересекается, добавляем этот столик в список занятых
                busy_tables.append(str(r_table_id))
        except:
            continue

    # Возвращаем чистый список занятых ID (например: ['11', '12', '24'])
    return busy_tables


async def send_reminder(chat_id: int, booking_date: str, booking_time: str):
    try:
        text = (
            f"🔔 <b>Напоминание о бронировании!</b>\n\n"
            f"Ждем вас сегодня в <b>{booking_time}</b>!\n"
            f"📍 Адрес: ул. Октябрьская, 23\n\n"
            f"Если ваши планы изменились, пожалуйста, сообщите нам. ❤️"
        )
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        print(f"Не удалось отправить напоминание {chat_id}: {e}")


# ---------------- KEYBOARDS ----------------
def main_menu():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🍽 Забронировать стол", callback_data="book"))
    # Добавляем кнопку Мероприятия
    kb.row(InlineKeyboardButton(text="🎉 Мероприятия", callback_data="events"))
    kb.row(InlineKeyboardButton(text="📖 Меню", url=MENU_URL),
           InlineKeyboardButton(text="📍 Найти нас", callback_data="map"))
    kb.row(InlineKeyboardButton(text="❓ FAQ", callback_data="faq"),
           InlineKeyboardButton(text="💬 Отзыв", callback_data="feedback"))
    return kb.as_markup()


async def get_time_kb(date: str):
    kb = InlineKeyboardBuilder()
    times = ["12:00", "12:30", "13:00", "13:30", "14:00", "14:30",
             "15:00", "15:30", "16:00", "16:30", "17:00", "17:30",
             "18:00", "18:30", "19:00", "19:30", "20:00", "20:30",
             "21:00", "21:30", "22:00", "22:30", "23:00"]

    for t in times:
        busy_list = await get_busy_tables(date, t)

        # Слот полностью занят, если заняты все столы в баре
        if len(busy_list) >= len(TABLES):
            status = "full"
        elif len(busy_list) > 0:
            status = "partial"
        else:
            status = "all"

        icon = "🔴" if status == "full" else ("🟢" if status == "all" else "🟡")
        callback = "ignore" if status == "full" else f"time:{t}"
        kb.button(text=f"{icon} {t}", callback_data=callback)

    kb.adjust(3)
    return kb.as_markup()


def get_calendar_kb(year, month):
    kb = InlineKeyboardBuilder()
    today = datetime.now().date()
    months = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь",
              "Декабрь"]

    # Шапка: Название месяца и года
    kb.row(InlineKeyboardButton(text=f"🗓 {months[month - 1]} {year}", callback_data="ignore"))

    # Дни недели
    week_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    kb.row(*[InlineKeyboardButton(text=day, callback_data="ignore") for day in week_days])

    # Генерация сетки календаря
    for week in calendar.monthcalendar(year, month):
        buttons = []
        for day in week:
            if day == 0:
                buttons.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                date_obj = datetime(year, month, day).date()
                if date_obj < today:
                    buttons.append(InlineKeyboardButton(text="❌", callback_data="ignore"))
                else:
                    buttons.append(
                        InlineKeyboardButton(text=str(day), callback_data=f"date:{day:02d}.{month:02d}.{year}"))
        kb.row(*buttons)

    # Кнопки навигации (Пред. месяц | След. месяц)
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    # Не даем листать назад в прошлое (раньше текущего месяца)
    now = datetime.now()
    if year > now.year or (year == now.year and month > now.month):
        kb.row(
            InlineKeyboardButton(text="⬅️", callback_data=f"prev_month:{prev_year}:{prev_month}"),
            InlineKeyboardButton(text="➡️", callback_data=f"next_month:{next_year}:{next_month}")
        )
    else:
        kb.row(
            InlineKeyboardButton(text=" ", callback_data="ignore"),
            InlineKeyboardButton(text="➡️", callback_data=f"next_month:{next_year}:{next_month}")
        )

    return kb.as_markup()


# ---------------- HANDLERS ----------------


@dp.message(CommandStart())
async def start(message: Message):
    # СОХРАНЕНИЕ В БАЗУ
    await db_manager.add_guest(
        user_id=message.from_user.id,
        username=message.from_user.username or "NoUsername"
    )

    await message.answer(
        f"Здравствуйте, <b>{message.from_user.first_name}</b>! 💫\n\n"
        "Рады приветствовать вас в Лампе. Я помогу вам забронировать столик "
        "и отвечу на вопросы. С чего начнем?😉",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )


# --- Инструкция по использованию ---
@dp.message(Command("help"))
async def help_command(message: Message):
    help_text = (
        "✨ <b>Как пользоваться ботом гастробара «Лампа»</b>\n\n"
        "1️⃣ <b>Бронирование:</b> Нажмите «🍽 Забронировать стол» в меню. Выберите дату, время, зал и столик. Обязательно введите ваше имя и телефон.\n\n"
        "2️⃣ <b>Подтверждение:</b> Ваш запрос уходит администратору. Как только он его подтвердит, вам придет сообщение. ✅\n\n"
        "3️⃣ <b>Напоминание:</b> Бот сам напомнит вам о визите за 2 часа до времени записи.\n\n"
        "4️⃣ <b>Меню и Карта:</b> Кнопки «📖 Меню» и «📍 Найти нас» помогут сориентироваться в блюдах и маршруте.\n\n"
        "5️⃣ <b>Отмена:</b> Если планы изменились, пожалуйста, напишите нам в ответ на это сообщение или свяжитесь через «💬 Отзыв».\n\n"
        "<i>Будем рады видеть вас!</i>"
    )

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main"))

    await message.answer(
        help_text,
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )


# --- Найти нас ---
@dp.callback_query(F.data == "map")
async def show_map(callback: CallbackQuery):
    address_text = (
        "📍 <b>Наш адрес:</b>\n"
        "г. Минск, ул. Октябрьская, 23\n"
        "<a href='https://yandex.com/maps/157/minsk/house/Zk4YcwBkTEMHQFtpfXR4cXxlYQ==/'>Открыть в Яндекс.Картах</a>\n\n"
        "🕰 <b>Мы открыты:</b>\n"
        "Ежедневно с 12:00 до 23:00\n\n"
        "Ждем вас в гости! ✨"
    )
    await callback.message.answer(address_text, parse_mode="HTML", disable_web_page_preview=False)
    # Отправка локации (координаты)
    await callback.message.answer_location(latitude=53.890065, longitude=27.574560)
    await callback.answer()


# --- Процесс бронирования ---
@dp.callback_query(F.data == "book")
async def book_init(callback: CallbackQuery):
    now = datetime.now()
    await callback.message.edit_text(
        "Пожалуйста, выберите <b>дату визита</b> в календаре:",
        reply_markup=get_calendar_kb(now.year, now.month),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("prev_month:") | F.data.startswith("next_month:"))
async def change_month(callback: CallbackQuery):
    _, year, month = callback.data.split(":")
    await callback.message.edit_reply_markup(
        reply_markup=get_calendar_kb(int(year), int(month))
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("date:"))
async def set_date(callback: CallbackQuery, state: FSMContext):
    date_str = callback.data.split(":")[1]
    await state.update_data(date=date_str)
    kb = await get_time_kb(date_str)
    await callback.message.edit_text(
        f"Вы выбрали дату: <b>{date_str}</b>\nТеперь подберем удобное время:",
        reply_markup=kb,
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("time:"))
async def set_time(callback: CallbackQuery, state: FSMContext):
    time = callback.data.split(":", 1)[1]
    data = await state.get_data()
    date_str = data.get('date')

    if date_str and date_str.startswith("30.05.") and time > "14:00":
        return await callback.answer("Извините, 30 мая бронирование доступно только до 14:00 🕑", show_alert=True)

    busy_tables = await get_busy_tables(date_str, time)
    if len(busy_tables) >= len(TABLES):
        return await callback.answer("Извините, на это время все столы уже заняты", show_alert=True)

    await state.update_data(time=time)

    kb = InlineKeyboardBuilder()
    for floor_id, floor_data in FLOORS.items():
        kb.button(text=floor_data["name"], callback_data=f"floor:{floor_id}")
    kb.adjust(1)

    await callback.message.edit_text("В каком зале вы бы хотели отдохнуть?", reply_markup=kb.as_markup())
    await state.set_state(Booking.floor)


# ШАГ 2: Отправка фото-схемы выбранного этажа и интерактивной клавиатуры столов
# ШАГ 2: Отправка фото-схемы выбранного этажа и интерактивной клавиатуры столов
@dp.callback_query(F.data.startswith("floor:"))
async def set_floor(callback: CallbackQuery, state: FSMContext):
    floor_id = callback.data.split(":")[1]
    await state.update_data(floor=floor_id)

    data = await state.get_data()
    busy_tables = await get_busy_tables(data['date'], data['time'])

    kb = InlineKeyboardBuilder()

    # Фильтруем и выводим столы только для выбранного этажа
    for t_id, t_data in TABLES.items():
        if t_data['floor'] == floor_id:
            if t_id in busy_tables:
                kb.button(text=f"❌ Стол {t_id}", callback_data="ignore")
            else:
                kb.button(text=f"✅ Стол {t_id}", callback_data=f"table:{t_id}")

    kb.adjust(3)
    kb.row(InlineKeyboardButton(text="⬅️ Изменить зал", callback_data="change_hall"))  # Возврат на шаг назад



    photo_path = FLOORS[floor_id]["photo"]

    try:
        # Если это file_id (обычно начинается на Ag) или http-ссылка,
        # aiogram отлично примет простую строку
        if photo_path.startswith("http") or photo_path.startswith("Ag"):
            photo = photo_path
        else:
            # Оборачиваем в FSInputFile, только если это реальный путь к файлу на диске
            photo = FSInputFile(photo_path)

        await callback.message.answer_photo(
            photo=photo,
            caption=f"<b>{FLOORS[floor_id]['name']}</b>\n\nПожалуйста, ознакомьтесь со схемой расположения и выберите свободный столик на кнопках ниже (в скобках указано количество гостей):",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        # Резервный вариант, если файл схемы отсутствует на сервере (или file_id неверный)
        print(f"Ошибка отправки фото схемы: {e}")
        await callback.message.answer(
            f"<b>{FLOORS[floor_id]['name']}</b>\n\nВыберите свободный столик ниже:",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )

    await state.set_state(Booking.table)


@dp.callback_query(F.data == "change_hall")
async def change_hall_handler(callback: CallbackQuery, state: FSMContext):
    # Удаляем сообщение с фотографией схемы зала, чтобы очистить чат

    # Генерируем клавиатуру выбора зала заново
    kb = InlineKeyboardBuilder()
    for floor_id, floor_data in FLOORS.items():
        kb.button(text=floor_data["name"], callback_data=f"floor:{floor_id}")
    kb.adjust(1)

    # Отправляем текстовое сообщение с выбором зала
    await callback.message.answer(
        "В каком зале вы бы хотели отдохнуть?",
        reply_markup=kb.as_markup()
    )

    # Возвращаем пользователя на состояние выбора этажа
    await state.set_state(Booking.floor)
    await callback.answer()
# ШАГ 3: Фиксация выбранного стола и переход к запросу количества гостей
@dp.callback_query(F.data.startswith("table:"))
async def set_table(callback: CallbackQuery, state: FSMContext):
    table_id = callback.data.split(":")[1]
    await state.update_data(table=table_id)

    max_capacity = TABLES[table_id]["capacity"]


    await callback.message.answer(
        f"Вы выбрали <b>Стол №{table_id}</b>.\n"
        f"Данный столик рассчитан максимум на <b>{max_capacity} чел.</b>\n\n"
        f"Сколько будет гостей?",
        parse_mode="HTML"
    )
    await state.set_state(Booking.guests)


# ШАГ 4: Валидация количества гостей с учетом ограничений конкретного стола


@dp.message(Booking.guests)
async def guests_process(message: Message, state: FSMContext):
    # 1. Проверка ввода
    if not message.text or not message.text.isdigit():
        return await message.answer("Пожалуйста, укажите количество гостей числом.")

    num = int(message.text)
    data = await state.get_data()

    table_id = data.get('table')

    # защита на случай, если используется table-flow
    if table_id:
        limit = TABLES[table_id]['capacity']

        if num > limit:
            return await message.answer(
                f"Выбранный Стол №{table_id} вмещает не более {limit} гостей. Пожалуйста, укажите подходящее число гостей:\n"
                f"(Для размещения более 8 человек звоните +375 29 149-33-33)"
            )


    # 2. Сохраняем гостей
    await state.update_data(guests=num)

    # 3. 🔥 АДМИНСКИЙ ФЛОУ
    if data.get("is_admin_booking"):
        await message.answer(
            "📞 Введите данные одной строкой:\n\n"
            "ФИО\n"
            "Телефон\n"
            "Комментарий (если есть)"
        )
        await state.set_state(Booking.admin_info)
        return

    # 4. Обычный пользовательский поток
    await message.answer("Благодарю. Как нам к вам обращаться (ваше имя)?")
    await state.set_state(Booking.name)


@dp.message(Booking.name)
async def name_process(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    # Кнопка запроса контакта
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(
        "Осталось совсем чуть-чуть! Оставьте ваш номер телефона для связи. "
        "Нажмите на кнопку ниже или введите номер вручную.",
        reply_markup=kb
    )
    await state.set_state(Booking.phone)


@dp.message(Booking.phone)
async def phone_process(message: Message, state: FSMContext):
    # Проверяем, пришел контакт или текст
    if message.contact:
        phone = message.contact.phone_number
    elif message.text and len(message.text) > 5:
        phone = message.text
    else:
        return await message.answer("Пожалуйста, отправьте номер через кнопку или введите его вручную.")

    # СОХРАНЯЕМ ТЕЛЕФОН В ТАБЛИЦУ GUESTS
    await db_manager.update_guest_phone(message.from_user.id, phone)

    # Сохраняем в FSM для текущей брони
    await state.update_data(phone=phone)

    await message.answer(
        "Есть ли у вас особые пожелания или комментарии? "
        "(Детский стул, аллергия, повод визита). Если нет - просто напишите любое сообщение.",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(Booking.comment)


@dp.message(Booking.comment)
async def comment_process(message: Message, state: FSMContext):
    await state.update_data(comment=message.text)

    data = await state.get_data()
    user_id = message.from_user.id

    # -------------------------------
    # КРИТИЧЕСКАЯ СЕКЦИЯ
    # -------------------------------
    async with booking_lock:

        # Повторно проверяем занятость стола
        busy_tables = await get_busy_tables(
            data['date'],
            data['time']
        )

        if data['table'] in busy_tables:
            await message.answer(
                "❌ К сожалению, этот столик только что забронировал другой гость.\n\n"
                "Пожалуйста, начните бронирование заново и выберите другой столик.",
                reply_markup=main_menu()
            )

            await state.clear()
            return

        query = """
            INSERT INTO bookings (
                user_id,
                date,
                time,
                guests,
                table_id,
                floor_id,
                name,
                phone,
                comment,
                wishes,
                status
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """

        params = (
            user_id,
            data['date'],
            data['time'],
            data['guests'],
            data['table'],
            data['floor'],
            data['name'],
            data['phone'],
            data['comment'],
            "",
            'pending'
        )

        async with db_manager.conn.execute(query, params) as cursor:
            booking_id = cursor.lastrowid
            await db_manager.conn.commit()

    # -------------------------------
    # Дальше уже обычная логика
    # -------------------------------

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="✅ Подтвердить",
            callback_data=f"conf_{booking_id}"
        ),
        InlineKeyboardButton(
            text="❌ Отменить",
            callback_data=f"canc_{booking_id}"
        )
    )

    floor_name = FLOORS.get(
        data['floor'],
        {}
    ).get(
        'name',
        data['floor']
    )

    admin_msg = (
        f"<b>🔔 НОВАЯ БРОНЬ №{booking_id}</b>\n\n"
        f"📅 Дата: <b>{data['date']}</b> в <b>{data['time']}</b>\n"
        f"📍 Зал: {floor_name}\n"
        f"🪑 <b>СТОЛ №{data['table']}</b>\n"
        f"👥 Гости: {data['guests']} чел.\n"
        f"👤 Имя: {data['name']}\n"
        f"📞 Тел: <code>{data['phone']}</code>\n"
        f"💬 Коммент: {data['comment']}"
    )

    try:
        await bot.send_message(
            ADMIN_ID,
            admin_msg,
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )
    except Exception as e:
        print(f"Ошибка при уведомлении админа: {e}")

    await message.answer(
        "Спасибо! Мы передали информацию администратору. ✨\n\n"
        "Как только столик будет подтвержден, вам придет сообщение.",
        reply_markup=main_menu()
    )

    await state.clear()

@dp.callback_query(F.data == "ignore")
async def ignore_cb(callback: CallbackQuery):
    await callback.answer()


# --- Обработка FAQ ---

@dp.callback_query(F.data == "faq")
async def faq_handler(callback: CallbackQuery):
    faq_text = (
        "<b>Ответы на частые вопросы:</b>\n\n"
        "<b>1. Есть ли депозит?</b>\n"
        "Нет, у нас всё просто — приходишь и отдыхаешь. ✨\n\n"
        "<b>2. Есть ли парковка?</b>\n"
        "Да, рядом с нами есть городская парковка. 🚗\n\n"
        "<b>3. Можно ли отметить день рождения?</b>\n"
        "Конечно! Мы будем рады стать частью вашего праздника. "
        "Укажите повод в комментариях при бронировании, и мы подберем для вас лучший стол! 🎉"
    )

    # Создаем кнопку "Назад", чтобы пользователь мог вернуться в меню
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_main"))

    await callback.message.edit_text(
        faq_text,
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


@dp.callback_query(F.data == "events")
async def events_handler(callback: CallbackQuery):

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="⬅️ Назад в меню",
            callback_data="back_to_main"
        )
    )

    try:

        await callback.message.answer_video(
            video=EVENT_VIDEO_ID,
            caption=EVENT_TEXT,
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )

    except Exception as e:
        print(f"Ошибка отправки мероприятия: {e}")

        await callback.message.answer(
            EVENT_TEXT,
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )

    await callback.answer()


# Хэндлер для возврата в главное меню
@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):

    try:
        await callback.message.delete()
    except:
        pass

    await callback.message.answer(
        f"Здравствуйте, <b>{callback.from_user.first_name}</b>! ✨\n\n"
        "Рады приветствовать вас в Lampa. С чего начнем?",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )

    await callback.answer()


# --- Обработка отзывов ---

@dp.callback_query(F.data == "feedback")
async def feedback_init(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Любимые гости, мы хотим быть лучшими для вас🫶🏼 Оставьте пожалуйста отзывы о блюдах и атмосфере в ЛАМПЕ \n(все направляются директору;)\n"
        "\n1. Что вы заказали? (блюдо/напиток)\n"
        "2. Было ли что-то, что хотелось улучшить в блюдах или напитках?\n"
        "3. Откуда вы узнали про ЛАМПУ? (инст / ТикТок / тредс / увидели на Октябрьской / от знакомых или др)\n"
        "\nСпасибо, что заглянули к нам💫"
    )
    await state.set_state(Booking.feedback)
    await callback.answer()


@dp.message(Booking.feedback)
async def feedback_process(message: Message, state: FSMContext):

    if not message.text and not message.photo:
        return await message.answer(
            "Пожалуйста, пришлите ваш отзыв текстом или отправьте фото с описанием. 📝"
        )

    user_info = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else f"ID: {message.from_user.id}"
    )

    feedback_text = message.text or message.caption or "[Без текста]"

    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    cursor = await db_manager.conn.execute(
        """
        INSERT INTO feedbacks
        (
            user_id,
            username,
            feedback_text,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            message.from_user.id,
            message.from_user.username or "",
            feedback_text,
            now
        )
    )

    feedback_id = cursor.lastrowid
    await db_manager.conn.commit()

    admin_msg = (
        f"<b>💬 НОВЫЙ ОТЗЫВ #{feedback_id}</b>\n\n"
        f"👤 От кого: {message.from_user.full_name} ({user_info})\n"
        f"📝 Текст:\n{feedback_text}"
    )

    kb = InlineKeyboardBuilder()

    kb.row(
        InlineKeyboardButton(
            text="✉ Ответить",
            callback_data=f"reply_feedback:{feedback_id}"
        )
    )

    try:

        if message.photo:

            await bot.send_photo(
                SMM_ID,
                photo=message.photo[-1].file_id,
                caption=admin_msg,
                parse_mode="HTML",
                reply_markup=kb.as_markup()
            )

        else:

            await bot.send_message(
                SMM_ID,
                admin_msg,
                parse_mode="HTML",
                reply_markup=kb.as_markup()
            )

        await message.answer(
            "Спасибо за ваш отзыв! ❤️\n"
            "Мы получили его и обязательно ознакомимся.",
            reply_markup=main_menu()
        )

    except Exception as e:
        print(f"Ошибка при отправке отзыва SMM: {e}")

        await message.answer(
            "Произошла ошибка при отправке. Попробуйте позже."
        )

    await state.clear()

async def set_main_menu(bot: Bot):
    # 1. Общее меню для всех
    user_commands = [
        BotCommand(command="/start", description="Главное меню"),
        BotCommand(command="/help", description="Помощь"),
    ]
    await bot.set_my_commands(user_commands)

    # 2. Меню для ТЕКУЩЕГО администратора
    admin_commands = [
        BotCommand(command="/start", description="Главное меню"),
        BotCommand(command="/today", description="Брони на сегодня"),
        BotCommand(command="/stats", description="Статистика"),
        BotCommand(command="/admin_book", description="📞 Телефонная бронь"),
        BotCommand(command="/all_bookings", description="📋 Все брони"),
    ]

    try:
        # Устанавливаем меню текущему админу
        await bot.set_my_commands(
            commands=admin_commands,
            scope=BotCommandScopeChat(chat_id=ADMIN_ID)
        )
        print(f"=== Меню установлено для админа {ADMIN_ID} ===")
    except Exception as e:
        print(f"Ошибка установки меню: {e}")


# Функция для принудительной очистки (вызвать один раз, если нужно выгнать старого админа)
async def clear_old_admin_menu(bot: Bot, old_id: int):
    try:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=old_id))
        print(f"=== Меню старого админа {old_id} удалено ===")
    except Exception as e:
        print(f"Не удалось удалить меню: {e}")

@dp.message(Booking.admin_info)
async def admin_info_process(message: Message, state: FSMContext):
    data = await state.get_data()

    lines = (message.text or "").split("\n")

    name = lines[0] if len(lines) > 0 else ""
    phone = lines[1] if len(lines) > 1 else ""
    comment = "\n".join(lines[2:]) if len(lines) > 2 else ""

    query = """
        INSERT INTO bookings
        (user_id, date, time, guests, table_id, floor_id, name, phone, comment, wishes, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """

    params = (
        0,  # user_id = 0 (админская бронь)
        data["date"],
        data["time"],
        data["guests"],
        data["table"],
        data["floor"],
        name,
        phone,
        comment,
        "",
        "confirmed"   # сразу подтверждена
    )

    await db_manager.execute(query, params)

    await message.answer(
        "✅ <b>Телефонная бронь создана</b>",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    await state.clear()

# --- АДМИН-ПАНЕЛЬ ---

# Хэндлер для команды /today
@dp.message(F.from_user.id == ADMIN_ID, Command("today"))
async def admin_today_bookings(message: Message):
    today_str = datetime.now().strftime("%d.%m.%Y")

    rows = await db_manager.fetchall(
        """
        SELECT * FROM bookings
        WHERE date = ? AND status = 'confirmed'
        ORDER BY time ASC
        """,
        (today_str,)
    )

    if not rows:
        return await message.answer(
            f"📅 На сегодня ({today_str}) подтвержденных броней пока нет."
        )

    text = f"📅 <b>Брони на сегодня ({today_str}):</b>\n\n"

    for row in rows:
        floor_name = FLOORS.get(
            row['floor_id'],
            {}
        ).get('name', row['floor_id'] or "Не указан")

        # 📌 пометка типа брони
        if row['user_id'] == 0:
            source_tag = "📞 <i>Телефонная бронь</i>"
        else:
            source_tag = "💬 <i>Онлайн бронь</i>"

        text += (
            f"⏰ <b>{row['time']}</b> — {row['name']}\n"
            f"🪑 <b>Стол №{row['table_id']}</b> | 👥 {row['guests']} чел.\n"
            f"📍 {floor_name}\n"
            f"📞 <code>{row['phone']}</code>\n"
            f"{source_tag}\n"
            f"---------------------------\n"
        )

    await message.answer(text, parse_mode="HTML")
@dp.message(F.from_user.id == ADMIN_ID, Command("all_bookings"))
async def admin_all_bookings(message: Message):

    rows = await db_manager.fetchall(
        """
        SELECT *
        FROM bookings
        WHERE status IN ('pending', 'confirmed')
        ORDER BY
            substr(date,7,4),
            substr(date,4,2),
            substr(date,1,2),
            time
        """
    )

    if not rows:
        return await message.answer(
            "📭 Активных броней нет."
        )

    for row in rows:

        floor_name = FLOORS.get(
            row["floor_id"],
            {}
        ).get(
            "name",
            row["floor_id"]
        )

        status_icon = (
            "🟡" if row["status"] == "pending"
            else "🟢"
        )

        text = (
            f"{status_icon} <b>Бронь №{row['id']}</b>\n\n"
            f"📅 {row['date']} {row['time']}\n"
            f"🪑 Стол №{row['table_id']}\n"
            f"👥 {row['guests']} чел.\n"
            f"👤 {row['name']}\n"
            f"📞 <code>{row['phone']}</code>\n"
            f"📍 {floor_name}\n"
            f"💬 {row['comment'] or '-'}"
        )

        kb = InlineKeyboardBuilder()

        kb.row(
            InlineKeyboardButton(
                text="❌ Отменить бронь",
                callback_data=f"admin_del_{row['id']}"
            )
        )

        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )
# Хэндлер для команды /stats
@dp.message(F.from_user.id == ADMIN_ID, Command("stats"))
async def admin_statistics(message: Message):
    total_bookings = await db_manager.fetchone("SELECT COUNT(*) as count FROM bookings")

    # Чтобы не упасть, если таблица guests еще пуста (fetchone может вернуть None)
    total_users_row = await db_manager.fetchone("SELECT COUNT(*) as count FROM guests")
    total_users = total_users_row['count'] if total_users_row else 0

    text = (
        f"📊 <b>Базовая статистика:</b>\n\n"
        f"👥 Всего пользователей в базе: {total_users}\n"
        f"📅 Всего бронирований за всё время: {total_bookings['count']}\n"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(F.from_user.id == ADMIN_ID, Command("admin_book"))
async def admin_book_start(message: Message, state: FSMContext):
    now = datetime.now()

    await state.update_data(is_admin_booking=True)

    await message.answer(
        "📞 <b>Создание телефонной брони</b>\n\n"
        "Выберите дату:",
        reply_markup=get_calendar_kb(now.year, now.month),
        parse_mode="HTML"
    )

# --- ОБРАБОТКА ПОДТВЕРЖДЕНИЯ И ОТМЕНЫ ---

@dp.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("conf_"))
async def admin_confirm_booking(callback: CallbackQuery):
    booking_id = int(callback.data.split("_")[1])

    # Достаем данные брони
    row = await db_manager.fetchone("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    if not row:
        return await callback.answer("Ошибка: бронь не найдена!", show_alert=True)

    # Обновляем статус в БД
    await db_manager.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (booking_id,))

    # Теперь ставим напоминание в планировщик
    try:
        booking_dt = datetime.strptime(f"{row['date']} {row['time']}", "%d.%m.%Y %H:%M")
        reminder_time = booking_dt - timedelta(hours=2)
        if reminder_time > datetime.now():
            scheduler.add_job(
                send_reminder,
                trigger="date",
                run_date=reminder_time,
                args=[row['user_id'], row['date'], row['time']],
                id=f"rem_{booking_id}",
                replace_existing=True,
                misfire_grace_time=300
            )
    except Exception as e:
        print(f"Ошибка планировщика: {e}")

    # Уведомляем клиента
    try:
        await bot.send_message(
            row['user_id'],
            f"✅ Ваша бронь на <b>{row['date']} ({row['time']})</b> подтверждена! Ждем вас! ❤️",
            parse_mode="HTML"
        )
    except:
        pass

    await callback.message.edit_text(callback.message.text + "\n\n✅ <b>ПОДТВЕРЖДЕНО</b>", parse_mode="HTML")
    await callback.answer("Бронь подтверждена")


@dp.callback_query(F.from_user.id == ADMIN_ID, F.data.startswith("canc_"))
async def admin_cancel_booking(callback: CallbackQuery):
    booking_id = int(callback.data.split("_")[1])

    row = await db_manager.fetchone("SELECT * FROM bookings WHERE id = ?", (booking_id,))

    # Удаляем бронь из базы совсем
    await db_manager.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
    await db_manager.conn.commit()

    # Уведомляем клиента об отмене
    if row:
        try:
            await bot.send_message(
                row['user_id'],
                f"❌ К сожалению, мы не можем подтвердить вашу бронь на {row['date']} ({row['time']}).\n\n"
                f"С вами свяжется администратор для уточнения деталей.",
            )
        except:
            pass

    await callback.message.edit_text(callback.message.text + "\n\n❌ <b>ОТМЕНЕНО И УДАЛЕНО</b>", parse_mode="HTML")
    await callback.answer("Бронь удалена")

@dp.callback_query(
    F.from_user.id == ADMIN_ID,
    F.data.startswith("admin_del_")
)
async def admin_delete_booking(callback: CallbackQuery):

    booking_id = int(
        callback.data.replace(
            "admin_del_",
            ""
        )
    )

    row = await db_manager.fetchone(
        "SELECT * FROM bookings WHERE id = ?",
        (booking_id,)
    )

    if not row:
        return await callback.answer(
            "Бронь не найдена",
            show_alert=True
        )

    await db_manager.execute(
        """
        UPDATE bookings
        SET status = 'cancelled'
        WHERE id = ?
        """,
        (booking_id,)
    )

    try:
        scheduler.remove_job(
            f"rem_{booking_id}"
        )
    except:
        pass

    if row["user_id"] != 0:
        try:
            await bot.send_message(
                row["user_id"],
                (
                    f"❌ Ваша бронь на "
                    f"{row['date']} {row['time']} "
                    f"была отменена администратором."
                )
            )
        except:
            pass

    await callback.message.edit_text(
        callback.message.text +
        "\n\n❌ <b>БРОНЬ ОТМЕНЕНА</b>",
        parse_mode="HTML"
    )

    await callback.answer(
        "Бронь отменена"
    )

@dp.callback_query(
    F.from_user.id == SMM_ID,
    F.data.startswith("reply_feedback:")
)
async def reply_feedback_start(
    callback: CallbackQuery,
    state: FSMContext
):

    feedback_id = int(
        callback.data.split(":")[1]
    )

    await state.update_data(
        feedback_id=feedback_id
    )

    await callback.message.answer(
        "Введите ответ клиенту:"
    )

    await state.set_state(
        Booking.reply_feedback
    )

    await callback.answer()

@dp.message(
    Booking.reply_feedback
)
async def send_feedback_reply(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    feedback_id = data["feedback_id"]

    feedback = await db_manager.fetchone(
        """
        SELECT *
        FROM feedbacks
        WHERE id = ?
        """,
        (feedback_id,)
    )

    if not feedback:

        await message.answer(
            "❌ Отзыв не найден."
        )

        await state.clear()
        return

    try:

        await bot.send_message(
            feedback["user_id"],
            (
                "💬 <b>Ответ на ваш отзыв</b>\n\n"
                f"{message.text}"
            ),
            parse_mode="HTML"
        )

        await db_manager.execute(
            """
            UPDATE feedbacks
            SET status = 'answered'
            WHERE id = ?
            """,
            (feedback_id,)
        )

        await message.answer(
            "✅ Ответ успешно отправлен клиенту."
        )

    except Exception as e:

        print(e)

        await message.answer(
            "❌ Не удалось отправить ответ."
        )

    await state.clear()

# ---------------- RUN ----------------
async def main():
    try:
        # ЭТАП 1: Инициализация
        print("=== Подготовка к запуску ===")
        await db_manager.connect()
        # --- БЛОК ОДНОРАЗОВОЙ ОЧИСТКИ ---
        OLD_ADMIN_IDS = [1000460496, 736559077]  # Список всех старых ID, у кого висит панель
        for old_id in OLD_ADMIN_IDS:
            try:
                # Удаляем команды конкретно для этого чата
                await bot.delete_my_commands(scope=types.BotCommandScopeChat(chat_id=old_id))
                print(f"✅ Меню для {old_id} успешно удалено")
            except Exception as e:
                print(f"❌ Не удалось удалить меню для {old_id}: {e}")

        # Удаляем ВООБЩЕ ВСЕ глобальные команды бота (на всякий случай)
        await bot.delete_my_commands(scope=types.BotCommandScopeAllPrivateChats())
        # --- КОНЕЦ БЛОКА ---
        await init_db()
        await set_main_menu(bot)

        if not scheduler.running:
            scheduler.start()
            print("=== Планировщик запущен ===")



        # ЭТАП 3: Работа
        print("=== Бот Lampa в эфире! ===")
        await dp.start_polling(bot)

    except Exception as e:
        # Если что-то пойдет не так при запуске, мы увидим это в консоли сервера
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА: {e}")

    finally:
        # ЭТАП 4: Наведение порядка (Cleanup)
        # Этот блок сработает даже при ошибке выше
        print("=== Начинаю процесс выключения... ===")

        # Останавливаем планировщик
        if scheduler.running:
            scheduler.shutdown()
            print("1. Планировщик остановлен.")

        # Закрываем сессию связи с Telegram (чтобы не было Unclosed Connector)
        await bot.session.close()
        print("2. Сессия бота закрыта.")

        # Закрываем базу данных
        await db_manager.close()
        print("3. База данных отключена.")

        print("=== Бот полностью и безопасно завершил работу ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот выключен.")
