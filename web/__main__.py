"""Run the web app: python -m web [--port 8000] [--database data/crawler.db]."""
import argparse
import logging
from pathlib import Path

from crawler.settings import load_settings

from .server import make_server

DEFAULT_SETTINGS_PATH = "config/settings.yaml"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def main():
    parser = argparse.ArgumentParser(prog="web", description=__doc__)
    parser.add_argument("--config", default=DEFAULT_SETTINGS_PATH, help="settings YAML")
    parser.add_argument("--database", help="override database_path from settings")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    database = args.database or load_settings(args.config).database_path
    if args.host not in LOOPBACK_HOSTS:
        print("warning: the data contains raw user names and ids; anyone who can reach "
              f"{args.host}:{args.port} can read it")
    logging.basicConfig(level=logging.INFO)
    server = make_server(Path(database), args.host, args.port)
    print(f"Phây-búc crawler web app: http://{args.host}:{args.port}  (database: {database})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
