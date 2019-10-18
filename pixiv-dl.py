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
from pprint import pprint
from typing import List, Tuple

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
    def __init__(self, aapi: pixivpy3.AppPixivAPI, papi: pixivpy3.PixivAPI,
                 *, allow_r18: bool = False, lewd_limits=(0, 6)):
        """
        :param aapi: The Pixiv app API interface.
        :param papi: The Pixiv Public API interface.

        Behaviour params:
        :param allow_r18: If R-18 content should be downloaded, too.
        :param lewd_limits: The 'lewd level' limits. 2 = sfw, 4 = moderately nsfw, 6 = super nsfw.

        .. note::

            Even if lewd_limits[1] is set to 6, allow_r18 needs to be set to download x_restrict
            tagged items.
        """
        self.aapi = aapi
        self.papi = papi

        self.allow_r18 = allow_r18
        self.lewd_limits = lewd_limits

    def download_page(self, raw_dir: Path, items: List[DownloadableImage]):
        """
        Downloads a page image.
        """
        for item in items:
            output_dir = raw_dir / str(item.id)
            output_dir.mkdir(parents=True, exist_ok=True)

            if (output_dir / "marker").exists():
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

        (raw_dir / str(items[0].id) / "marker").write_text("")

        print(f"Successfully downloaded {item.id}")

    @staticmethod
    def make_downloadable(illust: dict) -> List[DownloadableImage]:
        """
        Makes a list of downloadable images from an illustration object.
        """
        illust_id = illust['id']

        # if it's a single image...
        single_image = illust['page_count'] == 1
        if single_image:
            obs = [DownloadableImage(
                illust_id,
                multi_page=False,
                page_num=1,
                url=illust['meta_single_page']['original_image_url']
            )]
        else:
            obs = []
            for idx, subpage in enumerate(illust['meta_pages']):
                obb = DownloadableImage(
                    illust_id,
                    multi_page=True,
                    page_num=idx + 1,
                    url=subpage['image_urls']['original']
                )
                obs.append(obb)

        return obs

    @staticmethod
    def store_illust_metadata(output_dir: Path, illust: dict):
        """
        Stores the metadata for a specified illustration.
        """
        illust["_meta"] = {
            "download-date": arrow.utcnow().isoformat(),
            "tool": "pixiv-dl"
        }

        illust_id = illust['id']
        # the actual location
        subdir = (output_dir / str(illust_id))
        subdir.mkdir(exist_ok=True)

        # write the raw metadata for later usage, if needed
        (subdir / "meta.json").write_text(json.dumps(illust, indent=4))

    @staticmethod
    def depaginate_download(meth, param_name: str = "last_bookmark_id", search_name: str = None):
        """
        Depaginates a method. Pass a partial of the method you want here to depaginate.

        :param param_name: The param name to use for paginating.
        :param search_name: The search name to use in the regexp.
        """
        if search_name is None:
            search_name = param_name

        last_id = None
        to_process = []

        for x in range(0, 999):  # reasonable upper bound is 999, 999 * 25 is 25k bookmarks...
            # page = x + 1
            if last_id is None:
                print("Downloading initial illustrations page...")
                response = meth()
            else:
                print(f"Downloading illustrations page after {last_id}")
                params = {param_name: last_id}
                response = meth(**params)

            illusts = response['illusts']
            print(f"Downloaded {len(illusts)} objects")
            to_process += illusts

            next_url = response['next_url']
            if next_url is not None:
                last_id = re.findall(f"{search_name}=([0-9]+)", next_url)[0]
            else:
                # no more bookmarks!
                break

        return to_process

    def process_and_save_illusts(self, output_dir: Path,
                                 illusts: List[dict],
                                 silent: bool = False) -> List[List[DownloadableImage]]:
        """
        Processes and saves the list of illustrations.

        This takes the list of illustration responses, saves them, and returns a list of
        DownloadableImage to download.
        """
        to_dl = []

        for illust in illusts:
            id = illust['id']
            title = illust['title']
            lewd_level = illust['sanity_level']
            # granular sfw checks
            if lewd_level < self.lewd_limits[0]:
                if not silent:
                    print(f"Skipping illustation {id} ({title}): "
                          f"lewd level of {lewd_level} is below limit")

                continue

            if lewd_level > self.lewd_limits[1]:
                if not silent:
                    print(f"Skipping illustation {id} ({title}): "
                          f"lewd level of {lewd_level} is above limit")

                continue

            # R-18 tag
            if illust['x_restrict'] and not self.allow_r18:
                if not silent:
                    print(f"Skipping R-18 illustration {illust['id']} ({illust['title']})")

                continue

            self.store_illust_metadata(output_dir, illust)
            obs = self.make_downloadable(illust)
            to_dl.append(obs)

            if not silent:
                print(f"Processed metadata for {illust['id']} ({illust['title']}) "
                      f"with {len(obs)} pages")

        return to_dl

    def download_bookmarks(self, output_dir: Path):
        """
        Downloads the bookmarks for this user.
        """
        # set up the output dirs
        raw = (output_dir / "raw")
        raw.mkdir(exist_ok=True)

        fn = partial(self.aapi.user_bookmarks_illust, self.aapi.user_id)
        to_process = self.depaginate_download(fn, param_name="max_bookmark_id")
        # downloadable objects, list of lists
        to_dl = self.process_and_save_illusts(raw, to_process)
        # free memory during the download process, we don't need these anymore
        to_process.clear()

        print("Downloading images concurrently...")
        with ThreadPoolExecutor(4) as e:
            e.map(partial(self.download_page, raw), to_dl)

    def mirror_user(self, output_dir: Path, user_id: int, *, full: bool = False):
        """
        Mirrors a user.
        """
        raw = (output_dir / "raw")
        raw.mkdir(exist_ok=True)

        # the images themselves are downloaded to raw/ but we symlink them into the user dir
        user_dir = (output_dir / "users" / str(user_id))
        user_dir.mkdir(parents=True, exist_ok=True)

        print(f"Downloading info for user {user_id}...")
        user_info = self.aapi.user_detail(user_id)

        # unfortunately, this doesn't give the nice background image...
        images = user_info['user']['profile_image_urls']
        url = images['medium']
        suffix = url.split(".")[-1]
        print(f"Saving profile image...")
        self.aapi.download(url, path=user_dir, name=f"avatar.{suffix}")

        print(f"Saving metadata...")
        user_info["_meta"] = {
            "download-date": arrow.utcnow().isoformat(),
            "tool": "pixiv-dl"
        }
        (user_dir / "meta.json").write_text(json.dumps(user_info, indent=4))

        print(f"Downloading all works for user {user_id} || {user_info['user']['name']} "
              f"|| {user_info['user']['account']}")

        # very generic...
        fn = partial(self.aapi.user_illusts, user_id=user_id)
        to_process = self.depaginate_download(fn, param_name="offset")
        to_dl = self.process_and_save_illusts(raw, to_process)

        print("Downloading images concurrently...")
        with ThreadPoolExecutor(4) as e:
            e.map(partial(self.download_page, raw), to_dl)

        print("Setting up symlinks...")
        for illust in sorted(to_process, key=lambda i: arrow.get(i['create_date'])):
            original_dir = (raw / str(illust['id']))
            final_dir = (user_dir / str(illust['id']))
            print(f"Linking {final_dir} -> {original_dir}")

            # no easy way to check if a broken symlink exists other than just... doing this
            try:
                final_dir.unlink()
            except FileNotFoundError:
                pass

            final_dir.symlink_to(
                original_dir.resolve(),
                target_is_directory=True
            )


