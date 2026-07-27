from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pipeline_videos import cli
from pipeline_videos.approved_scripts import ApprovedScriptStore, materialize_approved_project
from pipeline_videos.editorial_board import (
    EditorialBoardStore,
    infer_editorial_cta_mode,
    validate_editorial_hook,
)
from pipeline_videos.storage import read_json, write_json


def _raw_script(topic_id: str, *, index: int, passed: bool = True) -> dict:
    hook = (
        "Eu poderia ter impedido tudo, mas permiti que aquela decisão continuasse. Então por quê?"
    )
    orientation = (
        "Eu conhecia o que estava acontecendo e mantive a pergunta diante dos meus discípulos."
    )
    evidence = (
        "Em João, capítulo 6, versículos 70 a 71, lemos: “Jesus lhes respondeu: "
        "‘Não escolhi eu a vocês, os doze, e um de vocês é um diabo?’ Ora, ele falava de "
        "Judas, filho de Simão Iscariotes, pois era ele quem o trairia, sendo um dos doze.”"
    )
    reversal = "Meu conhecimento não tomou a decisão no lugar dele."
    payoff = (
        "Essa tensão é a resposta central: conhecer uma escolha não significa obrigar alguém "
        "a fazê-la."
    )
    takeaway = "Você também continua responsável pelas decisões que toma diante do que já conhece."
    body = (
        f" {orientation} "
        f"{evidence} "
        "Mais tarde, ele procurou os sacerdotes e negociou minha entrega. "
        "Na mesa, ouviu o aviso e saiu para cumprir a própria decisão. Meu conhecimento não tomou "
        "a decisão no lugar dele. Lucas mantém lado a lado o cumprimento do plano e a "
        f"responsabilidade daquele que me traiu. {payoff} {takeaway} "
    )
    cta = (
        "Compartilhe com quem também faria essa pergunta."
        if index % 4 == 0
        else "Inscreva-se para acompanhar outras perguntas bíblicas respondidas com clareza."
        if index % 4 == 1
        else "Curta se esta resposta mudou sua forma de enxergar a história."
        if index % 4 == 2
        else "Comente qual grande pergunta bíblica devemos investigar depois."
    )
    return {
        "topic_id": topic_id,
        "hook": hook,
        "narration": f"{hook}{body}{cta}",
        "cta": cta,
        "title": "Por que eu permiti aquela decisão? | João 6:70-71",
        "description": (
            "Jesus e Judas diante de conhecimento, escolha e responsabilidade.\n\n"
            "#Judas #LivreArbitrio #Traicao #biblia #jesus"
        ),
        "specific_hashtags": ["#Judas", "#LivreArbitrio", "#Traicao"],
        "internal_tags": [
            "Jesus e Judas",
            "João 6 70 71",
            "Judas Iscariotes",
            "traição de Judas",
            "livre arbítrio",
            "responsabilidade de Judas",
            "Jesus sabia",
            "perguntas bíblicas",
            "curiosidades bíblicas",
            "explicação bíblica",
        ],
        "source_passages": ["João 6:70-71", "Lucas 22:21-22"],
        "biblical_review_status": "passed" if passed else "needs_changes",
        "biblical_review_summary": "Fontes conferidas; nenhum bloqueio aberto.",
        "hook_strength_score": 9,
        "clarity_score": 9,
        "payoff_score": 9,
        "quality_plan": {
            "hook_promise_excerpt": "permiti que aquela decisão continuasse",
            "progression_beats": [
                {
                    "excerpt": orientation,
                    "function": "orientation",
                    "new_information": "Jesus conhecia a situação antes da escolha de Judas.",
                },
                {
                    "excerpt": evidence,
                    "function": "evidence",
                    "new_information": (
                        "A citação identifica Judas e preserva sua posição entre os doze."
                    ),
                },
                {
                    "excerpt": reversal,
                    "function": "reversal",
                    "new_information": "Conhecimento prévio não equivale a causar uma decisão.",
                },
            ],
            "payoff_excerpt": payoff,
            "viewer_takeaway_excerpt": takeaway,
            "cta_mode": (
                "share"
                if index % 4 == 0
                else "subscribe"
                if index % 4 == 1
                else "like"
                if index % 4 == 2
                else "comment"
            ),
        },
        "index": index,
    }


