"""
Pixiv mass downloading tool.
"""
import argparse
import json
import logging
import os
import textwrap
import time
from concurrent.futures.thread import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from pprint import pprint
from typing import List, Set, Any, Tuple, Iterable, Optional
from urllib.parse import urlsplit, parse_qs

import pendulum
import pixivpy3
import requests
from pixivpy3 import PixivError
from sqlalchemy.orm import Session
from termcolor import cprint

from pixiv_dl.config import get_config_in
from pixiv_dl.db import DB, Author, Artwork, Bookmark, ExtendedAuthorInfo, ArtworkTag, Blacklist

RAW_DIR = Path("./raw")
BOOKMARKS_DIR = Path("./bookmarks")
TAGS_DIR = Path("./tags")
USERS_DIR = Path("./users")
FOLLOWING_DIR = Path("./following")
RANKINGS_DIR = Path("./rankings")
RECOMMENDS_DIR = Path("./recommends")

logging.basicConfig()


@dataclass
class DownloadableImage:
    # illust id
    id: int
    # if this is multiple page
    multi_page: bool
    # the page number
    page_num: int
    # the image url
    url: str


# https://stackoverflow.com/a/312464
def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i: i + n]


class Downloader(object):
    VALID_RANKINGS = {
        "day",
        "day_male",
        "day_female",
        "day_male_r18",
        "day_female_r18",
        "week",
        "week_original",
        "week_rookie",
        "week_r18",
        "week_r18g",
        "month",
    }

    def __init__(
        self,
        aapi: pixivpy3.AppPixivAPI,
        db: DB,
        config,
        *,
        allow_r18: bool = False,
        lewd_limits=(0, 6),
        filter_tags: Set[str] = None,
        required_tags: Set[str] = None,
        bookmark_limits: Tuple[int, int] = None,
        max_pages: int = None,
    ):
        """
        :param aapi: The Pixiv app API interface.
        :param papi: The Pixiv Public API interface.
        :param db: The DB object.
        :param config: The downloader-specific config.

        Behaviour params:
        :param allow_r18: If R-18 content should be downloaded, too.
        :param lewd_limits: The 'lewd level' limits. 2 = sfw, 4 = moderately nsfw, 6 = super nsfw.
        :param filter_tags: If an illustration has any of these tags, it will be ignored.
        :param required_tags: If an illustration doesn't have any of these tags, it will be ignored.
        :param bookmark_limits: The bookmark limits. Setting none for a field will ignore it.

        .. note::

            Even if lewd_limits[1] is set to 6, allow_r18 needs to be set to download x_restrict
            tagged items.
        """
        self.aapi = aapi
        self.config = config
        self.db = db

        self.allow_r18 = allow_r18
        self.allow_r18 = allow_r18
        self.lewd_limits = lewd_limits

        self.filtered_tags = filter_tags
        self.required_tags = required_tags

        self.bookmark_limits = bookmark_limits

        self.max_pages = max_pages

        self.should_filter = True

    def get_formatted_info(self) -> str:
        """
        Gets the formatted info for this downloader.
        """
        msgs = [
            f"  allow r-18: {self.allow_r18}",
            f"  lewd limits: max={self.lewd_limits[1]}, min={self.lewd_limits[0]}",
            f"  bookmark limits: max={self.bookmark_limits[1]}, min={self.bookmark_limits[0]}",
            f"  filtered tags: {self.filtered_tags}",
            f"  required tags: {self.required_tags}",
            f"  max pages: {self.max_pages}",
        ]
        return "\n".join(msgs)

    def retry_wrapper(self, cbl):
        """
        Retries a pixiv API request, re-authing if needed
        """
        # 3 retries
        for x in range(0, 3):
            try:
                res = cbl()
            except PixivError as e:
                if "connection aborted" in str(e).lower():
                    # ignore
                    continue

                raise

            # check for login:
            if res is False:
                raise Exception("Error downloading?")
            elif res is True:
                return res
            elif "error" in res:
                if "invalid_grant" not in res["error"]["message"]:
                    pprint(res)
                    raise Exception("Unknown error")
                # re-auths with the refresh token and retries
                self.aapi.auth()
                continue
            else:
                return res
        else:
            raise Exception(f"Failed to run {cbl} 3 times")

    def download_page(self, items: List[DownloadableImage]):
        """
        Downloads a page image.
        """
        for item in items:
            output_dir = RAW_DIR / str(item.id)
            output_dir.mkdir(parents=True, exist_ok=True)

            marker = output_dir / "marker.json"

            if marker.exists():
                cprint(f"Skipping download for {item.id} as marker already exists", "magenta")
                return

            cprint(f"Downloading {item.id} page {item.page_num}", "cyan")
            p = partial(self.aapi.download, url=item.url, path=output_dir, replace=True)
            self.retry_wrapper(p)

            cprint(f"Successfully downloaded image for {item.id}", "green")

        (RAW_DIR / str(items[0].id) / "marker.json").write_text(
            json.dumps({"downloaded": pendulum.now("UTC").isoformat()})
        )

        cprint(f"Successfully downloaded {item.id}", "green")

    def download_author_pic(self, user: dict):
        """
        Downloads and saves an author's profile picture from a user dict.
        """
        user_id = user["id"]
        pic_urls = list(user["profile_image_urls"].values())
        if not pic_urls:
            cprint(f"Skipping {user_id} profile image download", "magenta")
            return False

        pic_url = pic_urls[0]
        pic_raw_name = pic_url.split("/")[-1]
        pic_ext = pic_raw_name.split(".")[-1]

        output_dir = Path("profile_pictures")
        output_dir.mkdir(parents=True, exist_ok=True)
        symlink = output_dir / (str(user_id) + "." + pic_ext)

        if (output_dir / pic_raw_name).exists():
            cprint(f"Skipping {user_id} profile image download as it exists", "magenta")
            return False

        p = partial(self.aapi.download, url=pic_url, name=pic_raw_name, path=str(output_dir))
        self.retry_wrapper(p)
        # symlink to the raw file
        try:
            symlink.unlink()
        except FileNotFoundError:
            pass

        symlink.symlink_to(pic_raw_name)

        cprint(f"Downloaded {user_id}'s profile picture")
        return True

    @staticmethod
    def make_downloadable(illust: dict) -> List[DownloadableImage]:
        """
        Makes a list of downloadable images from an illustration object.
        """
        illust_id = illust["id"]

        # if it's a single image...
        single_image = illust["page_count"] == 1
        if single_image:
            obs = [
                DownloadableImage(
                    illust_id,
                    multi_page=False,
                    page_num=1,
                    url=illust["meta_single_page"]["original_image_url"],
                )
            ]
        else:
            obs = []
            for idx, subpage in enumerate(illust["meta_pages"]):
                obb = DownloadableImage(
                    illust_id,
                    multi_page=True,
                    page_num=idx + 1,
                    url=subpage["image_urls"]["original"],
                )
                obs.append(obb)

        return obs

    @staticmethod
    def store_illust_metadata(output_dir: Path, illust: dict, session: Session):
        """
        Stores the metadata for a specified illustration.
        """
        illust_id = illust["id"]
        illust["_meta"] = {
            "download-date": pendulum.now("UTC").isoformat(),
            "tool": "pixiv-dl",
            "weblink": f"https://pixiv.net/en/artworks/{illust_id}",
        }

        # the actual location
        subdir = output_dir / str(illust_id)
        subdir.mkdir(exist_ok=True)

        # write the raw metadata for later usage, if needed
        (subdir / "meta.json").write_text(json.dumps(illust, indent=4))

        # add objects to database
        # step 1: artwork
        artwork = session.query(Artwork).filter(Artwork.id == illust_id).first()
        if artwork is None:
            artwork = Artwork()
            # these never change, so we can trust they won't exist
            artwork.id = illust_id
            artwork.title = illust["title"]
            artwork.caption = illust.get("caption", None)
            artwork.uploaded_at = pendulum.parse(illust["create_date"])

            artwork.page_count = illust.get("page_count", 1)
            artwork.single_page = illust.get("page_count", 1) == 1

            artwork.r18 = illust.get("x_restrict") != 0
            artwork.r18g = illust.get("restrict") != 0
            artwork.lewd_level = illust.get("sanity_level", 2)

        # these *can* change, so we forcibly update them.
        artwork.bookmarks = illust.get("total_bookmarks", 0)
        artwork.views = illust.get("total_views", 0)

        artwork.is_bookmarked = illust.get("is_bookmarked", False)

        # step 2: author
        author = session.query(Author).filter(Author.id == illust["user"]["id"]).first()
        if author is None:
            author = Author()
            author.id = illust["user"]["id"]
            author.name = illust["user"]["name"]
            author.account_name = illust["user"]["account"]
        else:
            author.name = illust["user"]["name"]

        session.add(author)

        artwork.author = author

        # step 3: tags
        tags_to_add = []
        # Sometimes, artworks have the same tag multiple times!!!!!
        seen_keys = set()
        for tag in illust["tags"]:
            if tag["name"] in seen_keys:
                continue
            seen_keys.add(tag["name"])

            arttag = (
                session.query(ArtworkTag)
                    .filter((ArtworkTag.artwork_id == illust_id) & (ArtworkTag.name == tag["name"]))
                    .first()
            )
            if arttag is None:
                arttag = ArtworkTag()
                arttag.name = tag["name"]
                arttag.artwork_id = illust_id

            # update translations if needed
            if tag["translated_name"] is not None:
                arttag.translated_name = tag["translated_name"]

            tags_to_add.append(arttag)

        # step 3: add artwork
        session.add(artwork)

        # step 4: add tags we added
        with session.no_autoflush:
            for tag in tags_to_add:
                session.add(tag)

        # Flush now, to ensure that the tag state is consistent.
        session.flush()

    def depaginate_download(
        self,
        meth,
        param_names: Iterable[str] = ("max_bookmark_id",),
        key_name: str = "illusts",
        max_items: int = None,
        initial_params: Iterable[Any] = (),
    ):
        """
        Depaginates a method. Pass a partial of the method you want here to depaginate.

        :param param_names: The param names to use for paginating.
        :param key_name: The key name to use for unpacking the objects.
        :param max_items: The maximum items to depaginate.
        :param initial_params: The initial parameters to provide.
        """

        if isinstance(param_names, str):
            param_names = (param_names,)

        next_params = initial_params
        to_process = []

        for x in range(0, 9999):  # reasonable upper bound is 9999, 9999 * 30 is ~300k bookmarks...
            if not next_params:
                cprint("Downloading initial page...", "cyan")
                response = self.retry_wrapper(meth)
            else:
                params = dict(zip(param_names, next_params))
                fmt_params = " ".join(f"{name}={value}" for (name, value) in params.items())

                cprint(f"Downloading page with params {fmt_params}...", "cyan")
                p = partial(meth, **params)
                response = self.retry_wrapper(p)

            obbs = response[key_name]
            cprint(f"Downloaded {len(obbs)} objects (current tally: {len(to_process)})", "green")
            to_process += obbs

            if max_items is not None and len(to_process) >= max_items:
                break

            next_url = response["next_url"]
            if next_url is not None:
                query = parse_qs(urlsplit(next_url).query)
                next_params = [query[key][0] for key in param_names]
            else:
                # no more bookmarks!
                break

            # ratelimit...
            time.sleep(1.5)

        return to_process

    @staticmethod
    def do_symlinks(raw_dir: Path, dest_dir: Path, illust_id: int):
        """
        Performs symlinking for an illustration.
        """
        original_dir = raw_dir / str(illust_id)
        # invisible or otherwise excluded
        if not original_dir.exists():
            return

        final_dir = dest_dir / str(illust_id)

        # no easy way to check if a broken symlink exists other than just... doing this
        try:
            final_dir.unlink()
        except FileNotFoundError:
            pass

        final_dir.symlink_to(original_dir.resolve(), target_is_directory=True)
        cprint(f"Linked {final_dir} -> {original_dir}", "magenta")

    def do_download_with_symlinks(self, dest_dir: Path, items: List[DownloadableImage]):
        """
        Does a download with symlinking.
        """
        self.download_page(items)
        self.do_symlinks(RAW_DIR, dest_dir, items[0].id)

    def filter_illust(self, illust) -> Tuple[bool, str]:
        """
        Filters an illustration based on the criteria.
        """
        # clever! we only set msg if we can't filter.
        # so we can simply `return msg is not None, msg`
        msg = None

        # useful values so we dont type these over again
        lewd_level = illust["sanity_level"]
        tags = set()
        for td in illust["tags"]:
            tags.update(set(x.lower() for x in td.values() if x))

        filtered = tags.intersection(self.filtered_tags)
        required = tags.intersection(self.required_tags)
        bookmarks = illust["total_bookmarks"]
        pages = illust["meta_pages"]

        max_bm = self.bookmark_limits[1]
        min_bm = self.bookmark_limits[0]
        max_lewd = self.lewd_limits[1]
        min_lewd = self.lewd_limits[0]

        if not illust["visible"]:
            msg = "Illustration is not visible"
        elif self.should_filter:
            if illust["x_restrict"] and not self.allow_r18:
                msg = "Illustration is R-18"

            elif min_lewd is not None and lewd_level < min_lewd:
                msg = f"Illustration lewd level ({lewd_level}) is below minimum level ({min_lewd})"

            elif max_lewd is not None and lewd_level > max_lewd:
                msg = f"Illustration lewd level ({lewd_level}) is above maximum level ({max_lewd})"

            elif self.filtered_tags and filtered:
                msg = f"Illustration contains filtered tags {filtered}"

            elif self.required_tags and not required:
                msg = f"Illustration missing any of the required tags {self.required_tags}"

            elif max_bm is not None and bookmarks > max_bm:
                msg = f"Illustration has too many bookmarks ({bookmarks} > {max_bm})"

            elif min_bm is not None and bookmarks < min_bm:
                msg = f"Illustration doesn't have enough bookmarks ({bookmarks} < {min_bm})"

            elif self.max_pages is not None and len(pages) > self.max_pages:
                msg = f"Illustration has too many pages ({len(pages)} > {self.max_pages})"

            else:
                with self.db.session() as sess:
                    blacklist = (
                        sess.query(Blacklist)
                            .filter(
                            (Blacklist.author_id == illust["user"]["id"]) |
                            (Blacklist.artwork_id == illust["id"]) |
                            (Blacklist.tag.in_(tags))
                        ).first()
                    )
                    if blacklist is not None:
                        msg = f"Illustration is blacklisted ({blacklist})"

        return msg is not None, msg

    def process_and_save_illusts(self, illusts: List[dict]) -> List[List[DownloadableImage]]:
        """
        Processes and saves the list of illustrations.

        This takes the list of illustration responses, saves them, and returns a list of
        DownloadableImage to download.

        It also updates the database.
        """
        to_dl = []

        with self.db.session() as session:
            for illust in illusts:
                id = illust["id"]
                title = illust["title"]

                filtered, msg = self.filter_illust(illust)
                if filtered:
                    cprint(f"Filtered illustration {id} ({title}): {msg}", "red")
                    continue

                raw_dir = Path("raw")
                self.store_illust_metadata(raw_dir, illust, session)
                obs = self.make_downloadable(illust)
                to_dl.append(obs)

                cprint(
                    f"Processed metadata for {illust['id']} ({illust['title']}) "
                    f"with {len(obs)} pages",
                    "green",
                )

        return to_dl

    def save_profile_pics(self, to_process: List[dict]) -> int:
        """
        Saves profile pics from an illustration list.

        :return The number of pics saved.
        """
        seen = set()
        illusts_to_dl = []
        for illust in to_process:
            user_id = illust["user"]["id"]
            if user_id in seen:
                continue

            seen.add(user_id)
            illusts_to_dl.append(illust["user"])

        cprint(f"Got {len(illusts_to_dl)} unique authors, out of {len(to_process)}", "cyan")

        with ThreadPoolExecutor(8) as e:
            return sum(list(e.map(self.download_author_pic, illusts_to_dl)))

    def download_bookmarks(self):
        """
        Downloads the bookmarks for this user.
        """
        self.should_filter = self.config.get("filter_bookmarks", False)

        # set up the output dirs
        RAW_DIR.mkdir(exist_ok=True)

        bookmark_root_dir = BOOKMARKS_DIR
        bookmark_root_dir.mkdir(exist_ok=True)

        for restrict in "private", "public":
            bookmark_dir = bookmark_root_dir / restrict
            bookmark_dir.mkdir(exist_ok=True)

            cprint(f"Downloading bookmark metadata type {restrict}", "magenta")
            fn = partial(self.aapi.user_bookmarks_illust, self.aapi.user_id, restrict=restrict)
            to_process = self.depaginate_download(fn, param_names=("max_bookmark_id",))

            cprint("Saving author profile pictures...", "magenta")
            downloaded = self.save_profile_pics(to_process)
            cprint(f"Downloaded {downloaded} author avatars.", "cyan")

            # downloadable objects, list of lists
            to_dl = self.process_and_save_illusts(to_process)
            cprint(f"Got {len(to_dl)} bookmarks.", "cyan")

            # update bookmarks table
            with self.db.session() as session:
                for illust in to_process:
                    bookmark = (
                        session.query(Bookmark).filter(Bookmark.artwork_id == illust["id"]).first()
                    )

                    if bookmark is None:
                        bookmark = Bookmark()

                    bookmark.type = restrict
                    bookmark.artwork_id = illust["id"]
                    session.add(bookmark)

            # free memory during the download process, we don't need these anymore
            to_process.clear()

            cprint("Downloading images concurrently...", "magenta")
            with ThreadPoolExecutor(4) as e:
                list(e.map(self.download_page, to_dl))

    def _do_mirror_user_metadata(self, user_id: int, *, full: bool = False):
        """
        Does a user mirror with metadata.
        """
        raw = RAW_DIR
        raw.mkdir(exist_ok=True)

        cprint(f"Downloading info for user {user_id}...", "cyan")
        user_info = self.aapi.user_detail(user_id)

        cprint(f"Saving profile image...", "cyan")
        self.download_author_pic(user_info["user"])

        with self.db.session() as session:
            extended_info = (
                session.query(ExtendedAuthorInfo)
                    .filter(ExtendedAuthorInfo.author_id == user_info["user"]["id"])
                    .first()
            )

            if extended_info is None:
                extended_info = ExtendedAuthorInfo()
                extended_info.twitter_url = user_info["profile"]["twitter_url"]
                extended_info.comment = user_info["user"]["comment"]

            author_object = (
                session.query(Author).filter(Author.id == user_info["user"]["id"]).first()
            )

            if author_object is None:
                author_object = Author()
                author_object.id = user_info["user"]["id"]
                author_object.account_name = user_info["user"]["account"]

            author_object.name = user_info["user"]["name"]
            session.add(author_object)
            extended_info.author = author_object
            session.add(extended_info)

        cprint(
            f"Downloading all works info for user {user_id} || {user_info['user']['name']} "
            f"|| {user_info['user']['account']}",
            "cyan",
        )
        fn = partial(self.aapi.user_illusts, user_id=user_id)
        illusts = self.depaginate_download(fn, param_names=("offset",))
        to_process_works = self.process_and_save_illusts(illusts)

        if full:
            cprint(f"Downloading all bookmark info for user {user_id}", "cyan")
            fn2 = partial(self.aapi.user_bookmarks_illust, user_id=user_id)
            to_process_bookmarks = self.process_and_save_illusts(self.depaginate_download(fn2))
        else:
            to_process_bookmarks = []

        return to_process_works, to_process_bookmarks

    def mirror_user(self, user_id: int, *, full: bool = False):
        """
        Mirrors a user.
        """
        to_dl_works, to_dl_bookmarks = self._do_mirror_user_metadata(user_id, full=full)

        cprint("Downloading images concurrently...", "magenta")
        with ThreadPoolExecutor(4) as e:
            l1 = list(e.map(self.download_page, to_dl_works))
            if full:
                l2 = list(e.map(self.download_page, to_dl_bookmarks))
                return l1 + l2
            else:
                return l1

    def download_following(self, max_items: int = 100):
        """
        Downloads all your following.

        :param max_items: The maximum number of items to download.
        """
        raw = RAW_DIR
        raw.mkdir(exist_ok=True)

        follow_dir = FOLLOWING_DIR
        follow_dir.mkdir(exist_ok=True)

        for x in range(0, max_items, 30):
            cprint(f"Downloading items {x + 1} - {x + 31}", "cyan")

            fn = partial(self.aapi.illust_follow)
            to_process = self.depaginate_download(
                fn, max_items=30, param_names=("offset",), initial_params=(x,)
            )
            self.save_profile_pics(to_process)

            # no more to DL
            if len(to_process) == 0:
                return

            # no special db access; it's done here.
            to_dl = self.process_and_save_illusts(to_process)

            cprint("Downloading images concurrently...", "magenta")

            with ThreadPoolExecutor(4) as e:
                # list() call unwraps errors
                list(e.map(self.download_page, to_dl))

    def download_tag(self, main_tag: str, max_items: int = 500):
        """
        Downloads all items for a tag.
        """
        raw = RAW_DIR
        raw.mkdir(exist_ok=True)

        tags_dir = TAGS_DIR
        tags_dir.mkdir(exist_ok=True)

        # no plural, this is the singular tag
        tag_dir = tags_dir / main_tag
        tag_dir.mkdir(exist_ok=True)

        tag_info_got = False

        max_items = min(max_items, 5000)  # pixiv limit :(

        for x in range(0, max_items, 30):
            cprint(f"Downloading items {x + 1} - {x + 31}", "cyan")

            fn = partial(self.aapi.search_illust, word=main_tag)
            to_process = self.depaginate_download(
                fn, max_items=30, param_names=("offset",), initial_params=(x,)
            )
            self.save_profile_pics(to_process)
            # no more to DL
            if len(to_process) == 0:
                return

            # save very very basic tag info...
            if not tag_info_got:
                cprint("Saving tag info...", "cyan")
                one = next(iter(to_process))
                translated_name = None

                for tag_info in one["tags"]:
                    if tag_info["name"] == main_tag:
                        translated_name = tag_info["translated_name"]
                        break

                if translated_name:
                    cprint(f"Translated name: {translated_name}", "magenta")
                    tag_meta = tag_dir / "translation.json"
                    with tag_meta.open(mode="w") as f:
                        json.dump({"translated_name": translated_name}, f)

            to_dl = self.process_and_save_illusts(to_process)

            cprint("Downloading images concurrently...", "magenta")
            with ThreadPoolExecutor(4) as e:
                list(e.map(self.download_page, to_dl))

    def download_ranking(self, mode: str, date: str = None):
        """
        Downloads the current rankings.
        """
        cprint(f"Downloading the rankings for mode {mode}", "cyan")

        raw = RAW_DIR
        raw.mkdir(exist_ok=True)

        method = partial(self.aapi.illust_ranking, mode=mode, date=date)
        to_process = self.depaginate_download(method, param_names=("offset",))
        self.save_profile_pics(to_process)
        to_dl = self.process_and_save_illusts(to_process)

        with ThreadPoolExecutor(4) as e:
            # list() call unwraps errors
            list(e.map(self.download_page, to_dl))

    def download_recommended(self, max_items: int = 500):
        """
        Downloads recommended items.
        """
        cprint("Downloading recommended rankings...", "cyan")
        raw = RAW_DIR
        raw.mkdir(exist_ok=True)

        method = partial(self.aapi.illust_recommended)
        to_process = self.depaginate_download(
            method,
            param_names=(
                "min_bookmark_id_for_recent_illust",
                "max_bookmark_id_for_recommend",
                "offset",
            ),
            max_items=max_items,
        )
        self.save_profile_pics(to_process)
        to_dl = self.process_and_save_illusts(to_process)

        with ThreadPoolExecutor(4) as e:
            # list() call unwraps errors
            return list(e.map(self.download_page, to_dl))

    def blacklist(self, user_id: Optional[int], artwork_id: Optional[int], tag: Optional[str]):
        """
        Adds a blacklist entry.
        """

        if not any((user_id, artwork_id, tag)):
            cprint("Blacklist requires at least one of (user_id, artwork_id, tag)", "red")

        # extremely terrible gimped usage of sqlite
        with self.db.session() as sess:
            row = Blacklist()

            row.artwork_id = artwork_id
            row.author_id = user_id
            row.tag = tag
            sess.add(row)

    def supercrawl(self):
        """
        Performs a Super Crawl - downloading ALL images from ALL following.
        """
        cprint("PERFORMING A SUPER CRAWL!", "magenta")
        cprint("This is going to take a very long time.", "magenta")
        if input("Are you sure? [y/N] ") != "y":
            return

        meth = partial(self.aapi.user_following, user_id=self.aapi.user_id)
        following = self.depaginate_download(
            meth, param_names=("offset",), key_name="user_previews",
        )

        def _flatmap_fn(obb):
            author_id = obb["user"]["id"]
            author_data, _ = self._do_mirror_user_metadata(author_id)
            return author_data

        for chunked in chunks(following, 15):
            to_dl = []
            for chunk in chunked:
                to_dl.extend(_flatmap_fn(chunk))

            with ThreadPoolExecutor(4) as e:
                # list() call unwraps errors
                list(e.map(self.download_page, to_dl))

            print("GOing back around!")

    @staticmethod
    def print_stats():
        """
        Prints the statistics for the local download database.
        """
        raw_dir = RAW_DIR
        if not raw_dir.exists():
            cprint(f"No database found in {Path('.').resolve()}", "red")
            return

        total_objects = 0
        total_downloaded = 0
        total_files = 0
        page_count = 0

        for subdir in raw_dir.iterdir():
            # if the user decides to put random files in the raw/ directory...
            if not subdir.is_dir():
                continue

            # meta signifies existence of the actual object
            meta = subdir / "meta.json"
            if not meta.exists():
                continue

            with meta.open(mode="r") as f:
                data = json.load(f)

            total_objects += 1
            # marker is the sign that all files were downloaded
            marker = subdir / "marker.json"
            if marker.exists():
                total_downloaded += 1

            pages = data.get("meta_pages")
            if not pages:
                pages = [data["meta_single_page"]["original_image_url"]]
            else:
                pages = list(map(lambda x: x["image_urls"]["original"], pages))

            page_count += len(pages)

            for page in pages:
                fname = page.split("/")[-1]
                file = subdir / fname
                if file.exists():
                    total_files += 1

        cprint(f"Total illustration objects downloaded: {total_objects}", "magenta")
        cprint(f"Total pages: {page_count}", "magenta")
        cprint(f"Total files: {total_files}", "magenta")
        cprint(f"Total complete downloads: {total_downloaded}", "magenta")


