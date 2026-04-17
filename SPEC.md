# Music Manager — Logique métier

## 1. Démarrage

Si la **langue** n'est pas configurée : demander à l'utilisateur de choisir (English / Français), sauvegarder dans **settings.json** (plus tard). Ce choix adapte la langue de l'application (menus, messages) et la logique de parsing CSV (noms de colonnes Exportify).

Si la **plateforme** n'est pas macOS : afficher une erreur et quitter.

Si des **dépendances système** manquent (afplay, yt-dlp, ffmpeg ...) :
- Si **brew** est installé : proposer l'installation automatique. Si l'utilisateur refuse : quitter.
- Sinon : afficher une erreur avec les instructions d'installation et quitter.

Si **Apple Music** ne répond pas : afficher une erreur et quitter.

Si l'**API Deezer** n'est pas joignable : afficher une erreur et quitter.

Si aucun **dossier racine** n'est configuré : ouvrir le sélecteur de dossier Finder, sauvegarder le chemin choisi dans **settings.json** avec le choix de langue.

Si les **dossiers de données** n'existent pas : les créer (.cache/, playlists/, raccourcis/). Créer un **CSV de démo** (requests.csv ou requetes.csv selon la langue). Créer les **raccourcis web** .webloc (Deezer, Exportify, YouTube).

Si c'est le **premier lancement** (setup_done dans **settings.json** est faux) : scanner toute la bibliothèque Apple Music, sauvegarder toutes les pistes comme entrées **baseline** dans **tracks.json** (origin = "baseline", status = null), passer setup_done à vrai, message à l'utilisateur pour lui dire ce qu'il c'est passé + demander à l'utilisateur d'appuyer sur Entrée, puis aller au menu principal.

- que vaut-il mieux faire comme fichier de config ? un .json ou autre ?
- l'interface doit être clair pour l'utilisateur pour qu'il comprenne bien ce qu'il se passe.
- je souhaite que le spinner de chargement qui servait pour le scan de la bibliothèque soit peut être remplacée par une barre de progression plus informative, indiquant le nombre de pistes scannées sur le total, et éventuellement le temps estimé restant.
- sur l'ancienne version du projet, pour le scan d'une bibliothèque avec plus de 1000 pistes le scan est beaucoup trop long. interessant de passer en RUST pour cette fonction là ? ou alors un autre processus plus rapide ? plus le scan est rapide, plus l'expérience utilisateur est fluide dès le départ, plus on peut faire des scan régulièrement plus le projet est le projet est fiable et à jour avec la vraie bibliothèque Apple Music.
---

## 2. Menu principal

