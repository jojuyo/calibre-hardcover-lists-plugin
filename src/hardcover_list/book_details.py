COLUMN_LABEL = "hardcover_lists"
COLUMN_KEY = "#hardcover_lists"
COLUMN_NAME = "Hardcover Lists"
BOOK_DETAILS_PREF = "book_display_fields"

TEXT_DISPLAY = {"description": ""}


def _db(gui):
    """Return the Calibre Cache API for library read/write operations."""
    current = gui.current_db
    if hasattr(current, "new_api"):
        return current.new_api
    return current


def _existing_custom_column(db, label):
    query = db.backend.execute(
        "SELECT label, datatype, is_multiple FROM custom_columns WHERE label=?",
        (label,),
    )
    row = next(iter(query), None)
    if row is None:
        return None
    return {"label": row[0], "datatype": row[1], "is_multiple": bool(row[2])}


def _delete_column(db, label):
    try:
        db.delete_custom_column(label=label)
    except KeyError:
        db.backend.delete_custom_column(label=label)


def _migrate_column_to_tags(db, gui) -> bool:
    from .lists import lists_text_to_field_value, normalize_lists_display

    saved = {}
    for book_id in db.all_book_ids():
        value = db.field_for(COLUMN_KEY, book_id)
        text = normalize_lists_display(value)
        if text:
            saved[book_id] = text

    _delete_column(db, COLUMN_LABEL)
    db.create_custom_column(
        COLUMN_LABEL, COLUMN_NAME, "text", True, display=TEXT_DISPLAY
    )

    updates = {
        book_id: lists_text_to_field_value(text)
        for book_id, text in saved.items()
    }
    if updates:
        db.set_field(COLUMN_KEY, updates)
    gui.library_view.model().reset()
    return True


def ensure_hardcover_lists_column(gui) -> bool:
    """Ensure the Hardcover Lists tags-like column exists and is shown in book details."""
    db = _db(gui)
    changed = False

    display = _existing_custom_column(db, "hardcover_lists_view")
    if display is not None:
        _delete_column(db, "hardcover_lists_view")
        changed = True

    existing = _existing_custom_column(db, COLUMN_LABEL)
    if existing is None:
        db.create_custom_column(
            COLUMN_LABEL, COLUMN_NAME, "text", True, display=TEXT_DISPLAY
        )
        changed = True
    elif existing["datatype"] != "text":
        _delete_column(db, COLUMN_LABEL)
        db.create_custom_column(
            COLUMN_LABEL, COLUMN_NAME, "text", True, display=TEXT_DISPLAY
        )
        changed = True
    elif not existing["is_multiple"]:
        changed = _migrate_column_to_tags(db, gui) or changed

    if _ensure_field_in_book_details(db):
        changed = True

    if changed:
        gui.library_view.model().reset()
    return changed


def _ensure_field_in_book_details(db) -> bool:
    fieldlist = list(db.pref(BOOK_DETAILS_PREF))
    keys = {field for field, _ in fieldlist}

    updated = False
    if "#hardcover_lists_view" in keys:
        fieldlist = [
            (field, show)
            for field, show in fieldlist
            if field != "#hardcover_lists_view"
        ]
        updated = True

    if COLUMN_KEY not in keys:
        insert_idx = 0
        for index, (field, _) in enumerate(fieldlist):
            if field == "authors":
                insert_idx = index + 1
                break
            insert_idx = index + 1
        fieldlist.insert(insert_idx, (COLUMN_KEY, True))
        updated = True

    if updated:
        db.set_pref(BOOK_DETAILS_PREF, fieldlist)
    return updated
