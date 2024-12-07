import logging
import os
import re
import sys
import asyncio
import nest_asyncio
import datetime
from typing import Dict, List, Optional

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
from dotenv import load_dotenv

# Load environment variables from a .env file
load_dotenv()

# Patch asyncio to allow nested event loops (if needed)
nest_asyncio.apply()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Custom exception for configuration errors."""
    pass


class Config:
    """Configuration management with extensive validation."""
    @classmethod
    def get_env_variable(cls, var_name: str, required: bool = True, default: Optional[str] = None) -> str:
        """
        Retrieve and validate an environment variable.
        
        :param var_name: Name of the environment variable
        :param required: Whether the variable is required
        :param default: Default value if not required
        :return: Value of the environment variable
        :raises ConfigurationError: If required variable is missing
        """
        value = os.getenv(var_name)
        
        if value is None:
            if required:
                logger.error(f"Environment variable {var_name} is missing. Ensure it is set before running the bot.")
                raise ConfigurationError(f"Missing required environment variable: {var_name}")
            return default
        
        return value.strip()

    @classmethod
    def validate_config(cls):
        """
        Validate all critical configuration variables.
        
        :raises ConfigurationError: If any required configuration is invalid
        """
        try:
            # Retrieve and validate each critical configuration variable
            cls.TOKEN = cls.get_env_variable('TELEGRAM_TOKEN')
            cls.DB_URL = cls.get_env_variable('MONGODB_URL')
            
            # Group IDs require special handling to ensure they are valid integers
            search_group_str = cls.get_env_variable('SEARCH_GROUP_ID')
            storage_group_str = cls.get_env_variable('STORAGE_GROUP_ID')
            
            try:
                cls.SEARCH_GROUP_ID = int(search_group_str)
                cls.STORAGE_GROUP_ID = int(storage_group_str)
            except ValueError:
                raise ConfigurationError(
                    "SEARCH_GROUP_ID and STORAGE_GROUP_ID must be valid integers"
                )
            
            # Optional variables with defaults
            cls.ADMIN_ID = int(cls.get_env_variable('ADMIN_ID', required=False, default='0'))
            cls.PORT = int(cls.get_env_variable('PORT', required=False, default='8080'))
            
            logger.info("Configuration validated successfully.")
        
        except (ConfigurationError, ValueError) as e:
            logger.error(f"Configuration validation failed: {e}")
            raise


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
        """Run the bot."""
        try:
            self.setup_handlers()
            await self.application.run_polling()
        except Exception as e:
            logger.error(f"Error starting bot: {e}")
            sys.exit(1)


async def main():
    """Main entry point for the bot."""
    try:
        Config.validate_config()
        db_manager = DatabaseManager(Config.DB_URL)
        bot = MovieBot(token=Config.TOKEN, db_manager=db_manager)
        await bot.run()
    except ConfigurationError as e:
        logger.error(f"Configuration Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