def test_editorial_board_starts_with_one_concise_question(tmp_path) -> None:
    store = EditorialBoardStore(tmp_path / "jobs")

    status = store.workflow_status()

    assert status["next_step"] == "theme_suggestion"
    assert status["required_question"] == ("Você quer sugerir temas ou receber sugestões de temas?")
    assert status["production_counts"] == {"approved": 0, "reserved": 0, "produced": 0}
    assert status["production_items"] == []
    assert status["production_remote_synced_at"] is None
    assert "somente a etapa confirmada" in status["operational_rule"].casefold()


@pytest.mark.parametrize(
    "hook",
    [
        "Os mortos que Jesus levantou não eram o fim da morte.",
        "Por que Jesus curou o cego de Betsaida em duas etapas?",
        "Jesus realizou milagres públicos e depois pediu silêncio.",
    ],
)
def test_editorial_hook_rejects_the_three_third_person_patterns(hook: str) -> None:
    with pytest.raises(ValueError, match="eu lírico|terceira pessoa"):
        validate_editorial_hook(hook)


def test_editorial_hook_accepts_jesus_first_person_with_immediate_tension() -> None:
    validate_editorial_hook(
        "Eu podia curá-lo de uma vez, mas escolhi tocar seus olhos novamente. Por quê?"
    )


@pytest.mark.parametrize(
    ("cta", "expected"),
    [
        ("Comente qual pergunta devemos investigar.", "comment"),
        ("Inscreva-se para acompanhar outras histórias.", "subscribe"),
        ("Compartilhe com alguém que precisa desta verdade.", "share"),
        ("Curta se esta resposta mudou sua compreensão.", "like"),
    ],
)
def test_editorial_cta_mode_preserves_four_distinct_actions(cta: str, expected: str) -> None:
    assert infer_editorial_cta_mode(cta) == expected


def test_themes_are_approved_before_any_script_batch(tmp_path) -> None:
    store = EditorialBoardStore(tmp_path / "jobs")
    first = store.suggest_topic(
        title="Por que Jesus escolheu Judas?",
        central_question="Se eu sabia da traição, por que mantive Judas entre os doze?",
        hook_direction="Contradição conhecida em primeira pessoa.",
        source_passages=["João 6:70-71", "Lucas 22:21-22"],
    )
    second = store.suggest_topic(
        title="Por que Jesus não chamou os anjos?",
        central_question="Se eu podia chamar anjos, por que aceitei a prisão?",
        hook_direction="Poder disponível contra entrega voluntária.",
        source_passages=["Mateus 26:52-54"],
    )

    with pytest.raises(RuntimeError, match="aprovados"):
        store.prepare_batch(topic_ids=[first.topic_id, second.topic_id])

    approved = store.approve_topics(
        [first.topic_id, second.topic_id],
        approved_by="Pedro",
    )
    batch = store.prepare_batch(topic_ids=[item.topic_id for item in approved])
    queue = read_json(Path(batch.queue_path))

    assert len(approved) == 2
    assert queue["interaction_contract"]["project_creation"] is False
    assert queue["interaction_contract"]["media_production"] is False
    assert queue["interaction_contract"]["maximum_script_specialists"] == 1
    assert queue["interaction_contract"]["maximum_biblical_reviewers"] == 0
    assert queue["interaction_contract"]["post_user_approval_model_calls"] is False
    assert queue["quality_contract"]["narrator_identity"] == "jesus_first_person_v1"
    assert queue["quality_contract"]["quality_reference"] == "approved_magnetic_curiosity_six_v2"
    assert queue["quality_contract"]["writing_standard"] == "magnetic_curiosity_payoff_v2"
    assert len(queue["quality_contract"]["benchmark_hooks"]) == 6
    assert queue["quality_contract"]["quality_plan_required"]["progression_beats"]
    assert queue["quality_contract"]["biblical_validation"] == "inline_source_guard_v1"
    assert queue["quality_contract"]["approval_transition"] == "hash_only_no_model_v1"


