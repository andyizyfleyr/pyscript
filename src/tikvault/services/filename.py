"""Construction des chemins de destination à partir d'un modèle personnalisable.

Modèle par défaut : ``{username}/{date}_{id}_{title}.mp4``

Jetons disponibles : ``{username}`` ``{uploader_id}`` ``{id}`` ``{title}`` ``{date}``
``{datetime}`` ``{year}`` ``{index}`` ``{duration}`` ``{views}`` ``{source}`` ``{ext}``.

Le rendu est **sécurisé** : aucun jeton ne peut faire sortir du dossier de destination
(pas de ``..``, pas de caractère réservé Windows/macOS, longueur bornée).
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from tikvault.domain.errors import ConfigError
from tikvault.domain.models import DownloadOptions, VideoMeta

# Caractères interdits sur au moins un système de fichiers courant.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Noms réservés sous Windows (peu importe l'extension).
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_COMPONENT = 120  # laisse de la marge pour le dossier racine (limite NTFS : 255)
_MAX_TOTAL = 220

SUPPORTED_TOKENS = frozenset(
    {
        "username",
        "uploader_id",
        "id",
        "title",
        "date",
        "datetime",
        "year",
        "index",
        "duration",
        "views",
        "source",
        "ext",
    }
)

_TOKEN_RE = re.compile(r"\{(\w+)\}")


def sanitize_component(value: str, *, fallback: str = "sans-titre") -> str:
    """Rend une portion de chemin sûre pour tous les systèmes d'exploitation.

    Les caractères interdits deviennent des espaces (puis sont réduits) : un titre
    « Recette / été » donne « Recette été » plutôt que « Recette _ été ».
    """
    cleaned = _ILLEGAL.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = cleaned.replace("\u200b", "")  # espaces invisibles
    if len(cleaned) > _MAX_COMPONENT:
        cleaned = cleaned[:_MAX_COMPONENT].rstrip(" .")
    if cleaned.upper() in _RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned or fallback


def validate_template(template: str) -> None:
    """Vérifie un modèle saisi par l'utilisateur ; lève :class:`ConfigError` sinon."""
    if not template.strip():
        msg = "Le modèle de nom de fichier ne peut pas être vide."
        raise ConfigError(msg)

    if _TOKEN_RE.search(template) is None:
        msg = "Le modèle doit contenir au moins un jeton, par exemple {id} ou {title}."
        raise ConfigError(msg)

    unknown = {m.group(1) for m in _TOKEN_RE.finditer(template)} - SUPPORTED_TOKENS
    if unknown:
        msg = (
            "Jetons inconnus : "
            f"{', '.join(sorted(unknown))}. Jetons disponibles : {', '.join(sorted(SUPPORTED_TOKENS))}."
        )
        raise ConfigError(msg)

    if ".." in template:
        msg = "Le modèle ne peut pas contenir « .. »."
        raise ConfigError(msg)


def render(
    template: str,
    meta: VideoMeta | None,
    *,
    url: str = "",
    index: int = 1,
    options: DownloadOptions | None = None,
) -> PurePosixPath:
    """Construit le chemin **relatif** de destination (sans échapper du dossier racine)."""
    validate_template(template)

    video_id = (meta.video_id if meta else None) or canonical_id_from_url(url) or "inconnu"
    username = sanitize_component(
        (meta.uploader_id or meta.uploader) if meta else "", fallback="inconnu"
    )
    title = sanitize_component((meta.display_title if meta else "") or "", fallback=video_id)
    upload = meta.upload_date if meta else None

    values: dict[str, str] = {
        "username": username,
        "uploader_id": username,
        "id": sanitize_component(video_id, fallback="0"),
        "title": title,
        "date": upload.isoformat() if upload else "date-inconnue",
        "datetime": upload.isoformat() if upload else "date-inconnue",
        "year": str(upload.year) if upload else "0000",
        "index": f"{index:04d}",
        "duration": f"{int(meta.duration):03d}" if meta and meta.duration else "000",
        "views": f"{meta.view_count or 0}" if meta else "0",
        "source": sanitize_component(meta.source if meta else "tiktok", fallback="tiktok"),
        "ext": (options.final_extension if options else "mp4"),
    }

    # Les jetons sont remplacés manuellement (pas str.format) : un « { » ou « } » présent
    # dans un titre ne doit jamais faire planter le rendu.
    rendered = template
    for token, replacement in values.items():
        rendered = rendered.replace(f"{{{token}}}", replacement)
    rendered = _TOKEN_RE.sub("", rendered)  # jeton résiduel → on l'ignore proprement

    # Normalisation : séparateurs POSIX, puis nettoyage composant par composant.
    parts = [sanitize_component(p, fallback="_") for p in PurePosixPath(rendered).parts]
    parts = [p for p in parts if p not in {"", ".", ".."}]
    if not parts:
        parts = [f"{video_id}.mp4"]

    # Garantie anti-évasion : le chemin total reste sous la racine et de taille raisonnable.
    relative = PurePosixPath(*parts)
    if len(str(relative)) > _MAX_TOTAL:
        stem = relative.stem[: _MAX_TOTAL - len(relative.suffix) - 1]
        relative = relative.with_name(f"{stem}{relative.suffix}")
    return relative


def canonical_id_from_url(url: str) -> str | None:
    """Identifiant de vidéo extrait d'une URL (repli quand les métadonnées manquent)."""
    if match := re.search(r"/video/(\d+)", url):
        return match.group(1)
    if match := re.search(r"/v/(\d+)", url):
        return match.group(1)
    return None


def resolve_destination(root: Path, relative: PurePosixPath) -> Path:
    """Résout le chemin relatif sous la racine, en refusant toute évasion."""
    root = root.expanduser().resolve()
    candidate = (root / str(relative)).resolve()
    if root not in candidate.parents and candidate != root:
        msg = f"Le modèle produit un chemin hors du dossier de destination : {candidate}"
        raise ConfigError(msg)
    return candidate


def with_unique_suffix(path: Path) -> Path:
    """Ajoute ``(1)``, ``(2)``, … si le fichier existe déjà (évite tout écrasement)."""
    if not path.exists():
        return path
    for counter in range(1, 10_000):
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
    msg = f"Trop de fichiers homonymes pour {path.name}"
    raise ConfigError(msg)
