"""Menu labels, help text, messages.

Change these to adjust text content without touching rendering logic.
"""

from music_manager.ui.styles import BLUE

# ── Menu items ──────────────────────────────────────────────────────────────

TOOLS_ITEMS: list[tuple[str, str] | None] = [
    ("modify", "Modifier une piste"),
    ("complete", "Compléter les albums"),
    ("fix", "Corriger les métadonnées"),
    ("duplicates", "Trouver les doublons"),
    ("export", "Exporter une playlist"),
    ("recommend", "Recommandations"),
    None,
    ("back", "Retour au menu principal"),
]

MAINTENANCE_ITEMS: list[tuple[str, str] | None] = [
    ("snapshot", "Valider les imports comme existants"),
    ("reset_failed", "Relancer les imports échoués"),
    ("clear_prefs", "Réinitialiser les préférences"),
    ("revert", "Annuler tous les imports"),
    ("move_data", "Déplacer le dossier de données"),
    None,
    ("delete_all", "Supprimer toutes les données"),
    None,
    ("back", "Retour au menu principal"),
]

# ── Section labels ──────────────────────────────────────────────────────────

SECTION_PISTES = "Pistes"
SECTION_PLAYLISTS = "Playlists"

# ── Import status labels (shown during import progress) ─────────────────────

STATUS_LABELS = {
    "done": "",
    "skipped": "déjà traité",
    "not_found": "introuvable, à vérifier",
    "ambiguous": "plusieurs résultats, à vérifier",
    "mismatch": "différence Deezer, à vérifier",
    "youtube_failed": "échec téléchargement",
    "duration_suspect": "durée suspecte, à vérifier",
    "apple_import_failed": "échec import Apple Music",
}

# ── Reason labels (shown in review) ─────────────────────────────────────────

REASON_LABELS = {
    "not_found": "Introuvable sur Deezer.",
    "ambiguous": "Plusieurs éditions trouvées.",
    "mismatch": "Édition différente de celle demandée.",
    "youtube_failed": "Aucun résultat YouTube pour cet ISRC.",
    "duration_suspect": "Durée suspecte.",
}

# ── Summary labels ──────────────────────────────────────────────────────────

SUMMARY_IMPORTED = "importée(s)"
SUMMARY_EXISTING = "existante(s)"
SUMMARY_FAILED = "passée(s)"
SUMMARY_DELETED = "supprimée(s)"
SUMMARY_IGNORED = "ignorée(s)"

# ── Review labels ───────────────────────────────────────────────────────────

REVIEW_EDITION = "Quelle édition ?"
REVIEW_BATCH_ALL = "Tout accepter"
REVIEW_BATCH_REJECT = "Tout rejeter"
REVIEW_BATCH_ONE = "Revue une par une"

# ── Fix metadata labels ────────────────────────────────────────────────────

FIX_TITLE = "Corriger les métadonnées"
FIX_NO_IDENTIFIED = "Aucune piste identifiée."
FIX_UP_TO_DATE = "Tout est à jour."
FIX_SCANNING = "Analyse des métadonnées..."
FIX_APPLY = "Appliquer la sélection"
FIX_SKIP = "Passer cet album"
FIX_IGNORE = "Ignorer définitivement"

FIELD_LABELS: dict[str, str] = {
    "title": "Titre",
    "artist": "Artiste",
    "album": "Album",
    "genre": "Genre",
    "year": "Année",
    "track_number": "N° piste",
    "disk_number": "N° disque",
    "total_tracks": "Total pistes",
    "album_artist": "Artiste album",
    "explicit": "Explicit",
    "cover": "Pochette",
}

# ── Review actions ─────────────────────────────────────────────────────────

ACTION_LABELS: dict[str, str] = {
    "accept": "Accepter la version Deezer",
    "accept_audio": "Accepter cet audio",
    "skip": "Passer",
    "delete_csv": "Supprimer du CSV",
    "search_deezer": "Chercher sur Deezer",
    "search_youtube": "Chercher sur Youtube",
    "retry": "Réessayer",
    "ignore_identify": "Ignorer définitivement",
    "ignore": "Ignorer définitivement",
}

REVIEW_OPTIONS: dict[str, list[str]] = {
    "not_found": ["skip", "search_deezer", "ignore", "delete_csv"],
    "mismatch": ["accept", "search_deezer", "skip", "ignore", "delete_csv"],
    "youtube_failed": ["retry", "search_youtube", "skip", "ignore", "delete_csv"],
    "duration_suspect": ["accept_audio", "search_youtube", "skip", "ignore", "delete_csv"],
}

