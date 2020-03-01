"""
Webserver definition.
"""
import json
from functools import partial
from os import fspath
from pathlib import Path
from typing import NoReturn, Callable, List, Any

from flask import Flask, render_template, request, send_from_directory, safe_join
from jinja2 import StrictUndefined
from sqlalchemy.orm import Session
from werkzeug.exceptions import abort

from pixiv_dl.db import DB, Artwork, Author, ExtendedAuthorInfo
from pixiv_dl.webserver.queriers import (
    query_bookmark_grid,
    query_bookmark_total,
    query_tags_all,
    query_tags_named,
    query_tags_named_total,
    query_raw_grid,
    query_raw_total,
    query_users_all,
    query_users_id, query_users_id_total)
from pixiv_dl.webserver.structs import SortMode, ArtworkCard

#: Flask app.
app = Flask(__name__)
app.jinja_env.undefined = StrictUndefined

#: Global database connector.
db: DB

#: Path to raw files.
RAW = Path("raw")


# flask setup
@app.before_first_request
def load_user_info():
    user_json = Path("user.json")
    user_data = json.loads(user_json.read_text())
    app.config["user_data"] = user_data

    global db
    db = DB(app.config["db_url"])


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

    for extension in "jpg", "png":
        filename = f"{image_id}_p{page_id}.{extension}"
        if not (image_dir / filename).exists():
            continue

        return send_from_directory(str(image_dir.absolute()), filename)

    abort(404)


# static image for profile pics
@app.route("/db/avatars/<int:user_id>")
def static_image_avatar(user_id: int):
    image_dir = Path("profile_pictures")

    for extension in "jpg", "png", "gif":
        filename = f"{user_id}.{extension}"
        if not (image_dir / filename).exists():
            continue

        return send_from_directory(str(image_dir.absolute()), filename)

    abort(404)


# main frontend page
@app.route("/")
def main():
    return render_template("frontpage.html")


# Image renderer page
@app.route("/pages/artwork/<int:artwork_id>")
def artwork_page(artwork_id: int):
    with db.session() as sess:
        artwork: Artwork = sess.query(Artwork).get(artwork_id)
        if artwork is None:
            abort(404)

        if artwork.single_page:
            return render_template("artwork_view/single.html", data=artwork)
        else:
            return render_template("artwork_view/multiple.html", data=artwork)


def _artwork_grid(
    name: str,
    grid_querier: Callable[[Session, int, SortMode], List[Any]],
    total_querier: Callable[[Session], int] = None,
    **kwargs,
):
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

    with db.session() as sess:
        tiles = grid_querier(sess, after, sort_mode)
        if total_querier is None:
            total = len(tiles)
        else:
            total = total_querier(sess)

    return render_template(
        f"grids/{name}_grid.html",
        artworks=tiles,
        after=after,
        sortmode=sort_mode.value.lower(),
        total_count=total,
        **kwargs,
    )


# Bookmark routes
@app.route("/pages/bookmarks")
def bookmarks():
    with db.session() as session:
        public_count = query_bookmark_total("public", session)
        private_count = query_bookmark_total("private", session)

    return render_template(
        "bookmarks.html", bookmark_count_public=public_count, bookmark_count_private=private_count
    )


# bookmark views
@app.route("/pages/bookmarks/public")
def bookmarks_public():
    # noinspection PyTypeChecker
    return _artwork_grid(
        "bookmark",
        partial(query_bookmark_grid, "public"),
        partial(query_bookmark_total, "public"),
        bookmark_category="public",
    )


@app.route("/pages/bookmarks/private")
def bookmarks_private():
    # noinspection PyTypeChecker
    return _artwork_grid(
        "bookmark",
        partial(query_bookmark_grid, "private"),
        partial(query_bookmark_total, "private"),
        bookmark_category="private",
    )


# Raw file listing


@app.route("/pages/raw")
def raw():
    return _artwork_grid("raw", query_raw_grid, query_raw_total)


# Tags routes
@app.route("/pages/tags")
def tags():
    after = request.args.get("after", 0)
    try:
        after = int(after)
    except ValueError:
        after = 0

    try:
        sortmode = SortMode(request.args.get("sortmode", "DESCENDING").upper())
    except ValueError:
        sortmode = SortMode.DESCENDING

    with db.session() as sess:
        cards, total = query_tags_all(sess, after, sortmode)
        return render_template(
            "tags.html", tags=cards, after=after, sortmode=sortmode.value.lower(), total_count=total
        )


@app.route("/pages/tags/<tag>")
def tags_named(tag: str):
    # noinspection PyTypeChecker
    return _artwork_grid(
        "tags", partial(query_tags_named, tag), partial(query_tags_named_total, tag), tag=tag
    )


# Users routes
@app.route("/pagers/users/<int:author_id>")
def users_id(author_id: int):
    with db.session() as session:
        author = session.query(Author).get(author_id)
        extended_author = session.query(ExtendedAuthorInfo) \
            .filter(ExtendedAuthorInfo.author_id == author_id) \
            .first()

    # noinspection PyTypeChecker
    return _artwork_grid(
        "oneuser",
        partial(query_users_id, author_id),
        partial(query_users_id_total, author_id),
        author_id=author_id,
        author=author, extended_author=extended_author
    )


@app.route("/pages/users")
def users():
    after = request.args.get("after", 0)
    try:
        after = int(after)
    except ValueError:
        after = 0

    try:
        sortmode = SortMode(request.args.get("sortmode", "DESCENDING").upper())
    except ValueError:
        sortmode = SortMode.DESCENDING

    with db.session() as sess:
        cards, total = query_users_all(sess, after, sortmode)
        return render_template(
            "users.html",
            authors=cards,
            after=after,
            sortmode=sortmode.value.lower(),
            total_count=total,
        )
