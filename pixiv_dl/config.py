"""
Configuration tool for pixiv-dl. Allows loading default options (such as filtered tags, limits,
etc).
"""
from pathlib import Path

import tomlkit

default_config = """# Default values to load so you don't have to constantly re-define them.
# Uncomment any of these to apply them.
# Note that none of the commented out entries are the real "default" values if they don't exist -
# they're just there as examples.
[defaults.downloader]
## The default filtered tags. This corresponds to passing --filtered-tag on each entry.
# filtered_tags = [
#   "whatever",
# ]

## The default required tags. This corresponds to passing --required-tag on each entry.
# required_tags = [
#   "whatever"
# ]

## If R-18 works should be enabled. This corresponds to passing --allow-r18.
# allow_r18 = true

## Default lewd level controls.
# min_lewd_level = 2
# max_lewd_level = 6

## Default maximum amount of pages.
# max_pages = 100

## Default bookmark level controls.
# min_bookmarks = 10
# max_bookmarks = 100

[config.downloader]
## If the defaults specified above should apply to your bookmarks.
## This is a setting because, well, it doesn't make much sense to filter your bookmarks...
filter_bookmarks = false 
"""


def get_config_in(dir: Path):
    """
    Gets the configuration in the specified directory, writing the default one if it doesn't exist.
    """
    file = dir / "config.toml"
    if not file.exists():
        file.write_text(default_config)

    return tomlkit.parse(file.read_text())




if __name__ == "__main__":
    main()
