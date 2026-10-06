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
ADMIN_ID = [8607101615, 736559077]  # ID должен быть числом int(os.getenv("ADMIN_ID"))
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

    async def add_guest(
            self,
            user_id: int,
            username: str,
            source: str = "unknown"
    ):
        now = datetime.now().strftime("%d.%m.%Y %H:%M")

        query = """
            INSERT INTO guests (
                user_id,
                username,
                first_seen,
                source
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                source = CASE
                    WHEN guests.source IS NULL
                         OR guests.source = ''
                         OR guests.source = 'unknown'
                    THEN excluded.source
                    ELSE guests.source
                END
        """

        await self.execute(
            query,
            (
                user_id,
                username,
                now,
                source
            )
        )

    async def update_guest_phone(self, user_id: int, phone: str):
        query = "UPDATE guests SET phone = ? WHERE user_id = ?"
        await self.execute(query, (phone, user_id))


# Создаем объект БД
db_manager = Database(DB_NAME)

# ---------------- ANALYTICS FUNCTIONS ----------------

async def analytics_event(
    user_id: int,
    event_type: str,
    username: str = "",
    event_data: str = ""
):
    """
    Записывает любое действие пользователя.

    event_type:
        visit
        booking_start
        booking_date
        booking_time
        booking_floor
        booking_table
        booking_created
        booking_confirmed
        booking_cancelled
        feedback_sent
        menu
        map
        faq
        events
    """

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    await db_manager.execute(
        """
        INSERT INTO analytics_events
        (
            user_id,
            username,
            event_type,
            event_data,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            username or "",
            event_type,
            event_data or "",
            now
        )
    )


async def analytics_visit(user_id: int, username: str = ""):
    """
    Фиксирует посещение бота.

    Новым посещением считается запуск бота,
    если предыдущая сессия этого пользователя
    была больше 30 минут назад.

    Таким образом:

    /start
    /start через 5 секунд
    /start через 2 минуты

    = 1 посещение.

    А если человек вернулся через 40 минут:

    = новое посещение.
    """

    now = datetime.now()

    last_session = await db_manager.fetchone(
        """
        SELECT *
        FROM analytics_sessions
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,)
    )

    is_new_session = True

    if last_session:
        try:
            last_activity = datetime.strptime(
                last_session["last_activity"],
                "%Y-%m-%d %H:%M:%S"
            )

            # Сессия считается активной 30 минут.
            if now - last_activity < timedelta(minutes=30):
                is_new_session = False

        except Exception:
            pass

    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    if is_new_session:

        await db_manager.execute(
            """
            INSERT INTO analytics_sessions
            (
                user_id,
                username,
                started_at,
                last_activity
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                user_id,
                username or "",
                now_str,
                now_str
            )
        )

        await analytics_event(
            user_id=user_id,
            username=username,
            event_type="visit"
        )

    else:

        # Просто обновляем последнюю активность.
        await db_manager.execute(
            """
            UPDATE analytics_sessions
            SET
                username = ?,
                last_activity = ?
            WHERE id = ?
            """,
            (
                username or "",
                now_str,
                last_session["id"]
            )
        )


async def analytics_touch(
    user_id: int,
    username: str = ""
):
    """
    Обновляет последнюю активность пользователя
    в текущей сессии.

    Используется при действиях внутри бота.
    """

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    last_session = await db_manager.fetchone(
        """
        SELECT *
        FROM analytics_sessions
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,)
    )

    if last_session:
        await db_manager.execute(
            """
            UPDATE analytics_sessions
            SET
                username = ?,
                last_activity = ?
            WHERE id = ?
            """,
            (
                username or "",
                now_str,
                last_session["id"]
            )
        )


async def analytics_action(
    user_id: int,
    username: str,
    event_type: str,
    event_data: str = ""
):
    """
    Универсальная функция:
    обновляет активность + записывает событие.
    """

    await analytics_touch(
        user_id=user_id,
        username=username
    )

    await analytics_event(
        user_id=user_id,
        username=username,
        event_type=event_type,
        event_data=event_data
    )


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
    event_edit = State()

# ---------------- DB INIT ----------------
# ---------------- DB INIT ----------------
async def init_db():
    await db_manager.conn.execute('''
        CREATE TABLE IF NOT EXISTS guests (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            phone TEXT,
            first_seen TEXT,
            source TEXT DEFAULT 'unknown'
        )
    ''')

    # Добавляем source в существующую БД
    try:
        await db_manager.conn.execute(
            "ALTER TABLE guests ADD COLUMN source TEXT DEFAULT 'unknown'"
        )
    except Exception:
        pass

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

    # ---------------------------------------------------------
    # ЗАБЛОКИРОВАННЫЕ ДАТЫ
    # ---------------------------------------------------------
    # Здесь хранятся дни, когда обычное бронирование запрещено.
    #
    # Например:
    # 15.10.2026 -> закрыто на частное мероприятие
    # 20.10.2026 -> санитарный день
    #
    # date хранится в том же формате, что и bookings.date:
    # DD.MM.YYYY
    # ---------------------------------------------------------

    await db_manager.conn.execute("""
        CREATE TABLE IF NOT EXISTS blocked_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            reason TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            created_by INTEGER
        )
    """)

    # Индекс для быстрого поиска заблокированной даты
    await db_manager.conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_blocked_dates_date
        ON blocked_dates(date)
    """)

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

    await db_manager.conn.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY,
        text TEXT
    )
    """)

    # ---------------- ANALYTICS ----------------

    # Все события пользователей.
    # Здесь будем хранить:
    # visit
    # booking_start
    # booking_date
    # booking_time
    # booking_floor
    # booking_table
    # booking_created
    # booking_confirmed
    # booking_cancelled
    # feedback_sent
    #
    # Это позволит строить подробную статистику
    # и в дальнейшем добавлять новые метрики.

    await db_manager.conn.execute("""
    CREATE TABLE IF NOT EXISTS analytics_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        event_type TEXT NOT NULL,
        event_data TEXT,
        created_at TEXT NOT NULL
    )
    """)

    # Отдельные сессии посещения.
    # Нужны для того, чтобы человек, который нажал /start
    # несколько раз подряд, не считался новым посещением каждую секунду.

    await db_manager.conn.execute("""
    CREATE TABLE IF NOT EXISTS analytics_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        started_at TEXT NOT NULL,
        last_activity TEXT NOT NULL
    )
    """)

    # Индексы значительно ускорят статистику,
    # когда в базе накопятся тысячи событий.

    await db_manager.conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_analytics_events_user
    ON analytics_events(user_id)
    """)

    await db_manager.conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_analytics_events_type
    ON analytics_events(event_type)
    """)

    await db_manager.conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_analytics_events_date
    ON analytics_events(created_at)
    """)

    await db_manager.conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_analytics_sessions_user
    ON analytics_sessions(user_id)
    """)

    await db_manager.conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_analytics_sessions_date
    ON analytics_sessions(started_at)
    """)

    await db_manager.conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_analytics_events_user_type
        ON analytics_events(user_id, event_type)
    """)

    await db_manager.conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_guests_source
        ON guests(source)
    """)

    await db_manager.conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_bookings_user_status
        ON bookings(user_id, status)
    """)

    await db_manager.conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_feedbacks_status
        ON feedbacks(status)
    """)

    row = await db_manager.fetchone(
        "SELECT id FROM events LIMIT 1"
    )

    if not row:
        await db_manager.conn.execute(
            """
            INSERT INTO events
            (id, text)
            VALUES (?, ?)
            """,
            (
                1,
                EVENT_TEXT
            )
        )

    await db_manager.conn.commit()

# ============================================================
# БЛОКИРОВКА ДАТ
# ============================================================

async def is_date_blocked(date_str: str) -> bool:
    """
    Проверяет, заблокирована ли конкретная дата.

    date_str:
        формат DD.MM.YYYY

    Возвращает:
        True  -> дата заблокирована
        False -> дата доступна
    """

    row = await db_manager.fetchone(
        """
        SELECT id
        FROM blocked_dates
        WHERE date = ?
        LIMIT 1
        """,
        (date_str,)
    )

    return row is not None


async def get_blocked_dates() -> set:
    """
    Возвращает множество всех заблокированных дат.

    Например:

    {
        "15.10.2026",
        "20.10.2026",
        "31.12.2026"
    }

    Используется для отображения 🔒 в календаре.
    """

    rows = await db_manager.fetchall(
        """
        SELECT date
        FROM blocked_dates
        ORDER BY date
        """
    )

    return {
        row["date"]
        for row in rows
    }


async def block_date(
    date_str: str,
    reason: str = "",
    created_by: int = ADMIN_ID
) -> bool:
    """
    Блокирует дату.

    Возвращает:
        True  - дата была заблокирована
        False - дата уже была заблокирована
    """

    # Дополнительная защита:
    # не даём повторно добавлять одну и ту же дату.
    if await is_date_blocked(date_str):
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    await db_manager.execute(
        """
        INSERT INTO blocked_dates
        (
            date,
            reason,
            created_at,
            created_by
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            date_str,
            reason or "Закрыто администратором",
            now,
            created_by
        )
    )

    return True

