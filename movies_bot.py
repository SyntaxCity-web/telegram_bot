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
import difflib

# Apply nest_asyncio for nested event loops (useful in environments like Jupyter)
nest_asyncio.apply()

# Load environment variables
load_dotenv()

# Configuration variables from .env
TOKEN = os.getenv('TOKEN')
DB_URL = os.getenv('DB_URL')
SEARCH_GROUP_ID = int(os.getenv('SEARCH_GROUP_ID'))
STORAGE_GROUP_ID = int(os.getenv('STORAGE_GROUP_ID'))
ADMIN_ID = int(os.getenv('ADMIN_ID'))
PORT = int(os.getenv('PORT', 8080))  # Default to 8080 if not set

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# MongoDB client setup
def connect_mongo():
    retries = 5
    while retries > 0:
        try:
            client = MongoClient(DB_URL, serverSelectionTimeoutMS=5000)
            db = client['MoviesDB']
            collection = db['Movies']
            client.admin.command('ping')
            logging.info("MongoDB connection successful.")
            return collection
        except errors.ServerSelectionTimeoutError as e:
            logging.error(f"MongoDB connection failed. Retrying... {e}")
            retries -= 1
            time.sleep(5)
    logging.critical("Failed to connect to MongoDB.")
    return None

collection = connect_mongo()
search_group_messages = []

# Handlers
async def start(update: Update, context: CallbackContext):
    """Handle the /start command."""
    user_name = update.effective_user.full_name or "there"
    keyboard = [[InlineKeyboardButton("Add me to your chat! 🤖", url="https://t.me/+ERz0bGWEHHBmNTU9")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        text=f"Hi {user_name}! 👋 Use me to search or upload movies. 🎥", reply_markup=reply_markup
    )

async def add_movie(update: Update, context: CallbackContext):
    """Add a movie to the database when uploaded in the storage group."""
    if update.effective_chat.id != STORAGE_GROUP_ID:
        await update.message.reply_text("You can only upload movies in the designated storage group. 🎥")
        return

    file_info = update.message.document
    if file_info:
        movie_name = file_info.file_name
        file_id = file_info.file_id
        collection.insert_one({"name": movie_name, "file_id": file_id})
        await context.bot.send_message(chat_id=STORAGE_GROUP_ID, text=f"Added movie: {movie_name}")

async def search_movie(update: Update, context: CallbackContext):
    """
    Search for a movie in the database with advanced features and error handling.
    
    :param update: Telegram update object
    :param context: Callback context for the bot
    """
    # Validate chat context
    if update.effective_chat.id != SEARCH_GROUP_ID:
        await update.message.reply_text("❌ This feature can only be used in the designated search group. Please switch to the correct chat.")
        return

    # Extract and validate movie name
    movie_name = update.message.text.strip()
    if not movie_name:
        await update.message.reply_text("🚨 Please provide a movie name to search. Use /search <movie_name>")
        return

    try:
        # Create case-insensitive regex pattern with partial matching
        regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
        
        # Perform text search with more sophisticated matching
        results = list(collection.find({
            "$or": [
                {"name": {"$regex": regex_pattern}},
                {"name": {"$regex": f".*{re.escape(movie_name)}.*", "$options": "i"}}
            ]
        }).limit(10))  # Limit results to prevent overwhelming response

        if results:
            # Header message with result count
            await update.message.reply_text(
                f"🔍 **Found {len(results)} result(s) for '{movie_name}':**",
                parse_mode="Markdown"
            )

            # Process and send results with pagination-like handling
            for result in results:
                name = result.get('name', 'Unknown Movie')
                file_id = result.get('file_id')
                
                # Send movie details with file if available
                if file_id:
                    try:
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id, 
                            document=file_id, 
                            caption=f"🎥 **{name}**", 
                            parse_mode="Markdown"
                        )
                    except Exception as file_error:
                        logging.error(f"Error sending file for {name}: {file_error}")
                        await update.message.reply_text(f"🎥 **{name}**", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"🎥 **{name}**", parse_mode="Markdown")

        else:
            # Advanced suggestions with more comprehensive search
            suggestions = list(
                collection.find({
                    "$or": [
                        {"name": {"$regex": f".*{movie_name[:3]}.*", "$options": "i"}},
                        {"name": {"$regex": f".*{difflib.get_close_matches(movie_name, [doc['name'] for doc in collection.find()], n=3, cutoff=0.6)[0] if difflib.get_close_matches(movie_name, [doc['name'] for doc in collection.find()], n=3, cutoff=0.6) else ''}.*", "$options": "i"}}
                    ]
                }).limit(5)
            )

            if suggestions:
                suggestion_text = "\n".join([f"- {s['name']}" for s in suggestions])
                await update.message.reply_text(
                    f"😔 Movie not found. Did you mean:\n{suggestion_text}",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    "🤷‍♂️ No movies found. Try a different search term or check your spelling carefully.",
                    parse_mode="Markdown"
                )

    except Exception as e:
        # Comprehensive error handling
        logging.error(f"Search error: {e}")
        await update.message.reply_text(
            "❌ An unexpected error occurred during the search. Please try again later.",
            parse_mode="Markdown"
        )
