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
import difflib
from aiohttp import web

# Apply nest_asyncio for environments like Jupyter
nest_asyncio.apply()

# Load environment variables
load_dotenv()

# Configuration
TOKEN = os.getenv('TOKEN')
DB_URL = os.getenv('DB_URL')
SEARCH_GROUP_ID = int(os.getenv('SEARCH_GROUP_ID'))
STORAGE_GROUP_ID = int(os.getenv('STORAGE_GROUP_ID'))
ADMIN_ID = int(os.getenv('ADMIN_ID'))
PORT = int(os.getenv('PORT', 8080))  # Default to 8080 if not set

# Logging Configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# MongoDB Client Setup
def connect_mongo():
    retries = 5
    while retries > 0:
        try:
            client = MongoClient(DB_URL, serverSelectionTimeoutMS=5000)
            db = client['MoviesDB']
            collection = db['Movies']
            client.admin.command('ping')
            logging.info("MongoDB connection established.")
            return collection
        except errors.ServerSelectionTimeoutError as e:
            logging.error(f"MongoDB connection failed. Retrying... {e}")
            retries -= 1
            time.sleep(5)
    logging.critical("Failed to connect to MongoDB.")
    return None

collection = connect_mongo()
search_group_messages = []

# Handlers
async def start(update: Update, context: CallbackContext):
    """Handle the /start command."""
    user_name = update.effective_user.full_name or "there"
    keyboard = [[InlineKeyboardButton("Add me to your chat! 🤖", url="https://t.me/+ERz0bGWEHHBmNTU9")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        text=f"Hi {user_name}! 👋 Use me to search or upload movies. 🎥",
        reply_markup=reply_markup
    )

async def add_movie(update: Update, context: CallbackContext):
    """Add a movie to the database from the storage group."""
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
        await update.message.reply_text("❌ Use this feature in the designated search group.")
        return

    movie_name = update.message.text.strip()
    if not movie_name:
        await update.message.reply_text("🚨 Provide a movie name to search. Use /search <movie_name>")
        return

    try:
        # Search in the database
        regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
        results = list(collection.find({"name": {"$regex": regex_pattern}}).limit(10))

        if results:
            await update.message.reply_text(
                f"🔍 **Found {len(results)} result(s) for '{movie_name}':**",
                parse_mode="Markdown"
            )
            for result in results:
                name = result.get('name', 'Unknown Movie')
                file_id = result.get('file_id')
                if file_id:
                    try:
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=file_id,
                            caption=f"🎥 **{name}**",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logging.error(f"Error sending file for {name}: {e}")
                        await update.message.reply_text(f"🎥 **{name}**", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"🎥 **{name}**", parse_mode="Markdown")
        else:
            await suggest_movies(update, movie_name)

    except Exception as e:
        logging.error(f"Search error: {e}")
        await update.message.reply_text("❌ An unexpected error occurred. Please try again later.")

async def suggest_movies(update: Update, movie_name: str):
    """Provide suggestions for movie names."""
    try:
        suggestions = list(
            collection.find({"name": {"$regex": f".*{movie_name[:3]}.*", "$options": "i"}}).limit(5)
        )
        if suggestions:
            suggestion_text = "\n".join([f"- {s['name']}" for s in suggestions])
            await update.message.reply_text(
                f"😔 Movie not found. Did you mean:\n{suggestion_text}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("🤷‍♂️ No suggestions available. Try a different term.")
    except Exception as e:
        logging.error(f"Error in suggesting movies: {e}")
        await update.message.reply_text("❌ Error in generating suggestions.")

async def welcome_new_member(update: Update, context: CallbackContext):
    """Welcome new members to the group."""
    for new_member in update.message.new_chat_members:
        user_name = new_member.full_name or new_member.username or "Movie Fan"
        welcome_text = (
            f"Welcome, {user_name}! 🎬\n\n"
            "Search for any movie by typing its title. Easy as that! 🍿\n"
            "Enjoy exploring films with us! 🎥"
        )
        await update.message.reply_text(welcome_text)

async def delete_old_messages(application):
    """Delete messages in the search group that are older than 24 hours."""
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            # Set threshold to 24 hours (86400 seconds)
            threshold_seconds = 86400  # 24 hours
            to_delete = [
                msg for msg in search_group_messages
                if (now - msg["time"]).total_seconds() > threshold_seconds
            ]
            for message in to_delete:
                try:
                    await application.bot.delete_message(
                        chat_id=message["chat_id"], 
                        message_id=message["message_id"]
                    )
                    search_group_messages.remove(message)
                except Exception as e:
                    logging.warning(f"Failed to delete message: {e}")
            # Check every 1 hour (3600 seconds)
            await asyncio.sleep(3600)
        except Exception as e:
            logging.error(f"Error in delete_old_messages: {e}")
            await asyncio.sleep(10)

async def start_web_server():
    """Start a web server for health checks."""
    async def handle_health(request):
        return web.Response(text="Bot is running")

    app = web.Application()
    app.router.add_get('/', handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f"Web server running on port {PORT}")

async def main():
    """Main function to start the bot."""
    try:
        await start_web_server()

        application = ApplicationBuilder().token(TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.Document.ALL, add_movie))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_movie))
        application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))

        delete_task = asyncio.create_task(delete_old_messages(application))
        await application.run_polling()

    except Exception as e:
        logging.error(f"Main loop error: {e}")
    finally:
        logging.info("Shutting down bot...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped manually.")
    except Exception as e:
        logging.error(f"Unexpected error in main block: {e}")
