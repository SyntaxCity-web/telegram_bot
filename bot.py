import logging
import re
import datetime
import asyncio
import time
import pytz
import signal
from collections import defaultdict
from pymongo import MongoClient, errors
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackContext, filters
from dotenv import load_dotenv
import os
import nest_asyncio
import uuid
from aiohttp import web
from telegram.ext import CallbackQueryHandler

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
PORT = int(os.getenv('PORT', 8088))

# Custom Timezone Formatter
class TimezoneFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        ist = pytz.timezone('Asia/Kolkata')
        ct = datetime.datetime.fromtimestamp(record.created, ist)
        if datefmt:
            s = ct.strftime(datefmt)
        else:
            try:
                s = ct.isoformat(timespec='milliseconds')
            except TypeError:
                s = ct.isoformat()
        return s

# Logging Configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S %Z',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)

# Apply custom formatter
logger = logging.getLogger()
for handler in logger.handlers:
    handler.setFormatter(TimezoneFormatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S %Z'
    ))

# MongoDB Setup with retry mechanism
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
upload_sessions = defaultdict(lambda: {'files': [], 'image': None, 'caption': None})

def sanitize_unicode(text):
    """Sanitize Unicode text to remove invalid characters."""
    return text.encode('utf-8', 'ignore').decode('utf-8')

def signal_handler():
    """Handle system signals for graceful shutdown."""
    logging.info("Received stop signal")
    raise KeyboardInterrupt

