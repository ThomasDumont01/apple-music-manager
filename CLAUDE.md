# Music Project

## Conventions
- Always use `rtk` proxy for CLI commands (git, etc.)
- Always use CAVEMAN communication mode (fragments, no filler, short)
- Language: French preferred for communication

## Stack figée
- Python ≥ 3.11
- **Textual** pour toute l'interface (aucun ANSI manuel, aucun termios)
- `mutagen` (tagging M4A)
- `yt-dlp` (audio YouTube via ISRC)
- `requests` (HTTP)
- `musicbrainzngs` (fallback silencieux uniquement)
- `subprocess` + AppleScript (`osascript`) pour Apple Music
- `ffmpeg` (binaire système)
- `uv` pour les dépendances et le packaging
- `ruff` pour le lint et le formatage
- `pytest` pour les tests

Ne pas dévier sans consulter Thomas.

## Sources de métadonnées
1. **Deezer API** (principal, sans clé) — ISRC + métadonnées complètes
2. **iTunes Search API** (sans clé) — pochettes 3000×3000
3. **MusicBrainz** (fallback silencieux dans le resolver)
- Pas de Spotify

## Style visuel
Inspiration Claude Code :
- Palette sombre, monospace
- Accents colorés discrets (accent chaud pour l'action, neutres pour le reste)
- Layout en colonnes claires
- Navigation 100 % clavier, fluide
- Aucune fioriture, aucun emoji décoratif (sauf caractères spéciaux classiques : `▶`, `✓`, `✗`, `♪`, `♫`ect.)

## Architecture
- Séparation stricte logique métier / UI
- `core/` : modèles, normalisation, I/O, config, logger — aucun import externe
- `providers/` : deezer, itunes, musicbrainz, resolver — accès API
- `services/` : apple, youtube, tagger — services système
- `pipeline/` : importer, dedup, pending — orchestration
- `options/` : contrôleurs légers connectant logique et UI
- `ui/` : Textual uniquement

## Méthode de travail
- Avancer pas à pas, un module ou une fonction à la fois
- Expliquer avant d'écrire, expliquer le non-trivial après
- Attendre validation avant de passer à la suite
- Pas de sur-ingénierie
- Tests au fil de l'eau (pas d'étape N+1 sans tests N verts)
- Observer mon style ; proposer d'ajouter les patterns récurrents à CLAUDE.md
- Décisions d'archi : exposer 2-3 options avec trade-offs, ne pas trancher seul
- Claude montre le code, Thomas copie-colle — ne jamais écrire directement les fichiers
- Thomas fait les commits — Claude ne commit jamais sans demande explicite, propose des messages de commit clairs

## Organisation des fichiers Python
Structure top-down standard :
1. Module docstring
2. Imports (stdlib, then 3rd-party, then local)
3. Constants
4. Entry point — public functions first (main, do_xxx, etc.)
5. Private Functions — helpers prefixed _xxx, grouped at the bottom
6. Run script — if __name__ == "__main__": block at the very end

Sections délimitées par `# ── Section ────────────...` (séparateur Unicode `─`).
Sections par **rôle/visibilité** (Constants, Entry point, Private Functions),
pas par thématique métier (HTTP, Genres, …) qui varie d'un fichier à l'autre.

Tests : fixtures et données dans `tests/data/`. Tests dans `tests/` organisés par module (ex : `test_resolver.py` pour `resolver.py`), pas de sous-dossiers.

## Langue du code
Tout le code est en **anglais** : identifiants, docstrings, commentaires, messages de log. **Aucun mot
de français dans la codebase.** La localisation (FR/EN…) sera gérée uniquement côté UI, pour
l'utilisateur final.

## Critères qualité
- Type hints partout (`str | None`, pas `Optional[str]`)
- Docstrings sur classes et fonctions publiques
- `RuntimeError` sur I/O critique, retour neutre sur best-effort
- Logs via `logger`, aucun `print()` dans la logique métier
- Code propre selon `ruff` avant chaque commit
- Commits : `add | fix | update | refactor : <descriptif>`
- Noms de variables explicites, pas de lettres isolées (`f`, `k`, `v`) — sauf `cls`/`self` (conventions Python)
- Pas de valeur par défaut pour les paramètres qui changent la sémantique. Si oublier le paramètre produit un comportement silencieux (et potentiellement faux), pas de défaut → caller obligé de décider.

## Conventions
- Always use `rtk` proxy for CLI commands (git, etc.)
- Always use CAVEMAN communication mode
- Language: French preferred for communication