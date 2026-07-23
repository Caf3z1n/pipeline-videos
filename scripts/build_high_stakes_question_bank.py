# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from build_curated_verse_bank import (
    BOOKS,
    RIGHTS,
    SOURCE_REVISION,
    SOURCE_URL,
    TRANSLATION,
    extract_text,
    parse_reference_part,
    parse_vpl,
    source_url,
)

ACCESS_DATE = "2026-07-21"
VPL_ARTIFACT_URL = "https://ebible.org/Scriptures/porbrbsl_vpl.zip"


QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "jesus-between-death-and-resurrection",
        "slug": "o-que-aconteceu-com-jesus-antes-da-ressurreicao",
        "title": "O que aconteceu antes da ressurreicao",
        "theme": "O intervalo entre a morte e a ressurreicao de Jesus",
        "question": "O que aconteceu com Jesus entre a morte e a ressurreicao?",
        "implicit_question": "Se os Evangelhos narram a morte e depois o tumulo vazio, o que a Biblia permite afirmar sobre o intervalo?",
        "reveal": "A Biblia oferece pistas reais, mas nao uma cronologia completa; o valor esta em separar texto explicito de interpretacao.",
        "answer_status": "open",
        "mode": "question_driven_essay",
        "primary": ("ACT", "2:27", "2:22-32", "other"),
        "support": (
            ("LUK", "23:43", "23:39-46", "supporting_fact", "jesus"),
            ("1PE", "3:18-20", "3:18-22", "uncertainty_boundary", "other"),
        ),
        "hooks": {
            "surprising_fact": "O dia mais misterioso da historia de Jesus quase nao aparece nos Evangelhos.",
            "contradiction": "Jesus morreu na cruz, mas a Biblia nao encerra a historia no tumulo.",
            "unanswered_question": "O que aconteceu com Jesus entre a morte e a ressurreicao?",
        },
        "answer_points": (
            "Confirmar a morte real de Jesus e a ressurreicao ao terceiro dia.",
            "Comparar as pistas de Lucas 23, Atos 2 e 1 Pedro 3 sem fundi-las em uma cronologia inventada.",
            "Explicar por que o destino exato de cada movimento nesse intervalo permanece debatido.",
        ),
        "boundaries": (
            "Nao afirmar que os Evangelhos descrevem uma cronologia detalhada do intervalo.",
            "Nao apresentar uma interpretacao de 1 Pedro 3 como consenso biblico unanime.",
        ),
        "visual": (
            "A cruz vazia ao cair da tarde.",
            "O tumulo selado e guardado.",
            "Pistas textuais surgindo em ambientes distintos, sem representar especulacao como fato.",
            "O tumulo aberto ao amanhecer como resolucao documentada.",
        ),
    },
    {
        "id": "why-jesus-waited-for-lazarus",
        "slug": "por-que-jesus-esperou-antes-de-ajudar-lazaro",
        "title": "Por que Jesus esperou por Lazaro",
        "theme": "A demora deliberada de Jesus antes de ir a Betania",
        "question": "Por que Jesus esperou antes de ajudar Lazaro?",
        "implicit_question": "Como Jesus podia amar Lazaro e ainda assim permanecer dois dias onde estava?",
        "reveal": "O proprio relato une amor, demora e uma revelacao maior, sem tratar a dor das irmas como irrelevante.",
        "answer_status": "direct",
        "mode": "cause_and_consequence",
        "primary": ("JOH", "11:5-6", "11:1-16", "other"),
        "support": (
            ("JOH", "11:14-15", "11:11-16", "supporting_fact", "jesus"),
            ("JOH", "11:40-44", "11:38-44", "supporting_fact", "jesus"),
        ),
        "hooks": {
            "contradiction": "Jesus amava Lazaro, mas decidiu esperar antes de ir ajuda-lo.",
            "surprising_fact": "A demora de Jesus diante de Lazaro nao foi um acidente de viagem.",
            "unanswered_question": "Por que Jesus esperou se sabia que Lazaro estava morrendo?",
        },
        "answer_points": (
            "Mostrar que Joao declara o amor de Jesus imediatamente antes da demora.",
            "Explicar a finalidade revelada por Jesus sem prometer que toda demora humana termina em milagre.",
            "Entregar a ressurreicao de Lazaro como desfecho do episodio, nao como formula universal.",
        ),
        "boundaries": (
            "Nao dizer que Jesus sentia prazer no sofrimento da familia.",
            "Nao transformar o episodio em promessa de ressurreicao imediata para todo fiel.",
        ),
        "visual": (
            "O mensageiro chega a Jesus com urgencia.",
            "Jesus permanece onde esta enquanto os discipulos aguardam.",
            "Marta e Maria diante do luto em Betania.",
            "A pedra e retirada e Lazaro sai do tumulo.",
        ),
    },
    {
        "id": "why-jesus-did-not-raise-everyone",
        "slug": "por-que-jesus-nao-ressuscitou-todos-os-mortos",
        "title": "Por que Jesus nao ressuscitou todos",
        "theme": "Os sinais de ressurreicao e o limite de sua finalidade nos Evangelhos",
        "question": "Se Jesus podia ressuscitar mortos, por que nao ressuscitou todos?",
        "implicit_question": "Por que os milagres de ressurreicao foram sinais seletivos em vez do fim imediato de toda morte?",
        "reveal": "As ressurreicoes funcionam como sinais da identidade e da promessa de Jesus; a ressurreicao universal aparece como evento futuro.",
        "answer_status": "bounded_inference",
        "mode": "thematic_investigation",
        "primary": ("JOH", "11:25", "11:17-27", "jesus"),
        "support": (
            ("LUK", "7:14-15", "7:11-17", "supporting_fact", "jesus"),
            ("MAR", "5:41-42", "5:35-43", "supporting_fact", "jesus"),
            ("JOH", "5:28-29", "5:19-29", "supporting_fact", "jesus"),
        ),
        "hooks": {
            "contradiction": "Jesus ressuscitou mortos, mas nao esvaziou todos os tumulos de Israel.",
            "belief_reversal": "Os mortos que Jesus levantou nao eram o fim da morte.",
            "unanswered_question": "Se Jesus podia ressuscitar mortos, por que nao ressuscitou todos?",
        },
        "answer_points": (
            "Distinguir os sinais individuais narrados nos Evangelhos da ressurreicao final prometida.",
            "Mostrar que os sinais apontam para a identidade de Jesus e nao para favoritismo arbitrario.",
            "Preservar que o texto nao fornece uma lista de motivos para cada pessoa que nao foi ressuscitada.",
        ),
        "boundaries": (
            "Nao inventar quantas pessoas Jesus poderia ou deveria ter ressuscitado.",
            "Nao sugerir falta de compaixao como explicacao biblica.",
        ),
        "visual": (
            "Uma cidade cercada por sepulturas enquanto Jesus entra em cena.",
            "O filho da viuva volta a respirar.",
            "A filha de Jairo se levanta.",
            "Lazaro sai do tumulo e a imagem se abre para a promessa futura.",
        ),
    },
    {
        "id": "why-jesus-chose-judas",
        "slug": "se-jesus-sabia-por-que-escolheu-judas",
        "title": "Se Jesus sabia, por que escolheu Judas",
        "theme": "A escolha de Judas apesar do conhecimento de Jesus",
        "question": "Se Jesus sabia que Judas o trairia, por que o escolheu?",
        "implicit_question": "Judas foi escolhido para ser forcado a trair ou sua responsabilidade permaneceu real?",
        "reveal": "O texto mostra conhecimento previo e responsabilidade de Judas, mas nao transforma o conhecimento de Jesus em coacao.",
        "answer_status": "open",
        "mode": "ethical_dilemma",
        "primary": ("JOH", "6:70-71", "6:66-71", "jesus"),
        "support": (
            ("JOH", "13:21-27", "13:18-30", "tension", "jesus"),
            ("LUK", "22:21-22", "22:14-23", "uncertainty_boundary", "jesus"),
        ),
        "hooks": {
            "contradiction": "Eu sabia que Judas me trairia e, mesmo assim, permiti que ele permanecesse entre os doze. Ele ouviu meus ensinamentos, participou das viagens e sentou-se à mesa comigo. Então por que eu não o afastei antes?",
        },
        "answer_points": (
            "Confirmar que Jesus demonstra conhecimento da traicao antes de ela acontecer.",
            "Distinguir conhecimento previo de obrigacao moral ou coacao.",
            "Admitir que a Biblia nao entrega uma explicacao filosofica completa da escolha.",
        ),
        "boundaries": (
            "Nao dizer que Judas nao tinha escolha como se isso fosse afirmacao explicita do texto.",
            "Nao inventar motivacoes secretas de Jesus alem das passagens usadas.",
        ),
        "visual": (
            "Jesus escolhe os doze entre muitos seguidores.",
            "Judas permanece no grupo enquanto a tensao cresce.",
            "A mesa da ultima ceia coloca traidor e mestre no mesmo quadro.",
            "Judas sai para a noite sem representar determinismo como fato.",
        ),
    },
    {
        "id": "why-jesus-did-not-call-angels",
        "slug": "por-que-jesus-nao-chamou-os-anjos-na-cruz",
        "title": "Por que Jesus nao chamou os anjos",
        "theme": "A entrega voluntaria de Jesus quando podia evitar a prisao",
        "question": "Se Jesus podia chamar anjos, por que aceitou a cruz?",
        "implicit_question": "A prisao de Jesus aconteceu por falta de poder ou por entrega voluntaria?",
        "reveal": "Jesus afirma que tinha como evitar a prisao e liga sua decisao ao cumprimento de sua missao.",
        "answer_status": "direct",
        "mode": "cause_and_consequence",
        "primary": ("MAT", "26:53-54", "26:47-56", "jesus"),
        "support": (
            ("JOH", "10:17-18", "10:14-18", "supporting_fact", "jesus"),
            ("MAT", "20:28", "20:20-28", "supporting_fact", "jesus"),
        ),
        "hooks": {
            "consequence_first": "A cruz so aconteceu porque Jesus recusou a saida que tinha.",
            "contradiction": "Jesus podia chamar um exercito de anjos, mas mandou guardar a espada.",
            "unanswered_question": "Por que Jesus nao chamou os anjos para impedir a crucificacao?",
        },
        "answer_points": (
            "Mostrar que Jesus declara poder pedir ajuda angelical.",
            "Ligar sua recusa ao cumprimento das Escrituras e a entrega voluntaria.",
            "Evitar retratar a cruz como acidente ou impotencia.",
        ),
        "boundaries": (
            "Nao inventar uma batalha angelical que o texto nao narra.",
            "Nao descrever detalhes fisicos da crucificacao alem do necessario ao argumento.",
        ),
        "visual": (
            "A prisao com espadas e tochas no jardim.",
            "Pedro ergue a espada e Jesus interrompe a violencia.",
            "A possibilidade dos anjos e sugerida sem mostrar um evento inexistente.",
            "Jesus segue voluntariamente para cumprir a missao.",
        ),
    },
    {
        "id": "why-jesus-left-after-resurrection",
        "slug": "por-que-jesus-nao-ficou-na-terra-apos-ressuscitar",
        "title": "Por que Jesus nao ficou apos ressuscitar",
        "theme": "Os quarenta dias e a ascensao de Jesus",
        "question": "Por que Jesus nao permaneceu visivelmente na terra depois de ressuscitar?",
        "implicit_question": "Se Jesus voltou da morte, por que apareceu por um periodo limitado e depois ascendeu?",
        "reveal": "Atos apresenta quarenta dias de provas e ensino, seguidos por missao, promessa do Espirito e ascensao.",
        "answer_status": "direct",
        "mode": "chronological_reconstruction",
        "primary": ("ACT", "1:3", "1:1-5", "other"),
        "support": (
            ("ACT", "1:8-11", "1:6-11", "supporting_fact", "jesus"),
            ("JOH", "16:7", "16:5-15", "supporting_fact", "jesus"),
        ),
        "hooks": {
            "belief_reversal": "Jesus nao voltou da morte para retomar a vida de antes.",
            "surprising_fact": "A ressurreicao de Jesus foi seguida por quarenta dias de encontros verificaveis.",
            "unanswered_question": "Se Jesus ressuscitou, por que nao continuou visivelmente entre todos?",
        },
        "answer_points": (
            "Corrigir a ideia de apenas tres dias vivo apos a ressurreicao: Atos fala em quarenta dias.",
            "Organizar ensino, comissao, promessa do Espirito e ascensao em sequencia.",
            "Explicar que o texto desloca a missao para as testemunhas, sem alegar ausencia de Jesus.",
        ),
        "boundaries": (
            "Nao dizer que Jesus morreu novamente depois dos quarenta dias.",
            "Nao inventar uma agenda completa de aparicoes que o texto nao fornece.",
        ),
        "visual": (
            "O tumulo vazio abre a sequencia.",
            "Jesus encontra os discipulos em diferentes momentos.",
            "O ensino e a comissao preparam a transicao.",
            "A ascensao encerra a cronologia e entrega a missao aos discipulos.",
        ),
    },
    {
        "id": "why-jesus-healed-blind-man-in-two-stages",
        "slug": "por-que-jesus-curou-um-cego-em-duas-etapas",
        "title": "Por que Jesus curou um cego em duas etapas",
        "theme": "A cura progressiva do cego de Betsaida",
        "question": "Por que Jesus curou um cego em duas etapas?",
        "implicit_question": "Se Jesus podia curar imediatamente, por que a visão do homem de Betsaida foi restaurada de forma progressiva?",
        "reveal": "Marcos descreve uma cura completa em duas etapas e a posiciona entre a dificuldade dos discípulos para compreender e a confissão de Pedro; o texto não declara por que Jesus escolheu esse processo.",
        "answer_status": "open",
        "mode": "literary_exploration",
        "primary": ("MAR", "8:24-25", "8:22-26", "other"),
        "support": (
            ("MAR", "8:17-18", "8:14-21", "story_context", "jesus"),
            ("MAR", "8:29-30", "8:27-30", "story_context", "other"),
        ),
        "hooks": {
            "surprising_fact": "Uma cura de Jesus só ficou completa depois de um segundo toque.",
            "contradiction": "Jesus tocou o cego, mas o homem ainda enxergava pessoas como árvores.",
            "unanswered_question": "Por que Jesus curou o cego de Betsaida em duas etapas?",
        },
        "answer_points": (
            "Narrar com precisão que o homem primeiro enxergou de forma parcial e depois passou a ver claramente.",
            "Mostrar que Marcos coloca a cura entre a incompreensão dos discípulos e o reconhecimento de Jesus por Pedro.",
            "Distinguir essa posição literária da razão histórica do segundo toque, que o texto não revela.",
        ),
        "boundaries": (
            "Não afirmar que o primeiro toque falhou ou que faltou poder a Jesus.",
            "Não atribuir a cura progressiva à falta de fé do homem, pois Marcos não apresenta essa explicação.",
            "Não transformar a leitura simbólica da passagem em motivo explicitamente declarado por Jesus.",
        ),
        "visual": (
            "O cego é conduzido pela mão para fora de Betsaida.",
            "Após o primeiro toque, silhuetas humanas parecem árvores em movimento.",
            "Jesus impõe novamente as mãos sobre os olhos do homem.",
            "A visão se torna nítida antes da pergunta de Jesus aos discípulos no caminho.",
        ),
    },
    {
        "id": "why-jesus-commanded-silence-after-miracles",
        "slug": "por-que-jesus-mandava-esconder-seus-milagres",
        "title": "Por que Jesus mandava esconder seus milagres",
        "theme": "As ordens de silêncio dadas por Jesus após milagres e reconhecimentos",
        "question": "Por que Jesus mandava algumas pessoas não contarem seus milagres?",
        "implicit_question": "Se a missão cristã envolve anunciar Jesus, por que ele proibiu a divulgação em determinados momentos?",
        "reveal": "As ordens aparecem em contextos diferentes: em um deles, a divulgação desobediente dificultou a entrada de Jesus nas cidades; em outros, o texto preserva a ordem sem fornecer uma única explicação para todos os casos.",
        "answer_status": "bounded_inference",
        "mode": "thematic_investigation",
        "primary": ("MAR", "1:44", "1:40-45", "jesus"),
        "support": (
            ("MAR", "1:45", "1:40-45", "supporting_fact", "other"),
            ("MAR", "5:43", "5:35-43", "supporting_fact", "other"),
            ("MAR", "8:29-30", "8:27-31", "tension", "other"),
        ),
        "hooks": {
            "surprising_fact": "Jesus realizou milagres públicos e depois proibiu algumas pessoas de contar o que aconteceu.",
            "contradiction": "Jesus veio para ser conhecido, mas em vários momentos ordenou silêncio.",
            "unanswered_question": "Por que Jesus mandava algumas pessoas esconderem seus milagres?",
        },
        "answer_points": (
            "Comparar as ordens de silêncio sem presumir que todas tiveram exatamente o mesmo motivo.",
            "Mostrar que, após a cura do leproso, a divulgação fez Jesus permanecer fora das cidades.",
            "Relacionar a ordem após a confissão de Pedro ao momento narrativo anterior ao ensino sobre sofrimento, morte e ressurreição.",
        ),
        "boundaries": (
            "Não afirmar que Jesus queria esconder permanentemente sua identidade ou impedir toda proclamação futura.",
            "Não usar uma teoria única como resposta explícita para todas as ordens de silêncio.",
            "Não inventar motivações políticas ou psicológicas que as passagens não declaram.",
        ),
        "visual": (
            "Jesus cura o leproso e lhe dá uma ordem direta antes de despedi-lo.",
            "O homem espalha a notícia e uma multidão bloqueia a entrada pública de Jesus na cidade.",
            "A família de Jairo recebe a ordem de silêncio após a menina se levantar.",
            "Pedro reconhece Jesus no caminho e os discípulos são orientados a guardar silêncio naquele momento.",
        ),
    },
)

