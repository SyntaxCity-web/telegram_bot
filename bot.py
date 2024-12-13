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

# Handlers
async def start(update: Update, context: CallbackContext):
    """Handle the /start command."""
    user_name = update.effective_user.full_name or "there"
    keyboard = [[InlineKeyboardButton("Add me to your chat! 🤖", url="https://t.me/+ERz0bGWEHHBmNTU9")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        text=f"Hi {sanitize_unicode(user_name)}! 👋 Use me to search or upload movies. 🎥",
        reply_markup=reply_markup
    )

# Temporary storage for incomplete movie uploads
upload_sessions = defaultdict(lambda: {'files': [], 'image': None, 'caption': None})

async def add_movie(update: Update, context: CallbackContext):
    """Process movie uploads, cleaning filenames and managing sessions."""
    # Ensure the correct chat ID
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

    # If the user uploads a document (movie file)
    if file_info:
        filename = file_info.file_name
        # Updated pattern to clean the filename
        pattern = r'(?i)(?:\[.*?\]\s*|\s*-?\s*(HDRip|10bit|x264|AAC|\d{3,4}MB|AMZN|WEB-DL|WEBRip|HEVC|x265|ESub|HQ|\.mkv|\.mp4|\.avi|\.mov|BluRay|DVDRip|720p|1080p|540p|SD|HD|CAM|DVDScr|R5|TS|Rip|BRRip|AC3|DualAudio|6CH))'
        cleaned_name = re.sub(pattern, '', filename, flags=re.IGNORECASE).strip()

        # Extract only title, year, and language if possible
        match = re.search(r'^(.*?)(\(?\d{4}\)?)?(.*?Malayalam|Hindi|Tamil|Telugu|English)?$', cleaned_name, flags=re.IGNORECASE)
        if match:
            cleaned_name = ' '.join(part.strip() for part in match.groups() if part)

        session['files'].append({
            'file_id': file_info.file_id,
            'file_name': cleaned_name
        })
        session['caption'] = caption or session.get('caption', cleaned_name)

        await update.message.reply_text(
            sanitize_unicode(f"✅ {len(session['files'])} file(s) received! Now, please upload an image for the related file(s).")
        )

    # If the user uploads an image (poster for the movie)
    elif image_info:
        largest_photo = max(image_info, key=lambda photo: photo.width * photo.height)
        session['image'] = {
            'file_id': largest_photo.file_id,
            'width': largest_photo.width,
            'height': largest_photo.height
        }
        session['caption'] = caption or session.get('caption')
        await update.message.reply_text(sanitize_unicode("✅ Image received! Now, please upload the movie file(s)."))

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
                await context.bot.send_message(
                    chat_id=SEARCH_GROUP_ID,
                    text=sanitize_unicode(f"New movie added: **{movie_name}**! 🎬\nCheck it out! 🍿")
                )
            
            del upload_sessions[user_id]
        except Exception as e:
            logging.error(f"Database error: {str(e)}")
            await update.message.reply_text(sanitize_unicode("❌ Failed to add the movie. Please try again later."))

    # If neither file nor image is provided
    if not (file_info or image_info):
        await update.message.reply_text(sanitize_unicode("❌ Please upload both a movie file and an image."))

async def search_movie(update: Update, context: CallbackContext):
    """Search for a movie in the database."""
    if update.effective_chat.id != SEARCH_GROUP_ID:
        await update.message.reply_text(sanitize_unicode("❌ Use this feature in the designated search group."))
        return

    movie_name = sanitize_unicode(update.message.text.strip())
    if not movie_name:
        await update.message.reply_text(sanitize_unicode("🚨 Provide a movie name to search. Use /search <movie_name>"))
        return

    try:
        # Search in the database
        regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
        results = list(collection.find({"name": {"$regex": regex_pattern}}).limit(10))

        if results:
            sent_message = await update.message.reply_text(
                sanitize_unicode(f"🔍 **Found {len(results)} result(s) for '{movie_name}':**"),
                parse_mode="Markdown"
            )
            
            # Track the sent message for potential deletion
            search_group_messages.append({
                "message_id": sent_message.message_id,
                "chat_id": update.effective_chat.id,
                "time": datetime.datetime.now(datetime.timezone.utc)
            })
            
            for result in results:
                name = result.get('name', 'Unknown Movie')
                media = result.get('media', {})

                # Get image and document file info
                image_file_id = media.get('image', {}).get('file_id')
                document_files = media.get('documents', [])

                # If both image and document are found
                if image_file_id and document_files:
                    # Send the image first
                    try:
                        image_message = await context.bot.send_photo(
                            chat_id=update.effective_chat.id,
                            photo=image_file_id,
                            caption=sanitize_unicode(f"🎥 **{name}**"),
                            parse_mode="Markdown"
                        )
                        search_group_messages.append({
                            "message_id": image_message.message_id,
                            "chat_id": update.effective_chat.id,
                            "time": datetime.datetime.now(datetime.timezone.utc)
                        })
                        
                        # Send each document related to the movie
                        for doc in document_files:
                            document_file_id = doc.get('file_id')
                            document_file_name = doc.get('file_name')
                            if document_file_id:
                                doc_message = await context.bot.send_document(
                                    chat_id=update.effective_chat.id,
                                    document=document_file_id,
                                    parse_mode="Markdown"
                                )
                                search_group_messages.append({
                                    "message_id": doc_message.message_id,
                                    "chat_id": update.effective_chat.id,
                                    "time": datetime.datetime.now(datetime.timezone.utc)
                                })
                    except Exception as e:
                        logging.error(f"Error sending media for {sanitize_unicode(name)}: {sanitize_unicode(str(e))}")
                else:
                    # If no image or document is found, just send a text message
                    text_message = await update.message.reply_text(
                        sanitize_unicode(f"🎥 **{name}** (media missing)"),
                        parse_mode="Markdown"
                    )
                    search_group_messages.append({
                        "message_id": text_message.message_id,
                        "chat_id": update.effective_chat.id,
                        "time": datetime.datetime.now(datetime.timezone.utc)
                    })
        else:
            await suggest_movies(update, movie_name)

    except Exception as e:
        logging.error(f"Search error: {sanitize_unicode(str(e))}")
        await update.message.reply_text(
            sanitize_unicode("❌ An unexpected error occurred. Please try again later.")
        )
async def suggest_movies(update: Update, movie_name: str):
    """Provide humorous suggestions for movie names with structured error handling."""
    try:
        # Helper function to validate the query length
        def is_query_too_short(query):
            return len(query) < 3

        # Validate the input query
        if is_query_too_short(movie_name):
            await update.message.reply_text(
                sanitize_unicode(
                    "🧐 Are you trying to win at Scrabble or find a movie? Give me at least 3 letters!"
                ),
                parse_mode="Markdown"
            )
            return

        # Fetch suggestions from the database
        suggestions = list(
            collection.find({"name": {"$regex": f".*{movie_name[:3]}.*", "$options": "i"}}).limit(5)
        )

        # Format and send suggestions to the user
        if suggestions:
            suggestion_text = "\n".join([sanitize_unicode(f"- {s['name']} (the classic everyone forgot)") for s in suggestions])
            await update.message.reply_text(
                sanitize_unicode(
                    f"🎥 No luck finding your movie, but here are some golden oldies you might like:\n{suggestion_text}"
                ),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                sanitize_unicode(
                    "🤔 I got nothing. Maybe you're trying to invent a new genre? Try a different term."
                )
            )

    except pymongo.errors.PyMongoError as db_error:
        logging.error(f"Database error in suggesting movies: {sanitize_unicode(str(db_error))}")
        await update.message.reply_text(
            sanitize_unicode(
                "💾 Oops! Looks like our movie database tripped over its own wires. Try again later."
            )
        )

    except Exception as e:
        logging.error(f"Unexpected error in suggesting movies: {sanitize_unicode(str(e))}")
        await update.message.reply_text(
            sanitize_unicode(
                "😱 Something went wrong. Did you break the internet? Please try again later."
            )
        )

async def welcome_new_member(update: Update, context: CallbackContext):
    """Welcome new members to the group with a cinematic flair."""
    for new_member in update.message.new_chat_members:
        user_name = sanitize_unicode(new_member.full_name or new_member.username or "Movie Fan")
        
        welcome_messages = [
            f"🎬 Fade in: {user_name} enters the scene! 🍿\n"
            "Welcome to our movie lovers' blockbuster chat!\n"
            "Your seat is ready, the popcorn's hot, let the show begin!",
            
            f"🎥 Starring... {user_name}! 🌟\n"
            "Breaking into our movie chat with a grand entrance!\n"
            "Plot twist: You're now part of the most epic film crew ever!",
            
            f"📽️ Director's Cut: Welcome, {user_name}! 🎞️\n"
            "You've just been cast in the most exciting movie chat ensemble!\n"
            "Your mission: Discover, discuss, and devour movies!"
        ]
        
        # Randomly select a welcome message
        welcome_text = random.choice(welcome_messages)    
        await update.message.reply_text(welcome_text)

async def goodbye_member(update: Update, context: CallbackContext):
    """Send a cinematic goodbye message when a member leaves the group."""
    left_member = update.message.left_chat_member
    user_name = sanitize_unicode(left_member.full_name or left_member.username or "Movie Enthusiast")
    
    goodbye_messages = [
       f"🎬 Breaking News: {user_name} has left the movie chat! 🍿\n"
        "Our ratings just dropped, but the show must go on!",    
            
        f"📽️ {user_name} has exited the building! 🚪\n"
        "Another plot twist in our cinematic journey...",
            
        f"🎥 Farewell, {user_name}! You've officially cut to black. 👋\n"
        "Hope your next chat is a blockbuster!"
    ]    
    # Randomly select a goodbye message
    goodbye_text = random.choice(goodbye_messages)   
    await update.message.reply_text(goodbye_text)


# Rest of the code remains the same as in the previous implementation...

async def delete_old_messages(application):
    """Delete messages in the search group that are older than 24 hours."""
    while True:
        try:
            # Use timezone-aware current time
            now = datetime.datetime.now(datetime.timezone.utc)
            threshold_seconds = 86400  # 24 hours
            
            messages_to_check = search_group_messages.copy()
            
            for message in messages_to_check:
                if (now - message["time"]).total_seconds() > threshold_seconds:
                    try:
                        await application.bot.delete_message(
                            chat_id=message["chat_id"], 
                            message_id=message["message_id"]
                        )
                        search_group_messages.remove(message)
                    except Exception as e:
                        logging.warning(f"Failed to delete message: {e}")
            
            await asyncio.sleep(3600)
        except Exception as e:
            logging.error(f"Error in delete_old_messages: {e}")
            await asyncio.sleep(10)

async def cleanup_database(update: Update, context: CallbackContext):
    """Remove old or unused movie entries from the database."""
    try:
        # Example: delete movies older than a certain date or with no media
        collection.delete_many({"created_at": {"$lt": datetime.datetime.now() - datetime.timedelta(days=365)}})
        await update.message.reply_text("🧹 Database cleaned up successfully.")
    except Exception as e:
        logging.error(f"Error during database cleanup: {e}")
        await update.message.reply_text("❌ An error occurred during cleanup.")


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
        application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
        application.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, goodbye_member))
        application.add_handler(CommandHandler("cleanup", cleanup_database))


        delete_task = asyncio.create_task(delete_old_messages(application))
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
