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
from typing import List

import pixivpy3


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
                 *, allow_r18: bool = False):
        """
        :param aapi: The Pixiv app API interface.
        :param papi: The Pixiv Public API interface.

        Behaviour params:
        :param allow_r18: If R-18 content should be downloaded, too.
        """
        self.aapi = aapi
        self.papi = papi

        self.allow_r18 = allow_r18

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
        raw = (output_dir / "raw")
        raw.mkdir(exist_ok=True)

        illust_id = illust['id']
        # the actual location
        subdir = (raw / str(illust_id))
        subdir.mkdir(exist_ok=True)

        # write the raw metadata for later usage, if needed
        (subdir / "meta.json").write_text(json.dumps(illust, indent=4))

    def download_bookmarks(self, output_dir: Path):
        """
        Downloads the bookmarks for this user.
        """
        # set up the output dirs
        raw = (output_dir / "raw")
        raw.mkdir(exist_ok=True)

        last_bookmark_id = None
        to_process = []

        for x in range(0, 999):  # reasonable upper bound is 999, 999 * 25 is 25k bookmarks...
            # page = x + 1
            if last_bookmark_id is None:
                print("Downloading initial bookmark page...")
                response = self.aapi.user_bookmarks_illust(
                    self.aapi.user_id
                )
            else:
                print(f"Downloading bookmark page after {last_bookmark_id[0]}")
                response = self.aapi.user_bookmarks_illust(
                    self.aapi.user_id, max_bookmark_id=last_bookmark_id[0]
                )
            illusts = response['illusts']
            print(f"Downloaded {len(illusts)} objects")
            to_process += illusts

            next_url = response['next_url']
            if next_url is not None:
                last_bookmark_id = re.findall("max_bookmark_id=([0-9]+)", next_url)
            else:
                # no more bookmarks!
                break

        # downloadable objects, list of lists
        to_dl = []

        # begin processing
        for illust in to_process:
            # R-18 tag
            if illust['x_restrict'] and not self.allow_r18:
                print(f"Skipping R-18 illustration {illust['id']} ({illust['title']})")
                continue

            self.store_illust_metadata(output_dir, illust)
            obs = self.make_downloadable(illust)
            to_dl.append(obs)
            print(f"Processed metadata for {illust['title']} with {len(obs)} pages")

        # free memory during the download process
        to_process.clear()

        print("Downloading images concurrently...")
        with ThreadPoolExecutor(4) as e:
            e.map(partial(self.download_page, raw), to_dl)


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
    dl = Downloader(aapi, public_api, allow_r18=args.allow_r18)
    print(f"Successfully logged in as {aapi.user_id}")

    subcommand = args.subcommand
    if subcommand == "bookmarks":
        print("Downloading all bookmarks...")
        return dl.download_bookmarks(Path(args.output))
    elif subcommand == "following":
        print("Downloading your following...")
    else:
        print(f"Unknown command {subcommand}")


if __name__ == "__main__":
    main()
