import logging
import re
import asyncio
import nest_asyncio
from typing import List, Dict, Any
from flask import Flask, request
import sys

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
    handlers=[
        logging.FileHandler('bot_logs.log'),
        logging.StreamHandler(sys.stdout)  # Add console output
    ]
)
logger = logging.getLogger(__name__)

# Flask app for port binding
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

class MovieBot:
    def __init__(self):
        """Initialize bot configuration and database connection."""
        # Log all environment variables for debugging
        logger.info("Environment Variables:")
        for key, value in os.environ.items():
            logger.info(f"{key}: {value}")

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

    async def run(self) -> None:
        """Initialize and run the bot."""
        # Determine port explicitly
        port = int(os.getenv('PORT', 10000))
        logger.info(f"Using port: {port}")
        print(f"Using port: {port}")

        # Attempt to create Flask server in main thread
        try:
            import socket
            def find_free_port():
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('', 0))
                    s.listen(1)
                    port = s.getsockname()[1]
                return port

            free_port = find_free_port()
            logger.info(f"Found free port: {free_port}")
            print(f"Found free port: {free_port}")
        except Exception as port_error:
            logger.error(f"Port finding error: {port_error}")
            free_port = port

        try:
            # Initialize Telegram bot application
            application = ApplicationBuilder().token(self.TOKEN).build()

            # Register handlers
            handlers = [
                CommandHandler("start", self.start),
                MessageHandler(filters.Document.ALL, self.add_movie),
                MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message),
                MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.welcome_new_member),
            ]
            
            for handler in handlers:
                application.add_handler(handler)

            logger.info("🤖 Bot starting...")
            
            # Run Flask in a separate thread
            import threading
            def run_flask():
                logger.info(f"Starting Flask on port {free_port}")
                print(f"Starting Flask on port {free_port}")
                app.run(host='0.0.0.0', port=free_port)

            flask_thread = threading.Thread(target=run_flask, daemon=True)
            flask_thread.start()

            # Start bot polling
            await application.initialize()
            await application.start()
            await application.updater.start_polling(drop_pending_updates=True)
            await application.updater.idle()

        except Exception as e:
            logger.critical(f"Bot startup failed: {e}")
            print(f"Bot startup failed: {e}")

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
