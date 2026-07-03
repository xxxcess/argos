"""Canonical Bible book catalog for Venture Bible Quest Sources."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BibleBook:
    book_id: str
    name: str
    chapters: int
    testament: str
    canonical_order: int
    aliases: tuple[str, ...] = ()


OLD_TESTAMENT_BOOKS = [
    ("GEN", "Genesis", 50), ("EXO", "Exodus", 40), ("LEV", "Leviticus", 27), ("NUM", "Numbers", 36),
    ("DEU", "Deuteronomy", 34), ("JOS", "Joshua", 24), ("JDG", "Judges", 21), ("RUT", "Ruth", 4),
    ("1SA", "1 Samuel", 31), ("2SA", "2 Samuel", 24), ("1KI", "1 Kings", 22), ("2KI", "2 Kings", 25),
    ("1CH", "1 Chronicles", 29), ("2CH", "2 Chronicles", 36), ("EZR", "Ezra", 10), ("NEH", "Nehemiah", 13),
    ("EST", "Esther", 10), ("JOB", "Job", 42), ("PSA", "Psalms", 150), ("PRO", "Proverbs", 31),
    ("ECC", "Ecclesiastes", 12), ("SNG", "Song of Solomon", 8), ("ISA", "Isaiah", 66), ("JER", "Jeremiah", 52),
    ("LAM", "Lamentations", 5), ("EZK", "Ezekiel", 48), ("DAN", "Daniel", 12), ("HOS", "Hosea", 14),
    ("JOL", "Joel", 3), ("AMO", "Amos", 9), ("OBA", "Obadiah", 1), ("JON", "Jonah", 4),
    ("MIC", "Micah", 7), ("NAM", "Nahum", 3), ("HAB", "Habakkuk", 3), ("ZEP", "Zephaniah", 3),
    ("HAG", "Haggai", 2), ("ZEC", "Zechariah", 14), ("MAL", "Malachi", 4),
]

NEW_TESTAMENT_BOOKS = [
    ("MAT", "Matthew", 28), ("MRK", "Mark", 16), ("LUK", "Luke", 24), ("JHN", "John", 21),
    ("ACT", "Acts", 28), ("ROM", "Romans", 16), ("1CO", "1 Corinthians", 16), ("2CO", "2 Corinthians", 13),
    ("GAL", "Galatians", 6), ("EPH", "Ephesians", 6), ("PHP", "Philippians", 4), ("COL", "Colossians", 4),
    ("1TH", "1 Thessalonians", 5), ("2TH", "2 Thessalonians", 3), ("1TI", "1 Timothy", 6), ("2TI", "2 Timothy", 4),
    ("TIT", "Titus", 3), ("PHM", "Philemon", 1), ("HEB", "Hebrews", 13), ("JAS", "James", 5),
    ("1PE", "1 Peter", 5), ("2PE", "2 Peter", 3), ("1JN", "1 John", 5), ("2JN", "2 John", 1),
    ("3JN", "3 John", 1), ("JUD", "Jude", 1), ("REV", "Revelation", 22),
]

ALIASES = {
    "PSA": ("Psalm", "Psalms", "Ps"),
    "SNG": ("Song of Songs", "Song of Solomon"),
    "JHN": ("John", "Jn"),
    "MAT": ("Matthew", "Matt", "Mt"),
    "MRK": ("Mark", "Mk"),
    "LUK": ("Luke", "Lk"),
    "ROM": ("Romans", "Rom"),
    "REV": ("Revelation", "Rev"),
    "JUD": ("Jude",),
    "1JN": ("1 John", "First John", "I John"),
    "2JN": ("2 John", "Second John", "II John"),
    "3JN": ("3 John", "Third John", "III John"),
    "1CO": ("1 Corinthians", "First Corinthians", "I Corinthians"),
    "2CO": ("2 Corinthians", "Second Corinthians", "II Corinthians"),
}


def _norm(value: str) -> str:
    return "".join(ch for ch in value.lower().replace("&", "and") if ch.isalnum())


ALL_BOOKS: tuple[BibleBook, ...] = tuple(
    [BibleBook(bid, name, chapters, "old", idx + 1, ALIASES.get(bid, (name,))) for idx, (bid, name, chapters) in enumerate(OLD_TESTAMENT_BOOKS)]
    + [BibleBook(bid, name, chapters, "new", idx + 40, ALIASES.get(bid, (name,))) for idx, (bid, name, chapters) in enumerate(NEW_TESTAMENT_BOOKS)]
)
BOOKS_BY_ID = {book.book_id: book for book in ALL_BOOKS}
ALIASES_BY_KEY = {_norm(book.book_id): book.book_id for book in ALL_BOOKS}
for book in ALL_BOOKS:
    ALIASES_BY_KEY[_norm(book.name)] = book.book_id
    for alias in book.aliases:
        ALIASES_BY_KEY[_norm(alias)] = book.book_id


def books_for_testament(testament: str) -> list[BibleBook]:
    t = str(testament or "").lower()
    if t not in {"old", "new"}:
        raise ValueError("bible_scope_invalid")
    return [book for book in ALL_BOOKS if book.testament == t]


def get_book(book_id: str) -> BibleBook:
    try:
        return BOOKS_BY_ID[str(book_id or "").upper()]
    except KeyError as exc:
        raise ValueError("bible_scope_invalid") from exc


def resolve_book_alias(value: str) -> BibleBook | None:
    bid = ALIASES_BY_KEY.get(_norm(value or ""))
    return BOOKS_BY_ID.get(bid) if bid else None


def validate_book_in_testament(book_id: str, testament: str) -> BibleBook:
    book = get_book(book_id)
    if book.testament != str(testament or "").lower():
        raise ValueError("bible_scope_invalid")
    return book


def catalog_payload(testament: str) -> dict:
    books = books_for_testament(testament)
    recommended_ids = ["GEN", "EXO", "PSA", "ISA"] if testament == "old" else ["MAT", "JHN", "ROM", "REV"]
    return {
        "testament": testament,
        "book_count": len(books),
        "books": [book.__dict__ | {"aliases": list(book.aliases)} for book in books],
        "recommended_books": [BOOKS_BY_ID[bid].__dict__ | {"aliases": list(BOOKS_BY_ID[bid].aliases)} for bid in recommended_ids],
    }
