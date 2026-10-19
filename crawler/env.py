"""Read the crawl target from the command line, the environment, or a `.env` file."""
import os
from pathlib import Path

from .errors import CrawlerError

ENV_FILE = ".env"
TARGET_PAGE_KEY = "TARGET_PAGE"


class TargetPageMissingError(CrawlerError):
    pass


def load_env_file(path):
    """Parse KEY=VALUE lines; blank lines and `#` comments are ignored. Keys are upper-cased."""
    env_path = Path(path)
    if not env_path.is_file():
        return {}
    values = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip().upper()] = value.strip().strip("\"'")
    return values


def target_page(cli_value, env_file=ENV_FILE):
    """Priority: --page, then the TARGET_PAGE environment variable, then the .env file."""
    page = cli_value or os.environ.get(TARGET_PAGE_KEY) or load_env_file(env_file).get(TARGET_PAGE_KEY)
    if not page:
        raise TargetPageMissingError(
            f"no target page: pass --page or set {TARGET_PAGE_KEY} in the environment or {env_file}")
    return page
