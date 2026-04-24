"""View states for the menu screen — typed enum replacing raw strings."""

from enum import Enum


class View(Enum):
    """Every possible view state in MenuScreen."""

    # Core navigation
    MAIN = "main"
    TOOLS = "tools"
    MAINTENANCE = "maintenance"
    HELP = "help"
    SUMMARY = "summary"

    # Import flow
    IMPORTING = "importing"
    IMPORT_DONE_PENDING = "import_done_pending"
    QUEUE_NEXT = "queue_next"

    # Review flow (shared by import + identify)
    REVIEWING = "reviewing"
    BATCH_DECISION = "batch_decision"
    SEARCH_INPUT = "search_input"

    # Identify flow
    IDENTIFYING = "identifying"
    IDENTIFY_DONE = "identify_done"
    IDENTIFY_SUMMARY = "identify_summary"
    IDENTIFY_ALBUM_PICK = "identify_album_pick"

    # Modify flow
    MODIFY_SEARCH = "modify_search"
    MODIFY_RESULTS = "modify_results"
    MODIFY_ACTIONS = "modify_actions"
    MODIFY_EDITIONS = "modify_editions"
    MODIFY_COVERS = "modify_covers"
    MODIFY_METADATA = "modify_metadata"
    MODIFY_META_EDIT = "modify_meta_edit"
    MODIFY_WORKING = "modify_working"
    MODIFY_DONE = "modify_done"
    MODIFY_UNMATCHED = "modify_unmatched"
    MODIFY_DELETE_CONFIRM = "modify_delete_confirm"
    SEARCH_FAILED = "search_failed"

    # Fix metadata
    FIXING_SCAN = "fixing_scan"
    FIXING = "fixing"

    # Duplicates
    DUPLICATES = "duplicates"
    DUP_REMOVING = "dup_removing"

    # Maintenance
    MAINTENANCE_CONFIRM = "maintenance_confirm"

    # Export
    EXPORTING = "exporting"

    # Complete albums
    COMPLETING = "completing"
    COMPLETING_PROGRESS = "completing_progress"
