from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, MutableMapping


TextTranslator = Callable[[str, str, str], str]
TranslationCache = MutableMapping[tuple[str, str, str], str]


@dataclass(frozen=True, slots=True)
class SemanticTranslationResult:
    paragraphs: tuple[str, ...]
    chunks: tuple[str, ...]
    had_failures: bool


def _paragraphs(text: str) -> tuple[str, ...]:
    return tuple(
        paragraph.strip()
        for paragraph in re.split(r"\r?\n[ \t]*\r?\n+", str(text or "").strip())
        if paragraph.strip()
    )


def _split_long_words(text: str, *, max_chars: int) -> tuple[str, ...]:
    pieces: list[str] = []
    current = ""
    for word in text.split():
        candidates = (word,) if len(word) <= max_chars else tuple(
            word[index : index + max_chars]
            for index in range(0, len(word), max_chars)
        )
        for candidate_word in candidates:
            candidate = f"{current} {candidate_word}".strip()
            if current and len(candidate) > max_chars:
                pieces.append(current)
                current = candidate_word
            else:
                current = candidate
    if current:
        pieces.append(current)
    return tuple(pieces)


def split_translation_chunks(text: str, *, max_chars: int = 2500) -> tuple[str, ...]:
    """Divide texto por parágrafo, sentença e palavra sem descartar conteúdo."""

    if max_chars < 1:
        raise ValueError("O limite de tradução deve ser positivo.")
    chunks: list[str] = []
    for paragraph in _paragraphs(text):
        normalized = " ".join(paragraph.split())
        sentences = re.split(r"(?<=[.!?;:])\s+", normalized)
        current = ""
        for sentence in sentences:
            for value in _split_long_words(sentence, max_chars=max_chars):
                candidate = f"{current} {value}".strip()
                if current and len(candidate) > max_chars:
                    chunks.append(current)
                    current = value
                else:
                    current = candidate
        if current:
            chunks.append(current)
            current = ""
    return tuple(chunks)


def translate_semantic_text(
    text: str,
    translator: TextTranslator | None,
    *,
    source_language: str = "en",
    target_language: str = "pt-BR",
    max_chars: int = 2500,
    cache: TranslationCache | None = None,
) -> SemanticTranslationResult:
    """Traduz parágrafos por chunks; falha isolada preserva somente o chunk fonte."""

    paragraphs = _paragraphs(text)
    if not paragraphs:
        return SemanticTranslationResult((), (), False)
    if translator is None:
        chunks = tuple(
            chunk
            for paragraph in paragraphs
            for chunk in split_translation_chunks(paragraph, max_chars=max_chars)
        )
        return SemanticTranslationResult(paragraphs, chunks, False)

    translations = cache if cache is not None else {}
    translated_paragraphs: list[str] = []
    translated_all_chunks: list[str] = []
    had_failures = False
    for paragraph in paragraphs:
        translated_chunks: list[str] = []
        for chunk in split_translation_chunks(paragraph, max_chars=max_chars):
            key = (source_language, target_language, chunk)
            if key in translations:
                cached = str(translations[key])
                translated_chunks.append(cached)
                translated_all_chunks.append(cached)
                continue
            try:
                raw_value = translator(chunk, source_language, target_language)
                value = "" if raw_value is None else str(raw_value).strip()
                if not value:
                    raise ValueError("O tradutor retornou um bloco vazio.")
            except Exception:
                had_failures = True
                translated_chunks.append(chunk)
                translated_all_chunks.append(chunk)
                continue
            translations[key] = value
            translated_chunks.append(value)
            translated_all_chunks.append(value)
        translated_paragraphs.append(" ".join(translated_chunks))
    return SemanticTranslationResult(
        tuple(translated_paragraphs),
        tuple(translated_all_chunks),
        had_failures,
    )


def translate_in_chunks(
    text: str,
    translator: TextTranslator,
    *,
    source_language: str = "en",
    target_language: str = "pt-BR",
    max_chars: int = 2500,
) -> tuple[str, ...]:
    """Traduz cada parte em ordem; plugin output nunca deve chamar esta função."""

    translated: list[str] = []
    for chunk in split_translation_chunks(text, max_chars=max_chars):
        value = str(translator(chunk, source_language, target_language)).strip()
        if not value:
            raise ValueError("O tradutor retornou um bloco vazio.")
        translated.append(value)
    return tuple(translated)
