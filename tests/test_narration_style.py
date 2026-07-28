from __future__ import annotations

import pytest

from pipeline_videos.models import ScriptDocument, ScriptSection
from pipeline_videos.narration_style import (
    validate_authorial_narration_style,
    validate_biblical_explainer_narration,
    validate_jesus_first_person_narration,
)


def _document(narration: str, *, biblical_text: bool = False) -> ScriptDocument:
    return ScriptDocument(
        title="Teste de voz autoral",
        sections=[
            ScriptSection(
                section_id="section-001",
                role="opening",
                title="Abertura",
                narration=narration,
                biblical_basis=["Mateus 9:9"] if biblical_text else [],
                claim_classification=["biblical_text"] if biblical_text else ["verified_context"],
            )
        ],
    )


@pytest.mark.parametrize(
    "narration",
    [
        "O relato registra a mudança de Mateus.",
        "O retrato começa no lugar de trabalho.",
        "A sequência é compacta e precisa ser examinada.",
        "Não podemos afirmar o que ele sentiu naquele momento.",
    ],
)
def test_global_authorial_style_rejects_meta_language_without_long_profile(
    narration: str,
) -> None:
    with pytest.raises(ValueError, match="metalinguagem"):
        validate_authorial_narration_style(_document(narration))


@pytest.mark.parametrize(
    "narration",
    [
        "Mateus estava na coletoria, sem preparação anterior, explicação emocional ou aviso.",
        "A cena não descreve seus pensamentos, nem explica seus medos.",
        "Mateus não hesitou. Ele se levantou e seguiu Jesus.",
        "Não fez um discurso longo. Jesus apenas disse: Siga-me.",
        "Jesus não fez um discurso longo, mas o chamou imediatamente.",
        (
            "A cruz, portanto, não aparece como acidente nem impotência, mas como entrega "
            "voluntária."
        ),
        "A prisão não foi derrota nem falta de poder, mas uma entrega consciente.",
        (
            "A cruz não significou acaso tampouco impotência; pelo contrário, revelou a entrega "
            "voluntária de Jesus."
        ),
        "A decisão não nasceu do medo nem da fraqueza, e sim de uma escolha consciente.",
    ],
)
def test_global_authorial_style_rejects_negative_scaffolding(narration: str) -> None:
    with pytest.raises(ValueError, match="negação retórica|lista de ausências"):
        validate_authorial_narration_style(_document(narration))


@pytest.mark.parametrize(
    "narration",
    [
        "Nunca faça uma promessa que a Bíblia não fez.",
        "Você não está sozinho. Não desista agora.",
        "O que acontece se você parar de ouvir antes de compreender?",
        "Jesus não abandonou seus discípulos.",
        "Eles ainda não o reconheceram, mas ele já sabia quem estava diante dele.",
    ],
)
def test_global_authorial_style_preserves_legitimate_negative_hooks(narration: str) -> None:
    validate_authorial_narration_style(_document(narration))


def test_global_authorial_style_does_not_rewrite_literal_scripture() -> None:
    document = _document(
        "Em Mateus, capítulo 9, versículo 13, lemos: "
        "“Eu não vim para chamar os justos, mas os pecadores.”",
        biblical_text=True,
    )

    validate_authorial_narration_style(document)


def test_jesus_first_person_voice_accepts_literal_quote_with_third_person_name() -> None:
    document = ScriptDocument(
        title="Eu sabia quem me trairia",
        sections=[
            ScriptSection(
                section_id="opening",
                role="opening",
                title="Abertura",
                narration="Eu sabia quem me trairia e mantive Judas entre os doze.",
                claim_classification=["verified_context"],
            ),
            ScriptSection(
                section_id="scripture",
                role="biblical_narrative",
                title="Citação",
                narration=(
                    "Em João, capítulo 6, versículos 70 a 71, lemos: "
                    "“Jesus lhes respondeu: um de vocês é um diabo.”"
                ),
                claim_classification=["biblical_text"],
            ),
        ],
    )

    validate_jesus_first_person_narration(document)


def test_jesus_first_person_voice_rejects_third_person_opening() -> None:
    with pytest.raises(ValueError, match="eu lírico"):
        validate_jesus_first_person_narration(
            _document("Jesus sabia que Judas o trairia e ainda o manteve entre os doze.")
        )


def test_jesus_first_person_voice_rejects_third_person_development() -> None:
    document = ScriptDocument(
        title="Eu sabia quem me trairia",
        sections=[
            ScriptSection(
                section_id="opening",
                role="opening",
                title="Abertura",
                narration="Eu sabia quem me trairia.",
                claim_classification=["verified_context"],
            ),
            ScriptSection(
                section_id="development",
                role="biblical_narrative",
                title="Desenvolvimento",
                narration="Jesus permitiu que Judas permanecesse entre os doze.",
                claim_classification=["verified_context"],
            ),
        ],
    )

    with pytest.raises(ValueError, match="terceira pessoa"):
        validate_jesus_first_person_narration(document)


def test_biblical_explainer_accepts_third_person_jesus() -> None:
    validate_biblical_explainer_narration(
        _document("Jesus usou uma imagem profética conhecida para anunciar juízo e abalo cósmico.")
    )


def test_biblical_explainer_rejects_jesus_first_person_dramatization() -> None:
    with pytest.raises(ValueError, match="narrador bíblico explicativo"):
        validate_biblical_explainer_narration(
            _document("Minha frase anuncia que as estrelas cairão do céu.")
        )