async def unblock_date(date_str: str) -> bool:
    """
    Снимает блокировку с даты.

    Возвращает:
        True  - блокировка была снята
        False - дата не была заблокирована
    """

    if not await is_date_blocked(date_str):
        return False

    await db_manager.execute(
        """
        DELETE FROM blocked_dates
        WHERE date = ?
        """,
        (date_str,)
    )

    return True

async def get_active_bookings_count(date_str: str) -> int:
    """
    Возвращает количество активных броней
    на указанную дату.

    pending + confirmed.
    """

    row = await db_manager.fetchone(
        """
        SELECT COUNT(*) AS count
        FROM bookings
        WHERE date = ?
        AND status IN ('pending', 'confirmed')
        """,
        (date_str,)
    )

    return row["count"] if row else 0
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
# ---------------- KEYBOARDS ----------------

def main_menu(user_id=None):
    kb = InlineKeyboardBuilder()

    # ---------------------------------------------------------
    # ОСНОВНАЯ КНОПКА БРОНИРОВАНИЯ
    # ---------------------------------------------------------

    kb.row(
        InlineKeyboardButton(
            text="🍽 Забронировать стол",
            callback_data="book"
        )
    )

    # ---------------------------------------------------------
    # АДМИНСКАЯ КНОПКА
    # ---------------------------------------------------------
    # ВАЖНО:
    # Она добавляется только если Telegram ID пользователя
    # полностью совпадает с ADMIN_ID.
    # ---------------------------------------------------------

    if user_id == ADMIN_ID:

        kb.row(
            InlineKeyboardButton(
                text="🔒 Заблокировать бронирования",
                callback_data="admin_block_date_start"
            )
        )

    # ---------------------------------------------------------
    # ОСТАЛЬНЫЕ КНОПКИ
    # ---------------------------------------------------------

    kb.row(
        InlineKeyboardButton(
            text="🎉 Мероприятия",
            callback_data="events"
        )
    )

    kb.row(
        InlineKeyboardButton(
            text="📖 Меню",
            callback_data="menu"
        ),
        InlineKeyboardButton(
            text="📍 Найти нас",
            callback_data="map"
        )
    )

    kb.row(
        InlineKeyboardButton(
            text="❓ FAQ",
            callback_data="faq"
        ),
        InlineKeyboardButton(
            text="💬 Отзыв",
            callback_data="feedback"
        )
    )

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


def get_calendar_kb(year, month, blocked_dates=None):
    """
    Календарь выбора даты для клиента.

    blocked_dates:
        множество дат формата DD.MM.YYYY,
        которые нельзя бронировать.

    Заблокированные даты отображаются как 🔒.
    """

    kb = InlineKeyboardBuilder()

    today = datetime.now().date()

    months = [
        "Январь",
        "Февраль",
        "Март",
        "Апрель",
        "Май",
        "Июнь",
        "Июль",
        "Август",
        "Сентябрь",
        "Октябрь",
        "Ноябрь",
        "Декабрь"
    ]

    if blocked_dates is None:
        blocked_dates = set()

    # ---------------------------------------------------------
    # ШАПКА
    # ---------------------------------------------------------

    kb.row(
        InlineKeyboardButton(
            text=f"🗓 {months[month - 1]} {year}",
            callback_data="ignore"
        )
    )

    # ---------------------------------------------------------
    # ДНИ НЕДЕЛИ
    # ---------------------------------------------------------

    week_days = [
        "Пн",
        "Вт",
        "Ср",
        "Чт",
        "Пт",
        "Сб",
        "Вс"
    ]

    kb.row(
        *[
            InlineKeyboardButton(
                text=day,
                callback_data="ignore"
            )
            for day in week_days
        ]
    )

    # ---------------------------------------------------------
    # СЕТКА КАЛЕНДАРЯ
    # ---------------------------------------------------------

    for week in calendar.monthcalendar(year, month):

        buttons = []

        for day in week:

            # Пустая ячейка
            if day == 0:

                buttons.append(
                    InlineKeyboardButton(
                        text=" ",
                        callback_data="ignore"
                    )
                )

                continue

            date_obj = datetime(
                year,
                month,
                day
            ).date()

            date_str = (
                f"{day:02d}."
                f"{month:02d}."
                f"{year}"
            )

            # -------------------------------------------------
            # ПРОШЕДШАЯ ДАТА
            # -------------------------------------------------

            if date_obj < today:

                buttons.append(
                    InlineKeyboardButton(
                        text="❌",
                        callback_data="ignore"
                    )
                )

            # -------------------------------------------------
            # ЗАБЛОКИРОВАННАЯ ДАТА
            # -------------------------------------------------

            elif date_str in blocked_dates:

                buttons.append(
                    InlineKeyboardButton(
                        text=f"🔒 {day}",
                        callback_data="ignore"
                    )
                )

            # -------------------------------------------------
            # ОБЫЧНАЯ ДОСТУПНАЯ ДАТА
            # -------------------------------------------------

            else:

                buttons.append(
                    InlineKeyboardButton(
                        text=str(day),
                        callback_data=f"date:{date_str}"
                    )
                )

        kb.row(*buttons)

    # ---------------------------------------------------------
    # НАВИГАЦИЯ ПО МЕСЯЦАМ
    # ---------------------------------------------------------

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1

    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    now = datetime.now()

    # Можно листать назад только до текущего месяца
    if (
        year > now.year
        or (
            year == now.year
            and month > now.month
        )
    ):

        kb.row(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"prev_month:{prev_year}:{prev_month}"
            ),
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"next_month:{next_year}:{next_month}"
            )
        )

    else:

        kb.row(
            InlineKeyboardButton(
                text=" ",
                callback_data="ignore"
            ),
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"next_month:{next_year}:{next_month}"
            )
        )

    return kb.as_markup()

# ============================================================
# АДМИНСКИЙ КАЛЕНДАРЬ БЛОКИРОВКИ ДАТ
# ============================================================

def get_admin_block_calendar_kb(
    year: int,
    month: int,
    blocked_dates=None
):
    """
    Календарь администратора для управления блокировками.

    Свободная дата:
        обычная кнопка с числом

    Заблокированная дата:
        🔒 число

    Прошедшая дата:
        ❌

    В отличие от клиентского календаря,
    здесь даты используются для управления блокировками.
    """

    kb = InlineKeyboardBuilder()

    today = datetime.now().date()

    months = [
        "Январь",
        "Февраль",
        "Март",
        "Апрель",
        "Май",
        "Июнь",
        "Июль",
        "Август",
        "Сентябрь",
        "Октябрь",
        "Ноябрь",
        "Декабрь"
    ]

    if blocked_dates is None:
        blocked_dates = set()

    # ---------------------------------------------------------
    # ЗАГОЛОВОК
    # ---------------------------------------------------------

    kb.row(
        InlineKeyboardButton(
            text=f"🔒 {months[month - 1]} {year}",
            callback_data="ignore"
        )
    )

    # ---------------------------------------------------------
    # ДНИ НЕДЕЛИ
    # ---------------------------------------------------------

    week_days = [
        "Пн",
        "Вт",
        "Ср",
        "Чт",
        "Пт",
        "Сб",
        "Вс"
    ]

    kb.row(
        *[
            InlineKeyboardButton(
                text=day,
                callback_data="ignore"
            )
            for day in week_days
        ]
    )

    # ---------------------------------------------------------
    # ДНИ МЕСЯЦА
    # ---------------------------------------------------------

    for week in calendar.monthcalendar(year, month):

        buttons = []

        for day in week:

            # Пустая ячейка
            if day == 0:

                buttons.append(
                    InlineKeyboardButton(
                        text=" ",
                        callback_data="ignore"
                    )
                )

                continue

            date_obj = datetime(
                year,
                month,
                day
            ).date()

            date_str = (
                f"{day:02d}."
                f"{month:02d}."
                f"{year}"
            )

            # -------------------------------------------------
            # ПРОШЕДШАЯ ДАТА
            # -------------------------------------------------

            if date_obj < today:

                buttons.append(
                    InlineKeyboardButton(
                        text="❌",
                        callback_data="ignore"
                    )
                )

            # -------------------------------------------------
            # УЖЕ ЗАБЛОКИРОВАННАЯ ДАТА
            # -------------------------------------------------

            elif date_str in blocked_dates:

                buttons.append(
                    InlineKeyboardButton(
                        text=f"🔒 {day}",
                        callback_data=f"admin_block_date:{date_str}"
                    )
                )

            # -------------------------------------------------
            # СВОБОДНАЯ ДАТА
            # -------------------------------------------------

            else:

                buttons.append(
                    InlineKeyboardButton(
                        text=str(day),
                        callback_data=f"admin_block_date:{date_str}"
                    )
                )

        kb.row(*buttons)

    # ---------------------------------------------------------
    # НАВИГАЦИЯ
    # ---------------------------------------------------------

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1

    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    # Назад нельзя уходить дальше текущего месяца.
    if (
        year > today.year
        or (
            year == today.year
            and month > today.month
        )
    ):

        kb.row(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=(
                    f"admin_block_prev:"
                    f"{prev_year}:"
                    f"{prev_month}"
                )
            ),
            InlineKeyboardButton(
                text="➡️",
                callback_data=(
                    f"admin_block_next:"
                    f"{next_year}:"
                    f"{next_month}"
                )
            )
        )

    else:

        kb.row(
            InlineKeyboardButton(
                text=" ",
                callback_data="ignore"
            ),
            InlineKeyboardButton(
                text="➡️",
                callback_data=(
                    f"admin_block_next:"
                    f"{next_year}:"
                    f"{next_month}"
                )
            )
        )

    # ---------------------------------------------------------
    # КНОПКА ВЫХОДА
    # ---------------------------------------------------------

    kb.row(
        InlineKeyboardButton(
            text="⬅️ В главное меню",
            callback_data="admin_block_back"
        )
    )

    return kb.as_markup()


