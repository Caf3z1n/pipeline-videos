from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

TRANSLATION = "Bíblia Portuguesa Mundial (BPM)"
RIGHTS = "Domínio público, conforme declaração da edição BPM no eBible.org."

BOOKS = {
    "GEN": ("Gênesis", "old"),
    "DEU": ("Deuteronômio", "old"),
    "PSA": ("Salmos", "old"),
    "PRO": ("Provérbios", "old"),
    "ISA": ("Isaías", "old"),
    "JER": ("Jeremias", "old"),
    "LAM": ("Lamentações", "old"),
    "MIC": ("Miqueias", "old"),
    "HAB": ("Habacuque", "old"),
    "MAT": ("Mateus", "new"),
    "MAR": ("Marcos", "new"),
    "LUK": ("Lucas", "new"),
    "JOH": ("João", "new"),
    "ACT": ("Atos", "new"),
    "ROM": ("Romanos", "new"),
    "1CO": ("1 Coríntios", "new"),
    "2CO": ("2 Coríntios", "new"),
    "GAL": ("Gálatas", "new"),
    "EPH": ("Efésios", "new"),
    "PHI": ("Filipenses", "new"),
    "COL": ("Colossenses", "new"),
    "1TH": ("1 Tessalonicenses", "new"),
    "2TI": ("2 Timóteo", "new"),
    "HEB": ("Hebreus", "new"),
    "JAM": ("Tiago", "new"),
    "1PE": ("1 Pedro", "new"),
    "1JO": ("1 João", "new"),
    "REV": ("Apocalipse", "new"),
}

HTML_BOOK_CODES = {
    "MAR": "MRK",
    "JOH": "JHN",
    "PHI": "PHP",
    "JAM": "JAS",
    "1JO": "1JN",
}

