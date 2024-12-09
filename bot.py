import logging
import re
import datetime
import asyncio
import time
from pymongo import MongoClient, errors
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackContext, filters
from dotenv import load_dotenv
import os
import nest_asyncio

# Enable nest_asyncio to handle nested event loops (useful in environments like Jupyter)
nest_asyncio.apply()

# Load environment variables
load_dotenv()

# Configuration variables from .env
TOKEN = os.getenv('TOKEN')
DB_URL = os.getenv('DB_URL')
SEARCH_GROUP_ID = int(os.getenv('SEARCH_GROUP_ID'))
STORAGE_GROUP_ID = int(os.getenv('STORAGE_GROUP_ID'))
ADMIN_ID = int(os.getenv('ADMIN_ID'))
PORT = int(os.getenv('PORT', 8080))  # Default to 8080 if not set

# MongoDB client setup
def connect_mongo():
    retries = 5
    while retries > 0:
        try:
            client = MongoClient(DB_URL)
            db = client['MoviesDB']
            collection = db['Movies']
            logging.info("MongoDB connection successful.")
            return collection
        except errors.PyMongoError as e:
            logging.error(f"MongoDB connection failed. Retrying... {e}")
            retries -= 1
            time.sleep(5)
    logging.critical("Failed to connect to MongoDB after retries.")
    return None

collection = connect_mongo()

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

search_group_messages = []

async def start(update: Update, context: CallbackContext):
    """Handle the /start command."""
    user_name = update.effective_user.full_name or "there"
    keyboard = [[InlineKeyboardButton("Add me to your chat! 🤖", url="https://t.me/+ERz0bGWEHHBmNTU9")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        text=f"Hi {user_name}! 👋 Use me to search or upload movies. 🎥", reply_markup=reply_markup
    )

async def add_movie(update: Update, context: CallbackContext):
    """Add a movie to the database when uploaded in the storage group."""
    if update.effective_chat.id != STORAGE_GROUP_ID:
        await update.message.reply_text("You can only upload movies in the designated storage group. 🎥")
        return

    file_info = update.message.document
    if file_info:
        movie_name = file_info.file_name
        file_id = file_info.file_id
        collection.insert_one({"name": movie_name, "file_id": file_id})
        await context.bot.send_message(chat_id=STORAGE_GROUP_ID, text=f"Added movie: {movie_name}")

async def search_movie(update: Update, context: CallbackContext):
    """Search for a movie in the database."""
    if update.effective_chat.id != SEARCH_GROUP_ID:
        await update.message.reply_text("Use this feature in the search group. 🔍")
        return

    movie_name = update.message.text.strip()
    if not movie_name:
        await update.message.reply_text("Please enter a valid movie name. 🎬")
        return

    regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
    results = list(collection.find({"name": {"$regex": regex_pattern}}))

    if results:
        for result in results:
            await update.message.reply_text(f"Found movie: {result['name']} 🎥")
            await context.bot.send_document(chat_id=update.effective_chat.id, document=result['file_id'])
    else:
        await update.message.reply_text("Movie not found. 😔 Try a different search.")

async def delete_old_messages(application: ApplicationBuilder):
    """Delete messages in the search group that are older than 24 hours."""
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            to_delete = [
                msg for msg in search_group_messages
                if (now - msg["time"]).total_seconds() > 86400
            ]
            for message in to_delete:
                await application.bot.delete_message(chat_id=message["chat_id"], message_id=message["message_id"])
                search_group_messages.remove(message)
            await asyncio.sleep(3600)
        except Exception as e:
            logging.error(f"Error in delete_old_messages: {e}")

async def welcome_new_member(update: Update, context: CallbackContext):
    """Welcome new members to the search group."""
    for member in update.message.new_chat_members:
        await context.bot.send_message(chat_id=SEARCH_GROUP_ID, text=f"Welcome {member.full_name}! 🎉 Ask for a movie name, and I'll help you find it.")

async def handle_text_message(update: Update, context: CallbackContext):
    """Handle text messages in the search group."""
    await search_movie(update, context)

async def start_web_server():
    """Start a simple web server for health checks."""
    from aiohttp import web
    async def handle_health(request):
        return web.Response(text="Bot is running")
    app = web.Application()
    app.router.add_get('/', handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info("Web server started on port %d", PORT)

async def main():
    """Main function to start the bot."""
    await start_web_server()
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.ALL, add_movie))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    asyncio.create_task(delete_old_messages(application))

    # Await initialize() and shutdown() properly
    await application.initialize()
    await application.run_polling()
    await application.shutdown()

# Check if the event loop is already running
if __name__ == "__main__":
    try:
        asyncio.run(main())  # Will work if no loop is running
    except RuntimeError as e:
        if 'This event loop is already running' in str(e):
            # If the event loop is already running (such as in Jupyter), use await instead
            import nest_asyncio
            nest_asyncio.apply()
            asyncio.run(main())
