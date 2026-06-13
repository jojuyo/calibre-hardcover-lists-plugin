import json
from dataclasses import dataclass
from urllib import error, parse, request

from calibre.ebooks.metadata import check_isbn
from calibre_plugins.hardcover_list.hcl_graphql.client import GraphQLClient

from calibre_plugins.hardcover_list._version import __version__
from calibre_plugins.hardcover_list.config import get_api_key

API_URL = "https://api.hardcover.app/v1/graphql"

LOOKUP_BY_ISBN = """
query LookupEditionByIsbn($isbn: String!) {
  editions(
    where: {
      _or: [
        {isbn_13: {_eq: $isbn}},
        {isbn_10: {_eq: $isbn}}
      ]
    }
    order_by: {users_count: desc_nulls_last}
    limit: 3
  ) {
    title
    isbn_10
    isbn_13
    edition_format
    reading_format {
      format
    }
    cached_contributors
    book {
      title
    }
  }
}
"""


@dataclass(frozen=True)
class IsbnLookupResult:
    title: str
    authors: str
    format_type: str
    source: str
    work_title: str | None = None
    isbn_10: str | None = None
    isbn_13: str | None = None


def isbn10_to_isbn13(isbn10: str) -> str | None:
    normalized = check_isbn(isbn10)
    if not normalized or len(normalized) != 10:
        return None
    body = f"978{normalized[:9]}"
    total = sum((1 if index % 2 == 0 else 3) * int(digit) for index, digit in enumerate(body))
    check_digit = (10 - (total % 10)) % 10
    candidate = body + str(check_digit)
    return check_isbn(candidate)


def _normalized_isbn(value: str | None) -> str | None:
    if not value:
        return None
    return check_isbn(str(value))


def _format_label(edition_format: str | None, reading_format: str | None) -> str:
    if edition_format:
        return edition_format.replace("_", " ").title()
    if reading_format:
        return reading_format.replace("_", " ").title()
    return _("Unknown")


def _authors_from_contributors(contributors) -> str:
    if not contributors:
        return _("Unknown")
    names = []
    for entry in contributors:
        author = entry.get("author") or {}
        name = author.get("name")
        if name:
            names.append(name)
    return ", ".join(names) if names else _("Unknown")


def _lookup_hardcover(isbn: str, api_key: str) -> list[IsbnLookupResult]:
    useragent = f"hardcover-list-calibre-plugin/{__version__}"
    client = GraphQLClient(API_URL, useragent)
    client.set_token(api_key)
    data = client.execute(LOOKUP_BY_ISBN, {"isbn": isbn})
    editions = data.get("editions") or []
    results: list[IsbnLookupResult] = []
    for edition in editions:
        if not edition:
            continue
        book = edition.get("book") or {}
        reading_format = (edition.get("reading_format") or {}).get("format")
        isbn_10 = _normalized_isbn(edition.get("isbn_10"))
        isbn_13 = _normalized_isbn(edition.get("isbn_13"))
        if isbn_10 and not isbn_13:
            isbn_13 = isbn10_to_isbn13(isbn_10)
        results.append(
            IsbnLookupResult(
                title=edition.get("title") or book.get("title") or _("Unknown"),
                authors=_authors_from_contributors(edition.get("cached_contributors")),
                format_type=_format_label(
                    edition.get("edition_format"), reading_format
                ),
                source="Hardcover",
                work_title=book.get("title"),
                isbn_10=isbn_10,
                isbn_13=isbn_13,
            )
        )
    return results


def _lookup_open_library(isbn: str) -> IsbnLookupResult | None:
    url = (
        "https://openlibrary.org/api/books?"
        + parse.urlencode(
            {
                "bibkeys": f"ISBN:{isbn}",
                "format": "json",
                "jscmd": "data",
            }
        )
    )
    try:
        with request.urlopen(url, timeout=20) as response:
            payload = json.load(response)
    except error.URLError:
        return None

    entry = payload.get(f"ISBN:{isbn}")
    if not entry:
        return None

    authors = ", ".join(
        author.get("name", "")
        for author in entry.get("authors") or []
        if author.get("name")
    )
    normalized = _normalized_isbn(isbn)
    isbn_10 = normalized if normalized and len(normalized) == 10 else None
    isbn_13 = normalized if normalized and len(normalized) == 13 else None
    if isbn_10 and not isbn_13:
        isbn_13 = isbn10_to_isbn13(isbn_10)
    return IsbnLookupResult(
        title=entry.get("title") or _("Unknown"),
        authors=authors or _("Unknown"),
        format_type=_("Unknown"),
        source="Open Library",
        isbn_10=isbn_10,
        isbn_13=isbn_13,
    )


def lookup_isbn(isbn: str) -> list[IsbnLookupResult]:
    api_key = get_api_key()
    if api_key:
        results = _lookup_hardcover(isbn, api_key)
        if results:
            return results

    open_library = _lookup_open_library(isbn)
    if open_library:
        return [open_library]

    if not api_key:
        raise LookupError(
            _(
                "No match found. Configure a Hardcover API key in the "
                "Hardcover Lists or Hardcover metadata plugin for richer results."
            )
        )
    raise LookupError(_("No book found for ISBN {isbn}.").format(isbn=isbn))


def format_lookup_results(results: list[IsbnLookupResult], searched_isbn: str) -> str:
    searched = check_isbn(searched_isbn) or searched_isbn
    blocks = []
    for index, result in enumerate(results, start=1):
        lines = [
            _("Title: {title}").format(title=result.title),
            _("Authors: {authors}").format(authors=result.authors),
            _("Format: {format_type}").format(format_type=result.format_type),
        ]
        if len(searched) == 10:
            isbn_13 = result.isbn_13 or isbn10_to_isbn13(searched)
            if isbn_13 and isbn_13 != searched:
                lines.append(_("ISBN-13: {isbn}").format(isbn=isbn_13))
        if result.work_title and result.work_title != result.title:
            lines.insert(
                1,
                _("Work: {title}").format(title=result.work_title),
            )
        lines.append(_("Source: {source}").format(source=result.source))
        if len(results) > 1:
            blocks.append(_("Match {index}").format(index=index) + "\n" + "\n".join(lines))
        else:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