def main():
    parser = argparse.ArgumentParser(
        description=textwrap.dedent(
            """A pixiv downloader tool.

        This can download your bookmarks, your following feed, whole user accounts, etc.
        """
        )
    )
    parser.add_argument(
        "-d", "--db", help="The local db directory for the command to run", default="./output"
    )

    parser.add_argument(
        "--allow-r18", action="store_true", help="If R-18 works should also be downloaded"
    )

    # defaults: 0, 6
    parser.add_argument(
        "--min-lewd-level", type=int, help="The minimum 'lewd level'", required=False
    )
    parser.add_argument(
        "--max-lewd-level", type=int, help="The maximum 'lewd level'", required=False
    )

    parser.add_argument(
        "--filter-tag",
        action="append",
        help="Ignore any illustrations with this tag",
        required=False,
    )
    parser.add_argument(
        "--require-tag",
        action="append",
        help="Require illustrations to have this tag",
        required=False,
    )

    parser.add_argument(
        "--min-bookmarks", type=int, help="Minimum number of bookmarks", required=False
    )
    parser.add_argument(  # i have no idea when this will ever be useful, but symmetry
        "--max-bookmarks", type=int, help="Maximum number of bookmarks", required=False
    )

    parser.add_argument("--max-pages", type=int, help="Maximum number of pages", required=False)

    parsers = parser.add_subparsers(dest="subcommand")

    # download all bookmarks mode, no arguments
    bookmark_mode = parsers.add_parser("bookmarks", help="Download bookmarks")

    # super crawl mode, no arguments
    supercrawl = parsers.add_parser("supercrawl", help="Super-crawl mode")

    # download all following
    following_mode = parsers.add_parser("following", help="Download all following")
    following_mode.add_argument(
        "-l", "--limit", default=500, help="The maximum number of items to download", type=int
    )

    # mirror mode
    mirror_mode = parsers.add_parser("mirror", help="Mirror a user")
    mirror_mode.add_argument("userid", help="The user ID to mirror", type=int)
    mirror_mode.add_argument(
        "-f", "--full", action="store_true", help="If this should also mirror all their bookmarks"
    )

    # tag mode
    tag_mode = parsers.add_parser("tag", help="Download works with a tag")
    tag_mode.add_argument("tag", help="The main tag to filter by")
    tag_mode.add_argument(
        "-l", "--limit", default=500, help="The maximum number of items to download", type=int
    )

    ranking_mode = parsers.add_parser("rankings", help="Download works from the rankings")
    ranking_mode.add_argument(
        "-m",
        "--mode",
        help="The ranking mode to download",
        default="day",
        choices=Downloader.VALID_RANKINGS,
    )
    ranking_mode.add_argument(
        "--date", help="The date to download rankings on. Defaults to today.", default=None
    )

    recommended_mode = parsers.add_parser("recommended", help="Downloads recommended works")
    recommended_mode.add_argument(
        "-l", "--limit", default=500, help="The maximum number of items to download", type=int
    )

    blacklist_mode = parsers.add_parser("blacklist")
    blacklist_mode.add_argument(
        "-u", "--user-id", default=None, required=False, help="The user ID to blacklist",
        type=int
    )
    blacklist_mode.add_argument(
        "-a", "--artwork-id", default=None, required=False, help="The author ID to blacklist",
        type=int
    )
    blacklist_mode.add_argument(
        "-t", "--tag", default=None, required=False, help="The tag to blacklist"
    )

    auth = parsers.add_parser("auth", help="Generates the refresh token from credentials.")
    auth.add_argument("username", help="Username to log in with")
    auth.add_argument("password", help="Password associated with username")

    parsers.add_parser("stats", help="Shows statistics for the current download database.")

    args = parser.parse_args()

    output = Path(args.db)
    output.mkdir(exist_ok=True)
    cprint(f"Changing working directory to {output.resolve()}", "magenta")
    os.chdir(output)

    config = get_config_in(Path("."))

    # set up database
    db_url = config["config"]["database_url"]
    db = DB(db_url)
    db.migrate_database()

    defaults = config["defaults"]["downloader"]

    # set up pixiv downloader
    aapi = pixivpy3.AppPixivAPI()
    aapi.set_accept_language("en-us")

    class CustomAdapter(requests.adapters.HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            # When urllib3 hand-rolls a SSLContext, it sets 'options |= OP_NO_TICKET'
            # and CloudFlare really does not like this. We cannot control this behavior
            # in urllib3, but we can just pass our own standard context instead.
            import ssl
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ctx.load_default_certs()
            ctx.set_alpn_protocols(["http/1.1"])
            return super().init_poolmanager(*args, **kwargs, ssl_context=ctx)

    aapi.requests = requests.Session()
    aapi.requests.mount("https://", CustomAdapter())

    cprint("Authenticating with Pixiv...", "cyan")

    token_file = Path("refresh_token")
    if args.subcommand == "auth":
        aapi.auth(username=args.username, password=args.password)
        token_file.write_text(aapi.refresh_token)
        cprint(f"Successfully logged in with username/password as {aapi.user_id}", "magenta")
        cprint(
            f"Refresh token successfully written to {token_file.absolute()}. Exiting...", "green"
        )
        return

    if not token_file.exists():
        cprint("No refresh token found. Please use auth subcommand to authenticate.", "red")
        return

    aapi.auth(refresh_token=token_file.read_text())
    cprint(f"Successfully logged in with token as {aapi.user_id}", "magenta")

    user_info_path = Path("user.json")
    if not user_info_path.exists():
        detail = aapi.user_detail(aapi.user_id)
        user_info_path.write_text(json.dumps(detail, indent=4))

    # load defaults from the config
    load_default_fields = [
        "max_bookmarks",
        "min_bookmarks",
        "max_lewd_level",
        "min_lewd_level",
        "max_pages",
    ]

    if args.allow_r18 is False:
        args.allow_r18 = defaults.get("allow_r18", False)

    for field in load_default_fields:
        arg = getattr(args, field, None)
        if arg is None:
            setattr(args, field, defaults.get(field))

    # make sure to make these emtpy lists
    default_filters = defaults.get("filtered_tags", [])
    if args.filter_tag is None:
        args.filter_tag = default_filters
    else:
        args.filter_tag += default_filters

    default_requires = defaults.get("required_tags", [])
    if args.require_tag is None:
        args.require_tag = default_requires
    else:
        args.require_tag += default_requires

    dl = Downloader(
        aapi,
        db,
        config=config["config"]["downloader"],
        allow_r18=args.allow_r18,
        lewd_limits=(args.min_lewd_level, args.max_lewd_level),
        filter_tags=set(x.lower() for x in args.filter_tag),
        required_tags=set(x.lower() for x in args.require_tag),
        bookmark_limits=(args.min_bookmarks, args.max_bookmarks),
        max_pages=args.max_pages,
    )
    print("Running downloader with:")
    print(dl.get_formatted_info())

    subcommand = args.subcommand
    if subcommand == "bookmarks":
        cprint("Downloading all bookmarks...", "cyan")
        return dl.download_bookmarks()
    elif subcommand == "supercrawl":
        return dl.supercrawl()
    elif subcommand == "following":
        cprint("Downloading your following...", "cyan")
        return dl.download_following(max_items=args.limit)
    elif subcommand == "mirror":
        if args.full:
            cprint("Fully mirroring a user...", "cyan")
        else:
            cprint("Mirroring a user...", "cyan")
        return dl.mirror_user(args.userid, full=args.full)
    elif subcommand == "tag":
        cprint("Downloading a tag...", "cyan")
        return dl.download_tag(args.tag, max_items=args.limit)
    elif subcommand == "rankings":
        cprint("Downloading rankings...", "cyan")
        return dl.download_ranking(mode=args.mode, date=args.date)
    elif subcommand == "recommended":
        cprint("Downloading recommended works...", "cyan")
        return dl.download_recommended(max_items=args.limit)
    elif subcommand == "blacklist":
        return dl.blacklist(user_id=args.user_id, artwork_id=args.artwork_id, tag=args.tag)
    elif subcommand == "stats":
        cprint("Providing statistics...", "cyan")
        return dl.print_stats()
    elif subcommand == "auth":
        pass
    else:
        cprint(f"Unknown command {subcommand}", "red")


if __name__ == "__main__":
    main()
