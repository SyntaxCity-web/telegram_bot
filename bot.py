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

# Custom Timezone Formatter
class TimezoneFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        # Use Indian Standard Time (IST)
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
PORT = int(os.getenv('PORT', 8088))  # Default to 8088 if not set

# Logging Configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S %Z',  # Include timezone in the date format
    handlers=[
        logging.StreamHandler(),  # Console output
        logging.FileHandler('bot.log', encoding='utf-8')  # Log to file
    ]
)

# Get the root logger and apply the custom formatter
logger = logging.getLogger()
for handler in logger.handlers:
    handler.setFormatter(TimezoneFormatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S %Z'
    ))

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

# Helper function to sanitize Unicode text
def sanitize_unicode(text):
    """
    Sanitize Unicode text to remove invalid characters, such as surrogate pairs.
    """
    return text.encode('utf-8', 'ignore').decode('utf-8')

# Temporary storage for incomplete movie uploads
upload_sessions = defaultdict(lambda: {'files': [], 'image': None, 'caption': None})

async def add_movie(update: Update, context: CallbackContext):
    """Process movie uploads, cleaning filenames and managing sessions."""
    
    def clean_filename(filename):
        """Clean the uploaded filename by removing unnecessary tags."""
        
        # Remove emojis
        filename = re.sub(r'[^\x00-\x7F]+', '', filename)

        # Replace underscores with spaces
        filename = filename.replace('_', ' ')
        
        # Remove unwanted tags
        pattern = r'(?i)(?:\[.*?\]\s*|\s*-?\s*(HDRip|10bit|x264|AAC|\d{3,4}MB|AMZN|WEB-DL|WEBRip|HEVC|250M|x265|ESub|HQ|\.mkv|\.mp4|\.avi|\.mov|BluRay|DVDRip|720p|1080p|540p|SD|HD|CAM|DVDScr|R5|TS|Rip|BRRip|AC3|DualAudio|6CH|v\d+))'
        cleaned_name = re.sub(pattern, '', filename, flags=re.IGNORECASE).strip()
        
        # Extract movie name, year, and language
        match = re.search(r'^(.*?)(\(?\d{4}\)?)(.*?Tamil|Malayalam|Hindi|Telugu|English)?$', cleaned_name, flags=re.IGNORECASE)

        if match:
            # Concatenate parts with proper spacing
            parts = [part.strip() for part in match.groups() if part]
            cleaned_name = ' '.join(parts)
            
        # Remove multiple spaces if any
        return re.sub(r'\s+', ' ', cleaned_name).strip()

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

    def create_deep_link(movie_id):
        """Generate a deep link to the movie."""
        return f"https://t.me/{context.bot.username}?start={movie_id}"

    async def send_preview_to_group(movie_entry):
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
                    chat_id=SEARCH_GROUP_ID,
                    photo=image_file_id,
                    caption=sanitize_unicode(f"🎥 **{name}**"),
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
            else:
                await context.bot.send_message(
                    chat_id=SEARCH_GROUP_ID,
                    text=sanitize_unicode(f"🎥 **{name}**"),
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
        except Exception as e:
            logging.error(f"Error sending preview for {sanitize_unicode(name)}: {sanitize_unicode(str(e))}")

    # Main Logic
    if update.effective_chat.id != STORAGE_GROUP_ID:
        await update.message.reply_text(
            sanitize_unicode("❌ You can only upload movies in the designated storage group. 🎥")
        )
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

    # Check if both files and image are present
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
            await update.message.reply_text(sanitize_unicode(f"✅ Successfully added movie: {movie_name}"))

            if SEARCH_GROUP_ID:
                await send_preview_to_group(movie_entry)

            del upload_sessions[user_id]
        except Exception as e:
            logging.error(f"Database error: {str(e)}")
            await update.message.reply_text(sanitize_unicode("❌ Failed to add the movie. Please try again later."))

    elif not (file_info or image_info):
        await update.message.reply_text(sanitize_unicode("❌ Please upload both a movie file and an image."))
               
async def search_movie(update: Update, context: CallbackContext):
    """
    Search for a movie in the database and send preview to group.
    Clicking the deep link opens the bot's PM, where the user can download files.
    """
    # Validate the command usage
    if update.effective_chat.id != SEARCH_GROUP_ID:
        await update.message.reply_text(
            sanitize_unicode("❌ Use this feature in the designated search group.")
        )
        return
    # Get the movie name from the user's message
    movie_name = sanitize_unicode(update.message.text.strip())
    if not movie_name:
        await update.message.reply_text(
            sanitize_unicode("🚨 Provide a movie name to search. Use /search <movie_name>")
        )
        return

    try:
        # Search for the movie in the database
        regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
        results = list(collection.find({"name": {"$regex": regex_pattern}}).limit(10))

        if results:
            # Send preview messages for each movie result
            for result in results:
                name = result.get('name', 'Unknown Movie')
                media = result.get('media', {})
                image_file_id = media.get('image', {}).get('file_id')

                # Generate a direct deep link for bot PM with the movie ID
                deep_link = f"https://t.me/{context.bot.username}?start={result['movie_id']}"

                # Create an inline keyboard for the deep link
                keyboard = [
                    [InlineKeyboardButton(
                        "🎬 Download", 
                        url=deep_link
                    )],
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                # Send movie preview with an image if available
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
                        logging.error(
                            f"Error sending preview for {sanitize_unicode(name)}: {sanitize_unicode(str(e))}"
                        )
                else:
                    # If no image is available, send a text preview
                    await update.message.reply_text(
                        sanitize_unicode(f"🎥 **{name}**"),
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
        else:
            # Suggest similar movies or inform the user no results were found
            await suggest_movies(update, movie_name)

    except Exception as e:
        logging.error(f"Search error: {sanitize_unicode(str(e))}")
        await update.message.reply_text(
            sanitize_unicode("❌ An unexpected error occurred. Please try again later.")
        )
        
# New handler for retrieving movie files
async def get_movie_files(update: Update, context: CallbackContext):
    """Send movie files to user via private message."""
    query = update.callback_query
    await query.answer()

    # Extract movie ID from callback data
    movie_id = query.data.split('_')[1]

    try:
        # Fetch movie details from database
        movie = collection.find_one({"movie_id": movie_id})
        
        if movie and 'media' in movie and 'documents' in movie['media']:
            # Send a message to the user
            await query.message.reply_text(
                sanitize_unicode(f"📤 Sending files for **{movie.get('name', 'Movie')}**"),
                parse_mode="Markdown"
            )

            # Send each document related to the movie
            for doc in movie['media']['documents']:
                document_file_id = doc.get('file_id')
                document_file_name = doc.get('file_name', 'movie_file')
                
                if document_file_id:
                    try:
                        await context.bot.send_document(
                            chat_id=query.from_user.id,
                            document=document_file_id,
                            caption=sanitize_unicode(f"🎥 {document_file_name}")
                        )
                    except Exception as e:
                        logging.error(f"Error sending document: {sanitize_unicode(str(e))}")
            
            # Optional: Send a completion message
            await query.message.reply_text(
                sanitize_unicode("✅ All files have been sent!")
            )
        else:
            await query.message.reply_text(
                sanitize_unicode("❌ No files found for this movie.")
            )
    
    except Exception as e:
        logging.error(f"Error fetching files for movie {movie_id}: {sanitize_unicode(str(e))}")
        await query.message.reply_text(
            sanitize_unicode("❌ An error occurred while fetching the movie files.")
        )

async def start(update: Update, context: CallbackContext):
    """Handle the /start command with default features or deep link for movies."""
    user_name = update.effective_user.full_name or "there"
    args = context.args

    if args and len(args) > 0:
        # Deep link with movie_id
        movie_id = args[0]
        
        # Fetch movie details from database
        movie = collection.find_one({"movie_id": movie_id})
        
        if movie:
            name = movie.get('name', 'Unknown Movie')
            media = movie.get('media', {})
            image_file_id = media.get('image', {}).get('file_id')
            documents = media.get('documents', [])

            # Send image preview if available
            if image_file_id:
                try:
                    await update.message.reply_photo(
                        photo=image_file_id,
                        caption=sanitize_unicode(f"🎥 **{name}**\n\nFiles available: {len(documents)}"),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logging.error(f"Error sending movie details: {sanitize_unicode(str(e))}")
            # Send movie files
            for doc in documents:
                document_file_id = doc.get('file_id')
                document_file_name = doc.get('file_name', 'movie_file')
                if document_file_id:
                    try:
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=document_file_id
                        )
                    except Exception as e:
                        logging.error(f"Error sending file: {sanitize_unicode(str(e))}")

            return
    # Default behavior when no movie_id is provided
    keyboard = [[InlineKeyboardButton("Add me to your chat! 🤖", url="https://t.me/+ERz0bGWEHHBmNTU9")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        text=f"Hi {sanitize_unicode(user_name)}! 👋 Use me to search. 🎥",
        reply_markup=reply_markup
    )

# Define the /id command handler
async def id_command(update: Update, context: CallbackContext):
    """Respond with the user's ID and the group ID."""
    user_id = update.effective_user.id  # Get the user's ID
    chat_id = update.effective_chat.id  # Get the group/chat ID

    # Construct the response
    response = (
        f"👤 Your ID: {user_id}\n"
        f"💬 Group ID: {chat_id}"
    )

    # Send the response back to the user
    await update.message.reply_text(response)

async def start_web_server():
    """Start a web server for health checks."""
    async def handle_health(request):
        return web.Response(text="Bot is running")

    app = web.Application()
    app.router.add_get('/', handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f"Web server running on port {PORT}")

async def main():
    """Main function to start the bot."""
    try:
        await start_web_server()

        application = ApplicationBuilder().token(TOKEN).build()
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