EDITORIAL_ACCENTS = {
    "alem": "além",
    "alguem": "alguém",
    "analogias": "analogias",
    "apos": "após",
    "aparicoes": "aparições",
    "afirmacao": "afirmação",
    "afirmacoes": "afirmações",
    "arbitrario": "arbitrário",
    "Biblia": "Bíblia",
    "biblica": "bíblica",
    "biblicas": "bíblicas",
    "biblico": "bíblico",
    "biblicos": "bíblicos",
    "Betania": "Betânia",
    "coacao": "coação",
    "cinematografico": "cinematográfico",
    "comissao": "comissão",
    "comunhao": "comunhão",
    "conclusoes": "conclusões",
    "crucificacao": "crucificação",
    "decisao": "decisão",
    "determinismo": "determinismo",
    "divindade": "divindade",
    "discipulos": "discípulos",
    "duvidas": "dúvidas",
    "evidencia": "evidência",
    "Evangelhos": "Evangelhos",
    "exercito": "exército",
    "explicacao": "explicação",
    "explicita": "explícita",
    "explicito": "explícito",
    "filosofica": "filosófica",
    "forcado": "forçado",
    "historia": "história",
    "intelectualmente": "intelectualmente",
    "interpretacao": "interpretação",
    "irma": "irmã",
    "irmas": "irmãs",
    "Joao": "João",
    "Lazaro": "Lázaro",
    "missao": "missão",
    "motivacoes": "motivações",
    "nao": "não",
    "oracao": "oração",
    "oracoes": "orações",
    "periodo": "período",
    "prisao": "prisão",
    "proprio": "próprio",
    "ressurreicao": "ressurreição",
    "ressurreicoes": "ressurreições",
    "saida": "saída",
    "simbolicas": "simbólicas",
    "so": "só",
    "tres": "três",
    "Tome": "Tomé",
    "tumulo": "túmulo",
    "tumulos": "túmulos",
    "unico": "único",
    "visiveis": "visíveis",
    "voluntaria": "voluntária",
}