# ── Search input ───────────────────────────────────────────────────────────

SEARCH_DEEZER_TITLE = "Chercher sur Deezer"
SEARCH_YOUTUBE_TITLE = "Chercher sur Youtube"
SEARCH_PROMPT_DEEZER = "Collez un lien Deezer (piste ou album) :"
SEARCH_PROMPT_YOUTUBE = "Collez un lien YouTube :"
SEARCH_ERROR_INVALID = "Lien invalide."

# ── Help text ───────────────────────────────────────────────────────────────

HELP_TEXT = f"""\
[bold {BLUE}]Music Manager[/]

[dim]Importe de la musique dans Apple Music[/]
[dim]pochettes HD · audio qualité officielle[/]

[dim]─── [bold]Import[/] ─────────────────────────[/]

  Dépose un CSV ou un export Spotify
  (Exportify) dans le dossier de données.

[dim]─── [bold]Outils[/] ─────────────────────────[/]

  [{BLUE}]Identifier[/]  [dim]lier la bibliothèque à Deezer[/]
  [{BLUE}]Compléter[/]   [dim]pistes manquantes d'un album[/]
  [{BLUE}]Corriger[/]    [dim]pochettes, genres, numéros[/]
  [{BLUE}]Modifier[/]    [dim]édition, pochette, métadonnées[/]
  [{BLUE}]Doublons[/]    [dim]trouver et gérer les copies[/]
  [{BLUE}]Exporter[/]    [dim]playlist en CSV[/]

[dim]─── [bold]Maintenance[/] ────────────────────[/]

  [{BLUE}]Snapshot[/]       [dim]valider comme pistes existantes[/]
  [{BLUE}]Reset échecs[/]   [dim]relancer les imports échoués[/]
  [{BLUE}]Annuler[/]        [dim]supprimer les imports[/]
  [{BLUE}]Déplacer[/]       [dim]changer le dossier de données[/]"""

# ── Checks screen ──────────────────────────────────────────────────────────

CHECKS_TITLE = "Music Manager"
CHECKS_DEPS_LABEL = "Dépendances"
CHECKS_APPLE_LABEL = "Apple Music"
CHECKS_DEEZER_LABEL = "Deezer API"
CHECKS_YOUTUBE_LABEL = "YouTube"
CHECKS_ITUNES_LABEL = "iTunes Search API"
CHECKS_BREW_PROMPT = "Installer avec brew ?"
CHECKS_BREW_INSTALL = "Installation..."
CHECKS_ERROR_NO_BREW = "Installez les dépendances manuellement :\n  brew install ffmpeg yt-dlp"
CHECKS_ERROR_APPLE = (
    "Apple Music ne répond pas.\n"
    "  Ouvrez l'app Music (dans le Dock ou Applications),\n"
    "  puis relancez Music Manager."
)

# ── Setup screen ───────────────────────────────────────────────────────────

SETUP_TITLE = "Premier lancement"
SETUP_SCAN_LIBRARY = "Scan bibliothèque"
SETUP_SCAN_ISRC = "Lecture des identifiants"
SETUP_RESOLVE_ISRC = "Résolution Deezer"
SETUP_DONE = "Premier lancement terminé"

# ── Help bars ───────────────────────────────────────────────────────────────

HELP_MAIN = "↑↓  naviguer    ⏎  sélectionner    esc  quitter"
HELP_SUB = "↑↓  naviguer    ⏎  sélectionner    esc  retour"
HELP_IMPORT = "  import en cours..."
HELP_REVIEW = "↑↓  naviguer    ⏎  sélectionner    p  écouter"
HELP_REVIEW_START = "⏎  commencer la review"
HELP_REVIEW_BATCH = "↑↓  naviguer    ⏎  sélectionner"
HELP_SEARCH_INPUT = "⏎  valider    esc  retour"
HELP_BACK = "esc  retour au menu"
HELP_HELP = "esc  retour"
# ── Modify track labels ────────────────────────────────────────────────────

MODIFY_TITLE = "Modifier une piste"
MODIFY_SEARCH_PROMPT = "Recherche"
MODIFY_NO_RESULTS = "Aucun résultat"
MODIFY_SECTION_TRACKS = "Pistes"
MODIFY_SECTION_ALBUMS = "Albums"

MODIFY_TRACK_ACTIONS: list[tuple[str, str] | None] = [
    ("edition", "Changer d'édition"),
    ("redownload", "Retélécharger l'audio"),
    ("replace_url", "Remplacer l'audio (URL YouTube)"),
    ("cover", "Changer la pochette"),
    ("metadata", "Modifier les métadonnées"),
    ("delete", "Supprimer la piste"),
    None,
    ("back", "Retour"),
]

