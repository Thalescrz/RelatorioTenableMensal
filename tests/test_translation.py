from tenable_reports.presentation.translation import (
    split_translation_chunks,
    translate_semantic_text,
    translate_in_chunks,
)


def test_translation_chunks_preserve_all_text_in_order() -> None:
    source = " ".join(f"Sentence {index} has technical content." for index in range(100))
    chunks = split_translation_chunks(source, max_chars=180)
    assert len(chunks) > 1
    assert all(len(chunk) <= 180 for chunk in chunks)
    assert " ".join(chunks) == source


def test_each_chunk_is_translated_separately() -> None:
    calls = []

    def translator(text, source, target):
        calls.append((text, source, target))
        return f"TRADUZIDO:{text}"

    result = translate_in_chunks(
        "First sentence. Second sentence. Third sentence.",
        translator,
        max_chars=20,
    )
    assert len(calls) >= 3
    assert len(result) == len(calls)
    assert all(item.startswith("TRADUZIDO:") for item in result)


def test_semantic_translation_preserves_paragraphs_and_technical_tokens() -> None:
    source = (
        "CVE-2099-1000 affects version 1.2.3. Review "
        "https://fixture.invalid/security/advisory for details. "
        "Apply the documented mitigation after validation.\n\n"
        "A second paragraph remains independent and ordered."
    )
    calls: list[str] = []

    def translator(text: str, source_language: str, target_language: str) -> str:
        calls.append(text)
        assert source_language == "en"
        assert target_language == "pt-BR"
        return f"PT<{text}>"

    result = translate_semantic_text(source, translator, max_chars=72)

    assert not result.had_failures
    assert len(result.paragraphs) == 2
    assert len(calls) > 2
    assert all(len(chunk) <= 72 for chunk in calls)
    assert any("CVE-2099-1000" in chunk for chunk in calls)
    assert any("1.2.3" in chunk for chunk in calls)
    assert any(
        "https://fixture.invalid/security/advisory" in chunk for chunk in calls
    )
    assert result.paragraphs[0].startswith("PT<")
    assert result.paragraphs[1] == (
        "PT<A second paragraph remains independent and ordered.>"
    )


def test_semantic_translation_preserves_only_failed_chunk_and_continues() -> None:
    source = (
        "First technical sentence is safe. "
        "FAIL-MARKER chunk must survive. "
        "Last technical sentence is safe."
    )
    calls: list[str] = []

    def translator(text: str, *_: str) -> str:
        calls.append(text)
        if "FAIL-MARKER" in text:
            raise RuntimeError("fixture translator unavailable")
        return f"PT<{text}>"

    result = translate_semantic_text(source, translator, max_chars=40)

    assert result.had_failures
    assert len(calls) == 3
    assert result.paragraphs == (
        "PT<First technical sentence is safe.> "
        "FAIL-MARKER chunk must survive. "
        "PT<Last technical sentence is safe.>",
    )


def test_semantic_translation_caches_repeated_chunks_within_text() -> None:
    source = "Repeated technical paragraph.\n\nRepeated technical paragraph."
    calls: list[str] = []

    def translator(text: str, *_: str) -> str:
        calls.append(text)
        return f"PT<{text}>"

    result = translate_semantic_text(source, translator, max_chars=100)

    assert calls == ["Repeated technical paragraph."]
    assert result.paragraphs == (
        "PT<Repeated technical paragraph.>",
        "PT<Repeated technical paragraph.>",
    )


def test_semantic_translation_without_translator_preserves_paragraph_text() -> None:
    source = "Descrição já em português.\n\nSegundo parágrafo preservado."

    result = translate_semantic_text(source, None, max_chars=40)

    assert result.paragraphs == (
        "Descrição já em português.",
        "Segundo parágrafo preservado.",
    )
    assert not result.had_failures


def test_short_semantic_translation_uses_one_call() -> None:
    calls: list[str] = []

    def translator(text: str, *_: str) -> str:
        calls.append(text)
        return "Descrição traduzida."

    result = translate_semantic_text(
        "Short technical description.",
        translator,
        max_chars=900,
    )

    assert calls == ["Short technical description."]
    assert result.paragraphs == ("Descrição traduzida.",)
