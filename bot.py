import logging
import re
import datetime
import asyncio
import time
import backoff
import os

from pymongo import MongoClient, errors
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackContext, filters
from telegram.error import NetworkError, TimedOut
from dotenv import load_dotenv

# Apply nest_asyncio for nested event loops
import nest_asyncio
nest_asyncio.apply()

# Load environment variables
load_dotenv()

# Configuration variables from .env
TOKEN = os.getenv('TOKEN')
DB_URL = os.getenv('DB_URL')
SEARCH_GROUP_ID = int(os.getenv('SEARCH_GROUP_ID'))
STORAGE_GROUP_ID = int(os.getenv('STORAGE_GROUP_ID'))
ADMIN_ID = int(os.getenv('ADMIN_ID'))
PORT = int(os.getenv('PORT', 8080))

# Logging setup with more detailed configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def connect_mongo():
    """Establish MongoDB connection with enhanced error handling."""
    retries = 5
    while retries > 0:
        try:
            client = MongoClient(DB_URL, 
                                 serverSelectionTimeoutMS=5000, 
                                 connectTimeoutMS=5000)
            db = client['MoviesDB']
            collection = db['Movies']
            client.admin.command('ping')
            logger.info("MongoDB connection successful.")
            return collection
        except errors.ServerSelectionTimeoutError as e:
            logger.error(f"MongoDB connection failed. Retrying... {e}")
            retries -= 1
            time.sleep(5)
    logger.critical("Failed to connect to MongoDB after multiple attempts.")
    return None

# Global variables
collection = connect_mongo()
search_group_messages = []

