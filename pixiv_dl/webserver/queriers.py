from typing import List

from sqlalchemy import func
from sqlalchemy.orm import Session, Query

from pixiv_dl.db import Bookmark, ArtworkTag, Artwork
from pixiv_dl.webserver.structs import SortMode, ArtworkCard, TagCard


def query_tags_all(session: Session, after: int, sort_mode: SortMode):
    """
    Implements querying all tags.
    """
    total: int = session.query(ArtworkTag.name).group_by(ArtworkTag.name).count()

    if sort_mode == SortMode.ASCENDING:
        order = func.count(ArtworkTag.name).asc()
    else:
        order = func.count(ArtworkTag.name).desc()

    tag_results = (
        session.query(ArtworkTag.name, ArtworkTag.translated_name, func.count(ArtworkTag.name))
        .group_by(ArtworkTag.name)
        .order_by(order)
        .limit(25)
        .offset(after)
        .all()
    )

    cards = []
    for (name, translated_name, count) in tag_results:
        random_artwork = (
            session.query(Artwork)
            .join(ArtworkTag)
            .filter(ArtworkTag.name == name)
            .order_by(func.random())
            .limit(1)
            .first()
        )

        artwork_card = ArtworkCard.card_from_artwork(random_artwork)
        card = TagCard(
            name=name, artwork=artwork_card, count=count, translated_name=translated_name
        )
        cards.append(card)

    return cards, total


def query_tags_named(name: str, session: Session, after: int, sort_mode: SortMode):
    """
    Implements the tag named querier.
    """
    query: Query = session.query(ArtworkTag).filter(ArtworkTag.name == name)

    if sort_mode == SortMode.ASCENDING:
        query = query.order_by(ArtworkTag.artwork_id.asc())
    else:
        query = query.order_by(ArtworkTag.artwork_id.desc())

    query = query.limit(25).offset(after)
    cards = map(lambda tag: ArtworkCard.card_from_artwork(tag.artwork), query.all())
    return cards


def query_tags_named_total(name: str, session: Session):
    """
    Implements the named tag total querier.
    """
    return session.query(ArtworkTag).filter(ArtworkTag.name == name).count()


def query_bookmark_grid(
    type_: str, session: Session, after: int, sort_mode: SortMode
) -> List[ArtworkCard]:
    """
    Implements bookmark grid querying.
    """
    query: Query = session.query(Bookmark).filter(Bookmark.type == type_)

    if sort_mode == SortMode.ASCENDING:
        query = query.order_by(Bookmark.artwork_id.asc())
    else:
        query = query.order_by(Bookmark.artwork_id.desc())

    query = query.limit(25).offset(after)

    bookmarks = query.all()
    tiles = map(lambda bk: ArtworkCard.card_from_artwork(bk.artwork), bookmarks)

    return tiles


def query_bookmark_total(type_: str, session: Session):
    """
    Implements the total querying for a bookmark type.
    """
    return session.query(Bookmark).filter(Bookmark.type == type_).count()


def query_raw_grid(session: Session, after: int, sort_mode: SortMode):
    """
    Implements raw grid querying.
    """
    query: Query = session.query(Artwork)

    if sort_mode == SortMode.ASCENDING:
        query = query.order_by(Artwork.id.asc())
    else:
        query = query.order_by(Artwork.id.desc())

    query = query.limit(25).offset(after)

    artworks = query.all()
    tiles = map(ArtworkCard.card_from_artwork, artworks)
    return tiles


def query_raw_total(session: Session):
    """
    Implements raw total querying.
    """
    return session.query(Artwork).count()
