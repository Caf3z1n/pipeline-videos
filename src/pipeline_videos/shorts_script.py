from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pipeline_videos.editorial import quoted_comment_response
from pipeline_videos.models import (
    ShortsScriptPackage,
    SourceEntry,
    SourceLibrary,
    StageStatus,
)
from pipeline_videos.review import save_biblical_review
from pipeline_videos.script import save_script
from pipeline_videos.state import complete_stage, invalidate, start_stage, wait_stage
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json
from pipeline_videos.utils import (
    compact_json_schema,
    relative_posix,
    sha256_file,
    sha256_value,
    tokenize_words,
    utc_now,
)

SHORTS_SCRIPT_QUEUE_SCHEMA_VERSION = "2.0"
SHORTS_SCRIPT_ALGORITHM_VERSION = "3.0"
LEGACY_SCRIPTURE_BOUNDARY_PHRASE = "Até aqui, o texto bíblico."

_GENERIC_AI_PHRASES = (
    "a coragem nasce de",
    "acolha a sua dor",
    "essa passagem nos convida",
    "escolha um próximo passo responsável",
    "nomeie o seu medo",
    "para a vida real",
)
_MULTI_SCENE_VISUAL_TERMS = (
    "corte rápido para",
    "colagem",
    "díptico",
    "momentos diferentes",
    "montagem de",
    "painéis",
    "sequência de cenas",
    "split-screen",
    "storyboard",
    "tela dividida",
    "três ações",
    "três cenas",
    "três vinhetas",
    "tríptico",
    "várias cenas",
    "várias ações",
    "vinhetas",
)

_BIBLICAL_REFERENCE_PATTERN = re.compile(
    r"^(?P<book>.+?)\s+(?P<chapter>\d+)\s*:\s*(?P<verse>\d+)"
    r"(?:\s*[-–—]\s*(?P<end_verse>\d+))?$"
)
_RAW_NUMERIC_REFERENCE_PATTERN = re.compile(r"\b\d+\s*:\s*\d+\b")


def spoken_biblical_reference(reference: str) -> str:
    """Converte uma referência compacta para a forma natural exigida na locução."""
    normalized = " ".join(reference.split())
    match = _BIBLICAL_REFERENCE_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError(f"Referência bíblica sem formato reconhecido para locução: {reference!r}.")
    book = match.group("book")
    chapter = match.group("chapter")
    verse = match.group("verse")
    end_verse = match.group("end_verse")
    if end_verse:
        return f"{book}, capítulo {chapter}, versículos {verse} a {end_verse}"
    return f"{book}, capítulo {chapter}, versículo {verse}"


def _literal_opening_candidates(layout: ProjectLayout) -> list[SourceEntry]:
    sources = read_model(layout.sources, SourceLibrary)
    return [
        source
        for source in sources.entries
        if source.kind == "biblical_text"
        and source.verified
        and source.passage
        and source.translation
        and source.excerpt
        and source.author_or_publisher
        and source.accessed_at
        and source.rights_note
    ]


def _require_literal_opening_source(layout: ProjectLayout) -> list[SourceEntry]:
    candidates = _literal_opening_candidates(layout)
    if candidates:
        return candidates
    raise ValueError(
        "O roteiro de Short exige ao menos uma fonte biblical_text verificada com passage, "
        "translation, excerpt, author_or_publisher, accessed_at e rights_note. Registre o "
        "texto exato de um versículo conhecido, curto e claro antes de preparar o roteiro."
    )


def _stable_inputs(layout: ProjectLayout) -> dict[str, Any]:
    required = {
        "briefing": layout.briefing,
        "sources": layout.sources,
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Entradas ausentes para o roteirista de Shorts: " + ", ".join(missing)
        )
    project = layout.load_manifest()
    if project.video_format != "short":
        raise RuntimeError("A etapa shorts_script só é válida para projetos no formato Short.")
    return {
        "algorithm": SHORTS_SCRIPT_ALGORITHM_VERSION,
        "project_id": project.project_id,
        "title": project.title,
        "target_duration_seconds": project.target_duration_seconds,
        "config": project.config.shorts_script,
        "briefing": read_json(layout.briefing),
        "sources": read_json(layout.sources),
    }


def shorts_script_input_hash(layout: ProjectLayout) -> str:
    return sha256_value(_stable_inputs(layout))


def _baseline_hash(layout: ProjectLayout) -> str | None:
    return sha256_file(layout.script) if layout.script.is_file() else None


def _recent_decisions(layout: ProjectLayout, *, limit: int = 12) -> list[dict[str, Any]]:
    if not layout.decisions.is_file():
        return []
    decisions: list[dict[str, Any]] = []
    for line in layout.decisions.read_text(encoding="utf-8").splitlines()[-limit:]:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            value = {"raw": line}
        if isinstance(value, dict):
            decisions.append(value)
    return decisions


def _shorts_context_payload(
    layout: ProjectLayout,
    literal_candidates: list[SourceEntry],
) -> dict[str, Any]:
    project = layout.load_manifest()
    sources = read_model(layout.sources, SourceLibrary)
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "project": {
            "project_id": project.project_id,
            "title": project.title,
            "target_duration_seconds": project.target_duration_seconds,
            "narrative_standard": project.config.shorts_script.narrative_standard,
        },
        "briefing": read_json(layout.briefing),
        "sources": [source.model_dump(mode="json") for source in sources.entries],
        "scripture_candidates": [source.source_id for source in literal_candidates],
        "verse_opening_candidates": [source.source_id for source in literal_candidates],
        "recent_decisions": _recent_decisions(layout),
    }
    if layout.script.is_file():
        payload["current_script_for_critique"] = read_json(layout.script)
    if layout.biblical_review.is_file():
        payload["current_biblical_review_for_critique"] = read_json(layout.biblical_review)
    return payload


