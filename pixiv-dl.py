"""
Pixiv mass downloading tool.
"""
import argparse
import json
import re
import textwrap
import traceback
from concurrent.futures.thread import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import List, Set, Any, Tuple

import arrow
import pixivpy3


# fucking pycharm
# https://github.com/yellowbluesky/PixivforMuzei3/blob/master/app/src/main/java/com/antony/muzei
# /pixiv/PixivArtWorker.java#L503


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


class Downloader(object):
    def __init__(
        self,
        aapi: pixivpy3.AppPixivAPI,
        papi: pixivpy3.PixivAPI,
        output_dir: Path,
        *,
        allow_r18: bool = False,
        lewd_limits=(0, 6),
        filter_tags: Set[str] = None,
        required_tags: Set[str] = None,
        bookmark_limits: Tuple[int, int] = None,
    ):
        """
        :param aapi: The Pixiv app API interface.
        :param papi: The Pixiv Public API interface.
        :param output_dir: The output directory.

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
        self.papi = papi
        self.output_dir = output_dir

        self.allow_r18 = allow_r18
        self.lewd_limits = lewd_limits

        self.filtered_tags = filter_tags
        self.required_tags = required_tags

        self.bookmark_limits = bookmark_limits

    def download_page(self, items: List[DownloadableImage]):
        """
        Downloads a page image.
        """
        for item in items:
            output_dir = self.output_dir / "raw" / str(item.id)
            output_dir.mkdir(parents=True, exist_ok=True)

            marker = output_dir / "marker.json"

            if marker.exists():
                print(f"Skipping download for {item.id} as marker already exists")
                return

            print(f"Downloading {item.id} page {item.page_num}")
            try:
                self.aapi.download(url=item.url, path=output_dir, replace=True)
            except Exception:
                print("Failed to download image...")
                traceback.print_exc()
                return

            print(f"Successfully downloaded image for {item.id}")

        (self.output_dir / "raw" / str(items[0].id) / "marker.json").write_text(
            json.dumps({"downloaded": arrow.utcnow().isoformat()})
        )

        print(f"Successfully downloaded {item.id}")

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
    def store_illust_metadata(output_dir: Path, illust: dict):
        """
        Stores the metadata for a specified illustration.
        """
        illust_id = illust["id"]
        illust["_meta"] = {
            "download-date": arrow.utcnow().isoformat(),
            "tool": "pixiv-dl",
            "weblink": f"https://pixiv.net/en/artworks/{illust_id}"
        }

        # the actual location
        subdir = output_dir / str(illust_id)
        subdir.mkdir(exist_ok=True)

        # write the raw metadata for later usage, if needed
        (subdir / "meta.json").write_text(json.dumps(illust, indent=4))

    @staticmethod
    def depaginate_download(
        meth,
        param_name: str = "max_bookmark_id",
        search_name: str = None,
        max_items: int = None,
        initial_param: Any = None,
    ):
        """
        Depaginates a method. Pass a partial of the method you want here to depaginate.

        :param param_name: The param name to use for paginating.
        :param search_name: The search name to use in the regexp.
        :param max_items: The maximum items to depaginate.
        :param initial_param: The initial offset to depaginate.
        """
        if search_name is None:
            search_name = param_name

        last_id = initial_param
        to_process = []

        for x in range(0, 9999):  # reasonable upper bound is 9999, 9999 * 30 is ~300k bookmarks...
            if not last_id:
                print("Downloading initial illustrations page...")
                response = meth()
            else:
                print(f"Downloading illustrations page after {last_id}")
                params = {param_name: last_id}
                response = meth(**params)

            illusts = response["illusts"]
            print(f"Downloaded {len(illusts)} objects (current tally: {len(to_process)})")
            to_process += illusts

            if max_items is not None and len(to_process) >= max_items:
                break

            next_url = response["next_url"]
            if next_url is not None:
                last_id = re.findall(f"{search_name}=([0-9]+)", next_url)[0]
            else:
                # no more bookmarks!
                break

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
        print(f"Linked {final_dir} -> {original_dir}")

    def do_download_with_symlinks(self, dest_dir: Path, items: List[DownloadableImage]):
        """
        Does a download with symlinking.
        """
        raw_dir = self.output_dir / "raw"
        self.download_page(items)
        self.do_symlinks(raw_dir, dest_dir, items[0].id)

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
            tags.update(set(td.values()))

        filtered = tags.intersection(self.filtered_tags)
        required = tags.intersection(self.required_tags)
        bookmarks = illust['total_bookmarks']

        if not illust['visible']:
            msg = "Illustration is not visible"

        elif illust["x_restrict"] and not self.allow_r18:
            msg = "Illustration is R-18"

        elif lewd_level < self.lewd_limits[0]:
            msg = f"Illustration lewd level ({lewd_level}) is below minimum lewd level"

        elif lewd_level > self.lewd_limits[1]:
            msg = f"Illustration lewd level ({lewd_level}) is above maximum lewd level"

        elif self.filtered_tags and filtered:
            msg = f"Illustration contains filtered tags {filtered}"

        elif self.required_tags and not required:
            msg = f"Illustration missing any of the required tags {self.required_tags}"

        elif self.bookmark_limits[1] is not None and self.bookmark_limits[1] < bookmarks:
            msg = f"Illustration has too many bookmarks ({bookmarks})"

        elif self.bookmark_limits[0] is not None and self.bookmark_limits[0] > bookmarks:
            msg = f"Illustation doesn't have enough bookmarks ({bookmarks})"

        return msg is not None, msg

    def process_and_save_illusts(
        self, illusts: List[dict], silent: bool = False
    ) -> List[List[DownloadableImage]]:
        """
        Processes and saves the list of illustrations.

        This takes the list of illustration responses, saves them, and returns a list of
        DownloadableImage to download.
        """
        to_dl = []

        for illust in illusts:
            id = illust["id"]
            title = illust["title"]

            filtered, msg = self.filter_illust(illust)
            if filtered:
                print(f"Filtered illustration {id} ({title}): {msg}")
                continue

            raw_dir = self.output_dir / "raw"
            self.store_illust_metadata(raw_dir, illust)
            obs = self.make_downloadable(illust)
            to_dl.append(obs)

            print(
                f"Processed metadata for {illust['id']} ({illust['title']}) "
                f"with {len(obs)} pages"
            )

        return to_dl

    def download_bookmarks(self):
        """
        Downloads the bookmarks for this user.
        """
        # set up the output dirs
        raw = self.output_dir / "raw"
        raw.mkdir(exist_ok=True)

        bookmark_dir = self.output_dir / "bookmarks"
        bookmark_dir.mkdir(exist_ok=True)

        fn = partial(self.aapi.user_bookmarks_illust, self.aapi.user_id)
        to_process = self.depaginate_download(fn, param_name="max_bookmark_id")
        # downloadable objects, list of lists
        to_dl = self.process_and_save_illusts(to_process)
        # free memory during the download process, we don't need these anymore
        to_process.clear()

        print("Downloading images concurrently...")
        with ThreadPoolExecutor(4) as e:
            return list(e.map(partial(self.do_download_with_symlinks, bookmark_dir), to_dl))

    def mirror_user(self, user_id: int, *, full: bool = False):
        """
        Mirrors a user.
        """
        raw = self.output_dir / "raw"
        raw.mkdir(exist_ok=True)

        # the images themselves are downloaded to raw/ but we symlink them into the user dir
        user_dir = self.output_dir / "users" / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        works_dir = user_dir / "works"
        works_dir.mkdir(parents=True, exist_ok=True)
        bookmarks_dir = user_dir / "bookmarks"
        bookmarks_dir.mkdir(parents=True, exist_ok=True)

        print(f"Downloading info for user {user_id}...")
        user_info = self.aapi.user_detail(user_id)

        # unfortunately, this doesn't give the nice background image...
        images = user_info["user"]["profile_image_urls"]
        url = images["medium"]
        suffix = url.split(".")[-1]
        print(f"Saving profile image...")
        self.aapi.download(url, path=user_dir, name=f"avatar.{suffix}")

        print(f"Saving metadata...")
        user_info["_meta"] = {"download-date": arrow.utcnow().isoformat(), "tool": "pixiv-dl"}
        (user_dir / "meta.json").write_text(json.dumps(user_info, indent=4))

        print(
            f"Downloading all works for user {user_id} || {user_info['user']['name']} "
            f"|| {user_info['user']['account']}"
        )

        # very generic...
        fn = partial(self.aapi.user_illusts, user_id=user_id)
        to_process_works = self.depaginate_download(fn, param_name="offset")
        to_dl_works = self.process_and_save_illusts(to_process_works)

        if full:
            print(f"Downloading all bookmarks for user {user_id}")
            fn2 = partial(self.aapi.user_bookmarks_illust, user_id=user_id)
            to_process_bookmarks = self.depaginate_download(fn2)
            to_dl_bookmarks = self.process_and_save_illusts(to_process_bookmarks)

        print("Downloading images concurrently...")
        with ThreadPoolExecutor(4) as e:
            l1 = list(e.map(partial(self.do_download_with_symlinks, works_dir), to_dl_works))
            if full:
                l2 = e.map(partial(self.do_download_with_symlinks, bookmarks_dir), to_dl_bookmarks)
                return l1 + l2
            else:
                return l1

    def download_following(self, max_items: int = 100):
        """
        Downloads all your following.

        :param max_items: The maximum number of items to download.
        """
        raw = self.output_dir / "raw"
        raw.mkdir(exist_ok=True)

        follow_dir = self.output_dir / "following"
        follow_dir.mkdir(exist_ok=True)

        for x in range(0, max_items, 30):
            print(f"Downloading items {x + 1} - {x + 31}")

            fn = partial(self.aapi.illust_follow)
            to_process = self.depaginate_download(
                fn, max_items=30, param_name="offset", initial_param=x
            )
            # no more to DL
            if len(to_process) == 0:
                return

            to_dl = self.process_and_save_illusts(to_process)

            print("Downloading images concurrently...")

            with ThreadPoolExecutor(4) as e:
                # list() call unwraps errors
                list(e.map(partial(self.do_download_with_symlinks, follow_dir), to_dl))

    def download_tag(self, main_tag: str):
        """
        Downloads all items for a tag.
        """
        raw = self.output_dir / "raw"
        raw.mkdir(exist_ok=True)

        tags_dir = self.output_dir / "tags"
        tags_dir.mkdir(exist_ok=True)

        # no plural, this is the singular tag
        tag_dir = tags_dir / main_tag
        tag_dir.mkdir(exist_ok=True)

        # todo
        max_items = 1000

        for x in range(0, max_items, 30):
            print(f"Downloading items {x + 1} - {x + 31}")

            fn = partial(self.aapi.search_illust, word=main_tag)
            to_process = self.depaginate_download(
                fn, max_items=30, param_name="offset", initial_param=x
            )
            # no more to DL
            if len(to_process) == 0:
                return

            to_dl = self.process_and_save_illusts(to_process)

            print("Downloading images concurrently...")

            with ThreadPoolExecutor(4) as e:
                list(e.map(partial(self.do_download_with_symlinks, tag_dir), to_dl))


def main():
    parser = argparse.ArgumentParser(
        description=textwrap.dedent(
            """A pixiv downloader tool.

        This can download your bookmarks, your following feed, whole user accounts, etc.
        """
        )
    )
    parser.add_argument("-u", "--username", help="Your pixiv username")
    parser.add_argument("-p", "--password", help="Your pixiv password")
    parser.add_argument(
        "-o", "--output", help="The output directory for the command to run", default="./output"
    )

    parser.add_argument(
        "--allow-r18", action="store_true", help="If R-18 works should also be downloaded"
    )

    parser.add_argument("--min-lewd-level", type=int, default=0, help="The minimum 'lewd level'")
    parser.add_argument("--max-lewd-level", type=int, default=6, help="The maximum 'lewd level'")

    parser.add_argument(
        "--filter-tag", action="append", help="Ignore any illustrations with this tag"
    )
    parser.add_argument(
        "--require-tag", action="append", help="Require illustrations to have this tag"
    )

    parser.add_argument(
        "--min-bookmarks", type=int, default=None, help="Minimum number of bookmarks"
    )
    parser.add_argument(  # i have no idea when this will ever be useful, but symmetry
        "--max-bookmarks", type=int, default=None, help="Maximum number of bookmarks"
    )

    parsers = parser.add_subparsers(dest="subcommand")

    # download all bookmarks mode, no arguments
    bookmark_mode = parsers.add_parser("bookmarks", help="Download bookmarks")

    # download all following
    following_mode = parsers.add_parser("following", help="Download all following")
    following_mode.add_argument(
        "-l", "--limit", default=500, help="The maximum number of items to download", type=int
    )

    # mirror mode
    mirror_mode = parsers.add_parser("mirror", help="Mirror a user")
    mirror_mode.add_argument("userid", help="The user ID to mirror", type=int)
    mirror_mode.add_argument(
        "-f",
        "--full",
        action="store_true",
        help="If this should also " "mirror all their bookmarks",
    )

    # tag mode
    tag_mode = parsers.add_parser("tag", help="Download works with a tag")
    tag_mode.add_argument("tag", help="The main tag to filter by")

    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(exist_ok=True)

    public_api = pixivpy3.PixivAPI()
    public_api.set_accept_language("en-us")
    # ew
    aapi = pixivpy3.AppPixivAPI()
    aapi.set_accept_language("en-us")
    print("Authenticating with Pixiv...")
    token_file = output / "refresh_token"
    if token_file.exists():
        aapi.auth(refresh_token=token_file.read_text())
        print(f"Successfully logged in with token as {aapi.user_id}")
    else:
        if not args.username or not args.password:
            print("No refresh token found and no username/password provided, cannot login")
            return

        aapi.auth(username=args.username, password=args.password)
        public_api.set_auth(aapi.access_token, aapi.refresh_token)
        token_file.write_text(aapi.refresh_token)
        print(f"Successfully logged in with username/password as {aapi.user_id}")

    if args.filter_tag is None:
        args.filter_tag = []
    if args.require_tag is None:
        args.require_tag = []

    dl = Downloader(
        aapi,
        public_api,
        output,
        allow_r18=args.allow_r18,
        lewd_limits=(args.min_lewd_level, args.max_lewd_level),
        filter_tags=set(args.filter_tag),
        required_tags=set(args.require_tag),
        bookmark_limits=(args.min_bookmarks, args.max_bookmarks),
    )

    subcommand = args.subcommand
    if subcommand == "bookmarks":
        print("Downloading all bookmarks...")
        return dl.download_bookmarks()
    elif subcommand == "following":
        print("Downloading your following...")
        return dl.download_following(max_items=args.limit)
    elif subcommand == "mirror":
        if args.full:
            print("Fully mirroring a user...")
        else:
            print("Mirroring a user...")
        return dl.mirror_user(args.userid, full=args.full)
    elif subcommand == "tag":
        return dl.download_tag(args.tag)
    else:
        print(f"Unknown command {subcommand}")


if __name__ == "__main__":
    main()