# ============================================================
# АДМИН: ОТКРЫТИЕ КАЛЕНДАРЯ БЛОКИРОВКИ
# ============================================================

@dp.callback_query(
    F.from_user.id == ADMIN_ID,
    F.data == "admin_block_date_start"
)
async def admin_block_date_start(
    callback: CallbackQuery
):
    """
    Открывает календарь управления блокировками.

    Обработчик доступен только ADMIN_ID.
    """

    now = datetime.now()

    blocked_dates = await get_blocked_dates()

    await callback.message.edit_text(
        "🔒 <b>Управление бронированиями</b>\n\n"
        "Выберите дату.\n\n"
        "🟢 обычная дата — можно заблокировать\n"
        "🔒 заблокированная — можно снять блокировку",
        parse_mode="HTML",
        reply_markup=get_admin_block_calendar_kb(
            now.year,
            now.month,
            blocked_dates
        )
    )

    await callback.answer()

    # ============================================================
    # АДМИН: ПЕРЕКЛЮЧЕНИЕ МЕСЯЦА
    # ============================================================

    @dp.callback_query(
        F.from_user.id == ADMIN_ID,
        F.data.startswith("admin_block_prev:")
        | F.data.startswith("admin_block_next:")
    )
    async def admin_block_change_month(
            callback: CallbackQuery
    ):
        """
        Переключает месяц в админском календаре.
        """

        try:

            _, year, month = callback.data.split(":")

            year = int(year)
            month = int(month)

        except (ValueError, TypeError):

            await callback.answer(
                "❌ Ошибка календаря.",
                show_alert=True
            )

            return

        blocked_dates = await get_blocked_dates()

        await callback.message.edit_reply_markup(
            reply_markup=get_admin_block_calendar_kb(
                year,
                month,
                blocked_dates
            )
        )

        await callback.answer()

        # ============================================================
        # АДМИН: ВЫБОР ДАТЫ
        # ============================================================

        @dp.callback_query(
            F.from_user.id == ADMIN_ID,
            F.data.startswith("admin_block_date:")
        )
        async def admin_block_date_select(
                callback: CallbackQuery
        ):
            """
            Обработка выбора конкретной даты администратором.
            """

            date_str = callback.data.split(":", 1)[1]

            # ---------------------------------------------------------
            # Проверяем формат даты
            # ---------------------------------------------------------

            try:

                selected_date = datetime.strptime(
                    date_str,
                    "%d.%m.%Y"
                ).date()

            except ValueError:

                await callback.answer(
                    "❌ Некорректная дата.",
                    show_alert=True
                )

                return

            # ---------------------------------------------------------
            # Прошедшую дату блокировать нельзя
            # ---------------------------------------------------------

            if selected_date < datetime.now().date():
                await callback.answer(
                    "❌ Нельзя изменить прошедшую дату.",
                    show_alert=True
                )

                return

            # ---------------------------------------------------------
            # Проверяем текущий статус даты
            # ---------------------------------------------------------

            blocked = await is_date_blocked(date_str)

            # =========================================================
            # ЕСЛИ ДАТА УЖЕ ЗАБЛОКИРОВАНА
            # =========================================================

            if blocked:
                kb = InlineKeyboardBuilder()

                kb.row(
                    InlineKeyboardButton(
                        text="🔓 Снять блокировку",
                        callback_data=(
                            f"admin_unblock_confirm:{date_str}"
                        )
                    )
                )

                kb.row(
                    InlineKeyboardButton(
                        text="⬅️ Назад к календарю",
                        callback_data="admin_block_date_start"
                    )
                )

                await callback.message.edit_text(
                    f"🔒 <b>{date_str}</b>\n\n"
                    "Бронирования на эту дату уже закрыты.\n\n"
                    "Хотите снять блокировку?",
                    parse_mode="HTML",
                    reply_markup=kb.as_markup()
                )

                await callback.answer()

                return

            # =========================================================
            # ЕСЛИ ДАТА СВОБОДНА
            # =========================================================

            active_bookings = await get_active_bookings_count(
                date_str
            )

            kb = InlineKeyboardBuilder()

            kb.row(
                InlineKeyboardButton(
                    text="✅ Заблокировать",
                    callback_data=(
                        f"admin_block_confirm:{date_str}"
                    )
                )
            )

            kb.row(
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="admin_block_date_start"
                )
            )

            if active_bookings > 0:

                warning_text = (
                    f"⚠️ <b>Внимание!</b>\n\n"
                    f"На {date_str} уже есть "
                    f"<b>{active_bookings}</b> активных броней.\n\n"
                    "Блокировка даты <b>не отменит</b> "
                    "существующие бронирования.\n"
                    "Она только запретит создавать новые "
                    "онлайн-брони.\n\n"
                )

            else:

                warning_text = (
                    f"На <b>{date_str}</b> активных броней нет.\n\n"
                )

            await callback.message.edit_text(
                warning_text +
                "🔒 <b>Заблокировать бронирования "
                f"на эту дату?</b>",
                parse_mode="HTML",
                reply_markup=kb.as_markup()
            )

            await callback.answer()

# ============================================================
# АДМИН: ПОДТВЕРЖДЕНИЕ БЛОКИРОВКИ
# ============================================================

@dp.callback_query(
    F.from_user.id == ADMIN_ID,
    F.data.startswith("admin_block_confirm:")
)
async def admin_block_confirm(
    callback: CallbackQuery
):
    """
    Фактически добавляет дату в blocked_dates.
    """

    date_str = callback.data.split(":", 1)[1]

    # ---------------------------------------------------------
    # Повторная проверка
    # ---------------------------------------------------------

    try:

        selected_date = datetime.strptime(
            date_str,
            "%d.%m.%Y"
        ).date()

    except ValueError:

        await callback.answer(
            "❌ Некорректная дата.",
            show_alert=True
        )

        return

    if selected_date < datetime.now().date():

        await callback.answer(
            "❌ Нельзя блокировать прошедшую дату.",
            show_alert=True
        )

        return

    # ---------------------------------------------------------
    # Проверяем, не заблокировал ли её уже кто-то
    # ---------------------------------------------------------

    if await is_date_blocked(date_str):

        await callback.answer(
            "Эта дата уже заблокирована.",
            show_alert=True
        )

        return

    # ---------------------------------------------------------
    # Блокируем
    # ---------------------------------------------------------

    success = await block_date(
        date_str=date_str,
        reason="Закрыто администратором",
        created_by=callback.from_user.id
    )

    if not success:

        await callback.answer(
            "❌ Не удалось заблокировать дату.",
            show_alert=True
        )

        return

    # ---------------------------------------------------------
    # Возвращаем результат
    # ---------------------------------------------------------

    kb = InlineKeyboardBuilder()

    kb.row(
        InlineKeyboardButton(
            text="🔒 Управление датами",
            callback_data="admin_block_date_start"
        )
    )

    kb.row(
        InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data="admin_block_back"
        )
    )

    await callback.message.edit_text(
        f"✅ <b>Бронирования на {date_str} заблокированы.</b>\n\n"
        "Гости больше не смогут создать новую "
        "онлайн-бронь на эту дату.\n\n"
        "⚠️ Уже существующие бронирования "
        "автоматически не отменяются.",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )

    await callback.answer(
        "Дата заблокирована."
    )

# ============================================================
# АДМИН: ПОДТВЕРЖДЕНИЕ РАЗБЛОКИРОВКИ
# ============================================================

@dp.callback_query(
    F.from_user.id == ADMIN_ID,
    F.data.startswith("admin_unblock_confirm:")
)
async def admin_unblock_confirm(
    callback: CallbackQuery
):
    """
    Снимает блокировку с даты.
    """

    date_str = callback.data.split(":", 1)[1]

    # ---------------------------------------------------------
    # Проверяем, действительно ли дата заблокирована
    # ---------------------------------------------------------

    if not await is_date_blocked(date_str):

        await callback.answer(
            "Эта дата уже разблокирована.",
            show_alert=True
        )

        return

    # ---------------------------------------------------------
    # Снимаем блокировку
    # ---------------------------------------------------------

    success = await unblock_date(
        date_str
    )

    if not success:

        await callback.answer(
            "❌ Не удалось снять блокировку.",
            show_alert=True
        )

        return

    # ---------------------------------------------------------
    # Возвращаемся к календарю
    # ---------------------------------------------------------

    now = datetime.now()

    blocked_dates = await get_blocked_dates()

    await callback.message.edit_text(
        f"✅ <b>Блокировка {date_str} снята.</b>\n\n"
        "Гости снова смогут бронировать столы "
        "на эту дату.",
        parse_mode="HTML",
        reply_markup=get_admin_block_calendar_kb(
            now.year,
            now.month,
            blocked_dates
        )
    )

    await callback.answer(
        "Блокировка снята."
    )

    # ============================================================
    # АДМИН: ВОЗВРАТ В ГЛАВНОЕ МЕНЮ
    # ============================================================

    @dp.callback_query(
        F.from_user.id == ADMIN_ID,
        F.data == "admin_block_back"
    )
    async def admin_block_back(
            callback: CallbackQuery
    ):
        """
        Возвращает администратора в главное меню.
        """

        await callback.message.edit_text(
            f"Здравствуйте, "
            f"<b>{callback.from_user.first_name}</b>! ✨\n\n"
            "Рады приветствовать вас в Лампе. "
            "С чего начнем?",
            parse_mode="HTML",
            reply_markup=main_menu(
                callback.from_user.id
            )
        )

        await callback.answer()
