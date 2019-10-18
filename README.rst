pixiv-dl
========

A simple tool to automatically download stuff from pixiv.

Usage
-----

.. code-block::

    usage: pixiv-dl.py [-h] [-o OUTPUT]
                   USERNAME PASSWORD {bookmarks,following,mirror} ...

    A pixiv downloader tool. This can download your bookmarks, your following
    feed, whole user accounts, etc.

    positional arguments:
      USERNAME              Your pixiv username
      PASSWORD              Your pixiv password
      {bookmarks,following,mirror}
        bookmarks           Download bookmarks
        following           Download all following
        mirror              Mirror a user

    optional arguments:
      -h, --help            show this help message and exit
      -o OUTPUT, --output OUTPUT
                            The output directory for the command to run