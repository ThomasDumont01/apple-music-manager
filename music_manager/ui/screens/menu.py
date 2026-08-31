"""Main menu screen — combines feature mixins into MenuScreen.

Each mixin provides methods for one feature area. MenuScreenCore holds
shared state (__init__), compose, rendering helpers, and action dispatchers.
"""

from music_manager.ui.screens._complete import CompleteMixin
from music_manager.ui.screens._core import MenuScreenCore
from music_manager.ui.screens._duplicates import DuplicatesMixin
from music_manager.ui.screens._export import ExportMixin
from music_manager.ui.screens._fix_metadata import FixMetadataMixin
from music_manager.ui.screens._identify import IdentifyMixin
from music_manager.ui.screens._import import ImportMixin
from music_manager.ui.screens._maintenance import MaintenanceMixin
from music_manager.ui.screens._modify import ModifyMixin
from music_manager.ui.screens._preview import PreviewMixin
from music_manager.ui.screens._review import ReviewMixin


class MenuScreen(
    ImportMixin,
    ReviewMixin,
    IdentifyMixin,
    ModifyMixin,
    FixMetadataMixin,
    DuplicatesMixin,
    CompleteMixin,
    ExportMixin,
    MaintenanceMixin,
    PreviewMixin,
    MenuScreenCore,
):
    """Single screen: menu + all features via mixins."""
