import asyncio
import calendar
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, \
    ReplyKeyboardRemove, BotCommand, BotCommandScopeChat # Добавь BotCommandScopeChat и BotCommand
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
DB_NAME = os.getenv("DB_NAME", "lampa.sqlite")

# Остальные настройки можно оставить как есть, так как они не секретные
MENU_URL = "https://menusa.app/11f147d08be313bb8dcc55efc6664fa5"
TOTAL_TABLES = 23

ZONES = {
    "hall_1": {"name": "🛋 Первый этаж (основной зал)", "capacity": 26},
    "hall_2": {"name": "✨ Второй этаж (уютная зона)", "capacity": 24},
    "terrace": {"name": "🌿 Летняя терраса", "capacity": 16},
}

if not BOT_TOKEN:
    exit("Ошибка: токен бота не найден! Проверь файл .env")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

# Настраиваем хранилище задач.
# Используем ту же базу, что и для броней, но через протокол sqlite:///
jobstores = {
    'default': SQLAlchemyJobStore(url=f'sqlite:///{DB_NAME}')
}

# Инициализируем планировщик с этим хранилищем
scheduler = AsyncIOScheduler(jobstores=jobstores)

#------------------Base---------------------
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
class Booking(StatesGroup):
    date = State()
    time = State()
    zone = State()
    guests = State()
    name = State()
    phone = State()
    comment = State()
    wishes = State()
    feedback = State()

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
    # Проверка на случай, если таблица уже была создана без новых колонок
    try:
        await db_manager.conn.execute("ALTER TABLE bookings ADD COLUMN zone TEXT")
    except:
        pass
    try:
        await db_manager.conn.execute("ALTER TABLE bookings ADD COLUMN status TEXT DEFAULT 'pending'")
    except:
        pass

    await db_manager.conn.commit()

# ---------------- LOGIC ----------------
def get_duration(dt: datetime):
    return timedelta(hours=1, minutes=30) if 12 <= dt.hour < 18 else timedelta(hours=3)


async def get_time_status(date: str, time: str):
    try:
        start = datetime.strptime(f"{date} {time}", "%d.%m.%Y %H:%M")
    except ValueError:
        return "full", []

    if start < datetime.now():
        return "full", []

    duration = get_duration(start)
    end = start + duration

    cursor = await db_manager.conn.execute("SELECT time, guests, zone FROM bookings WHERE date=?", (date,))
    rows = await cursor.fetchall()

    tables_used = 0
    zone_usage = {z: 0 for z in ZONES}

    for r_time, r_guests, r_zone in rows:
        try:
            existing_start = datetime.strptime(f"{date} {r_time}", "%d.%m.%Y %H:%M")
            existing_end = existing_start + get_duration(existing_start)
            if not (end <= existing_start or start >= existing_end):
                tables_used += 1
                if r_zone in zone_usage:
                    zone_usage[r_zone] += r_guests
        except:
            continue

    if tables_used >= TOTAL_TABLES:
        return "full", []

    available_zones = [z for z in ZONES if zone_usage[z] < ZONES[z]["capacity"]]
    if not available_zones:
        return "full", []

    status = "all" if len(available_zones) == len(ZONES) else "partial"
    return status, available_zones
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

    # Ограничение для 30 мая: оставляем только слоты до 14:00 включительно
    if date.startswith("30.05."):
        times = [t for t in times if t <= "14:00"]

    for t in times:
        status, _ = await get_time_status(date, t)
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
        f"Здравствуйте, <b>{message.from_user.first_name}</b>! ✨\n\n"
        "Рады приветствовать вас в Лампе. Я помогу вам забронировать столик "
        "и отвечу на вопросы. С чего начнем?",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )

# --- Инструкция по использованию ---
@dp.message(Command("help"))
async def help_command(message: Message):
    help_text = (
        "✨ <b>Как пользоваться ботом гастробара «Лампа»</b>\n\n"
        "1️⃣ <b>Бронирование:</b> Нажмите «🍽 Забронировать стол» в меню. Выберите дату, время и зону. Обязательно введите ваше имя и телефон.\n\n"
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

    # Защита на стороне сервера для 30 мая
    if date_str and date_str.startswith("30.05.") and time > "14:00":
        return await callback.answer("Извините, 30 мая бронирование доступно только до 14:00 🕑", show_alert=True)

    status, zones = await get_time_status(date_str, time)

    if status == "full":
        return await callback.answer("Извините, на это время мест нет", show_alert=True)

    await state.update_data(time=time)
    kb = InlineKeyboardBuilder()
    for z in zones:
        kb.button(text=ZONES[z]["name"], callback_data=f"zone:{z}")
    kb.adjust(1)

    await callback.message.edit_text("В какой зоне вы бы хотели отдохнуть?", reply_markup=kb.as_markup())
    await state.set_state(Booking.zone)


@dp.callback_query(F.data.startswith("zone:"))
async def set_zone(callback: CallbackQuery, state: FSMContext):
    zone_id = callback.data.split(":")[1]
    await state.update_data(zone=zone_id)
    await callback.message.edit_text("Будем рады узнать, на какое количество гостей подготовить стол?")
    await state.set_state(Booking.guests)


@dp.message(Booking.guests)
async def guests_process(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        return await message.answer("Пожалуйста, укажите количество гостей числом.")

    num = int(message.text)
    data = await state.get_data()
    limit = ZONES[data['zone']]['capacity']

    if num > limit:
        return await message.answer(
            f"В этой зоне мы можем разместить до {limit} гостей. Пожалуйста, введите другое число.")

    await state.update_data(guests=num)
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
    await message.answer("Ваши дополнительные пожелания? (Если их нет, просто отправьте любое сообщение)")
    await state.set_state(Booking.wishes)


@dp.message(Booking.wishes)
async def finish_booking(message: Message, state: FSMContext):
    data = await state.get_data()
    wishes = message.text
    user_id = message.from_user.id

    # 1. Сохранение в БД с получением ID новой брони
    # Используем прямое соединение для контроля за курсором
    query = """
        INSERT INTO bookings (user_id, date, time, guests, zone, name, phone, comment, wishes, status)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """
    params = (
        user_id, data['date'], data['time'], data['guests'], data['zone'],
        data['name'], data['phone'], data['comment'], wishes, 'pending'
    )

    async with db_manager.conn.execute(query, params) as cursor:
        booking_id = cursor.lastrowid  # Получаем ID, пока курсор открыт
        await db_manager.conn.commit()

    # 2. Подготовка кнопок для администратора
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"conf_{booking_id}"),
        InlineKeyboardButton(text="❌ Отменить", callback_data=f"canc_{booking_id}")
    )

    # Название зоны для красивого вывода
    zone_display = ZONES.get(data['zone'], {}).get('name', data['zone'])

    admin_msg = (
        f"<b>🔔 НОВАЯ БРОНЬ №{booking_id}</b>\n\n"
        f"📅 Дата: <b>{data['date']}</b> в <b>{data['time']}</b>\n"
        f"📍 Зона: {zone_display}\n"
        f"👥 Гости: {data['guests']} чел.\n"
        f"👤 Имя: {data['name']}\n"
        f"📞 Тел: <code>{data['phone']}</code>\n"
        f"💬 Коммент: {data['comment']}\n"
        f"🌟 Пожелания: {wishes}"
    )

    # 3. Уведомление админа
    try:
        await bot.send_message(ADMIN_ID, admin_msg, parse_mode="HTML", reply_markup=kb.as_markup())
    except Exception as e:
        print(f"Ошибка при уведомлении админа: {e}")

    # 4. Ответ пользователю
    await message.answer(
        "Спасибо! Мы передали информацию администратору. ✨\n\n"
        "Как только столик будет подтвержден, вам придет сообщение. "
        "Обычно это занимает не более 15 минут. ❤️",
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
    events_text = (
        "<b>На данный момент мероприятия не запланированы!</b>\n\n"
        "Следите за нашими обновлениями! ✨"
    )

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_main"))

    await callback.message.edit_text(
        events_text,
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )
    await callback.answer()

# Хэндлер для возврата в главное меню
@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text(
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
        "Нам очень важно ваше мнение! ✨\n"
        "Пожалуйста, напишите ваш отзыв одним сообщением, и я сразу передам его руководству."
    )
    await state.set_state(Booking.feedback)
    await callback.answer()


@dp.message(Booking.feedback)
async def feedback_process(message: Message, state: FSMContext):
    # 1. Проверяем, есть ли хоть какой-то контент (текст или фото)
    if not message.text and not message.photo:
        return await message.answer(
            "Пожалуйста, пришлите ваш отзыв текстом или отправьте фото с описанием. 📝"
        )

    user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.from_user.id}"

    # 2. Собираем текст отзыва (если это фото, текст может быть в caption)
    feedback_text = message.text or message.caption or "[Без текста]"

    admin_msg = (
        f"<b>💬 НОВЫЙ ОТЗЫВ</b>\n\n"
        f"👤 От кого: {message.from_user.full_name} ({user_info})\n"
        f"📝 Текст: {feedback_text}"
    )

    try:
        if message.photo:
            # Если пользователь прислал фото, пересылаем его админу с подписью
            # Берем последнее фото из списка (оно в самом лучшем качестве)
            await bot.send_photo(
                ADMIN_ID,
                photo=message.photo[-1].file_id,
                caption=admin_msg,
                parse_mode="HTML"
            )
        else:
            # Если только текст — отправляем просто сообщением
            await bot.send_message(ADMIN_ID, admin_msg, parse_mode="HTML")

        await message.answer(
            "Спасибо за ваш отзыв! ❤️\n"
            "Мы получили его и обязательно ознакомимся.",
            reply_markup=main_menu()
        )
    except Exception as e:
        print(f"Ошибка при отправке отзыва админу: {e}")
        await message.answer("Произошла ошибка при отправке. Попробуйте позже.")

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