def _restore_editorial_accents(value: Any) -> Any:
    if isinstance(value, str):
        value = value.replace("Se Jesus e Deus", "Se Jesus é Deus")
        for source, replacement in EDITORIAL_ACCENTS.items():
            value = re.sub(rf"\b{re.escape(source)}\b", replacement, value)
        return value
    if isinstance(value, list):
        return [_restore_editorial_accents(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_restore_editorial_accents(item) for item in value)
    if isinstance(value, dict):
        return {key: _restore_editorial_accents(item) for key, item in value.items()}
    return value


def _reference(code: str, part: str) -> str:
    return f"{BOOKS[code][0]} {part}"


def _source(
    verses: dict[tuple[str, int, int], str],
    *,
    question_id: str,
    code: str,
    part: str,
    context: str,
    role: str,
    spoken_by: str,
) -> dict[str, Any]:
    chapter, _, _ = parse_reference_part(part)
    return {
        "passage_id": f"high-stakes-{question_id}-{code.lower()}-{part.replace(':', '-').replace('/', '-')}",
        "reference": _reference(code, part),
        "context_reference": _reference(code, context),
        "verse_text": extract_text(verses, code, part),
        "source_url": source_url(code, chapter),
        "role": role,
        "spoken_by": spoken_by,
    }


def build(vpl_path: Path) -> dict[str, Any]:
    verses, _ = parse_vpl(vpl_path)
    artifact_sha = hashlib.sha256(vpl_path.read_bytes()).hexdigest()
    candidates: list[dict[str, Any]] = []
    for item in QUESTIONS:
        code, part, context, spoken_by = item["primary"]
        chapter, _, _ = parse_reference_part(part)
        primary_text = extract_text(verses, code, part)
        if len(primary_text.split()) > 40:
            raise ValueError(f"Citação primaria longa demais em {item['id']}: {part}")
        related = [
            _source(
                verses,
                question_id=item["id"],
                code=support_code,
                part=support_part,
                context=support_context,
                role=role,
                spoken_by=support_speaker,
            )
            for support_code, support_part, support_context, role, support_speaker in item[
                "support"
            ]
        ]
        candidates.append(
            _restore_editorial_accents(
                {
                    "candidate_id": item["id"],
                    "source_id": f"high-stakes-primary-{item['id']}",
                    "reference": _reference(code, part),
                    "context_reference": _reference(code, context),
                    "book": BOOKS[code][0],
                    "testament": BOOKS[code][1],
                    "translation": TRANSLATION,
                    "verse_text": primary_text,
                    "source_url": source_url(code, chapter),
                    "accessed_at": ACCESS_DATE,
                    "rights_note": RIGHTS,
                    "title": item["title"],
                    "slug": item["slug"],
                    "central_theme": item["theme"],
                    "practical_application": "",
                    "audience": "pessoas com duvidas frequentes sobre Jesus e os relatos biblicos",
                    "emotional_intent": "provocar curiosidade honesta e entregar uma explicacao biblica clara",
                    "narration_tone": "investigativo, direto, cinematografico e intelectualmente honesto",
                    "themes": ["Jesus", "grande pergunta biblica", "curiosidade biblica"],
                    "characters": ["Jesus"],
                    "related_passages": related,
                    "preferred_development_modes": ["context", "cross_reference", "promise_limits"],
                    "interpretation_limits": list(item["boundaries"]),
                    "claims_to_avoid": list(item["boundaries"]),
                    "visual_notes": ["Representar apenas acontecimentos documentados como fatos."],
                    "retention_archetypes": ["counterintuitive_reveal", "stair_step_progression"],
                    "human_conflict": item["implicit_question"],
                    "central_question": item["question"],
                    "visual_progression": list(item["visual"]),
                    "possible_reveal": item["reveal"],
                    "title_seed": item["title"],
                    "retention_potential_score": 0.96,
                    "selection_weight": 2.0,
                    "familiarity_score": 0.95,
                    "clarity_score": 0.9,
                    "catalog_segment": "high_stakes_question",
                    "recommended_formats": ["short"],
                    "source_text_scope": "verse_range" if "-" in part else "verse",
                    "spoken_by": spoken_by,
                    "editorial_profile": "high_stakes_biblical_question_v1",
                    "opening_hooks": item["hooks"],
                    "implicit_viewer_question": item["implicit_question"],
                    "required_answer_points": list(item["answer_points"]),
                    "uncertainty_boundaries": list(item["boundaries"]),
                    "answer_status": item["answer_status"],
                    "preferred_creative_mode": item["mode"],
                }
            )
        )
    return {
        "schema_version": "1.0",
        "catalog_id": "high-stakes-biblical-question-bank-v1",
        "translation": TRANSLATION,
        "publisher": "eBible.org / Bíblia Portuguesa Mundial",
        "source_url": SOURCE_URL,
        "rights_note": RIGHTS,
        "accessed_at": ACCESS_DATE,
        "source_artifact_url": VPL_ARTIFACT_URL,
        "source_artifact_sha256": artifact_sha,
        "source_revision": SOURCE_REVISION,
        "candidates": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vpl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build(args.vpl)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
