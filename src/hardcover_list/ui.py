from functools import partial
from threading import Thread

from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import QInputDialog, QMenu, QTimer, QToolButton, pyqtSignal

from calibre_plugins.hardcover_list.book_details import (
    COLUMN_KEY,
    _db,
    ensure_hardcover_lists_column,
)
from calibre_plugins.hardcover_list.lists import (
    HardcoverListsClient,
    LOADING_TEXT,
    NO_IDENTIFIER,
    NOT_ON_LISTS,
    SPECIAL_COLUMN_VALUES,
    column_values_equal,
    get_hardcover_edition_id,
    get_hardcover_lookup,
    has_hardcover_link,
    is_stale_lists_column_value,
    lists_text_to_field_value,
    normalize_lists_display,
)
from calibre_plugins.hardcover_list.lists_cache import (
    get_cached_lists,
    restore_lists_cache_to_column,
    save_lists_cache_entry,
)
from calibre_plugins.hardcover_list.config import ensure_plugin_prefs
from calibre_plugins.hardcover_list.menu_setup import ensure_context_menu_action
from calibre_plugins.hardcover_list.cull_isbn.isbn_select_dialog import (
    IsbnSelectDialog,
)

# Apply list results in small batches so multi-select updates show progress
# without waiting for the entire selection to finish.
FETCH_APPLY_SIZE = 3


