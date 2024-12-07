import logging
import re
import datetime
import asyncio
from pymongo import MongoClient, errors
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackContext, filters
)
import os
import aiohttp
from aiohttp import web

# Configuration variables
TOKEN = os.environ.get('TOKEN')
DB_URL = os.environ.get('DB_URL')
SEARCH_GROUP_ID = int(os.environ.get('SEARCH_GROUP_ID'))
STORAGE_GROUP_ID = int(os.environ.get('STORAGE_GROUP_ID'))
PORT = int(os.environ.get('PORT', 8080))  # Default to 8080 if not set

# MongoDB client setup
try:
    client = MongoClient(DB_URL)
    db = client['MoviesDB']
    collection = db['Movies']
except errors.PyMongoError as e:
    logging.error(f"Error connecting to MongoDB: {e}")
    exit(1)

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Track messages for scheduled deletion
search_group_messages = []

# Helper function to check if a chat is the search group
def is_in_group(chat_id, group_id):
    return chat_id == group_id

async def start(update: Update, context: CallbackContext):
    """Handle the /start command."""
    try:
        user_name = update.effective_user.full_name or "there"
        keyboard = [[
            InlineKeyboardButton("Add me to your chat! 🤖", url="https://t.me/+ERz0bGWEHHBmNTU9")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        welcome_message = (
            f"Hi {user_name}! 👋 I'm Olive, your group assistant. 🎉\n"
            "Use me to search for movies, or upload a movie to the storage group! 🎥"
        )
        await update.message.reply_text(text=welcome_message, reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"Error in /start command: {e}")
        await update.message.reply_text("Oops! Something went wrong. 😕 Please try again later.")

async def add_movie(update: Update, context: CallbackContext):
    """Add a movie to the database when uploaded in the storage group."""
    try:
        if update.effective_chat.id != STORAGE_GROUP_ID:
            await update.message.reply_text("You can only upload movies in the designated storage group. 🎥")
            return

        file_info = update.message.document
        if file_info:
            movie_name = file_info.file_name
            file_id = file_info.file_id
            collection.insert_one({"name": movie_name, "file_id": file_id})
            await context.bot.send_message(chat_id=STORAGE_GROUP_ID, text=f"Added movie: {movie_name}")
    except Exception as e:
        logging.error(f"Error in add_movie: {e}")
        await update.message.reply_text("An error occurred while adding the movie. 😕")

async def search_movie(update: Update, context: CallbackContext):
    """Search for a movie in the database with improved formatting."""
    try:
        if not is_in_group(update.effective_chat.id, SEARCH_GROUP_ID):
            await update.message.reply_text("Use this feature in the search group. 🔍")
            return

        movie_name = update.message.text.strip()
        if not movie_name:
            msg = await update.message.reply_text("Please enter a valid movie name. 🎬")
            search_group_messages.append({"chat_id": msg.chat_id, "message_id": msg.message_id, "time": datetime.datetime.utcnow()})
            return

        # Search using regex
        regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
        results = list(collection.find({"name": {"$regex": regex_pattern}}))

        if results:
            # Pagination setup
            results_per_page = 5
            page = 0
            num_pages = len(results) // results_per_page + (1 if len(results) % results_per_page != 0 else 0)

            def format_results(page):
                start = page * results_per_page
                end = start + results_per_page
                page_results = results[start:end]

                buttons = []
                for result in page_results:
                    file_info = f"{result['name']} ({result['file_size']})\n" \
                                f"Quality: {result['quality']}\n" \
                                f"Language: {result['language']}"
                    buttons.append([
                        InlineKeyboardButton(f"📂 {result['name']}", url=f"https://t.me/{result['file_id']}")
                    ])

                return buttons

            # Show results with pagination
            buttons = format_results(page)
            keyboard = [
                *buttons,
                [
                    InlineKeyboardButton("Next ▶️", callback_data=f"next:{page + 1}" if page + 1 < num_pages else None),
                    InlineKeyboardButton("Previous ◀️", callback_data=f"prev:{page - 1}" if page > 0 else None),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"Found {len(results)} Files for {movie_name}", reply_markup=reply_markup)
        else:
            msg = await update.message.reply_text("Movie not found. 😔 Try a different search.")
            search_group_messages.append({"chat_id": msg.chat_id, "message_id": msg.message_id, "time": datetime.datetime.utcnow()})
    except errors.PyMongoError as e:
        logging.error(f"MongoDB error while searching for movie: {e}")
        await update.message.reply_text("There was an error while searching. 🛑 Please try again later.")
    except Exception as e:
        logging.error(f"Error searching movie: {e}")
        await update.message.reply_text("Oops! Something went wrong. 😕 Please try again later.")


async def page_callback(update: Update, context: CallbackContext):
    """Handle the pagination for next and previous buttons."""
    callback_data = update.callback_query.data
    if callback_data.startswith("next:") or callback_data.startswith("prev:"):
        page = int(callback_data.split(":")[1])
        await search_movie(update, context)  # You can also send page-specific results here



async def delete_old_messages(application: ApplicationBuilder):
    """Delete messages in the search group that are older than 24 hours."""
    while True:
        try:
            now = datetime.datetime.utcnow()
            to_delete = []

            for message in search_group_messages:
                if (now - message["time"]).total_seconds() > 86400:  # 24 hours
                    try:
                        await application.bot.delete_message(chat_id=message["chat_id"], message_id=message["message_id"])
                        to_delete.append(message)
                    except Exception as e:
                        logging.error(f"Failed to delete message {message['message_id']}: {e}")

            for message in to_delete:
                search_group_messages.remove(message)

            await asyncio.sleep(3600)  # Check hourly
        except Exception as e:
            logging.error(f"Error in delete_old_messages task: {e}")

async def welcome_new_member(update: Update, context: CallbackContext):
    """Send a welcome message when a new user joins the search group."""
    for member in update.message.new_chat_members:
        await context.bot.send_message(chat_id=SEARCH_GROUP_ID, text=f"Welcome {member.full_name}! 🎉 Ask for a movie name, and I'll help you find it. 🔍")

async def handle_text_message(update: Update, context: CallbackContext):
    """Handle text messages in the search group."""
    await search_movie(update, context)

# Add a simple web handler for health checks
async def handle_webhook(request):
    return web.Response(text="Bot is running")

async def start_web_server():
    """Start a simple web server to keep the application alive"""
    app = web.Application()
    app.router.add_get('/', handle_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f"Web server started on port {PORT}")
    return runner

async def main():
    """Start the bot"""
    # Start web server first
    web_runner = await start_web_server()

    application = ApplicationBuilder().token(TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start))

    # Message handlers
    application.add_handler(MessageHandler(filters.Document.ALL, add_movie))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Start the background task for deleting old messages
    asyncio.create_task(delete_old_messages(application))

    try:
        # Initialize the application
        await application.initialize()
        logging.info("Application initialized.")

        # Start the bot
        await application.start()
        logging.info("Bot started.")

        # Start polling for updates
        await application.updater.start_polling()
        logging.info("Polling for updates started.")

        # Keep the event loop running
        await asyncio.Event().wait()

    except Exception as e:
        logging.error(f"Error in main function: {e}")
    
    finally:
        # Graceful shutdown
        logging.info("Shutting down the bot...")
        await application.stop()
        await application.shutdown()
        await web_runner.cleanup()
        logging.info("Bot and web server shut down successfully.")

if __name__ == "__main__":
    try:
        # Attempt to start the main function with asyncio.run()
        asyncio.run(main())
    except RuntimeError as e:
        if "This event loop is already running" in str(e):
            logging.warning("Detected running event loop, switching to asyncio.create_task().")
            loop = asyncio.get_event_loop()
            loop.create_task(main())  # Create a task for the main coroutine
            loop.run_forever()  # Keep the loop running
        else:
            logging.error(f"Unexpected RuntimeError: {e}")
            raise
