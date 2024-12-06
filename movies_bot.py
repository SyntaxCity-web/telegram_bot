import logging
import re
import asyncio
import nest_asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackContext
from pymongo import MongoClient

# Patch asyncio to allow nested event loops
nest_asyncio.apply()

# Constants
# TOKEN = "7225498446:AAEE3HDDa-3IoM0b76ajKUuWKZ2uAU1lwhc"  # Replace with your Telegram Bot Token
# DB_URL = "mongodb+srv://machupanoor8:machupanoor8@cluster0.ppnnllc.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"  # Replace with your MongoDB connection string
# SEARCH_GROUP_ID = '-1002385916738'  # Replace with your search group ID
# STORAGE_GROUP_ID = '-1002419819856'  # Replace with your storage group ID
# ADMIN_ID = 5790440984  # Replace with your admin ID

import os

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

# List of funny questions and responses
FUNNY_RESPONSES = {
    "what's your favorite color?": "I love the color of binary! 0s and 1s are so pretty!",
    "tell me a joke": "Why did the scarecrow win an award? Because he was outstanding in his field!",
    "how are you?": "I'm just a bunch of code, but thanks for asking! How are you?",
    "do you believe in love?": "Of course! But I'm still waiting for my algorithm to find the one!",
    "what's your purpose?": "To make your life easier, one response at a time! And to tell jokes!"
}

async def start(update: Update, context: CallbackContext):
    """Send a welcome message when the command /start is issued."""
    user_name = update.effective_user.full_name  # Get the user's full name

    # Create inline button to add the bot to chat
    keyboard = [
        [InlineKeyboardButton("Add me to your chat!", url="https://t.me/+ERz0bGWEHHBmNTU9")]  # Replace with your bot's username
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send the welcome message with the button
    welcome_message = (
        f"Hey there! My name is Olive - I'm here to help you manage your groups! "
        f"Use /help to find out more about how to use me to my full potential."
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
    logging.info(f"Received message for search in group: {update.effective_chat.id}")
    
    # Check if the user is in the search group
    if update.effective_chat.id == int(SEARCH_GROUP_ID):
        movie_name = update.message.text.strip()  # Capture and strip whitespace from the message (movie name)
        logging.info(f"Searching for movie: '{movie_name}'")

        if movie_name:
            # Using regex to search for the movie name
            regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
            logging.info(f"Using regex pattern: {regex_pattern}")

            try:
                # Search the MongoDB collection using the regex pattern
                results = collection.find({"name": {"$regex": regex_pattern}})

                # Check if results were found
                results_list = list(results)  # Convert cursor to a list
                if results_list:
                    for result in results_list:
                        file_id = result['file_id']
                        movie_title = result['name']
                        logging.info(f"Found movie: {movie_title} with file_id: {file_id}")

                        # Send the movie title and the file as a response
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Found movie: {movie_title}")
                        await context.bot.send_document(chat_id=update.effective_chat.id, document=file_id)
                else:
                    logging.warning(f"No movie found for search term: '{movie_name}'")
                    await context.bot.send_message(chat_id=update.effective_chat.id, text="Movie not found.")
            except Exception as e:
                logging.error(f"An error occurred while searching: {str(e)}")  # Log the actual error message
                await context.bot.send_message(chat_id=update.effective_chat.id, text="An error occurred while searching.")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Please use this feature in the designated search group.")

async def get_user_id(update: Update, context: CallbackContext):
    """Return the user's ID when /id command is issued in the search group."""
    if update.effective_chat.id == int(SEARCH_GROUP_ID):
        user_id = update.effective_user.id  # Get the user's ID
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Your User ID: {user_id}")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="You can only use the /id command in the designated search group.")

async def welcome_new_member(update: Update, context: CallbackContext):
    """Send a welcome message when a new user joins the search group."""
    for member in update.message.new_chat_members:
        await context.bot.send_message(chat_id=SEARCH_GROUP_ID, text=f"Welcome {member.full_name}! Ask for a movie name.")

async def funny_questions_handler(update: Update, context: CallbackContext):
    """Respond to funny questions."""
    user_message = update.message.text.lower()  # Convert user message to lowercase
    for question, response in FUNNY_RESPONSES.items():
        if question in user_message:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=response)
            return  # Exit after responding to the first matched funny question

async def handle_text_message(update: Update, context: CallbackContext):
    """Handle text messages: try to find a movie or respond to funny questions."""
    user_message = update.message.text.strip().lower()  # Clean and convert message to lowercase
    
    # Check for funny questions first
    for question, response in FUNNY_RESPONSES.items():
        if question in user_message:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=response)
            return
    
    # If no funny questions matched, search for a movie
    await search_movie_by_name(update, context)

async def main():
    """Start the bot."""
    # Create an Application object instead of Updater
    application = ApplicationBuilder().token(TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.ALL, add_movie))
    application.add_handler(CommandHandler("id", get_user_id))  # New handler for /id command
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))  # Handle all text messages for search or funny questions

    # Start polling
    await application.run_polling()

if __name__ == '__main__':
    # Use asyncio.run to handle the event loop automatically
    asyncio.run(main())