def prepare_shorts_script_queue(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> dict[str, Any]:
    project = layout.load_manifest()
    config = project.config.shorts_script
    main_agent_mode = project.config.production.mode in {
        "light_manual",
        "light_automatic",
        "remote_review",
    }
    if not config.enabled or (not config.use_specialist_subagent and not main_agent_mode):
        raise RuntimeError("O subagente especialista de roteiro está desativado neste projeto.")
    literal_candidates = (
        _require_literal_opening_source(layout)
        if config.require_verified_scripture or config.require_literal_verse_opening
        else _literal_opening_candidates(layout)
    )
    input_hash = shorts_script_input_hash(layout)
    stage = project.stages["shorts_script"]
    if (
        not force
        and layout.shorts_script_queue.is_file()
        and layout.shorts_script_context.is_file()
        and stage.input_hash == input_hash
    ):
        queue = read_json(layout.shorts_script_queue)
        if stage.status in {StageStatus.WAITING_EXTERNAL, StageStatus.COMPLETED}:
            return queue

    existing_script = read_json(layout.script) if layout.script.is_file() else None
    next_version = int(existing_script.get("version", 0)) + 1 if existing_script else 1
    baseline_hash = _baseline_hash(layout)
    job_id = f"shorts-script-{input_hash[:16]}"
    context = _shorts_context_payload(layout, literal_candidates)
    write_json(layout.shorts_script_context, context)
    main_passage = str(context["briefing"].get("main_passage", "")).strip()
    main_passage_spoken = spoken_biblical_reference(main_passage)
    inputs: dict[str, str] = {
        "compact_context": relative_posix(layout.shorts_script_context, layout.root),
    }
    current_render = layout.root / "render" / "final-v1.mp4"
    if not current_render.is_file() and layout.final_video.is_file():
        current_render = layout.final_video
    if current_render.is_file():
        inputs["current_video_for_critique"] = relative_posix(current_render, layout.root)

    payload: dict[str, Any] = {
        "schema_version": SHORTS_SCRIPT_QUEUE_SCHEMA_VERSION,
        "generated_at": utc_now(),
        "project_id": project.project_id,
        "job_id": job_id,
        "input_hash": input_hash,
        "baseline_script_hash": baseline_hash,
        "next_script_version": next_version,
        "status": StageStatus.WAITING_EXTERNAL.value,
        "role": config.specialist_role,
        "orchestration": {
            "mode": "main_agent" if main_agent_mode else "subagent",
            "worker_id": "main" if main_agent_mode else "shorts-script-specialist",
            "single_writer": True,
            "may_modify_project_state": False,
            "may_publish": False,
            "reasoning_effort": "low" if main_agent_mode else "high",
            "recommended_model": "gpt-5.6-luna" if main_agent_mode else "gpt-5.6-sol",
            "context_policy": "compact_only",
            "fork_turns": "none" if not main_agent_mode else None,
        },
        "inputs": inputs,
        "outputs": {
            "package": relative_posix(layout.shorts_script_package_staging, layout.root),
        },
        "schemas": {
            "package": compact_json_schema(ShortsScriptPackage.model_json_schema()),
        },
        "scripture_candidates": [source.source_id for source in literal_candidates],
        "verse_opening_candidates": [source.source_id for source in literal_candidates],
        "spoken_reference_standard": {
            "compact_reference": main_passage,
            "spoken_reference": main_passage_spoken,
            "lead_in_example": f"Em {main_passage_spoken}, lemos:",
        },
        "scripture_boundary_standard": {
            "spoken_phrase": None,
            "boundary_mode": "quotation_sections_and_claim_classification",
            "direct_quote_caption_style": "italic",
            "context_labels_enabled": False,
        },
        "scripture_quote_standard": {
            "scope": "verse_text_only",
            "opening_quote": "“",
            "closing_quote": "”",
            "opening_example": f"Em {main_passage_spoken}, lemos: “texto do versículo.”",
        },
        "instructions": [
            (
                "No modo light, o agente principal deve produzir este pacote diretamente em uma "
                "única passagem, sem criar um subagente de roteiro separado."
                if main_agent_mode
                else "O shorts_script_specialist deve produzir o pacote isoladamente no staging."
            ),
            (
                "Leia primeiro e normalmente somente inputs.compact_context. Ele já reúne "
                "briefing, fontes, decisões recentes e material anterior necessário; abra o "
                "vídeo anterior apenas quando ele existir e a crítica visual for indispensável."
            ),
            (
                "Analise criticamente o roteiro e o vídeo atuais quando existirem; "
                "não os copie de forma mecânica."
            ),
            (
                (
                    "Use obrigatoriamente o padrão human_hook_to_bible. Comece com uma frase "
                    "humana curta que faça o espectador se reconhecer numa dor, pergunta ou "
                    "contraste; leve essa tensão imediatamente ao contexto bíblico e cite o "
                    "versículo verificado logo depois."
                )
                if config.narrative_standard == "human_hook_to_bible"
                else (
                    "Use o padrão legado verse_first: comece com a referência e o versículo, "
                    "mas faça a transição seguinte soar natural e não use rótulos falados."
                )
            ),
            (
                "Preencha strategy.verse_opening com a citação bíblica principal escolhida entre "
                "scripture_candidates. Copie verse_text exatamente do excerpt verificado e "
                "registre tradução e fonte. Dê preferência a um versículo conhecido, curto e "
                "fácil de entender. Use additional_scriptures somente se houver outra fonte "
                "verificada e se a segunda citação realmente acrescentar algo sem apertar o ritmo."
            ),
            (
                "Toda referência bíblica falada deve escrever capítulo e versículo por extenso "
                "na locução. Nunca narre apenas 'Livro X:Y'. Use o padrão 'Livro, capítulo X, "
                "versículo Y'; por exemplo: "
                f"'{main_passage_spoken}'. Na seção da citação, use uma construção como "
                f"'Em {main_passage_spoken}, lemos:' em verse_opening.lead_in."
            ),
            (
                "Na seção da citação, deixe a apresentação do narrador fora das aspas e coloque "
                "somente verse_text entre aspas. Use exatamente o padrão "
                f"'Em {main_passage_spoken}, lemos: “texto literal do versículo.”' Não abra "
                "aspas antes de 'Em' e não inclua a referência dentro da citação. As aspas são "
                "pontuação visual e não devem ser pronunciadas pela voz."
            ),
            (
                "A legenda não deve exibir os rótulos TEXTO BÍBLICO ou REFLEXÃO. Somente as "
                "palavras copiadas literalmente de verse_text recebem itálico; apresentação, "
                "referência, paráfrases, contexto e reflexão permanecem sem itálico."
            ),
            (
                "Não fale nem escreva 'Até aqui, o texto bíblico' e não use outro rótulo para "
                "anunciar a mudança. Deixe o limite da citação claro pelas aspas, pelo itálico, "
                "pelas seções estruturadas e pela classificação das afirmações; depois siga com "
                "uma transição cotidiana e natural."
            ),
            (
                "A locução inteira deve permanecer em português. Termos técnicos podem existir "
                "nos campos de estratégia, mas nunca narre expressões como 'open loop', 'payoff', "
                "'storytelling' ou 'call to action'. Não diga 'o texto fala/diz/registra'; prefira "
                "'a Bíblia diz' ou verbalize Livro, capítulo e versículo."
            ),
            (
                (
                    "O texto exato de strategy.hook deve ser o prefixo da abertura humana, usar "
                    f"no máximo {config.hook_max_words} palavras, mirar "
                    f"{config.hook_target_seconds:.1f}s e usar de uma a três técnicas em "
                    "hook_techniques: identificação, espelhamento emocional, curiosidade, "
                    "contraste, especificidade, quebra de padrão ou open loop. A técnica deve "
                    "servir à verdade da mensagem, nunca manipular."
                )
                if config.narrative_standard == "human_hook_to_bible"
                else (
                    "No padrão verse_first, strategy.hook é o prefixo da locução bíblica, com "
                    f"no máximo {config.hook_max_words} palavras e alvo de "
                    f"{config.hook_target_seconds:.1f}s."
                )
            ),
            (
                (
                    "Siga seis movimentos: gancho humano, citação bíblica, contexto ou conflito "
                    "bíblico, verdade central que resolve o gancho, aplicação concreta e "
                    "encerramento. Em narrative_spine, use tension_section_id para o gancho, "
                    "verse_section_id para a citação e bridge_section_id para o contexto."
                )
                if config.narrative_standard == "human_hook_to_bible"
                else (
                    "Siga seis movimentos: versículo, pergunta de tensão, ponte bíblica, verdade "
                    "central, aplicação concreta e encerramento."
                )
            ),
            (
                "Escolha exatamente um development_mode: contextual_reflection para explicar "
                "o contexto e refletir; biblical_story_bridge para uma cena/personagem bíblico; "
                "biblical_image_bridge para uma imagem ou metáfora explicitamente bíblica; "
                "cross_reference_bridge para correlacionar outro versículo; ou promise_boundary "
                "para explicar o que o texto afirma e o que ele não promete."
            ),
            (
                "Use contextual_reflection como padrão seguro. Só escolha story, image ou "
                "cross_reference quando houver outra fonte bíblica verificada nas entradas; "
                "registre-a em narrative_spine.bridge_source_ids. Não invente alegorias."
            ),
            (
                "A tensão precisa ser honesta, reconhecível e específica. Resolva o loop na "
                "verdade central; não esconda informação trivial, ameace, use culpa, explore medo "
                "ou prometa benefício espiritual para manipular retenção."
            ),
            (
                f"Entregue entre {config.min_words} e {config.max_words} palavras, "
                f"adequado ao limite de {project.target_duration_seconds}s."
            ),
            (
                f"Mapeie pelo menos {config.minimum_retention_beats} beats de "
                "retenção, com valor real para o espectador e tempos crescentes."
            ),
            (
                "Inclua mudanças de ritmo e uma direção visual específica em cada seção, sem "
                "inventar fatos bíblicos. Cada visual_direction descreve um único momento, um "
                "único ambiente contínuo e uma ação principal; nunca peça vinhetas, painéis, "
                "colagem, tela dividida, storyboard ou várias cenas na mesma imagem."
            ),
            (
                "Na primeira seção, descreva em visual_direction uma única imagem-âncora "
                "vertical, fiel ao gancho e visualmente forte: assunto dominante, emoção "
                "ou gesto legível, conflito simples, luz direcional e espaço negativo no terço "
                "superior. Não escreva texto de capa dentro da imagem e não use choque gratuito."
            ),
            (
                "A aplicação precisa oferecer uma resposta prática específica para hoje, "
                "diferenciar texto bíblico de interpretação e evitar promessa de vitória "
                "automática."
            ),
            (
                "Use uma voz pastoral simples: a clareza acolhedora de um bom pastor ou padre "
                "conversando com uma pessoa, sem caricatura religiosa e sem tom de palestra. "
                "Escreva em português brasileiro falado, com frases curtas, palavras comuns, "
                "verbos concretos, contrações naturais e ritmo cotidiano. Leia tudo em voz alta "
                "e reescreva o que soar formal, terapêutico, publicitário ou com cara de IA."
            ),
            (
                "Evite fórmulas com aparência de texto gerado, como 'a coragem nasce de', "
                "'nomeie o seu medo', 'escolha um próximo passo responsável', 'acolha a sua dor', "
                "'essa passagem nos convida' e 'para a vida real'. Diga concretamente o que a "
                "pessoa pode pensar, sentir ou fazer, sem empilhar substantivos abstratos."
            ),
            (
                "Não repita a mesma construção entre vídeos. A voz pode usar perguntas naturais, "
                "contrações e transições como 'talvez você pense', 'repara nisso', 'só que' e "
                "'então', quando combinarem com o tom; não transforme essas expressões em bordões."
            ),
            (
                "Faça as frases serem magnéticas pela verdade e pela forma: fale diretamente com "
                "a experiência do público, crie imagens mentais, use contraste, detalhes concretos "
                "e perguntas que a pessoa realmente faria. Não use palavras raras só para soar "
                "profundo e não escreva frases de efeito vazias."
            ),
            (
                "Antes do CTA, escolha exatamente um comment_prompt adequado ao vídeo: "
                "keyword_reaffirmation para uma palavra temática; next_step_commitment para um "
                "compromisso curto; sentence_completion para o espectador completar uma frase; "
                "message_declaration para reafirmar a mensagem em até oito palavras; ou "
                "community_encouragement para deixar uma palavra de apoio a outras pessoas."
            ),
            (
                "Preencha comment_prompt.prompt_text com o convite completo falado, "
                "response_cue com a palavra, frase ou início de frase esperado, objective com o "
                "objetivo humano da interação e fit_reason explicando por que esse modo combina "
                "com este vídeo. Coloque response_cue entre aspas tipográficas no convite, por "
                "exemplo: escreva “CORAGEM” nos comentários. O pipeline deixará essa resposta "
                "sozinha na legenda e inserirá um segundo de silêncio. Use somente um pedido de "
                "comentário e mantenha-o natural."
            ),
            (
                "O comentário nunca pode funcionar como condição para bênção, resposta de Deus, "
                "cura, proteção, milagre ou prova de fé. Não use culpa, urgência artificial, "
                "promessa de resultado nem frases como 'comente amém para receber'."
            ),
            (
                "Encerre sempre com o CTA exato, imediatamente depois do convite para comentar: "
                f"'{config.final_subscribe_cta}' Não altere, abrevie nem acrescente texto depois."
            ),
            (
                "Produza uma revisão bíblica separada, registre incertezas e não "
                "deixe finding blocking aberto."
            ),
            (
                "Use somente source_id e referências presentes nas entradas do projeto. "
                "Ao menos uma citação literal verificada é obrigatória. Não estenda uma citação "
                "para versos vizinhos não registrados e não use o versículo fora do significado "
                "permitido pelo contexto e pelos limites de interpretação."
            ),
            "Repita job_id e input_hash exatamente e grave somente o package declarado no staging.",
            (
                "Não altere project.json, roteiro ativo, aprovações, manifestos ou "
                "qualquer arquivo fora do staging."
            ),
            "Não prometa viralização, retenção ou CTR e não invente métricas.",
        ],
    }
    start_stage(layout, "shorts_script", input_hash, provider=config.specialist_role)
    write_json(layout.shorts_script_queue, payload)
    wait_stage(
        layout,
        "shorts_script",
        input_hash,
        "Aguardando o subagente shorts_script_specialist escrever somente no staging.",
        [
            relative_posix(layout.shorts_script_queue, layout.root),
            relative_posix(layout.shorts_script_context, layout.root),
        ],
    )
    return payload


def _safe_staging_file(layout: ProjectLayout, relative_path: str) -> Path:
    path = (layout.root / relative_path).resolve()
    try:
        path.relative_to(layout.shorts_script_staging.resolve())
    except ValueError as exc:
        raise ValueError(f"Caminho fora do staging do roteirista: {relative_path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"Saída do roteirista ausente: {path}")
    return path


def _normalized(text: str) -> str:
    return " ".join(token.casefold() for token in tokenize_words(text))


def _collapsed(text: str) -> str:
    return " ".join(text.split())


def _contains_normalized(container: str, fragment: str) -> bool:
    normalized_fragment = _normalized(fragment)
    return bool(normalized_fragment) and normalized_fragment in _normalized(container)


def _validate_scripture_source(citation: Any, source_by_id: dict[str, SourceEntry]) -> None:
    spoken_reference = spoken_biblical_reference(citation.reference)
    if not _contains_normalized(citation.lead_in, spoken_reference):
        raise ValueError(
            "A introdução da citação precisa narrar a referência no formato "
            "'Livro, capítulo X, versículo Y'."
        )
    source = source_by_id.get(citation.source_id)
    if source is None or source.kind != "biblical_text" or not source.verified:
        raise ValueError("Toda citação precisa apontar para uma fonte bíblica verificada.")
    required_source_fields = {
        "passage": source.passage,
        "translation": source.translation,
        "excerpt": source.excerpt,
        "author_or_publisher": source.author_or_publisher,
        "accessed_at": source.accessed_at,
        "rights_note": source.rights_note,
    }
    missing_fields = [name for name, value in required_source_fields.items() if not value]
    if missing_fields:
        raise ValueError("A fonte literal está incompleta: " + ", ".join(missing_fields) + ".")
    if _normalized(source.translation or "") != _normalized(citation.translation):
        raise ValueError("A tradução do versículo diverge da tradução registrada na fonte.")
    if citation.quote_mode != "literal":
        raise ValueError("A citação bíblica obrigatória precisa ser literal e verificada.")
    if _normalized(source.excerpt or "") != _normalized(citation.verse_text):
        raise ValueError("O texto literal da citação diverge do excerpt verificado da fonte.")


def _citation_forms(citation: Any) -> set[str]:
    return {
        _collapsed(f'{citation.lead_in.strip()} "{citation.verse_text.strip()}"'),
        _collapsed(f"{citation.lead_in.strip()} “{citation.verse_text.strip()}”"),
    }


def _validate_narrative_spine(package: ShortsScriptPackage) -> dict[str, Any]:
    strategy = package.strategy
    spine = strategy.narrative_spine
    if spine is None:
        raise ValueError("O roteiro precisa preencher strategy.narrative_spine.")
    sections = package.script.sections
    section_by_id = {section.section_id: section for section in sections}
    spine_ids = [
        spine.verse_section_id,
        spine.tension_section_id,
        spine.bridge_section_id,
        spine.meaning_section_id,
        spine.application_section_id,
    ]
    if len(set(spine_ids)) != len(spine_ids):
        raise ValueError("Cada movimento da espinha narrativa precisa de uma seção distinta.")
    if not set(spine_ids).issubset(section_by_id):
        raise ValueError("A espinha narrativa referencia section_id inexistente.")
    if len(sections) < 6 or sections[-1].section_id in spine_ids:
        raise ValueError("O Short precisa de cinco movimentos editoriais e um encerramento.")
    if section_by_id[spine.application_section_id].role != "application":
        raise ValueError("A resposta prática precisa usar uma seção role=application.")
    mapped_text = (
        (spine.tension_section_id, spine.tension_question, "tension_question"),
        (spine.bridge_section_id, spine.bridge_statement, "bridge_statement"),
        (spine.meaning_section_id, spine.central_truth, "central_truth"),
        (spine.application_section_id, spine.practical_response, "practical_response"),
    )
    for section_id, required_text, field_name in mapped_text:
        if not _contains_normalized(section_by_id[section_id].narration, required_text):
            raise ValueError(f"narrative_spine.{field_name} precisa aparecer na seção mapeada.")
    if _normalized(strategy.open_loop) != _normalized(spine.tension_question):
        raise ValueError("strategy.open_loop precisa repetir a tensão central da espinha.")
    return section_by_id


def _validate_scripture_contract(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
    sources: SourceLibrary,
    section_by_id: dict[str, Any],
) -> set[str]:
    project = layout.load_manifest()
    strategy = package.strategy
    primary = strategy.verse_opening
    if primary is None:
        raise ValueError("O roteiro precisa citar ao menos um versículo bíblico verificado.")
    citations = [primary, *strategy.additional_scriptures]
    if len(citations) > 2:
        raise ValueError("Um Short pode usar no máximo duas citações literais para manter clareza.")
    source_by_id = {source.source_id: source for source in sources.entries}
    for citation in citations:
        _validate_scripture_source(citation, source_by_id)
        if (
            citation.target_end_seconds
            > project.config.shorts_script.scripture_citation_max_end_seconds
        ):
            raise ValueError("A citação bíblica termina tarde demais para o ritmo do Short.")
    spine = strategy.narrative_spine
    assert spine is not None
    primary_section = section_by_id[spine.verse_section_id]
    if _collapsed(primary_section.narration) not in _citation_forms(primary):
        raise ValueError(
            "A seção bíblica deve deixar a referência fora das aspas e colocar somente o "
            "versículo literal entre aspas."
        )
    for citation in strategy.additional_scriptures:
        forms = _citation_forms(citation)
        if not any(
            any(form in _collapsed(section.narration) for form in forms)
            for section in package.script.sections
        ):
            raise ValueError("Uma citação complementar declarada não aparece no roteiro.")
    return {citation.source_id for citation in citations}


def _validate_bridge_sources(
    package: ShortsScriptPackage,
    sources: SourceLibrary,
    required_source_ids: set[str],
) -> None:
    strategy = package.strategy
    spine = strategy.narrative_spine
    assert spine is not None
    source_by_id = {source.source_id: source for source in sources.entries}
    bridge_source_ids = set(spine.bridge_source_ids)
    external_bridge_modes = {
        "biblical_story_bridge",
        "biblical_image_bridge",
        "cross_reference_bridge",
    }
    if spine.development_mode in external_bridge_modes:
        complementary_ids = bridge_source_ids - required_source_ids
        if not complementary_ids:
            raise ValueError("O modo de ponte escolhido exige outra fonte bíblica verificada.")
        if any(
            source_id not in source_by_id
            or source_by_id[source_id].kind != "biblical_text"
            or not source_by_id[source_id].verified
            for source_id in complementary_ids
        ):
            raise ValueError("A ponte referencia fonte bíblica ausente ou não verificada.")
    required_source_ids |= bridge_source_ids
    if not required_source_ids.issubset(package.script.source_ids):
        raise ValueError("O roteiro não registra todas as fontes bíblicas usadas.")
    if not required_source_ids.issubset(strategy.source_ids):
        raise ValueError("A estratégia não registra todas as fontes bíblicas usadas.")


def _validate_verse_first_standard(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
    sources: SourceLibrary,
) -> None:
    project = layout.load_manifest()
    strategy = package.strategy
    spine = strategy.narrative_spine
    if strategy.verse_opening is None or spine is None:
        raise ValueError(
            "O padrão verse_first exige strategy.verse_opening e strategy.narrative_spine."
        )
    if spine.pattern != "verse_first" or strategy.hook_type != "scripture_opening":
        raise ValueError("O gancho verse_first precisa usar pattern e hook_type bíblicos.")
    if (
        strategy.verse_opening.target_end_seconds
        > project.config.shorts_script.verse_opening_max_seconds
    ):
        raise ValueError("O versículo termina tarde demais para a abertura configurada.")
    section_by_id = _validate_narrative_spine(package)
    if spine.verse_section_id != package.script.sections[0].section_id:
        raise ValueError("O versículo precisa ocupar a primeira seção do padrão verse_first.")
    if section_by_id[spine.verse_section_id].role != "opening":
        raise ValueError("A seção do versículo precisa usar role=opening.")
    required_source_ids = _validate_scripture_contract(layout, package, sources, section_by_id)
    _validate_bridge_sources(package, sources, required_source_ids)


def _validate_human_hook_standard(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
    sources: SourceLibrary,
) -> None:
    strategy = package.strategy
    spine = strategy.narrative_spine
    if strategy.verse_opening is None or spine is None:
        raise ValueError("O padrão human_hook_to_bible exige citação bíblica e espinha narrativa.")
    if spine.pattern != "human_hook_to_bible":
        raise ValueError("narrative_spine.pattern precisa ser human_hook_to_bible.")
    if strategy.hook_type == "scripture_opening":
        raise ValueError(
            "O novo padrão precisa começar com um gancho humano, não com a referência."
        )
    if not 1 <= len(strategy.hook_techniques) <= 3:
        raise ValueError("O gancho precisa registrar de uma a três técnicas naturais de atenção.")
    section_by_id = _validate_narrative_spine(package)
    ordered_ids = [section.section_id for section in package.script.sections]
    expected_order = [
        spine.tension_section_id,
        spine.verse_section_id,
        spine.bridge_section_id,
        spine.meaning_section_id,
        spine.application_section_id,
    ]
    if [ordered_ids.index(section_id) for section_id in expected_order] != sorted(
        ordered_ids.index(section_id) for section_id in expected_order
    ):
        raise ValueError("A estrutura deve seguir gancho, Bíblia, contexto, sentido e aplicação.")
    if spine.tension_section_id != package.script.sections[0].section_id:
        raise ValueError("A primeira seção precisa ser o gancho humano do Short.")
    if ordered_ids.index(spine.verse_section_id) != 1:
        raise ValueError("A citação bíblica precisa entrar imediatamente depois do gancho.")
    if section_by_id[spine.verse_section_id].role != "biblical_narrative":
        raise ValueError("A citação após o gancho precisa usar role=biblical_narrative.")
    required_source_ids = _validate_scripture_contract(layout, package, sources, section_by_id)
    _validate_bridge_sources(package, sources, required_source_ids)


def _validate_engagement_contract(layout: ProjectLayout, package: ShortsScriptPackage) -> None:
    config = layout.load_manifest().config.shorts_script
    strategy = package.strategy
    comment_prompt = strategy.comment_prompt
    if config.require_comment_prompt and comment_prompt is None:
        raise ValueError("O encerramento precisa escolher um strategy.comment_prompt.")
    if comment_prompt is not None:
        if not all(
            value.strip()
            for value in (
                comment_prompt.prompt_text,
                comment_prompt.response_cue,
                comment_prompt.objective,
                comment_prompt.fit_reason,
            )
        ):
            raise ValueError(
                "O comment_prompt precisa explicar texto, resposta, objetivo e escolha."
            )
        if len(tokenize_words(comment_prompt.response_cue)) > config.comment_response_max_words:
            raise ValueError("A resposta sugerida para o comentário está longa demais.")
        if not _contains_normalized(comment_prompt.prompt_text, comment_prompt.response_cue):
            raise ValueError("comment_prompt.response_cue precisa aparecer no convite falado.")
        normalized_prompt = _normalized(comment_prompt.prompt_text)
        if "coment" not in normalized_prompt and "escrev" not in normalized_prompt:
            raise ValueError("O comment_prompt precisa convidar claramente a escrever ou comentar.")
        forbidden_engagement_bait = (
            "comente amém para receber",
            "escreva amém para receber",
            "para receber uma bênção",
            "para deus te abençoar",
            "prove sua fé",
            "se você ama deus",
        )
        if any(
            _normalized(forbidden) in normalized_prompt for forbidden in forbidden_engagement_bait
        ):
            raise ValueError("O convite para comentar usa pressão ou promessa espiritual indevida.")
        if layout.load_manifest().config.editorial_policy.require_quoted_comment_response:
            quoted_response = quoted_comment_response(comment_prompt.prompt_text)
            if not quoted_response or _normalized(quoted_response) != _normalized(
                comment_prompt.response_cue
            ):
                raise ValueError(
                    "comment_prompt.response_cue precisa aparecer entre aspas no convite falado."
                )

    if config.require_subscribe_cta:
        if _collapsed(strategy.cta) != config.final_subscribe_cta:
            raise ValueError("O CTA final diverge do texto padronizado do projeto.")
        expected_closing = (
            f"{comment_prompt.prompt_text.strip()} {config.final_subscribe_cta}"
            if comment_prompt is not None
            else config.final_subscribe_cta
        )
        if _collapsed(package.script.sections[-1].narration) != _collapsed(expected_closing):
            raise ValueError(
                "O encerramento deve conter somente o convite para comentar seguido do CTA fixo."
            )
        if config.narrative_standard == "human_hook_to_bible":
            normalized_cta = _normalized(strategy.cta)
            if "curt" not in normalized_cta or "compartilh" not in normalized_cta:
                raise ValueError(
                    "O CTA novo precisa pedir para curtir, compartilhar e se inscrever."
                )


def _validate_language_and_visuals(package: ShortsScriptPackage) -> None:
    narration = _normalized(package.script.narration_text())
    if _normalized(LEGACY_SCRIPTURE_BOUNDARY_PHRASE) in narration:
        raise ValueError("O roteiro não pode narrar o rótulo 'Até aqui, o texto bíblico'.")
    if any(_normalized(phrase) in narration for phrase in _GENERIC_AI_PHRASES):
        raise ValueError(
            "O roteiro contém uma construção genérica ou com aparência de texto de IA."
        )
    for section in package.script.sections:
        direction = _normalized(section.visual_direction or "")
        if any(_normalized(term) in direction for term in _MULTI_SCENE_VISUAL_TERMS):
            raise ValueError(
                f"A direção visual de {section.section_id} pede múltiplas cenas na mesma imagem."
            )


def _validate_package(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
    queue: dict[str, Any],
) -> None:
    if package.job_id != queue.get("job_id") or package.input_hash != queue.get("input_hash"):
        raise RuntimeError("O pacote do roteirista pertence a uma fila antiga.")
    project = layout.load_manifest()
    config = project.config.shorts_script
    script = package.script
    strategy = package.strategy
    sections = script.sections
    if not sections or sections[0].role != "opening" or sections[-1].role != "closing":
        raise ValueError("O roteiro precisa começar com opening e terminar com closing.")
    section_ids = [section.section_id for section in sections]
    if len(section_ids) != len(set(section_ids)):
        raise ValueError("O roteiro contém section_id duplicado.")
    if any(_RAW_NUMERIC_REFERENCE_PATTERN.search(section.narration) for section in sections):
        raise ValueError(
            "Referências bíblicas faladas não podem usar o formato numérico 'Livro X:Y'; "
            "narre 'Livro, capítulo X, versículo Y'."
        )
    words = tokenize_words(script.narration_text())
    if not config.min_words <= len(words) <= config.max_words:
        raise ValueError(
            f"O roteiro tem {len(words)} palavras; esperado entre "
            f"{config.min_words} e {config.max_words}."
        )
    estimated_duration = len(words) / config.estimated_narration_wpm * 60
    if estimated_duration > project.target_duration_seconds + config.duration_tolerance_seconds:
        raise ValueError(
            f"O roteiro estima {estimated_duration:.1f}s na voz configurada e ultrapassa o "
            f"alvo de {project.target_duration_seconds}s."
        )
    hook_words = tokenize_words(strategy.hook)
    if len(hook_words) > config.hook_max_words:
        raise ValueError("O gancho ultrapassa o limite de palavras configurado.")
    if strategy.hook_target_end_seconds > config.hook_target_seconds + 0.5:
        raise ValueError("O gancho termina tarde demais para a meta inicial do Short.")
    if not _normalized(sections[0].narration).startswith(_normalized(strategy.hook)):
        raise ValueError("strategy.hook precisa ser o prefixo exato da abertura.")
    closing = _normalized(sections[-1].narration)
    if not closing.endswith(_normalized(strategy.cta)):
        raise ValueError("strategy.cta precisa ser o sufixo exato do encerramento.")
    if config.require_subscribe_cta and "inscrev" not in closing:
        raise ValueError("O CTA final precisa convidar o espectador a se inscrever.")
    if not strategy.reason_to_subscribe.strip():
        raise ValueError("O CTA precisa explicar o benefício de acompanhar o canal.")
    if len(strategy.retention_beats) < config.minimum_retention_beats:
        raise ValueError("A estratégia contém poucos beats de retenção.")
    beat_ids = [beat.section_id for beat in strategy.retention_beats]
    if not set(beat_ids).issubset(section_ids):
        raise ValueError("A estratégia referencia section_id inexistente.")
    previous_start = -1.0
    for beat in strategy.retention_beats:
        if beat.target_start_seconds < previous_start:
            raise ValueError("Os beats de retenção não estão em ordem temporal.")
        if beat.target_end_seconds > project.target_duration_seconds:
            raise ValueError("Um beat ultrapassa a duração-alvo do Short.")
        previous_start = beat.target_start_seconds
    if not any(beat.opens_loop for beat in strategy.retention_beats):
        raise ValueError("A estratégia precisa abrir ao menos um loop de curiosidade.")
    if not any(beat.resolves_loop for beat in strategy.retention_beats):
        raise ValueError("A estratégia precisa resolver o loop de curiosidade.")
    _validate_language_and_visuals(package)
    sources = read_model(layout.sources, SourceLibrary)
    if config.narrative_standard == "verse_first":
        _validate_verse_first_standard(layout, package, sources)
    else:
        _validate_human_hook_standard(layout, package, sources)
    _validate_engagement_contract(layout, package)
    valid_source_ids = {entry.source_id for entry in sources.entries}
    referenced_sources = set(script.source_ids) | set(strategy.source_ids)
    if not referenced_sources.issubset(valid_source_ids):
        raise ValueError("O roteiro ou a estratégia referencia source_id inexistente.")
    if set(strategy.section_ids) != set(section_ids):
        raise ValueError("strategy.section_ids precisa mapear todas as seções do roteiro.")
    for section in sections:
        if not section.biblical_basis:
            raise ValueError(f"A seção {section.section_id} não registra base bíblica.")
        if not section.claim_classification:
            raise ValueError(f"A seção {section.section_id} não classifica suas afirmações.")
    review = package.biblical_review
    if review.script_version != script.version:
        raise ValueError("A revisão bíblica do pacote não corresponde ao roteiro entregue.")
    if review.has_open_blockers:
        raise ValueError("A revisão bíblica do pacote ainda possui bloqueios abertos.")


def import_shorts_script_package(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> ShortsScriptPackage:
    if not layout.shorts_script_queue.is_file():
        raise FileNotFoundError(f"Fila do roteirista ausente: {layout.shorts_script_queue}")
    queue = read_json(layout.shorts_script_queue)
    if not isinstance(queue, dict) or queue.get("role") != "shorts_script_specialist":
        raise ValueError("Fila do roteirista inválida.")
    if layout.load_manifest().stages["shorts_script"].status == StageStatus.COMPLETED and not force:
        return read_model(layout.shorts_script_package, ShortsScriptPackage)
    if queue.get("input_hash") != shorts_script_input_hash(layout):
        raise RuntimeError("A fila do roteirista ficou obsoleta após mudanças editoriais.")
    baseline = queue.get("baseline_script_hash")
    if baseline != _baseline_hash(layout):
        raise RuntimeError("O roteiro ativo mudou depois que a fila do especialista foi criada.")
    package_path = _safe_staging_file(layout, queue["outputs"]["package"])
    package = read_model(package_path, ShortsScriptPackage)
    _validate_package(layout, package, queue)
    script = package.script.model_copy(deep=True)
    script.version = int(queue["next_script_version"])
    script.word_count = len(tokenize_words(script.narration_text()))
    config = layout.load_manifest().config.shorts_script
    script.estimated_duration_seconds = round(
        script.word_count / config.estimated_narration_wpm * 60,
        2,
    )
    review = package.biblical_review.model_copy(
        update={
            "script_version": script.version,
            "approved": False,
            "approval_note": None,
        }
    )
    final_package = package.model_copy(
        update={
            "script": script,
            "biblical_review": review,
        }
    )

    save_script(layout, script, provider="shorts-script-specialist")
    save_biblical_review(layout, review, provider="shorts-script-specialist")
    write_json(layout.shorts_script_package, final_package)
    write_json(layout.shorts_script_strategy, final_package.strategy)
    completed_queue = dict(queue)
    completed_queue["status"] = StageStatus.COMPLETED.value
    completed_queue["imported_at"] = utc_now()
    write_json(layout.shorts_script_queue, completed_queue)
    invalidate(
        layout,
        stages=("youtube_growth", "metadata", "thumbnail", "render", "validation"),
        approvals=("metadata", "final_render"),
        note="O roteiro especializado de Short foi importado.",
    )
    complete_stage(
        layout,
        "shorts_script",
        str(queue["input_hash"]),
        [
            relative_posix(layout.shorts_script_queue, layout.root),
            relative_posix(layout.shorts_script_context, layout.root),
            relative_posix(layout.shorts_script_package, layout.root),
            relative_posix(layout.shorts_script_strategy, layout.root),
        ],
        provider="shorts-script-specialist",
        usage={
            "words": script.word_count,
            "retention_beats": len(final_package.strategy.retention_beats),
            "context_bytes": layout.shorts_script_context.stat().st_size,
            "schema_bytes": len(
                json.dumps(queue["schemas"]["package"], ensure_ascii=False).encode("utf-8")
            ),
        },
        note="Roteiro de Short importado com gancho, payoff, valor e CTA empático.",
    )
    layout.append_event(
        {
            "event": "shorts_script_package_imported",
            "job_id": package.job_id,
            "script_version": script.version,
            "words": script.word_count,
        }
    )
    return final_package
