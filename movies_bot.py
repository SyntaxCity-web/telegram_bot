import logging
import re
import datetime
import asyncio
import os
from pymongo import MongoClient, errors
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackContext, filters
)
import aiohttp
from aiohttp import web
import psutil

# Configuration variables
TOKEN = os.getenv('TOKEN')
DB_URL = os.getenv('DB_URL')
SEARCH_GROUP_ID = int(os.getenv('SEARCH_GROUP_ID', 0))
STORAGE_GROUP_ID = int(os.getenv('STORAGE_GROUP_ID', 0))
PORT = int(os.getenv('PORT', 8080))  # Default to 8080 if not set

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG  # Switch to DEBUG for detailed logs
)

# Global variables
search_group_messages = []

# MongoDB client setup with retries
def connect_mongo():
    retries = 5
    while retries > 0:
        try:
            client = MongoClient(DB_URL, serverSelectionTimeoutMS=5000)
            db = client['MoviesDB']
            collection = db['Movies']
            client.server_info()  # Test connection
            logging.info("MongoDB connection successful.")
            return collection
        except errors.PyMongoError as e:
            logging.error(f"MongoDB connection failed. Retrying... {e}")
            retries -= 1
            asyncio.sleep(5)
    logging.critical("Failed to connect to MongoDB after retries.")
    return None

collection = connect_mongo()

# Helper function to check if a chat is the search group
def is_in_group(chat_id, group_id):
    return chat_id == group_id

# Health check endpoint
async def handle_webhook(request):
    try:
        client.server_info()  # Test MongoDB connection
        return web.Response(text="Bot and MongoDB are running.")
    except errors.PyMongoError as e:
        logging.error(f"MongoDB health check failed: {e}")
        return web.Response(text="Bot is running, but MongoDB is down.", status=500)

# Background task to monitor event loop health
async def monitor_event_loop():
    while True:
        try:
            logging.info("Bot is active and running.")
            await asyncio.sleep(3600)  # Log every hour
        except Exception as e:
            logging.error(f"Error in event loop monitor: {e}")
            await asyncio.sleep(10)  # Retry after failure

# Background task to monitor resource usage
async def monitor_resources():
    while True:
        try:
            process = psutil.Process()
            memory = process.memory_info().rss / 1024 ** 2  # Memory in MB
            cpu = process.cpu_percent(interval=1)
            logging.debug(f"Resource Usage: Memory = {memory:.2f} MB, CPU = {cpu:.2f}%")
            await asyncio.sleep(600)  # Log every 10 minutes
        except Exception as e:
            logging.error(f"Error monitoring resources: {e}")

# Command handler: /start
async def start(update: Update, context: CallbackContext):
    try:
        user_name = update.effective_user.full_name or "there"
        keyboard = [[InlineKeyboardButton("Add me to your chat! 🤖", url="https://t.me/+ERz0bGWEHHBmNTU9")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        welcome_message = (
            f"Hi {user_name}! 👋 I'm Olive, your group assistant. 🎉\n"
            "Use me to search for movies, or upload a movie to the storage group! 🎥"
        )
        await update.message.reply_text(text=welcome_message, reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"Error in /start command: {e}")
        await update.message.reply_text("Oops! Something went wrong. 😕 Please try again later.")

# Command handler: Add movie
async def add_movie(update: Update, context: CallbackContext):
    try:
        if update.effective_chat.id != STORAGE_GROUP_ID:
            await update.message.reply_text("You can only upload movies in the designated storage group. 🎥")
            return

        file_info = update.message.document
        if file_info:
            movie_name = file_info.file_name.strip()
            file_id = file_info.file_id
            collection.insert_one({"name": movie_name, "file_id": file_id})
            await context.bot.send_message(chat_id=STORAGE_GROUP_ID, text=f"Added movie: {movie_name}")
    except Exception as e:
        logging.error(f"Error in add_movie: {e}")
        await update.message.reply_text("An error occurred while adding the movie. 😕")

# Command handler: Search movie
async def search_movie(update: Update, context: CallbackContext):
    try:
        if not is_in_group(update.effective_chat.id, SEARCH_GROUP_ID):
            await update.message.reply_text("Use this feature in the search group. 🔍")
            return

        movie_name = update.message.text.strip()
        if not movie_name:
            msg = await update.message.reply_text("Please enter a valid movie name. 🎬")
            search_group_messages.append({"chat_id": msg.chat_id, "message_id": msg.message_id, "time": datetime.datetime.utcnow()})
            return

        regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
        results = list(collection.find({"name": {"$regex": regex_pattern}}))

        if results:
            for result in results:
                msg = await update.message.reply_text(f"Found movie: {result['name']} 🎥")
                await context.bot.send_document(chat_id=update.effective_chat.id, document=result['file_id'])
                search_group_messages.append({"chat_id": msg.chat_id, "message_id": msg.message_id, "time": datetime.datetime.utcnow()})
        else:
            msg = await update.message.reply_text("Movie not found. 😔 Try a different search.")
            search_group_messages.append({"chat_id": msg.chat_id, "message_id": msg.message_id, "time": datetime.datetime.utcnow()})
    except Exception as e:
        logging.error(f"Error searching movie: {e}")
        await update.message.reply_text("Oops! Something went wrong. 😕 Please try again later.")

# Background task to delete old messages
async def delete_old_messages(application):
    while True:
        try:
            now = datetime.datetime.utcnow()
            to_delete = [
                msg for msg in search_group_messages if (now - msg["time"]).total_seconds() > 86400
            ]
            for msg in to_delete:
                try:
                    await application.bot.delete_message(chat_id=msg["chat_id"], message_id=msg["message_id"])
                    search_group_messages.remove(msg)
                except Exception as e:
                    logging.error(f"Failed to delete message {msg['message_id']}: {e}")
            await asyncio.sleep(3600)
        except Exception as e:
            logging.error(f"Error in delete_old_messages: {e}")

# Web server
async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f"Web server started on port {PORT}")
    return runner

# Main function
async def main():
    try:
        web_runner = await start_web_server()
        asyncio.create_task(monitor_event_loop())
        asyncio.create_task(monitor_resources())

        application = ApplicationBuilder().token(TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.Document.ALL, add_movie))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_movie))

        asyncio.create_task(delete_old_messages(application))

        await application.initialize()
        await application.start()
        await application.updater.start_polling()

        logging.info("Bot is running.")
        await asyncio.Event().wait()
    except Exception as e:
        logging.error(f"Error in main function: {e}")
    finally:
        logging.info("Shutting down...")
        await application.stop()
        await application.shutdown()
        await web_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