# --- АДМИН-ПАНЕЛЬ ---

# Хэндлер для команды /today
@dp.message(F.from_user.id == ADMIN_ID, Command("today"))
async def admin_today_bookings(message: Message):
    today_str = datetime.now().strftime("%d.%m.%Y")
    # Добавляем фильтр: status = 'confirmed'
    rows = await db_manager.fetchall(
        "SELECT * FROM bookings WHERE date = ? AND status = 'confirmed' ORDER BY time ASC",
        (today_str,)
    )

    if not rows:
        return await message.answer(f"📅 На сегодня ({today_str}) подтвержденных броней пока нет.")

    text = f"📅 <b>Брони на сегодня ({today_str}):</b>\n\n"
    for row in rows:
        zone_name = ZONES.get(row['zone'], {}).get('name', row['zone'])
        text += (
            f"⏰ <b>{row['time']}</b> — {row['name']}\n"
            f"👥 {row['guests']} чел. | 📍 {zone_name}\n"
            f"📞 <code>{row['phone']}</code>\n"
            f"---------------------------\n"
        )
    await message.answer(text, parse_mode="HTML")


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
    await db_manager.conn.commit()

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
    await db_manager.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
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
# ---------------- RUN ----------------
async def main():
    try:
        # ЭТАП 1: Инициализация
        print("=== Подготовка к запуску ===")
        await db_manager.connect()
        # --- БЛОК ОДНОРАЗОВОЙ ОЧИСТКИ ---
        OLD_ADMIN_IDS = [736559077,1000460496]  # Список всех старых ID, у кого висит панель
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

        # ЭТАП 2: Очистка очереди
        # Если бот был выключен, за это время люди могли натыкать кнопок.
        # Чтобы бот не сошел с ума от лавины старых сообщений, мы их удаляем.
        await bot.delete_webhook(drop_pending_updates=True)

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