class HardcoverListsAction(InterfaceAction):
    name = "Hardcover Lists"
    action_type = "current"
    popup_type = QToolButton.ToolButtonPopupMode.InstantPopup
    lists_batch_fetched = pyqtSignal(int, object)
    books_membership_changed = pyqtSignal(object)
    membership_delta = pyqtSignal(object, str, str)
    user_lists_loaded = pyqtSignal(object, object)
    list_operation_done = pyqtSignal(str, str)
    status_message = pyqtSignal(str, int)
    status_clear = pyqtSignal()
    isbns_found = pyqtSignal(int, int, object)
    action_spec = (
        "Hardcover Lists",
        None,
        _("Manage Hardcover lists for the selected book"),
        None,
    )
    dont_add_to = frozenset(
        [
            "menubar",
            "toolbar",
            "toolbar-child",
            "context-menu-device",
            "toolbar-device",
            "menubar-device",
        ]
    )

    def genesis(self):
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(300)
        self._update_timer.timeout.connect(self._update_lists_for_selection)
        self._fetch_counter = 0
        self._fetch_status = None
        self._manual_refresh = False
        self._suppress_selection_updates = 0
        self._isbn_scan_counter = 0
        self._lists_client = HardcoverListsClient()
        self._user_lists = []
        self._user_lists_loading = False
        self.lists_batch_fetched.connect(self._apply_lists_batch)
        self.books_membership_changed.connect(self._refresh_books_by_ids)
        self.membership_delta.connect(self._on_membership_delta)
        self.user_lists_loaded.connect(self._store_user_lists)
        self.list_operation_done.connect(self._show_operation_message)
        self.status_message.connect(self._on_status_message)
        self.status_clear.connect(self._on_status_clear)
        self.isbns_found.connect(self._on_isbns_found)

        self.menu = QMenu(self.gui)
        self.qaction.setMenu(self.menu)
        self._menu_book_ids: list[int] = []
        self.menu.aboutToShow.connect(self._on_menu_about_to_show)
        self.menu.aboutToShow.connect(self._populate_context_menu)

    def initialization_complete(self):
        ensure_plugin_prefs()
        ensure_context_menu_action(self.gui)
        QTimer.singleShot(0, self._setup_book_details)
        self.gui.library_view.selectionModel().selectionChanged.connect(
            self._schedule_list_update
        )
        self._schedule_list_update()
        self._refresh_user_lists()

    def gui_layout_complete(self):
        ensure_context_menu_action(self.gui)
        QTimer.singleShot(0, self._refresh_tag_browser)

    def library_changed(self, db):
        QTimer.singleShot(0, self._setup_book_details)
        self._schedule_list_update()
        self._refresh_user_lists()

    def location_selected(self, loc):
        self.qaction.setEnabled(loc == "library")

    def _setup_book_details(self):
        if not getattr(self.gui, "current_db", None):
            return
        try:
            changed = ensure_hardcover_lists_column(self.gui)
        except Exception:
            import traceback

            traceback.print_exc()
            return

        if changed:
            from calibre.gui2.dialogs.confirm_delete import confirm

            confirm(
                _(
                    "Hardcover Lists updated the custom column in your library. "
                    "Restart calibre if the field does not appear in book details."
                ),
                "hardcover_lists_column_created",
                parent=self.gui,
            )

        def restore_and_refresh():
            restored = restore_lists_cache_to_column(self.gui)
            if restored:
                self._refresh_books_in_ui(restored)
            else:
                self._refresh_tag_browser()

        self._run_without_selection_updates(restore_and_refresh)
        self._schedule_list_update()

    def _schedule_list_update(self, *args):
        if self._suppress_selection_updates:
            return
        self._update_timer.start()

    def _run_without_selection_updates(self, callback):
        self._suppress_selection_updates += 1
        try:
            callback()
        finally:
            self._suppress_selection_updates -= 1

    def _on_status_message(self, message: str, timeout: int = 0):
        if not hasattr(self.gui, "status_bar"):
            return
        self.gui.status_bar.show_message(
            message, timeout=timeout, show_notification=False
        )

    def _on_status_clear(self):
        if not hasattr(self.gui, "status_bar"):
            return
        self.gui.status_bar.clear_message()

    def _set_status(self, message: str, timeout: int = 0):
        self.status_message.emit(message, timeout)

    def _clear_status(self):
        self.status_clear.emit()

    def _begin_fetch_status(self, fetch_id: int, total: int, *, manual: bool = False):
        self._fetch_status = {
            "fetch_id": fetch_id,
            "total": total,
            "completed": 0,
            "errors": 0,
            "changed": 0,
            "manual": manual,
        }
        self._set_status(
            _("Hardcover Lists: looking up 0/{total}…").format(total=total)
        )

    def _update_fetch_status(
        self, fetch_id: int, results: dict, *, changed: int = 0
    ):
        status = self._fetch_status
        if not status or status["fetch_id"] != fetch_id:
            return

        status["completed"] += len(results)
        status["errors"] += sum(
            1
            for value in results.values()
            if str(value).startswith("Hardcover error:")
        )
        status["changed"] += changed

        total = status["total"]
        done = status["completed"]
        if done >= total:
            errors = status["errors"]
            changed = status["changed"]
            manual = status.get("manual", False)
            if errors:
                message = _(
                    "Hardcover Lists: finished {done}/{total} ({errors} errors)"
                ).format(done=done, total=total, errors=errors)
            elif manual and changed:
                message = _(
                    "Hardcover Lists: updated {changed} of {done} books"
                ).format(changed=changed, done=done)
            else:
                message = _("Hardcover Lists: finished {done} books").format(
                    done=done
                )
            self._set_status(message, timeout=5000)
            self._fetch_status = None
            self._manual_refresh = False
        else:
            self._set_status(
                _("Hardcover Lists: looking up {done}/{total}…").format(
                    done=done, total=total
                )
            )

    def _on_menu_about_to_show(self):
        self._menu_book_ids = list(self.gui.library_view.get_selected_ids())

    def _schedule_refresh_selected_books(self):
        QTimer.singleShot(0, self._refresh_selected_books_membership)

    def _selected_book_ids(self):
        return list(self.gui.library_view.get_selected_ids())

    def _selected_book_id(self):
        book_ids = self._selected_book_ids()
        if not book_ids:
            return None
        return book_ids[-1]

    def _books_with_hardcover_ids(self, db, book_ids):
        books = []
        for book_id in book_ids:
            identifiers = dict(db.field_for("identifiers", book_id))
            if not has_hardcover_link(identifiers):
                continue
            books.append((book_id, identifiers))
        return books

    def _partition_by_hardcover_link(self, db, book_ids):
        with_link = []
        without_link = []
        for book_id in book_ids:
            identifiers = dict(db.field_for("identifiers", book_id))
            if has_hardcover_link(identifiers):
                with_link.append((book_id, identifiers))
            else:
                without_link.append(book_id)
        return with_link, without_link

    def _mark_books_without_identifier(self, db, book_ids):
        """Label books that have no Hardcover identifier so they form a list."""
        value = lists_text_to_field_value(NO_IDENTIFIER)
        updates = {}
        for book_id in book_ids:
            current = db.field_for(COLUMN_KEY, book_id)
            if not column_values_equal(current, value):
                updates[book_id] = value
        if not updates:
            return []

        def apply_no_identifier():
            db.set_field(COLUMN_KEY, updates)
            self._refresh_books_in_ui(list(updates))

        self._run_without_selection_updates(apply_no_identifier)
        return list(updates)

    def _book_needs_api_refresh(self, db, book_id, identifiers) -> bool:
        current = db.field_for(COLUMN_KEY, book_id)
        if not is_stale_lists_column_value(current):
            return False
        return get_cached_lists(identifiers) is None

    def _apply_cache_to_stale_books(self, db, books) -> list[int]:
        updates = {}
        for book_id, identifiers in books:
            if not is_stale_lists_column_value(db.field_for(COLUMN_KEY, book_id)):
                continue
            cached = get_cached_lists(identifiers)
            if not cached:
                continue
            updates[book_id] = lists_text_to_field_value(cached)

        if not updates:
            return []

        def apply_cache():
            db.set_field(COLUMN_KEY, updates)
            self._refresh_books_in_ui(list(updates))

        self._run_without_selection_updates(apply_cache)
        return list(updates)

    def _update_lists_for_selection(self):
        if self._manual_refresh or self._fetch_status is not None:
            return
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        if COLUMN_KEY not in db.field_metadata:
            return

        book_ids = self._selected_book_ids()
        if not book_ids:
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        self._mark_books_without_identifier(db, without_link)
        if not books:
            return

        restored_ids = self._apply_cache_to_stale_books(db, books)

        books_to_fetch = [
            (book_id, identifiers)
            for book_id, identifiers in books
            if self._book_needs_api_refresh(db, book_id, identifiers)
        ]
        if not books_to_fetch:
            if restored_ids:
                self._set_status(
                    _("Hardcover Lists: restored {count} from cache").format(
                        count=len(restored_ids)
                    ),
                    timeout=3000,
                )
            return

        self._queue_books_for_fetch(books_to_fetch, show_loading=True)

    def _queue_books_for_fetch(
        self, books, *, show_loading=True, manual=False, snapshot=False
    ):
        if not books:
            return

        self._fetch_counter += 1
        fetch_id = self._fetch_counter
        if manual:
            self._manual_refresh = True
        self._begin_fetch_status(fetch_id, len(books), manual=manual)

        db = _db(self.gui)
        if show_loading:
            loading = {}
            for book_id, identifiers in books:
                current = db.field_for(COLUMN_KEY, book_id)
                if manual or (
                    is_stale_lists_column_value(current)
                    and get_cached_lists(identifiers) is None
                ):
                    loading[book_id] = LOADING_TEXT
            if loading:

                def apply_loading():
                    db.set_field(
                        COLUMN_KEY,
                        {
                            book_id: lists_text_to_field_value(LOADING_TEXT)
                            for book_id in loading
                        },
                    )
                    self._refresh_books_in_ui(list(loading))

                self._run_without_selection_updates(apply_loading)

        worker = self._fetch_lists_snapshot if snapshot else self._fetch_lists_batch
        Thread(
            target=worker,
            args=(books, fetch_id),
            daemon=True,
        ).start()

    def _fetch_lists_batch(self, books, fetch_id):
        batch = {}
        for book_id, identifiers in books:
            if fetch_id != self._fetch_counter:
                if batch:
                    self.lists_batch_fetched.emit(fetch_id, dict(batch))
                return
            try:
                lists_text, resolved_id = self._lists_client.lists_for_book(
                    identifiers
                )
                batch[book_id] = (lists_text, resolved_id)
            except Exception as exc:
                batch[book_id] = (f"Hardcover error: {exc}", None)

            if len(batch) >= FETCH_APPLY_SIZE:
                self.lists_batch_fetched.emit(fetch_id, dict(batch))
                batch.clear()

        if batch:
            self.lists_batch_fetched.emit(fetch_id, batch)

    def _fetch_lists_snapshot(self, books, fetch_id):
        """Resolve membership for many books from a single bulk snapshot.

        Fetches all of the user's list entries in a few requests, batch-resolves
        any edition-only books, then maps every selected book locally instead of
        making one request per book.
        """
        self._set_status(_("Hardcover Lists: fetching list memberships…"))
        try:
            snapshot = self._lists_client.snapshot_list_memberships()
        except Exception as exc:
            self._emit_snapshot_results(
                fetch_id, [(book_id, (f"Hardcover error: {exc}", None)) for book_id, _ in books]
            )
            return

        if fetch_id != self._fetch_counter:
            return

        resolved_books = []
        edition_ids = set()
        for book_id, identifiers in books:
            hc_id, slug, edition_id = get_hardcover_lookup(identifiers)
            if hc_id is None and slug is None and edition_id is not None:
                edition_ids.add(edition_id)
            resolved_books.append((book_id, hc_id, slug, edition_id))

        edition_map = {}
        if edition_ids:
            self._set_status(
                _("Hardcover Lists: resolving {count} editions…").format(
                    count=len(edition_ids)
                )
            )
            try:
                edition_map = self._lists_client.resolve_editions(edition_ids)
            except Exception:
                edition_map = {}

        if fetch_id != self._fetch_counter:
            return

        results = []
        for book_id, hc_id, slug, edition_id in resolved_books:
            resolved_id = hc_id
            resolved_slug = slug
            if resolved_id is None and edition_id is not None:
                mapped = edition_map.get(edition_id)
                if mapped:
                    resolved_id, mapped_slug = mapped
                    if not resolved_slug:
                        resolved_slug = mapped_slug
            text = snapshot.lists_text(resolved_id, resolved_slug)
            results.append((book_id, (text, resolved_id)))

        self._emit_snapshot_results(fetch_id, results)

    def _emit_snapshot_results(self, fetch_id, results):
        batch = {}
        for book_id, payload in results:
            batch[book_id] = payload
            if len(batch) >= 50:
                self.lists_batch_fetched.emit(fetch_id, dict(batch))
                batch.clear()
        if batch:
            self.lists_batch_fetched.emit(fetch_id, batch)

    def _apply_lists_batch(self, fetch_id: int, results: dict):
        if fetch_id != self._fetch_counter:
            return

        db = _db(self.gui)
        updates = {}
        status_results = {}
        changed = 0

        for book_id, payload in results.items():
            if isinstance(payload, tuple):
                lists_text, resolved_id = payload
            else:
                lists_text, resolved_id = payload, None
            status_results[book_id] = lists_text
            if not db.has_id(book_id):
                continue
            identifiers = dict(db.field_for("identifiers", book_id))
            save_lists_cache_entry(
                identifiers, lists_text, resolved_book_id=resolved_id
            )
            field_value = lists_text_to_field_value(lists_text)
            current = db.field_for(COLUMN_KEY, book_id)
            if not column_values_equal(current, field_value):
                updates[book_id] = field_value
                changed += 1

        self._update_fetch_status(fetch_id, status_results, changed=changed)

        if not updates:
            return

        def apply_updates():
            db.set_field(COLUMN_KEY, updates)
            self._refresh_books_in_ui(list(updates))
            if book_id := self._selected_book_id():
                if book_id in updates:
                    self._refresh_book_details()

        self._run_without_selection_updates(apply_updates)

    def _refresh_tag_browser(self):
        if not hasattr(self.gui, "tags_view"):
            return
        tags_view = self.gui.tags_view

        def refresh():
            if not getattr(self.gui, "current_db", None):
                return
            tags_view.recount()

        QTimer.singleShot(0, refresh)

    def _refresh_books_in_ui(self, book_ids):
        if not book_ids:
            return
        model = self.gui.library_view.model()
        model.refresh_ids(tuple(book_ids))
        self._refresh_tag_browser()

    def _refresh_books_by_ids(self, book_ids):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        books = self._books_with_hardcover_ids(db, book_ids)
        self._queue_books_for_fetch(books, show_loading=False)

    def _books_needing_forced_refresh(self, db, book_ids):
        return self._books_with_hardcover_ids(db, book_ids)

    def _refresh_selected_books_membership(self):
        if not getattr(self.gui, "current_db", None):
            return
        db = _db(self.gui)
        book_ids = list(self._menu_book_ids) or self._selected_book_ids()
        if not book_ids:
            info_dialog(
                self.gui,
                _("Hardcover Lists"),
                _(
                    "No books are selected. Select books in the library, "
                    "then choose Refresh selected books."
                ),
            ).exec()
            return

        books, without_link = self._partition_by_hardcover_link(db, book_ids)
        labeled = self._mark_books_without_identifier(db, without_link)

        if not books:
            self._set_status(
                _(
                    "Hardcover Lists: marked {count} books without a "
                    "Hardcover identifier"
                ).format(count=len(without_link)),
                timeout=5000,
            )
            return
        if labeled:
            self._set_status(
                _(
                    "Hardcover Lists: refreshing {count} books "
                    "({labeled} marked without Hardcover id)…"
                ).format(count=len(books), labeled=len(without_link))
            )
        self._queue_books_for_fetch(
            books, show_loading=True, manual=True, snapshot=True
        )

    def _refresh_book_details(self):
        current_index = self.gui.library_view.currentIndex()
        if current_index.isValid():
            self.gui.library_view.model().current_changed(
                current_index, current_index
            )

    def _refresh_user_lists(self):
        if self._user_lists_loading:
            return
        self._user_lists_loading = True
        self._set_status(_("Hardcover Lists: loading your lists…"))
        Thread(target=self._fetch_user_lists, daemon=True).start()

    def _fetch_user_lists(self):
        try:
            lists = self._lists_client.fetch_user_lists()
            error = None
        except Exception as exc:
            lists = None
            error = str(exc)
        self.user_lists_loaded.emit(lists, error)

    def _store_user_lists(self, lists, error):
        self._user_lists_loading = False
        if error is not None:
            self._user_lists = []
            self._set_status(
                _("Hardcover Lists: failed to load lists ({error})").format(
                    error=error
                ),
                timeout=5000,
            )
            return
        self._user_lists = lists or []
        if not self._fetch_status:
            self._set_status(
                _("Hardcover Lists: loaded {count} lists").format(
                    count=len(self._user_lists)
                ),
                timeout=3000,
            )

    def _populate_context_menu(self):
        self.menu.clear()
        book_ids = self.gui.library_view.get_selected_ids()
        if not book_ids:
            action = self.menu.addAction(_("No books selected"))
            action.setEnabled(False)
            return

        if not self._lists_client.client.token:
            action = self.menu.addAction(_("Configure Hardcover API key"))
            action.setEnabled(False)
            return

        if self._user_lists_loading and not self._user_lists:
            action = self.menu.addAction(_("Loading lists..."))
            action.setEnabled(False)
            self._refresh_user_lists()
            return

        if not self._user_lists:
            action = self.menu.addAction(_("No Hardcover lists found"))
            action.setEnabled(False)
            self._add_list_management_actions()
            return

        for user_list in self._user_lists:
            list_menu = self.menu.addMenu(user_list["name"])
            list_id = user_list["id"]
            self.create_menu_action(
                list_menu,
                f"add-{list_id}",
                _("Add to List"),
                triggered=partial(self._add_to_list, list_id, user_list),
                shortcut=False,
            )
            self.create_menu_action(
                list_menu,
                f"remove-{list_id}",
                _("Remove from List"),
                triggered=partial(self._remove_from_list, list_id, user_list),
                shortcut=False,
            )

        self.menu.addSeparator()
        self._add_list_management_actions()

    def _add_list_management_actions(self):
        create_action = self.menu.addAction(_("Create New List"))
        create_action.triggered.connect(self._create_new_list)
        refresh_action = self.menu.addAction(_("Refresh selected books"))
        refresh_action.triggered.connect(self._schedule_refresh_selected_books)
        self.menu.addSeparator()
        cull_action = self.menu.addAction(_("Cull ISBN"))
        cull_action.triggered.connect(self._start_cull_isbn)

    def _start_cull_isbn(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                _("Cull ISBN"),
                _("Select a book to search for ISBNs."),
            ).exec()
            return
        if len(book_ids) != 1:
            error_dialog(
                self.gui,
                _("Cull ISBN"),
                _("Select exactly one book to search for ISBNs."),
            ).exec()
            return

        if not getattr(self.gui, "current_db", None):
            return

        book_id = book_ids[0]
        title = self.gui.current_db.new_api.field_for("title", book_id)
        self._isbn_scan_counter += 1
        scan_id = self._isbn_scan_counter

        if hasattr(self.gui, "status_bar"):
            self.gui.status_bar.show_message(
                _("Cull ISBN: scanning “{title}”…").format(title=title),
                show_notification=False,
            )

        Thread(
            target=self._scan_book_for_isbns,
            args=(scan_id, book_id),
            daemon=True,
        ).start()

    def _scan_book_for_isbns(self, scan_id: int, book_id: int):
        from calibre_plugins.hardcover_list.cull_isbn.isbn_scan import (
            find_isbns_for_book,
        )

        try:
            isbns = find_isbns_for_book(self.gui.current_db.new_api, book_id)
        except Exception as exc:
            isbns = None
            error = str(exc)
        else:
            error = None

        self.isbns_found.emit(scan_id, book_id, (isbns, error))

    def _on_isbns_found(self, scan_id: int, book_id: int, payload):
        if scan_id != self._isbn_scan_counter:
            return

        isbns, error = payload
        if hasattr(self.gui, "status_bar"):
            self.gui.status_bar.clear_message()

        if error is not None:
            error_dialog(
                self.gui,
                _("Cull ISBN"),
                _("Failed to scan the book for ISBNs: {error}").format(error=error),
            ).exec()
            return

        if not isbns:
            info_dialog(
                self.gui,
                _("Cull ISBN"),
                _("No ISBN-10 or ISBN-13 numbers were found in the book text."),
            ).exec()
            return

        dialog = IsbnSelectDialog(
            self.gui,
            isbns,
            _("Select an ISBN to save on this book:"),
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        isbn = dialog.selected_isbn()
        if not isbn:
            return
        db = self.gui.current_db.new_api
        identifiers = dict(db.field_for("identifiers", book_id))
        identifiers["isbn"] = isbn
        db.set_field("identifiers", {book_id: identifiers})

        title = db.field_for("title", book_id)
        info_dialog(
            self.gui,
            _("Cull ISBN"),
            _('Saved ISBN {isbn} on “{title}”.').format(isbn=isbn, title=title),
        ).exec()

    def _create_new_list(self):
        name, ok = QInputDialog.getText(
            self.gui,
            _("Create Hardcover List"),
            _("List name:"),
        )
        if not ok:
            return

        name = name.strip()
        if not name:
            error_dialog(
                self.gui,
                _("Hardcover Lists"),
                _("List name cannot be empty."),
            ).exec()
            return

        Thread(target=self._do_create_list, args=(name,), daemon=True).start()

    def _do_create_list(self, name: str):
        try:
            new_list = self._lists_client.create_list(name)
        except Exception as exc:
            self.list_operation_done.emit(
                _("Hardcover Lists"),
                _("Failed to create list: {error}").format(error=exc),
            )
            return

        self.list_operation_done.emit(
            _("Hardcover Lists"),
            _('Created list "{name}".').format(name=new_list["name"]),
        )
        self._refresh_user_lists()

    def _selected_hardcover_books(self):
        db = _db(self.gui)
        book_ids = self.gui.library_view.get_selected_ids()
        books = []
        skipped = 0
        for book_id in book_ids:
            identifiers = dict(db.field_for("identifiers", book_id))
            hardcover_id = self._lists_client.resolve_book_id(identifiers)
            if hardcover_id is None:
                skipped += 1
                continue
            books.append(
                {
                    "book_id": book_id,
                    "hardcover_id": hardcover_id,
                    "edition_id": get_hardcover_edition_id(identifiers),
                    "title": db.field_for("title", book_id),
                }
            )
        return books, skipped

    def _add_to_list(self, list_id: int, user_list: dict):
        books, skipped = self._selected_hardcover_books()
        if not books:
            error_dialog(
                self.gui,
                _("Hardcover Lists"),
                _(
                    "None of the selected books have a Hardcover identifier. "
                    "Download metadata from Hardcover first."
                ),
            ).exec()
            return

        list_name = user_list["name"]
        Thread(
            target=self._do_add_to_list,
            args=(list_id, list_name, books, skipped),
            daemon=True,
        ).start()

    def _remove_from_list(self, list_id: int, user_list: dict):
        books, skipped = self._selected_hardcover_books()
        if not books:
            error_dialog(
                self.gui,
                _("Hardcover Lists"),
                _(
                    "None of the selected books have a Hardcover identifier. "
                    "Download metadata from Hardcover first."
                ),
            ).exec()
            return

        list_name = user_list["name"]
        Thread(
            target=self._do_remove_from_list,
            args=(list_id, list_name, books, skipped),
            daemon=True,
        ).start()

    def _do_add_to_list(
        self, list_id: int, list_name: str, books: list[dict], skipped: int
    ):
        payload = [
            {
                "book_id": book["hardcover_id"],
                "edition_id": book["edition_id"],
                "_book": book,
            }
            for book in books
        ]
        results = self._lists_client.add_books_to_list(list_id, payload)

        added = 0
        errors = []
        updated_ids = []
        for result in results:
            book = result["book"]["_book"]
            if result["error"] is None:
                added += 1
                updated_ids.append(book["book_id"])
            else:
                errors.append(f'{book["title"]}: {result["error"]}')

        if added == 1:
            message = _("Added 1 book to {list_name}.").format(list_name=list_name)
        else:
            message = _("Added {count} books to {list_name}.").format(
                count=added, list_name=list_name
            )
        if skipped == 1:
            message += " " + _("Skipped 1 book without a Hardcover identifier.")
        elif skipped:
            message += " " + _(
                "Skipped {count} books without a Hardcover identifier."
            ).format(count=skipped)
        if errors:
            message += "\n\n" + "\n".join(errors)
        self.list_operation_done.emit(_("Hardcover Lists"), message)
        if updated_ids:
            self.membership_delta.emit(updated_ids, list_name, "")

    def _do_remove_from_list(
        self, list_id: int, list_name: str, books: list[dict], skipped: int
    ):
        payload = [
            {"book_id": book["hardcover_id"], "_book": book} for book in books
        ]
        results = self._lists_client.remove_books_from_list(list_id, payload)

        removed = 0
        not_on_list = 0
        errors = []
        updated_ids = []
        for result in results:
            book = result["book"]["_book"]
            if result["error"]:
                errors.append(f'{book["title"]}: {result["error"]}')
            elif result["not_on_list"]:
                not_on_list += 1
            elif result["removed"] > 0:
                removed += 1
                updated_ids.append(book["book_id"])

        if removed == 1:
            message = _("Removed 1 book from {list_name}.").format(list_name=list_name)
        else:
            message = _("Removed {count} books from {list_name}.").format(
                count=removed, list_name=list_name
            )
        if not_on_list == 1:
            message += " " + _("1 book was not on the list.")
        elif not_on_list:
            message += " " + _("{count} books were not on the list.").format(
                count=not_on_list
            )
        if skipped == 1:
            message += " " + _("Skipped 1 book without a Hardcover identifier.")
        elif skipped:
            message += " " + _(
                "Skipped {count} books without a Hardcover identifier."
            ).format(count=skipped)
        if errors:
            message += "\n\n" + "\n".join(errors)
        self.list_operation_done.emit(_("Hardcover Lists"), message)
        if updated_ids:
            self.membership_delta.emit(updated_ids, "", list_name)

    @staticmethod
    def _current_list_names(value) -> set[str]:
        text = normalize_lists_display(value).strip()
        if (
            not text
            or text in SPECIAL_COLUMN_VALUES
            or text.startswith("Hardcover error:")
        ):
            return set()
        return {part.strip() for part in text.split(",") if part.strip()}

    def _on_membership_delta(self, book_ids, add_name: str, remove_name: str):
        if not getattr(self.gui, "current_db", None) or not book_ids:
            return
        db = _db(self.gui)
        updates = {}
        for book_id in book_ids:
            if not db.has_id(book_id):
                continue
            current = db.field_for(COLUMN_KEY, book_id)
            names = self._current_list_names(current)
            if add_name:
                names.add(add_name)
            if remove_name:
                names.discard(remove_name)
            text = ", ".join(sorted(names)) if names else NOT_ON_LISTS
            value = lists_text_to_field_value(text)
            identifiers = dict(db.field_for("identifiers", book_id))
            resolved_id, _slug, _edition = get_hardcover_lookup(identifiers)
            save_lists_cache_entry(identifiers, text, resolved_book_id=resolved_id)
            if not column_values_equal(current, value):
                updates[book_id] = value
        if not updates:
            return

        def apply():
            db.set_field(COLUMN_KEY, updates)
            self._refresh_books_in_ui(list(updates))

        self._run_without_selection_updates(apply)

    def _show_operation_message(self, title: str, message: str):
        info_dialog(self.gui, title, message).exec()
