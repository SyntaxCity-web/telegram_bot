import logging
import re
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext
)
from pymongo import MongoClient, errors
import os
import signal

# Load environment variables
TOKEN = os.getenv('TOKEN')
DB_URL = os.getenv('DB_URL')
SEARCH_GROUP_ID = int(os.getenv('SEARCH_GROUP_ID', 0))

# Ensure critical environment variables are set
if not TOKEN or not DB_URL or not SEARCH_GROUP_ID:
    raise EnvironmentError("Required environment variables are missing.")

# MongoDB client setup with enhanced error handling
try:
    client = MongoClient(DB_URL)
    db = client['MoviesDB']
    collection = db['Movies']
    # Create an index for better search performance
    collection.create_index([("name", "text")])
except errors.PyMongoError as e:
    logging.error(f"Error connecting to MongoDB: {e}")
    raise

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Helper functions
def is_in_group(chat_id, group_id):
    """Check if a chat ID matches the specified group ID."""
    return chat_id == group_id

# Handlers
async def start(update: Update, context: CallbackContext):
    """Handle the /start command."""
    try:
        user_name = update.effective_user.full_name
        keyboard = [[
            InlineKeyboardButton("Add me to your chat! 🤖", url="https://t.me/+ERz0bGWEHHBmNTU9")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        welcome_message = (
            f"Hi {user_name}! 👋 I'm your movie assistant bot. 🎉\n"
            "You can search for movies in the designated group. Use /id to get your ID."
        )
        await update.message.reply_text(text=welcome_message, reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"Error in /start command: {e}")
        await update.message.reply_text("Oops! Something went wrong. 😕 Please try again later.")

async def search_movie(update: Update, context: CallbackContext):
    """Search for a movie in the database."""
    try:
        if not is_in_group(update.effective_chat.id, SEARCH_GROUP_ID):
            await update.message.reply_text("Use this feature in the search group. 🔍")
            return

        movie_name = update.message.text.strip()
        if not movie_name:
            await update.message.reply_text("Please enter a valid movie name. 🎬")
            return

        # Search using regex
        regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
        results = list(collection.find({"name": {"$regex": regex_pattern}}))

        if results:
            for result in results:
                await update.message.reply_text(f"Found movie: {result['name']} 🎥")
                await context.bot.send_document(chat_id=update.effective_chat.id, document=result['file_id'])
        else:
            await update.message.reply_text("Movie not found. 😔 Try a different search.")
    except errors.PyMongoError as e:
        logging.error(f"MongoDB error while searching for movie: {e}")
        await update.message.reply_text("There was an error while searching. 🛑 Please try again later.")
    except Exception as e:
        logging.error(f"Error searching movie: {e}")
        await update.message.reply_text("Oops! Something went wrong. 😕 Please try again later.")

async def get_user_id(update: Update, context: CallbackContext):
    """Return the user's ID."""
    try:
        user_id = update.effective_user.id
        await update.message.reply_text(f"Your User ID: {user_id} 🆔")
    except Exception as e:
        logging.error(f"Error getting user ID: {e}")
        await update.message.reply_text("Oops! Something went wrong. 😕 Please try again later.")

# Global error handling for unhandled exceptions
async def error_handler(update: Update, context: CallbackContext):
    """Handle uncaught errors globally."""
    logging.error(f"Unhandled error: {context.error}")
    if update:
        await update.message.reply_text("Oops! Something went wrong. 😕 Please try again later.")

# Main application
async def main():
    """Start the bot."""
    application = ApplicationBuilder().token(TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("id", get_user_id))

    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_movie))

    # Global error handler
    application.add_error_handler(error_handler)

    # Graceful shutdown
    async def shutdown():
        logging.info("Shutting down gracefully...")
        await application.stop()
        await application.shutdown()
        logging.info("Shutdown complete.")

    # Register signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    logging.info("Bot is starting...")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
