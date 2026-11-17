"""Filtres métier et quota par source."""

from __future__ import annotations

from datetime import date

import pytest

from tikvault.domain.filters import QuotaTracker, VideoFilter
from tikvault.domain.models import FilterOptions, VideoMeta


def make_meta(**kwargs: object) -> VideoMeta:
    base: dict[str, object] = {
        "video_id": "1",
        "url": "https://www.tiktok.com/@a/video/1",
        "duration": 30.0,
        "upload_date": date(2024, 6, 1),
        "view_count": 10_000,
    }
    base.update(kwargs)
    return VideoMeta(**base)  # type: ignore[arg-type]


class TestVideoFilter:
    def test_aucun_filtre_accepte_tout(self) -> None:
        assert VideoFilter(FilterOptions()).evaluate(make_meta()).accepted

    def test_date_trop_ancienne(self) -> None:
        options = FilterOptions(date_from=date(2024, 7, 1))
        decision = VideoFilter(options).evaluate(make_meta(upload_date=date(2024, 6, 1)))
        assert not decision.accepted
        assert "avant" in decision.reason

    def test_date_trop_recente(self) -> None:
        options = FilterOptions(date_to=date(2024, 5, 1))
        assert not VideoFilter(options).evaluate(make_meta()).accepted

    def test_duree_minimale(self) -> None:
        options = FilterOptions(min_duration=60)
        assert not VideoFilter(options).evaluate(make_meta(duration=30.0)).accepted
        assert VideoFilter(options).evaluate(make_meta(duration=90.0)).accepted

    def test_duree_maximale(self) -> None:
        options = FilterOptions(max_duration=20)
        assert not VideoFilter(options).evaluate(make_meta(duration=30.0)).accepted

    def test_vues_minimales(self) -> None:
        options = FilterOptions(min_views=50_000)
        assert not VideoFilter(options).evaluate(make_meta(view_count=1_000)).accepted
        assert VideoFilter(options).evaluate(make_meta(view_count=90_000)).accepted

    def test_information_manquante_ne_bloque_pas(self) -> None:
        """Sans durée ni date connues, on télécharge quand même (pas de perte de vidéo)."""
        options = FilterOptions(min_duration=60, date_from=date(2024, 1, 1), min_views=100)
        meta = make_meta(duration=None, upload_date=None, view_count=None)
        assert VideoFilter(options).evaluate(meta).accepted

    def test_critere_vide(self) -> None:
        assert FilterOptions().is_empty
        assert not FilterOptions(min_views=10).is_empty


class TestQuotaTracker:
    def test_sans_plafond(self) -> None:
        tracker = QuotaTracker(None)
        tracker.consume(500)
        assert not tracker.exhausted
        assert tracker.remaining() is None

    def test_plafond_atteint(self) -> None:
        tracker = QuotaTracker(3)
        tracker.consume(2)
        assert not tracker.exhausted
        assert tracker.remaining() == 1
        tracker.consume(1)
        assert tracker.remaining() == 0
        assert tracker.exhausted

    @pytest.mark.parametrize("max_items", [1, 5, 10])
    def test_comptage(self, max_items: int) -> None:
        tracker = QuotaTracker(max_items)
        tracker.consume(max_items)
        assert tracker.exhausted
