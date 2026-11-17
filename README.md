<p align="center">
  <img src="assets/icon.png" alt="TikVault" width="96" />
</p>

<h1 align="center">TikVault</h1>

<p align="center">
  <strong>Application desktop de téléchargement massif de vidéos TikTok</strong><br/>
  Python 3.13 · PySide6 / Qt 6 · yt-dlp · asyncio · SQLAlchemy 2.0 async · Pydantic v2
</p>

---

## Sommaire

1. [Présentation](#présentation)
2. [Fonctionnalités](#fonctionnalités)
3. [Architecture](#architecture)
4. [Installation (uv)](#installation-uv)
5. [Lancement](#lancement)
6. [Guide d'utilisation page par page](#guide-dutilisation-page-par-page)
7. [Mode ligne de commande](#mode-ligne-de-commande)
8. [Qualité : lint, typage, tests](#qualité--lint-typage-tests)
9. [Fabriquer l'exécutable (.exe)](#fabriquer-lexécutable-exe)
10. [Dépannage](#dépannage)
11. [Idées d'améliorations futures](#idées-daméliorations-futures)
12. [Avertissement légal](#avertissement-légal)

---

## Présentation

**TikVault** est une application de bureau complète pour télécharger des vidéos
TikTok en masse : collez des liens en vrac, importez un fichier, aspirez un
profil `@username`, un hashtag, un son ou une collection — le moteur s'occupe
du reste : file d'attente persistante, reprise après crash, relances avec
backoff exponentiel, rotation de proxies et d'User-Agents, anti-doublon par
hash de contenu, extraction audio MP3/M4A avec miniature et métadonnées
intégrées.

Le moteur (asyncio pur, zéro dépendance Qt) et l'interface (PySide6) sont
découplés par un bus d'événements : l'application reste fluide même avec des
centaines de téléchargements en file.

## Fonctionnalités

### Sources
- **Collage en vrac** : détection automatique des URL dans du texte libre,
  nettoyage des paramètres de pistage, dédoublonnage (y compris les liens
  courts `vm.tiktok.com` / `vt.tiktok.com`).
- **Import de fichier** `.txt` ou `.csv`.
- **Profil complet** `@username`, **hashtag** `#defi`, **son/musique**,
  **playlist/collection**.
- **Veille de comptes** : surveillance périodique d'un `@compte`, les nouvelles
  vidéos sont téléchargées automatiquement.

### Téléchargement
- Meilleure qualité disponible, **sans filigrane** quand la plateforme le permet.
- **Audio seul** : MP3, M4A, Opus, FLAC, WAV ou AAC, avec miniature en
  pochette et métadonnées (titre, auteur) intégrées.
- **Filtres** : plage de dates, nombre maximum de vidéos, durée min/max,
  nombre de vues minimum.
- Concurrence réglable **1 à 10** téléchargements simultanés + **limitation de
  débit** (Ko/s).
- **Pause / reprise / annulation** par vidéo ou globales — l'état survit à la
  fermeture de l'application, et les téléchargements interrompus **reprennent
  automatiquement** au démarrage suivant.

### Robustesse
- Relances avec **backoff exponentiel + jitter décorrélé** (4 tentatives par
  défaut).
- **Rotation de proxies** (liste configurable, quarantaine automatique des
  proxies défaillants) et **rotation d'User-Agents**.
- **Cookies** : fichier `cookies.txt` ou import direct depuis Chrome/Firefox.
- Détection claire des échecs : vidéo **privée**, **supprimée**, **restreinte
  par pays**, **429 / limitation de débit** — chaque cas affiche un message en
  français et décide s'il est pertinent de réessayer.
- **Architecture à plugins** : si yt-dlp échoue, un extracteur de secours
  (lecture directe de la page web TikTok) prend le relais ; d'autres moteurs
  peuvent être ajoutés en implémentant l'interface `VideoExtractor`.
- **Mise à jour de yt-dlp intégrée** : vérification et application depuis la
  page Réglages, sans réinstaller l'application.

### Fichiers
- **Modèle de nom personnalisable** avec jetons :
  `{username}`, `{uploader_id}`, `{id}`, `{title}`, `{date}`, `{datetime}`,
  `{year}`, `{index}`, `{duration}`, `{views}`, `{source}`, `{ext}` —
  défaut : `{username}/{date}_{id}_{title}.mp4`.
- **Anti-doublon par hash de contenu** (BLAKE2) : un même fichier n'est jamais
  stocké deux fois, même sous un autre nom.
- Dossier de destination configurable, espace disque surveillé en continu.

### Interface
- Thème **sombre / clair**, couleur d'accent personnalisable, icônes
  vectorielles dessinées par le programme (aucune image externe).
- Tableau de bord temps réel : vitesse totale, réussites/échecs, espace
  disque, ETA, journal d'activité.
- File d'attente réordonnable par **glisser-déposer**, aperçu **miniature**,
  progression **globale et par vidéo** animée.
- Historique avec **recherche et filtres**, **export CSV** (séparateur `;`,
  encodage compatible Excel).
- **Notifications système** en fin de lot et icône de zone de notification.

## Architecture

Architecture hexagonale : le domaine ne dépend de rien, les services ne
dépendent que du domaine, l'infrastructure implémente les ports, l'interface
ne parle au moteur qu'à travers des événements.

```
src/tikvault/
├── __main__.py              # point d'entrée : GUI ou CLI (--headless)
├── app.py                   # conteneur d'injection de dépendances
├── settings.py              # AppSettings (Pydantic v2), chargement/sauvegarde JSON
├── domain/                  # cœur métier, aucune dépendance externe
│   ├── models.py            #   DownloadTask, VideoMeta, états, statistiques
│   ├── errors.py            #   hiérarchie d'erreurs + classify_error()
│   ├── events.py            #   EventBus et événements du moteur
│   ├── ports.py             #   interfaces (VideoExtractor, Notifier, …)
│   └── filters.py           #   filtres date/durée/vues + quotas
├── services/                # logique applicative (asyncio pur)
│   ├── url_parser.py        #   détection/nettoyage/classement des sources
│   ├── filename.py          #   rendu du modèle de nom de fichier
│   ├── retry.py             #   backoff exponentiel + jitter décorrélé
│   ├── dedupe.py            #   hash de contenu BLAKE2
│   ├── disk.py              #   espace disque, formats lisibles (3,2 Go)
│   ├── progress.py          #   agrégation vitesse/ETA
│   ├── download_service.py  #   orchestration d'un téléchargement
│   ├── queue_service.py     #   file + workers (semaphore), pause/reprise
│   ├── source_resolver.py   #   profil/hashtag/son → liste de vidéos
│   ├── watch_service.py     #   veille périodique de comptes
│   └── report_service.py    #   export CSV
├── infrastructure/          # adaptateurs concrets
│   ├── db/                  #   SQLAlchemy 2.0 async + SQLite (WAL)
│   ├── extractors/          #   yt-dlp (prioritaire) + secours web + registre
│   ├── proxy_pool.py        #   rotation round-robin + quarantaine
│   ├── user_agents.py       #   rotation d'User-Agents
│   ├── cookies.py           #   cookies.txt / navigateur
│   ├── updater.py           #   mise à jour portable de yt-dlp
│   ├── thumbnails.py        #   cache local de miniatures
│   ├── notifier.py          #   notifications système
│   ├── paths.py             #   emplacements portables (platformdirs)
│   └── logging_setup.py     #   structlog (console + fichier)
└── ui/                      # PySide6, ne touche jamais le moteur directement
    ├── engine_bridge.py     #   pont signaux Qt ↔ boucle asyncio dédiée
    ├── main_window.py       #   navigation, notifications, raccourcis
    ├── theme.py, icons.py   #   thèmes sombre/clair, icônes vectorielles
    ├── tray.py              #   icône de zone de notification
    ├── widgets/             #   progression animée, cartes, miniatures, dialogue d'ajout
    └── pages/               #   Tableau de bord, File, Veille, Historique, Réglages
tests/                       # 262 tests pytest sur le moteur
build/tikvault.spec          # recette PyInstaller
scripts/make_icon.py         # régénération des icônes (sans dépendance)
```

Principes appliqués : type hints partout (mypy **strict**), dataclasses et
modèles Pydantic, injection de dépendances via `app.Container`, événements
pour découpler moteur et interface, commentaires en français.

## Installation (uv)

Prérequis : [uv](https://docs.astral.sh/uv/) (gestionnaire de projet Python).
Aucun `requirements.txt`, aucun pip manuel : tout passe par `pyproject.toml`.

```console
# 1. Installer uv (une seule fois)
#    Windows (PowerShell) :
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
#    Linux / macOS :
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Cloner puis installer le projet (Python 3.13 téléchargé automatiquement)
git clone <votre-dépôt> tikvault && cd tikvault
uv sync                   # dépendances d'exécution + outils de dev
```

> **FFmpeg** (recommandé) : nécessaire pour la conversion audio MP3/M4A et
> l'intégration des miniatures. `winget install ffmpeg` (Windows),
> `brew install ffmpeg` (macOS), `sudo apt install ffmpeg` (Debian/Ubuntu).

## Lancement

```console
uv run tikvault            # interface graphique
make run                   # équivalent via le Makefile
```

Premier démarrage : la base SQLite, le dossier de configuration et le cache
sont créés automatiquement dans les emplacements standards du système
(`%APPDATA%/TikVault` sous Windows, `~/.local/share/TikVault` sous Linux,
`~/Library/Application Support/TikVault` sous macOS). Les vidéos vont par
défaut dans `~/TikVault`.

## Guide d'utilisation page par page

### 1 · Tableau de bord
Vue d'ensemble en temps réel : vitesse totale, nombre de réussites et
d'échecs, espace disque restant, estimation du temps restant (ETA), barre de
progression globale animée et journal d'activité. Les boutons **Ajouter**,
**Tout mettre en pause**, **Tout reprendre** et **Tout annuler** pilotent le
lot entier. Une notification système s'affiche à la fin de chaque lot.

### 2 · File d'attente
Chaque téléchargement a sa carte : miniature, titre, auteur, état coloré,
progression, vitesse et ETA individuels, boutons pause/reprise/annulation.
Réordonnez la file par **glisser-déposer** : l'ordre est respecté par les
workers. Le bouton **Relancer les échecs** remet en file toutes les vidéos en
erreur récupérable.

**Ajout de sources** (bouton « Ajouter » ou `Ctrl+N`) : collez n'importe quel
texte contenant des liens — l'application extrait, nettoie et dédoublonne les
URL ; tapez `@compte` pour un profil entier, `#hashtag` pour un défi, ou
importez un fichier `.txt`/`.csv`. Les options rapides (audio seul, nombre
maximum, dossier) s'appliquent au lot ajouté.

### 3 · Veille de comptes
Ajoutez un `@compte` à surveiller : à intervalle régulier (réglable),
TikVault compare les vidéos publiées avec l'historique et télécharge
automatiquement les nouveautés. Chaque compte peut être suspendu, vérifié
immédiatement (« Vérifier maintenant ») ou retiré.

### 4 · Historique
Toutes les tâches passées, avec recherche plein texte et filtres par état
(réussi, échoué, annulé…). **Export CSV** en un clic : fichier `;`-séparé,
encodé UTF-8 avec BOM, ouvrable directement dans Excel. « Nettoyer les
terminés » retire les entrées abouties sans toucher aux fichiers.

### 5 · Réglages
- **Téléchargement** : dossier de destination, modèle de nom de fichier,
  qualité maximale, sans filigrane, audio seul + format, miniature et
  métadonnées intégrées.
- **Performances** : téléchargements simultanés (1–10), limitation de débit,
  nombre de tentatives, délai réseau.
- **Filtres par défaut** : dates, durées, vues minimum, maximum par source.
- **Réseau** : liste de proxies (un par ligne) avec rotation automatique,
  rotation d'User-Agents, fichier `cookies.txt` ou import navigateur
  (Chrome / Firefox) pour les contenus nécessitant une session.
- **Apparence** : thème sombre/clair, couleur d'accent, notifications,
  démarrage réduit, confirmation de fermeture.
- **Mises à jour** : vérifier la version de yt-dlp et **« Mettre à jour
  maintenant »** sans réinstaller l'application (mise à jour portable,
  prise en compte au redémarrage).

## Mode ligne de commande

Le même moteur, sans fenêtre — pratique pour l'automatisation :

```console
uv run tikvault --headless "https://www.tiktok.com/@user/video/1234" --wait
uv run tikvault --headless "@compte" --max-items 50 --audio-only --audio-format mp3 --wait
uv run tikvault --headless --file liens.txt --concurrency 5 --rate-limit 2048 --wait
uv run tikvault --export rapport.csv        # export CSV de l'historique
uv run tikvault --check-update              # version de yt-dlp disponible
uv run tikvault --update                    # mise à jour de yt-dlp puis sortie
uv run tikvault --portable ./donnees        # tout stocker dans un dossier
```

`uv run tikvault --help` liste toutes les options (proxies, cookies, modèle de
nom, filtres…).

## Qualité : lint, typage, tests

```console
make lint        # ruff check + ruff format --check (0 erreur)
make typecheck   # mypy strict (src + tests, 0 erreur)
make test        # pytest : 262 tests du moteur (file, retry, filtres, BD, …)
make check       # les trois d'un coup
```

Les tests couvrent le moteur de téléchargement de bout en bout avec de faux
extracteurs : file d'attente et workers, pause/reprise/annulation, reprise
après crash, relances et backoff, anti-doublon, filtres, analyse d'URL,
nommage, persistance SQLite, export CSV, veille de comptes, rotation de
proxies, classification d'erreurs.

## Fabriquer l'exécutable (.exe)

```console
make exe
# ou, à la main :
uv sync --extra build
uv run pyinstaller build/tikvault.spec --noconfirm
```

Le binaire autonome est produit dans `dist/` (`TikVault.exe` sous Windows —
lancez la commande **sur** Windows pour obtenir un `.exe`, PyInstaller ne
compile pas en croisé). La recette embarque les icônes, collecte les
extracteurs yt-dlp chargés dynamiquement et exclut les modules inutiles
(tkinter, QtWebEngine…) pour limiter la taille. UPX est volontairement
désactivé pour éviter les faux positifs antivirus.

Icônes : `make icon` régénère `assets/icon.png` et `assets/icon.ico` via
`scripts/make_icon.py` (script autonome, sans dépendance).

## Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| « Vidéo privée » / « supprimée » | Contenu inaccessible | Rien à faire ; l'erreur n'est pas retentée |
| « Restriction de pays » | Géoblocage | Configurer un proxy du bon pays (Réglages → Réseau) |
| Erreurs 429 en rafale | Limitation de débit TikTok | Réduire la concurrence, activer la rotation de proxies, ajouter un débit maximum |
| Audio non converti en MP3 | FFmpeg absent | Installer FFmpeg et le mettre dans le `PATH` |
| Contenu nécessitant une connexion | Session requise | Importer les cookies du navigateur (Réglages → Réseau) |
| Beaucoup d'échecs soudains | yt-dlp obsolète | Réglages → Mises à jour → « Mettre à jour maintenant » |
| Linux : `libGL.so.1` manquante | Dépendance Qt | `sudo apt install libgl1 libegl1` |

## Idées d'améliorations futures

- **Planificateur** : télécharger la nuit, limiter la bande passante par plage horaire.
- **Prévisualisation vidéo** intégrée (QtMultimedia) depuis la file ou l'historique.
- **Transcription automatique** des vidéos (whisper.cpp) et recherche plein texte dans les sous-titres.
- **Synchronisation bidirectionnelle** : supprimer localement ce qui a disparu du compte, ou archiver avant suppression.
- **Autres plateformes** : l'architecture à plugins permet d'ajouter Instagram Reels ou YouTube Shorts en implémentant un `VideoExtractor`.
- **Mode serveur** : API REST + interface web pour piloter TikVault depuis un NAS.
- **Profils de configuration** par projet (journalisme, archivage, veille) avec dossiers et filtres distincts.
- **Statistiques enrichies** : graphiques d'évolution (vues, fréquence de publication) des comptes suivis.
- **Internationalisation** (Qt Linguist) : anglais, espagnol…
- **Installeur signé** (MSIX/Inno Setup) et mises à jour automatiques de l'application elle-même.

## Avertissement légal

TikVault est un outil d'archivage personnel. Respectez les conditions
d'utilisation de TikTok ainsi que le droit d'auteur : ne téléchargez que des
contenus que vous êtes autorisé à conserver, et ne republiez jamais le travail
d'un créateur sans son accord.

---

Licence MIT — voir `pyproject.toml`.
