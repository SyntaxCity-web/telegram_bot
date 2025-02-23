import logging
import re
import datetime
import asyncio
import time
import pytz
from collections import defaultdict, deque
from pymongo import MongoClient, errors
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackContext, filters
from dotenv import load_dotenv
import os
import nest_asyncio
import uuid
from aiohttp import web
from telegram.ext import CallbackQueryHandler
from logging.handlers import RotatingFileHandler

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

# Upload Session Management
class UploadSession:
    def __init__(self):
        self.files = []
        self.image = None
        self.caption = None
        self.created_at = datetime.datetime.now()
        self.timeout = datetime.timedelta(minutes=30)
    
    def is_expired(self):
        return datetime.datetime.now() - self.created_at > self.timeout

# Rate Limiter
class RateLimiter:
    def __init__(self, max_requests, time_window):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = defaultdict(lambda: deque(maxlen=max_requests))
    
    def can_proceed(self, user_id):
        now = datetime.datetime.now()
        requests = self.requests[user_id]
        
        while requests and now - requests[0] > self.time_window:
            requests.popleft()
        
        if len(requests) < self.max_requests:
            requests.append(now)
            return True
        return False

# Custom Logger Setup
class CustomLogger:
    def __init__(self):
        self.logger = logging.getLogger('MovieBot')
        self.setup_logging()
    
    def setup_logging(self):
        formatter = TimezoneFormatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S %Z'
        )
        
        file_handler = RotatingFileHandler(
            'bot.log',
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.logger.setLevel(logging.INFO)

# Apply nest_asyncio
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

# Setup custom logger
logger = CustomLogger().logger

# MongoDB Setup
def connect_mongo():
    retries = 5
    while retries > 0:
        try:
            client = MongoClient(DB_URL, serverSelectionTimeoutMS=5000)
            db = client['MoviesDB']
            collection = db['Movies']
            client.admin.command('ping')
            logger.info("MongoDB connection established")
            
            # Create indexes
            collection.create_index([("name", "text")])
            collection.create_index([("movie_id", 1)])
            
            return collection
        except errors.ServerSelectionTimeoutError as e:
            logger.error(f"MongoDB connection failed. Retrying... {e}")
            retries -= 1
            time.sleep(5)
    logger.critical("Failed to connect to MongoDB")
    return None

# Initialize global variables
collection = connect_mongo()
upload_sessions = {}
rate_limiter = RateLimiter(max_requests=5, time_window=datetime.timedelta(minutes=1))

# File validation
async def validate_file(file_info):
    MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2GB
    if file_info.file_size > MAX_FILE_SIZE:
        return False, "File size exceeds maximum limit of 2GB"
    
    ALLOWED_MIME_TYPES = ['video/mp4', 'video/x-matroska', 'video/avi']
    if file_info.mime_type not in ALLOWED_MIME_TYPES:
        return False, "Invalid file type. Only video files are allowed"
    
    return True, None

def get_session(user_id):
    if user_id in upload_sessions:
        session = upload_sessions[user_id]
        if session.is_expired():
            del upload_sessions[user_id]
            return UploadSession()
        return session
    session = UploadSession()
    upload_sessions[user_id] = session
    return session

def sanitize_unicode(text):
    return text.encode('utf-8', 'ignore').decode('utf-8')

async def add_movie(update: Update, context: CallbackContext):
    if update.effective_chat.id != STORAGE_GROUP_ID:
        await update.message.reply_text(
            sanitize_unicode("❌ You can only upload movies in the designated storage group")
        )
        return

    user_id = update.effective_user.id
    session = get_session(user_id)
    file_info = update.message.document
    image_info = update.message.photo
    caption = sanitize_unicode(update.message.caption or "")

    def clean_filename(filename):
        filename = re.sub(r'^@[\w_]+[\s-]*', '', filename)
        filename = re.sub(r'[^\x00-\x7F]+', '', filename)
        filename = filename.replace('_', ' ')
        pattern = r'(?i)(HDRip|10bit|x264|AAC|\d{3,4}MB|AMZN|WEB-DL|WEBRip|HEVC|250M|x265|ESub|HQ|\.mkv|\.mp4|\.avi|\.mov|BluRay|DVDRip|720p|1080p|540p|SD|HD|CAM|DVDScr|R5|TS|Rip|BRRip|AC3|DualAudio|6CH|v\d+)'
        filename = re.sub(pattern, '', filename).strip()
        
        match = re.search(r'^(.*?)[\s_]*\(?(\d{4})\)?[\s_]*(Malayalam|Tamil|Hindi|Telugu|English)?', filename, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            year = match.group(2).strip() if match.group(2) else ""
            language = match.group(3).strip() if match.group(3) else ""
            cleaned_name = f"{name} ({year}) {language}".strip()
            return re.sub(r'\s+', ' ', cleaned_name)
        return re.sub(r'\s+', ' ', filename).strip()

    if file_info:
        # Validate file
        is_valid, error_message = await validate_file(file_info)
        if not is_valid:
            await update.message.reply_text(sanitize_unicode(error_message))
            return
            
        filename = file_info.file_name
        cleaned_name = clean_filename(filename)
        session.files.append({
            'file_id': file_info.file_id,
            'file_name': cleaned_name
        })
        session.caption = caption or session.caption or cleaned_name
        await update.message.reply_text(
            sanitize_unicode(f"✅ {len(session.files)} file(s) received! Now, please upload an image for the related file(s)")
        )
    elif image_info:
        largest_photo = max(image_info, key=lambda photo: photo.width * photo.height)
        session.image = {
            'file_id': largest_photo.file_id,
            'width': largest_photo.width,
            'height': largest_photo.height
        }
        session.caption = caption or session.caption
        await update.message.reply_text(
            sanitize_unicode("✅ Image received! Now, please upload the movie file(s)")
        )

    if session.files and session.image:
        movie_name = session.files[0]['file_name']
        movie_id = str(uuid.uuid4())
        movie_entry = {
            'movie_id': movie_id,
            'name': movie_name,
            'media': {
                'documents': session.files,
                'image': session.image
            }
        }

        try:
            collection.insert_one(movie_entry)
            await update.message.reply_text(
                sanitize_unicode(f"✅ Successfully added movie: {movie_name}")
            )
            if SEARCH_GROUP_ID:
                await send_preview_to_group(context, movie_entry)
            del upload_sessions[user_id]
        except Exception as e:
            logger.error(f"Database error: {str(e)}")
            await update.message.reply_text(
                sanitize_unicode("❌ Failed to add the movie. Please try again later")
            )

async def search_movie(update: Update, context: CallbackContext):
    if update.effective_chat.id != SEARCH_GROUP_ID:
        await update.message.reply_text(
            sanitize_unicode("❌ Use this feature in the designated search group")
        )
        return

    user_id = update.effective_user.id
    if not rate_limiter.can_proceed(user_id):
        await update.message.reply_text(
            sanitize_unicode("Please wait before making another search request")
        )
        return

    movie_name = sanitize_unicode(update.message.text.strip())
    if not movie_name:
        await update.message.reply_text(
            sanitize_unicode("🚨 Provide a movie name to search")
        )
        return

    try:
        regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
        results = list(collection.find({"name": {"$regex": regex_pattern}}).limit(10))

        if not results:
            await update.message.reply_text(
                sanitize_unicode("No movies found matching your search")
            )
            return

        for result in results:
            await send_movie_preview(context, update.effective_chat.id, result)

    except Exception as e:
        logger.error(f"Search error: {str(e)}")
        await update.message.reply_text(
            sanitize_unicode("❌ An error occurred while searching")
        )

async def send_preview_to_group(context, movie_entry):
    try:
        await send_movie_preview(context, SEARCH_GROUP_ID, movie_entry)
    except Exception as e:
        logger.error(f"Error sending preview: {str(e)}")

async def send_movie_preview(context, chat_id, movie_entry):
    name = movie_entry.get('name', 'Unknown Movie')
    media = movie_entry.get('media', {})
    image_file_id = media.get('image', {}).get('file_id')
    deep_link = f"https://t.me/{context.bot.username}?start={movie_entry['movie_id']}"

    keyboard = [[InlineKeyboardButton("🎬 Download", url=deep_link)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if image_file_id:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=image_file_id,
                caption=sanitize_unicode(f"🎥 **{name}**"),
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=sanitize_unicode(f"🎥 **{name}**"),
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error sending preview: {str(e)}")

async def start(update: Update, context: CallbackContext):
    user_name = update.effective_user.full_name or "there"
    args = context.args

    if args and len(args) > 0:
        movie_id = args[0]
        movie = collection.find_one({"movie_id": movie_id})
        
        if movie:
            await send_movie_files(update, context, movie)
            return

    keyboard = [[InlineKeyboardButton("Add me to your chat! 🤖", url="https://t.me/+ERz0bGWEHHBmNTU9")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        text=f"Hi {sanitize_unicode(user_name)}! 👋 Use me to search movies. 🎥",
        reply_markup=reply_markup
    )

async def send_movie_files(update, context, movie):
    name = movie.get('name', 'Unknown Movie')
    documents = movie.get('media', {}).get('documents', [])
    
    await update.message.reply_text(
        sanitize_unicode(f"📤 Sending files for **{name}**"),
        parse_mode="Markdown"
    )

    try:
        tasks = []
        for doc in documents:
            task = context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=doc['file_id'],
                caption=sanitize_unicode(f"🎥 {doc.get('file_name', 'movie_file')}")
            )
            tasks.append(task)
        
        await asyncio.gather(*tasks)
        await update.message.reply_text(
            sanitize_unicode("✅ All files have been sent!")
        )
    except Exception as e:
        logger.error(f"Error sending files: {str(e)}")
        await update.message.reply_text(
            sanitize_unicode("❌ An error occurred while sending files")
        )

async def id_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"👤 Your ID: {user_id}\n💬 Group ID: {chat_id}"
    )

async def start_web_server():
    """Start a web server for health checks."""
    async def handle_health(request):
        return web.Response(text="Bot is running")

    app = web.Application()
    app.router.add_get('/', handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    
    try:
        await site.start()
        logger.info(f"Web server running on port {PORT}")
        return runner  # Return runner for cleanup
    except Exception as e:
        logger.error(f"Failed to start web server: {str(e)}")
        await runner.cleanup()
        return None

async def main():
    """Main function to start the bot."""
    web_runner = None
    try:
        # Start web server
        web_runner = await start_web_server()

        # Build and configure the application
        application = ApplicationBuilder().token(TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.Document.ALL, add_movie))
        application.add_handler(MessageHandler(filters.PHOTO, add_movie))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_movie))
        application.add_handler(CallbackQueryHandler(get_movie_files))
        application.add_handler(CommandHandler("id", id_command))

        # Start polling in a way that can be properly shutdown
        async with application:
            await application.start()
            logger.info("Bot started successfully")
            await application.run_polling(allowed_updates=Update.ALL_TYPES)
            
    except Exception as e:
        logger.error(f"Main loop error: {str(e)}")
    finally:
        # Cleanup
        if web_runner:
            await web_runner.cleanup()
        logger.info("Bot shutdown completed")

if __name__ == "__main__":
    try:
        # Create new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run the main function
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped manually")
    except Exception as e:
        logger.error(f"Unexpected error in main block: {str(e)}")
    finally:
        # Clean up the event loop
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")
