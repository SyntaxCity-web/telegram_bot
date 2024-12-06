import os
import logging
import re
import asyncio
from flask import Flask, request
from pymongo import MongoClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, Dispatcher, CommandHandler, MessageHandler, filters, CallbackContext

# Constants
TOKEN = os.environ.get("TOKEN")  # Telegram Bot Token
DB_URL = os.environ.get("DB_URL")  # MongoDB Connection String
SEARCH_GROUP_ID = os.environ.get("SEARCH_GROUP_ID")  # ID of the Search Group
STORAGE_GROUP_ID = os.environ.get("STORAGE_GROUP_ID")  # ID of the Storage Group
ADMIN_ID = os.environ.get("ADMIN_ID")  # ID of the Admin

# MongoDB client setup
client = MongoClient(DB_URL)
db = client["MoviesDB"]
collection = db["Movies"]

# Logging setup
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# Flask app for webhook
app = Flask(__name__)

# Telegram application setup
application = ApplicationBuilder().token(TOKEN).build()
dispatcher = Dispatcher(application.bot, None)

# Bot Handlers
async def start(update: Update, context: CallbackContext):
    """Send a welcome message when the command /start is issued."""
    user_name = update.effective_user.full_name
    keyboard = [
        [InlineKeyboardButton("Add me to your chat!", url="https://t.me/your_bot_username")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_message = f"Hey there! My name is Olive - I'm here to help you manage your groups!"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=welcome_message, reply_markup=reply_markup)

async def add_movie(update: Update, context: CallbackContext):
    """Add a movie to the database."""
    if update.effective_chat.id == int(STORAGE_GROUP_ID):
        file_info = update.message.document
        if file_info:
            movie_name = file_info.file_name
            file_id = file_info.file_id
            collection.insert_one({"name": movie_name, "file_id": file_id})
            await context.bot.send_message(chat_id=STORAGE_GROUP_ID, text=f"Added movie: {movie_name}")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="You can only upload movies in the designated storage group.")

async def search_movie_by_name(update: Update, context: CallbackContext):
    """Search for a movie in the database."""
    if update.effective_chat.id == int(SEARCH_GROUP_ID):
        movie_name = update.message.text.strip()
        regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
        results = list(collection.find({"name": {"$regex": regex_pattern}}))
        if results:
            for result in results:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Found movie: {result['name']}")
                await context.bot.send_document(chat_id=update.effective_chat.id, document=result["file_id"])
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Movie not found.")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Use this feature in the designated search group.")

async def get_user_id(update: Update, context: CallbackContext):
    """Return the user's ID when /id command is issued."""
    if update.effective_chat.id == int(SEARCH_GROUP_ID):
        user_id = update.effective_user.id
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Your User ID: {user_id}")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Use the /id command in the designated search group.")

async def welcome_new_member(update: Update, context: CallbackContext):
    """Send a welcome message when a new user joins."""
    for member in update.message.new_chat_members:
        await context.bot.send_message(chat_id=SEARCH_GROUP_ID, text=f"Welcome {member.full_name}! Ask for a movie name.")

async def handle_text_message(update: Update, context: CallbackContext):
    """Handle text messages by searching for a movie."""
    await search_movie_by_name(update, context)

async def error_handler(update: Update, context: CallbackContext):
    """Log errors and notify admin."""
    logging.error(f"An error occurred: {context.error}")
    if ADMIN_ID:
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"An error occurred:\n{context.error}")
    if update and update.effective_chat:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Something went wrong! Our team has been notified.")

# Flask Webhook Route
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    """Handle incoming webhook updates."""
    update = Update.de_json(request.get_json(force=True), application.bot)
    dispatcher.process_update(update)
    return "OK", 200

# Flask Health Check Route
@app.route("/", methods=["GET"])
def health_check():
    """Health check endpoint for Render."""
    return "OK", 200

async def main():
    """Register Telegram handlers and start the bot."""
    # Register bot handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(MessageHandler(filters.Document.ALL, add_movie))
    dispatcher.add_handler(CommandHandler("id", get_user_id))
    dispatcher.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    dispatcher.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Error handler
    dispatcher.add_error_handler(error_handler)

    # Set the webhook URL
    webhook_url = f"https://telegram-bot-th04.onrender.com/{TOKEN}"
    await application.bot.set_webhook(webhook_url)

if __name__ == "__main__":
    # Start the bot and Flask app
    asyncio.run(main())
    PORT = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=PORT)