async def welcome_new_member(update: Update, context: CallbackContext):
    """Welcome new members to the movie search group."""
    if update.message.new_chat_members:
        for new_member in update.message.new_chat_members:
            user_name = new_member.full_name or new_member.username or "Movie Fan"
            
            welcome_text = (
                f"Welcome, {user_name}! 🎬\n\n"
                "Search for any movie by typing its title. Easy as that! 🍿\n"
                "Enjoy exploring films with us! 🎥"
            )
            
            await update.message.reply_text(welcome_text)

async def delete_old_messages(application):
    """Delete messages in the search group that are older than 24 hours."""
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            to_delete = [
                msg for msg in search_group_messages
                if (now - msg["time"]).total_seconds() > 86400
            ]
            for message in to_delete:
                try:
                    await application.bot.delete_message(chat_id=message["chat_id"], message_id=message["message_id"])
                    search_group_messages.remove(message)
                except Exception as e:
                    logging.warning(f"Failed to delete message: {e}")
            await asyncio.sleep(3600)
        except Exception as e:
            logging.error(f"Error in delete_old_messages: {e}")
            await asyncio.sleep(10)

async def start_web_server():
    """Start a simple web server for health checks."""
    from aiohttp import web

    async def handle_health(request):
        return web.Response(text="Bot is running")

    app = web.Application()
    app.router.add_get('/', handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f"Web server started on port {PORT}")

async def main():
    """Main function to start the bot."""
    application = None
    delete_task = None

    try:
        # Start web server for health checks
        await start_web_server()

        # Build the Telegram bot application
        application = ApplicationBuilder().token(TOKEN).build()

        # Register handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.Document.ALL, add_movie))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_movie))
        application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))

        logging.info("Starting bot...")

        # Start auxiliary task
        delete_task = asyncio.create_task(delete_old_messages(application))

        # Run polling (blocks until stopped)
        await application.run_polling()
    except asyncio.CancelledError:
        logging.info("Bot operation cancelled.")
    except Exception as e:
        logging.error(f"Error in main: {e}")
    finally:
        logging.info("Shutting down bot...")

        # Stop application if it was initialized
        if application:
            try:
                await application.shutdown()
            except Exception as e:
                logging.error(f"Error during application shutdown: {e}")

        # Cancel and clean up delete task
        if delete_task:
            delete_task.cancel()
            try:
                await delete_task
            except asyncio.CancelledError:
                logging.info("Delete task cleaned up.")
        
        logging.info("Cleanup complete.")

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Use the running event loop
            loop.create_task(main())
        else:
            # Start a new event loop
            loop.run_until_complete(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped manually.")
    except Exception as e:
        logging.error(f"Unexpected error in main block: {e}")