# Referências e enquadramentos editoriais são curados. O texto literal nunca vem
# desta lista: ele é extraído do arquivo VPL oficial da BPM.
PROFILES = [
    (
        "GEN",
        "50:20",
        "50:15-21",
        "Quando o mal não recebe a palavra final",
        "providência e responsabilidade",
        "Reconhecer a dor sem chamar o mal de bem e escolher uma resposta fiel.",
    ),
    (
        "DEU",
        "6:5",
        "6:4-9",
        "Amar a Deus por inteiro",
        "amor a Deus na vida cotidiana",
        "Reordenar uma decisão concreta para amar a Deus com toda a vida.",
    ),
    (
        "PSA",
        "23:1",
        "23:1-4",
        "O cuidado de Deus no caminho",
        "cuidado de Deus",
        "Identificar onde a ansiedade tenta assumir o controle e confiar o próximo passo a Deus.",
    ),
    (
        "PSA",
        "27:1",
        "27:1-5",
        "Quando o medo perde o comando",
        "confiança em meio ao medo",
        "Nomear o medo sem entregar a ele a direção das escolhas.",
    ),
    (
        "PSA",
        "46:1",
        "46:1-3",
        "Refúgio quando tudo parece instável",
        "refúgio em Deus",
        "Buscar em Deus estabilidade antes de reagir ao caos.",
    ),
    (
        "PSA",
        "91:1",
        "91:1-4",
        "Permanecer perto de Deus",
        "comunhão e proteção",
        "Cultivar presença e oração sem transformar a passagem em promessa de "
        "ausência de sofrimento.",
    ),
    (
        "PSA",
        "121:1-2",
        "121:1-4",
        "De onde vem o socorro",
        "socorro de Deus",
        "Trocar a autossuficiência por uma busca concreta do auxílio de Deus.",
    ),
    (
        "PSA",
        "139:23-24",
        "139:23-24",
        "A coragem de pedir exame",
        "arrependimento e direção",
        "Abrir uma área da vida à correção de Deus e responder com honestidade.",
    ),
    (
        "PRO",
        "4:23",
        "4:20-27",
        "O que você permite criar raízes",
        "cuidado com o coração",
        "Observar o que tem alimentado pensamentos e decisões e estabelecer um limite saudável.",
    ),
    (
        "PRO",
        "16:3",
        "16:1-9",
        "Entregar os planos antes de avançar",
        "planos submetidos a Deus",
        "Apresentar a Deus um plano real e aceitar correção de rota.",
    ),
    (
        "PRO",
        "17:17",
        "17:17",
        "A amizade que permanece na adversidade",
        "amizade e lealdade",
        "Praticar presença fiel com alguém que atravessa um momento difícil.",
    ),
    (
        "ISA",
        "40:31",
        "40:27-31",
        "Força renovada para continuar",
        "esperança perseverante",
        "Esperar em Deus sem abandonar as responsabilidades do presente.",
    ),
    (
        "ISA",
        "43:2",
        "43:1-3",
        "Deus presente no meio da travessia",
        "presença de Deus no sofrimento",
        "Atravessar a dificuldade com apoio, prudência e consciência da presença de Deus.",
    ),
    (
        "JER",
        "29:11",
        "29:10-14",
        "Esperança dentro de uma espera longa",
        "esperança no exílio",
        "Preservar esperança sem transformar a promessa dada aos exilados em "
        "garantia imediata pessoal.",
    ),
    (
        "LAM",
        "3:22-23",
        "3:19-26",
        "Misericórdias novas em dias difíceis",
        "misericórdia e fidelidade",
        "Reconhecer uma evidência concreta da misericórdia de Deus mesmo durante a dor.",
    ),
    (
        "MIC",
        "7:8",
        "7:7-9",
        "Levantar depois da queda",
        "recomeço e esperança",
        "Assumir a queda, buscar restauração e dar um passo responsável de recomeço.",
    ),
    (
        "HAB",
        "3:17-18",
        "3:17-19",
        "Alegria quando as circunstâncias falham",
        "fé sem negação da perda",
        "Distinguir alegria em Deus de negação emocional ou passividade diante da perda.",
    ),
    (
        "MAT",
        "6:33",
        "6:25-34",
        "O que vem primeiro quando a ansiedade cresce",
        "prioridades e ansiedade",
        "Reordenar a agenda de hoje a partir do Reino de Deus e da justiça.",
    ),
    (
        "MAT",
        "7:12",
        "7:7-12",
        "A medida que você usa com os outros",
        "empatia e responsabilidade",
        "Tomar uma decisão tratando o outro com a dignidade que você gostaria de receber.",
    ),
    (
        "MAT",
        "11:29",
        "11:28-30",
        "Aprender o ritmo de Jesus",
        "mansidão e descanso",
        "Trocar uma carga autodestrutiva por obediência, limites e aprendizado com Jesus.",
    ),
    (
        "MAT",
        "22:37",
        "22:34-40",
        "O amor que organiza todos os outros",
        "amor integral a Deus",
        "Avaliar qual escolha demonstra amor a Deus com mente, coração e ações.",
    ),
    (
        "MAR",
        "10:27",
        "10:23-31",
        "Quando suas forças não bastam",
        "dependência de Deus",
        "Reconhecer limites humanos sem usar a fé como desculpa para irresponsabilidade.",
    ),
    (
        "LUK",
        "1:37",
        "1:26-38",
        "Nada é impossível para Deus",
        "fé e submissão",
        "Responder a Deus com fé sem transformar a passagem em autorização para "
        "qualquer desejo pessoal.",
    ),
    (
        "LUK",
        "6:31",
        "6:27-36",
        "Como tratar quem não facilita",
        "misericórdia e reciprocidade",
        "Praticar bondade com limites e sem encobrir abuso ou injustiça.",
    ),
    (
        "JOH",
        "3:16",
        "3:14-18",
        "O amor que Deus demonstrou",
        "amor e salvação",
        "Receber o amor de Deus e responder com fé que produz uma vida transformada.",
    ),
    (
        "JOH",
        "8:12",
        "8:12",
        "Luz para não caminhar no escuro",
        "direção em Jesus",
        "Trazer uma decisão escondida para a luz e seguir a direção de Jesus.",
    ),
    (
        "JOH",
        "10:10",
        "10:7-11",
        "A diferença entre roubo e vida",
        "vida abundante em Jesus",
        "Identificar o que destrói silenciosamente e escolher o caminho de Jesus.",
    ),
    (
        "JOH",
        "11:25",
        "11:21-27",
        "Esperança diante da morte",
        "ressurreição e esperança",
        "Sustentar esperança cristã sem minimizar o luto ou apressar a dor.",
    ),
    (
        "JOH",
        "15:5",
        "15:1-8",
        "Fruto não nasce da desconexão",
        "permanecer em Jesus",
        "Substituir ativismo vazio por comunhão e obediência consistentes.",
    ),
    (
        "JOH",
        "16:33",
        "16:29-33",
        "Paz sem negar as aflições",
        "paz e perseverança",
        "Enfrentar a aflição com realismo e confiança em Jesus.",
    ),
    (
        "ACT",
        "1:8",
        "1:6-9",
        "Poder para testemunhar",
        "Espírito Santo e missão",
        "Usar palavras e atitudes para testemunhar com fidelidade, não para buscar prestígio.",
    ),
    (
        "ROM",
        "5:8",
        "5:6-11",
        "Amados antes de merecer",
        "graça e reconciliação",
        "Abandonar a tentativa de merecer o amor de Deus e responder com gratidão.",
    ),
    (
        "ROM",
        "8:28",
        "8:26-30",
        "Deus trabalha sem chamar o mal de bem",
        "providência no sofrimento",
        "Confiar na ação de Deus sem romantizar perdas ou injustiças.",
    ),
    (
        "ROM",
        "12:2",
        "12:1-2",
        "A mudança começa na mente",
        "renovação e discernimento",
        "Questionar um padrão automático e substituí-lo por uma escolha coerente com Deus.",
    ),
    (
        "ROM",
        "15:13",
        "15:7-13",
        "Esperança que transborda",
        "esperança pelo Espírito",
        "Alimentar esperança por meio da confiança em Deus e de ações coerentes.",
    ),
    (
        "1CO",
        "10:13",
        "10:12-14",
        "A saída no meio da tentação",
        "tentação e responsabilidade",
        "Identificar antecipadamente uma saída concreta e pedir ajuda antes da queda.",
    ),
    (
        "1CO",
        "13:4-5",
        "13:1-7",
        "O amor aparece no comportamento",
        "amor paciente e responsável",
        "Transformar uma qualidade do amor em uma atitude observável hoje.",
    ),
    (
        "1CO",
        "16:14",
        "16:13-14",
        "Firmeza sem perder o amor",
        "coragem e amor",
        "Agir com firmeza mantendo respeito, verdade e amor.",
    ),
    (
        "2CO",
        "4:8-9",
        "4:7-12",
        "Pressionados, mas não abandonados",
        "resiliência e presença de Deus",
        "Buscar apoio e perseverar sem fingir que a pressão não existe.",
    ),
    (
        "2CO",
        "5:17",
        "5:14-21",
        "Uma nova vida começa em Cristo",
        "nova criação",
        "Romper com um padrão antigo por meio de arrependimento e prática renovada.",
    ),
    (
        "2CO",
        "12:9",
        "12:7-10",
        "Graça suficiente na fraqueza",
        "graça e limites humanos",
        "Receber ajuda e depender da graça sem glorificar sofrimento evitável.",
    ),
    (
        "GAL",
        "5:22-23",
        "5:16-25",
        "O fruto que revela a raiz",
        "fruto do Espírito",
        "Escolher uma prática que cultive um aspecto do fruto do Espírito.",
    ),
    (
        "EPH",
        "2:8-9",
        "2:1-10",
        "Salvos pela graça, não pelo desempenho",
        "graça e fé",
        "Parar de negociar mérito com Deus e viver boas obras como resposta, não pagamento.",
    ),
    (
        "EPH",
        "4:32",
        "4:25-32",
        "Bondade que não apaga a verdade",
        "perdão e compaixão",
        "Praticar compaixão e perdão com verdade, prudência e limites.",
    ),
    (
        "EPH",
        "6:10",
        "6:10-18",
        "Força para permanecer firme",
        "força no Senhor",
        "Preparar-se espiritualmente para uma pressão real sem demonizar pessoas.",
    ),
    (
        "PHI",
        "4:6-7",
        "4:4-9",
        "Ansiedade levada à oração",
        "oração e ansiedade",
        "Levar uma preocupação específica a Deus e buscar também o cuidado responsável necessário.",
    ),
    (
        "PHI",
        "4:13",
        "4:10-13",
        "Força para atravessar toda circunstância",
        "contentamento em Cristo",
        "Receber força para fidelidade em abundância ou necessidade, sem prometer "
        "sucesso ilimitado.",
    ),
    (
        "COL",
        "3:12",
        "3:12-17",
        "Vestir-se de misericórdia",
        "caráter cristão",
        "Escolher uma resposta marcada por compaixão, humildade e paciência.",
    ),
    (
        "COL",
        "3:23",
        "3:22-24",
        "Trabalhar com inteireza",
        "trabalho e propósito",
        "Realizar uma responsabilidade com integridade sem aceitar exploração "
        "como vontade de Deus.",
    ),
    (
        "1TH",
        "5:16-18",
        "5:16-22",
        "Alegria, oração e gratidão na vida real",
        "constância espiritual",
        "Praticar oração e gratidão sem negar tristeza ou sofrimento.",
    ),
    (
        "2TI",
        "3:16",
        "3:14-17",
        "A Escritura que forma a vida",
        "autoridade e formação bíblica",
        "Permitir que a Bíblia corrija uma convicção ou comportamento concreto.",
    ),
    (
        "HEB",
        "4:12",
        "4:12-13",
        "A palavra que expõe o interior",
        "discernimento pela Palavra",
        "Ler a Escritura com disposição para ser examinado e corrigido.",
    ),
    (
        "HEB",
        "10:23",
        "10:19-25",
        "Segurar a esperança sem soltar",
        "esperança e comunidade",
        "Permanecer firme e aproximar-se de pessoas que também estimulam a fé.",
    ),
    (
        "JAM",
        "1:5",
        "1:2-8",
        "Pedir sabedoria no meio da pressão",
        "sabedoria e perseverança",
        "Pedir sabedoria e agir conforme a luz já recebida.",
    ),
    (
        "JAM",
        "1:22",
        "1:19-25",
        "Ouvir sem praticar não transforma",
        "obediência prática",
        "Converter uma verdade ouvida em uma ação verificável hoje.",
    ),
    (
        "1PE",
        "2:9",
        "2:9-12",
        "Chamados das trevas para a luz",
        "identidade e testemunho",
        "Viver a identidade recebida em Cristo com caráter e serviço.",
    ),
    (
        "1JO",
        "1:9",
        "1:5-10",
        "Confissão que abre caminho",
        "confissão e perdão",
        "Confessar com honestidade, abandonar a justificativa e buscar reparação "
        "quando necessária.",
    ),
    (
        "1JO",
        "4:18",
        "4:16-21",
        "O amor enfrenta o medo",
        "amor e medo",
        "Permitir que o amor de Deus corrija uma reação governada pelo medo.",
    ),
    (
        "REV",
        "21:4",
        "21:1-5",
        "A promessa do fim da dor",
        "esperança na restauração",
        "Sustentar esperança futura sem apressar o luto presente.",
    ),
]