def test_batch_import_and_single_hash_mass_approval(tmp_path) -> None:
    store = EditorialBoardStore(tmp_path / "jobs")
    topics = [
        store.suggest_topic(
            title=f"Tema forte {index}",
            central_question=f"Por que isso aconteceu {index}?",
            hook_direction="Contradição factual em primeira pessoa.",
            source_passages=["João 6:70-71"],
        )
        for index in range(2)
    ]
    store.approve_topics([item.topic_id for item in topics], approved_by="Pedro")
    batch = store.prepare_batch(topic_ids=[item.topic_id for item in topics])
    package_path = tmp_path / "package.json"
    write_json(
        package_path,
        {
            "batch_id": batch.batch_id,
            "writing_standard": batch.writing_standard,
            "scripts": [
                _raw_script(topic.topic_id, index=index) for index, topic in enumerate(topics)
            ],
        },
    )

    imported = store.import_batch(package_path)
    review = store.review_batch(batch.batch_id)
    approved = store.approve_batch(
        batch.batch_id,
        confirm_hash=review["aggregate_hash"],
        approved_by="Pedro",
    )

    assert imported.status == "imported"
    assert len(review["scripts"]) == 2
    assert approved.status == "approved"
    assert len(approved.production_script_ids) == 2
    assert len(store.list_scripts(status="approved")) == 2
    assert len(store.list_topics(status="drafted")) == 2
    production_store = ApprovedScriptStore(tmp_path / "jobs")
    production_scripts = production_store.list(status="approved")
    assert {item.approved_script_id for item in production_scripts} == set(
        approved.production_script_ids
    )
    selected = production_store.find_by_label(
        topics[0].title,
        status="approved",
    )
    assert selected.content_hash
    assert (
        selected.script.narration_text()
        == _raw_script(
            topics[0].topic_id,
            index=0,
        )["narration"]
    )
    assert selected.voice_variant == "jesus_1"
    assert selected.music_track == "leaning"
    assert selected.metadata is not None
    layout = materialize_approved_project(
        selected,
        job_id="video-20260723-140000-12345678",
        projects_root=tmp_path / "projects",
        publication_mode="private_review",
        voice_id="voice-test",
        voice_model_id="model-test",
    )
    manifest = layout.load_manifest()
    assert manifest.duration_policy == "approved_script_locked_v1"
    assert layout.script.is_file()
    assert layout.shorts_script_strategy.is_file()
    release = CliRunner().invoke(
        cli.app,
        [
            "approved-scripts",
            "release",
            "--title",
            topics[0].title,
            "--authorize-external",
            "--jobs-dir",
            str(tmp_path / "jobs"),
        ],
    )
    assert release.exit_code == 0, release.output
    released = json.loads(release.output)
    assert released[0]["job"]["production_profile"] == "approved_fast_v1"
    assert released[0]["script"]["label"] == topics[0].title
    assert production_store.find_by_label(
        topics[0].title,
        status="reserved",
    ).reserved_by_job_id
    workflow = store.workflow_status()
    assert workflow["script_counts"]["approved"] == 2
    assert workflow["production_counts"] == {"approved": 1, "reserved": 1, "produced": 0}
    assert workflow["next_step"] == "production_release"
    repeated = store.approve_batch(
        batch.batch_id,
        confirm_hash=review["aggregate_hash"],
        approved_by="Pedro",
    )
    assert repeated.production_script_ids == approved.production_script_ids


def test_magnetic_batch_rejects_missing_quality_plan_without_partial_import(tmp_path) -> None:
    store = EditorialBoardStore(tmp_path / "jobs")
    topic = store.suggest_topic(
        title="Tema sem plano magnético",
        central_question="Por que isso aconteceu?",
        source_passages=["João 6:70-71"],
    )
    store.approve_topics([topic.topic_id], approved_by="Pedro")
    batch = store.prepare_batch(topic_ids=[topic.topic_id])
    raw = _raw_script(topic.topic_id, index=0)
    raw.pop("quality_plan")
    package_path = tmp_path / "missing-quality-plan.json"
    write_json(
        package_path,
        {
            "batch_id": batch.batch_id,
            "writing_standard": batch.writing_standard,
            "scripts": [raw],
        },
    )

    with pytest.raises(ValueError, match="quality_plan"):
        store.import_batch(package_path)

    assert store.list_scripts(status="draft") == []
    assert store.find_batch(batch.batch_id).status == "prepared"