Afficher les **statistiques** de la bibliothèque (nombre de pistes, nombre d'albums), les infos du **CSV** (nom du fichier, nombre de lignes), et le nombre de pistes **en attente** s'il y en a.

Le reste de l'interface sera construit lors de la phase UI. Cette spécification se concentre uniquement sur la logique métier.

---

## 3. Pipeline d'import

### 3.1 Chargement du CSV

Si le **fichier CSV** n'existe pas : afficher une erreur et revenir au menu.

Charger toutes les lignes du CSV. Si le CSV est **vide** : afficher une erreur et revenir.

Si le CSV est au **format Exportify/Spotify** (détecté par les noms de colonnes comme "Track Name", "Artist Name(s)") : le convertir au format standard (**title**, **artist**, **album**, **isrc**) et écraser le fichier.

### 3.2 Traitement de chaque ligne

Pour chaque ligne du CSV :

#### Vérification des doublons

Si la ligne a un **ISRC** et que cet ISRC existe déjà dans **tracks.json** :
- Si le statut de l'entrée est **"done"** ou **"failed"** : ignorer cette ligne et passer à la suivante.

Sinon, s'il existe une entrée dans tracks.json (avec statut "done" ou "failed") où **is_same**(titre_ligne, titre_entrée, "title") et **is_same**(artiste_ligne, artiste_entrée, "artist") sont tous les deux vrais : ignorer cette ligne et passer à la suivante.

#### Résolution des métadonnées

Si la ligne a un **ISRC** :
- Chercher la piste sur Deezer via l'ISRC. L'ISRC est très fiable : si une piste est trouvée, la considérer comme résolue sans validation supplémentaire. Passer à l'**enrichissement de la pochette**.
- Si Deezer ne retourne rien pour cet ISRC : passer à la **recherche par titre + artiste** ci-dessous.

Si la ligne n'a **pas d'ISRC** (ou si la recherche ISRC a échoué) :
- Chercher sur Deezer par **titre** et **premier artiste**. Si la ligne a un album, l'inclure dans la recherche.
- Si **un seul résultat** est retourné et que **is_match**(titre) et **is_match**(artiste) passent : utiliser ce résultat. Puis lancer une **post-validation stricte** : si **is_same**(titre_csv, titre_piste, "title") est faux, créer un **PendingTrack** avec raison **"mismatch"** (title_mismatch = vrai) et continuer. Même vérification pour l'artiste.
- Si **plusieurs résultats** correspondent : créer un **PendingTrack** avec raison **"ambiguous"** et joindre tous les candidats. Continuer.
- Si **zéro résultat** : essayer **MusicBrainz** en fallback silencieux. Si MusicBrainz trouve un ISRC, le chercher sur Deezer. Si ça marche : résolu. Sinon : créer un **PendingTrack** avec raison **"not_found"** et continuer.

#### Enrichissement de la pochette

Chercher sur **iTunes** une pochette haute résolution (3000×3000) avec le nom d'album et l'artiste de la piste.

Si iTunes retourne une **URL de pochette** : l'utiliser comme **cover_url** de la piste (remplace la pochette Deezer).

Sinon : garder la **pochette Deezer** déjà présente sur la piste.

Télécharger l'image de pochette dans un **fichier temporaire** pour le tagging.

#### Téléchargement YouTube

Chercher sur YouTube par **ISRC**, titre et artiste. Prioriser les résultats des **chaînes Topic** (audio officiel). Trier les candidats restants par **proximité de durée** avec la durée attendue (Deezer).

Si **aucun candidat** n'est trouvé : créer un **PendingTrack** avec raison **"youtube_failed"** et continuer.

Télécharger le meilleur candidat en **M4A**. Si le téléchargement échoue après **un réessai** (délai de 3 secondes) : créer un **PendingTrack** avec raison **"youtube_failed"** et continuer.

#### Vérification de la durée

Si la **durée réelle** et la **durée attendue** (Deezer) sont toutes les deux disponibles : calculer le ratio (réelle / attendue).

Si le ratio est **inférieur à 0.93** ou **supérieur à 1.07** : créer un **PendingTrack** avec raison **"duration_suspect"**, joindre le chemin du fichier téléchargé, la durée réelle et les candidats YouTube restants. Continuer.

Sinon : la durée est acceptable, continuer.

#### Tagging et import

Taguer le fichier M4A téléchargé avec toutes les métadonnées via **mutagen** : titre, artiste, album, genre, année, numéro de piste, numéro de disque, total pistes, artiste de l'album, ISRC et pochette.

Importer le fichier tagué dans Apple Music via **AppleScript**. Récupérer l'identifiant persistant **apple_id**.

Mettre à jour la piste : apple_id, status = **"done"**, origin = **"imported"**, imported_at = horodatage actuel. Sauvegarder dans **tracks.json**. Supprimer le fichier de pochette temporaire.

#### Playlist

Si le fichier CSV était situé dans le dossier **playlists/** : dériver un **nom de playlist** à partir du nom du fichier et ajouter la piste importée à cette playlist dans Apple Music (créer la playlist si elle n'existe pas).

### 3.3 Résumé post-import

Afficher un résumé : N importées, M en attente, K ignorées, J échouées.

S'il y a des pistes en attente : demander à l'utilisateur "Revoir les pistes en attente maintenant ?". Si oui : aller au §4 (**Pending / Review**).

---

## 4. Pistes en attente / Review

### 4.1 Modèle PendingTrack

Un **PendingTrack** stocke tout ce qui est nécessaire pour reprendre ou résoudre un import bloqué :
- **reason** : "not_found", "mismatch", "ambiguous", "youtube_failed" ou "duration_suspect".
- **csv_title**, **csv_artist**, **csv_album** : ce que l'utilisateur a demandé à l'origine.
- **track** : l'objet Track résolu (null si not_found).
- **title_mismatch**, **artist_mismatch**, **album_mismatch** : indicateurs de quel champ diverge.
- **dl_path** : chemin vers le fichier audio déjà téléchargé (pour duration_suspect).
- **actual_duration** : la durée de l'audio téléchargé.
- **candidates** : candidats YouTube restants non encore essayés.

### 4.2 Quand les PendingTrack sont créés

Les PendingTrack sont créés pendant :
- §3 **Pipeline d'import** : les cinq raisons sont possibles.
- §6 **Compléter les albums** : mêmes raisons (cf §4 review).
- §10 **Modifier une piste** : youtube_failed est possible (cf §4 review).

Les PendingTrack ne sont PAS créés pendant :
- §5 **Corriger les métadonnées** : les divergences sont présentées en multi-select, pas en pending.
- §7 **Chercher les doublons** : suppression directe, pas de pending.
- §8 **Doctor** : résolution directe, pas de pending.

### 4.3 Déroulement de la review

Collecter tous les **PendingTrack** de la session en cours.

S'il n'y en a aucun : afficher "Rien à revoir" et revenir au menu.

Pour chaque piste en attente, afficher la demande CSV originale (titre, artiste, album) et la raison avec les détails.

#### Si la raison est "not_found"

Proposer à l'utilisateur : "Chercher sur Deezer", "Fournir une URL Deezer", "Chercher directement sur YouTube", "Passer", "Supprimer définitivement".

Si l'utilisateur choisit **"Chercher sur Deezer"** : ouvrir le navigateur avec une URL de recherche Deezer (titre + artiste). Attendre que l'utilisateur colle une URL ou un ID Deezer.
- Si l'URL est une **URL de piste** (/track/{id}) : récupérer la piste sur Deezer. Demander confirmation à l'utilisateur ("C'est bien cette piste ?"). Si confirmé : reprendre la pipeline d'import à partir du **téléchargement YouTube**. Sinon : re-proposer les options.
- Si l'URL est une **URL d'album** (/album/{id}) : récupérer toutes les pistes de l'album. Si le titre CSV correspond à exactement une piste (via **is_match**) : confirmer cette piste. Sinon : afficher la liste des pistes et laisser l'utilisateur choisir. Puis reprendre la pipeline.
- Si l'URL est **invalide** : afficher une erreur et re-proposer les options.

Si **"Fournir une URL Deezer"** : même logique mais sans ouvrir le navigateur d'abord.

Si **"Chercher sur YouTube"** : ouvrir la recherche YouTube dans le navigateur. Demander une URL YouTube. Télécharger l'audio depuis cette URL. Demander : utiliser les métadonnées du CSV ou chercher les métadonnées sur Deezer ? Taguer et importer.

Si **"Passer"** : garder cette piste en attente pour la prochaine session de review.

Si **"Supprimer définitivement"** : abandonner cette piste. Elle ne sera plus reproposée.

#### Si la raison est "mismatch"

Afficher une comparaison : "Vous avez demandé : [titre_csv] de [artiste_csv]" vs "Deezer a trouvé : [titre_piste] de [artiste_piste]". Indiquer quel champ diffère (titre et/ou artiste).

Si une **URL de preview Deezer** est disponible : proposer d'écouter un aperçu de 30 secondes.

Proposer : "Accepter la version Deezer", "Chercher manuellement sur Deezer", "Rejeter".

Si **"Accepter"** : reprendre la pipeline d'import avec la piste résolue.
Si **"Chercher manuellement"** : même déroulement que l'option "Chercher sur Deezer" dans not_found.
Si **"Rejeter"** : abandonner cette piste.

#### Si la raison est "ambiguous"

Afficher tous les **candidats** avec leur titre, artiste, album et durée.

Proposer : une liste numérotée des candidats, "Chercher manuellement sur Deezer", "Passer".

Si l'utilisateur sélectionne un candidat : reprendre la pipeline avec la piste choisie.
Si **"Chercher manuellement"** : même déroulement.

#### Si la raison est "youtube_failed"

Afficher : "Aucun résultat YouTube pour l'ISRC [isrc]" avec les infos de la piste.

Proposer : "Réessayer la recherche YouTube", "Fournir une URL YouTube", "Ouvrir la recherche YouTube dans le navigateur", "Passer".

Si **"Réessayer"** : relancer la recherche YouTube. Si des candidats sont trouvés : choisir le meilleur, télécharger, reprendre l'import. Si toujours rien : afficher "Toujours aucun résultat" et re-proposer les options.

Si **"Fournir une URL"** : télécharger depuis l'URL fournie. Vérifier la durée. Si OK : taguer et importer. Si suspecte : afficher un avertissement et laisser l'utilisateur décider.

Si **"Ouvrir YouTube"** : ouvrir le navigateur avec une recherche YouTube (titre + artiste + album), puis passer au déroulement "Fournir une URL".

#### Si la raison est "duration_suspect"

Afficher : la durée attendue, la durée réelle et le ratio (seuil : 0.93–1.07).

Si le **fichier téléchargé** existe encore : proposer de jouer les 30 premières secondes. Si une **URL de preview Deezer** est disponible : proposer de jouer l'aperçu Deezer.

Proposer : "Accepter cet audio quand même", "Essayer un autre candidat YouTube", "Fournir une URL YouTube", "Rejeter".

Si **"Accepter"** : taguer et importer avec le fichier téléchargé existant.

Si **"Essayer un autre"** : s'il reste des candidats YouTube, les afficher avec leurs durées. L'utilisateur en choisit un. Télécharger, re-vérifier la durée. Si OK : taguer et importer. Si encore suspect : re-proposer ce déroulement. S'il n'y a plus de candidats : afficher "Plus de candidats" et re-proposer les options.

Si **"Fournir une URL"** : télécharger, vérifier, continuer ou re-proposer.

Si **"Rejeter"** : supprimer le fichier téléchargé et abandonner cette piste.

### 4.4 Aperçu audio

Deux fonctions d'aperçu utilisées dans plusieurs contextes de review :

**Aperçu Deezer** : si une URL de preview fraîche peut être récupérée via /track/{id}, l'utiliser. Sinon utiliser l'URL stockée. Télécharger le MP3 dans un fichier temporaire, jouer via **afplay** en arrière-plan. Retourner le handle du processus pour l'arrêter plus tard.

**Aperçu YouTube** : utiliser **yt-dlp** pour télécharger les 30 premières secondes d'une URL YouTube. Jouer via afplay.

**Arrêter l'aperçu** : terminer le processus afplay.

### 4.5 Post-review

Afficher un résumé : N acceptées, M passées, K rejetées. Revenir au menu.

---

## 5. Corriger les métadonnées

### 5.1 Traitement album par album

Scanner la bibliothèque Apple Music et grouper les pistes par **titre d'album**. Charger **tracks.json**.

Pour chaque album :

Si l'album est dans la liste des **albums ignorés** (preferences.ignored_albums) : l'ignorer et afficher "OK".

#### Résolution de l'album Deezer

**Priorité 1** — préférence sauvegardée : chercher le titre d'album (normalisé) dans **preferences.edition_choices**. Si trouvé : utiliser cet ID d'album Deezer.

**Priorité 2** — depuis tracks.json : parcourir tracks.json pour trouver une entrée dont le nom d'album normalisé correspond. Si une a un **album_id** : l'utiliser.

**Priorité 3** — recherche Deezer : chercher des albums sur Deezer par titre et artiste.
- Si **aucun candidat** n'est trouvé : essayer de chercher par le titre et l'artiste de la première piste en fallback. Si ça retourne une piste avec un album_id : sauvegarder dans les préférences et l'utiliser. Sinon : ajouter cet album à la liste des **non résolus** et continuer.
- Si **un seul candidat** correspond (ou qu'un candidat a un titre normalisé identique) : l'utiliser et sauvegarder dans les préférences.
- Si **plusieurs candidats** existent (éditions différentes : Standard, Deluxe, Remastered...) : afficher un **sélecteur** à l'utilisateur avec les noms d'édition et le nombre de pistes. Proposer aussi "Chercher manuellement", "Passer", "Ignorer définitivement". Si l'utilisateur choisit une édition : sauvegarder dans les préférences. Si "Chercher manuellement" : ouvrir le navigateur, demander une URL/ID, sauvegarder. Si "Passer" : continuer. Si "Ignorer définitivement" : ajouter à la liste des ignorés et continuer.

#### Chargement des données Deezer

Récupérer les données de l'album et la liste des pistes depuis Deezer avec l'ID résolu. En cas d'erreur : avertir et continuer à l'album suivant.

Construire les **tables de correspondance** depuis la liste des pistes Deezer : ISRC vers titre/artiste, (numéro_piste, numéro_disque) vers titre/artiste, titre normalisé vers titre/artiste, et titre normalisé vers ISRC.

#### Enrichissement automatique silencieux de tracks.json

Pour chaque piste locale de cet album : trouver l'entrée correspondante dans tracks.json (par ISRC ou par **is_match** sur titre + artiste). Si trouvée et qu'un champ est manquant (genre, durée, album_id, numéro de piste, numéro de disque, ISRC) : le compléter silencieusement. Aucune interaction utilisateur.

#### Mise à jour automatique silencieuse des pochettes

Si l'**URL de pochette Deezer** diffère de la pochette connue pour cet album, ou si un fichier audio n'a pas de pochette embarquée : télécharger la nouvelle pochette, l'embarquer dans chaque fichier audio, mettre à jour l'artwork Apple Music, et mettre à jour cover_url dans tracks.json. Aucune interaction utilisateur.

#### Détection des divergences

Comparer les métadonnées locales de l'album avec les données Deezer. Pour chaque correction potentielle, vérifier qu'elle n'a pas été déjà appliquée ou déjà refusée (via les préférences).

Si le **titre de l'album Deezer** diffère du titre local (après normalisation) : signaler "titre diffère".

Si le **genre Deezer** diffère du genre local : signaler "genre diffère".

Si l'**année Deezer** diffère de l'année locale : signaler "année diffère".

Pour chaque piste locale sans **numéro de piste** : si celui-ci peut être résolu via la table Deezer, signaler "numéros de piste manquants".

Pour chaque piste locale avec un mauvais **numéro de disque** (et l'album a plusieurs disques) : signaler "numéros de disque incorrects".

Pour chaque piste locale sans **total de pistes** (et le total Deezer est supérieur à 1) : signaler "total manquant".

Pour chaque piste locale dont le **titre** diffère du titre Deezer résolu : signaler une divergence de titre de piste.

Pour chaque piste locale dont l'**artiste** diffère de l'artiste Deezer résolu : signaler une divergence d'artiste de piste.

Pour chaque piste locale sans **ISRC** : si Deezer a un ISRC pour cette position de piste, l'**appliquer automatiquement et silencieusement** (écrire dans le fichier audio et sauvegarder dans les préférences). Aucune interaction utilisateur.

Si l'album a **plusieurs artistes** et qu'aucun **artiste d'album** n'est défini : s'il y a un artiste d'album sauvegardé dans les préférences ou un artiste d'album Deezer, signaler "artiste d'album nécessaire".

#### Aucune divergence

Si aucun signalement n'a été levé : afficher "OK" pour cet album et continuer.

#### Confirmation interactive

Construire une **liste multi-select** à partir de tous les signalements. Chaque divergence est une case à cocher (par défaut : cochée). Ajouter des options d'action : "Aperçu pochette", "Voir sur Deezer", "Passer cet album", "Ignorer définitivement".

Si l'utilisateur sélectionne **"Passer"** : sauvegarder les refus dans les préférences et continuer.

Si **"Ignorer définitivement"** : ajouter l'album à la liste des ignorés et continuer.

Si **"Aperçu pochette"** : télécharger la pochette et l'ouvrir dans Aperçu (Preview.app), puis re-proposer le multi-select.

Si **"Voir sur Deezer"** : ouvrir le navigateur avec une recherche Deezer. Si l'utilisateur fournit une URL/ID d'album différent : recharger les données Deezer, re-détecter les divergences et re-proposer le multi-select.

Sinon : appliquer les corrections sélectionnées.

#### Application des corrections sélectionnées

Sauvegarder les corrections acceptées dans les préférences. Sauvegarder les refus pour les éléments non cochés.

Pour chaque piste locale de l'album : construire un dictionnaire de champs Apple Music à mettre à jour selon les corrections sélectionnées (titre d'album, genre, année, numéro de piste, numéro de disque, total pistes, artiste d'album). Si des champs doivent être mis à jour : appeler **apple.update_track**. Si une correction de titre ou d'artiste de piste individuelle a été sélectionnée : mettre à jour ce champ spécifique. Patcher l'entrée correspondante dans tracks.json.

Si **"artiste d'album"** a été signalé : si une valeur sauvegardée existe dans les préférences, l'utiliser. Sinon : afficher la liste des artistes uniques de l'album, demander "Définir [artiste_album_deezer] comme artiste d'album ? (oui / non / saisir un nom personnalisé)". Sauvegarder le choix dans les préférences.

### 5.2 Post-traitement

Appliquer toutes les mises à jour de pochettes en attente (télécharger, embarquer, définir l'artwork). Sauvegarder **tracks.json**. Sauvegarder le cache Deezer (**albums.json**).

S'il y a des albums **non résolus** : les lister et proposer "Chercher sur Deezer", "Chercher sur MusicBrainz", "Passer", "Ignorer". Ce n'est PAS un pending/review — c'est une résolution interactive directe.

Afficher un résumé : N albums mis à jour, M non résolus, K déjà OK.

---

## 6. Compléter les albums

Scanner la bibliothèque Apple Music et grouper par album.

Pour chaque album : résoudre l'ID d'album Deezer avec la même logique que §5.1 (priorité 1 : préférences, priorité 2 : tracks.json, priorité 3 : recherche Deezer avec sélecteur d'édition si nécessaire).

Si l'album n'a pas pu être résolu : continuer au suivant.

Récupérer la liste des pistes Deezer pour cet album. Calculer l'ensemble des positions locales (numéro_piste, numéro_disque) et l'ensemble des positions Deezer. Les positions manquantes sont celles présentes chez Deezer mais pas localement.

Pour chaque piste Deezer à une position manquante : vérifier qu'elle n'est pas déjà présente localement par titre (via **is_match** sur le titre). Si elle l'est : la retirer des manquantes.

Si aucune piste ne manque : continuer (album complet).

Sinon : afficher "Album [X] : [local]/[total] pistes. Importer [N] manquantes ?".

Si l'utilisateur confirme : pour chaque piste manquante, lancer la pipeline d'import complète à partir de l'**enrichissement de la pochette** (§3.2). Si une étape échoue : gérer via review (cf §4).

---

## 7. Chercher les doublons

Scanner la bibliothèque Apple Music.

Grouper les pistes par une clé composée de **normalize**(titre) + "||" + **normalize**(**first_artist**(artiste)).

Ensuite fusionner tous les groupes qui partagent au moins un **ISRC** (une piste avec des métadonnées légèrement différentes mais le même ISRC doit être dans le même groupe).

Ne garder que les groupes avec **2 entrées ou plus**.

Si aucun doublon n'est trouvé : afficher "Aucun doublon trouvé" et revenir.

Pour chaque groupe de doublons : afficher toutes les versions avec leurs métadonnées.

Calculer automatiquement la **meilleure version**. Ordre de priorité : a un ISRC > durée la plus longue > a un chemin de fichier > importée en premier.

Afficher : "Garder la version [meilleure] ? Supprimer les autres ?".

Si l'utilisateur confirme : supprimer les autres versions d'Apple Music et les retirer de tracks.json.

Si l'utilisateur choisit une autre version : garder celle-là, supprimer le reste.

Si l'utilisateur passe : continuer au groupe suivant.

---

## 8. Doctor

Charger **tracks.json** et scanner la bibliothèque Apple Music.

Détecter trois types de divergences :

**Type 1 — done_missing** : entrées dans tracks.json avec status "done" et un apple_id qui n'existe pas dans la bibliothèque Apple Music.

**Type 2 — unknown_track** : pistes dans la bibliothèque Apple Music dont l'apple_id n'est référencé dans aucune entrée de tracks.json.

**Type 3 — baseline_missing** : entrées baseline dans tracks.json dont l'apple_id n'existe plus dans la bibliothèque.

Si aucune divergence : afficher "Tout est cohérent" et revenir.

Afficher un résumé : N done_missing, M unknown, K baseline_missing.

Proposer : "Résolution automatique", "Review manuelle", "Passer".

Si **"Résolution automatique"** :
- Pour chaque **done_missing** : essayer de retrouver la piste dans la bibliothèque par **is_match**(titre + artiste). Si trouvée : mettre à jour l'apple_id dans tracks.json. Sinon : supprimer l'entrée ou la marquer pour réimport.
- Pour chaque **unknown_track** : essayer de retrouver dans tracks.json par ISRC ou par **is_match**(titre + artiste). Si trouvée : mettre à jour l'apple_id. Sinon : l'ajouter comme nouvelle entrée baseline.
- Pour chaque **baseline_missing** : supprimer de tracks.json (l'utilisateur a supprimé la piste d'Apple Music).

Si **"Review manuelle"** : pour chaque divergence, afficher les détails et laisser l'utilisateur choisir : "Corriger", "Supprimer l'entrée", "Réimporter", "Passer". Si "Réimporter" : lancer la pipeline d'import complète ; si elle échoue, gérer via review (cf §4).

---

## 9. Exporter

Récupérer la liste des playlists Apple Music avec leur nombre de pistes.

Si aucune playlist n'existe : afficher "Aucune playlist" et revenir.

Laisser l'utilisateur choisir une ou plusieurs playlists.

Pour chaque playlist sélectionnée : récupérer toutes les pistes et les sauvegarder en CSV dans le dossier playlists/, avec les colonnes : titre, artiste, album, genre, année, durée, numéro_piste, numéro_disque, artiste_album, isrc.

Afficher "N playlist(s) exportée(s)".

---

## 10. Modifier une piste

Laisser l'utilisateur chercher dans la bibliothèque (recherche en direct). L'utilisateur sélectionne une piste.

Proposer : "Changer d'édition d'album", "Re-télécharger l'audio", "Remplacer l'audio depuis une URL".

Si **"Changer d'édition"** : chercher l'album sur Deezer, afficher les éditions disponibles (Standard, Deluxe, Remastered...). Si l'utilisateur en choisit une : supprimer l'ancienne piste d'Apple Music, importer la nouvelle version via la pipeline complète, mettre à jour tracks.json. Si l'import échoue : gérer via review (cf §4).

Si **"Re-télécharger"** : re-chercher sur YouTube par ISRC, télécharger le meilleur candidat, re-taguer avec les métadonnées existantes, ré-importer dans Apple Music, mettre à jour l'apple_id dans tracks.json. Si YouTube échoue : gérer via review (cf §4).

Si **"Remplacer depuis une URL"** : demander une URL YouTube, télécharger depuis cette URL, re-taguer avec les métadonnées existantes, ré-importer, mettre à jour l'apple_id.

---

## 11. Snapshot

Trouver toutes les entrées dans tracks.json avec origin **"imported"** et status **"done"**.

S'il n'y en a aucune : afficher "Rien à archiver" et revenir.

Demander : "Promouvoir [N] pistes importées en baseline ?".

Si l'utilisateur confirme : archiver le tracks.json actuel (garder les 5 dernières archives, supprimer les plus anciennes). Passer toutes les entrées importées+done à origin **"baseline"**. Sauvegarder.

---

## 12. Supprimer

Laisser l'utilisateur chercher dans la bibliothèque. L'utilisateur sélectionne une piste ou un album.

Si une **piste unique** est sélectionnée : demander confirmation. Si confirmé : supprimer d'Apple Music et retirer de tracks.json.

Si un **album** est sélectionné (N pistes) : demander une confirmation renforcée. Si confirmé : supprimer les N pistes d'Apple Music et les retirer de tracks.json.

---

## 13. Maintenance

**"Réinitialiser les pistes échouées"** : passer le status à null et vider fail_reason pour toutes les entrées avec status "failed". Sauvegarder.

**"Vider les préférences"** : écraser preferences.json avec un objet vide.

**"Changer le dossier racine"** : ouvrir le sélecteur de dossier Finder, déplacer tous les fichiers du projet vers le nouvel emplacement, mettre à jour settings.json.

**"Annuler tous les imports"** : demander une confirmation renforcée. Si confirmé : supprimer toutes les pistes avec origin "imported" d'Apple Music, les retirer de tracks.json.

**"Désinstaller"** : demander une double confirmation. Si confirmé : supprimer toutes les pistes importées d'Apple Music, supprimer le dossier de données, supprimer les paramètres.

---

## Module de matching (normalize.py)

### Deux fonctions, deux contextes

**is_same(a, b, kind)** retourne un booléen. C'est le mode **strict** : "Est-ce le même morceau / artiste / album ?". Normalise les deux chaînes (minuscules, suppression des accents, suppression de la ponctuation, compression des espaces) et compare. Sensible aux versions : "Song (Remastered)" est considéré identique à "Song" (marqueur non-distinguant supprimé), mais "Song (Live)" n'est PAS identique à "Song" (marqueur distinguant préservé). Sensible aux artistes : "The Beatles" est identique à "Beatles" (article supprimé), mais "Dave" n'est PAS identique à "Dave Brubeck" (mot de contenu différent). Utilisé par : dédup, détection de mismatch post-résolution, matching de pistes dans un album résolu (fix-metadata), groupement de doublons.

**is_match(a, b, kind, threshold)** retourne un booléen. C'est le mode **recherche** : "Est-ce que a ressemble à b ?". Applique la pipeline de préparation complète (suppression des suffixes entre parenthèses, suppression des suffixes après tiret, canonicalisation des volumes, conversion des chiffres romains, normalisation) puis score avec **rapidfuzz**. Utilise des seuils par défaut par type (title = 85, artist = 90, album = 85). Utilisé par : filtrage des résultats de recherche Deezer, recherche dans la bibliothèque, validation MusicBrainz, détection de pistes manquantes dans les albums, résolution automatique du doctor.

**match_score(a, b, kind)** retourne un flottant 0–100. Le score de similarité brut. Utilisé pour l'affichage UI, le débogage et l'optimisation des seuils.

### Qui appelle quoi

**Dédup** : is_same(titre) + is_same(artiste) — strict.

**Détection de mismatch à l'import** : is_same(titre) + is_same(artiste) — strict.

**Filtrage de recherche Deezer** : is_match(titre) + is_match(artiste) — recherche.

**Validation MusicBrainz** : is_match(titre) + is_match(artiste) — recherche.

**Résolution d'album fix-metadata** : normalize() comparaison exacte — strict.

**Matching de pistes fix-metadata** : normalize() recherche exacte dans la table Deezer — strict.

**Albums incomplets (pistes manquantes)** : is_match(titre) — recherche.

**Doublons** : normalize() + first_artist() comme clé de groupement — strict.

**Recherche bibliothèque** : is_match(titre) + is_match(artiste) — recherche.

**Doctor résolution automatique** : is_match(titre) + is_match(artiste) — recherche.

---

## Ordre de développement

**Phase 1** : core/ (normalize, models, io, config, logger) avec tests.

**Phase 2** : pipeline/ (dedup, pending, helpers) avec tests.

**Phase 3** : providers/ (deezer, itunes, musicbrainz, resolver) avec tests.

**Phase 4** : services/ (youtube, tagger, apple) avec tests.

**Phase 5** : pipeline/importer.py avec tests.

**Phase 6** : options/ (import, review, fix, complete, doublons, doctor, export, modifier, snapshot, supprimer, maintenance) avec tests.

**Phase 7** : ui/ (Textual).

Règle : tests AVANT le code pour chaque phase. Chaque condition de cette spécification est un cas de test.
