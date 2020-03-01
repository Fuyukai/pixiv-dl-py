"""
ORM table definitions.
"""
from contextlib import contextmanager
from typing import Callable, ContextManager

from sqlalchemy import (
    Column,
    Integer,
    Text,
    ForeignKey,
    Boolean,
    create_engine,
    DateTime,
    UniqueConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker, Session

#: Declarative base variable.
Base = declarative_base()


class DB(object):
    """
    Small DB wrapper.
    """

    def __init__(self, connection_url: str):
        self.engine = create_engine(connection_url, echo=True)
        self.sessionmaker: Callable[[], Session] = sessionmaker(
            bind=self.engine, autocommit=False, autoflush=False
        )

    @contextmanager
    def session(self) -> ContextManager[Session]:
        """
        Gets a session for the database.
        """
        session = self.sessionmaker()
        try:
            yield session
        except:
            session.rollback()
            raise
        else:
            session.commit()

    def migrate_database(self) -> None:
        """
        Creates all tables in the database.
        """

        Base.metadata.create_all(self.engine)


class Author(Base):
    """
    Basic author table.
    """

    __tablename__ = "author"

    id = Column(Integer(), primary_key=True, autoincrement=False)
    account_name = Column(Text(), unique=True, nullable=False, index=True)
    name = Column(Text(), unique=False, nullable=False, index=True)

    extended_data = relationship("ExtendedAuthorInfo", uselist=False, back_populates="author")

    artworks = relationship("Artwork", back_populates="author")


class ExtendedAuthorInfo(Base):
    """
    Extended author info, if available.
    """

    __tablename__ = "author_extended"

    id = Column(Integer(), primary_key=True, autoincrement=True)

    author_id = Column(Integer(), ForeignKey(Author.id), nullable=False, unique=True, index=True)
    author = relationship(Author, back_populates="extended_data")

    # all of these are nullable
    twitter_url = Column(Text(), nullable=True)
    comment = Column(Text(), nullable=True)


class ArtworkTag(Base):
    """
    Artwork tag info.
    """

    __tablename__ = "artwork_tag"

    id = Column(Integer(), primary_key=True, autoincrement=True)

    # all tags have a name
    name = Column(Text(), nullable=False, index=True)
    # ... but only some tags have a translation
    translated_name = Column(Text(), nullable=True, unique=False, index=True)

    artwork_id = Column(
        Integer(), ForeignKey("artwork.id"), nullable=False, unique=False, index=True
    )
    artwork = relationship("Artwork", back_populates="tags", lazy="joined")

    __table_args__ = (UniqueConstraint("name", "artwork_id"),)


class Artwork(Base):
    """
    Artwork info object.
    """

    __tablename__ = "artwork"

    id = Column(Integer(), primary_key=True, autoincrement=False)

    # key data
    title = Column(Text(), nullable=False, unique=False, index=True)
    caption = Column(Text(), nullable=True, unique=False, index=True)
    uploaded_at = Column(DateTime(), nullable=False, unique=False)

    # author data
    author_id = Column(Integer(), ForeignKey(Author.id), nullable=False, unique=False, index=True)
    author = relationship(Author, back_populates="artworks", lazy="joined")

    # nsfw data
    lewd_level = Column(Integer(), nullable=False, default=2)
    r18 = Column(Boolean(), nullable=False, default=False)
    r18g = Column(Boolean(), nullable=False, default=False)

    # stats
    bookmarks = Column(Integer(), nullable=False, default=0)
    views = Column(Integer(), nullable=False, default=0)

    # our info
    is_bookmarked = Column(Boolean(), nullable=False, default=False)

    # pages
    single_page = Column(Boolean(), nullable=False)
    page_count = Column(Integer(), nullable=False)

    # tag relationship
    tags = relationship(ArtworkTag, back_populates="artwork")

    # bookmark relationship
    bookmark = relationship("Bookmark", uselist=False, back_populates="artwork")


class Bookmark(Base):
    """
    Bookmark info object.
    """

    __tablename__ = "bookmark"

    id = Column(Integer(), primary_key=True, autoincrement=True)
    type = Column(Text(), unique=False)

    artwork_id = Column(Integer(), ForeignKey(Artwork.id), nullable=False, index=True)
    artwork = relationship(Artwork, back_populates="bookmark", lazy="joined")