def parse_vpl(path: Path) -> dict[tuple[str, int, int], str]:
    verses: dict[tuple[str, int, int], str] = {}
    pattern = re.compile(r"^(\S+)\s+(\d+):(\d+)\s+(.+)$")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = pattern.match(line.strip())
        if match:
            verses[(match.group(1), int(match.group(2)), int(match.group(3)))] = match.group(4)
    return verses


def verse_range(reference: str) -> tuple[int, int, int]:
    chapter, raw_verses = reference.split(":", 1)
    bounds = raw_verses.split("-", 1)
    start = int(bounds[0])
    return int(chapter), start, int(bounds[-1])


def slugify(value: str) -> str:
    table = str.maketrans("áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ", "aaaaeeioooucAAAAEEIOOOUC")
    return re.sub(r"[^a-z0-9]+", "-", value.translate(table).casefold()).strip("-")


def build_candidate(profile: tuple[str, str, str, str, str, str], verses: dict) -> dict:
    code, reference_part, context_part, title, theme, application = profile
    book, testament = BOOKS[code]
    chapter, start, end = verse_range(reference_part)
    selected = [verses[(code, chapter, verse)] for verse in range(start, end + 1)]
    reference = f"{book} {reference_part}"
    context_reference = f"{book} {context_part}"
    reference_slug = slugify(reference)
    html_chapter = f"{chapter:03d}" if code == "PSA" else f"{chapter:02d}"
    html_code = HTML_BOOK_CODES.get(code, code)
    return {
        "candidate_id": f"bpm-{reference_slug}",
        "source_id": f"bpm-{reference_slug}",
        "reference": reference,
        "context_reference": context_reference,
        "book": book,
        "testament": testament,
        "translation": TRANSLATION,
        "verse_text": " ".join(selected),
        "source_url": f"https://ebible.org/porbrbsl/{html_code}{html_chapter}.htm",
        "accessed_at": "2026-07-17",
        "rights_note": RIGHTS,
        "title": title,
        "slug": f"{reference_slug}-{slugify(theme).split('-')[0]}",
        "central_theme": theme.capitalize() + ".",
        "practical_application": application,
        "themes": list(dict.fromkeys(slugify(theme).split("-"))),
        "preferred_development_modes": ["context", "cross_reference", "promise_limits"],
        "interpretation_limits": [
            "Preservar o contexto original e não transformar a passagem em garantia "
            "automática fora dele."
        ],
        "claims_to_avoid": [
            "Não prometer ausência de sofrimento, sucesso material ou solução instantânea."
        ],
        "title_seed": title,
        "retention_potential_score": 0.78,
        "selection_weight": 1.0,
        "familiarity_score": 0.8,
        "clarity_score": 0.85,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("vpl", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    verses = parse_vpl(args.vpl)
    candidates = [build_candidate(profile, verses) for profile in PROFILES]
    payload = {
        "schema_version": "1.0",
        "catalog_id": "shorts-bpm-public-domain-expansion-v1",
        "translation": TRANSLATION,
        "publisher": "eBible.org",
        "source_url": "https://ebible.org/details.php?id=porbrbsl",
        "rights_note": RIGHTS,
        "accessed_at": "2026-07-17",
        "candidates": candidates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"generated {len(candidates)} candidates at {args.output}")


if __name__ == "__main__":
    main()
