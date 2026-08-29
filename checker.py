"""
checker.py
Modul pengecekan status username Telegram.

Status yang dikembalikan:
- AVAILABLE : username kosong, bisa langsung diklaim
- FRAGMENT  : username sedang dilelang di fragment.com (butuh beli via TON)
- TAKEN     : username sudah dipakai orang lain
- BANNED    : diduga kena banned Telegram (heuristik, BUKAN 100% pasti —
              Telegram tidak punya flag resmi "banned" di API publik)
- INVALID   : format username tidak valid (terlalu pendek / karakter aneh)

Kenapa ini lebih tahan flood-wait dibanding versi resolveUsername biasa:
1. Pakai account.checkUsername, bukan contacts.resolveUsername.
   checkUsername didesain untuk dipanggil berulang kali (dipakai live saat
   user mengetik username baru di menu Settings Telegram), jadi limitnya
   jauh lebih longgar.
2. Beban dibagi ke banyak "worker" (bisa banyak bot token) secara round robin.
3. Kalau satu worker kena FloodWaitError, dia didinginkan (cooldown) dan
   request otomatis dialihkan ke worker lain — bukan bikin seluruh bot stuck.
4. Ada delay + jitter kecil supaya pola request tidak terlalu mekanis.
"""

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from telethon import TelegramClient
from telethon.errors import FloodWaitError, UsernameInvalidError
from telethon.tl.functions.account import CheckUsernameRequest

# Aturan format username Telegram: 5-32 karakter, huruf/angka/underscore,
# tidak boleh diawali angka.
USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{4,31}$")


class Status:
    AVAILABLE = "available"
    FRAGMENT = "fragment"
    TAKEN = "taken"
    BANNED = "banned"
    INVALID = "invalid"


@dataclass
class Worker:
    """Satu sesi Telethon (biasanya login via bot token)."""

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
            return Status.INVALID

        worker = self._pick_worker()
        while worker is None:
            await asyncio.sleep(1)
            worker = self._pick_worker()

        async with worker.lock:
            await asyncio.sleep(self.min_delay + random.uniform(0, 0.6))
            try:
                is_free = await worker.client(CheckUsernameRequest(username))
                return Status.AVAILABLE if is_free else Status.TAKEN
            except UsernameInvalidError:
                # Format lolos regex tapi Telegram bilang invalid.
                # Kandidat kuat: username kena banned Telegram.
                return Status.BANNED
            except FloodWaitError as e:
                worker.cooldown_until = time.time() + e.seconds + 2
                return await self.check(username, _depth + 1)
            except Exception as e:
                if "USERNAME_PURCHASE_AVAILABLE" in str(e):
                    return Status.FRAGMENT
                raise


async def check_fragment_price(username: str) -> Optional[str]:
    """
    Cek best-effort apakah username sedang dilelang di fragment.com.
    Endpoint ini TIDAK resmi dan Fragment cukup sering ubah struktur HTML,
    jadi anggap ini info tambahan/fallback, bukan sumber utama.
    """
    url = f"https://fragment.com/username/{username}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, follow_redirects=True)
        if r.status_code != 200:
            return None
        if "tm-section-title" in r.text and ("USD" in r.text or "TON" in r.text):
            return "listed_on_fragment"
        return None
    except Exception:
        return None
