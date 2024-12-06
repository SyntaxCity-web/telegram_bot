import logging
import re
import asyncio
import nest_asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackContext
from pymongo import MongoClient
import os

# Patch asyncio to allow nested event loops
nest_asyncio.apply()

# Constants
TOKEN = os.environ.get('TOKEN')
DB_URL = os.environ.get('DB_URL')
SEARCH_GROUP_ID = os.environ.get('SEARCH_GROUP_ID')
STORAGE_GROUP_ID = os.environ.get('STORAGE_GROUP_ID')
ADMIN_ID = os.environ.get('ADMIN_ID')

# MongoDB client setup
client = MongoClient(DB_URL)
db = client['MoviesDB']
collection = db['Movies']

# Logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

async def start(update: Update, context: CallbackContext):
    """Send a welcome message when the command /start is issued."""
    user_name = update.effective_user.full_name

    # Create inline button to add the bot to chat
    keyboard = [
        [InlineKeyboardButton("Add me to your chat!", url="https://t.me/+ERz0bGWEHHBmNTU9")]  # Replace with your bot's username
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send the welcome message with the button
    welcome_message = (
        f"Hey there! My name is Olive - I'm here to help you manage your groups! "
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=welcome_message, reply_markup=reply_markup)

async def add_movie(update: Update, context: CallbackContext):
    """Add movie to the database when a file is sent in the storage group."""
    if update.effective_chat.id == int(STORAGE_GROUP_ID):
        file_info = update.message.document
        if file_info:
            movie_name = file_info.file_name
            file_id = file_info.file_id
            
            # Insert movie into MongoDB
            collection.insert_one({"name": movie_name, "file_id": file_id})
            await context.bot.send_message(chat_id=STORAGE_GROUP_ID, text=f"Added movie: {movie_name}")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="You can only upload movies in the designated storage group.")

async def search_movie_by_name(update: Update, context: CallbackContext):
    """Search for a movie in the database when a user sends a message in the search group."""
    if update.effective_chat.id == int(SEARCH_GROUP_ID):
        movie_name = update.message.text.strip()
        if movie_name:
            regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
            try:
                results = collection.find({"name": {"$regex": regex_pattern}})
                results_list = list(results)

                if results_list:
                    for result in results_list:
                        file_id = result['file_id']
                        movie_title = result['name']
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Found movie: {movie_title}")
                        await context.bot.send_document(chat_id=update.effective_chat.id, document=file_id)
                else:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text="Movie not found.")
            except Exception as e:
                logging.error(f"An error occurred while searching: {str(e)}")
                await context.bot.send_message(chat_id=update.effective_chat.id, text="An error occurred while searching.")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Please use this feature in the designated search group.")

async def get_user_id(update: Update, context: CallbackContext):
    """Return the user's ID when /id command is issued in the search group."""
    if update.effective_chat.id == int(SEARCH_GROUP_ID):
        user_id = update.effective_user.id
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Your User ID: {user_id}")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="You can only use the /id command in the designated search group.")

async def welcome_new_member(update: Update, context: CallbackContext):
    """Send a welcome message when a new user joins the search group."""
    for member in update.message.new_chat_members:
        await context.bot.send_message(chat_id=SEARCH_GROUP_ID, text=f"Welcome {member.full_name}! Ask for a movie name.")

async def handle_text_message(update: Update, context: CallbackContext):
    """Handle text messages by searching for a movie."""
    await search_movie_by_name(update, context)

async def error_handler(update: Update, context: CallbackContext):
    """Log the error and notify admin or user."""
    logging.error(f"An error occurred: {context.error}")
    if ADMIN_ID:
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"An error occurred:\n{context.error}")
        except Exception as admin_notify_error:
            logging.error(f"Failed to notify admin: {admin_notify_error}")
    if update and update.effective_chat:
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Something went wrong! Our team has been notified.")
        except Exception as user_notify_error:
            logging.error(f"Failed to notify user: {user_notify_error}")

async def main():
    """Start the bot."""
    application = ApplicationBuilder().token(TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.ALL, add_movie))
    application.add_handler(CommandHandler("id", get_user_id))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # Add error handler
    application.add_error_handler(error_handler)

    # Start polling
    await application.run_polling()

if __name__ == '__main__':
    nest_asyncio.apply()
    app = main()
    asyncio.get_event_loop().run_until_complete(app)