def test_magnetic_batch_requires_cta_variety_without_partial_import(tmp_path) -> None:
    store = EditorialBoardStore(tmp_path / "jobs")
    topics = [
        store.suggest_topic(
            title=f"Tema com CTA {index}",
            central_question=f"Por que isso aconteceu {index}?",
            source_passages=["João 6:70-71"],
        )
        for index in range(2)
    ]
    store.approve_topics([item.topic_id for item in topics], approved_by="Pedro")
    batch = store.prepare_batch(topic_ids=[item.topic_id for item in topics])
    package_path = tmp_path / "repeated-cta.json"
    write_json(
        package_path,
        {
            "batch_id": batch.batch_id,
            "writing_standard": batch.writing_standard,
            "scripts": [
                _raw_script(topics[0].topic_id, index=0),
                _raw_script(topics[1].topic_id, index=4),
            ],
        },
    )

    with pytest.raises(ValueError, match="CTA distintos"):
        store.import_batch(package_path)

    assert store.list_scripts(status="draft") == []
    assert store.find_batch(batch.batch_id).status == "prepared"


@pytest.mark.parametrize("repetitions", [1, 24])
def test_approved_editorial_draft_has_no_word_count_duration_gate(
    tmp_path,
    repetitions: int,
) -> None:
    store = EditorialBoardStore(tmp_path / f"jobs-{repetitions}")
    topic = store.suggest_topic(
        title=f"Tema livre de duração {repetitions}",
        central_question="Por que eu permiti que isso acontecesse?",
        source_passages=["João 6:70-71"],
        target_duration_seconds=50,
    )
    store.approve_topics([topic.topic_id], approved_by="Pedro")
    batch = store.prepare_batch(topic_ids=[topic.topic_id])
    raw = _raw_script(topic.topic_id, index=0)
    body = " ".join(
        "Eu apresentei uma nova evidência e mantive a resposta ligada à pergunta."
        for _ in range(repetitions)
    )
    raw["narration"] = f"{raw['hook']} {body} {raw['cta']}"
    raw["quality_plan"] = {
        "hook_promise_excerpt": "permiti que aquela decisão continuasse",
        "progression_beats": [
            {
                "excerpt": "Eu apresentei uma primeira evidência concreta.",
                "function": "orientation",
                "new_information": "A primeira evidência inicia o desenvolvimento.",
            },
            {
                "excerpt": "Eu acrescentei uma segunda evidência bíblica.",
                "function": "evidence",
                "new_information": "A segunda evidência sustenta a explicação.",
            },
            {
                "excerpt": "Eu revelei uma consequência que mudava a pergunta.",
                "function": "reversal",
                "new_information": "A consequência transforma a compreensão inicial.",
            },
        ],
        "payoff_excerpt": "Eu entreguei a resposta prometida pela abertura.",
        "viewer_takeaway_excerpt": "Você recebeu um significado claro para levar consigo.",
        "cta_mode": "share",
    }
    raw["narration"] = (
        f"{raw['hook']} Eu apresentei uma primeira evidência concreta. "
        "Eu acrescentei uma segunda evidência bíblica. "
        "Eu revelei uma consequência que mudava a pergunta. "
        f"{body} Eu entreguei a resposta prometida pela abertura. "
        f"Você recebeu um significado claro para levar consigo. {raw['cta']}"
    )
    package_path = tmp_path / f"duration-free-{repetitions}.json"
    write_json(
        package_path,
        {
            "batch_id": batch.batch_id,
            "writing_standard": batch.writing_standard,
            "scripts": [raw],
        },
    )

    imported = store.import_batch(package_path)

    assert imported.status == "imported"
    assert len(store.list_scripts(status="draft")) == 1


