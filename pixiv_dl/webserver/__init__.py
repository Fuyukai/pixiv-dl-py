"""
Webserver definition.
"""
import enum
import json
import random
from dataclasses import dataclass
from os import fspath
from pathlib import Path
from typing import NoReturn

import pendulum
from flask import Flask, render_template, request, send_from_directory, safe_join
from pendulum import DateTime
from werkzeug.exceptions import abort

app = Flask(__name__)

RAW = Path("raw")
BK_PRIVATE = Path("bookmarks/private")
BK_PUBLIC = Path("bookmarks/public")
TAGS = Path("tags")
USERS = Path("users")


@dataclass
class ArtworkCard:
    """
    Container class for an artwork card.
    """

    #: ID of the artwork
    id: int
    #: Title of the artwork
    title: str
    #: Description of the artwork
    description: str
    #: Creation time of the artwork
    create_date: DateTime
    #: Author ID
    author_id: int
    #: Author name
    author_name: str
    #: Is work R-18?
    r18: bool


@dataclass
class TagCard:
    """
    Container class for a tag card.
    """

    #: The name of the tag.
    name: str
    #: The artwork card associated with this tag.
    artwork: ArtworkCard
    #: The number of artworks saved under this tag.
    count: int
    #: The translated name of this tag, if any.
    translated_name: str = None


class SortMode(enum.Enum):
    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"
    RANDOM = "RANDOM"


# Common functions
def count_artworks(path: Path) -> int:
    """
    Counts the number of artworks in a specified path.

    :param path: The path to count in.
    :return: The number of artwork folders.
    """
    folders = sum(1 for subdir in path.iterdir() if subdir.name.isdigit())
    return folders


def newest_artwork(path: Path) -> Path:
    """
    Gets the newest artwork from a path.

    :param path: The path of the artworks.
    :return: The Path corresponding to the newest artwork.
    """
    folders = sorted([subdir.name for subdir in path.iterdir() if subdir.name.isdigit()])
    newest = folders[-1]
    finalpath = path / newest
    return finalpath


def info_from_path(path: Path) -> ArtworkCard:
    """
    Gets an ArtworkCard from a resolved path.
    """
    meta = json.loads((path / "meta.json").read_text())
    author = meta["user"]

    tile = ArtworkCard(
        id=meta["id"],
        title=meta["title"],
        author_id=author["id"],
        author_name=author["name"],
        description="...",
        create_date=pendulum.parse(meta["create_date"]),
        r18=meta["x_restrict"] != 0,
    )

    return tile


# flask setup
@app.before_first_request
def load_user_info():
    user_json = Path("user.json")
    user_data = json.loads(user_json.read_text())
    app.config["user_data"] = user_data


@app.context_processor
def inject_stage_and_region():
    return {
        "userid": app.config["user_data"]["user"]["id"],
        "username": app.config["user_data"]["user"]["account"],
    }


# db image server
def _get_images_path(image_id: str) -> Path:
    image_dir = Path(safe_join(fspath(RAW), image_id))

    if not image_dir.exists():
        abort(404)

    return image_dir


# static image grid for the artwork grid page
@app.route("/db/images/<image_id>/grid")
def static_image_grid(image_id: str):
    # TODO: Smaller images
    image_dir = _get_images_path(image_id)
    meta = image_dir / "meta.json"
    data = json.loads(meta.read_text())

    if data["page_count"] > 1:
        page = data["meta_pages"][0]["image_urls"]["original"]
    else:
        page = data["meta_single_page"]["original_image_url"]

    filename = page.split("/")[-1]
    return send_from_directory(str(image_dir.absolute()), filename)


# static image for the artwork page
@app.route("/db/images/<image_id>/page/<int:page_id>")
def static_image_full(image_id: str, page_id: int):
    image_dir = _get_images_path(image_id)

    meta = image_dir / "meta.json"
    data = json.loads(meta.read_text())

    if data["page_count"] > 1:
        page = data["meta_pages"][0]["image_urls"]["original"]
    else:
        page = data["meta_single_page"]["original_image_url"]

    filename = page.split("/")[-1]
    return send_from_directory(str(image_dir.absolute()), filename)


