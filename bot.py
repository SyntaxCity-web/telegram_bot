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
from difflib import get_close_matches
from aiohttp import web

# Apply nest_asyncio for environments like Jupyter
nest_asyncio.apply()

# Load environment variables
load_dotenv()

# Configuration
TOKEN = os.getenv('TOKEN', 'default_token')
DB_URL = os.getenv('DB_URL', 'mongodb://localhost:27017')
SEARCH_GROUP_ID = int(os.getenv('SEARCH_GROUP_ID', '-1'))
STORAGE_GROUP_ID = int(os.getenv('STORAGE_GROUP_ID', '-1'))
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
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

# Helper Functions
def search_movies_by_name(movie_name):
    """Search for movies by name."""
    regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
    return list(collection.find({"name": {"$regex": regex_pattern}}).limit(10))

def suggest_alternatives(movie_name, limit=5):
    """Suggest similar movies using partial matches."""
    all_movie_names = [movie['name'] for movie in collection.find({}, {"name": 1})]
    return get_close_matches(movie_name, all_movie_names, n=limit, cutoff=0.6)

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
        try:
            collection.insert_one({"name": movie_name, "file_id": file_id})
            await context.bot.send_message(chat_id=STORAGE_GROUP_ID, text=f"✅ Added movie: {movie_name}")
            logging.info(f"Movie added: {movie_name}")
        except Exception as e:
            logging.error(f"Error adding movie {movie_name}: {e}")
            await update.message.reply_text("❌ Error saving movie to the database.")

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
        results = search_movies_by_name(movie_name)

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
            suggestions = suggest_alternatives(movie_name)
            if suggestions:
                suggestion_text = "\n".join([f"- {s}" for s in suggestions])
                await update.message.reply_text(
                    f"😔 Movie not found. Did you mean:\n{suggestion_text}",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text("🤷‍♂️ No suggestions available. Try a different term.")

    except Exception as e:
        logging.error(f"Search error: {e}")
        await update.message.reply_text("❌ An unexpected error occurred. Please try again later.")

# (Rest of the script remains the same)

