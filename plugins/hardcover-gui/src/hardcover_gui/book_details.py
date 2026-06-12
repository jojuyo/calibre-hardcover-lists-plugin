import json

COLUMN_LABEL = "hardcover_lists"
COLUMN_KEY = "#hardcover_lists"
COLUMN_NAME = "Hardcover Lists"
PLACEHOLDER = "Specific Lists TBD"
BOOK_DETAILS_PREF = "book_display_fields"


def ensure_hardcover_lists_column(gui) -> bool:
    """Ensure the Hardcover Lists field exists and is shown in book details."""
    db = gui.current_db.new_api
    created = False

    if COLUMN_KEY not in db.field_metadata:
        display = json.dumps(
            {
                "composite_template": PLACEHOLDER,
                "composite_sort": "text",
                "make_category": False,
                "contains_html": False,
                "use_decorations": False,
            }
        )
        db.create_custom_column(
            COLUMN_LABEL, COLUMN_NAME, "composite", False, display=display
        )
        created = True

    inserted = _ensure_field_in_book_details(db)
    if created or inserted:
        gui.library_view.model().reset()
    return created


def _ensure_field_in_book_details(db) -> bool:
    fieldlist = list(db.prefs[BOOK_DETAILS_PREF])
    if COLUMN_KEY in {field for field, _ in fieldlist}:
        return False

    insert_idx = 0
    for index, (field, _) in enumerate(fieldlist):
        if field == "authors":
            insert_idx = index + 1
            break
        insert_idx = index + 1

    fieldlist.insert(insert_idx, (COLUMN_KEY, True))
    db.prefs.set(BOOK_DETAILS_PREF, fieldlist)
    return True
