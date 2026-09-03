from __future__ import annotations

from typing import Any


class GoogleTextTranslator:
    """Adaptador lazy do tradutor usado pelo gerador legado, com cache por execução."""

    def __init__(self) -> None:
        self._provider: Any | None = None
        self._cache: dict[tuple[str, str, str], str] = {}

    @staticmethod
    def _provider_language(language: str) -> str:
        normalized = str(language or "").strip()
        if normalized.casefold() == "pt-br":
            return "pt"
        return normalized

    def __call__(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        source = self._provider_language(source_language)
        target = self._provider_language(target_language)
        value = str(text or "").strip()
        key = (source, target, value)
        if key in self._cache:
            return self._cache[key]
        if self._provider is None:
            from deep_translator import GoogleTranslator

            self._provider = GoogleTranslator(source=source, target=target)
        translated = str(self._provider.translate(value) or "").strip()
        if not translated:
            raise ValueError("O serviço de tradução retornou um bloco vazio.")
        self._cache[key] = translated
        return translated


def build_default_text_translator() -> GoogleTextTranslator:
    return GoogleTextTranslator()


__all__ = ["GoogleTextTranslator", "build_default_text_translator"]