# main frontend page
@app.route("/")
def main():
    return render_template("frontpage.html")


# Image renderer page
@app.route("/pages/artwork/<int:artwork_id>")
def artwork_page(artwork_id: int):
    image_dir = _get_images_path(str(artwork_id))

    meta = image_dir / "meta.json"
    data = json.loads(meta.read_text())

    return render_template("artwork_info.html", data=data)


# Bookmark routes
@app.route("/pages/bookmarks")
def bookmarks():
    public_count = count_artworks(BK_PUBLIC)
    private_count = count_artworks(BK_PRIVATE)

    return render_template(
        "bookmarks.html", bookmark_count_public=public_count, bookmark_count_private=private_count
    )


def _artwork_grid(name: str, path: Path, **kwargs):
    """
    Implements the loading of an artwork grid.
    """
    try:
        after = int(request.args.get("after", 0))
    except ValueError:
        abort(400)  # type: NoReturn
        raise Exception

    try:
        sort_mode = SortMode(request.args.get("sortmode", "DESCENDING").upper())
    except ValueError:
        abort(400)  # type: NoReturn
        raise Exception

    after = max(after, 0)
    files = [subdir for subdir in path.iterdir() if subdir.name.isdigit()]
    count = len(files)

    sorted_files = sorted(files, reverse=sort_mode == SortMode.DESCENDING)
    filelist = sorted_files[after : after + 25]
    tiles = []

    for subdir in filelist:
        tiles.append(info_from_path(subdir))

    return render_template(
        f"grids/{name}_grid.html",
        artworks=tiles,
        after=after,
        sortmode=sort_mode.value.lower(),
        total_count=count,
        **kwargs,
    )


@app.route("/pages/bookmarks/public")
def bookmarks_public():
    return _artwork_grid("bookmark", BK_PUBLIC, bookmark_category="public")


@app.route("/pages/bookmarks/private")
def bookmarks_private():
    return _artwork_grid("bookmark", BK_PRIVATE, bookmark_category="public")


# Raw routes
@app.route("/pages/raw")
def raw():
    try:
        after = int(request.args.get("after", 0))
    except ValueError:
        abort(400)  # type: NoReturn

    try:
        sortmode = SortMode(request.args.get("sortmode", "DESCENDING").upper())
    except ValueError:
        abort(400)  # type: NoReturn

    return _artwork_grid("raw", RAW, after, sortmode)


# Tags routes
@app.route("/pages/tags")
def tags():
    # list all tags in the tags dir
    tags = []
    for subdir in TAGS.iterdir():
        if not subdir.is_dir():
            continue

        subsubdirs = [subsubdir for subsubdir in subdir.iterdir() if subsubdir.name.isdigit()]

        # blegh
        count = len(subsubdirs)
        if count <= 0:
            continue

        chosen = random.choice(subsubdirs)
        # make the artwork card
        art_card = info_from_path(chosen)

        # find a translated name
        translated_name = None
        translation_path = subdir / "translation.json"
        if translation_path.exists():
            data = json.loads(translation_path.read_text())
            translated_name = data["translated_name"]

        tag_card = TagCard(
            name=subdir.name, artwork=art_card, translated_name=translated_name, count=count
        )
        tags.append(tag_card)

    tags = sorted(tags, key=lambda tagcard: tagcard.count, reverse=True)

    return render_template("tags.html", tags=tags)


@app.route("/pages/tags/<tag>")
def tags_named(tag: str):
    try:
        after = int(request.args.get("after", 0))
    except ValueError:
        abort(400)  # type: NoReturn

    try:
        sortmode = SortMode(request.args.get("sortmode", "DESCENDING").upper())
    except ValueError:
        abort(400)  # type: NoReturn

    return _artwork_grid("tags", (TAGS / tag), tag=tag)


# Users routes
@app.route("/pages/users")
def users():
    abort(404)
