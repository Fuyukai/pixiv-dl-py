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
from typing import List, Set, Any

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
        *,
        allow_r18: bool = False,
        lewd_limits=(0, 6),
        filter_tags: Set[str] = None,
        required_tags: Set[str] = None,
    ):
        """
        :param aapi: The Pixiv app API interface.
        :param papi: The Pixiv Public API interface.

        Behaviour params:
        :param allow_r18: If R-18 content should be downloaded, too.
        :param lewd_limits: The 'lewd level' limits. 2 = sfw, 4 = moderately nsfw, 6 = super nsfw.
        :param filter_tags: If an illustration has any of these tags, it will be ignored.
        :param required_tags: If an illustration doesn't have any of these tags, it will be ignored.

        .. note::

            Even if lewd_limits[1] is set to 6, allow_r18 needs to be set to download x_restrict
            tagged items.
        """
        self.aapi = aapi
        self.papi = papi

        self.allow_r18 = allow_r18
        self.lewd_limits = lewd_limits

        self.filtered_tags = filter_tags
        self.required_tags = required_tags

    def download_page(self, raw_dir: Path, items: List[DownloadableImage]):
        """
        Downloads a page image.
        """
        for item in items:
            output_dir = raw_dir / str(item.id)
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

        (raw_dir / str(items[0].id) / "marker.json").write_text(
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
        illust["_meta"] = {"download-date": arrow.utcnow().isoformat(), "tool": "pixiv-dl"}

        illust_id = illust["id"]
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
    def do_symlinks(raw_dir: Path, output_dir: Path, illust_id: int):
        """
        Performs symlinking for an illustration.
        """
        original_dir = raw_dir / str(illust_id)
        # invisible or otherwise excluded
        if not original_dir.exists():
            return

        final_dir = output_dir / str(illust_id)

        # no easy way to check if a broken symlink exists other than just... doing this
        try:
            final_dir.unlink()
        except FileNotFoundError:
            pass

        final_dir.symlink_to(original_dir.resolve(), target_is_directory=True)
        print(f"Linked {final_dir} -> {original_dir}")

    def do_download_with_symlinks(self, raw_dir: Path, output_dir: Path,
                                  items: List[DownloadableImage]):
        """
        Does a download with symlinking.
        """
        self.download_page(raw_dir, items)
        self.do_symlinks(raw_dir, output_dir, items[0].id)

    def process_and_save_illusts(
        self, output_dir: Path, illusts: List[dict], silent: bool = False
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
            lewd_level = illust["sanity_level"]

            # visibility check
            if not illust["visible"]:
                if not silent:
                    msg = f"Skipping illustration {id} because it is not marked as visible!"
                    print(msg)

                continue

            # R-18 tag
            if illust["x_restrict"] and not self.allow_r18:
                if not silent:
                    msg = f"Skipping R-18 illustration {illust['id']} ({illust['title']})"
                    print(msg)

                continue

            # granular sfw checks
            if lewd_level < self.lewd_limits[0]:
                if not silent:
                    msg = (
                        f"Skipping illustation {id} ({title}): "
                        f"lewd level of {lewd_level} is below limit"
                    )
                    print(msg)

                continue

            if lewd_level > self.lewd_limits[1]:
                if not silent:
                    msg = (
                        f"Skipping illustation {id} ({title}): "
                        f"lewd level of {lewd_level} is above limit"
                    )
                    print(msg)

                continue

            # verify tags
            tags = set()
            for td in illust["tags"]:
                tags.update(set(td.values()))

            # note to self: self if check is to ensure that we don't do a tag filter if we don't
            # need to.
            filtered = tags.intersection(self.filtered_tags)
            if self.filtered_tags and filtered:
                if not silent:
                    msg = (
                        f"Skipping illustration {illust['id']} ({illust['title']}) "
                        f"as it has filtered tags: {filtered}"
                    )
                    print(msg)

                continue

            required = tags.intersection(self.required_tags)
            if self.required_tags and not required:
                if not silent:
                    msg = (
                        f"Skipping illustration {illust['id']} ({illust['title']}) "
                        f"as it has none of the required tags: {self.required_tags}"
                    )
                    print(msg)

                continue

            self.store_illust_metadata(output_dir, illust)
            obs = self.make_downloadable(illust)
            to_dl.append(obs)

            if not silent:
                print(
                    f"Processed metadata for {illust['id']} ({illust['title']}) "
                    f"with {len(obs)} pages"
                )

        return to_dl

    def download_bookmarks(self, output_dir: Path):
        """
        Downloads the bookmarks for this user.
        """
        # set up the output dirs
        raw = output_dir / "raw"
        raw.mkdir(exist_ok=True)

        bookmark_dir = output_dir / "bookmarks"
        bookmark_dir.mkdir(exist_ok=True)

        fn = partial(self.aapi.user_bookmarks_illust, self.aapi.user_id)
        to_process = self.depaginate_download(fn, param_name="max_bookmark_id")
        # downloadable objects, list of lists
        to_dl = self.process_and_save_illusts(raw, to_process)
        # free memory during the download process, we don't need these anymore
        to_process.clear()

        print("Downloading images concurrently...")
        with ThreadPoolExecutor(4) as e:
            e.map(partial(self.do_download_with_symlinks, raw, bookmark_dir), to_dl)

    def mirror_user(self, output_dir: Path, user_id: int, *, full: bool = False):
        """
        Mirrors a user.
        """
        raw = output_dir / "raw"
        raw.mkdir(exist_ok=True)

        # the images themselves are downloaded to raw/ but we symlink them into the user dir
        user_dir = output_dir / "users" / str(user_id)
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
        to_dl_works = self.process_and_save_illusts(raw, to_process_works)

        if full:
            print(f"Downloading all bookmarks for user {user_id}")
            fn2 = partial(self.aapi.user_bookmarks_illust, user_id=user_id)
            to_process_bookmarks = self.depaginate_download(fn2)
            to_dl_bookmarks = self.process_and_save_illusts(raw, to_process_bookmarks)

        print("Downloading images concurrently...")
        with ThreadPoolExecutor(4) as e:
            e.map(partial(self.do_download_with_symlinks, raw, works_dir), to_dl_works)
            if full:
                e.map(partial(self.do_download_with_symlinks, raw, bookmarks_dir), to_dl_bookmarks)

    def download_following(self, output_dir: Path, max_items: int = 100):
        """
        Downloads all your following.

        :param max_items: The maximum number of items to download.
        """
        raw = output_dir / "raw"
        raw.mkdir(exist_ok=True)

        follow_dir = output_dir / "following"
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

            to_dl = self.process_and_save_illusts(raw, to_process)

            print("Downloading images concurrently...")

            with ThreadPoolExecutor(4) as e:
                e.map(partial(self.do_download_with_symlinks, raw, follow_dir), to_dl)


def main():
    parser = argparse.ArgumentParser(
        description=textwrap.dedent(
            """A pixiv downloader tool.

        This can download your bookmarks, your following feed, whole user accounts, etc.
        """
        )
    )
    parser.add_argument("-u", "--username", help="Your pixiv username", required=True)
    parser.add_argument("-p", "--password", help="Your pixiv password", required=True)
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
        allow_r18=args.allow_r18,
        lewd_limits=(args.min_lewd_level, args.max_lewd_level),
        filter_tags=set(args.filter_tag),
        required_tags=set(args.require_tag),
    )

    subcommand = args.subcommand
    if subcommand == "bookmarks":
        print("Downloading all bookmarks...")
        return dl.download_bookmarks(output)
    elif subcommand == "following":
        print("Downloading your following...")
        return dl.download_following(output, max_items=args.limit)
    elif subcommand == "mirror":
        if args.full:
            print("Fully mirroring a user...")
        else:
            print("Mirroring a user...")
        return dl.mirror_user(output, args.userid, full=args.full)
    else:
        print(f"Unknown command {subcommand}")


if __name__ == "__main__":
    main()
