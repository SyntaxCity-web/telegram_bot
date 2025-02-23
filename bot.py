import logging
import re
import datetime
import asyncio
import time
import pytz
from collections import defaultdict
from pymongo import MongoClient, errors
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackContext, filters
from dotenv import load_dotenv
import os
import nest_asyncio
import uuid
import random
from aiohttp import web
from telegram.ext import CallbackQueryHandler
import aiohttp

# Apply nest_asyncio for environments like Jupyter
nest_asyncio.apply()

# Load environment variables
load_dotenv()

# Configuration
class Config:
    TOKEN = os.getenv('TOKEN')
    DB_URL = os.getenv('DB_URL')
    SEARCH_GROUP_ID = int(os.getenv('SEARCH_GROUP_ID'))
    STORAGE_GROUP_ID = int(os.getenv('STORAGE_GROUP_ID'))
    ADMIN_ID = int(os.getenv('ADMIN_ID'))
    PORT = int(os.getenv('PORT', 8088))  # Default to 8088 if not set

# MongoDB Client Setup
def connect_mongo():
    retries = 5
    while retries > 0:
        try:
            client = MongoClient(Config.DB_URL, serverSelectionTimeoutMS=5000)
            db = client['MoviesDB']
            collection = db['Movies']
            client.admin.command('ping')
            logging.info("MongoDB connection established.")
            return collection
        except errors.ServerSelectionTimeoutError as e:
            logging.error(f"MongoDB connection failed. Retrying... {e}")
            retries -= 1
            time.sleep(5)
        except Exception as e:
            logging.error(f"Unexpected MongoDB error: {e}")
            retries -= 1
            time.sleep(5)
    logging.critical("Failed to connect to MongoDB.")
    return None

collection = connect_mongo()
upload_sessions = defaultdict(lambda: {'files': [], 'image': None, 'caption': None})

# Helper function to sanitize Unicode text
def sanitize_unicode(text):
    """Sanitize Unicode text to remove invalid characters, such as surrogate pairs."""
    return text.encode('utf-8', 'ignore').decode('utf-8')

# Helper function to clean filenames
def clean_filename(filename):
    """Clean the uploaded filename by removing unnecessary tags and extracting relevant details."""
    # Remove prefixes like @TamilMob_LinkZz
    filename = re.sub(r'^@[\w_]+[\s-]*', '', filename)

    # Remove emojis and special characters
    filename = re.sub(r'[^\x00-\x7F]+', '', filename)

    # Replace underscores with spaces
    filename = filename.replace('_', ' ')

    # Remove unwanted tags
    pattern = r'(?i)(HDRip|10bit|x264|AAC|\d{3,4}MB|AMZN|WEB-DL|WEBRip|HEVC|250M|x265|ESub|HQ|\.mkv|\.mp4|\.avi|\.mov|BluRay|DVDRip|720p|1080p|540p|SD|HD|CAM|DVDScr|R5|TS|Rip|BRRip|AC3|DualAudio|6CH|v\d+)'
    filename = re.sub(pattern, '', filename).strip()

    # Extract movie name, year, and language
    match = re.search(r'^(.*?)[\s_]*\(?(\d{4})\)?[\s_]*(Malayalam|Tamil|Hindi|Telugu|English)?', filename, re.IGNORECASE)

    if match:
        name = match.group(1).strip()
        year = match.group(2).strip() if match.group(2) else ""
        language = match.group(3).strip() if match.group(3) else ""

        # Format the cleaned name
        cleaned_name = f"{name} ({year}) {language}".strip()
        return re.sub(r'\s+', ' ', cleaned_name)  # Remove extra spaces

    # If no match is found, return the cleaned filename
    return re.sub(r'\s+', ' ', filename).strip()

# Process movie file upload
async def process_movie_file(file_info, session, caption):
    """Handle the movie file upload."""
    filename = file_info.file_name
    cleaned_name = clean_filename(filename)
    session['files'].append({
        'file_id': file_info.file_id,
        'file_name': cleaned_name
    })
    session['caption'] = caption or session.get('caption', cleaned_name)
    await update.message.reply_text(
        sanitize_unicode(f"✅ {len(session['files'])} file(s) received! Now, please upload an image for the related file(s).")
    )

# Process image upload
async def process_image_upload(image_info, session, caption):
    """Handle the movie poster upload."""
    largest_photo = max(image_info, key=lambda photo: photo.width * photo.height)
    session['image'] = {
        'file_id': largest_photo.file_id,
        'width': largest_photo.width,
        'height': largest_photo.height
    }
    session['caption'] = caption or session.get('caption')
    await update.message.reply_text(sanitize_unicode("✅ Image received! Now, please upload the movie file(s)."))

# Generate deep link
def create_deep_link(movie_id):
    """Generate a deep link to the movie."""
    return f"https://t.me/{context.bot.username}?start={movie_id}"

