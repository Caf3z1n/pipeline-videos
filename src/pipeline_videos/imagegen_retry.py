from __future__ import annotations

from typing import Any


def imagegen_retry_policy() -> dict[str, Any]:
    """Return the shared, auditable retry contract for image generation."""
    return {
        "schema_version": "1.0",
        "mode": "same_prompt_then_continuity_preserving_alternative",
        "max_attempts_per_item": 3,
        "same_prompt_attempts": 2,
        "alternative_scene_attempts": 1,
        "retryable_error_codes": ["moderation_blocked"],
        "skip_existing_valid_staging": True,
        "continuity_reference": "nearest_successful_image_of_same_character",
        "immutable_on_alternative": [
            "character_identity",
            "apparent_age",
            "facial_features",
            "hairstyle",
            "wardrobe",
            "body_proportions_and_silhouette",
            "visual_standard",
            "color_palette",
            "environment_continuity",
            "narrative_meaning",
        ],
        "mutable_on_alternative": [
            "single_action",
            "camera_angle",
            "framing",
            "pose",
            "nonessential_prop_arrangement",
        ],
        "failure_behavior": "preserve_partial_state_and_report_original_error",
    }


def imagegen_retry_instructions(*, asset_label: str) -> list[str]:
    """Human-readable instructions that mirror :func:`imagegen_retry_policy`."""
    return [
        (
            f"Para cada {asset_label}, faça a primeira chamada com o prompt declarado sem "
            "alterá-lo. Se ela retornar moderation_blocked, repita exatamente o mesmo prompt "
            "uma única vez, pois o bloqueio pode ser um falso positivo de saída."
        ),
        (
            "Somente se a repetição idêntica também retornar moderation_blocked, faça uma "
            "terceira e última chamada com uma alternativa da mesma cena."
        ),
        (
            "Na alternativa, preserve obrigatoriamente identidade e idade aparente do "
            "personagem, rosto, cabelo, figurino, proporções, silhueta, padrão visual, paleta, "
            "continuidade do ambiente e sentido narrativo. Nunca substitua ou envelheça o "
            "personagem para contornar moderação."
        ),
        (
            "A alternativa pode mudar somente a ação única, pose, ângulo de câmera, "
            "enquadramento ou disposição de objetos não essenciais. Use a imagem bem-sucedida "
            "mais próxima do mesmo personagem como referência visual quando ela existir."
        ),
        (
            "Antes de gerar, pule staging_path já existente e válido. Após três bloqueios, "
            "preserve todo o estado parcial e reporte moderation_blocked como causa principal; "
            "não esconda a causa com erros posteriores de arquivos ausentes."
        ),
    ]
