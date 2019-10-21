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

    @abc.abstractmethod
    def get_message(self, value: Any) -> str:
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

    def __init__(self, field: str, value: Any, *, invert: bool = False, custom_message: str = None):
        """
        :param field: The field to filter.
        :param value: The value to check.
        :param invert: If this should be a negative field - i.e. True if field != value.
        :param custom_message: If there is a custom message.
        """
        self._field = field
        self.value = value
        self.invert = invert
        self.custom_message = custom_message

    @property
    def field(self) -> str:
        return self._field

    def get_message(self, value: Any) -> str:
        if self.custom_message:
            return self.custom_message.format(field=self.field, value=value, invert=self.invert)

        if self.invert:
            return f"The value `{self.value}` matched the illustation's {self.field}"
        else:
            return f"The value `{self.value}` did not match the illustration's {self.field}"

    def filter(self, value: Any) -> bool:
        # clever!!
        # if negative is False, then == should be not False so it should be True
        # if negative is True, then == should be not True so it should be False
        return (value == self.value) is not self.invert


class GtLtFilterRule(BasicFieldFilterer):
    """
    A greater-than/less-than filter rule
    """

    def __init__(self, field: str, value: Any, op: str = ">=", *, custom_message: str = None):
        super().__init__(field=field, value=value, custom_message=custom_message)
        self.op = op

    def filter(self, value: Any) -> bool:
        if self.op == ">=":
            return value >= self.value
        elif self.op == ">":
            return value > self.value
        elif self.op == "<":
            return value < self.value
        elif self.op == "<=":
            return value <= self.value


class TagFilterer(FilterRule):
    """
    Filters by a tag.
    """

    field = "tags"

    def __init__(self, tag: str, *, invert: bool = False):
        """
        :param tag: The tag to filter.
        :param invert: If this should work in reverse mode and exclude the tag instead.
        """
        self.tag = tag.lower()
        self.invert = invert

    def get_message(self, value) -> str:
        if self.invert:
            return f"Unwanted tag found: {self.tag}"
        else:
            return f"Tag not found: {self.tag}"

    def filter(self, value: Any) -> bool:
        # annoying tags...
        tags = set()
        for td in value:
            tags.update(set(x.lower() for x in td.values() if x))

        if self.invert:
            return self.tag not in tags
        else:
            return self.tag in tags


class UserFilterer(FilterRule):
    """
    Filters by a user.
    """

    field = "user"

    def __init__(self, user_id: int, *, invert: bool = False):
        self.user_id = user_id
        self.invert = invert

    def filter(self, value: Any) -> bool:
        user_id = value["id"]
        if self.invert:
            return user_id == self.user_id
        else:
            return user_id != self.user_id

    def get_message(self, value) -> str:
        user_id = value["id"]
        if self.invert:
            return f"Not posted by {self.user_id}, but by {user_id}"
        else:
            return f"Posted by {self.user_id}"


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
                return False, rule.get_message(data)

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
        "--override-dir", help="The dir to actually filter in. Defaults to $db/raw", default=None
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

    # generic parser
    parser.add_argument(
        "--require-field", help="Adds a filter on a field", action="append", default=[]
    )

    # tags
    parser.add_argument("--require-tag", help="Requires a tag", action="append", default=[])
    parser.add_argument("--exclude-tag", help="Excludes a tag", action="append", default=[])

    r18_group = parser.add_mutually_exclusive_group()
    r18_group.add_argument(
        "--require-r18", help="Requires R-18 content", action="store_true"
    )  # for the ultimate in porn downloading
    r18_group.add_argument("--exclude-r18", help="Excludes R-18 content", action="store_true")

    parser.add_argument(
        "--require-user", help="Requires a user ID", type=int, action="append", default=[]
    )
    parser.add_argument(
        "--exclude-user", help="Excludes a user ID", type=int, action="append", default=[]
    )

    # copied from downloader
    parser.add_argument(
        "--min-bookmarks", type=int, help="Minimum number of bookmarks", required=False
    )
    parser.add_argument(  # i have no idea when this will ever be useful, but symmetry
        "--max-bookmarks", type=int, help="Maximum number of bookmarks", required=False
    )
    parser.add_argument(
        "--min-lewd-level", type=int, help="The minimum 'lewd level'", required=False
    )
    parser.add_argument(
        "--max-lewd-level", type=int, help="The maximum 'lewd level'", required=False
    )

    args = parser.parse_args()

    # create dirs
    db_dir = Path(args.db)
    db_dir.mkdir(exist_ok=True)

    if args.override_dir is not None:
        filter_dir = Path(args.override_dir)
    else:
        filter_dir = db_dir / "raw"

    output_dir = Path(args.output)
    output_dir.mkdir(exist_ok=True)

    filterer = Filterer(filter_dir)

    # build all the rules
    for simple_rule in args.require_field:
        cprint(f"Adding filter rule for {simple_rule}", "cyan")
        field, value = simple_rule.split("=", 1)
        filterer.add_rule(BasicFieldFilterer(field, value))

    for user in args.require_user:
        cprint(f"Adding required user {user}", "cyan")
        filterer.add_rule(UserFilterer(user))
    for user in args.exclude_user:
        cprint(f"Adding excluded user {user}", "magenta")
        filterer.add_rule(UserFilterer(user, invert=True))

    if args.require_r18:
        cprint(f"Adding required R-18 rule", "cyan")
        filterer.add_rule(
            BasicFieldFilterer(
                "x_restrict", 0, invert=True, custom_message="Illustration is not R-18"
            )
        )

    elif args.exclude_r18:
        cprint(f"Adding required non R-18 rule", "magenta")
        filterer.add_rule(
            BasicFieldFilterer("x_restrict", 0, custom_message="Illustration is R-18")
        )

    for tag in args.require_tag:
        cprint(f"Adding required tag {tag}", "cyan")
        filterer.add_rule(TagFilterer(tag))
    for tag in args.exclude_tag:
        cprint(f"Adding excluded tag {tag}", "magenta")
        filterer.add_rule(TagFilterer(tag, invert=True))

    if args.min_bookmarks is not None:
        cprint(f"Adding minimum bookmarks requirement {args.min_bookmarks}", "cyan")
        filterer.add_rule(GtLtFilterRule("total_bookmarks", args.min_bookmarks, op=">="))
    if args.max_bookmarks is not None:
        cprint(f"Adding maximum bookmarks requirement {args.max_bookmarks}", "magenta")
        filterer.add_rule(GtLtFilterRule("total_bookmarks", args.max_bookmarks, op="<="))

    if args.min_lewd_level is not None:
        cprint(f"Adding minimum lewd level requirement {args.min_lewd_level}", "cyan")
        msg = f"Lewd level {{value}} lower than minimum ({args.min_lewd_level})"

        filterer.add_rule(
            GtLtFilterRule("sanity_level", args.min_lewd_level, op=">=", custom_message=msg)
        )
    if args.max_lewd_level is not None:
        cprint(f"Adding maximum lewd level requirement {args.max_lewd_level}", "magenta")
        msg = f"Lewd level {{value}} higher than maximum ({args.max_lewd_level})"

        filterer.add_rule(
            GtLtFilterRule("sanity_level", args.max_lewd_level, op="<=", custom_message=msg)
        )

    filterer.symlink_filtered(output_dir, suppress_filter_messages=args.suppress_extra)


if __name__ == "__main__":
    main()
