from dataclasses import dataclass, field
from typing import Optional

from calibre_plugins.hardcover_list.hcl_graphql.client import GraphQLClient

from ._version import __version__
from .config import get_api_key
from .queries import (
    ALL_LIST_BOOKS,
    BOOK_ID_BY_EDITION,
    BOOK_ID_BY_SLUG,
    BOOKS_BY_EDITIONS,
    CURRENT_USER_ID,
    DELETE_LIST_BOOK,
    INSERT_LIST,
    INSERT_LIST_BOOK,
    LIST_BOOK_ENTRIES,
    LIST_BOOK_ENTRY,
    LIST_MEMBERSHIP_BY_ID,
    USER_LISTS,
)

# Page size for streaming all list_books. The whole library typically fits in
# one or two pages, so the entire membership map costs only a few requests.
LIST_BOOKS_PAGE_SIZE = 1000
EDITION_RESOLVE_CHUNK = 500

API_URL = "https://api.hardcover.app/v1/graphql"
NO_API_KEY = "Configure Hardcover API key"
NO_IDENTIFIER = "No Hardcover identifier"
NOT_ON_LISTS = "Not on any lists"
LOADING_TEXT = "Loading..."
SPECIAL_COLUMN_VALUES = frozenset(
    {NOT_ON_LISTS, NO_IDENTIFIER, NO_API_KEY, LOADING_TEXT}
)


@dataclass
class ListMembershipSnapshot:
    by_id: dict[int, set[str]] = field(default_factory=dict)
    by_slug: dict[str, set[str]] = field(default_factory=dict)

    def lists_text(self, book_id: int | None, slug: str | None) -> str:
        names: set[str] = set()
        if book_id is not None:
            names |= self.by_id.get(book_id, set())
        if slug:
            names |= self.by_slug.get(slug, set())
        if not names:
            return NOT_ON_LISTS
        return ", ".join(sorted(names))


def normalize_lists_display(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(part) for part in value if part)
    return str(value)


def lists_text_to_field_value(text: str):
    if not text:
        return text
    if text in SPECIAL_COLUMN_VALUES or text.startswith("Hardcover error:"):
        return text
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        return text
    return parts if len(parts) > 1 else parts[0]


def column_values_equal(current, new) -> bool:
    def as_key(val):
        if val is None or val == "":
            return ()
        if isinstance(val, (list, tuple)):
            items = [str(part) for part in val if part]
        else:
            text = str(val)
            if text in SPECIAL_COLUMN_VALUES or text.startswith("Hardcover error:"):
                return (text,)
            items = [part.strip() for part in text.split(",") if part.strip()]
        return tuple(sorted(items))

    return as_key(current) == as_key(new)


def is_stale_lists_column_value(value) -> bool:
    text = normalize_lists_display(value)
    if not text or text == LOADING_TEXT:
        return True
    if text in {NO_IDENTIFIER, NO_API_KEY}:
        return True
    return text.startswith("Hardcover error:")


def _parse_positive_int(value) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def get_hardcover_edition_id(identifiers: dict) -> int | None:
    edition_id = _parse_positive_int(identifiers.get("hardcover-edition"))
    if edition_id is not None:
        return edition_id
    legacy = identifiers.get("hardcover")
    if legacy is not None and str(legacy).isdigit():
        return int(legacy)
    return None


def get_hardcover_lookup(
    identifiers: dict,
) -> tuple[int | None, str | None, int | None]:
    """Return (book_id, slug, edition_id) usable to match a book against lists."""
    book_id = _parse_positive_int(identifiers.get("hardcover-id"))
    slug = None
    for key in ("hardcover-slug", "hardcover"):
        value = identifiers.get(key)
        if value and not str(value).isdigit():
            slug = str(value)
            break
    edition_id = get_hardcover_edition_id(identifiers)
    return book_id, slug, edition_id


def get_hardcover_book_ref(identifiers: dict) -> tuple[str, str | int] | None:
    book_id = _parse_positive_int(identifiers.get("hardcover-id"))
    if book_id is not None:
        return ("id", book_id)

    for key in ("hardcover-slug", "hardcover"):
        value = identifiers.get(key)
        if value and not str(value).isdigit():
            return ("slug", str(value))

    edition_id = get_hardcover_edition_id(identifiers)
    if edition_id is not None:
        return ("edition", edition_id)
    return None


