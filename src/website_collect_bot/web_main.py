import logging

import uvicorn
from dotenv import load_dotenv

from website_collect_bot.config import get_settings


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    settings = get_settings()
    uvicorn.run(
        "website_collect_bot.web:create_app",
        factory=True,
        host=settings.web_host,
        port=settings.web_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
