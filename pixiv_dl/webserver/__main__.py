"""
Main entry for the library downloader.
"""
import argparse

from pixiv_dl.webserver import app

parser = argparse.ArgumentParser(prog="pixiv-web")

parser.add_argument(
    "-d", "--db", help="The local db directory for the command to run", default="./output"
)
parser.add_argument("-p", "--port", help="The port to bind on", default=4280, type=int)

args = parser.parse_args()
app.config["db_dir"] = args.db

app.run(port=args.port, debug=True)
