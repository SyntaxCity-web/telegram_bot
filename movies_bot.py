import logging
import re
import asyncio
import nest_asyncio
from typing import List, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    MessageHandler, 
    CallbackContext, 
    filters
)
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Enhanced logging configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    filename='bot_logs.log',
    filemode='a'
)
logger = logging.getLogger(__name__)

class MovieBot:
    def __init__(self):
        """Initialize bot configuration and database connection."""
        self.TOKEN = self._get_env_variable('TOKEN')
        self.DB_URL = self._get_env_variable('DB_URL')
        self.SEARCH_GROUP_ID = self._get_env_variable('SEARCH_GROUP_ID')
        self.STORAGE_GROUP_ID = self._get_env_variable('STORAGE_GROUP_ID')
        self.ADMIN_ID = self._get_env_variable('ADMIN_ID')

        # Enhanced MongoDB connection with error handling
        try:
            self.client = MongoClient(self.DB_URL, serverSelectionTimeoutMS=5000)
            self.client.server_info()  # Verify connection
            self.db = self.client['MoviesDB']
            self.collection: Collection = self.db['Movies']
        except PyMongoError as e:
            logger.error(f"Database connection error: {e}")
            raise

        # Configurable funny responses with more flexibility
        self.FUNNY_RESPONSES: Dict[str, str] = {
            "what's your favorite color?": "I love the color of binary! 0s and 1s are so pretty!",
            "tell me a joke": "Why did the scarecrow win an award? Because he was outstanding in his field!",
            "how are you?": "I'm just a bunch of code, but thanks for asking! How are you?",
            "do you believe in love?": "Of course! But I'm still waiting for my algorithm to find the one!",
            "what's your purpose?": "To make your life easier, one response at a time! And to tell jokes!"
        }

    def _get_env_variable(self, var_name: str) -> str:
        """
        Retrieve environment variable with error handling.
        
        Args:
            var_name (str): Name of the environment variable
        
        Returns:
            str: Value of the environment variable
        
        Raises:
            ValueError: If environment variable is not set
        """
        value = os.getenv(var_name)
        if not value:
            logger.error(f"Environment variable {var_name} not set")
            raise ValueError(f"Environment variable {var_name} is required")
        return value

    async def start(self, update: Update, context: CallbackContext) -> None:
        """Send a welcome message with an inline button."""
        keyboard = [
            [InlineKeyboardButton("Add me to your chat!", url="https://t.me/+YourBotUsername")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        welcome_message = (
            f"Hey {update.effective_user.first_name}! 👋\n"
            "I'm Olive, your personal movie management bot. "
            "I can help you store and search for movies in your group!"
        )
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text=welcome_message, 
            reply_markup=reply_markup
        )

    async def add_movie(self, update: Update, context: CallbackContext) -> None:
        """Add movie to the database with enhanced error handling."""
        if update.effective_chat.id != int(self.STORAGE_GROUP_ID):
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text="❌ Movie uploads are only allowed in the designated storage group."
            )
            return

        file_info = update.message.document
        if not file_info:
            return

        try:
            movie_name = file_info.file_name
            file_id = file_info.file_id
            
            # Upsert to avoid duplicates
            self.collection.update_one(
                {"file_id": file_id},
                {"$set": {"name": movie_name, "file_id": file_id}},
                upsert=True
            )
            
            await context.bot.send_message(
                chat_id=self.STORAGE_GROUP_ID, 
                text=f"✅ Added movie: {movie_name}"
            )
        except PyMongoError as e:
            logger.error(f"Database insertion error: {e}")
            await context.bot.send_message(
                chat_id=self.STORAGE_GROUP_ID, 
                text="❌ Failed to add movie due to a database error."
            )

    async def search_movie(self, update: Update, context: CallbackContext) -> None:
        """Enhanced movie search with more robust error handling."""
        if update.effective_chat.id != int(self.SEARCH_GROUP_ID):
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text="❌ Movie searches are only allowed in the designated search group."
            )
            return

        movie_name = update.message.text.strip()
        if not movie_name:
            return

        try:
            regex_pattern = re.compile(re.escape(movie_name), re.IGNORECASE)
            results = list(self.collection.find({"name": {"$regex": regex_pattern}}).limit(5))

            if results:
                for result in results:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id, 
                        document=result['file_id'],
                        caption=f"📽️ Movie: {result['name']}"
                    )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id, 
                    text=f"🔍 No movies found matching '{movie_name}'."
                )
        except PyMongoError as e:
            logger.error(f"Movie search error: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text="❌ An error occurred during the movie search."
            )

    
    async def welcome_new_member(update: Update, context: CallbackContext):
    """Send a welcome message when a new user joins the search group."""
    for member in update.message.new_chat_members:
        await context.bot.send_message(chat_id=SEARCH_GROUP_ID, text=f"Welcome {member.full_name}! Ask for a movie name.")

    

    async def handle_text_message(self, update: Update, context: CallbackContext) -> None:
        """Centralized message handling with funny responses and movie search."""
        user_message = update.message.text.strip().lower()
        
        # Check funny responses first
        for question, response in self.FUNNY_RESPONSES.items():
            if question in user_message:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id, 
                    text=response
                )
                return
        
        # If no funny response, attempt movie search
        await self.search_movie(update, context)

    async def run(self) -> None:
        """Initialize and run the bot."""
        application = ApplicationBuilder().token(self.TOKEN).build()

        # Register handlers
        handlers = [
            CommandHandler("start", self.start),
            MessageHandler(filters.Document.ALL, self.add_movie),
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message),
        ]
        
        for handler in handlers:
            application.add_handler(handler)

        try:
            logger.info("🤖 Bot starting...")
            await application.run_polling(drop_pending_updates=True)
        except Exception as e:
            logger.critical(f"Bot startup failed: {e}")

def main():
    """Entry point for the bot application."""
    nest_asyncio.apply()
    bot = MovieBot()
    
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.critical(f"Unhandled exception: {e}")

if __name__ == '__main__':
    main()
