"""
checker.py
Modul pengecekan status username Telegram.

Status yang dikembalikan:
- AVAILABLE : username kosong, bisa langsung diklaim
- FRAGMENT  : username terdaftar/dilelang di fragment.com (butuh beli via TON)
- TAKEN     : username sudah dipakai orang lain
- BANNED    : diduga kena banned Telegram (heuristik, BUKAN 100% pasti --
              Telegram tidak punya flag resmi "banned" di API publik)
- INVALID   : format username tidak valid (terlalu pendek / karakter aneh)
- ERROR     : gagal dicek (network/API error), lihat pesan error di bot.py

CATATAN PENTING:
`account.checkUsername` (versi sebelumnya) di-restrict Telegram khusus untuk
akun user asli -- bot token akan selalu kena `BotMethodInvalidError` kalau
manggil method itu. Jadi versi ini pakai `contacts.resolveUsername`, yang
BOLEH dipanggil oleh bot, dengan alur:

1. resolveUsername -> berhasil resolve (ada peer) => TAKEN
2. resolveUsername -> UsernameNotOccupiedError => berarti bebas dari sisi
   Telegram, tapi belum tentu "available" beneran -- masih perlu dicek ke
   fragment.com dulu:
      - kalau ketemu di fragment.com (listing/lelang) => FRAGMENT
      - kalau tidak ketemu sama sekali => AVAILABLE
3. resolveUsername -> UsernameInvalidError padahal format lolos regex
   => heuristik BANNED

Strategi anti-flood tetap sama: banyak worker (bot token) round-robin,
delay + jitter tiap request, dan auto-cooldown per-worker kalau kena
FloodWaitError.
"""

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import fragment
from fragment.errors import FragmentHTTPError, ParserError
from telethon import TelegramClient
from telethon.errors import FloodWaitError, UsernameInvalidError, UsernameNotOccupiedError
from telethon.tl.functions.contacts import ResolveUsernameRequest

# Aturan format username Telegram: 5-32 karakter, huruf/angka/underscore,
# tidak boleh diawali angka.
USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{4,31}$")


class Status:
    AVAILABLE = "available"
    FRAGMENT = "fragment"
    TAKEN = "taken"
    BANNED = "banned"
    INVALID = "invalid"
    ERROR = "error"


@dataclass
class Worker:
    """Satu sesi Telethon (login via bot token)."""

    client: TelegramClient
    name: str
    cooldown_until: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def is_ready(self) -> bool:
        return time.time() >= self.cooldown_until


class UsernamePool:
    """Kumpulan worker supaya beban & flood-wait terbagi rata."""

    def __init__(self, workers: list[Worker], min_delay: float = 1.2):
        if not workers:
            raise ValueError("Butuh minimal 1 worker/bot token.")
        self.workers = workers
        self.min_delay = min_delay
        self._rr = 0  # index round-robin

    async def start(self):
        await asyncio.gather(*(w.client.connect() for w in self.workers))

    def _pick_worker(self) -> Optional[Worker]:
        n = len(self.workers)
        for i in range(n):
            idx = (self._rr + i) % n
            w = self.workers[idx]
            if w.is_ready():
                self._rr = (idx + 1) % n
                return w
        return None

    async def check(self, username: str, _depth: int = 0) -> str:
        username = username.lstrip("@").strip()

        if not USERNAME_RE.match(username):
            return Status.INVALID

        if _depth > len(self.workers) + 2:
            # semua worker lagi cooldown lama sekali, cegah rekursi tak berujung
            return Status.ERROR

        worker = self._pick_worker()
        while worker is None:
            await asyncio.sleep(1)
            worker = self._pick_worker()

        not_occupied = False

        async with worker.lock:
            await asyncio.sleep(self.min_delay + random.uniform(0, 0.6))
            try:
                await worker.client(ResolveUsernameRequest(username))
                return Status.TAKEN
            except UsernameNotOccupiedError:
                not_occupied = True
            except UsernameInvalidError:
                # Format lolos regex tapi Telegram bilang invalid.
                # Kandidat kuat: username kena banned Telegram.
                return Status.BANNED
            except FloodWaitError as e:
                worker.cooldown_until = time.time() + e.seconds + 2
                return await self.check(username, _depth + 1)

        # Fragment.com dicek DI LUAR lock worker, supaya worker langsung
        # bebas ngecek username lain -- nggak nunggu HTTP call ke fragment.com
        # yang bisa makan waktu beberapa detik.
        if not_occupied:
            is_listed = await check_fragment_listed(username)
            return Status.FRAGMENT if is_listed else Status.AVAILABLE


async def check_fragment_listed(username: str) -> bool:
    """
    Cek apakah username ini "milik" fragment.com (lagi dilelang, sudah
    kejual/resale, dsb) -- pakai library python-fragment yang parsing
    halamannya secara proper (bukan tebak dari og:title kayak sebelumnya).

    Logikanya: kalau fragment.com PUNYA data soal username ini (apapun
    status-nya -- auction/sale/sold/dsb), berarti ini "milik" Fragment
    dan harus dibeli lewat sana, bukan bisa langsung diklaim di Telegram.
    Kalau fragment.com nggak punya data sama sekali (page nggak ke-parse /
    404), berarti murni available.
    """
    try:
        async with fragment.AsyncClient() as client:
            info = await client.username_info(username)
        return bool(info.get("status"))
    except (FragmentHTTPError, ParserError):
        return False
    except Exception:
        # kalau fragment.com error/timeout, jangan gagalkan seluruh cek --
        # anggap saja tidak listed (fallback ke available)
        return False
