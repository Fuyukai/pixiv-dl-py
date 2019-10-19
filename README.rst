pixiv-dl
========

A simple tool to automatically download stuff from pixiv.

Usage
-----

.. code-block::

    usage: pixiv-dl.py [-h] -u USERNAME -p PASSWORD [-o OUTPUT] [--allow-r18]
                       [--min-lewd-level MIN_LEWD_LEVEL]
                       [--max-lewd-level MAX_LEWD_LEVEL] [--filter-tag FILTER_TAG]
                       [--require-tag REQUIRE_TAG]
                       {bookmarks,following,mirror} ...

    A pixiv downloader tool. This can download your bookmarks, your following
    feed, whole user accounts, etc.

    positional arguments:
      {bookmarks,following,mirror}
        bookmarks           Download bookmarks
        following           Download all following
        mirror              Mirror a user

    optional arguments:
      -h, --help            show this help message and exit
      -u USERNAME, --username USERNAME
                            Your pixiv username
      -p PASSWORD, --password PASSWORD
                            Your pixiv password
      -o OUTPUT, --output OUTPUT
                            The output directory for the command to run
      --allow-r18           If R-18 works should also be downloaded
      --min-lewd-level MIN_LEWD_LEVEL
                            The minimum 'lewd level'
      --max-lewd-level MAX_LEWD_LEVEL
                            The maximum 'lewd level'
      --filter-tag FILTER_TAG
                            Ignore any illustrations with this tag
      --require-tag REQUIRE_TAG
                            Require illustrations to have this tag