def has_hardcover_link(identifiers: dict) -> bool:
    return get_hardcover_book_ref(identifiers) is not None


def format_list_names(lists: list[dict]) -> str:
    matching = [entry["name"] for entry in lists if entry.get("list_books")]
    if not matching:
        return NOT_ON_LISTS
    return ", ".join(matching)


def _me_from_result(result: dict | None) -> dict | None:
    if not result:
        return None
    me = result.get("me")
    if isinstance(me, list):
        me = me[0] if me else None
    return me


class HardcoverListsClient:
    def __init__(self, api_key: Optional[str] = None):
        useragent = f"hardcover-list-calibre-plugin/{__version__}"
        self.client = GraphQLClient(API_URL, useragent)
        self.client.set_token(api_key or get_api_key())

    def lists_for_book(self, identifiers: dict, timeout=30) -> tuple[str, int | None]:
        if not self.client.token:
            return NO_API_KEY, None

        book_id = self.resolve_book_id(identifiers, timeout)
        if book_id is None:
            return NO_IDENTIFIER, None

        result = self.client.execute(
            LIST_MEMBERSHIP_BY_ID, {"book_id": book_id}, timeout
        )
        me = _me_from_result(result)
        lists = (me or {}).get("lists") or []
        return format_list_names(lists), book_id

    def fetch_user_lists(self, timeout=30) -> list[dict]:
        if not self.client.token:
            return []

        result = self.client.execute(USER_LISTS, {}, timeout)
        me = _me_from_result(result)
        return (me or {}).get("lists") or []

    def current_user_id(self, timeout=30) -> int | None:
        result = self.client.execute(CURRENT_USER_ID, {}, timeout)
        me = _me_from_result(result)
        user_id = (me or {}).get("id")
        return int(user_id) if user_id is not None else None

    def snapshot_list_memberships(self, timeout=30) -> "ListMembershipSnapshot":
        """Fetch every list_books entry for the user in a few paginated requests.

        Returns a snapshot mapping Hardcover book ids and slugs to the set of
        list names that contain them. This replaces per-book membership probes.
        """
        if not self.client.token:
            raise RuntimeError(NO_API_KEY)

        user_id = self.current_user_id(timeout)
        if user_id is None:
            raise RuntimeError("Could not determine Hardcover user id")

        by_id: dict[int, set[str]] = {}
        by_slug: dict[str, set[str]] = {}
        offset = 0
        while True:
            result = self.client.execute(
                ALL_LIST_BOOKS,
                {
                    "user_id": user_id,
                    "limit": LIST_BOOKS_PAGE_SIZE,
                    "offset": offset,
                },
                timeout,
            )
            rows = (result or {}).get("list_books") or []
            for row in rows:
                name = (row.get("list") or {}).get("name")
                if not name:
                    continue
                book_id = row.get("book_id")
                if book_id is not None:
                    by_id.setdefault(int(book_id), set()).add(name)
                slug = (row.get("book") or {}).get("slug")
                if slug:
                    by_slug.setdefault(slug, set()).add(name)
            if len(rows) < LIST_BOOKS_PAGE_SIZE:
                break
            offset += LIST_BOOKS_PAGE_SIZE

        return ListMembershipSnapshot(by_id=by_id, by_slug=by_slug)

    def resolve_editions(
        self, edition_ids, timeout=30
    ) -> dict[int, tuple[int, str | None]]:
        """Resolve edition ids to (book_id, slug) in batched requests."""
        resolved: dict[int, tuple[int, str | None]] = {}
        ids = [int(eid) for eid in edition_ids if eid is not None]
        for start in range(0, len(ids), EDITION_RESOLVE_CHUNK):
            chunk = ids[start : start + EDITION_RESOLVE_CHUNK]
            result = self.client.execute(BOOKS_BY_EDITIONS, {"ids": chunk}, timeout)
            for entry in (result or {}).get("editions") or []:
                book = entry.get("book") or {}
                book_id = book.get("id")
                if entry.get("id") is not None and book_id is not None:
                    resolved[int(entry["id"])] = (int(book_id), book.get("slug"))
        return resolved

    def resolve_book_id(self, identifiers: dict, timeout=30) -> int | None:
        book_id = _parse_positive_int(identifiers.get("hardcover-id"))
        if book_id is not None:
            return book_id

        ref = get_hardcover_book_ref(identifiers)
        if ref is None:
            return None
        ref_type, ref_value = ref
        if ref_type == "id":
            return ref_value
        if ref_type == "slug":
            result = self.client.execute(
                BOOK_ID_BY_SLUG, {"slug": ref_value}, timeout
            )
            books = (result or {}).get("books") or []
            if not books:
                return None
            return books[0]["id"]

        result = self.client.execute(
            BOOK_ID_BY_EDITION, {"edition_id": ref_value}, timeout
        )
        editions = (result or {}).get("editions") or []
        if not editions:
            return None
        book = editions[0].get("book") or {}
        return book.get("id")

    def find_list_book_id(
        self, list_id: int, book_id: int, timeout=30
    ) -> int | None:
        result = self.client.execute(
            LIST_BOOK_ENTRY,
            {"list_id": list_id, "book_id": book_id},
            timeout,
        )
        entries = (result or {}).get("list_books") or []
        if not entries:
            return None
        return entries[0]["id"]

    def add_book_to_list(
        self,
        book_id: int,
        list_id: int,
        edition_id: int | None = None,
        timeout=30,
    ) -> int:
        variables = {"list_id": list_id, "book_id": book_id, "edition_id": edition_id}
        result = self.client.execute(INSERT_LIST_BOOK, variables, timeout)
        entry = (result or {}).get("insert_list_book") or {}
        list_book_id = entry.get("id")
        if list_book_id is None:
            raise RuntimeError("Hardcover did not add the book to the list")
        return list_book_id

    def add_books_to_list(
        self,
        list_id: int,
        books: list[dict],
        timeout=30,
        chunk_size: int = 50,
    ) -> list[dict]:
        """Add many books to a list using batched GraphQL requests.

        Hardcover has no native bulk-insert mutation, but a single GraphQL
        request can contain many aliased ``insert_list_book`` fields, which the
        server executes serially. This collapses N inserts into a handful of
        HTTP requests (one per chunk) instead of one request per book.

        ``books`` is a list of dicts with at least ``book_id`` and an optional
        ``edition_id``. Returns a list (same order as input) of dicts:
        ``{"book": <input dict>, "list_book_id": int | None, "error": str | None}``.
        """
        results: list[dict] = []
        for start in range(0, len(books), chunk_size):
            chunk = books[start : start + chunk_size]
            var_defs: list[str] = []
            fields: list[str] = []
            variables: dict = {}
            for offset, book in enumerate(chunk):
                idx = start + offset
                var_defs.append(f"$list_{idx}: Int!")
                var_defs.append(f"$book_{idx}: Int!")
                var_defs.append(f"$edition_{idx}: Int")
                variables[f"list_{idx}"] = list_id
                variables[f"book_{idx}"] = book["book_id"]
                variables[f"edition_{idx}"] = book.get("edition_id")
                fields.append(
                    f"  b{idx}: insert_list_book("
                    f"object: {{list_id: $list_{idx}, book_id: $book_{idx}, "
                    f"edition_id: $edition_{idx}}}) {{ id }}"
                )
            query = (
                "mutation HardcoverBatchAddListBooks("
                + ", ".join(var_defs)
                + ") {\n"
                + "\n".join(fields)
                + "\n}"
            )

            try:
                data = self.client.execute(query, variables, timeout) or {}
                request_error = None
            except Exception as exc:  # noqa: BLE001 - reported per book below
                data = {}
                request_error = str(exc)

            for offset, book in enumerate(chunk):
                idx = start + offset
                entry = data.get(f"b{idx}") if request_error is None else None
                list_book_id = (entry or {}).get("id") if entry else None
                if list_book_id is not None:
                    book_error = None
                elif request_error is not None:
                    book_error = request_error
                else:
                    book_error = "Hardcover did not add the book to the list"
                results.append(
                    {
                        "book": book,
                        "list_book_id": list_book_id,
                        "error": book_error,
                    }
                )
        return results

    def remove_book_from_list(self, list_book_id: int, timeout=30) -> None:
        result = self.client.execute(
            DELETE_LIST_BOOK, {"id": list_book_id}, timeout
        )
        entry = (result or {}).get("delete_list_book") or {}
        if entry.get("id") is None:
            raise RuntimeError("Hardcover did not remove the book from the list")

    def _list_book_entries(
        self, list_id: int, book_ids: list[int], timeout=30
    ) -> dict[int, list[int]]:
        """Map each book_id to its list_book entry ids for the given list."""
        entries: dict[int, list[int]] = {}
        if not book_ids:
            return entries
        offset = 0
        while True:
            data = self.client.execute(
                LIST_BOOK_ENTRIES,
                {
                    "list_id": list_id,
                    "book_ids": book_ids,
                    "limit": LIST_BOOKS_PAGE_SIZE,
                    "offset": offset,
                },
                timeout,
            ) or {}
            rows = data.get("list_books") or []
            for row in rows:
                entries.setdefault(row["book_id"], []).append(row["id"])
            if len(rows) < LIST_BOOKS_PAGE_SIZE:
                break
            offset += LIST_BOOKS_PAGE_SIZE
        return entries

    def _delete_list_books(
        self, list_book_ids: list[int], timeout=30, chunk_size: int = 50
    ) -> dict[int, str | None]:
        """Delete many list_book entries via batched aliased mutations.

        Returns a map of list_book_id -> error string (None on success).
        """
        results: dict[int, str | None] = {}
        for start in range(0, len(list_book_ids), chunk_size):
            chunk = list_book_ids[start : start + chunk_size]
            var_defs: list[str] = []
            fields: list[str] = []
            variables: dict = {}
            for offset, lb_id in enumerate(chunk):
                idx = start + offset
                var_defs.append(f"$id_{idx}: Int!")
                variables[f"id_{idx}"] = lb_id
                fields.append(f"  d{idx}: delete_list_book(id: $id_{idx}) {{ id }}")
            query = (
                "mutation HardcoverBatchDeleteListBooks("
                + ", ".join(var_defs)
                + ") {\n"
                + "\n".join(fields)
                + "\n}"
            )
            try:
                data = self.client.execute(query, variables, timeout) or {}
                request_error = None
            except Exception as exc:  # noqa: BLE001 - reported per entry below
                data = {}
                request_error = str(exc)
            for offset, lb_id in enumerate(chunk):
                idx = start + offset
                entry = data.get(f"d{idx}") if request_error is None else None
                if entry and entry.get("id") is not None:
                    results[lb_id] = None
                elif request_error is not None:
                    results[lb_id] = request_error
                else:
                    results[lb_id] = "Hardcover did not remove the book from the list"
        return results

    def remove_books_from_list(
        self,
        list_id: int,
        books: list[dict],
        timeout=30,
        chunk_size: int = 50,
    ) -> list[dict]:
        """Remove many books from a list using batched GraphQL requests.

        ``books`` is a list of dicts with at least ``book_id``. Looks up all
        matching list_book entries in a single query, then deletes them with
        aliased batch mutations. Returns a list (input order) of dicts:
        ``{"book": <input dict>, "removed": int, "not_on_list": bool,
        "error": str | None}``.
        """
        entry_map = self._list_book_entries(
            list_id, [book["book_id"] for book in books], timeout
        )
        all_ids: list[int] = []
        for book in books:
            all_ids.extend(entry_map.get(book["book_id"], []))
        delete_results = self._delete_list_books(all_ids, timeout, chunk_size)

        results: list[dict] = []
        for book in books:
            ids = entry_map.get(book["book_id"], [])
            if not ids:
                results.append(
                    {"book": book, "removed": 0, "not_on_list": True, "error": None}
                )
                continue
            removed = sum(1 for lb_id in ids if delete_results.get(lb_id) is None)
            errors = [delete_results[lb_id] for lb_id in ids if delete_results.get(lb_id)]
            results.append(
                {
                    "book": book,
                    "removed": removed,
                    "not_on_list": False,
                    "error": errors[0] if errors and removed == 0 else None,
                }
            )
        return results

    def create_list(self, name: str, timeout=30) -> dict:
        trimmed = name.strip()
        if not trimmed:
            raise ValueError("List name cannot be empty")

        result = self.client.execute(INSERT_LIST, {"name": trimmed}, timeout)
        entry = (result or {}).get("insert_list") or {}
        errors = entry.get("errors") or []
        if errors:
            raise RuntimeError("; ".join(str(error) for error in errors))

        list_id = entry.get("id")
        if list_id is None:
            raise RuntimeError("Hardcover did not create the list")

        list_data = entry.get("list") or {}
        return {
            "id": list_id,
            "name": list_data.get("name") or trimmed,
            "slug": list_data.get("slug"),
        }
