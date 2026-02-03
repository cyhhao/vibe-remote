"""
Backend i18n module for Slack messages.
Supports language setting synchronized with Web UI.
"""

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional


class I18n:
    """Internationalization manager for backend messages."""

    _instance: Optional["I18n"] = None
    _translations: Dict[str, Dict[str, Any]] = {}

    def __init__(self):
        self._load_translations()

    @classmethod
    def get_instance(cls) -> "I18n":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reload(cls) -> None:
        """Reload translations from files."""
        if cls._instance is not None:
            cls._instance._load_translations()

    def _load_translations(self) -> None:
        """Load all translation files from the i18n directory."""
        i18n_dir = Path(__file__).parent
        self._translations = {}
        for lang_file in i18n_dir.glob("*.json"):
            lang = lang_file.stem
            try:
                self._translations[lang] = json.loads(lang_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                # Skip invalid files
                pass

    def get_available_languages(self) -> list[str]:
        """Get list of available language codes."""
        return list(self._translations.keys())

    def t(self, key: str, lang: str = "en", **kwargs: Any) -> str:
        """
        Translate a key with optional interpolation.

        Args:
            key: Dot-separated key path (e.g., "modal.settings.title")
            lang: Language code (e.g., "en", "zh")
            **kwargs: Interpolation values (e.g., name="John" for "{name}")

        Returns:
            Translated string, or key if not found
        """
        # Get translations for the requested language, fallback to English
        translations = self._translations.get(lang)
        if translations is None:
            translations = self._translations.get("en", {})

        # Navigate nested keys: "modal.settings.title" -> translations["modal"]["settings"]["title"]
        parts = key.split(".")
        value: Any = translations
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
                if value is None:
                    # Key not found, try English fallback
                    if lang != "en":
                        return self.t(key, "en", **kwargs)
                    return key
            else:
                return key

        if not isinstance(value, str):
            return key

        # Interpolate variables: {name} -> value of kwargs["name"]
        if kwargs:
            for k, v in kwargs.items():
                value = value.replace(f"{{{k}}}", str(v))

        return value


def get_translator(lang: str = "en") -> Callable[..., str]:
    """
    Get a translator function bound to a specific language.

    Args:
        lang: Language code

    Returns:
        A function that translates keys in the specified language
    """
    i18n = I18n.get_instance()
    return lambda key, **kwargs: i18n.t(key, lang, **kwargs)


def t(key: str, lang: str = "en", **kwargs: Any) -> str:
    """
    Shortcut function to translate a key.

    Args:
        key: Dot-separated key path
        lang: Language code
        **kwargs: Interpolation values

    Returns:
        Translated string
    """
    return I18n.get_instance().t(key, lang, **kwargs)


def get_supported_languages() -> list[str]:
    """Get supported language codes with stable ordering."""
    languages = I18n.get_instance().get_available_languages()
    if not languages:
        return ["en"]
    if "en" in languages:
        return ["en"] + sorted([lang for lang in languages if lang != "en"])
    return sorted(languages)


def normalize_language(lang: Optional[str], default: str = "en") -> str:
    """Normalize language to a supported value with fallback."""
    if not lang:
        return default
    supported = get_supported_languages()
    return lang if lang in supported else default
