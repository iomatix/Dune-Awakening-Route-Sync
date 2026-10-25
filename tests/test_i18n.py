import json
import pytest
from pathlib import Path
from dune_ops.i18n import I18nEngine


@pytest.fixture
def temp_locales(tmp_path: Path):
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()

    pl = {
        "app": {"title": "Panel Dune"},
        "greet": "Witaj {name}!",
        "nested": {"level1": {"level2": "Wartość"}}
    }
    en = {
        "app": {"title": "Dune Console"},
        "greet": "Hello {name}!",
        "nested": {"level1": {"level2": "Value"}}
    }

    with open(locales_dir / "pl.json", "w", encoding="utf-8") as f:
        json.dump(pl, f)
    with open(locales_dir / "en.json", "w", encoding="utf-8") as f:
        json.dump(en, f)

    return locales_dir


def test_i18n_loading_and_translation(temp_locales: Path):
    engine = I18nEngine(locales_dir=temp_locales, default_lang="pl")
    assert "pl" in engine.available_languages
    assert "en" in engine.available_languages
    assert engine.t("app.title") == "Panel Dune"


def test_i18n_language_switch(temp_locales: Path):
    engine = I18nEngine(locales_dir=temp_locales, default_lang="pl")
    assert engine.set_language("en") is True
    assert engine.t("app.title") == "Dune Console"
    assert engine.t("greet", name="Zane") == "Hello Zane!"


def test_i18n_fallback_behavior(temp_locales: Path):
    engine = I18nEngine(locales_dir=temp_locales, default_lang="pl")
    engine.set_language("en")
    # Non-existent key returns raw path
    assert engine.t("non.existent.key") == "non.existent.key"


def test_real_locales_parity():
    """Ensures production PL and EN files share identical key sets."""
    repo_locales = Path(__file__).resolve().parent.parent / "dune_ops" / "locales"
    if not repo_locales.exists():
        pytest.skip("dune_ops/locales directory not found in repository root")

    engine = I18nEngine(locales_dir=repo_locales, default_lang="pl")
    assert "pl" in engine.available_languages
    assert "en" in engine.available_languages

    def extract_keys(d, prefix=""):
        keys = set()
        for k, v in d.items():
            curr = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                keys.update(extract_keys(v, curr))
            else:
                keys.add(curr)
        return keys

    pl_keys = extract_keys(engine._bundles["pl"])
    en_keys = extract_keys(engine._bundles["en"])

    assert pl_keys == en_keys, f"Locale key parity mismatch: {pl_keys.symmetric_difference(en_keys)}"