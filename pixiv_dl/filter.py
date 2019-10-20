"""
Filtering utility for filtering downloaded works.
"""
import abc
import argparse
import json
from pathlib import Path
from typing import Any, List, Generator, Tuple, Optional

from termcolor import cprint


class FilterRule(abc.ABC):
    """
    Represents a filter rule.
    """

    @abc.abstractproperty
    def field(self) -> str:
        """
        The field to load from the object to filter.
        """

    @abc.abstractproperty
    def message(self) -> str:
        """
        The failure message to get, if this filter failed.
        """

    @abc.abstractmethod
    def filter(self, value: Any) -> bool:
        """
        Filters a downloaded illustration.

        :param value: The value extracted from the JSON.
        """


class BasicFieldFilterer(FilterRule):
    """
    Represents a basic field filterer.
    """

    def __init__(
        self, field: str, value: Any, *, negative: bool = False, custom_message: str = None
    ):
        """
        :param field: The field to filter.
        :param value: The value to check.
        :param negative: If this should be a negative field - i.e. True if field != value.
        :param custom_message: If there is a custom message.
        """
        self._field = field
        self.value = value
        self.negative = negative
        self.custom_message = custom_message

    @property
    def field(self) -> str:
        return self._field

    @property
    def message(self) -> str:
        if self.custom_message:
            return self.custom_message.format()

        return f"The value `{self.value}` did match the illustation's {self.field}"

    def filter(self, value: Any) -> bool:
        # clever!!
        # if negative is False, then == should be not False so it should be True
        # if negative is True, then == should be not True so it should be False
        return (value == self.value) is not self.negative


class TagFilterer(FilterRule):
    """
    Filters by a tag.
    """

    field = "tags"

    def __init__(self, tag: str, *, exclude: bool = False):
        """
        :param tag: The tag to filter.
        :param exclude: If this should work in reverse mode and exclude the tag instead.
        """
        self.tag = tag

    @property
    def message(self) -> str:
        return f"Tag not found: {self.tag}"

    def filter(self, value: Any) -> bool:
        # annoying tags...
        tags = set()
        for td in value:
            tags.update(set(x.lower() for x in td.values() if x))

        return self.tag.lower() in tags


class Filterer(object):
    """
    Represents a filterer that filters out a downloaded pixiv database.
    """

    def __init__(self, dir: Path):
        """
        :param dir: The directory to filter.
        """
        self.dir = dir

        self.filter_rules: List[FilterRule] = []

    def add_rule(self, rule: FilterRule):
        """
        Adds a rule to the list of filter rules.
        """
        self.filter_rules.append(rule)

    def check_valid(self, obb) -> Tuple[bool, Optional[str]]:
        """
        Checks if an illustration is valid.
        """
        for rule in self.filter_rules:
            data = obb[rule.field]
            valid = rule.filter(data)
            if not valid:
                return False, rule.message

        return True, None

    def filter_illusts(self, *, print_messages: bool = True) -> Generator[Path, None, None]:
        """
        Filters illustrations.
        """
        for item in self.dir.iterdir():
            # make sure we have a meta file
            meta = item / "meta.json"
            if not meta.exists():
                continue

            with meta.open(mode="r") as f:
                data = json.load(f)

            id = data["id"]

            valid, message = self.check_valid(data)
            if valid:
                if print_messages:
                    cprint(f"Found illust {id} ({data['title']})", "green")

                yield item
            else:
                if print_messages:
                    cprint(f"Skipped illust {id}: {message}", "red")

    def symlink_filtered(self, output_dir: Path, *, suppress_filter_messages: bool = False):
        """
        Filters data then symlinks it into the output.
        """
        for path in self.filter_illusts(print_messages=not suppress_filter_messages):
            initial = path.resolve()
            id = initial.parts[-1]
            to_dir = output_dir / id

            # no easy way to check if a broken symlink exists other than just... doing this
            try:
                to_dir.unlink()
            except FileNotFoundError:
                pass

            to_dir.symlink_to(initial, target_is_directory=True)
            cprint(f"Linked {path} to {to_dir}", "cyan")


def main():
    parser = argparse.ArgumentParser(description="Filters out a locally downloaded pixiv mirror")
    # output controls
    parser.add_argument("-d", "--db", help="The db dir (containing config, etc)", default="output/")
    parser.add_argument(
        "-s",
        "--subdir",
        help="The subdirectory to filter through (default is the raw downloaded files",
        default="raw/",
    )
    parser.add_argument(
        "-o", "--output", help="The directory to output the filtered items", default="filtered/"
    )
    parser.add_argument(
        "--suppress-extra",
        help="Suppress unfiltered entry message printing",
        action="store_true",
        default=False,
    )

    # filter controls
    parser.add_argument(
        "--filter-field", help="Adds a filter on a field", action="append", default=[]
    )
    parser.add_argument("--filter-tag", help="Adds a filter on a tag", action="append", default=[])

    r18_group = parser.add_mutually_exclusive_group()
    r18_group.add_argument(
        "--filter-r18", help="Adds a filter for all R-18 content", action="store_true"
    )
    r18_group.add_argument(
        "--filter-not-r18", help="Adds a filter for all non R-18 content", action="store_true"
    )  # for the ultimate in porn downloading

    parser.add_argument(
        "--filter-user", help="Adds a filter on a user", type=int, action="append", default=[]
    )

    args = parser.parse_args()

    # create dirs
    db_dir = Path(args.db)
    db_dir.mkdir(exist_ok=True)

    subdir = db_dir / args.subdir
    subdir.mkdir(exist_ok=True)

    output_dir = Path(args.output)
    output_dir.mkdir(exist_ok=True)

    filterer = Filterer(subdir)

    # build all the rulees
    for simple_rule in args.filter_field:
        field, value = simple_rule.split("=", 1)
        filterer.add_rule(BasicFieldFilterer(field, value))

    if args.filter_r18:
        filterer.add_rule(
            BasicFieldFilterer(
                "x_restrict", 0, negative=True, custom_message="Illustration is not R-18"
            )
        )
    elif args.filter_not_r18:
        filterer.add_rule(
            BasicFieldFilterer("x_restrict", 0, custom_message="Illustration is " "R-18")
        )

    for tag in args.filter_tag:
        filterer.add_rule(TagFilterer(tag))

    filterer.symlink_filtered(output_dir, suppress_filter_messages=args.suppress_extra)


if __name__ == "__main__":
    main()
