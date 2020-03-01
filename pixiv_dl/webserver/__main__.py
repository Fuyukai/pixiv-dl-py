"""
Main entry for the library downloader.
"""
import argparse
import os
from pathlib import Path

from pixiv_dl.config import get_config_in

loaded = False


def main():
    global loaded
    parser = argparse.ArgumentParser(prog="pixiv-web")

    parser.add_argument(
        "-d", "--db", help="The local db directory for the command to run", default="./output"
    )
    parser.add_argument("-p", "--port", help="The port to bind on", default=4280, type=int)

    args = parser.parse_args()
    path = Path(args.db).resolve()
    print(f"Running from path {path}, changing working directory")
    os.chdir(path)

    config = get_config_in(Path("."))["config"]

    from pixiv_dl.webserver import app

    app.config["db_url"] = config["database_url"]
    app.config.update(config.get("webserver", {}))
    app.run(port=args.port, debug=True, use_reloader=False)


if __name__ == "__main__":
    if not loaded:
        main()
