from threading import Thread

from qt.core import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    pyqtSignal,
)

from calibre_plugins.hardcover_list.cull_isbn.isbn_scan import FoundIsbn


def _list_label(entry: FoundIsbn) -> str:
    kind = _("ISBN-13") if len(entry.isbn) == 13 else _("ISBN-10")
    if entry.is_correction:
        return _("{isbn} ({kind}, suggested correction)").format(
            isbn=entry.isbn, kind=kind
        )
    if entry.verified:
        return f"{entry.isbn} ({kind})"
    return _("{isbn} ({kind}, unverified)").format(isbn=entry.isbn, kind=kind)


class IsbnSelectDialog(QDialog):
    lookup_finished = pyqtSignal(object)

    def __init__(self, parent, found_isbns: list[FoundIsbn], prompt: str):
        super().__init__(parent)
        self._found_isbns = found_isbns
        self._lookup_thread = None
        self.setWindowTitle(_("Cull ISBN"))

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(prompt))

        self.list_widget = QListWidget(self)
        for entry in found_isbns:
            self.list_widget.addItem(_list_label(entry))
        if found_isbns:
            self.list_widget.setCurrentRow(0)
        self.list_widget.currentRowChanged.connect(self._clear_lookup_info)
        layout.addWidget(self.list_widget)

        self.info_panel = QTextEdit(self)
        self.info_panel.setReadOnly(True)
        self.info_panel.setPlaceholderText(
            _("Select an ISBN and click More info to look up title, author, and format.")
        )
        self.info_panel.setMinimumHeight(110)
        layout.addWidget(self.info_panel)

        action_row = QHBoxLayout()
        copy_button = QPushButton(_("Copy to clipboard"), self)
        copy_button.clicked.connect(self._copy_selected_isbn)
        action_row.addWidget(copy_button)

        self.more_info_button = QPushButton(_("More info"), self)
        self.more_info_button.clicked.connect(self._start_lookup)
        action_row.addWidget(self.more_info_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.lookup_finished.connect(self._show_lookup_result)
        self.resize(520, max(360, 240 + len(found_isbns) * 24))

    def selected_isbn(self) -> str | None:
        entry = self._selected_entry()
        return entry.isbn if entry else None

    def _selected_entry(self) -> FoundIsbn | None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._found_isbns):
            return None
        return self._found_isbns[row]

    def _clear_lookup_info(self, _row: int) -> None:
        self.info_panel.clear()

    def _copy_selected_isbn(self) -> None:
        isbn = self.selected_isbn()
        if not isbn:
            return
        QApplication.clipboard().setText(isbn)
        gui = self.parent()
        if gui is not None and hasattr(gui, "status_bar"):
            gui.status_bar.show_message(
                _("Copied ISBN {isbn} to clipboard.").format(isbn=isbn),
                timeout=3000,
            )

    def _start_lookup(self) -> None:
        isbn = self.selected_isbn()
        if not isbn:
            return
        if self._lookup_thread and self._lookup_thread.is_alive():
            return

        self.more_info_button.setEnabled(False)
        self.info_panel.setPlainText(
            _("Looking up ISBN {isbn}…").format(isbn=isbn)
        )
        self._lookup_thread = Thread(
            target=self._lookup_isbn,
            args=(isbn,),
            daemon=True,
        )
        self._lookup_thread.start()

    def _lookup_isbn(self, isbn: str) -> None:
        from calibre_plugins.hardcover_list.cull_isbn.isbn_lookup import (
            format_lookup_results,
            lookup_isbn,
        )

        try:
            results = lookup_isbn(isbn)
            payload = ("ok", format_lookup_results(results, isbn))
        except Exception as exc:
            payload = ("error", str(exc))
        self.lookup_finished.emit(payload)

    def _show_lookup_result(self, payload) -> None:
        self.more_info_button.setEnabled(True)
        _status, message = payload
        self.info_panel.setPlainText(message)
