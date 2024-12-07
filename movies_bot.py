import logging
import re
import datetime
import asyncio
import nest_asyncio
import os
from typing import Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext
)
from pymongo import MongoClient, errors
from aiohttp import web

# Patch asyncio to allow nested event loops (if needed)
nest_asyncio.apply()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment Configuration
class Config:
    TOKEN = os.getenv('TELEGRAM_TOKEN')
    DB_URL = os.getenv('MONGODB_URL')
    SEARCH_GROUP_ID = int(os.getenv('SEARCH_GROUP_ID', 0))
    STORAGE_GROUP_ID = int(os.getenv('STORAGE_GROUP_ID', 0))
    ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
    PORT = int(os.getenv('PORT', 8080))

# Database Setup
class DatabaseManager:
    def __init__(self, db_url):
        try:
            self.client = MongoClient(db_url)
            self.db = self.client['MoviesDB']
            self.collection = self.db['Movies']
        except errors.PyMongoError as e:
            logger.error(f"MongoDB Connection Error: {e}")
            raise

    def add_movie(self, movie_name: str, file_id: str):
        """Add a movie to the database."""
        try:
            return self.collection.insert_one({"name": movie_name, "file_id": file_id})
        except errors.PyMongoError as e:
            logger.error(f"Error adding movie: {e}")
            raise

    def search_movies(self, query: str):
        """Search for movies with case-insensitive regex."""
        try:
            regex_pattern = re.compile(re.escape(query), re.IGNORECASE)
            return list(self.collection.find({"name": {"$regex": regex_pattern}}))
        except errors.PyMongoError as e:
            logger.error(f"Error searching movies: {e}")
            raise

# Message Tracking
search_group_messages: List[Dict] = []

class MovieBot:
    def __init__(self, token: str, db_manager: DatabaseManager):
        self.token = token
        self.db_manager = db_manager
        self.application = None

    async def start_command(self, update: Update, context: CallbackContext):
        """Handle the /start command."""
        try:
            user_name = update.effective_user.full_name
            keyboard = [[
                InlineKeyboardButton("Add me to your chat! 🤖", url="https://t.me/+YourInviteLink")
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            welcome_message = (
                f"Hi {user_name}! 👋 I'm Olive, your group assistant. 🎉\n"
                f"Have fun! 😄"
            )
            await update.message.reply_text(text=welcome_message, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error in /start command: {e}")
            await update.message.reply_text("Oops! Something went wrong. 😕 Please try again later.")

    async def add_movie(self, update: Update, context: CallbackContext):
        """Handle movie file uploads in the storage group."""
        try:
            if update.effective_chat.id != Config.STORAGE_GROUP_ID:
                await update.message.reply_text("Please upload movies in the designated storage group. 🎬")
                return

            if update.message.document:
                movie_name = update.message.document.file_name
                file_id = update.message.document.file_id
                self.db_manager.add_movie(movie_name, file_id)
                await update.message.reply_text(f"Added movie: {movie_name} 🎥")
            else:
                await update.message.reply_text("No file found. Please send a movie file. 📁")
        except Exception as e:
            logger.error(f"Error adding movie: {e}")
            await update.message.reply_text("Oops! Something went wrong. 😕 Please try again later.")

    async def search_movie(self, update: Update, context: CallbackContext):
        """Search for a movie in the database."""
        try:
            if update.effective_chat.id != Config.SEARCH_GROUP_ID:
                await update.message.reply_text("Use this feature in the search group. 🔍")
                return

            movie_name = update.message.text.strip()
            if not movie_name:
                await update.message.reply_text("Please enter a valid movie name. 🎬")
                return

            results = self.db_manager.search_movies(movie_name)

            if results:
                for result in results:
                    await update.message.reply_text(f"Found movie: {result['name']} 🎥")
                    await context.bot.send_document(chat_id=update.effective_chat.id, document=result['file_id'])
            else:
                await update.message.reply_text("Movie not found. 😔 Try a different search.")
        except Exception as e:
            logger.error(f"Error searching movie: {e}")
            await update.message.reply_text("Oops! Something went wrong. 😕 Please try again later.")

    async def welcome_new_member(self, update: Update, context: CallbackContext):
        """Send a welcome message when a new user joins the search group."""
        try:
            if update.effective_chat.id == Config.SEARCH_GROUP_ID:
                for member in update.message.new_chat_members:
                    welcome_message = (
                        f"👋 Welcome {member.full_name}! 🎉\n\n"
                        f"I'm Olive, your group assistant. 🤖\n"
                        f"Feel free to ask for a movie by its name, and I'll try to find it for you. 🎥 "
                        f"Enjoy your stay! 😄"
                    )
                    await context.bot.send_message(chat_id=Config.SEARCH_GROUP_ID, text=welcome_message)
        except Exception as e:
            logger.error(f"Error welcoming new member: {e}")

    async def delete_old_messages(self, context: CallbackContext):
        """Delete messages in the search group older than 24 hours."""
        try:
            now = datetime.datetime.utcnow()
            to_delete = []

            for message in search_group_messages.copy():
                if (now - message["time"]).total_seconds() > 86400:  # 24 hours
                    try:
                        await context.bot.delete_message(chat_id=message["chat_id"], message_id=message["message_id"])
                        to_delete.append(message)
                    except Exception as e:
                        logger.error(f"Failed to delete message {message['message_id']}: {e}")

            for message in to_delete:
                search_group_messages.remove(message)
        except Exception as e:
            logger.error(f"Error in delete_messages_task: {e}")

    def setup_handlers(self):
        """Set up bot handlers."""
        self.application = ApplicationBuilder().token(self.token).build()

        # Command Handlers
        self.application.add_handler(CommandHandler("start", self.start_command))

        # Message Handlers
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.add_movie))
        self.application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.welcome_new_member))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.search_movie))

        # Job Queue for Message Deletion
        self.application.job_queue.run_repeating(self.delete_old_messages, interval=3600, first=0)

    async def start_webhook(self, app):
        """Start webhook server for Render deployment."""
        async def handle_webhook(request):
            return web.Response(text="Bot is running")

        app.router.add_get('/', handle_webhook)
        return app

    async def run(self):
        """Run the bot with webhook support."""
        self.setup_handlers()

        # Create web app for port binding
        app = web.Application()
        await self.start_webhook(app)

        # Start web server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', Config.PORT)
        await site.start()

        logger.info(f"Webhook server started on port {Config.PORT}")

        # Start bot polling
        await self.application.run_polling()

async def main():
    """Main entry point for the application."""
    # Validate configuration
    if not all([Config.TOKEN, Config.DB_URL, Config.SEARCH_GROUP_ID, Config.STORAGE_GROUP_ID]):
        logger.error("Missing required environment variables")
        return

    try:
        # Initialize database and bot
        db_manager = DatabaseManager(Config.DB_URL)
        movie_bot = MovieBot(Config.TOKEN, db_manager)
        
        # Run the bot
        await movie_bot.run()
    except Exception as e:
        logger.error(f"Critical startup error: {e}")

if __name__ == "__main__":
    import sys
    
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unhandled critical error: {e}")
        sys.exit(1)
