"""Analyse des sources d'entrée : liens en vrac, fichiers .txt/.csv, profils, hashtags.

Responsabilités :
  1. **détection** — extraire toute URL ou mention exploitable d'un texte libre ;
  2. **nettoyage** — supprimer les paramètres de suivi TikTok (``?_r=1&_t=…``) ;
  3. **qualification** — décider s'il s'agit d'une vidéo, d'un profil, d'un son… ;
  4. **dédoublonnage** — une seule tâche par vidéo, ordre de saisie conservé.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from tikvault.domain.models import SourceType

# ---------------------------------------------------------------------------
# Expressions régulières de reconnaissance
# ---------------------------------------------------------------------------
# Hôtes TikTok connus (y compris les raccourcisseurs de lien).
_TIKTOK_HOSTS = (
    r"(?:www\.|m\.|vm\.|vt\.|v\.)?tiktok\.com"
    r"|(?:www\.)?tiktokv\.com"
    r"|(?:www\.)?douyin\.com"
)
# Raccourcisseurs : l'identifiant réel n'est connu qu'après redirection. On les traite
# donc comme des vidéos et on laisse l'extracteur résoudre le lien.
_SHORTENER_RE = re.compile(r"(?i)^https?://(?:vm|vt|v)\.(?:tiktok\.com|tiktokv\.com)/")
# Hashtags « de bruit » : très fréquents, aucune valeur de recherche.
_NOISE_TAGS = {
    "fyp",
    "foryou",
    "foryoupage",
    "viral",
    "tiktok",
    "pourtoi",
    "xyzbca",
    "trend",
    "trending",
}

_URL_RE = re.compile(
    r"""(?xi)
    \b(?:https?://)                          # schéma obligatoire
    [^\s<>"'`\)\]\},;]+                      # tout sauf les délimiteurs usuels
    """
)

# Vidéo : /@pseudo/video/1234567890123456789
_VIDEO_RE = re.compile(rf"(?i)(?:{_TIKTOK_HOSTS})/@[\w.\-]+/video/(\d+)")
# Ancien format mobile : m.tiktok.com/v/1234567890.html
_VIDEO_LEGACY_RE = re.compile(rf"(?i)(?:{_TIKTOK_HOSTS})/v/(\d+)")
# Photo/carrousel : /@pseudo/photo/123…
_PHOTO_RE = re.compile(rf"(?i)(?:{_TIKTOK_HOSTS})/@[\w.\-]+/photo/(\d+)")
# Musique / son
_SOUND_RE = re.compile(rf"(?i)(?:{_TIKTOK_HOSTS})/(?:@[\w.\-]+/)?music/[^/?#]+")
# Hashtag de page : tiktok.com/tag/nom
_TAG_RE = re.compile(rf"(?i)(?:{_TIKTOK_HOSTS})/tag/[^/?#]+")
# Playlist ou collection d'un utilisateur
_PLAYLIST_RE = re.compile(rf"(?i)(?:{_TIKTOK_HOSTS})/@[\w.\-]+/(?:playlist|collection)/\d+")
# Profil seul
_PROFILE_RE = re.compile(rf"(?i)^(?:https?://)?(?:{_TIKTOK_HOSTS})/@([\w.\-]+)/?$")
# Mention nue dans un texte : @pseudo
_BARE_HANDLE_RE = re.compile(r"(?<![\w/])@([A-Za-z0-9._]{2,24})(?![\w.])")
# Hashtag nu : #nom (au moins 2 caractères, pas uniquement des chiffres)
_BARE_TAG_RE = re.compile(r"(?<![\w])#([A-Za-zÀ-ÿ0-9_]{2,60})(?![\w])")
# Identifiant de vidéo isolé (19 chiffres, format snowflake TikTok)
_BARE_VIDEO_ID_RE = re.compile(r"(?<!\d)(\d{18,20})(?!\d)")

# Paramètres de suivi à retirer (TikTok ajoute beaucoup de bruit).
_TRACKING_PARAMS = {
    "_r",
    "_t",
    "is_copy_url",
    "is_from_webapp",
    "sender_device",
    "sender_web_id",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "share_app_id",
    "share_item_id",
    "share_link_id",
    "share_tag",
    "timestamp",
    "unique_id",
    "referer",
    "source",
    "fbclid",
    "gclid",
}


@dataclass(slots=True, frozen=True)
class ParsedSource:
    """Une source d'entrée normalisée, prête à être résolue par un extracteur."""

    kind: SourceType
    value: str  # URL canonique, @pseudo ou #tag
    original: str  # texte tel que saisi (pour l'affichage et les rapports)

    @property
    def dedupe_key(self) -> str:
        """Clé de dédoublonnage, insensible à la casse et au format."""
        match self.kind:
            case SourceType.VIDEO:
                return f"video:{self.value.lower()}"
            case SourceType.PROFILE:
                return f"profile:{self.value.lower().lstrip('@')}"
            case SourceType.HASHTAG:
                return f"tag:{self.value.lower().lstrip('#')}"
            case SourceType.SOUND:
                return f"sound:{self.value.lower()}"
            case SourceType.PLAYLIST:
                return f"playlist:{self.value.lower()}"
            case _:
                return f"raw:{self.value.lower()}"


def clean_url(raw: str) -> str:
    """Nettoie une URL : espaces, ponctuation finale collée, paramètres de suivi."""
    url = raw.strip().strip("<>").strip()
    # Ponctuation souvent collée quand on copie depuis un message : « …video/123. »
    url = url.rstrip(".,;:!?)】」》'\"")

    if "?" not in url:
        return url

    base, _, query = url.partition("?")
    kept: list[str] = []
    for pair in query.split("&"):
        if not pair:
            continue
        key = pair.split("=", 1)[0].lower()
        if key in _TRACKING_PARAMS:
            continue
        kept.append(pair)
    return f"{base}?{'&'.join(kept)}" if kept else base


def canonical_video_id(url_or_id: str) -> str | None:
    """Extrait l'identifiant numérique d'une vidéo depuis une URL ou un ID brut."""
    for pattern in (_VIDEO_RE, _VIDEO_LEGACY_RE, _PHOTO_RE):
        if match := pattern.search(url_or_id):
            return match.group(1)
    if _BARE_VIDEO_ID_RE.fullmatch(url_or_id.strip()):
        return url_or_id.strip()
    return None


def classify(url: str) -> SourceType:
    """Qualifie une URL TikTok nettoyée."""
    if canonical_video_id(url):
        return SourceType.VIDEO
    if _PLAYLIST_RE.search(url):
        return SourceType.PLAYLIST
    if _SOUND_RE.search(url):
        return SourceType.SOUND
    if _TAG_RE.search(url):
        return SourceType.HASHTAG
    if _PROFILE_RE.match(url):
        return SourceType.PROFILE
    if _SHORTENER_RE.match(url):
        # Lien raccourci : impossible de connaître l'identifiant sans le suivre,
        # mais c'est une vidéo dans l'immense majorité des cas.
        return SourceType.VIDEO
    return SourceType.UNKNOWN


class SourceParser:
    """Transforme un texte libre (ou un fichier) en liste de :class:`ParsedSource`."""

    def __init__(self, *, allow_bare_handles: bool = True, allow_bare_tags: bool = True) -> None:
        self._allow_bare_handles = allow_bare_handles
        self._allow_bare_tags = allow_bare_tags

    # --- API publique -----------------------------------------------------
    def parse_text(self, text: str) -> list[ParsedSource]:
        """Analyse un bloc de texte : un lien par ligne, ou plusieurs sur la même ligne."""
        sources: list[ParsedSource] = []
        seen: set[str] = set()

        def add(source: ParsedSource) -> None:
            if source.dedupe_key not in seen:
                seen.add(source.dedupe_key)
                sources.append(source)

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Commentaire : « # ma note » (espace après le dièse) ou « ;; ma note ».
            # Un hashtag de recherche (« #dance ») n'a pas d'espace, il est donc conservé.
            if stripped.startswith("# ") or stripped.startswith(";;"):
                continue
            for source in self._parse_line(stripped):
                add(source)

        return sources

    def parse_file(self, path: Path) -> list[ParsedSource]:
        """Importe un fichier ``.txt`` (une entrée par ligne) ou ``.csv``."""
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if path.suffix.lower() == ".csv":
            return self._parse_csv(text)
        return self.parse_text(text)

    # --- Internes ---------------------------------------------------------
    def _parse_csv(self, text: str) -> list[ParsedSource]:
        """CSV : on cherche une colonne ``url``/``lien``, sinon on balaie toutes les cellules."""
        sources: list[ParsedSource] = []
        seen: set[str] = set()
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return sources

        header = [cell.strip().lower() for cell in rows[0]]
        url_columns = [
            i for i, name in enumerate(header) if name in {"url", "lien", "link", "urls"}
        ]
        body = rows[1:] if url_columns or self._looks_like_header(header) else rows

        for row in body:
            candidates: list[str] = []
            if url_columns:
                candidates = [row[i] for i in url_columns if i < len(row)]
            if not any("tiktok" in c.lower() or "@" in c or "#" in c for c in candidates):
                candidates = list(row)  # pas de colonne dédiée → balayage complet
            for cell in candidates:
                for source in self._parse_line(cell.strip()):
                    if source.dedupe_key not in seen:
                        seen.add(source.dedupe_key)
                        sources.append(source)
        return sources

    @staticmethod
    def _looks_like_header(header: list[str]) -> bool:
        """Heuristique : la première ligne est un en-tête si aucune cellule n'est une URL."""
        return not any("tiktok.com" in cell or "://" in cell for cell in header)

    def _parse_line(self, line: str) -> list[ParsedSource]:
        """Analyse une ligne : d'abord les URL, puis les mentions/hashtags nus."""
        found: list[ParsedSource] = []
        consumed = line

        for raw_url in _URL_RE.findall(line):
            url = clean_url(raw_url)
            if not url.startswith(("http://", "https://")):
                url = f"https://{url}"

            kind = classify(url)
            if kind is SourceType.VIDEO:
                # On normalise vers l'URL longue quand l'ID est connu : évite les doublons
                # entre vm.tiktok.com/xxxx et tiktok.com/@user/video/xxxx.
                video_id = canonical_video_id(url)
                found.append(ParsedSource(kind, url, original=raw_url))
                if video_id:
                    consumed = consumed.replace(raw_url, f" __ID{video_id}__ ")
            elif kind is not SourceType.UNKNOWN:
                found.append(ParsedSource(kind, url, original=raw_url))
                consumed = consumed.replace(raw_url, " ")
            # Une URL inconnue (youtube, etc.) est ignorée silencieusement.

        # Mentions et hashtags restants dans la ligne.
        if self._allow_bare_handles:
            for handle in _BARE_HANDLE_RE.findall(consumed):
                if handle.lower() in _NOISE_TAGS:
                    continue  # hashtag déguisé en mention
                found.append(ParsedSource(SourceType.PROFILE, f"@{handle}", original=f"@{handle}"))

        if self._allow_bare_tags:
            for tag in _BARE_TAG_RE.findall(consumed):
                if tag.isdigit() or tag.lower() in _NOISE_TAGS:
                    continue
                found.append(ParsedSource(SourceType.HASHTAG, f"#{tag}", original=f"#{tag}"))

        # Identifiant de vidéo collé tel quel (ex. depuis un tableur).
        for video_id in _BARE_VIDEO_ID_RE.findall(consumed):
            if f"__ID{video_id}__" in consumed:
                continue  # déjà traité via une URL
            found.append(
                ParsedSource(
                    SourceType.VIDEO,
                    f"https://www.tiktok.com/@user/video/{video_id}",
                    original=video_id,
                )
            )

        return found