MODIFY_ALBUM_ACTIONS: list[tuple[str, str] | None] = [
    ("album_edition", "Changer d'édition de l'album"),
    ("album_cover", "Changer la pochette"),
    ("album_metadata", "Modifier les métadonnées"),
    ("album_delete", "Supprimer l'album"),
    None,
    ("back", "Retour"),
]

MODIFY_STATUS = {
    "resolving": "Résolution Deezer...",
    "importing": "Import en cours...",
    "downloading": "Téléchargement...",
    "deleting_old": "Suppression ancienne version...",
}

MODIFY_METADATA_FIELDS: list[tuple[str, str]] = [
    ("title", "Titre"),
    ("artist", "Artiste"),
    ("album", "Album"),
    ("album_artist", "Artiste album"),
    ("genre", "Genre"),
    ("year", "Année"),
    ("track_number", "N° piste"),
]

HELP_MODIFY_SEARCH = "  tapez pour rechercher    esc  retour"
HELP_MODIFY_ACTIONS = "↑↓  naviguer    ⏎  sélectionner    esc  retour"
HELP_MODIFY_EDITIONS = "↑↓  naviguer    ⏎  sélectionner    p  écouter    esc  retour"
HELP_MODIFY_METADATA = "↑↓  naviguer    ⏎  modifier / appliquer    esc  retour"
HELP_MODIFY_COVERS = "↑↓  naviguer    ⏎  sélectionner    p  voir    esc  retour"

# ── Maintenance labels ─────────────────────────────────────────────────────

MAINT_CONFIRM_REVERT = "Supprimer {} import(s) d'Apple Music ?"
MAINT_CONFIRM_DELETE = "Supprimer toutes les données Music Manager ?"
MAINT_CONFIRM = "Confirmer"
MAINT_CANCEL = "Annuler"

# ── Export playlist labels ─────────────────────────────────────────────────

EXPORT_TITLE = "Exporter une playlist"
EXPORT_NO_PLAYLISTS = "Aucune playlist trouvée."
EXPORT_APPLY = "Exporter la sélection"
EXPORT_BACK = "Retour"

HELP_EXPORT = "↑↓  naviguer    espace  cocher/décocher    ⏎  exporter"

# ── Complete albums labels ─────────────────────────────────────────────────

COMPLETE_TITLE = "Compléter les albums"
COMPLETE_NO_IDENTIFIED = "Aucune piste identifiée."
COMPLETE_NONE_FOUND = "Tous les albums sont complets."
COMPLETE_APPLY = "Compléter la sélection"
COMPLETE_BACK = "Retour"

HELP_COMPLETE = "↑↓  naviguer    espace  cocher/décocher    a  tout    ⏎  compléter"
HELP_COMPLETE_PROGRESS = "  complétion en cours..."
RATE_LIMIT_WAIT = "Limite YouTube atteinte — nouvel essai dans {wait}…"
RATE_LIMIT_REASON = "{reason} — nouvel essai dans {wait}…"

COOKIES_FOUND = "Compte YouTube détecté dans Safari. Utiliser les cookies ?"
COOKIES_NOT_FOUND = "Connexion YouTube requise pour cette vidéo."
COOKIES_WAIT_LOGIN = "Connectez-vous à YouTube dans Safari, puis appuyez sur Entrée…"
COOKIES_ACTIVATED = "✓ Cookies Safari activés pour cette session"
COOKIES_FAILED = "Cookies Safari invalides — vidéo ignorée"
COOKIES_DECLINED = "Vidéos restreintes ignorées pour cette session"