async def start(update: Update, context: CallbackContext):
    """Handle the /start command with enhanced user experience."""
    try:
        user_name = update.effective_user.full_name or "there"
        keyboard = [
            [InlineKeyboardButton("Add me to your chat! 🤖", url="https://t.me/+ERz0bGWEHHBmNTU9")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            text=f"Hi {user_name}! 👋 Use me to search or upload movies. 🎥", 
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in start command: {e}")

async def add_movie(update: Update, context: CallbackContext):
    """Enhanced movie addition with validation and error handling."""
    try:
        if update.effective_chat.id != STORAGE_GROUP_ID:
            await update.message.reply_text("You can only upload movies in the designated storage group. 🎥")
            return

        file_info = update.message.document
        if file_info:
            # Basic file validation
            max_file_size = 2 * 1024 * 1024 * 1024  # 2GB limit
            if file_info.file_size > max_file_size:
                await update.message.reply_text("File too large. Maximum 2GB allowed.")
                return

            movie_name = file_info.file_name
            file_id = file_info.file_id
            
            # Check for duplicate
            existing = collection.find_one({"name": movie_name})
            if existing:
                await update.message.reply_text(f"Movie {movie_name} already exists.")
                return

            collection.insert_one({
                "name": movie_name, 
                "file_id": file_id, 
                "uploaded_at": datetime.datetime.now(),
                "uploader_id": update.effective_user.id
            })
            await context.bot.send_message(
                chat_id=STORAGE_GROUP_ID, 
                text=f"Added movie: {movie_name}"
            )
    except Exception as e:
        logger.error(f"Error adding movie: {e}")
        await update.message.reply_text("An error occurred while adding the movie.")

async def search_movie(update: Update, context: CallbackContext):
    """Enhanced movie search with more robust search and result handling."""
    try:
        if update.effective_chat.id != SEARCH_GROUP_ID:
            await update.message.reply_text("Use this feature in the search group. 🔍")
            return

        movie_name = update.message.text.strip()
        
        # More flexible search with multiple matching strategies
        search_patterns = [
            re.compile(re.escape(movie_name), re.IGNORECASE),  # Exact match
            re.compile(movie_name, re.IGNORECASE),  # Partial match
        ]

        results = []
        for pattern in search_patterns:
            results.extend(list(collection.find({"name": {"$regex": pattern}})))

        # Remove duplicates
        results = list({result['name']: result for result in results}.values())

        if results:
            for result in results[:5]:  # Limit to 5 results
                await update.message.reply_text(f"Found movie: {result['name']} 🎥")
                await context.bot.send_document(
                    chat_id=update.effective_chat.id, 
                    document=result['file_id']
                )
            
            if len(results) > 5:
                await update.message.reply_text(f"... and {len(results) - 5} more results.")
        else:
            await update.message.reply_text("Movie not found. 😔 Try a different search.")

    except Exception as e:
        logger.error(f"Search error: {e}")
        await update.message.reply_text("An error occurred during the search.")

async def delete_old_messages(application):
    """Improved message cleanup with more robust error handling."""
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            to_delete = [
                msg for msg in search_group_messages
                if (now - msg["time"]).total_seconds() > 86400
            ]
            
            for message in to_delete:
                try:
                    await application.bot.delete_message(
                        chat_id=message["chat_id"], 
                        message_id=message["message_id"]
                    )
                    search_group_messages.remove(message)
                except Exception as e:
                    logger.warning(f"Failed to delete message: {e}")
            
            await asyncio.sleep(3600)
        
        except Exception as e:
            logger.error(f"Error in delete_old_messages: {e}")
            await asyncio.sleep(10)

async def start_web_server():
    """Asynchronous web server for health checks with more detailed logging."""
    from aiohttp import web

    async def handle_health(request):
        return web.Response(text="Bot is running", status=200)

    app = web.Application()
    app.router.add_get('/', handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

def handle_telegram_errors(update: Update, context: CallbackContext):
    """Comprehensive global error handler for Telegram bot."""
    error = context.error
    logger.error(f"Update {update} caused error: {error}")

    # Attempt to send error notification to admin
    if update and update.effective_chat:
        try:
            asyncio.create_task(
                context.bot.send_message(
                    chat_id=ADMIN_ID, 
                    text=f"Bot encountered an error: {error}"
                )
            )
        except Exception as notify_error:
            logger.error(f"Could not send error notification: {notify_error}")

@backoff.on_exception(
    backoff.expo, 
    (NetworkError, TimedOut, asyncio.TimeoutError), 
    max_tries=5
)
async def run_bot_with_retry():
    """Main bot runner with automatic retry mechanism."""
    await main()

async def main():
    """Consolidated main function with comprehensive error handling."""
    application = None
    delete_task = None

    try:
        # Start web server for health checks
        await start_web_server()

        # Validate critical configurations
        if not all([TOKEN, DB_URL, SEARCH_GROUP_ID, STORAGE_GROUP_ID, ADMIN_ID]):
            raise ValueError("Missing critical configuration")

        # Build the Telegram bot application
        application = ApplicationBuilder().token(TOKEN).build()

        # Add error handler
        application.add_error_handler(handle_telegram_errors)

        # Register handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.Document.ALL, add_movie))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_movie))

        logger.info("Starting bot...")

        # Start auxiliary task
        delete_task = asyncio.create_task(delete_old_messages(application))

        # Run polling
        await application.run_polling(
            drop_pending_updates=True,
            stop_on_sigint=True,
            timeout=30
        )

    except KeyboardInterrupt:
        logger.info("Bot stopped manually.")
    except Exception as e:
        logger.critical(f"Critical error in main: {e}")
    finally:
        logger.info("Initiating bot shutdown...")
        
        # Stop application if initialized
        if application:
            try:
                await application.shutdown()
            except Exception as e:
                logger.error(f"Error during application shutdown: {e}")

        # Cancel and clean up delete task
        if delete_task:
            delete_task.cancel()
            try:
                await delete_task
            except asyncio.CancelledError:
                logger.info("Delete task cleaned up.")
        
        logger.info("Bot shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(run_bot_with_retry())
    except Exception as e:
        logger.critical(f"Unhandled exception: {e}")
