"""Reconnaissance des sources YouTube et Facebook (en plus de TikTok).

L'extracteur yt-dlp acceptait déjà n'importe quelle URL ; le verrou était
``url_parser.classify`` qui ignorait tout ce qui n'était pas TikTok.
"""

from __future__ import annotations

from tikvault.domain.models import SourceType
from tikvault.services.url_parser import SourceParser, classify, clean_url, youtube_video_id

# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------


def test_youtube_watch_classee_video() -> None:
    assert classify("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is SourceType.VIDEO


def test_youtube_short_classee_video() -> None:
    assert classify("https://www.youtube.com/shorts/aBcDeFgHiJk") is SourceType.VIDEO


def test_youtu_be_classee_video() -> None:
    assert classify("https://youtu.be/dQw4w9WgXcQ") is SourceType.VIDEO


def test_youtube_playlist_classee_playlist() -> None:
    url = "https://www.youtube.com/playlist?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG"
    assert classify(url) is SourceType.PLAYLIST


def test_chaine_youtube_classee_profil() -> None:
    assert classify("https://www.youtube.com/@mkbhd") is SourceType.PROFILE
    assert classify("https://www.youtube.com/@mkbhd/videos") is SourceType.PROFILE
    assert classify("https://www.youtube.com/channel/UCBJycsmduvYEL83R_U4JriQ") is (
        SourceType.PROFILE
    )


def test_formats_youtube_normalises_vers_watch() -> None:
    """youtu.be, Short et watch?v= pointent vers la même vidéo → une seule tâche."""
    parser = SourceParser()
    sources = parser.parse_text(
        "https://youtu.be/dQw4w9WgXcQ\n"
        "https://www.youtube.com/shorts/dQw4w9WgXcQ\n"
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )
    assert len(sources) == 1
    assert sources[0].value == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_parametre_si_de_partage_retire() -> None:
    propre = clean_url("https://youtu.be/dQw4w9WgXcQ?si=AbCdEfGh123&feature=share")
    assert propre == "https://youtu.be/dQw4w9WgXcQ"


def test_extraction_identifiant_youtube() -> None:
    assert youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube_video_id("https://www.tiktok.com/@a/video/1") is None


# ---------------------------------------------------------------------------
# Facebook
# ---------------------------------------------------------------------------


def test_facebook_watch_classee_video() -> None:
    assert classify("https://www.facebook.com/watch?v=1234567890123456") is SourceType.VIDEO


def test_facebook_reel_classee_video() -> None:
    assert classify("https://www.facebook.com/reel/987654321012345") is SourceType.VIDEO


def test_facebook_page_videos_classee_video() -> None:
    url = "https://www.facebook.com/LeMonde.fr/videos/123456789012345"
    assert classify(url) is SourceType.VIDEO


def test_fb_watch_classee_video() -> None:
    assert classify("https://fb.watch/aBc1DeF2gH/") is SourceType.VIDEO


def test_parametre_mibextid_retire() -> None:
    propre = clean_url("https://fb.watch/aBc1DeF2gH/?mibextid=Nif5oz")
    assert propre == "https://fb.watch/aBc1DeF2gH/"


# ---------------------------------------------------------------------------
# Mélange des trois plateformes
# ---------------------------------------------------------------------------


def test_collage_mixte_trois_plateformes() -> None:
    parser = SourceParser()
    sources = parser.parse_text(
        "Regarde ça https://www.tiktok.com/@chef/video/7301234567890123456 "
        "et https://www.youtube.com/watch?v=dQw4w9WgXcQ puis "
        "https://fb.watch/aBc1DeF2gH/ !"
    )
    assert [s.kind for s in sources] == [SourceType.VIDEO] * 3
    valeurs = " ".join(s.value for s in sources)
    assert "tiktok.com" in valeurs
    assert "youtube.com/watch?v=dQw4w9WgXcQ" in valeurs
    assert "fb.watch" in valeurs


def test_url_youtube_ne_genere_pas_de_faux_profil() -> None:
    """Le @pseudo d'une URL de chaîne ne doit pas devenir un profil TikTok en plus."""
    parser = SourceParser()
    sources = parser.parse_text("https://www.youtube.com/@mkbhd/videos")
    assert len(sources) == 1
    assert sources[0].kind is SourceType.PROFILE
    assert sources[0].value == "https://www.youtube.com/@mkbhd/videos"


def test_plateforme_inconnue_toujours_ignoree() -> None:
    parser = SourceParser()
    assert parser.parse_text("https://www.instagram.com/reel/Cxyz123/") == []


def test_tiktok_intact() -> None:
    """Garde-fou : le comportement TikTok historique ne change pas."""
    parser = SourceParser()
    sources = parser.parse_text("@cuisinefacile #recette")
    assert [s.kind for s in sources] == [SourceType.PROFILE, SourceType.HASHTAG]
