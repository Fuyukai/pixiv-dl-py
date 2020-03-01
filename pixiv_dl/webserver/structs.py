from __future__ import annotations

import enum
from dataclasses import dataclass

import pendulum
from pendulum import DateTime

from pixiv_dl.db import Artwork


@dataclass
class ArtworkCard:
    """
    Container class for an artwork card.
    """

    #: ID of the artwork
    id: int
    #: Title of the artwork
    title: str
    #: Description of the artwork
    description: str
    #: Creation time of the artwork
    create_date: DateTime
    #: Author ID
    author_id: int
    #: Author name
    author_name: str
    #: Is work R-18?
    r18: bool
    #: The page count of this artwork.
    page_count: int

    @staticmethod
    def card_from_artwork(artwork: Artwork) -> ArtworkCard:
        """
        Makes an ArtworkCard for an Artwork.
        """
        return ArtworkCard(
            id=artwork.id,
            title=artwork.title,
            description=artwork.caption,
            author_id=artwork.author_id,
            author_name=artwork.author.name,
            r18=artwork.r18 or artwork.r18g,
            create_date=pendulum.instance(artwork.uploaded_at),
            page_count=artwork.page_count,
        )


@dataclass
class TagCard:
    """
    Container class for a tag card.
    """

    #: The name of the tag.
    name: str
    #: The artwork card associated with this tag.
    artwork: ArtworkCard
    #: The number of artworks saved under this tag.
    count: int
    #: The translated name of this tag, if any.
    translated_name: str = None


class SortMode(enum.Enum):
    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"
    RANDOM = "RANDOM"
