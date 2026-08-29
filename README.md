# Telegram Username Checker Bot

Cek status username Telegram: **available**, **fragment** (dilelang), **taken**,
atau **banned** (heuristik) — dirancang buat cek banyak username sekaligus
(sampai 500) tanpa kena flood wait.

## ⚠️ Wajib sebelum jalan
Kalau kamu pernah nempel bot token langsung di source code (hardcoded), token
itu dianggap bocor. **Revoke semua token lama lewat @BotFather** (`/token` ->
pilih bot -> Revoke), lalu masukkan token yang baru ke `.env`, bukan ke kode.

## Setup

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Isi `.env`:
- `API_ID` & `API_HASH` — daftar gratis di https://my.telegram.org (API Development Tools)
- `BOT_TOKENS` — daftar token bot dari @BotFather, dipisah koma

Jalankan:
```bash
python bot.py
```

Pertama kali jalan, tiap worker akan bikin session Telethon di folder
`sessions/` (auto dibuat). Setelah itu bot langsung polling dan siap dipakai.

## Kenapa ini lebih tahan flood wait dibanding versi lama

| Versi lama | Versi ini |
|---|---|
| Cek status pakai scraping `og:title` fragment.com | Cek status pakai `account.checkUsername` (MTProto resmi), Fragment cuma jadi sinyal tambahan |
| 1 balasan Telegram per username (500 username = 500 pesan berturut-turut) | 1 pesan progress yang di-*edit*, + beberapa pesan ringkasan di akhir |
| `requests.get()` sinkron di dalam handler async -> nge-*block* seluruh event loop | Semua HTTP/MTProto call full async, jalan paralel antar-worker |
| Semua worker/bot jalan tanpa pembagian beban terkoordinasi | Request dibagi round-robin ke banyak worker; kalau satu kena `FloodWaitError`, otomatis didinginkan dan yang lain lanjut |

## Cara baca hasil

```
✅ Selesai cek 500 username
Taken: 454

Available (12):
@a @b @c ...

Frag & Banned (34):
@d @e @f ...
```

- **Available** — bisa langsung diklaim.
- **Frag & Banned** digabung sengaja, karena keduanya = "coret dari list lama
  kamu" (satu karena harus dibeli via Fragment, satu karena kena banned).
- **Taken** cuma dihitung karena biasanya paling banyak dan paling gak actionable.

## Catatan akurasi
- Status **banned** itu heuristik: Telegram tidak expose flag resmi "banned"
  lewat API publik. Yang dideteksi adalah "format username valid, tapi
  `checkUsername` bilang invalid" — pola ini paling sering cocok dengan
  username yang kena banned, tapi bukan jaminan 100%.
- Kalau mau tambahan info harga/link auction Fragment untuk yang berstatus
  `fragment`, ada fungsi `check_fragment_price()` di `checker.py` yang belum
  dipanggil di `bot.py` — tinggal panggil di `handle_message` kalau perlu.

## Batasan
- `MAX_PER_BATCH` di `bot.py` (default 500) membatasi jumlah username per
  kiriman supaya tidak berlebihan; ubah sesuai kebutuhan.
- Semakin banyak token bot di `BOT_TOKENS`, semakin cepat 500 username selesai
  dicek (beban terbagi rata).
