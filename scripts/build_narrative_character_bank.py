from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from build_curated_verse_bank import (
    ACCESS_DATE,
    BOOKS,
    RIGHTS,
    SOURCE_REVISION,
    SOURCE_URL,
    TRANSLATION,
    VPL_ARTIFACT_URL,
    extract_text,
    parse_reference_part,
    parse_vpl,
    slugify,
    source_url,
)
from narrative_character_data import NARRATIVE_CHARACTER_STORIES, NarrativeStorySpec


def formatted_reference(code: str, reference_part: str) -> str:
    return f"{BOOKS[code][0]} {reference_part}"


def passage_payload(
    verses: dict[tuple[str, int, int], str],
    *,
    story_id: str,
    order: int,
    code: str,
    reference_part: str,
    role: str,
) -> dict[str, Any]:
    chapter, _, _ = parse_reference_part(reference_part)
    return {
        "passage_id": f"related-{order:02d}",
        "reference": formatted_reference(code, reference_part),
        "context_reference": formatted_reference(code, reference_part),
        "verse_text": extract_text(verses, code, reference_part),
        "source_url": source_url(code, chapter),
        "role": role,
        "spoken_by": "unspecified",
    }


def candidate_payload(
    verses: dict[tuple[str, int, int], str],
    spec: NarrativeStorySpec,
) -> dict[str, Any]:
    code = spec["code"]
    chapter, _, _ = parse_reference_part(spec["anchor"])
    reference = formatted_reference(code, spec["anchor"])
    characters = spec["characters"]
    story_id = spec["story_id"]
    return {
        "candidate_id": f"bpm-narrative-{story_id}",
        "source_id": f"bpm-narrative-{story_id}-anchor",
        "reference": reference,
        "context_reference": formatted_reference(code, spec["context"]),
        "book": BOOKS[code][0],
        "testament": BOOKS[code][1],
        "translation": TRANSLATION,
        "verse_text": extract_text(verses, code, spec["anchor"]),
        "source_url": source_url(code, chapter),
        "accessed_at": ACCESS_DATE,
        "rights_note": RIGHTS,
        "title": spec["title"],
        "slug": f"personagem-{slugify(story_id)}",
        "central_theme": spec["central_theme"],
        "practical_application": (
            "Reconstruir a trajetória do personagem pelo texto bíblico, distinguindo fatos, "
            "contexto verificado e aplicação pastoral."
        ),
        "audience": "pessoas que desejam compreender personagens e histórias bíblicas",
        "emotional_intent": (
            "aproximar o espectador do conflito humano do relato sem inventar pensamentos, "
            "motivações ou diálogos"
        ),
        "narration_tone": "narrativo, cinematográfico, reverente, claro e emocionalmente contido",
        "themes": ["character_story", slugify(spec["central_theme"])],
        "characters": characters,
        "character_aliases": list(dict.fromkeys([*characters, *spec["aliases"]])),
        "related_passages": [
            passage_payload(
                verses,
                story_id=story_id,
                order=order,
                code=passage["code"],
                reference_part=passage["reference"],
                role=passage["role"],
            )
            for order, passage in enumerate(spec["related"], start=1)
        ],
        "preferred_development_modes": ["biblical_story", "context", "cross_reference"],
        "interpretation_limits": [
            (
                "Não atribuir ao personagem pensamentos, emoções ou motivações que o texto "
                "não registra."
            ),
            "Não transformar reconstrução narrativa em citação bíblica literal.",
            "Distinguir relato principal, passagem paralela e aplicação ao espectador.",
        ],
        "claims_to_avoid": [
            "Não harmonizar relatos paralelos apagando diferenças de detalhe.",
            "Não apresentar silêncio do texto como certeza histórica.",
            "Não usar o desfecho do personagem como promessa automática ao espectador.",
        ],
        "visual_notes": [
            f"Manter {', '.join(characters)} reconhecível e consistente ao longo das cenas.",
            "Representar um momento, ambiente e ação por imagem, sem colagem.",
            "Preservar figurino, idade aparente e contexto histórico entre os movimentos.",
        ],
        "retention_archetypes": [
            "conflict_first",
            "counterintuitive_reveal",
            "stair_step_progression",
        ],
        "human_conflict": spec["human_conflict"],
        "central_question": f"O que a trajetória de {characters[0]} revela neste conflito?",
        "visual_progression": [
            "Abrir no instante de maior tensão humana do relato.",
            "Recuar ao contexto necessário sem interromper a progressão.",
            "Avançar pelas escolhas documentadas e suas consequências.",
            "Encerrar no desfecho bíblico, preservando limites e perguntas abertas.",
        ],
        "possible_reveal": spec["reveal"],
        "title_seed": spec["title"],
        "retention_potential_score": 0.9,
        "selection_weight": 1.0,
        "familiarity_score": 0.78,
        "clarity_score": 0.9,
        "catalog_segment": "narrative_story",
        "recommended_formats": ["short", "long"],
        "source_text_scope": "pericope_excerpt",
        "spoken_by": "unspecified",
    }


def validate_candidates(candidates: list[dict[str, Any]]) -> None:
    if len(candidates) < 80:
        raise RuntimeError(f"Esperadas ao menos 80 histórias; obtidas {len(candidates)}.")
    ids = [candidate["candidate_id"] for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise RuntimeError("O banco narrativo possui candidate_id duplicado.")
    for candidate in candidates:
        if len(candidate["related_passages"]) < 3:
            raise RuntimeError(
                f"{candidate['candidate_id']} precisa de ao menos três passagens relacionadas."
            )
        references = [
            candidate["reference"],
            *[passage["reference"] for passage in candidate["related_passages"]],
        ]
        if len(references) != len(set(references)):
            raise RuntimeError(
                f"{candidate['candidate_id']} repete a mesma referência no pacote narrativo."
            )
        if not candidate["characters"] or not candidate["character_aliases"]:
            raise RuntimeError(f"{candidate['candidate_id']} não possui índice de personagem.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("vpl", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    verses, _ = parse_vpl(args.vpl)
    candidates = [candidate_payload(verses, spec) for spec in NARRATIVE_CHARACTER_STORIES]
    validate_candidates(candidates)
    payload = {
        "schema_version": "1.0",
        "catalog_id": "bpm-narrative-character-v1",
        "translation": TRANSLATION,
        "publisher": "eBible.org",
        "source_url": SOURCE_URL,
        "rights_note": RIGHTS,
        "accessed_at": ACCESS_DATE,
        "source_artifact_url": VPL_ARTIFACT_URL,
        "source_artifact_sha256": hashlib.sha256(args.vpl.read_bytes()).hexdigest(),
        "source_revision": SOURCE_REVISION,
        "candidates": candidates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    character_count = len(
        {character for candidate in candidates for character in candidate["characters"]}
    )
    source_count = sum(1 + len(candidate["related_passages"]) for candidate in candidates)
    print(
        f"generated {len(candidates)} stories for {character_count} characters "
        f"with {source_count} verified excerpts at {args.output}"
    )


if __name__ == "__main__":
    main()