# ---------------- HANDLERS ----------------


@dp.message(CommandStart())
async def start(message: Message):

    username = message.from_user.username or "NoUsername"

    # -----------------------------------------
    # ИСТОЧНИК ПЕРЕХОДА
    # -----------------------------------------

    source = "unknown"

    if message.text:
        parts = message.text.split(maxsplit=1)

        if len(parts) > 1:
            source = parts[1].strip().lower()

    # Разрешённые источники
    allowed_sources = {
        "instagram",
        "tiktok",
        "threads",
        "telegram",
        "google",
        "yandex",
        "blogger",
        "qr",
        "friends",
        "street",
        "advertising",
        "other"
    }

    if source not in allowed_sources:
        source = "unknown"

    # -----------------------------------------
    # Сохраняем пользователя
    # -----------------------------------------

    await db_manager.add_guest(
        user_id=message.from_user.id,
        username=username,
        source=source
    )

    # -----------------------------------------
    # Фиксируем посещение
    # -----------------------------------------

    await analytics_visit(
        user_id=message.from_user.id,
        username=username
    )

    # -----------------------------------------
    # Фиксируем источник отдельным событием
    # -----------------------------------------

    await analytics_action(
        user_id=message.from_user.id,
        username=username,
        event_type="source_detected",
        event_data=source
    )

    await message.answer(
        f"Здравствуйте, <b>{message.from_user.first_name}</b>! 💫\n\n"
        "Рады приветствовать вас в Лампе. Я помогу вам забронировать столик "
        "и отвечу на вопросы. С чего начнем?😉",
        reply_markup=main_menu(message.from_user.id),
        parse_mode="HTML"
    )


# --- Инструкция по использованию ---
@dp.message(Command("help"))
async def help_command(message: Message):
    await analytics_action(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        event_type="help"
    )
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
@dp.callback_query(F.data == "menu")
async def show_menu(callback: CallbackQuery):

    await analytics_action(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        event_type="menu"
    )

    kb = InlineKeyboardBuilder()

    kb.row(
        InlineKeyboardButton(
            text="📖 Открыть меню",
            url=MENU_URL
        )
    )

    kb.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="back_to_main"
        )
    )

    await callback.message.answer(
        "📖 <b>Наше меню</b>\n\n"
        "Нажмите кнопку ниже, чтобы открыть меню.",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )

    await callback.answer()
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

    await analytics_action(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        event_type="booking_start"
    )

    now = datetime.now()

    # Получаем заблокированные даты
    blocked_dates = await get_blocked_dates()

    await callback.message.edit_text(
        "Пожалуйста, выберите <b>дату визита</b> в календаре:\n\n"
        "🔒 — бронирование на эту дату закрыто",
        reply_markup=get_calendar_kb(
            now.year,
            now.month,
            blocked_dates
        ),
        parse_mode="HTML"
    )

    await callback.answer()

@dp.callback_query(
    F.data.startswith("prev_month:")
    | F.data.startswith("next_month:")
)
async def change_month(callback: CallbackQuery):

    _, year, month = callback.data.split(":")

    year = int(year)
    month = int(month)

    # Получаем актуальный список блокировок
    blocked_dates = await get_blocked_dates()

    await callback.message.edit_reply_markup(
        reply_markup=get_calendar_kb(
            year,
            month,
            blocked_dates
        )
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("date:"))
async def set_date(
    callback: CallbackQuery,
    state: FSMContext
):

    date_str = callback.data.split(":", 1)[1]

    # ========================================================
    # КРИТИЧЕСКАЯ ПРОВЕРКА
    # ========================================================
    # Администратор мог заблокировать дату уже после того,
    # как пользователь открыл календарь.
    # Поэтому проверяем БД непосредственно здесь.
    # ========================================================

    if await is_date_blocked(date_str):

        return await callback.answer(
            "🔒 Бронирование на эту дату закрыто.\n"
            "Пожалуйста, выберите другой день.",
            show_alert=True
        )

    await analytics_action(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        event_type="booking_date",
        event_data=date_str
    )

    await state.update_data(
        date=date_str
    )

    kb = await get_time_kb(date_str)

    await callback.message.edit_text(
        f"Вы выбрали дату: <b>{date_str}</b>\n\n"
        "Теперь подберем удобное время:",
        reply_markup=kb,
        parse_mode="HTML"
    )

    await callback.answer()

@dp.callback_query(F.data.startswith("time:"))
async def set_time(callback: CallbackQuery, state: FSMContext):

    time = callback.data.split(":", 1)[1]

    await analytics_action(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        event_type="booking_time",
        event_data=time
    )

    data = await state.get_data()
    date_str = data.get('date')
    # ---------------------------------------------------------
    # Проверяем, не была ли дата заблокирована уже после
    # выбора пользователем даты.
    # ---------------------------------------------------------

    if date_str and await is_date_blocked(date_str):

        await state.clear()

        return await callback.answer(
            "🔒 Администратор только что закрыл бронирование "
            "на эту дату.\n\n"
            "Пожалуйста, выберите другой день.",
            show_alert=True
        )

    if date_str and date_str.startswith("19.07.") and time > "20:00":
        return await callback.answer("Извините, 19 июля бронирование столов на время после 20:00 осуществляется исключительно по телефону. Ждем вашего звонка! 📞", show_alert=True)

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

    await analytics_action(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        event_type="booking_floor",
        event_data=floor_id
    )
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

    await analytics_action(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        event_type="booking_table",
        event_data=table_id
    )

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
        # -----------------------------------------------------
        # ФИНАЛЬНАЯ ПРОВЕРКА БЛОКИРОВКИ ДАТЫ
        # -----------------------------------------------------

        if await is_date_blocked(data["date"]):

            await message.answer(
                "🔒 К сожалению, бронирование на выбранную дату "
                "только что было закрыто администратором.\n\n"
                "Пожалуйста, выберите другой день.",
                reply_markup=main_menu(message.from_user.id)
            )

            await state.clear()
            return
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

    await analytics_action(
        user_id=user_id,
        username=message.from_user.username or "",
        event_type="booking_created",
        event_data=str(booking_id)
    )
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
    await analytics_action(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        event_type="faq"
    )
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
    await analytics_action(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        event_type="events"
    )
    event = await db_manager.fetchone(
        """
        SELECT *
        FROM events
        WHERE id = 1
        """
    )

    event_text = (
        event["text"]
        if event
        else EVENT_TEXT
    )

    kb = InlineKeyboardBuilder()

    kb.row(
        InlineKeyboardButton(
            text="⬅️ Назад в меню",
            callback_data="back_to_main"
        )
    )

    try:

        await callback.message.answer_video(
            caption=event_text,
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )

    except Exception as e:

        print(f"Ошибка отправки мероприятия: {e}")

        await callback.message.answer(
            event_text,
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
        reply_markup=main_menu(callback.from_user.id),
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

    await analytics_action(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        event_type="feedback_sent",
        event_data=str(feedback_id)
    )

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
            reply_markup=main_menu(message.from_user.id)
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
        BotCommand(command="/last_booking", description="🔍 Последняя бронь"),
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

    smm_commands = [
        BotCommand(
            command="/start",
            description="Главное меню"
        ),
        BotCommand(
            command="/help",
            description="Помощь"
        ),
        BotCommand(
            command="/smm_stats",
            description="📊 Статистика"
        ),
        BotCommand(
            command="/edit_event",
            description="🎉 Изменить мероприятие"
        )
    ]

    await bot.set_my_commands(
        commands=smm_commands,
        scope=BotCommandScopeChat(
            chat_id=SMM_ID
        )
    )

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
        reply_markup=main_menu(message.from_user.id)
    )

    await state.clear()

# --- АДМИН-ПАНЕЛЬ ---

# ---------------- SMM ANALYTICS ----------------

# ============================================================
# 24. SMM ANALYTICS
# ============================================================

