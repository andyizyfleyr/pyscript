# ---------------------------------------------------------------------------
# TikVault — raccourcis de développement (gestion de projet : uv uniquement)
#
#   make install     installe l'environnement (dépendances + outils de dev)
#   make run         lance l'interface graphique
#   make headless    exemple de lancement sans fenêtre
#   make lint        Ruff : lint + vérification du formatage
#   make format      Ruff : reformate les sources
#   make typecheck   mypy en mode strict
#   make test        pytest (moteur de téléchargement)
#   make check       lint + typecheck + test (porte de qualité complète)
#   make icon        régénère assets/icon.png et assets/icon.ico
#   make exe         fabrique l'exécutable autonome avec PyInstaller
#   make clean       supprime caches et artefacts de compilation
# ---------------------------------------------------------------------------

.PHONY: install run headless lint format typecheck test check icon exe clean

UV ?= uv

install:
	$(UV) sync --all-extras

run:
	$(UV) run tikvault

headless:
	$(UV) run tikvault --headless "@tiktok" --max-items 5 --wait

lint:
	$(UV) run ruff check src tests
	$(UV) run ruff format --check src tests

format:
	$(UV) run ruff check src tests --fix
	$(UV) run ruff format src tests

typecheck:
	$(UV) run mypy

test:
	$(UV) run pytest

check: lint typecheck test

icon:
	$(UV) run python scripts/make_icon.py

exe:
	$(UV) sync --extra build
	$(UV) run pyinstaller build/tikvault.spec --noconfirm
	@echo "→ exécutable disponible dans dist/"

clean:
	rm -rf build/TikVault build/tikvault dist .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
