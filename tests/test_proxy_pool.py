"""Pool de proxies : rotation, quarantaine, analyse de la liste saisie."""

from __future__ import annotations

import pytest

from tikvault.infrastructure.proxy_pool import ProxyPool, parse_proxy_list


class Horloge:
    """Horloge maîtrisée pour tester les quarantaines sans attendre."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestRotation:
    def test_pool_vide(self) -> None:
        pool = ProxyPool(urls=[])
        assert pool.next() is None
        assert len(pool) == 0

    def test_rotation_circulaire(self) -> None:
        pool = ProxyPool(urls=["http://a:1", "http://b:2", "http://c:3"])
        vus = [pool.next() for _ in range(6)]
        assert vus == ["http://a:1", "http://b:2", "http://c:3"] * 2

    def test_proxy_unique_toujours_renvoye(self) -> None:
        pool = ProxyPool(urls=["http://seul:1"])
        assert all(pool.next() == "http://seul:1" for _ in range(5))


class TestQuarantaine:
    def test_mise_a_l_ecart_apres_trop_d_echecs(self) -> None:
        horloge = Horloge()
        pool = ProxyPool(
            urls=["http://a:1", "http://b:2"], max_failures=2, cooldown_seconds=60, clock=horloge
        )

        for _ in range(2):
            pool.report_failure("http://a:1")

        # Le proxy fautif est écarté : on ne doit plus tomber dessus.
        assert {pool.next() for _ in range(6)} == {"http://b:2"}

    def test_retour_apres_la_quarantaine(self) -> None:
        horloge = Horloge()
        pool = ProxyPool(
            urls=["http://a:1", "http://b:2"], max_failures=1, cooldown_seconds=30, clock=horloge
        )
        pool.report_failure("http://a:1")
        horloge.advance(31)
        assert "http://a:1" in {pool.next() for _ in range(4)}

    def test_succes_reinitialise_les_echecs(self) -> None:
        horloge = Horloge()
        pool = ProxyPool(urls=["http://a:1", "http://b:2"], max_failures=3, clock=horloge)
        pool.report_failure("http://a:1")
        pool.report_failure("http://a:1")
        pool.report_success("http://a:1")
        pool.report_failure("http://a:1")
        pool.report_failure("http://a:1")
        # Un seul échec restant après le succès : toujours pas de quarantaine.
        assert "http://a:1" in {pool.next() for _ in range(6)}

    def test_tous_en_quarantaine_on_tente_quand_meme(self) -> None:
        """Mieux vaut une tentative lente qu'un téléchargement bloqué à jamais."""
        horloge = Horloge()
        pool = ProxyPool(urls=["http://a:1"], max_failures=1, cooldown_seconds=600, clock=horloge)
        pool.report_failure("http://a:1")
        assert pool.next() == "http://a:1"

    def test_proxy_inconnu_ignoré(self) -> None:
        pool = ProxyPool(urls=["http://a:1"])
        pool.report_failure("http://inconnu:9")  # ne doit rien lever
        pool.report_success("http://inconnu:9")
        assert pool.next() == "http://a:1"


class TestEtat:
    def test_etat_lisible(self) -> None:
        horloge = Horloge()
        pool = ProxyPool(urls=["http://a:1"], clock=horloge)
        pool.report_success("http://a:1")
        etat = pool.status()
        assert etat[0]["url"] == "http://a:1"
        assert etat[0]["succès"] == 1
        assert etat[0]["disponible"] is True


class TestAnalyseDeListe:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("", []),
            ("  \n  ", []),
            ("# commentaire\nhttp://a:1", ["http://a:1"]),
            ("http://a:1\nhttp://b:2", ["http://a:1", "http://b:2"]),
            ("http://a:1;http://b:2", ["http://a:1", "http://b:2"]),
            ("1.2.3.4:8080", ["http://1.2.3.4:8080"]),
            ("socks5://127.0.0.1:9050", ["socks5://127.0.0.1:9050"]),
            ("http://a:1\nhttp://a:1", ["http://a:1"]),
        ],
    )
    def test_analyse(self, entree: str, attendu: list[str]) -> None:
        assert parse_proxy_list(entree) == attendu

    def test_liste_deja_construite(self) -> None:
        assert parse_proxy_list(["http://a:1", "http://b:2"]) == ["http://a:1", "http://b:2"]

    def test_identifiants_dans_l_url(self) -> None:
        url = "http://user:pass@1.2.3.4:8080"
        assert parse_proxy_list(url) == [url]
