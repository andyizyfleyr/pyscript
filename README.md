<p align="center">
  <img src="assets/icon.png" alt="TikVault" width="96" />
</p>

<h1 align="center">TikVault</h1>

<p align="center">
  <strong>Application desktop de téléchargement massif de vidéos TikTok — v2 « coller → télécharger »</strong><br/>
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
TikTok en masse. La **v2** met la simplicité au premier plan : un seul écran
pour l'essentiel — collez vos liens (ou `@comptes`, `#hashtags`), choisissez
Vidéo ou Audio MP3, cliquez **Télécharger**. Le moteur s'occupe du reste : file d'attente persistante, reprise après crash, relances avec
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

### Interface (v2, pensée pour la simplicité)
- **Un seul écran pour tout le flux principal** : zone de collage, choix
  Vidéo / Audio MP3, bouton Télécharger, progression globale résumée en une
  ligne, liste des téléchargements et journal replié.
- **4 pages seulement** : Télécharger, Veille de comptes, Historique, Réglages.
- Réglages « l'essentiel d'abord » : les options avancées (proxies, cookies,
  modèle de nom, filtres…) sont repliées mais toujours là.
- Thème **sombre / clair**, couleur d'accent personnalisable, icônes
  vectorielles dessinées par le programme (aucune image externe).
- Liste réordonnable par **glisser-déposer**, aperçu **miniature**,
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
    ├── widgets/             #   progression animée, sections repliables, miniatures
    └── pages/               #   Télécharger (tout-en-un), Veille, Historique, Réglages
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

### 1 · Télécharger — l'écran unique du quotidien
Tout se passe ici, en trois gestes :

1. **Collez** vos liens dans la grande zone (plusieurs liens à la fois, même
   noyés dans du texte — l'application extrait, nettoie et dédoublonne). Les
   `@comptes` (profil entier) et `#hashtags` fonctionnent aussi.
2. Choisissez **Vidéo** ou **Audio (MP3)**.
3. Cliquez **Télécharger**.

La barre de progression globale et son résumé d'une ligne (terminées, échecs,
vitesse, temps restant) suivent le lot en temps réel. En dessous, chaque vidéo
a sa ligne : miniature, état coloré, progression, boutons pause/annuler. On
peut réordonner par glisser-déposer, tout mettre en pause, **relancer les
échecs** en un clic.

- **Importer un fichier…** : télécharge tous les liens d'un `.txt`/`.csv`.
- **Options avancées…** : filtres par lot (plage de dates, vues minimum,
  nombre maximum de vidéos).
- **Journal d'activité** (replié en bas) : le détail des événements du moteur.

### 2 · Veille de comptes
Ajoutez un `@compte` : à intervalle régulier, TikVault détecte ses nouvelles
vidéos et les télécharge automatiquement. Chaque compte peut être suspendu,
vérifié immédiatement ou retiré.

### 3 · Historique
Toutes les tâches passées, avec recherche plein texte et filtres par état.
**Export CSV** en un clic (fichier `;`-séparé, UTF-8 BOM, ouvrable dans
Excel). « Nettoyer les terminés » retire les entrées abouties sans toucher
aux fichiers.

### 4 · Réglages — l'essentiel d'abord
En haut, ce qui sert tous les jours : dossier de destination, audio par
défaut + format, téléchargements simultanés, thème, notifications, et les
mises à jour de yt-dlp (« Mettre à jour maintenant »).

En dessous, **cliquez sur un titre pour déplier** les options avancées :
- *Nom des fichiers et qualité* : modèle de nom personnalisable, sans
  filigrane, métadonnées/miniature intégrées ;
- *Filtres appliqués par défaut* : dates, durées, vues, maximum par source ;
- *Réseau* : proxies avec rotation, cookies navigateur (Chrome/Firefox) ;
- *Performance et relances* : limitation de débit, nombre de tentatives ;
- *Comportement de la fenêtre*.

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

## Fabriquer l'exécutable (.exe) et l'installeur Windows

### Étape 1 — Embarquer FFmpeg (recommandé, une seule fois)

```powershell
uv run python scripts/fetch_ffmpeg.py
```

Télécharge les builds officiels FFmpeg (~170 Mo) et place `ffmpeg.exe` +
`ffprobe.exe` dans `build/ffmpeg/`. La recette PyInstaller les détecte et les
glisse dans le `.exe` : **l'utilisateur final n'a rien à installer** —
extraction MP3, miniatures et métadonnées fonctionnent d'emblée. (Sans cette
étape, le `.exe` est fabriqué quand même, mais exige un FFmpeg système.)

### Étape 2 — Fabriquer l'exécutable

```powershell
uv sync --extra build
uv run pyinstaller build/tikvault.spec --noconfirm
```

→ **`dist\TikVault.exe`** : un fichier unique, lançable par double-clic sur
n'importe quel Windows 10/11 64 bits, sans Python ni dépendance. (Lancez la
même commande sur macOS ou Linux pour un binaire natif de ces systèmes —
PyInstaller ne compile pas en croisé.)

### Étape 3 — Créer l'installeur (Inno Setup)

```powershell
winget install JRSoftware.InnoSetup
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss
```

→ **`dist\TikVault-2.0.0-installeur.exe`** : un vrai assistant d'installation
en français qui :
- installe TikVault **sans droits administrateur** (par utilisateur) ;
- crée les raccourcis **menu Démarrer** et **Bureau** (optionnel) ;
- propose de lancer l'application à la fin ;
- apparaît dans « Applications installées » avec **désinstallation propre**
  (vos téléchargements et réglages ne sont jamais supprimés) ;
- gère les mises à jour : réinstaller une nouvelle version par-dessus suffit.

> **SmartScreen** : l'exécutable n'étant pas signé numériquement, Windows peut
> afficher « Windows a protégé votre ordinateur » au premier lancement.
> Cliquez *Informations complémentaires* → *Exécuter quand même*.

Raccourcis `Makefile` : `make ffmpeg`, `make exe`, `make installer`.

Icônes : `make icon` régénère `assets/icon.png` et `assets/icon.ico` via
`scripts/make_icon.py` (script autonome, sans dépendance).

## Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| « Vidéo privée » / « supprimée » | Contenu inaccessible | Rien à faire ; l'erreur n'est pas retentée |
| « Restriction de pays » | Géoblocage | Configurer un proxy du bon pays (Réglages → Réseau) |
| Erreurs 429 en rafale | Limitation de débit TikTok | Réduire la concurrence, activer la rotation de proxies, ajouter un débit maximum |
| Audio non converti en MP3 | FFmpeg absent | Installer FFmpeg — inutile avec le `.exe` qui l'embarque |
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
