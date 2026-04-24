# Contributing

Guide de contribution pour Music Manager.

## Pre-requis developpement

- macOS 10.15+ (Catalina)
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) pour les dependances
- Apple Music installe et ouvert

```bash
# Cloner le projet
git clone https://github.com/ThomasDumont01/apple-music-manager.git
cd apple-music-manager

# Installer les dependances (dev incluses)
uv sync --group dev

# Installer les outils systeme
brew install ffmpeg yt-dlp
```

## Architecture

```
music_manager/
    core/           Modeles, normalisation, I/O, config, logger
                    Aucun import externe (stdlib only)
    services/       Stores (tracks, albums) + wrappers (resolver, tagger, apple, youtube)
    pipeline/       Orchestration (importer, dedup)
    options/        Controleurs legers (identify, import, modify, fix, export...)
    ui/             Textual TUI
        screens/    Ecrans (menu via mixins, checks, setup, welcome)
        render.py   Fonctions de rendu (Rich Text)
        text.py     Tous les textes utilisateur (FR)
        styles.py   Couleurs et symboles
```

### Regles d'architecture

- `core/` ne doit **jamais** importer de libs externes (requests, mutagen...)
- `options/` sont des controleurs **legers** — la logique lourde va dans `services/` ou `pipeline/`
- `ui/screens/menu.py` est une composition de mixins — chaque feature dans son fichier `_xxx.py`
- Saves centralises dans `_save_all()` au changement de vue (pas dans les options)

## Code style

### Langue

- **Code** : tout en anglais (identifiants, docstrings, commentaires, logs)
- **UI** : textes utilisateur en francais dans `ui/text.py`

### Conventions Python

```bash
# Lint + format
uv run ruff check music_manager/ tests/ --fix
uv run ruff format music_manager/ tests/
```

- Type hints partout (`str | None`, pas `Optional[str]`)
- Docstrings sur classes et fonctions publiques
- Variables explicites, pas de lettres isolees (`f`, `k`, `v` interdits sauf `cls`/`self`)
- Sections delimitees par `# ── Section ──────...`
- `RuntimeError` sur I/O critique, retour neutre sur best-effort
- ISRC toujours en UPPERCASE dans les indexes et comparaisons

### Organisation des fichiers

```
1. Module docstring
2. Imports (stdlib, 3rd-party, local)
3. Constants
4. Entry point (fonctions publiques)
5. Private Functions (_xxx)
6. if __name__ == "__main__"
```

## Tests

```bash
# Lancer tous les tests
uv run pytest tests/ -q

# Tests unitaires uniquement (sans integration macOS)
uv run pytest tests/ -q -m "not integration"

# Un fichier specifique
uv run pytest tests/test_services_resolver.py -v
```

### Regles

- Tests **avant** implementation (TDD)
- Edge cases systematiques : `None`, `""`, `0`, ISRC mixte, caracteres speciaux
- Operations destructives : prouver qu'elles ne suppriment pas trop
- `uv run pytest tests/ -q` doit passer a 100% avant chaque commit

### Organisation

- Un fichier test par module source (`test_resolver.py` pour `resolver.py`)
- Fixtures dans `tests/data/`
- Tests d'integration marques `@pytest.mark.integration`

## Commits

Format : `type : description`

Types : `add`, `fix`, `update`, `refactor`, `perf`, `remove`

```bash
# Exemples
git commit -m "fix : ISRC normalization in build_track"
git commit -m "add : welcome screen for first launch"
git commit -m "refactor : split menu.py into mixins"
```

## Sources de metadonnees

| Source | Usage | Cle API |
|--------|-------|---------|
| Deezer API | ISRC + metadonnees completes | Aucune |
| iTunes Search API | Pochettes 3000x3000 | Aucune |
| YouTube (via yt-dlp) | Audio officiel | Aucune |

**Pas** de Spotify API, **pas** de MusicBrainz.

## Thread safety

- `Tracks` store protege par `threading.RLock()`
- `Albums.put()` **pas** thread-safe — collecter les resultats, appliquer sequentiellement
- `_API_CACHE` protege par `_CACHE_LOCK`
- Operations Apple Music (AppleScript) : toujours sequentielles