async def count_unique_event_users(
    event_type: str,
    days=None
):
    """
    Возвращает количество уникальных пользователей,
    совершивших указанное событие за выбранный период.

    Параметры:
        event_type:
            тип события из таблицы analytics_events.

        days:
            None -> весь период
            1    -> сегодня
            7    -> последние 7 дней
            30   -> последние 30 дней

    ВАЖНО:
        Здесь считается именно количество уникальных user_id,
        а не количество записей события.

        Например:

        Пользователь 123:
            booking_start
            booking_start
            booking_start

        В событиях будет 3 записи,
        но уникальный пользователь только 1.

        Поэтому функция вернет:
            1
    """

    if days is None:
        date_filter = ""
        params = (event_type,)
    else:
        start_date = (
            datetime.now() - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d 00:00:00")

        date_filter = """
            AND created_at >= ?
        """

        params = (
            event_type,
            start_date
        )

    row = await db_manager.fetchone(
        f"""
        SELECT COUNT(DISTINCT user_id) AS count
        FROM analytics_events
        WHERE event_type = ?
          AND user_id IS NOT NULL
          AND user_id != 0
          {date_filter}
        """,
        params
    )

    if row:
        return row["count"] or 0

    return 0


async def count_event(
    event_type: str,
    days=None
):
    """
    Возвращает общее количество событий.

    В отличие от count_unique_event_users()
    здесь один пользователь может учитываться
    несколько раз.

    Например:

        user 123 -> booking_start
        user 123 -> booking_start
        user 123 -> booking_start

    Результат:
        3

    Эта функция нужна для понимания общего
    количества действий пользователей.
    """

    if days is None:
        date_filter = ""
        params = (event_type,)
    else:
        start_date = (
            datetime.now() - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d 00:00:00")

        date_filter = """
            AND created_at >= ?
        """

        params = (
            event_type,
            start_date
        )

    row = await db_manager.fetchone(
        f"""
        SELECT COUNT(*) AS count
        FROM analytics_events
        WHERE event_type = ?
          AND user_id IS NOT NULL
          AND user_id != 0
          {date_filter}
        """,
        params
    )

    if row:
        return row["count"] or 0

    return 0


async def count_booking_events(event_type: str, days=None):
    """
    Считает уникальные ID бронирований,
    а не уникальных пользователей.
    """

    if days is None:

        date_filter = ""
        params = (event_type,)

    else:

        start_date = (
            datetime.now()
            - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d 00:00:00")

        date_filter = "AND created_at >= ?"
        params = (
            event_type,
            start_date
        )

    row = await db_manager.fetchone(
        f"""
        SELECT COUNT(DISTINCT event_data) AS count
        FROM analytics_events
        WHERE event_type = ?
          AND user_id IS NOT NULL
          AND user_id != 0
          AND event_data IS NOT NULL
          AND event_data != ''
          {date_filter}
        """,
        params
    )

    return row["count"] if row else 0

async def get_source_stats(days=None):

    if days is None:

        date_filter = ""
        params = ()

    else:

        start_date = (
            datetime.now()
            - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d 00:00:00")

        date_filter = "AND created_at >= ?"
        params = (start_date,)

    rows = await db_manager.fetchall(
        f"""
        SELECT
            event_data AS source,
            COUNT(DISTINCT user_id) AS users
        FROM analytics_events
        WHERE event_type = 'source_detected'
          AND event_data IS NOT NULL
          AND event_data != ''
          AND event_data != 'unknown'
          AND user_id IS NOT NULL
          AND user_id != 0
          {date_filter}
        GROUP BY event_data
        ORDER BY users DESC
        """,
        params
    )

    return rows

async def get_smm_stats(days=None):
    """
    Основная статистика SMM.

    days:
        None -> всё время
        1    -> сегодня
        7    -> последние 7 дней
        30   -> последние 30 дней

    В статистике одновременно храним:

        1. Общее количество событий.
        2. Количество уникальных пользователей.

    Это важно для правильного расчёта конверсии.
    """

    # --------------------------------------------------------
    # ПЕРИОД
    # --------------------------------------------------------

    if days is None:
        date_filter = ""
        params = ()
    else:
        start_date = (
            datetime.now() - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d 00:00:00")

        date_filter = """
            AND created_at >= ?
        """

        params = (start_date,)

    # --------------------------------------------------------
    # ПОСЕЩЕНИЯ
    # --------------------------------------------------------

    visits_row = await db_manager.fetchone(
        f"""
        SELECT COUNT(*) AS count
        FROM analytics_events
        WHERE event_type = 'visit'
          AND user_id IS NOT NULL
          AND user_id != 0
          {date_filter}
        """,
        params
    )

    visits = (
        visits_row["count"]
        if visits_row
        else 0
    )

    # --------------------------------------------------------
    # УНИКАЛЬНЫЕ ПОСЕТИТЕЛИ
    # --------------------------------------------------------

    unique_visitors_row = await db_manager.fetchone(
        f"""
        SELECT COUNT(DISTINCT user_id) AS count
        FROM analytics_events
        WHERE event_type = 'visit'
          AND user_id IS NOT NULL
          AND user_id != 0
          {date_filter}
        """,
        params
    )

    unique_visitors = (
        unique_visitors_row["count"]
        if unique_visitors_row
        else 0
    )

    # --------------------------------------------------------
    # НОВЫЕ ПОЛЬЗОВАТЕЛИ
    # --------------------------------------------------------
    #
    # В твоей текущей БД first_seen хранится в формате:
    # DD.MM.YYYY HH:MM
    #
    # Поэтому преобразуем его в YYYY-MM-DD
    # непосредственно внутри SQL.
    #
    # Это именно новые пользователи бота,
    # а не новые уникальные посетители.
    # --------------------------------------------------------

    if days is None:

        new_users_row = await db_manager.fetchone(
            """
            SELECT COUNT(*) AS count
            FROM guests
            """
        )

    else:

        new_users_row = await db_manager.fetchone(
            """
            SELECT COUNT(*) AS count
            FROM guests
            WHERE substr(first_seen, 7, 4) || '-' ||
                  substr(first_seen, 4, 2) || '-' ||
                  substr(first_seen, 1, 2) >= ?
            """,
            (
                (
                    datetime.now()
                    - timedelta(days=days - 1)
                ).strftime("%Y-%m-%d"),
            )
        )

    new_users = (
        new_users_row["count"]
        if new_users_row
        else 0
    )

    source_rows = await get_source_stats(days)

    source_stats = []

    for row in source_rows:
        source_stats.append({
            "source": row["source"],
            "users": row["users"] or 0
        })
    # --------------------------------------------------------
    # ОБЩЕЕ КОЛИЧЕСТВО ДЕЙСТВИЙ
    # --------------------------------------------------------

    booking_start = await count_event(
        "booking_start",
        days
    )

    booking_date = await count_event(
        "booking_date",
        days
    )

    booking_time = await count_event(
        "booking_time",
        days
    )

    booking_floor = await count_event(
        "booking_floor",
        days
    )

    booking_table = await count_event(
        "booking_table",
        days
    )

    booking_created = await count_booking_events(
        "booking_created",
        days
    )

    booking_confirmed = await count_booking_events(
        "booking_confirmed",
        days
    )

    booking_cancelled = await count_booking_events(
        "booking_cancelled",
        days
    )

    feedback_sent = await count_event(
        "feedback_sent",
        days
    )

    # --------------------------------------------------------
    # УНИКАЛЬНЫЕ ПОЛЬЗОВАТЕЛИ НА КАЖДОМ ШАГЕ
    # --------------------------------------------------------
    #
    # Это главная часть новой аналитики.
    #
    # Раньше:
    #
    #   booking_start / unique_visitors
    #
    # могло дать больше 100%,
    # если один человек нажал "Забронировать"
    # несколько раз.
    #
    # Теперь:
    #
    #   уникальные пользователи, начавшие бронь
    #   ----------------------------------------
    #   уникальные посетители
    #
    # --------------------------------------------------------

    unique_booking_start = await count_unique_event_users(
        "booking_start",
        days
    )

    unique_booking_date = await count_unique_event_users(
        "booking_date",
        days
    )

    unique_booking_time = await count_unique_event_users(
        "booking_time",
        days
    )

    unique_booking_floor = await count_unique_event_users(
        "booking_floor",
        days
    )

    unique_booking_table = await count_unique_event_users(
        "booking_table",
        days
    )

    unique_booking_created = await count_unique_event_users(
        "booking_created",
        days
    )

    unique_booking_confirmed = await count_unique_event_users(
        "booking_confirmed",
        days
    )

    unique_booking_cancelled = await count_unique_event_users(
        "booking_cancelled",
        days
    )

    unique_feedback_sent = await count_unique_event_users(
        "feedback_sent",
        days
    )

    # --------------------------------------------------------
    # СРЕДНЕЕ КОЛИЧЕСТВО ПОСЕЩЕНИЙ
    # НА ОДНОГО УНИКАЛЬНОГО ПОСЕТИТЕЛЯ
    # --------------------------------------------------------

    if unique_visitors:
        visits_per_user = round(
            visits / unique_visitors,
            2
        )
    else:
        visits_per_user = 0

    # --------------------------------------------------------
    # КОНВЕРСИЯ:
    # ПОСЕТИТЕЛЬ -> НАЧАЛ БРОНИРОВАНИЕ
    # --------------------------------------------------------

    if unique_visitors:
        booking_start_conversion = round(
            unique_booking_start
            / unique_visitors
            * 100,
            1
        )
    else:
        booking_start_conversion = 0

    # --------------------------------------------------------
    # КОНВЕРСИЯ:
    # НАЧАЛ БРОНИРОВАНИЕ -> ОТПРАВИЛ ЗАЯВКУ
    # --------------------------------------------------------

    if unique_booking_start:
        booking_complete_conversion = round(
            unique_booking_created
            / unique_booking_start
            * 100,
            1
        )
    else:
        booking_complete_conversion = 0

    # --------------------------------------------------------
    # КОНВЕРСИЯ:
    # ПОСЕТИТЕЛЬ -> ОТПРАВИЛ ЗАЯВКУ
    # --------------------------------------------------------

    if unique_visitors:
        booking_conversion = round(
            unique_booking_created
            / unique_visitors
            * 100,
            1
        )
    else:
        booking_conversion = 0

    # --------------------------------------------------------
    # КОНВЕРСИЯ:
    # ОТПРАВИЛ ЗАЯВКУ -> ПОДТВЕРЖДЕНО
    # --------------------------------------------------------

    if unique_booking_created:
        booking_confirmation_conversion = round(
            unique_booking_confirmed
            / unique_booking_created
            * 100,
            1
        )
    else:
        booking_confirmation_conversion = 0

    # --------------------------------------------------------
    # ПОЛНАЯ КОНВЕРСИЯ:
    # ПОСЕТИТЕЛЬ -> ПОДТВЕРЖДЁННАЯ БРОНЬ
    # --------------------------------------------------------

    if unique_visitors:
        visitor_to_confirmed_conversion = round(
            unique_booking_confirmed
            / unique_visitors
            * 100,
            1
        )
    else:
        visitor_to_confirmed_conversion = 0

    # --------------------------------------------------------
    # ВОЗВРАЩАЕМ ВСЮ СТАТИСТИКУ
    # --------------------------------------------------------

    return {

        # Посещаемость
        "visits": visits,
        "unique_visitors": unique_visitors,
        "new_users": new_users,
        "visits_per_user": visits_per_user,
        "source_stats": source_stats,

        # Общее количество действий
        "booking_start": booking_start,
        "booking_date": booking_date,
        "booking_time": booking_time,
        "booking_floor": booking_floor,
        "booking_table": booking_table,
        "booking_created": booking_created,
        "booking_confirmed": booking_confirmed,
        "booking_cancelled": booking_cancelled,
        "feedback_sent": feedback_sent,

        # Уникальные пользователи
        "unique_booking_start": unique_booking_start,
        "unique_booking_date": unique_booking_date,
        "unique_booking_time": unique_booking_time,
        "unique_booking_floor": unique_booking_floor,
        "unique_booking_table": unique_booking_table,
        "unique_booking_created": unique_booking_created,
        "unique_booking_confirmed": unique_booking_confirmed,
        "unique_booking_cancelled": unique_booking_cancelled,
        "unique_feedback_sent": unique_feedback_sent,

        # Конверсии
        "booking_start_conversion":
            booking_start_conversion,

        "booking_complete_conversion":
            booking_complete_conversion,

        "booking_conversion":
            booking_conversion,

        "booking_confirmation_conversion":
            booking_confirmation_conversion,

        "visitor_to_confirmed_conversion":
            visitor_to_confirmed_conversion,
    }


def smm_stats_keyboard(days=1):
    """
    Клавиатура основной SMM-аналитики.

    days:
        1    -> сегодня
        7    -> последние 7 дней
        30   -> последние 30 дней
        None -> всё время

    Передаём выбранный период в кнопку
    "Пользователи", чтобы список пользователей
    соответствовал тому же периоду.
    """

    kb = InlineKeyboardBuilder()

    # --------------------------------------------------------
    # ПЕРИОДЫ
    # --------------------------------------------------------

    kb.row(
        InlineKeyboardButton(
            text="📅 Сегодня",
            callback_data="smm_stats:1"
        ),
        InlineKeyboardButton(
            text="📆 7 дней",
            callback_data="smm_stats:7"
        )
    )

    kb.row(
        InlineKeyboardButton(
            text="🗓 30 дней",
            callback_data="smm_stats:30"
        ),
        InlineKeyboardButton(
            text="♾ Всё время",
            callback_data="smm_stats:all"
        )
    )

    # --------------------------------------------------------
    # ПОЛЬЗОВАТЕЛИ
    # --------------------------------------------------------

    if days is None:
        users_period = "all"
    else:
        users_period = str(days)

    kb.row(
        InlineKeyboardButton(
            text="👤 Пользователи",
            callback_data=f"smm_users:{users_period}"
        )
    )

    # --------------------------------------------------------
    # ОБНОВЛЕНИЕ
    # --------------------------------------------------------

    kb.row(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=f"smm_stats:refresh:{users_period}"
        )
    )

    return kb.as_markup()


async def build_smm_stats_text(days=1):
    """
    Полностью формирует SMM-статистику бота.

    days:
        1    -> сегодня
        7    -> последние 7 дней
        30   -> последние 30 дней
        None -> всё время
    """

    # ============================================================
    # 1. ПОЛУЧАЕМ ОСНОВНУЮ СТАТИСТИКУ
    # ============================================================

    stats = await get_smm_stats(days)

    # Популярность столов
    table_stats = await get_table_stats(days)

    # Популярность залов
    floor_stats = await get_floor_stats(days)

    # Популярность времени
    time_stats = await get_time_stats(days)


    # ============================================================
    # 2. НАЗВАНИЕ ПЕРИОДА
    # ============================================================

    if days == 1:
        period = "Сегодня"
    elif days == 7:
        period = "Последние 7 дней"
    elif days == 30:
        period = "Последние 30 дней"
    else:
        period = "Всё время"


    # ============================================================
    # 3. ОСНОВНАЯ ШАПКА
    # ============================================================

    text = (
        "📊 <b>СТАТИСТИКА БОТА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📅 Период: <b>{period}</b>\n\n"
    )


    # ============================================================
    # 4. АУДИТОРИЯ
    # ============================================================

    text += (
        "👥 <b>АУДИТОРИЯ</b>\n"
        f"• Уникальных посетителей: "
        f"<b>{stats['unique_visitors']}</b>\n"
        f"• Всего посещений: "
        f"<b>{stats['visits']}</b>\n"
        f"• Новых пользователей: "
        f"<b>{stats['new_users']}</b>\n"
        f"• Заходов на человека: "
        f"<b>{stats['visits_per_user']}</b>\n\n"
    )


    # ============================================================
    # 5. ИСТОЧНИКИ ПРИВЛЕЧЕНИЯ
    # ============================================================

    text += "📣 <b>ИСТОЧНИКИ ПРИВЛЕЧЕНИЯ</b>\n"

    if stats["source_stats"]:

        source_names = {
            "instagram": "Instagram",
            "tiktok": "TikTok",
            "threads": "Threads",
            "telegram": "Telegram",
            "google": "Google",
            "yandex": "Yandex",
            "blogger": "Blogger",
            "qr": "QR-код",
            "friends": "Знакомые",
            "street": "Улица",
            "advertising": "Реклама",
            "other": "Другое",
            "unknown": "Не определён"
        }

        for source in stats["source_stats"]:

            source_name = source_names.get(
                source["source"],
                source["source"]
            )

            text += (
                f"• {source_name}: "
                f"<b>{source['users']}</b>\n"
            )

    else:

        text += "• Данных пока нет\n"

    text += "\n"


    # ============================================================
    # 6. ВОРОНКА БРОНИРОВАНИЯ
    # ============================================================

    text += (
        "🍽️ <b>ВОРОНКА БРОНИРОВАНИЯ</b>\n"

        f"• Начали бронирование: "
        f"<b>{stats['unique_booking_start']}</b> "
        f"(действий: {stats['booking_start']})\n"

        f"• Выбрали дату: "
        f"<b>{stats['unique_booking_date']}</b> "
        f"(действий: {stats['booking_date']})\n"

        f"• Выбрали время: "
        f"<b>{stats['unique_booking_time']}</b> "
        f"(действий: {stats['booking_time']})\n"

        f"• Выбрали зал: "
        f"<b>{stats['unique_booking_floor']}</b> "
        f"(действий: {stats['booking_floor']})\n"

        f"• Выбрали стол: "
        f"<b>{stats['unique_booking_table']}</b> "
        f"(действий: {stats['booking_table']})\n"

        f"• Отправили заявку: "
        f"<b>{stats['unique_booking_created']}</b> "
        f"(заявок: {stats['booking_created']})\n\n"
    )


    # ============================================================
    # 7. РЕЗУЛЬТАТ БРОНИРОВАНИЯ
    # ============================================================

    text += (
        "📋 <b>РЕЗУЛЬТАТ БРОНИРОВАНИЯ</b>\n"

        f"• Подтверждено: "
        f"<b>{stats['unique_booking_confirmed']}</b> "
        f"(событий: {stats['booking_confirmed']})\n"

        f"• Отменено: "
        f"<b>{stats['unique_booking_cancelled']}</b> "
        f"(событий: {stats['booking_cancelled']})\n\n"
    )


    # ============================================================
    # 8. ПОПУЛЯРНЫЕ СТОЛЫ
    # ============================================================

    text += "🪑 <b>ПОПУЛЯРНЫЕ СТОЛЫ</b>\n"

    if table_stats:

        medals = ["🥇", "🥈", "🥉"]

        for index, row in enumerate(table_stats[:3]):

            medal = medals[index]

            table_id = row["table_id"]
            selections = row["selections"]
            unique_users = row["unique_users"]

            text += (
                f"{medal} Стол №<b>{table_id}</b> — "
                f"<b>{selections}</b> выборов "
                f"({unique_users} польз.)\n"
            )

    else:

        text += "• Данных пока нет\n"

    text += "\n"


    # ============================================================
    # 9. ПОПУЛЯРНЫЕ ЗАЛЫ
    # ============================================================

    text += "📍 <b>ПОПУЛЯРНЫЕ ЗАЛЫ</b>\n"

    if floor_stats:

        medals = ["🥇", "🥈", "🥉"]

        for index, row in enumerate(floor_stats[:3]):

            medal = medals[index]

            floor_id = row["floor_id"]

            floor_name = FLOORS.get(
                floor_id,
                {}
            ).get(
                "name",
                floor_id
            )

            selections = row["selections"]
            unique_users = row["unique_users"]

            text += (
                f"{medal} {floor_name} — "
                f"<b>{selections}</b> выборов "
                f"({unique_users} польз.)\n"
            )

    else:

        text += "• Данных пока нет\n"

    text += "\n"


    # ============================================================
    # 10. ПОПУЛЯРНОЕ ВРЕМЯ
    # ============================================================

    text += "🕐 <b>ПОПУЛЯРНОЕ ВРЕМЯ</b>\n"

    if time_stats:

        medals = ["🥇", "🥈", "🥉"]

        for index, row in enumerate(time_stats[:3]):

            medal = medals[index]

            booking_time = row["booking_time"]
            selections = row["selections"]
            unique_users = row["unique_users"]

            text += (
                f"{medal} <b>{booking_time}</b> — "
                f"<b>{selections}</b> выборов "
                f"({unique_users} польз.)\n"
            )

    else:

        text += "• Данных пока нет\n"

    text += "\n"


    # ============================================================
    # 11. ОТЗЫВЫ
    # ============================================================

    text += (
        "💬 <b>ОТЗЫВЫ</b>\n"

        f"• Пользователей оставили отзыв: "
        f"<b>{stats['unique_feedback_sent']}</b>\n"

        f"• Всего отзывов: "
        f"<b>{stats['feedback_sent']}</b>\n\n"
    )


    # ============================================================
    # 12. КОНВЕРСИЯ
    # ============================================================

    text += (
        "📈 <b>КОНВЕРСИЯ</b>\n"

        f"• Посетитель → начал бронь: "
        f"<b>{stats['booking_start_conversion']}%</b>\n"

        f"• Начал бронь → отправил заявку: "
        f"<b>{stats['booking_complete_conversion']}%</b>\n"

        f"• Посетитель → отправил заявку: "
        f"<b>{stats['booking_conversion']}%</b>\n"

        f"• Заявка → подтверждена: "
        f"<b>{stats['booking_confirmation_conversion']}%</b>\n"

        f"• Посетитель → подтверждённая бронь: "
        f"<b>{stats['visitor_to_confirmed_conversion']}%</b>\n"
    )


    return text


@dp.message(
    F.from_user.id == SMM_ID,
    Command("smm_stats")
)
async def smm_stats_command(message: Message):
    """
    Команда:

        /smm_stats

    Открывает SMM-аналитику.
    По умолчанию показывается статистика за сегодня.
    """

    text = await build_smm_stats_text(1)

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=smm_stats_keyboard(1)
    )


@dp.callback_query(
    F.from_user.id == SMM_ID,
    F.data.startswith("smm_stats:")
)
async def smm_stats_callback(callback: CallbackQuery):
    """
    Переключение периода SMM-аналитики
    и обновление текущего периода.

    Поддерживаемые callback:

        smm_stats:1
        smm_stats:7
        smm_stats:30
        smm_stats:all

        smm_stats:refresh:1
        smm_stats:refresh:7
        smm_stats:refresh:30
        smm_stats:refresh:all
    """

    parts = callback.data.split(":")

    # --------------------------------------------------------
    # ОБЫЧНАЯ КНОПКА ПЕРИОДА
    # --------------------------------------------------------

    if len(parts) == 2:

        value = parts[1]

        if value == "1":
            days = 1

        elif value == "7":
            days = 7

        elif value == "30":
            days = 30

        elif value == "all":
            days = None

        else:
            return await callback.answer(
                "Неизвестный период",
                show_alert=True
            )

    # --------------------------------------------------------
    # КНОПКА ОБНОВИТЬ
    # --------------------------------------------------------

    elif len(parts) == 3 and parts[1] == "refresh":

        current_period = parts[2]

        if current_period == "1":
            days = 1

        elif current_period == "7":
            days = 7

        elif current_period == "30":
            days = 30

        elif current_period == "all":
            days = None

        else:
            days = 1

    else:

        return await callback.answer(
            "Некорректная команда статистики",
            show_alert=True
        )

    # --------------------------------------------------------
    # ПОЛУЧАЕМ СТАТИСТИКУ
    # --------------------------------------------------------

    text = await build_smm_stats_text(days)

    # --------------------------------------------------------
    # ОБНОВЛЯЕМ СООБЩЕНИЕ
    # --------------------------------------------------------

    try:

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=smm_stats_keyboard(days)
        )

    except Exception:
        pass

    await callback.answer()


# ============================================================
# 25. ПОЛЬЗОВАТЕЛИ ДЛЯ SMM
# ============================================================

async def get_smm_users(days=30):
    """
    Получает список пользователей за выбранный период.

    ВАЖНО:
        В отличие от старой версии функция не привязана
        намертво к 30 дням.

        Можно передать:

            1    -> сегодня
            7    -> 7 дней
            30   -> 30 дней
            None -> всё время
    """

    # --------------------------------------------------------
    # ФИЛЬТР ПО ДАТЕ
    # --------------------------------------------------------

    if days is None:

        date_filter = ""
        params = ()

    else:

        start_date = (
            datetime.now()
            - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d 00:00:00")

        date_filter = """
            AND created_at >= ?
        """

        params = (start_date,)

    # --------------------------------------------------------
    # ПОЛУЧАЕМ ПОЛЬЗОВАТЕЛЕЙ
    # --------------------------------------------------------

    rows = await db_manager.fetchall(
        f"""
        SELECT
            user_id,

            MAX(username) AS username,

            COUNT(
                CASE
                    WHEN event_type = 'visit'
                    THEN 1
                END
            ) AS visits,

            COUNT(
                CASE
                    WHEN event_type = 'booking_start'
                    THEN 1
                END
            ) AS booking_starts,

            COUNT(
                CASE
                    WHEN event_type = 'booking_created'
                    THEN 1
                END
            ) AS bookings,

            COUNT(
                CASE
                    WHEN event_type = 'booking_confirmed'
                    THEN 1
                END
            ) AS confirmed,

            COUNT(
                CASE
                    WHEN event_type = 'booking_cancelled'
                    THEN 1
                END
            ) AS cancelled,

            COUNT(
                CASE
                    WHEN event_type = 'feedback_sent'
                    THEN 1
                END
            ) AS feedbacks,

            MIN(created_at) AS first_activity,

            MAX(created_at) AS last_activity

        FROM analytics_events

        WHERE user_id IS NOT NULL
          AND user_id != 0
          {date_filter}

        GROUP BY user_id

        ORDER BY visits DESC

        LIMIT 50
        """,
        params
    )

    return rows

async def get_table_stats(days=None):
    """
    Популярность столов по количеству выборов.
    """

    if days is None:

        date_filter = ""
        params = ()

    else:

        start_date = (
            datetime.now()
            - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d 00:00:00")

        date_filter = "AND created_at >= ?"
        params = (start_date,)

    rows = await db_manager.fetchall(
        f"""
        SELECT
            event_data AS table_id,
            COUNT(*) AS selections,
            COUNT(DISTINCT user_id) AS unique_users
        FROM analytics_events
        WHERE event_type = 'booking_table'
          AND user_id IS NOT NULL
          AND user_id != 0
          {date_filter}
        GROUP BY event_data
        ORDER BY selections DESC
        """,
        params
    )

    return rows

async def get_floor_stats(days=None):
    """
    Популярность залов.
    """

    if days is None:

        date_filter = ""
        params = ()

    else:

        start_date = (
            datetime.now()
            - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d 00:00:00")

        date_filter = "AND created_at >= ?"
        params = (start_date,)

    rows = await db_manager.fetchall(
        f"""
        SELECT
            event_data AS floor_id,
            COUNT(*) AS selections,
            COUNT(DISTINCT user_id) AS unique_users
        FROM analytics_events
        WHERE event_type = 'booking_floor'
          AND user_id IS NOT NULL
          AND user_id != 0
          {date_filter}
        GROUP BY event_data
        ORDER BY selections DESC
        """,
        params
    )

    return rows

async def get_time_stats(days=None):
    """
    Популярность времени бронирования.
    """

    if days is None:

        date_filter = ""
        params = ()

    else:

        start_date = (
            datetime.now()
            - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d 00:00:00")

        date_filter = "AND created_at >= ?"
        params = (start_date,)

    rows = await db_manager.fetchall(
        f"""
        SELECT
            event_data AS booking_time,
            COUNT(*) AS selections,
            COUNT(DISTINCT user_id) AS unique_users
        FROM analytics_events
        WHERE event_type = 'booking_time'
          AND user_id IS NOT NULL
          AND user_id != 0
          {date_filter}
        GROUP BY event_data
        ORDER BY selections DESC
        """,
        params
    )

    return rows

def smm_period_name(days):
    """
    Возвращает человекочитаемое название периода.
    """

    if days == 1:
        return "Сегодня"

    if days == 7:
        return "Последние 7 дней"

    if days == 30:
        return "Последние 30 дней"

    return "Всё время"


def smm_users_keyboard(days=30):
    """
    Клавиатура списка пользователей.

    После возврата сохраняет выбранный период.
    """

    kb = InlineKeyboardBuilder()

    if days is None:
        period = "all"
    else:
        period = str(days)

    kb.row(
        InlineKeyboardButton(
            text="📊 К статистике",
            callback_data=f"smm_users_back:{period}"
        )
    )

    return kb.as_markup()


@dp.callback_query(
    F.from_user.id == SMM_ID,
    F.data.startswith("smm_users:")
)
async def smm_users_callback(callback: CallbackQuery):
    """
    Показывает список пользователей за выбранный период.

    Поддерживаемые callback:
        smm_users:1
        smm_users:7
        smm_users:30
        smm_users:all
    """

    parts = callback.data.split(":", 1)

    if len(parts) != 2:
        return await callback.answer(
            "Некорректный период",
            show_alert=True
        )

    period_value = parts[1]

    if period_value == "1":
        days = 1
    elif period_value == "7":
        days = 7
    elif period_value == "30":
        days = 30
    elif period_value == "all":
        days = None
    else:
        return await callback.answer(
            "Неизвестный период",
            show_alert=True
        )

    users = await get_smm_users(days)

    if not users:
        return await callback.answer(
            "Пользователей за выбранный период нет",
            show_alert=True
        )

    period = smm_period_name(days)

    text = (
        "👤 <b>ПОЛЬЗОВАТЕЛИ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📅 Период: <b>{period}</b>\n"
        f"👥 Найдено: <b>{len(users)}</b>\n\n"
    )

    for index, user in enumerate(users, start=1):

        username = user["username"]

        if username:
            name = f"@{username}"
        else:
            name = f"ID {user['user_id']}"

        text += (
            f"<b>{index}. {name}</b>\n"
            f"🆔 ID: <code>{user['user_id']}</code>\n"
            f"👀 Посещений: {user['visits'] or 0}\n"
            f"🍽 Начал бронь: {user['booking_starts'] or 0}\n"
            f"📋 Заявок: {user['bookings'] or 0}\n"
            f"✅ Подтверждено: {user['confirmed'] or 0}\n"
            f"❌ Отменено: {user['cancelled'] or 0}\n"
            f"💬 Отзывов: {user['feedbacks'] or 0}\n"
            f"🕐 Последняя активность: "
            f"{user['last_activity'] or '-'}\n"
            f"━━━━━━━━━━━━━━\n"
        )

    max_length = 3800

    back_keyboard = smm_users_keyboard(days)

    if len(text) <= max_length:

        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=back_keyboard
            )
        except Exception:
            await callback.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=back_keyboard
            )

    else:

        try:
            await callback.message.delete()
        except Exception:
            pass

        await callback.message.answer(
            (
                "👤 <b>ПОЛЬЗОВАТЕЛИ</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📅 Период: <b>{period}</b>\n"
                f"👥 Найдено: <b>{len(users)}</b>\n"
            ),
            parse_mode="HTML"
        )

        current_chunk = ""

        for index, user in enumerate(users, start=1):

            username = user["username"]

            if username:
                name = f"@{username}"
            else:
                name = f"ID {user['user_id']}"

            user_text = (
                f"<b>{index}. {name}</b>\n"
                f"🆔 ID: <code>{user['user_id']}</code>\n"
                f"👀 Посещений: {user['visits'] or 0}\n"
                f"🍽 Начал бронь: {user['booking_starts'] or 0}\n"
                f"📋 Заявок: {user['bookings'] or 0}\n"
                f"✅ Подтверждено: {user['confirmed'] or 0}\n"
                f"❌ Отменено: {user['cancelled'] or 0}\n"
                f"💬 Отзывов: {user['feedbacks'] or 0}\n"
                f"🕐 Последняя активность: "
                f"{user['last_activity'] or '-'}\n"
                f"━━━━━━━━━━━━━━\n"
            )

            if (
                current_chunk
                and len(current_chunk) + len(user_text) > max_length
            ):
                await callback.message.answer(
                    current_chunk,
                    parse_mode="HTML"
                )

                current_chunk = user_text

            else:
                current_chunk += user_text

        if current_chunk:
            await callback.message.answer(
                current_chunk,
                parse_mode="HTML"
            )

        await callback.message.answer(
            "👇",
            reply_markup=back_keyboard
        )

    await callback.answer()

@dp.callback_query(
    F.from_user.id == SMM_ID,
    F.data.startswith("smm_users_back:")
)
async def smm_users_back_callback(callback: CallbackQuery):

    parts = callback.data.split(":", 1)

    if len(parts) != 2:
        return await callback.answer(
            "Некорректный период",
            show_alert=True
        )

    period_value = parts[1]

    if period_value == "1":
        days = 1
    elif period_value == "7":
        days = 7
    elif period_value == "30":
        days = 30
    elif period_value == "all":
        days = None
    else:
        return await callback.answer(
            "Неизвестный период",
            show_alert=True
        )

    text = await build_smm_stats_text(days)

    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=smm_stats_keyboard(days)
        )
    except Exception:
        await callback.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=smm_stats_keyboard(days)
        )

    await callback.answer()

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
@dp.message(
    F.from_user.id == ADMIN_ID,
    Command("last_booking")
)
async def show_last_admin_booking(message: Message):

    row = await db_manager.fetchone(
        """
        SELECT *
        FROM bookings
        WHERE user_id = 0
        AND status != 'cancelled'
        ORDER BY id DESC
        LIMIT 1
        """
    )

    if not row:
        return await message.answer(
            "📭 Телефонных броней пока нет."
        )

    floor_name = FLOORS.get(
        row["floor_id"],
        {}
    ).get(
        "name",
        row["floor_id"]
    )

    text = (
        f"📞 <b>Последняя телефонная бронь</b>\n\n"
        f"🆔 №{row['id']}\n"
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
            callback_data=f"cancel_last_admin_{row['id']}"
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

    if row["user_id"] != 0:
        await analytics_action(
            user_id=row["user_id"],
            username="",
            event_type="booking_confirmed",
            event_data=str(booking_id)
        )

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
    if row and row["user_id"] != 0:
        await analytics_action(
            user_id=row["user_id"],
            username="",
            event_type="booking_cancelled",
            event_data=str(booking_id)
        )
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
    F.data.startswith("cancel_last_admin_")
)
async def cancel_last_admin_booking(
    callback: CallbackQuery
):
    booking_id = int(
        callback.data.replace(
            "cancel_last_admin_",
            ""
        )
    )

    row = await db_manager.fetchone(
        """
        SELECT *
        FROM bookings
        WHERE id = ?
        """,
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

    if row and row["user_id"] != 0:
        await analytics_action(
            user_id=row["user_id"],
            username="",
            event_type="booking_cancelled",
            event_data=str(booking_id)
        )

    try:
        scheduler.remove_job(
            f"rem_{booking_id}"
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

    if row and row["user_id"] != 0:
        await analytics_action(
            user_id=row["user_id"],
            username="",
            event_type="booking_cancelled",
            event_data=str(booking_id)
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
@dp.message(
    Booking.event_edit
)
async def save_event_text(
    message: Message,
    state: FSMContext
):

    await db_manager.execute(
        """
        UPDATE events
        SET text = ?
        WHERE id = 1
        """,
        (
            message.text,
        )
    )

    await message.answer(
        "✅ Текст мероприятия обновлен."
    )

    await state.clear()

@dp.message(
    F.from_user.id == SMM_ID,
    Command("edit_event")
)
async def edit_event_start(
    message: Message,
    state: FSMContext
):

    event = await db_manager.fetchone(
        """
        SELECT *
        FROM events
        WHERE id = 1
        """
    )

    current_text = (
        event["text"]
        if event
        else "Нет текста"
    )

    kb = InlineKeyboardBuilder()

    kb.row(
        InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data="smm_main_menu"
        )
    )

    await message.answer(
        "🎉 Текущее мероприятие:\n\n"
        f"{current_text}\n\n"
        "Отправьте новый текст мероприятия.",
        reply_markup=kb.as_markup()
    )

    await state.set_state(
        Booking.event_edit
    )
@dp.callback_query(
    F.from_user.id == SMM_ID,
    F.data == "smm_main_menu"
)
async def smm_main_menu(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.clear()

    try:
        await callback.message.delete()
    except:
        pass

    await callback.message.answer(
        f"Здравствуйте, <b>{callback.from_user.first_name}</b>! 💫\n\n"
        "Рады приветствовать вас в Лампе. Я помогу вам забронировать столик "
        "и отвечу на вопросы. С чего начнем?😉",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )

    await callback.answer()
# ---------------- RUN ----------------
async def main():
    try:
        # ЭТАП 1: Инициализация
        print("=== Подготовка к запуску ===")
        await db_manager.connect()
        # --- БЛОК ОДНОРАЗОВОЙ ОЧИСТКИ ---
        OLD_ADMIN_IDS = [1000460496]  # Список всех старых ID, у кого висит панель
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