def test_batch_rejects_third_person_hook_and_unreviewed_script(tmp_path) -> None:
    store = EditorialBoardStore(tmp_path / "jobs")
    topic = store.suggest_topic(
        title="Tema de teste",
        central_question="Por que isso aconteceu?",
        source_passages=["João 6:70-71"],
    )
    store.approve_topics([topic.topic_id], approved_by="Pedro")
    batch = store.prepare_batch(topic_ids=[topic.topic_id])
    raw = _raw_script(topic.topic_id, index=0, passed=False)
    raw["hook"] = "Jesus sabia o que aconteceria, mas permitiu que a decisão continuasse."
    raw["narration"] = raw["hook"] + raw["narration"].split("Então por quê?", 1)[1]
    package_path = tmp_path / "invalid-package.json"
    write_json(
        package_path,
        {
            "batch_id": batch.batch_id,
            "writing_standard": batch.writing_standard,
            "scripts": [raw],
        },
    )

    with pytest.raises(ValueError, match="eu lírico"):
        store.import_batch(package_path)


def test_batch_preserves_user_wording_without_biblical_reviewer(tmp_path) -> None:
    store = EditorialBoardStore(tmp_path / "jobs")
    topic = store.suggest_topic(
        title="Por que usei barro?",
        central_question="Por que escolhi barro para aquela cura?",
        source_passages=["João 9:1-7"],
        required_answer_points=["Explicar que o barro não possuía poder próprio."],
        uncertainty_boundaries=[
            "Não apresentar a nova criação como explicação declarada em João 9."
        ],
    )
    store.approve_topics([topic.topic_id], approved_by="Pedro")
    batch = store.prepare_batch(topic_ids=[topic.topic_id])
    raw = _raw_script(topic.topic_id, index=0)
    raw["source_passages"] = ["João 9:1-7"]
    package_path = tmp_path / "unbounded-package.json"
    write_json(
        package_path,
        {
            "batch_id": batch.batch_id,
            "writing_standard": batch.writing_standard,
            "scripts": [raw],
        },
    )

    imported = store.import_batch(package_path)

    assert imported.status == "imported"


def test_batch_does_not_partially_approve_without_literal_citation(tmp_path) -> None:
    store = EditorialBoardStore(tmp_path / "jobs")
    topic = store.suggest_topic(
        title="Tema sem citação",
        central_question="Por que isso aconteceu?",
        source_passages=["João 6:70-71"],
    )
    store.approve_topics([topic.topic_id], approved_by="Pedro")
    batch = store.prepare_batch(topic_ids=[topic.topic_id])
    raw = _raw_script(topic.topic_id, index=0)
    raw["narration"] = raw["narration"].replace(
        (
            "Em João, capítulo 6, versículos 70 a 71, lemos: “Jesus lhes respondeu: "
            "‘Não escolhi eu a vocês, os doze, e um de vocês é um diabo?’ Ora, ele falava de "
            "Judas, filho de Simão Iscariotes, pois era ele quem o trairia, sendo um dos doze.” "
        ),
        "João 6:70-71 identifica Judas entre os doze. ",
    )
    raw["quality_plan"]["progression_beats"][1] = {
        "excerpt": "João 6:70-71 identifica Judas entre os doze.",
        "function": "evidence",
        "new_information": "A referência identifica Judas sem incluir uma citação literal.",
    }
    package_path = tmp_path / "missing-citation-package.json"
    write_json(
        package_path,
        {
            "batch_id": batch.batch_id,
            "writing_standard": batch.writing_standard,
            "scripts": [raw],
        },
    )
    imported = store.import_batch(package_path)
    review = store.review_batch(batch.batch_id)

    with pytest.raises(ValueError, match="citação bíblica literal"):
        store.approve_batch(
            batch.batch_id,
            confirm_hash=review["aggregate_hash"],
            approved_by="Pedro",
        )

    assert imported.status == "imported"
    assert store.find_batch(batch.batch_id).status == "imported"
    assert len(store.list_scripts(status="draft")) == 1
    assert len(store.list_topics(status="approved")) == 1
    assert ApprovedScriptStore(tmp_path / "jobs").list() == []