def main():
    parser = argparse.ArgumentParser(
        description=textwrap.dedent("""A pixiv downloader tool.
        
        This can download your bookmarks, your following feed, whole user accounts, etc.
        """)
    )
    parser.add_argument("USERNAME", help="Your pixiv username")
    parser.add_argument("PASSWORD", help="Your pixiv password")
    parser.add_argument("-o", "--output", help="The output directory for the command to run",
                        default="./output")
    parser.add_argument("--allow-r18", action="store_true",
                        help="If R-18 works should also be downloaded")
    parser.add_argument("--min-lewd-level", type=int, default=0,
                        help="The minimum 'lewd level'")
    parser.add_argument("--max-lewd-level", type=int, default=6,
                        help="The maximum 'lewd level'")

    parsers = parser.add_subparsers(dest="subcommand")

    # download all bookmarks mode, no arguments
    bookmark_mode = parsers.add_parser("bookmarks", help="Download bookmarks")

    # download all following
    following_mode = parsers.add_parser("following", help="Download all following")

    # mirror mode
    mirror_mode = parsers.add_parser("mirror", help="Mirror a user")
    mirror_mode.add_argument("userid", help="The user ID to mirror", type=int)
    mirror_mode.add_argument("-f", "--full", action="store_true", help="If this should also "
                                                                       "mirror all their bookmarks")

    args = parser.parse_args()

    public_api = pixivpy3.PixivAPI()
    public_api.set_accept_language("en-us")
    # ew
    aapi = pixivpy3.AppPixivAPI()
    aapi.set_accept_language("en-us")
    print("Authenticating with Pixiv...")
    aapi.auth(username=args.USERNAME, password=args.PASSWORD)
    public_api.set_auth(aapi.access_token, aapi.refresh_token)
    dl = Downloader(aapi, public_api, allow_r18=args.allow_r18,
                    lewd_limits=(args.min_lewd_level, args.max_lewd_level))
    print(f"Successfully logged in as {aapi.user_id}")

    output = Path(args.output)
    output.mkdir(exist_ok=True)

    subcommand = args.subcommand
    if subcommand == "bookmarks":
        print("Downloading all bookmarks...")
        return dl.download_bookmarks(output)
    elif subcommand == "following":
        print("Downloading your following...")
    elif subcommand == "mirror":
        print("Mirroring a user...")
        return dl.mirror_user(output, args.userid, full=args.full)
    else:
        print(f"Unknown command {subcommand}")


if __name__ == "__main__":
    main()