async def start_web_server():
    """Start a web server for health checks."""
    try:
        async def handle_health(request):
            return web.Response(text="Bot is running")

        app = web.Application()
        app.router.add_get('/', handle_health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        logging.info(f"Web server running on port {PORT}")
        return runner, site
    except Exception as e:
        logging.error(f"Web server setup error: {e}")
        raise

async def add_movie(update: Update, context: CallbackContext):
    """Process movie uploads, cleaning filenames and managing sessions."""
    
    def clean_filename(filename):
        # Remove prefixes like @Channel_name
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
            cleaned_name = f"{name} ({year}) {language}".strip()
            return re.sub(r'\s+', ' ', cleaned_name)
        
        return re.sub(r'\s+', ' ', filename).strip()

    async def process_movie_file(file_info, session, caption):
        filename = file_info.file_name
        cleaned_name = clean_filename(filename)
        session['files'].append({
            'file_id': file_info.file_id,
            'file_name': cleaned_name
        })
        session['caption'] = caption or session.get('caption', cleaned_name)
        await update.message.reply_text(
            sanitize_unicode(f"✅ {len(session['files'])} file(s) received! Now, please upload an image.")
        )

    async def process_image_upload(image_info, session, caption):
        largest_photo = max(image_info, key=lambda photo: photo.width * photo.height)
        session['image'] = {
            'file_id': largest_photo.file_id,
            'width': largest_photo.width,
            'height': largest_photo.height
        }
        session['caption'] = caption or session.get('caption')
        await update.message.reply_text(
            sanitize_unicode("✅ Image received! Now, please upload the movie file(s).")
        )

    if update.effective_chat.id != STORAGE_GROUP_ID:
        await update.message.reply_text(
            sanitize_unicode("❌ You can only upload movies in the designated storage group.")
        )
        return

    user_id = update.effective_user.id
    session = upload_sessions[user_id]
    file_info = update.message.document
    image_info = update.message.photo
    caption = sanitize_unicode(update.message.caption or "")

    try:
        if file_info:
            await process_movie_file(file_info, session, caption)
        elif image_info:
            await process_image_upload(image_info, session, caption)

        if session['files'] and session['image']:
            movie_id = str(uuid.uuid4())
            movie_entry = {
                'movie_id': movie_id,
                'name': session['files'][0]['file_name'],
                'media': {
                    'documents': session['files'],
                    'image': session['image']
                }
            }
            
            collection.insert_one(movie_entry)
            await update.message.reply_text(
                sanitize_unicode(f"✅ Successfully added movie: {movie_entry['name']}")
            )
            
            # Send preview to search group
            if SEARCH_GROUP_ID:
                deep_link = f"https://t.me/{context.bot.username}?start={movie_id}"
                keyboard = [[InlineKeyboardButton("🎬 Download", url=deep_link)]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                try:
                    await context.bot.send_photo(
                        chat_id=SEARCH_GROUP_ID,
                        photo=session['image']['file_id'],
                        caption=sanitize_unicode(f"🎥 **{movie_entry['name']}**"),
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logging.error(f"Error sending preview: {str(e)}")
            
            del upload_sessions[user_id]

    except Exception as e:
        logging.error(f"Error processing upload: {str(e)}")
        await update.message.reply_text(
            sanitize_unicode("❌ An error occurred while processing the upload.")
        )

async def search_movie(update: Update, context: CallbackContext):
    """Search for movies and send results."""
    if update.effective_chat.id != SEARCH_GROUP_ID:
        await update.message.reply_text(
            sanitize_unicode("❌ Use this feature in the designated search group.")
        )
        return

    query = update.message.text.strip()
    if not query:
        await update.message.reply_text(
            sanitize_unicode("🚨 Please provide a movie name to search.")
        )
        return

    try:
        regex_pattern = re.compile(re.escape(query), re.IGNORECASE)
        results = list(collection.find({"name": {"$regex": regex_pattern}}).limit(10))

        if not results:
            await update.message.reply_text(
                sanitize_unicode("❌ No movies found matching your search.")
            )
            return

        for result in results:
            deep_link = f"https://t.me/{context.bot.username}?start={result['movie_id']}"
            keyboard = [[InlineKeyboardButton("🎬 Download", url=deep_link)]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            if 'media' in result and 'image' in result['media']:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=result['media']['image']['file_id'],
                    caption=sanitize_unicode(f"🎥 **{result['name']}**"),
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_text(
                    sanitize_unicode(f"🎥 **{result['name']}**"),
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )

    except Exception as e:
        logging.error(f"Search error: {str(e)}")
        await update.message.reply_text(
            sanitize_unicode("❌ An error occurred while searching.")
        )

async def start(update: Update, context: CallbackContext):
    """Handle /start command and deep links."""
    user_name = update.effective_user.full_name or "there"
    args = context.args

    if args and len(args) > 0:
        movie_id = args[0]
        movie = collection.find_one({"movie_id": movie_id})
        
        if movie:
            try:
                if 'media' in movie and 'image' in movie['media']:
                    await update.message.reply_photo(
                        photo=movie['media']['image']['file_id'],
                        caption=sanitize_unicode(f"🎥 **{movie['name']}**"),
                        parse_mode="Markdown"
                    )

                for doc in movie['media']['documents']:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=doc['file_id'],
                        caption=sanitize_unicode(f"🎥 {doc['file_name']}")
                    )
            except Exception as e:
                logging.error(f"Error sending movie files: {str(e)}")
                await update.message.reply_text(
                    sanitize_unicode("❌ Error sending movie files.")
                )
        else:
            await update.message.reply_text(
                sanitize_unicode("❌ Movie not found.")
            )
        return

    keyboard = [[InlineKeyboardButton("Add me to your group! 🤖", url="https://t.me/+ERz0bGWEHHBmNTU9")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        text=sanitize_unicode(f"Hi {user_name}! 👋 Use me to search for movies. 🎥"),
        reply_markup=reply_markup
    )

async def id_command(update: Update, context: CallbackContext):
    """Get user and chat IDs."""
    await update.message.reply_text(
        f"👤 Your ID: {update.effective_user.id}\n💬 Chat ID: {update.effective_chat.id}"
    )

async def main():
    """Main function to run the bot."""
    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Initialize components
    web_runner = None
    application = None

    try:
        # Start web server
        web_runner, site = await start_web_server()

        # Initialize bot
        application = ApplicationBuilder().token(TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.Document.ALL, add_movie))
        application.add_handler(MessageHandler(filters.PHOTO, add_movie))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_movie))
        application.add_handler(CallbackQueryHandler(get_movie_files))
        application.add_handler(CommandHandler("id", id_command))

        # Start bot
        await application.initialize()
        await application.start()
        await application.run_polling(stop_signals=None)

    except Exception as e:
        logging.error(f"Main loop error: {str(e)}")
    finally:
        # Cleanup
        if application:
            await application.stop()
            await application.shutdown()
        
        if web_runner:
            await web_runner.cleanup()
        
        logging.info("Bot shutdown complete")

if __name__ == "__main__":
    try:
        # Create and set event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run main function
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped manually")
    except Exception as e:
        logging.error(f"Fatal error: {str(e)}")
    finally:
        # Clean up event loop
        try:
            loop.stop()
            loop.close()
        except Exception as e:
            logging.error(f"Error during cleanup: {str(e)}")
