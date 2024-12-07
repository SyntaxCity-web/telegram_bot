from telegram.ext import Application
import logging

# Enable logging to see logs in the console
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    # Your bot's token
    application = Application.builder().token("YOUR_BOT_TOKEN").build()

    # Add handlers, etc. to your application here

    # Run the bot using polling
    await application.run_polling()

# Directly await the main function instead of using asyncio.run()
if __name__ == "__main__":
    import asyncio
    asyncio.ensure_future(main())
    # No need for asyncio.run(main())
