from calibre.gui2.actions import InterfaceAction
from qt.core import QTimer

from calibre_plugins.hardcover_gui.book_details import ensure_hardcover_lists_column


class HardcoverListsAction(InterfaceAction):
    name = "Hardcover Lists"
    action_type = "global"
    action_spec = (
        "Hardcover Lists",
        None,
        _("Show Hardcover lists in the book details panel"),
        None,
    )
    dont_add_to = frozenset(
        [
            "menubar",
            "toolbar",
            "context-menu",
            "toolbar-child",
            "context-menu-device",
            "toolbar-device",
            "menubar-device",
        ]
    )

    def genesis(self):
        self.qaction.setVisible(False)

    def initialization_complete(self):
        QTimer.singleShot(0, self._setup_book_details)

    def library_changed(self, db):
        QTimer.singleShot(0, self._setup_book_details)

    def _setup_book_details(self):
        if not getattr(self.gui, "current_db", None):
            return
        try:
            created = ensure_hardcover_lists_column(self.gui)
        except Exception:
            import traceback

            traceback.print_exc()
            return

        if created:
            from calibre.gui2.dialogs.confirmation import confirm

            confirm(
                _(
                    "Hardcover Lists added a custom column to your library. "
                    "Restart calibre if the new field does not appear in book details."
                ),
                "hardcover_lists_column_created",
                parent=self.gui,
            )
