from src.bible_catalog import ALL_BOOKS, NEW_TESTAMENT_BOOKS, OLD_TESTAMENT_BOOKS, books_for_testament, resolve_book_alias
from services.bible_lookup import parse_bible_reference


def test_bible_catalog_counts_and_ids():
    assert len(OLD_TESTAMENT_BOOKS) == 39
    assert len(NEW_TESTAMENT_BOOKS) == 27
    assert len(ALL_BOOKS) == 66
    ids = [book.book_id for book in ALL_BOOKS]
    assert len(ids) == len(set(ids))
    assert all(book.chapters > 0 for book in ALL_BOOKS)


def test_bible_catalog_canonical_order_is_stable():
    assert [book.book_id for book in books_for_testament("old")[:5]] == ["GEN", "EXO", "LEV", "NUM", "DEU"]
    assert [book.book_id for book in books_for_testament("new")[:5]] == ["MAT", "MRK", "LUK", "JHN", "ACT"]
    assert books_for_testament("old")[-1].book_id == "MAL"
    assert books_for_testament("new")[-1].book_id == "REV"


def test_bible_aliases_resolve_common_forms():
    assert resolve_book_alias("Psalm").book_id == "PSA"
    assert resolve_book_alias("Song of Songs").book_id == "SNG"
    assert resolve_book_alias("Jn").book_id == "JHN"
    assert resolve_book_alias("Matt").book_id == "MAT"
    assert resolve_book_alias("I Corinthians").book_id == "1CO"
    assert resolve_book_alias("Second John").book_id == "2JN"
    assert resolve_book_alias("Rev").book_id == "REV"


def test_parse_bible_reference_supported_forms():
    assert parse_bible_reference("Genesis 1:1").book_id == "GEN"
    ref = parse_bible_reference("Genesis 1:1-5")
    assert (ref.book_id, ref.chapter, ref.start_verse, ref.end_verse) == ("GEN", 1, 1, 5)
    assert parse_bible_reference("Psalm 23").book_id == "PSA"
    assert parse_bible_reference("John 3:16").book_id == "JHN"
    assert parse_bible_reference("1 Corinthians 13").book_id == "1CO"
    assert parse_bible_reference("Jude 5").book_id == "JUD"
    assert parse_bible_reference("Revelation 21").book_id == "REV"