# Send movie preview to group
async def send_preview_to_group(movie_entry, context):
    """Send the movie preview to the search group."""
    name = movie_entry.get('name', 'Unknown Movie')
    media = movie_entry.get('media', {})
    image_file_id = media.get('image', {}).get('file_id')
    deep_link = create_deep_link(movie_entry['movie_id'])

    keyboard = [[InlineKeyboardButton("🎬 Download", url=deep_link)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if image_file_id:
            await context.bot.send_photo(
                chat_id=Config.SEARCH_GROUP_ID,
                photo=image_file_id,
                caption=sanitize_unicode(f"🎥 **{name}**"),
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_message(
                chat_id=Config.SEARCH_GROUP_ID,
                text=sanitize_unicode(f"🎥 **{name}**"),
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
    except Exception as e:
        logging.error(f"Error sending preview for {sanitize_unicode(name)}: {sanitize_unicode(str(e))}")

# Add movie handler
async def add_movie(update: Update, context: CallbackContext):
    """Process movie uploads, cleaning filenames and managing sessions."""
    if update.effective_chat.id != Config.STORAGE_GROUP_ID:
        await update.message.reply_text("❌ You can only upload movies in the designated storage group. 🎥")
        return

    user_id = update.effective_user.id
    session = upload_sessions.setdefault(user_id, {"files": [], "image": None})
    file_info = update.message.document
    image_info = update.message.photo
    caption = sanitize_unicode(update.message.caption or "")

    if file_info:
        await process_movie_file(file_info, session, caption)
    elif image_info:
        await process_image_upload(image_info, session, caption)

    if session['files'] and session['image']:
        movie_name = session['files'][0]['file_name']
        movie_id = str(uuid.uuid4())
        movie_entry = {
            'movie_id': movie_id,
            'name': movie_name,
            'media': {
                'documents': session['files'],
                'image': session['image']
            }
        }

        try:
            collection.insert_one(movie_entry)
            await update.message.reply_text(f"✅ Successfully added movie: {movie_name}")

            if Config.SEARCH_GROUP_ID:
                await send_preview_to_group(movie_entry, context)

            del upload_sessions[user_id]
        except Exception as e:
            logging.error(f"Database error: {e}")
            await update.message.reply_text("❌ Failed to add the movie. Please try again later.")
    elif not (file_info or image_info):
        await update.message.reply_text("❌ Please upload both a movie file and an image.")

# Search movie handler
async def search_movie(update: Update, context: CallbackContext):
    """Search for a movie in the database and send preview to group."""
    if update.effective_chat.id != Config.SEARCH_GROUP_ID:
        await update.message.reply_text("❌ Use this feature in the designated search group.")
        return

    movie_name = sanitize_unicode(update.message.text.strip())
    if not movie_name:
        await update.message.reply_text("🚨 Provide a movie name to search. Use /search <movie_name>")
        return

    try:
        regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
        results = list(collection.find({"name": {"$regex": regex_pattern}}).limit(10))

        if results:
            for result in results:
                name = result.get('name', 'Unknown Movie')
                media = result.get('media', {})
                image_file_id = media.get('image', {}).get('file_id')
                deep_link = f"https://t.me/{context.bot.username}?start={result['movie_id']}"

                keyboard = [[InlineKeyboardButton("🎬 Download", url=deep_link)]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                if image_file_id:
                    try:
                        await context.bot.send_photo(
                            chat_id=update.effective_chat.id,
                            photo=image_file_id,
                            caption=sanitize_unicode(f"🎥 **{name}**"),
                            parse_mode="Markdown",
                            reply_markup=reply_markup
                        )
                    except Exception as e:
                        logging.error(f"Error sending preview for {sanitize_unicode(name)}: {sanitize_unicode(str(e))}")
                else:
                    await update.message.reply_text(
                        sanitize_unicode(f"🎥 **{name}**"),
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
        else:
            await update.message.reply_text("❌ No results found.")
    except Exception as e:
        logging.error(f"Search error: {sanitize_unicode(str(e))}")
        await update.message.reply_text("❌ An unexpected error occurred. Please try again later.")

# Start web server for health checks
async def start_web_server():
    """Start a web server for health checks."""
    async def handle_health(request):
        return web.Response(text="Bot is running")

    app = web.Application()
    app.router.add_get('/', handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', Config.PORT)
    await site.start()
    logging.info(f"Web server running on port {Config.PORT}")

# Main function
async def main():
    """Main function to start the bot."""
    try:
        await start_web_server()

        application = ApplicationBuilder().token(Config.TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.Document.ALL, add_movie))
        application.add_handler(MessageHandler(filters.PHOTO, add_movie))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_movie))
        application.add_handler(CallbackQueryHandler(get_movie_files))
        application.add_handler(CommandHandler("id", id_command))

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
