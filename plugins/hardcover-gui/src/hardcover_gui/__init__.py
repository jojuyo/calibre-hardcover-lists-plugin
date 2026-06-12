from calibre.customize import InterfaceActionBase

from ._version import __version_tuple__


class HardcoverGuiPlugin(InterfaceActionBase):
    name = "Hardcover Lists"
    description = "Shows Hardcover list membership in the book details panel"
    supported_platforms = ["windows", "osx", "linux"]
    author = "Rob Brazier"
    version = __version_tuple__
    minimum_calibre_version = (7, 7, 0)

    actual_plugin = "calibre_plugins.hardcover_gui.ui:HardcoverListsAction"
