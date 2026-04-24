# Music Manager

Import de morceaux dans Apple Music avec pochettes HD et audio officiel.

```
Music Manager                                502 pistes · 34 albums

  ─── Pistes ──────────────────────────────────────────────────────

  ❯ Importer des pistes
    requetes                                                  4/12
    ma_playlist                                                  ✓

  ─── Playlists ───────────────────────────────────────────────────

    road_trip                                                  0/8

  ───
    Outils
    Maintenance
    Aide

↑↓  naviguer    ⏎  sélectionner    esc  quitter
```

## Download

**[Telecharger la derniere version](https://github.com/ThomasDumont01/apple-music-manager/releases/latest)**

## Fonctionnalites

- **Importer des pistes** depuis un fichier CSV (export Spotify/Deezer/playlist)
- **Identifier la bibliotheque** : lier vos morceaux Apple Music a Deezer pour enrichir les metadonnees
- **Corriger les metadonnees** : titre, artiste, album, pochette HD (3000x3000)
- **Completer les albums** : importer les pistes manquantes d'albums partiels
- **Trouver les doublons** : detecter et supprimer les morceaux en double
- **Modifier une piste** : changer d'edition, de pochette, re-telecharger l'audio
- **Exporter une playlist** : sauvegarder vos playlists Apple Music en CSV

## Pre-requis

- **macOS 10.15** (Catalina) ou plus recent
- **Apple Music** installee (l'app l'ouvre automatiquement au lancement)
- **Connexion internet** pour la recherche de metadonnees et le telechargement

## Installation

### Via le DMG (recommande)

1. Telecharger le fichier `.dmg` depuis les [Releases](https://github.com/ThomasDumont01/apple-music-manager/releases/latest)
2. Ouvrir le DMG
3. Clic sur **Installer Music Manager**. L'app n'est pas signee Apple, macOS peu bloquer le lancement. Si c'est le cas : **Reglages Systeme → Confidentialite et securite → Autoriser quand meme → Relancer le dmg**
4. L'installation est automatique (~2 minutes) :
   - Installe Homebrew si absent
   - Installe ffmpeg et yt-dlp via brew
   - Installe Music Manager dans `/Applications`
5. L'app s'ouvre automatiquement a la fin

### Via la ligne de commande

```bash
brew install ffmpeg yt-dlp
uv tool install music-manager
music-manager
```

## Premier lancement

Au premier lancement, Music Manager :

1. **Presente les fonctionnalites** et explique le fonctionnement
2. **Verifie les dependances** (ffmpeg, yt-dlp, Apple Music)
3. **Scanne votre bibliotheque** Apple Music
4. **Identifie vos pistes** en lisant les identifiants audio et en les resolvant sur Deezer

## Utilisation

### Navigation

| Touche | Action |
|--------|--------|
| `↑` `↓` | Naviguer dans les menus |
| `⏎` | Selectionner / Confirmer |
| `esc` | Retour / Quitter |
| `espace` | Cocher / Decocher |
| `s` | Passer un element |
| `p` | Previsualiser (ecouter 30s / voir pochette) |

### Importer des pistes

1. Placez vos fichiers CSV dans le dossier Music Manager
2. Selectionnez **Importer des pistes** dans le menu
3. Music Manager cherche chaque piste sur Deezer, telecharge l'audio depuis YouTube, et l'importe dans Apple Music avec les metadonnees et la pochette HD

### Format CSV

```csv
title,artist,album
Bohemian Rhapsody,Queen,A Night at the Opera
Imagine,John Lennon,Imagine
```

Les exports Spotify (via [Exportify](https://exportify.net)) et Deezer sont automatiquement convertis.

## Dependances systeme

| Outil | Usage | Installation |
|-------|-------|-------------|
| ffmpeg | Conversion audio | `brew install ffmpeg` |
| yt-dlp | Telechargement audio | `brew install yt-dlp` |
| afplay | Previsualisation (inclus dans macOS) | — |

## Donnees

Toutes les donnees sont stockees **localement** :

- **Configuration** : `~/.config/music_manager/config.json`
- **Donnees** : dossier choisi au premier lancement
- **Aucune donnee** n'est envoyee a des serveurs tiers

## Depannage

### Apple Music ne repond pas

Ouvrez l'app Music (dans le Dock ou Applications), puis relancez Music Manager.

### Import echoue sur certaines pistes

Certaines pistes peuvent echouer si :
- La piste n'existe pas sur Deezer (orthographe differente, titre rare)
- YouTube n'a pas de version audio officielle
- La duree ne correspond pas (protection contre les mauvais matches)

Ces pistes passent en **revue manuelle** ou vous pouvez choisir parmi les alternatives proposees.

## Licence

[MIT](LICENSE)