def format_wait(seconds: int) -> str:
    """Format seconds into human-readable wait time (30s, 2min, 30min, 1h)."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}min"
    hours = seconds // 3600
    return f"{hours}h"

# ── Identify labels ────────────────────────────────────────────────────────

IDENTIFY_TITLE = "Identifier la bibliothèque"
IDENTIFY_SCANNING = "Identification en cours..."

HELP_IDENTIFY = "  identification en cours..."
HELP_IDENTIFY_DONE = "⏎  commencer la review"
HELP_IDENTIFY_REVIEW = "↑↓  naviguer    ⏎  sélectionner    p  écouter    esc  passer"

# ── Duplicates labels ─────────────────────────────────────────────────────

DUP_TITLE = "Trouver les doublons"
DUP_NO_IDENTIFIED = "Aucune piste identifiée."
DUP_NONE_FOUND = "Aucun doublon trouvé."
DUP_KEEP = "Garder cette version"
DUP_SKIP = "Passer"
DUP_IGNORE = "Ignorer définitivement"
DUP_REMOVING = "Suppression en cours..."

HELP_DUP = "↑↓  naviguer    ⏎  garder    s  passer    p  écouter    esc  retour"

# ── Recommendations labels ────────────────────────────────────────────────

RECOMMEND_TITLE = "Recommandations"
RECOMMEND_FOLDER_NAME = "for me"
RECOMMEND_PLAYLIST_NAME = "for me"  # legacy alias (used by old UI strings)
RECOMMEND_API_KEY_PROMPT = (
    "Clé API Last.fm requise.\n"
    "Crée-en une gratuitement sur https://www.last.fm/api\n"
    "puis colle-la ici :"
)
RECOMMEND_API_KEY_INVALID = "Clé invalide. Vérifie et réessaie."
RECOMMEND_SELECT_PROMPT = "Quel type de recommandations ?"
RECOMMEND_MODE_LIBRARY = "Bibliothèque — basé sur tous tes goûts"
RECOMMEND_MODE_GENERAL = RECOMMEND_MODE_LIBRARY  # legacy alias
RECOMMEND_MODE_PLAYLIST = "Playlist — à partir d'une de tes playlists"
RECOMMEND_MODE_GENRE = "Genre — cibler un genre précis"
RECOMMEND_MODE_MOOD = "Ambiance — cibler une humeur"
RECOMMEND_MODE_DISCOVERY = "Découverte — sortir des sentiers battus"
RECOMMEND_NO_GENRES = "Pas assez de genres dans ta bibliothèque."
RECOMMEND_NO_USER_PLAYLISTS = "Aucune playlist trouvée dans Apple Music."
RECOMMEND_GENRE_PROMPT = "Choisis un genre :"
RECOMMEND_MOOD_PROMPT = "Choisis un mood :"
RECOMMEND_PLAYLIST_PROMPT = "Choisis une playlist source :"
RECOMMEND_COUNT_PROMPT = "Combien de recommandations ?"
RECOMMEND_COUNTS: list[tuple[int, str]] = [
    (10, "10  — rapide (~30 s)"),
    (20, "20  — équilibré (~1 min)"),
    (30, "30  — plus de matière (~2 min)"),
    (50, "50  — grande exploration (~3-4 min)"),
]
RECOMMEND_MOODS: list[tuple[str, str]] = [
    ("chill", "Chill — apaisant"),
    ("energetic", "Énergique — boost"),
    ("melancholic", "Mélancolique — introspectif"),
    ("romantic", "Romantique — tendre"),
    ("party", "Party — pour danser"),
    ("focus", "Focus — pour bosser"),
]
RECOMMEND_SCAN_RUNNING = "Détection des recommandations supprimées..."
RECOMMEND_SCAN_RESULT = "{count} ancienne(s) recommandation(s) supprimée(s) — blacklistée(s)."
RECOMMEND_GENERATING = "Recherche de recommandations sur Last.fm..."
RECOMMEND_RESOLVING = "Recherche sur Deezer..."
RECOMMEND_IMPORTING_PROGRESS = "Import {current}/{total}..."
RECOMMEND_DONE_TITLE = "Recommandations ajoutées"
RECOMMEND_DONE_SUMMARY = (
    "{imported} ajoutée(s) à « for me / {playlist} ».\n"
    "{failed} échouée(s).\n"
    "Bilan apprentissage : {adopted} adoptée(s), "
    "{kept} gardée(s) en bibliothèque, {rejected} retirée(s)."
)
RECOMMEND_ERROR_NO_KEY = "Aucune clé Last.fm configurée."
RECOMMEND_ERROR_EMPTY = "Last.fm n'a renvoyé aucune piste. Réessaie plus tard."
RECOMMEND_ERROR_GENERIC = "Erreur : {message}"

HELP_RECOMMEND = "↑↓  naviguer    ⏎  valider    esc  retour"
HELP_RECOMMEND_API_KEY = "⏎  valider    esc  retour"
HELP_RECOMMEND_RUNNING = "  recommandations en cours..."
HELP_RECOMMEND_DONE = "⏎  retour au menu"

# ── Checks screen ──────────────────────────────────────────────────────────

HELP_CHECKS = "⏎  continuer"
HELP_CHECKS_BREW = "⏎  installer    esc  quitter"
HELP_CHECKS_ERROR = "esc  quitter"
HELP_SETUP = "  scan en cours..."
HELP_SETUP_DONE = "⏎  continuer vers le menu"
