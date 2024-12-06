import logging
import re
import asyncio
import nest_asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext
)
from pymongo import MongoClient
import os

# Patch asyncio to allow nested event loops
nest_asyncio.apply()

# Load environment variables
TOKEN = os.getenv('TOKEN')
DB_URL = os.getenv('DB_URL')
SEARCH_GROUP_ID = int(os.getenv('SEARCH_GROUP_ID', 0))
STORAGE_GROUP_ID = int(os.getenv('STORAGE_GROUP_ID', 0))
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))

# Ensure critical environment variables are set
if not TOKEN or not DB_URL or not SEARCH_GROUP_ID or not STORAGE_GROUP_ID:
    raise EnvironmentError("Required environment variables are missing.")

# MongoDB client setup
client = MongoClient(DB_URL)
db = client['MoviesDB']
collection = db['Movies']

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Predefined responses for fun questions
FUNNY_RESPONSES = {
    "what's your favorite color?": "I love the color of binary! 0s and 1s are so pretty!",
    "tell me a joke": "Why did the scarecrow win an award? Because he was outstanding in his field!",
    "how are you?": "I'm just a bunch of code, but thanks for asking! How are you?",
    "do you believe in love?": "Of course! But I'm still waiting for my algorithm to find the one!",
    "what's your purpose?": "To make your life easier, one response at a time! And to tell jokes!"
}

# Helper functions
def is_in_group(chat_id, group_id):
    """Check if a chat ID matches the specified group ID."""
    return chat_id == group_id

# Handlers
async def start(update: Update, context: CallbackContext):
    """Handle the /start command."""
    user_name = update.effective_user.full_name

    keyboard = [[
        InlineKeyboardButton("Add me to your chat!", url=f"https://t.me/{context.bot.username}")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_message = (
        f"Hi {user_name}! I'm Olive, your group assistant. "
        f"Use /help to learn how to use me. Have fun!"
    )
    await update.message.reply_text(text=welcome_message, reply_markup=reply_markup)

async def add_movie(update: Update, context: CallbackContext):
    """Handle movie file uploads in the storage group."""
    if not is_in_group(update.effective_chat.id, STORAGE_GROUP_ID):
        await update.message.reply_text("Please upload movies in the designated storage group.")
        return

    if update.message.document:
        movie_name = update.message.document.file_name
        file_id = update.message.document.file_id
        
        collection.insert_one({"name": movie_name, "file_id": file_id})
        await update.message.reply_text(f"Added movie: {movie_name}")

async def search_movie(update: Update, context: CallbackContext):
    """Search for a movie in the database."""
    if not is_in_group(update.effective_chat.id, SEARCH_GROUP_ID):
        await update.message.reply_text("Use this feature in the search group.")
        return

    movie_name = update.message.text.strip()
    if not movie_name:
        await update.message.reply_text("Please enter a valid movie name.")
        return

    # Search using regex
    regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
    results = list(collection.find({"name": {"$regex": regex_pattern}}))

    if results:
        for result in results:
            await update.message.reply_text(f"Found movie: {result['name']}")
            await context.bot.send_document(chat_id=update.effective_chat.id, document=result['file_id'])
    else:
        await update.message.reply_text("Movie not found.")

async def get_user_id(update: Update, context: CallbackContext):
    """Return the user's ID."""
    if is_in_group(update.effective_chat.id, SEARCH_GROUP_ID):
        user_id = update.effective_user.id
        await update.message.reply_text(f"Your User ID: {user_id}")
    else:
        await update.message.reply_text("This command works only in the search group.")

async def welcome_new_member(update: Update, context: CallbackContext):
    """Welcome new members in the search group."""
    for member in update.message.new_chat_members:
        await update.message.reply_text(f"Welcome {member.full_name}! Ask for a movie name.")

async def handle_funny_questions(update: Update, context: CallbackContext):
    """Respond to predefined funny questions."""
    user_message = update.message.text.lower()
    for question, response in FUNNY_RESPONSES.items():
        if question in user_message:
            await update.message.reply_text(response)
            return

async def handle_text_message(update: Update, context: CallbackContext):
    """Handle general text messages."""
    # Prioritize funny questions
    await handle_funny_questions(update, context)
    # If not a funny question, treat it as a movie search
    await search_movie(update, context)

# Main application
async def main():
    """Start the bot."""
    application = ApplicationBuilder().token(TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("id", get_user_id))

    # Message handlers
    application.add_handler(MessageHandler(filters.Document.ALL, add_movie))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Start the bot
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
