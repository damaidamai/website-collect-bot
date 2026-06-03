import logging

from dotenv import load_dotenv

from website_collect_bot.bot import WebsiteCollectBot
from website_collect_bot.config import get_settings


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    settings = get_settings()
    bot = WebsiteCollectBot(settings)
    app = bot.build_application()
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
