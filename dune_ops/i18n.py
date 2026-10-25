import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class I18nEngine:
    """Zero-dependency reactive i18n translation engine."""

    def __init__(self, locales_dir: Optional[Path] = None, default_lang: str = "pl"):
        self.default_lang = default_lang
        self.current_lang = default_lang
        self._bundles: Dict[str, Dict[str, Any]] = {}

        if locales_dir is None:
            locales_dir = Path(__file__).resolve().parent / "locales"
        self.locales_dir = locales_dir
        self.reload_locales()

    def reload_locales(self) -> None:
        """Loads all JSON files from the locales directory."""
        self._bundles.clear()
        if not self.locales_dir.exists():
            return

        for file_path in self.locales_dir.glob("*.json"):
            lang_code = file_path.stem.lower()
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    self._bundles[lang_code] = json.load(f)
            except Exception:
                self._bundles[lang_code] = {}

    def set_language(self, lang: str) -> bool:
        """Switches the active language if supported."""
        lang_normalized = lang.lower()
        if lang_normalized in self._bundles:
            self.current_lang = lang_normalized
            return True
        return False

    def t(self, key_path: str, lang: Optional[str] = None, **kwargs) -> str:
        """
        Translates a dotted key (e.g. 'app.title').
        Falls back to default_lang, then to key_path itself.
        Supports string formatting via kwargs.
        """
        target_lang = (lang or self.current_lang).lower()
        bundle = self._bundles.get(target_lang) or self._bundles.get(self.default_lang) or {}

        val: Any = bundle
        for part in key_path.split("."):
            if isinstance(val, dict):
                val = val.get(part)
            else:
                val = None
                break

        if val is None and target_lang != self.default_lang:
            # Fallback to default language
            fallback_bundle = self._bundles.get(self.default_lang, {})
            val = fallback_bundle
            for part in key_path.split("."):
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    val = None
                    break

        if val is None:
            return key_path

        template = str(val)
        if kwargs:
            try:
                return template.format(**kwargs)
            except KeyError:
                return template
        return template

    @property
    def available_languages(self) -> list[str]:
        return sorted(list(self._bundles.keys()))