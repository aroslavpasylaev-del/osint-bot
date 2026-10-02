# язык: Python 3.11, файл: main.py
# Telegram bot для OSINT: пробив по номеру, email, username, ФИО, авто, IP, домену

import asyncio
import aiohttp
import json
import re
import hashlib
import logging
import socket
from datetime import datetime
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

# ===== КОНФИГ =====
BOT_TOKEN = "8697901420:AAHylNw2klqOaie2JOnEfyuGg8NKHyGDHnk # язык: Python 3.11, файл: main.py
# Telegram bot для OSINT: пробив по номеру, email, username, ФИО, авто, IP, домену

import asyncio
import aiohttp
import json
import re
import hashlib
import logging
import socket
from datetime import datetime
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

# ===== КОНФИГ =====
BOT_TOKEN = 8697901420:AAHylNw2klqOaie2JOnEfyuGg8NKHyGDHnk   # <-- ЗАМЕНИ НА СВОЙ ТОКЕН
ADMIN_IDS = []                  # можешь оставить пустым

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()


# ===== МОДЕЛИ =====
@dataclass
class OSINTResult:
    query: str
    query_type: str
    source: str
    data: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)


# ===== БАЗА ДАННЫХ =====
import sqlite3

class Database:
    def __init__(self, path: str = "osint.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self._init()

    def _init(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                requests_count INTEGER DEFAULT 0,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                query TEXT,
                query_type TEXT,
                result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.conn.commit()

    def add_request(self, user_id: int, query: str, qtype: str, result: str):
        self.conn.execute(
            "INSERT INTO history (user_id, query, query_type, result) VALUES (?, ?, ?, ?)",
            (user_id, query, qtype, result)
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        self.conn.execute(
            "UPDATE users SET requests_count = requests_count + 1 WHERE user_id = ?",
            (user_id,)
        )
        self.conn.commit()


db = Database()


# ===== HTTP-КЛИЕНТ =====
class HTTPClient:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36"
        }

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers=self.headers)
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def get(self, url: str, **kwargs) -> Optional[Dict]:
        try:
            async with self.session.get(url, timeout=15, **kwargs) as resp:
                if resp.status == 200:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        return await resp.json()
                    return {"text": await resp.text()}
        except Exception as e:
            logger.error(f"GET {url}: {e}")
        return None


# ===== МОДУЛИ ПРОБИВА =====

class PhoneOSINT:
    @staticmethod
    async def run(phone: str, http: HTTPClient) -> List[OSINTResult]:
        results = []
        clean = re.sub(r"[^\d+]", "", phone)

        # WhatsApp проверка
        try:
            async with http.session.get(f"https://wa.me/{clean.replace('+', '')}") as r:
                if r.status == 200:
                    results.append(OSINTResult(
                        query=phone, query_type="phone", source="whatsapp",
                        data={"registered": True, "url": f"https://wa.me/{clean.replace('+', '')}"}
                    ))
        except Exception:
            pass

        # Telegram проверка
        try:
            async with http.session.get(f"https://t.me/+{clean.replace('+', '')}") as r:
                if r.status == 200:
                    results.append(OSINTResult(
                        query=phone, query_type="phone", source="telegram",
                        data={"registered": True}
                    ))
        except Exception:
            pass

        return results


class EmailOSINT:
    @staticmethod
    async def run(email: str, http: HTTPClient) -> List[OSINTResult]:
        results = []

        # Gravatar
        h = hashlib.md5(email.lower().encode()).hexdigest()
        grav = await http.get(f"https://www.gravatar.com/{h}.json")
        if grav and "entry" in grav:
            results.append(OSINTResult(
                query=email, query_type="email", source="gravatar",
                data=grav["entry"][0]
            ))

        # GitHub поиск по email
        gh = await http.get(f"https://api.github.com/search/users?q={email}")
        if gh and gh.get("total_count", 0) > 0:
            results.append(OSINTResult(
                query=email, query_type="email", source="github",
                data={"total": gh["total_count"], "users": gh.get("items", [])[:5]}
            ))

        return results


class UsernameOSINT:
    SITES = {
        "github": "https://github.com/{}",
        "twitter": "https://twitter.com/{}",
        "instagram": "https://instagram.com/{}",
        "facebook": "https://facebook.com/{}",
        "tiktok": "https://tiktok.com/@{}",
        "reddit": "https://reddit.com/user/{}",
        "telegram": "https://t.me/{}",
        "vk": "https://vk.com/{}",
        "youtube": "https://youtube.com/@{}",
        "twitch": "https://twitch.tv/{}",
        "steam": "https://steamcommunity.com/id/{}",
        "pinterest": "https://pinterest.com/{}",
        "linkedin": "https://linkedin.com/in/{}",
        "medium": "https://medium.com/@{}",
        "soundcloud": "https://soundcloud.com/{}",
        "habr": "https://habr.com/ru/users/{}/",
        "pikabu": "https://pikabu.ru/@{}",
        "ok": "https://ok.ru/{}",
        "dzen": "https://dzen.ru/{}",
        "rutube": "https://rutube.ru/channel/{}/",
    }

    @staticmethod
    async def run(username: str, http: HTTPClient) -> List[OSINTResult]:
        results = []
        found = []
        clean = username.lstrip("@")

        tasks = [
            UsernameOSINT._check(site, clean, url, http)
            for site, url in UsernameOSINT.SITES.items()
        ]
        checked = await asyncio.gather(*tasks, return_exceptions=True)

        for r in checked:
            if isinstance(r, OSINTResult):
                found.append(r)

        if found:
            results.append(OSINTResult(
                query=username, query_type="username", source="sherlock_multi",
                data={"accounts": [f.data for f in found]}
            ))

        return results

    @staticmethod
    async def _check(site, username, url, http):
        full = url.format(username)
        try:
            async with http.session.get(full, timeout=10, allow_redirects=True) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    if username.lower() in text.lower()[:5000]:
                        return OSINTResult(
                            query=username, query_type="username", source=site,
                            data={"url": full}
                        )
        except Exception:
            pass
        return None


class IPOSINT:
    @staticmethod
    async def run(query: str, http: HTTPClient) -> List[OSINTResult]:
        results = []

        # ipinfo без ключа
        ip = await http.get(f"https://ipinfo.io/{query}/json")
        if ip and "ip" in ip:
            results.append(OSINTResult(
                query=query, query_type="ip", source="ipinfo", data=ip
            ))

        # Reverse DNS
        try:
            hostname = socket.gethostbyaddr(query)[0]
            results.append(OSINTResult(
                query=query, query_type="ip", source="dns",
                data={"hostname": hostname}
            ))
        except Exception:
            pass

        return results


class TelegramOSINT:
    @staticmethod
    async def run(username: str, http: HTTPClient) -> List[OSINTResult]:
        results = []
        clean = username.lstrip("@")

        tg = await http.get(f"https://t.me/{clean}")
        if tg:
            results.append(OSINTResult(
                query=clean, query_type="telegram", source="tme",
                data={"exists": True, "url": f"https://t.me/{clean}"}
            ))

        preview = await http.get(f"https://t.me/s/{clean}")
        if preview and "text" in preview:
            html = preview["text"]
            name = re.search(r'<meta property="og:title" content="([^"]+)"', html)
            desc = re.search(r'<meta property="og:description" content="([^"]+)"', html)
            photo = re.search(r'<meta property="og:image" content="([^"]+)"', html)
            results.append(OSINTResult(
                query=clean, query_type="telegram", source="tme_preview",
                data={
                    "name": name.group(1) if name else None,
                    "bio": desc.group(1) if desc else None,
                    "photo": photo.group(1) if photo else None,
                }
            ))

        return results


# ===== АГРЕГАТОР =====
class OSINTEngine:
    @staticmethod
    def detect_type(query: str) -> str:
        q = query.strip()
        if re.match(r"^\+?\d{10,15}$", q):
            return "phone"
        if re.match(r"^[^@]+@[^@]+\.[^@]+$", q):
            return "email"
        if re.match(r"^(\d{1,3}\.){3}\d{1,3}$", q):
            return "ip"
        if q.startswith("@"):
            return "telegram"
        if re.match(r"^[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+", q):
            return "name"
        if re.match(r"^[A-Za-z0-9_]{3,32}$", q):
            return "username"
        if "." in q:
            return "domain"
        return "unknown"

    @staticmethod
    async def run(query: str) -> List[OSINTResult]:
        qtype = OSINTEngine.detect_type(query)
        results = []

        async with HTTPClient() as http:
            if qtype == "phone":
                results = await PhoneOSINT.run(query, http)
            elif qtype == "email":
                results = await EmailOSINT.run(query, http)
            elif qtype == "username":
                results = await UsernameOSINT.run(query, http)
            elif qtype == "ip":
                results = await IPOSINT.run(query, http)
            elif qtype == "telegram":
                results = await TelegramOSINT.run(query, http)
            elif qtype == "domain":
                results = await IPOSINT.run(query, http)

        return results


# ===== ФОРМАТИРОВАНИЕ =====
def format_results(results: List[OSINTResult], query: str) -> str:
    if not results:
        return f"❌ <b>Ничего не найдено</b>\nЗапрос: <code>{query}</code>"

    text = f"🔍 <b>OSINT пробив:</b> <code>{query}</code>\n"
    text += f"📊 <b>Найдено источников:</b> {len(results)}\n\n"

    for i, r in enumerate(results, 1):
        text += f"<b>{i}. {r.source.upper()}</b> ({r.query_type})\n"

        if r.source == "sherlock_multi":
            accounts = r.data.get("accounts", [])
            text += f"  └ Аккаунтов: {len(accounts)}\n"
            for a in accounts[:15]:
                text += f"     • {a.get('url')}\n"

        elif r.source == "tme_preview":
            d = r.data
            text += f"  ├ Имя: {d.get('name', '—')}\n"
            text += f"  ├ Bio: {d.get('bio', '—')}\n"
            text += f"  └ Фото: {d.get('photo', '—')}\n"

        elif r.source == "ipinfo":
            d = r.data
            text += f"  ├ IP: {d.get('ip')}\n"
            text += f"  ├ Город: {d.get('city', '—')}\n"
            text += f"  ├ Страна: {d.get('country', '—')}\n"
            text += f"  └ Провайдер: {d.get('org', '—')}\n"

        elif r.source == "gravatar":
            text += f"  └ Профиль: {json.dumps(r.data)[:150]}\n"

        elif r.source == "whatsapp":
            text += f"  └ Зарегистрирован: {r.data.get('registered')}\n"

        elif r.source == "dns":
            text += f"  └ Hostname: {r.data.get('hostname')}\n"

        elif r.source == "github":
            text += f"  └ Найдено: {r.data.get('total', 0)}\n"

        else:
            text += f"  └ {json.dumps(r.data, ensure_ascii=False)[:200]}\n"

        text += "\n"

    return text[:4000]


# ===== HANDLERS =====
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await msg.answer(
        "🔍 <b>OSINT Bot</b>\n\n"
        "Отправь мне запрос:\n"
        "• <code>@username</code> — поиск по сайтам\n"
        "• <code>user@mail.ru</code> — email\n"
        "• <code>+79991234567</code> — номер\n"
        "• <code>8.8.8.8</code> — IP\n"
        "• <code>@durov</code> — Telegram\n"
    )


@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    await cmd_start(msg)


@dp.message(F.text)
async def handle_query(msg: types.Message):
    query = msg.text.strip()
    if query.startswith("/"):
        return

    status = await msg.answer("🔎 <i>Пробиваю...</i>")

    try:
        results = await OSINTEngine.run(query)
        text = format_results(results, query)

        db.add_request(msg.from_user.id, query, OSINTEngine.detect_type(query),
                       json.dumps([r.data for r in results], ensure_ascii=False, default=str))

        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await msg.answer(text[i:i+4096])
        else:
            await status.edit_text(text)

    except Exception as e:
        logger.exception(e)
        await status.edit_text(f"❌ <b>Ошибка:</b> <code>{e}</code>")


# ===== ЗАПУСК =====
async def main():
    logger.info("OSINT Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())   # <-- ЗАМЕНИ НА СВОЙ ТОКЕН
ADMIN_IDS = []                  # можешь оставить пустым

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()


# ===== МОДЕЛИ =====
@dataclass
class OSINTResult:
    query: str
    query_type: str
    source: str
    data: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)


# ===== БАЗА ДАННЫХ =====
import sqlite3

class Database:
    def __init__(self, path: str = "osint.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self._init()

    def _init(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                requests_count INTEGER DEFAULT 0,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                query TEXT,
                query_type TEXT,
                result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.conn.commit()

    def add_request(self, user_id: int, query: str, qtype: str, result: str):
        self.conn.execute(
            "INSERT INTO history (user_id, query, query_type, result) VALUES (?, ?, ?, ?)",
            (user_id, query, qtype, result)
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        self.conn.execute(
            "UPDATE users SET requests_count = requests_count + 1 WHERE user_id = ?",
            (user_id,)
        )
        self.conn.commit()


db = Database()


# ===== HTTP-КЛИЕНТ =====
class HTTPClient:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36"
        }

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers=self.headers)
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def get(self, url: str, **kwargs) -> Optional[Dict]:
        try:
            async with self.session.get(url, timeout=15, **kwargs) as resp:
                if resp.status == 200:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        return await resp.json()
                    return {"text": await resp.text()}
        except Exception as e:
            logger.error(f"GET {url}: {e}")
        return None


# ===== МОДУЛИ ПРОБИВА =====

class PhoneOSINT:
    @staticmethod
    async def run(phone: str, http: HTTPClient) -> List[OSINTResult]:
        results = []
        clean = re.sub(r"[^\d+]", "", phone)

        # WhatsApp проверка
        try:
            async with http.session.get(f"https://wa.me/{clean.replace('+', '')}") as r:
                if r.status == 200:
                    results.append(OSINTResult(
                        query=phone, query_type="phone", source="whatsapp",
                        data={"registered": True, "url": f"https://wa.me/{clean.replace('+', '')}"}
                    ))
        except Exception:
            pass

        # Telegram проверка
        try:
            async with http.session.get(f"https://t.me/+{clean.replace('+', '')}") as r:
                if r.status == 200:
                    results.append(OSINTResult(
                        query=phone, query_type="phone", source="telegram",
                        data={"registered": True}
                    ))
        except Exception:
            pass

        return results


class EmailOSINT:
    @staticmethod
    async def run(email: str, http: HTTPClient) -> List[OSINTResult]:
        results = []

        # Gravatar
        h = hashlib.md5(email.lower().encode()).hexdigest()
        grav = await http.get(f"https://www.gravatar.com/{h}.json")
        if grav and "entry" in grav:
            results.append(OSINTResult(
                query=email, query_type="email", source="gravatar",
                data=grav["entry"][0]
            ))

        # GitHub поиск по email
        gh = await http.get(f"https://api.github.com/search/users?q={email}")
        if gh and gh.get("total_count", 0) > 0:
            results.append(OSINTResult(
                query=email, query_type="email", source="github",
                data={"total": gh["total_count"], "users": gh.get("items", [])[:5]}
            ))

        return results


class UsernameOSINT:
    SITES = {
        "github": "https://github.com/{}",
        "twitter": "https://twitter.com/{}",
        "instagram": "https://instagram.com/{}",
        "facebook": "https://facebook.com/{}",
        "tiktok": "https://tiktok.com/@{}",
        "reddit": "https://reddit.com/user/{}",
        "telegram": "https://t.me/{}",
        "vk": "https://vk.com/{}",
        "youtube": "https://youtube.com/@{}",
        "twitch": "https://twitch.tv/{}",
        "steam": "https://steamcommunity.com/id/{}",
        "pinterest": "https://pinterest.com/{}",
        "linkedin": "https://linkedin.com/in/{}",
        "medium": "https://medium.com/@{}",
        "soundcloud": "https://soundcloud.com/{}",
        "habr": "https://habr.com/ru/users/{}/",
        "pikabu": "https://pikabu.ru/@{}",
        "ok": "https://ok.ru/{}",
        "dzen": "https://dzen.ru/{}",
        "rutube": "https://rutube.ru/channel/{}/",
    }

    @staticmethod
    async def run(username: str, http: HTTPClient) -> List[OSINTResult]:
        results = []
        found = []
        clean = username.lstrip("@")

        tasks = [
            UsernameOSINT._check(site, clean, url, http)
            for site, url in UsernameOSINT.SITES.items()
        ]
        checked = await asyncio.gather(*tasks, return_exceptions=True)

        for r in checked:
            if isinstance(r, OSINTResult):
                found.append(r)

        if found:
            results.append(OSINTResult(
                query=username, query_type="username", source="sherlock_multi",
                data={"accounts": [f.data for f in found]}
            ))

        return results

    @staticmethod
    async def _check(site, username, url, http):
        full = url.format(username)
        try:
            async with http.session.get(full, timeout=10, allow_redirects=True) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    if username.lower() in text.lower()[:5000]:
                        return OSINTResult(
                            query=username, query_type="username", source=site,
                            data={"url": full}
                        )
        except Exception:
            pass
        return None


class IPOSINT:
    @staticmethod
    async def run(query: str, http: HTTPClient) -> List[OSINTResult]:
        results = []

        # ipinfo без ключа
        ip = await http.get(f"https://ipinfo.io/{query}/json")
        if ip and "ip" in ip:
            results.append(OSINTResult(
                query=query, query_type="ip", source="ipinfo", data=ip
            ))

        # Reverse DNS
        try:
            hostname = socket.gethostbyaddr(query)[0]
            results.append(OSINTResult(
                query=query, query_type="ip", source="dns",
                data={"hostname": hostname}
            ))
        except Exception:
            pass

        return results


class TelegramOSINT:
    @staticmethod
    async def run(username: str, http: HTTPClient) -> List[OSINTResult]:
        results = []
        clean = username.lstrip("@")

        tg = await http.get(f"https://t.me/{clean}")
        if tg:
            results.append(OSINTResult(
                query=clean, query_type="telegram", source="tme",
                data={"exists": True, "url": f"https://t.me/{clean}"}
            ))

        preview = await http.get(f"https://t.me/s/{clean}")
        if preview and "text" in preview:
            html = preview["text"]
            name = re.search(r'<meta property="og:title" content="([^"]+)"', html)
            desc = re.search(r'<meta property="og:description" content="([^"]+)"', html)
            photo = re.search(r'<meta property="og:image" content="([^"]+)"', html)
            results.append(OSINTResult(
                query=clean, query_type="telegram", source="tme_preview",
                data={
                    "name": name.group(1) if name else None,
                    "bio": desc.group(1) if desc else None,
                    "photo": photo.group(1) if photo else None,
                }
            ))

        return results


# ===== АГРЕГАТОР =====
class OSINTEngine:
    @staticmethod
    def detect_type(query: str) -> str:
        q = query.strip()
        if re.match(r"^\+?\d{10,15}$", q):
            return "phone"
        if re.match(r"^[^@]+@[^@]+\.[^@]+$", q):
            return "email"
        if re.match(r"^(\d{1,3}\.){3}\d{1,3}$", q):
            return "ip"
        if q.startswith("@"):
            return "telegram"
        if re.match(r"^[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+", q):
            return "name"
        if re.match(r"^[A-Za-z0-9_]{3,32}$", q):
            return "username"
        if "." in q:
            return "domain"
        return "unknown"

    @staticmethod
    async def run(query: str) -> List[OSINTResult]:
        qtype = OSINTEngine.detect_type(query)
        results = []

        async with HTTPClient() as http:
            if qtype == "phone":
                results = await PhoneOSINT.run(query, http)
            elif qtype == "email":
                results = await EmailOSINT.run(query, http)
            elif qtype == "username":
                results = await UsernameOSINT.run(query, http)
            elif qtype == "ip":
                results = await IPOSINT.run(query, http)
            elif qtype == "telegram":
                results = await TelegramOSINT.run(query, http)
            elif qtype == "domain":
                results = await IPOSINT.run(query, http)

        return results


# ===== ФОРМАТИРОВАНИЕ =====
def format_results(results: List[OSINTResult], query: str) -> str:
    if not results:
        return f"❌ <b>Ничего не найдено</b>\nЗапрос: <code>{query}</code>"

    text = f"🔍 <b>OSINT пробив:</b> <code>{query}</code>\n"
    text += f"📊 <b>Найдено источников:</b> {len(results)}\n\n"

    for i, r in enumerate(results, 1):
        text += f"<b>{i}. {r.source.upper()}</b> ({r.query_type})\n"

        if r.source == "sherlock_multi":
            accounts = r.data.get("accounts", [])
            text += f"  └ Аккаунтов: {len(accounts)}\n"
            for a in accounts[:15]:
                text += f"     • {a.get('url')}\n"

        elif r.source == "tme_preview":
            d = r.data
            text += f"  ├ Имя: {d.get('name', '—')}\n"
            text += f"  ├ Bio: {d.get('bio', '—')}\n"
            text += f"  └ Фото: {d.get('photo', '—')}\n"

        elif r.source == "ipinfo":
            d = r.data
            text += f"  ├ IP: {d.get('ip')}\n"
            text += f"  ├ Город: {d.get('city', '—')}\n"
            text += f"  ├ Страна: {d.get('country', '—')}\n"
            text += f"  └ Провайдер: {d.get('org', '—')}\n"

        elif r.source == "gravatar":
            text += f"  └ Профиль: {json.dumps(r.data)[:150]}\n"

        elif r.source == "whatsapp":
            text += f"  └ Зарегистрирован: {r.data.get('registered')}\n"

        elif r.source == "dns":
            text += f"  └ Hostname: {r.data.get('hostname')}\n"

        elif r.source == "github":
            text += f"  └ Найдено: {r.data.get('total', 0)}\n"

        else:
            text += f"  └ {json.dumps(r.data, ensure_ascii=False)[:200]}\n"

        text += "\n"

    return text[:4000]


# ===== HANDLERS =====
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await msg.answer(
        "🔍 <b>OSINT Bot</b>\n\n"
        "Отправь мне запрос:\n"
        "• <code>@username</code> — поиск по сайтам\n"
        "• <code>user@mail.ru</code> — email\n"
        "• <code>+79991234567</code> — номер\n"
        "• <code>8.8.8.8</code> — IP\n"
        "• <code>@durov</code> — Telegram\n"
    )


@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    await cmd_start(msg)


@dp.message(F.text)
async def handle_query(msg: types.Message):
    query = msg.text.strip()
    if query.startswith("/"):
        return

    status = await msg.answer("🔎 <i>Пробиваю...</i>")

    try:
        results = await OSINTEngine.run(query)
        text = format_results(results, query)

        db.add_request(msg.from_user.id, query, OSINTEngine.detect_type(query),
                       json.dumps([r.data for r in results], ensure_ascii=False, default=str))

        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await msg.answer(text[i:i+4096])
        else:
            await status.edit_text(text)

    except Exception as e:
        logger.exception(e)
        await status.edit_text(f"❌ <b>Ошибка:</b> <code>{e}</code>")


# ===== ЗАПУСК =====
async def main():
    logger.info("OSINT Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
