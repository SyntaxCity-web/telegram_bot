# 🎬 Telegram Movie Bot

A feature-rich Telegram bot built in Python to manage, search, and share movies in groups and private chats. Supports uploading files with poster images, deep-linking previews, and file delivery via PM.

---

## 🚀 Features

- 📤 **Upload Movies**  
  Upload movie files and poster images to a designated **storage group**. The bot sanitizes file names and organizes them neatly.

- 🔍 **Search Functionality**  
  Search for uploaded movies in the **search group**, with inline buttons and deep-link previews.

- 📥 **Private Downloads**  
  Users can download movie files via bot PM using deep links generated in the search group.

- 🧠 **Smart Filename Cleanup**  
  Strips unnecessary tags like quality, source, format, etc., to auto-generate clean movie titles.

- 💾 **MongoDB Integration**  
  Stores movie metadata and media details in a persistent MongoDB collection.

- 🌐 **Web Health Check**  
  aiohttp server for deployment environments like Koyeb, Render, or Heroku.

- 🔁 **Self Keep-Alive Pings**  
  Uses `aiocron` to ping itself every 5 minutes to prevent downtime on free hosting platforms.

- 🕒 **Timezone-aware Logging**  
  Logs events in Indian Standard Time (IST) using a custom logging formatter.

---

## 🛠 Requirements

- Python 3.10+
- MongoDB URI (local or cloud)
- Telegram Bot Token
- Python packages (see below)

### 📦 Install Dependencies

```bash
pip install -r requirements.txt
