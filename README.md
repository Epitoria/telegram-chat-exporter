# Telegram Chat Exporter

A fast, standalone desktop app that exports any Telegram chat to a self-contained **HTML file** (Telegram Desktop style) or **JSON**, including photos, videos, voice messages, stickers, files, reactions, and call history.

![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

---

## Features

- 🌐 **HTML export** — opens in any browser, looks exactly like Telegram Desktop's built-in export
- 📄 **JSON export** — structured, machine-readable data for scripting or archiving
- ⚡ **Fast** — producer/consumer pipeline, batch size 200 (API max), parallel downloads, sync fast-path for text messages
- ▶️ **Resume support** — skipped already-downloaded files if re-run after a crash
- 📷 Photos, 🎬 Videos, 🎙 Voice messages, ⭕ Video messages, 🎭 Stickers, 🎞 GIFs, 📎 Files
- 👤 Profile pictures, 😀 Reactions, ↩️ Replies, 📨 Forwarded messages, 📞 Calls
- 🌙 Dark mode support in HTML output
- 🖥️ Clean GUI with progress bar, live log, and per-chat export

---

## Requirements

```
pip install telethon python-dotenv pillow
```

Optional (faster JSON output):
```
pip install ujson
```

---

## Setup

### 1. Get your Telegram API credentials

1. Go to [https://my.telegram.org](https://my.telegram.org) and log in
2. Click **API development tools**
3. Create an app — note your **API ID** and **API Hash**

### 2. Configure (optional)

Create a `.env` file next to the script to pre-fill credentials:

```env
API_ID=12345678
API_HASH=your_api_hash_here
PHONE=+9647801234567
```

### 3. Run

```bash
python export.py
```

---

## Usage

1. Enter your **API ID**, **API Hash**, and **Phone number**
2. Click **Connect & Load Chats** — you'll be asked for your verification code on first run (the session is saved locally so you only do this once)
3. Select a chat from the dropdown
4. Choose **HTML** or **JSON** format
5. Pick which media types to include and set the max file size limit
6. Click **▶ Export**

The output folder will contain:
```
messages.html / messages.json
photos/
video_files/
files/
css/
js/
images/
```

Open `messages.html` directly in your browser — no server needed.

---

## Performance tips

- **Parallel DLs slider** — raise to 10–15 on a fast connection; lower to 2–3 if you hit Telegram flood-wait errors
- **Msg limit** — set to e.g. `5000` to export only the most recent messages
- Text-only chats export very fast (messages are rendered synchronously with no task overhead)
- Re-running an export over the same output folder skips already-downloaded files instantly

---

## Notes

- This tool uses the **Telegram user API** (MTProto), not the bot API — it can export any chat you're a member of
- Your session file (`tg_exporter.session`) is stored locally; never share it
- Telegram may rate-limit heavy exports — the app handles flood-wait errors automatically

---

## License

MIT
