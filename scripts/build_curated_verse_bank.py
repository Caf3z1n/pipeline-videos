# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalog_curated_data import BOOKS, GENERAL_REFERENCE_SEEDS, LONG_FORM_PASSAGE_CANDIDATES

TRANSLATION = "Bíblia Portuguesa Mundial (BPM)"
RIGHTS = "Domínio público, conforme declaração da edição BPM no eBible.org."
SOURCE_URL = "https://ebible.org/details.php?id=porbrbsl"
VPL_ARTIFACT_URL = "https://ebible.org/Scriptures/porbrbsl_vpl.zip"
USFM_ARTIFACT_URL = "https://ebible.org/Scriptures/porbrbsl_usfm.zip"
ACCESS_DATE = "2026-07-20"
SOURCE_REVISION = "2026-04-25"
TARGET_COMPACT = 120
TARGET_GENERAL = 180
TARGET_LONG_FORM = 100
WORD_RE = re.compile(r"\b[\wÀ-ÿ]+(?:[-’'][\wÀ-ÿ]+)?\b", re.UNICODE)
VPL_LINE_RE = re.compile(r"^(\S+)\s+(\d+):(\d+)\s+(.+)$")
USFM_VERSE_RE = re.compile(r"\\v\s+(\d+)\s+(.*)")


@dataclass(frozen=True)
class DirectSpeechUnit:
    code: str
    chapter: int
    start_verse: int
    end_verse: int
    text: str

    @property
    def reference_part(self) -> str:
        verses = (
            str(self.start_verse)
            if self.start_verse == self.end_verse
            else f"{self.start_verse}-{self.end_verse}"
        )
        return f"{self.chapter}:{verses}"


THEMES: dict[str, dict[str, Any]] = {
    "peace": {
        "label": "Paz de Jesus em meio ao medo",
        "keywords": (
            "minha paz",
            "tenham paz",
            "paz seja nesta casa",
            "não tenham medo",
            "não se turbe",
            "bom ânimo",
            "angústia",
        ),
        "application": "Receber a paz de Jesus antes de permitir que o medo escolha a resposta.",
        "question": "O que muda quando a paz não depende de o cenário melhorar primeiro?",
        "reveal": "A paz prometida por Jesus não exige a negação da aflição.",
        "hooks": (
            "Olhe para mim. Leve paz antes de cobrar qualquer resposta.",
            "Respire e me escute. O medo não decidirá por você.",
            "Não recue agora. O medo não pode escolher seu próximo passo.",
        ),
    },
    "rest": {
        "label": "Descanso para uma alma sobrecarregada",
        "keywords": ("descanso", "cansad", "jugo", "fardo", "sobrecarreg"),
        "application": "Parar de carregar tudo da mesma forma e aprender o ritmo de Jesus.",
        "question": "Que peso continua sendo carregado sem necessidade?",
        "reveal": "O descanso de Jesus também ensina uma nova maneira de caminhar.",
        "hooks": (
            "Eu sei que você está cansado. Não carregue tudo sozinho.",
            "Pare um instante. Sua alma não precisa continuar correndo assim.",
            "Você não precisa provar força carregando tudo sozinho até desabar.",
        ),
    },
    "faith": {
        "label": "Fé quando a dúvida tenta encerrar a história",
        "keywords": ("fé", "creia", "creiam", "creem", "crer", "incréd", "confia"),
        "application": "Responder a Jesus com confiança obediente, sem transformar fé em negação da realidade.",
        "question": "Qual decisão a dúvida está impedindo hoje?",
        "reveal": "Jesus não trata a fé como espetáculo, mas como resposta à sua presença.",
        "hooks": (
            "Olhe para mim. A dúvida não precisa terminar sua história.",
            "Você ainda não viu o fim. Continue confiando e escute.",
            "Não entregue sua decisão à dúvida antes de me ouvir.",
        ),
    },
    "love": {
        "label": "O amor reconhecível nas escolhas",
        "keywords": (
            "amor",
            "amem",
            "amem os seus inimigos",
            "ame",
            "amado",
            "amigos",
            "amarás o Senhor",
        ),
        "application": "Converter o amor recebido de Jesus em uma atitude concreta, verdadeira e responsável.",
        "question": "Como o amor se torna visível quando custa alguma coisa?",
        "reveal": "O amor de Jesus é reconhecido por ações, não apenas por intenção.",
        "hooks": (
            "Escute com atenção. O amor que transforma começa nesta escolha.",
            "Antes de se fechar novamente, veja como o amor responde.",
            "Você não precisa endurecer para impedir que alguém o machuque.",
        ),
    },
    "mercy": {
        "label": "Misericórdia sem negar verdade ou responsabilidade",
        "keywords": ("miseric", "perdo", "compaix"),
        "application": "Praticar misericórdia com verdade, arrependimento e limites responsáveis.",
        "question": "Como responder à falha sem repetir a violência recebida?",
        "reveal": "A misericórdia de Jesus não chama o mal de bem.",
        "hooks": (
            "Antes de responder à ferida, olhe para mim e escute.",
            "Não deixe a dor decidir quem você vai se tornar.",
            "Antes de condenar alguém, lembre-se também da misericórdia que recebeu.",
        ),
    },
    "hidden_good": {
        "label": "Bondade praticada sem necessidade de aplauso",
        "keywords": ("obras de misericórdia", "em secreto", "não toque trombeta"),
        "application": "Praticar o bem por fidelidade, sem transformar cuidado em palco pessoal.",
        "question": "A bondade continuaria existindo se ninguém pudesse aplaudir?",
        "reveal": "Jesus confronta não apenas a ação, mas a necessidade de ser visto.",
        "hooks": (
            "Pare de buscar aplausos. O bem importa quando ninguém vê.",
            "Olhe para mim. Sua intenção pesa tanto quanto sua ação.",
            "Não transforme bondade em palco. Existe uma recompensa mais profunda.",
        ),
    },
    "prayer": {
        "label": "Oração diante de uma necessidade real",
        "keywords": ("orem", "orar", "oração", "peçam", "pedirem"),
        "application": "Levar uma necessidade específica ao Pai com confiança e disposição para obedecer.",
        "question": "O que ainda está sendo carregado sem ser colocado em oração?",
        "reveal": "A oração começa diante de um Pai que já conhece a necessidade.",
        "hooks": (
            "Você não está falando sozinho. Eu conheço o que precisa.",
            "Pare de repetir sua preocupação. Fale comigo agora sobre ela.",
            "Antes de procurar outra saída, coloque esta necessidade diante de mim.",
        ),
    },
    "following": {
        "label": "Permanecer e seguir Jesus de forma concreta",
        "keywords": (
            "siga-me",
            "me segue",
            "carrega a sua própria cruz",
            "não pode ser meu discípulo",
            "permaneçam em mim",
            "videira",
            "meus discípulos",
        ),
        "application": "Trocar admiração distante por uma decisão concreta de seguir e permanecer em Jesus.",
        "question": "O que precisa mudar para que seguir deixe de ser apenas intenção?",
        "reveal": "Fruto duradouro nasce de permanência, não de ativismo desconectado.",
        "hooks": (
            "Pare de lutar sozinho. Você precisa ouvir o que vou dizer.",
            "Olhe para mim. Seu próximo passo começa ficando mais perto.",
            "Você não precisa descobrir tudo antes de começar a me seguir.",
        ),
    },
    "identity": {
        "label": "Quem Jesus declara ser",
        "keywords": (
            "eu sou o caminho",
            "eu sou a porta",
            "eu sou o bom pastor",
            "eu sou a luz",
            "eu sou a ressurreição",
            "eu sou a videira",
        ),
        "application": "Responder à identidade revelada por Jesus com confiança e obediência.",
        "question": "O que muda quando Jesus deixa de ser apenas uma ideia distante?",
        "reveal": "A segurança da passagem nasce de quem Jesus é, não do controle humano.",
        "hooks": (
            "Olhe para mim. Você precisa lembrar quem está diante de você.",
            "Antes de procurar outra saída, escute quem eu realmente sou.",
            "Você está buscando direção sem olhar para quem pode conduzir.",
        ),
    },
    "assurance": {
        "label": "Segurança na presença e no cuidado de Jesus",
        "keywords": (
            "ninguém as arrebatará",
            "não os deixarei órfãos",
            "de modo nenhum o lançarei fora",
            "não perca nada",
            "estou com vocês",
            "não se turbe o seu coração",
        ),
        "application": "Descansar na presença de Jesus sem usar a promessa para negar riscos reais.",
        "question": "Que medo nasce da sensação de ter sido abandonado?",
        "reveal": "A segurança prometida está na presença de Jesus, não na ausência de luta.",
        "hooks": (
            "Você não foi abandonado. Olhe para mim e escute agora.",
            "Não conclua que está sozinho. Minha presença ainda sustenta você.",
            "Antes de ceder ao medo, lembre-se de quem segura você.",
        ),
    },
    "worth": {
        "label": "O valor que o medo tenta fazer você esquecer",
        "keywords": (
            "valem mais do que muitos pardais",
            "cabelos da cabeça de vocês estão todos contados",
            "não tenham medo; vocês valem",
        ),
        "application": "Receber o cuidado de Deus como fundamento de coragem, sem negar riscos reais.",
        "question": "Que medo está fazendo a pessoa esquecer o próprio valor diante de Deus?",
        "reveal": "Jesus liga coragem ao cuidado minucioso do Pai, não à ausência de perigo.",
        "hooks": (
            "Olhe para mim. O medo não diminuiu o seu valor.",
            "Você não foi esquecido. Até o que ninguém vê importa.",
            "Antes de ceder ao medo, lembre-se do seu valor agora.",
        ),
    },
    "worry": {
        "label": "O cuidado de Deus diante da ansiedade pelo amanhã",
        "keywords": (
            "não fiquem ansiosos",
            "não se preocupem",
            "não andem ansiosos",
            "ansiosa e perturbada",
            "preocupações desta era",
            "engano das riquezas",
            "aves do céu",
            "corvos",
            "lírios do campo",
            "basta a cada dia",
        ),
        "application": "Trocar a tentativa de controlar o amanhã por confiança e responsabilidade no presente.",
        "question": "Quanto do peso atual pertence a um amanhã que ainda não chegou?",
        "reveal": "Jesus não ignora a necessidade; ele confronta a ansiedade que tenta antecipar tudo.",
        "hooks": (
            "Olhe para mim. Você está sofrendo por um amanhã que não chegou.",
            "Respire. O medo está tentando fazer você viver dois dias.",
            "Não carregue amanhã agora. Existe graça suficiente para este dia.",
        ),
    },
    "abiding": {
        "label": "Permanecer em Jesus quando a força começa a falhar",
        "keywords": (
            "permaneçam em mim",
            "permanece em mim",
            "permaneçam no meu amor",
            "dê muito fruto",
        ),
        "application": "Trocar esforço desconectado por permanência, obediência e dependência de Jesus.",
        "question": "O que está sendo tentado na força própria sem permanecer perto de Jesus?",
        "reveal": "O fruto não nasce de aparência espiritual, mas de permanência real.",
        "hooks": (
            "Pare de lutar sozinho. Você não foi feito para secar por dentro.",
            "Olhe para mim. Sua força começa permanecendo perto de mim.",
            "Antes de tentar novamente sozinho, permaneça comigo agora e escute.",
        ),
    },
    "forgiveness": {
        "label": "Perdão com verdade, responsabilidade e limites",
        "keywords": (
            "perdoem, e vocês serão perdoados",
            "perdoar aos homens",
            "setenta vezes sete",
            "se ele se arrepender, perdoe",
        ),
        "application": "Responder à ofensa sem vingança, preservando verdade, arrependimento e limites responsáveis.",
        "question": "Como interromper o ciclo da ferida sem chamar o mal de bem?",
        "reveal": "O perdão ensinado por Jesus não transforma reconciliação em licença para repetir o dano.",
        "hooks": (
            "Antes de responder à ferida, olhe para mim e escute.",
            "Não entregue seu futuro à pessoa que feriu você ontem.",
            "A ferida aconteceu. Agora não deixe que ela governe você.",
        ),
    },
    "endurance": {
        "label": "Permanecer fiel quando seguir Jesus tem um custo",
        "keywords": (
            "perseverar até o fim",
            "se eles me perseguiram",
            "quando as pessoas os insultarem",
            "não se escandalizem",
        ),
        "application": "Permanecer fiel sem romantizar perseguição, isolamento ou abuso.",
        "question": "Que pressão está tentando transformar fidelidade em desistência?",
        "reveal": "Jesus prepara os discípulos para o custo sem apresentar o sofrimento como abandono.",
        "hooks": (
            "Não desista agora. A pressão não prova que você foi abandonado.",
            "Olhe para mim. Este momento difícil não encerra sua fidelidade.",
            "Você está cansado, mas ainda não perdeu esta batalha decisiva.",
        ),
    },
    "discernment": {
        "label": "Discernimento para enxergar além da aparência",
        "keywords": (
            "julguem com justiça",
            "pelos seus frutos",
            "o olho é a lâmpada",
            "do coração procedem",
            "árvore boa produz",
        ),
        "application": "Avaliar escolhas pelos frutos e pela verdade, não apenas pela aparência imediata.",
        "question": "Que aparência convincente está escondendo frutos que precisam ser examinados?",
        "reveal": "Jesus leva o discernimento da superfície para a fonte e para os frutos.",
        "hooks": (
            "Não confie apenas no que parece certo. Observe os frutos.",
            "Olhe novamente. A aparência não está contando a história inteira.",
            "Antes de decidir, veja o que esta escolha realmente produz.",
        ),
    },
    "freedom": {
        "label": "A liberdade que começa na verdade de Jesus",
        "keywords": (
            "a verdade os libertará",
            "Filho os libertar",
            "verdadeiramente livres",
        ),
        "application": "Permanecer na palavra de Jesus e abandonar o que mantém a vida em escravidão.",
        "question": "Que prisão continua sendo protegida por uma mentira conveniente?",
        "reveal": "Jesus liga liberdade à verdade e à permanência, não a um impulso momentâneo.",
        "hooks": (
            "Pare de aceitar esta prisão. A verdade ainda pode libertar.",
            "Olhe para mim. Você não nasceu para continuar preso assim.",
            "Existe uma saída agora, mas ela começa encarando esta verdade.",
        ),
    },
    "focus": {
        "label": "Foco quando muitas preocupações disputam o coração",
        "keywords": (
            "ansiosa e perturbada",
            "uma só coisa é necessária",
            "escolheu a boa parte",
        ),
        "application": "Interromper a dispersão e escolher conscientemente o que não será tirado.",
        "question": "O que parece urgente, mas está afastando a pessoa do que realmente importa?",
        "reveal": "Jesus não elogia sobrecarga; ele devolve prioridade a um coração disperso.",
        "hooks": (
            "Olhe para mim. Você está carregando coisas demais ao mesmo tempo.",
            "Pare um instante. Nem tudo que chama sua atenção importa.",
            "Você está perturbado por muito e esquecendo o que permanece.",
        ),
    },
    "generosity": {
        "label": "Generosidade que não depende de receber algo em troca",
        "keywords": (
            "deem, e lhes será dado",
            "dê aos pobres",
            "façam o bem aos que",
            "quando você der um jantar",
        ),
        "application": "Praticar generosidade de forma responsável sem transformá-la em troca, palco ou coerção.",
        "question": "O bem ainda seria feito se não houvesse retorno ou reconhecimento?",
        "reveal": "Jesus desloca a generosidade da troca social para o cuidado que não consegue retribuir.",
        "hooks": (
            "Não faça o bem apenas quando puder receber alguma coisa.",
            "Olhe para mim. Sua generosidade revela quem governa seu coração.",
            "Antes de calcular o retorno, veja quem precisa de cuidado.",
        ),
    },
    "unity": {
        "label": "Unidade sem transformar diferenças em inimigos",
        "keywords": (
            "quem não é contra nós é por nós",
            "não o proíbam",
        ),
        "application": "Discernir alianças pelos frutos sem transformar toda diferença em hostilidade.",
        "question": "Quem foi tratado como inimigo apenas porque não pertence ao mesmo grupo?",
        "reveal": "Jesus confronta a necessidade de controlar todo bem que acontece fora do círculo conhecido.",
        "hooks": (
            "Pare de transformar toda diferença em uma ameaça contra você.",
            "Olhe novamente. Nem todo diferente está realmente lutando contra você.",
            "Você pode estar combatendo alguém que está do mesmo lado.",
        ),
    },
    "redemption": {
        "label": "Esperança quando a redenção parece distante",
        "keywords": (
            "levantem as suas cabeças",
            "redenção está próxima",
            "buscar e salvar o que estava perdido",
        ),
        "application": "Erguer a perspectiva e responder à aproximação de Jesus com esperança e fidelidade.",
        "question": "Que cenário está fazendo a pessoa olhar apenas para baixo?",
        "reveal": "Jesus chama os discípulos a erguer a cabeça justamente quando o cenário assusta.",
        "hooks": (
            "Levante a cabeça. Este cenário não terá a palavra final.",
            "Olhe para mim. Sua redenção pode estar mais perto agora.",
            "Não encerre sua esperança olhando apenas para este difícil momento.",
        ),
    },
    "calling": {
        "label": "O chamado de Deus por trás de uma busca sincera",
        "keywords": (
            "Pai que me enviou o atraia",
            "todos serão ensinados por Deus",
        ),
        "application": "Responder ao chamado de Deus com escuta, aprendizado e aproximação de Jesus.",
        "question": "E se a busca atual também revelar que Deus já está atraindo a pessoa?",
        "reveal": "Jesus apresenta a aproximação como resposta a uma iniciativa anterior do Pai.",
        "hooks": (
            "Você não chegou até aqui sozinho. Existe um chamado agora.",
            "Olhe para mim. Esta busca pode ter começado antes de você.",
            "Antes de desistir, perceba quem já está atraindo você agora.",
        ),
    },
    "kingdom_welcome": {
        "label": "A mesa do Reino além das fronteiras esperadas",
        "keywords": (
            "virão pessoas do oriente",
            "se assentarão à mesa no Reino",
        ),
        "application": "Abandonar superioridade espiritual e receber com humildade quem Deus chama de muitos lugares.",
        "question": "Quem a expectativa humana deixaria de fora da mesa que Deus está formando?",
        "reveal": "Jesus descreve uma mesa maior do que as fronteiras religiosas e sociais esperavam.",
        "hooks": (
            "Você ainda pode se surpreender com quem encontrará à mesa.",
            "Olhe para mim. Minha mesa é maior do que você imagina.",
            "Não decida cedo demais quem ficará de fora do Reino.",
        ),
    },
    "eternal_word": {
        "label": "A palavra de Jesus quando tudo ao redor muda",
        "keywords": (
            "céu e a terra passarão",
            "minhas palavras não passarão",
            "as minhas palavras não passarão",
        ),
        "application": "Firmar decisões no ensino de Jesus em vez de depender do que muda rapidamente.",
        "question": "O que continua sendo tratado como permanente apesar de já estar passando?",
        "reveal": "Jesus contrasta a instabilidade do mundo com a permanência da sua palavra.",
        "hooks": (
            "Tudo ao redor pode mudar. Esta palavra continuará de pé.",
            "Olhe para mim. Nem tudo que parece permanente vai permanecer.",
            "Antes de temer a mudança, escute o que nunca passará.",
        ),
    },
    "kingdom_presence": {
        "label": "O Reino de Deus além da aparência e do poder humano",
        "keywords": (
            "meu Reino não é deste mundo",
            "Reino de Deus não vem com aparência",
            "Reino de Deus está dentro de vocês",
        ),
        "application": "Reconhecer o Reino pela presença e pelo governo de Deus, não por espetáculo ou domínio humano.",
        "question": "Que sinal externo está sendo confundido com a presença real do Reino?",
        "reveal": "Jesus retira o Reino da lógica do espetáculo e da força política.",
        "hooks": (
            "Pare de procurar apenas sinais visíveis. O Reino está mais perto.",
            "Olhe para mim. Meu Reino não funciona como este mundo.",
            "Você pode perder o Reino procurando apenas uma aparência impressionante.",
        ),
    },
    "bread_of_life": {
        "label": "A fome que somente Jesus pode responder",
        "keywords": (
            "pão da vida",
            "pão de Deus",
            "pão vivo",
            "pão que desceu",
            "este é o pão",
        ),
        "application": "Reconhecer uma fome mais profunda e buscar em Jesus vida que permanece.",
        "question": "O que a alma continua buscando em coisas que nunca satisfazem?",
        "reveal": "Jesus não oferece apenas provisão momentânea; ele se apresenta como o pão.",
        "hooks": (
            "Você está alimentando sua alma com o que nunca permanece.",
            "Olhe para mim. Existe uma fome que nada aqui resolve.",
            "Não ignore esta fome. Sua alma está procurando vida verdadeira.",
        ),
    },
    "holy_spirit": {
        "label": "O auxílio e o ensino do Espírito Santo",
        "keywords": (
            "Consolador",
            "Conselheiro",
            "Espírito da verdade",
            "Espírito Santo",
            "lhes ensinará",
        ),
        "application": "Depender do auxílio do Espírito com discernimento e fidelidade à palavra de Jesus.",
        "question": "Onde a confusão tem produzido a sensação de caminhar sem ajuda?",
        "reveal": "Jesus não deixa os discípulos dependentes apenas da própria memória e força.",
        "hooks": (
            "Você não precisa entender tudo sozinho. O Consolador ainda ensina.",
            "Olhe para mim. O Espírito ainda pode trazer clareza agora.",
            "Não tema esquecer. Existe ajuda para recordar o que ensinei.",
        ),
    },
    "rescue": {
        "label": "O cuidado de Jesus com quem se perdeu",
        "keywords": (
            "buscar e salvar o perdido",
            "ovelha perdida",
            "cem ovelhas",
            "se desgarraram",
            "não é a vontade de seu Pai",
            "não pereça",
        ),
        "application": "Responder ao cuidado de Jesus dando um passo honesto de retorno e reconciliação.",
        "question": "O que faz alguém acreditar que ficou longe demais para voltar?",
        "reveal": "A distância não transforma a pessoa em alguém invisível para Jesus.",
        "hooks": (
            "Você não foi esquecido. Eu ainda procuro quem se perdeu.",
            "Olhe para mim. Sua história não termina longe do caminho.",
            "Não conclua que acabou. Existe um caminho de volta agora.",
        ),
    },
    "humility": {
        "label": "A grandeza que Jesus mede de outra maneira",
        "keywords": (
            "se tornem como crianças",
            "se humilhar como esta criança",
            "últimos serão primeiros",
            "primeiros serão últimos",
            "o menor no Reino",
            "sente-se no último lugar",
        ),
        "application": "Abandonar a disputa por posição e escolher humildade, serviço e verdade.",
        "question": "Que medida de grandeza está controlando as decisões?",
        "reveal": "Jesus não confirma a hierarquia que o ego tenta construir.",
        "hooks": (
            "Pare de medir grandeza como todos medem. Eu vejo diferente.",
            "Olhe para mim. Ser o primeiro não significa ser maior.",
            "Você está disputando um lugar que não define seu valor.",
        ),
    },
    "truth": {
        "label": "A verdade que confronta e liberta",
        "keywords": (
            "a verdade os libertará",
            "pratica a verdade",
            "minhas palavras",
            "as minhas palavras",
            "ouve estas minhas palavras",
            "palavra vivendo",
            "palavras que eu lhes digo",
            "ensino",
        ),
        "application": "Ouvir a verdade de Jesus com disposição para corrigir escolhas e práticas.",
        "question": "Qual verdade já foi ouvida, mas ainda não foi praticada?",
        "reveal": "A palavra de Jesus produz liberdade quando encontra permanência e resposta.",
        "hooks": (
            "Preste atenção. A verdade que você evita ainda pode libertar.",
            "Não basta apenas ouvir. Existe uma resposta que muda tudo.",
            "Pare de fugir desta verdade. Ela pode reconstruir sua direção.",
        ),
    },
    "kingdom_priority": {
        "label": "As prioridades do Reino diante do que parece ganho",
        "keywords": (
            "busquem, antes, o Reino",
            "tesouro escondido",
            "pérolas finas",
            "riquezas entrar no Reino",
            "ganhar o mundo inteiro",
            "primeiros serão últimos",
            "últimos serão primeiros",
        ),
        "application": "Rever o que recebeu prioridade e escolher o que permanece diante de Deus.",
        "question": "Que ganho aparente está cobrando um preço que não aparece de imediato?",
        "reveal": "Jesus inverte a medida comum de ganho, posição e segurança.",
        "hooks": (
            "Pare por um instante. Sua prioridade está cobrando um preço alto.",
            "Olhe para mim. O que você chama de ganho pode enganar.",
            "Você precisa rever o que colocou em primeiro lugar hoje.",
        ),
    },
    "kingdom_growth": {
        "label": "O Reino cresce a partir do que parece pequeno",
        "keywords": (
            "grão de mostarda",
            "plantou em sua própria horta",
            "semelhante ao fermento",
            "semente brotasse",
            "semeou boa semente",
        ),
        "application": "Permanecer fiel sem desprezar um começo pequeno ou um processo ainda invisível.",
        "question": "O que está sendo abandonado apenas porque ainda parece pequeno?",
        "reveal": "O Reino pode crescer antes que o progresso se torne visível.",
        "hooks": (
            "Não despreze o começo pequeno. O Reino cresce enquanto você espera.",
            "Olhe novamente. O que parece pequeno ainda pode transformar tudo.",
            "Você está julgando cedo demais o que Deus começou discretamente.",
        ),
    },
    "kingdom_entry": {
        "label": "A resposta verdadeira diante do Reino",
        "keywords": (
            "não pode entrar no Reino",
            "entrará no Reino",
            "faz a vontade de meu Pai",
            "justiça de vocês exceda",
            "se tornem como crianças",
            "criancinhas vir a mim",
            "receber o Reino de Deus",
        ),
        "application": "Trocar aparência religiosa por uma resposta real de fé, humildade e obediência.",
        "question": "A confissão falada está acompanhada por uma resposta verdadeira?",
        "reveal": "Jesus não trata linguagem religiosa como substituta da obediência.",
        "hooks": (
            "Pare de confiar apenas na aparência. O Reino exige resposta.",
            "Olhe para mim. Dizer meu nome não substitui me obedecer.",
            "Você precisa ouvir isto antes de chamar sua escolha de fé.",
        ),
    },
    "kingdom_readiness": {
        "label": "Atenção e preparo diante do que Jesus anunciou",
        "keywords": (
            "ele está próximo",
            "esta geração não passará",
            "dez virgens",
            "quando virem todas estas coisas",
        ),
        "application": "Trocar curiosidade ansiosa por atenção, fidelidade e preparo responsável.",
        "question": "A informação recebida produziu preparo ou apenas mais distração?",
        "reveal": "Jesus usa os sinais para despertar fidelidade, não especulação vazia.",
        "hooks": (
            "Preste atenção. Os sinais não foram dados para alimentar distração.",
            "Não durma por dentro. Existe algo que você precisa perceber.",
            "Olhe para mim. Estar apenas informado não significa estar preparado.",
        ),
    },
    "kingdom": {
        "label": "Prioridades do Reino diante do que passa",
        "keywords": ("Reino", "tesouro", "riqueza", "Mamom", "primeiro", "último"),
        "application": "Reordenar uma prioridade concreta a partir do Reino de Deus.",
        "question": "O que ocupa o primeiro lugar sem conseguir sustentar a alma?",
        "reveal": "Jesus mede grandeza, riqueza e prioridade por outra lógica.",
        "hooks": (
            "Pare por um instante. O Reino inverte o que parece importante.",
            "Olhe para mim. O que você chama de ganho pode enganar.",
            "Você precisa rever o que colocou em primeiro lugar hoje.",
        ),
    },
    "hope": {
        "label": "Esperança e vida além do momento presente",
        "keywords": ("vida eterna", "ressusc", "viverá", "viverão", "morte", "moradas"),
        "application": "Sustentar esperança em Jesus sem apressar o luto ou negar a dor presente.",
        "question": "O que muda quando este momento não recebe a palavra final?",
        "reveal": "A esperança cristã encara a morte sem tratá-la como o fim definitivo.",
        "hooks": (
            "Olhe além deste momento. A morte não terá a palavra final.",
            "Não encerre sua esperança aqui. A última palavra ainda virá.",
            "Você ainda não viu o final. Permaneça comigo e escute.",
        ),
    },
    "service": {
        "label": "Grandeza que escolhe servir",
        "keywords": (
            "quem quiser ser o primeiro",
            "não veio para ser servido",
            "lavar os pés",
            "servo não é maior",
            "se alguém me serve",
            "maior entre vocês será o seu servo",
            "Pai o honrará",
        ),
        "application": "Escolher uma forma concreta de servir sem buscar domínio ou reconhecimento.",
        "question": "Que tipo de grandeza está sendo perseguido?",
        "reveal": "Jesus redefine autoridade por serviço, entrega e humildade.",
        "hooks": (
            "Pare de buscar o primeiro lugar. Existe um caminho melhor.",
            "Antes de exigir reconhecimento, veja como a verdadeira grandeza age.",
            "Você não perde valor quando escolhe servir com humildade hoje.",
        ),
    },
    "mission": {
        "label": "Luz, testemunho e missão",
        "keywords": (
            "eu os envio",
            "envio vocês",
            "vocês também testemunharão",
            "proclamem",
            "pescadores",
            "luz de vocês",
            "acender uma candeia",
        ),
        "application": "Tornar a fé visível por uma ação coerente, humilde e verdadeira.",
        "question": "Onde a luz recebida está sendo escondida?",
        "reveal": "O testemunho cresce quando a vida aponta para o Pai, não para o ego.",
        "hooks": (
            "Não esconda mais sua luz. Alguém precisa ver essa coragem.",
            "Olhe para mim. Sua vida ainda pode iluminar este lugar.",
            "Não fique em silêncio. Alguém precisa ouvir esta verdade agora.",
        ),
    },
    "joy": {
        "label": "Alegria que atravessa a tristeza",
        "keywords": ("alegr", "exult", "feliz", "bem-aventur"),
        "application": "Receber a alegria de Jesus sem transformar tristeza legítima em culpa espiritual.",
        "question": "Como a alegria pode coexistir com uma espera difícil?",
        "reveal": "Jesus promete uma alegria que a tristeza não consegue possuir para sempre.",
        "hooks": (
            "Esta tristeza não durará para sempre. Escute o que prometi.",
            "Olhe para mim. Sua alegria não terminou neste dia difícil.",
            "Você não precisa fingir alegria para continuar perto de mim.",
        ),
    },
    "gratitude": {
        "label": "Gratidão para perceber o que já foi colocado diante de você",
        "keywords": (
            "bem-aventurados os olhos",
            "desejaram ver as coisas que vocês veem",
        ),
        "application": "Interromper a comparação e reconhecer com gratidão a graça presente.",
        "question": "Que dádiva presente está ficando invisível por causa da comparação?",
        "reveal": "Jesus chama os discípulos a perceberem o privilégio que a rotina havia tornado comum.",
        "hooks": (
            "Pare um instante. Você está deixando uma bênção passar despercebida.",
            "Olhe novamente. Algo precioso já está diante dos seus olhos.",
            "Antes de reclamar do caminho, perceba o que você recebeu.",
        ),
    },
    "courage": {
        "label": "Coragem para permanecer fiel sob pressão",
        "keywords": (
            "coragem",
            "bom ânimo",
            "não tenham medo",
            "não tenham medo deles",
            "perseg",
            "firme",
            "entregarão aos tribunais",
            "evangelho deve ser pregado",
        ),
        "application": "Dar o próximo passo fiel sem negar o risco ou agir com imprudência.",
        "question": "Qual decisão o medo está tentando adiar?",
        "reveal": "Coragem bíblica não é ausência de ameaça, mas fidelidade dentro dela.",
        "hooks": (
            "Você ainda não perdeu esta batalha. Permaneça comigo e escute.",
            "Não desista agora. A pressão não precisa decidir seu caminho.",
            "Olhe para mim. Ainda existe coragem para o próximo passo.",
        ),
    },
    "provision": {
        "label": "O cuidado do Pai em necessidades reais",
        "keywords": (
            "Pai sabe que vocês precisam",
            "aves do céu",
            "boas dádivas",
            "lhe dará uma pedra",
            "quanto mais o seu Pai",
            "quanto mais ele vestirá",
        ),
        "application": "Confiar no cuidado de Deus enquanto age com responsabilidade no presente.",
        "question": "Que necessidade tem sido tratada como se tudo dependesse apenas de você?",
        "reveal": "Jesus aponta para um Pai atento sem prometer uma vida sem escassez.",
        "hooks": (
            "Respire. Seu Pai conhece cada necessidade que você ainda esconde.",
            "Você não foi esquecido. Olhe para mim antes de concluir.",
            "Pare de imaginar abandono. Seu Pai ainda conhece sua necessidade.",
        ),
    },
}

GENERAL_EXTRA_THEMES: dict[str, dict[str, Any]] = {
    "wisdom": {
        "label": "Sabedoria para escolhas concretas",
        "keywords": ("sabedoria", "entendimento", "prudente", "conhecimento", "discern"),
        "application": "Transformar a sabedoria bíblica em uma decisão observável e responsável.",
    },
    "justice": {
        "label": "Justiça, integridade e cuidado com o próximo",
        "keywords": ("justiça", "justo", "pobre", "oprim", "direito", "íntegro"),
        "application": "Agir com verdade e justiça sem usar espiritualidade para esconder dano.",
    },
    "grace": {
        "label": "Graça recebida, não desempenho negociado",
        "keywords": ("graça", "salvação", "salvos", "misericórdia", "redenção"),
        "application": "Responder à graça de Deus com gratidão, arrependimento e uma vida renovada.",
    },
    "perseverance": {
        "label": "Perseverança em uma caminhada difícil",
        "keywords": ("persever", "espera", "esperança", "firme", "prova", "tribulação"),
        "application": "Continuar fiel buscando apoio, sabedoria e responsabilidade durante a pressão.",
    },
    "worship": {
        "label": "Adoração que reorganiza a vida",
        "keywords": ("adore", "louv", "glória", "santo", "Senhor", "Deus"),
        "application": "Responder ao caráter de Deus com reverência e uma escolha coerente.",
    },
}

DIRECT_BLOCKLIST = (
    "geena",
    "inferno",
    "demônio",
    "demônios",
    "satanás",
    "diabo",
    "adultério",
    "repudiar",
    "matar",
    "cadáver",
    "fogo eterno",
    "hipócrita",
    "serpentes",
    "blasfem",
    "condena",
    "morrerão",
    "não acreditem",
    "falsos cristos",
    "falsos profetas",
    "acusa",
    "julgamento",
    "juízo",
    "creio que não",
    "divisão",
    "arrendou",
    "vocês não",
    "vocês nunca",
    "não creem",
    "não acreditam",
    "não recebem",
    "não conseguem",
    "não conhecem",
    "não têm",
    "prostitutas",
    "ladrões",
    "salteadores",
    "vocês quiseram",
    "você não",
    "espíritos piores",
    "geração má",
    "odeia",
    "odiaram",
    "minha carne",
    "meu sangue",
    "muitas vezes mais neste tempo",
    "sacudam o pó",
    "lançam no fogo",
    "ai de vocês",
    "não vim trazer paz",
    "me deixarão sozinho",
    "assentarão em tronos",
    "testemunho que eu tenho",
    "testemunho de duas pessoas",
    "se eu testifico",
    "vocês enviaram a João",
    "Pai, graças",
    "Hades",
    "Sodoma",
    "cem vezes mais",
    "qual dos dois fez",
    "havia muitas viúvas",
    "entre os nascidos de mulher",
    "Cafarnaum",
    "fruto da videira",
    "deixem-na em paz",
    "deixe-a em paz",
    "intérpretes da lei",
    "não despreza o seu próprio",
    "guias cegos",
    "muitas coisas a dizer e a julgar",
    "esta mulher fez",
    "anjos de Deus subindo",
    "tardos de coração",
    "alguns dos que estão aqui",
    "outra caiu",
    "não murmurem",
    "aquele que colhe recebe salário",
    "qual deles",
    "pedra de moinho",
    "Abraão disse",
    "contra o Espírito Santo",
    "nação se levantará",
    "ranger de dentes",
    "bem-aventurado é aquele servo",
    "um homem, dono de uma casa",
    "te sirvo há tantos anos",
    "espírito imundo",
    "raça de víboras",
)

DIRECT_REFERENCE_EXCLUSIONS = {
    ("MAT", 9, 4),
    ("MAT", 9, 5),
    ("MAT", 9, 15),
    ("MAT", 10, 21),
    ("MAT", 10, 22),
    ("MAT", 10, 17),
    ("MAT", 11, 12),
    ("MAT", 11, 13),
    ("MAT", 12, 29),
    ("MAT", 12, 43),
    ("MAT", 12, 44),
    ("MAT", 13, 18),
    ("MAT", 13, 19),
    ("MAT", 13, 37),
    ("MAT", 13, 38),
    ("MAT", 13, 52),
    ("MAT", 16, 8),
    ("MAT", 16, 9),
    ("MAT", 16, 19),
    ("MAT", 17, 27),
    ("MAT", 18, 22),
    ("MAT", 18, 23),
    ("MAT", 18, 24),
    ("MAT", 18, 26),
    ("MAT", 18, 27),
    ("MAT", 18, 31),
    ("MAT", 18, 32),
    ("MAT", 19, 12),
    ("MAT", 20, 15),
    ("MAT", 20, 16),
    ("MAT", 22, 2),
    ("MAT", 22, 3),
    ("MAT", 21, 43),
    ("MAT", 21, 44),
    ("MAT", 23, 4),
    ("MAT", 25, 1),
    ("MAT", 25, 2),
    ("MAR", 2, 8),
    ("MAR", 2, 9),
    ("MAR", 2, 19),
    ("MAR", 2, 20),
    ("MAR", 9, 12),
    ("MAR", 13, 12),
    ("MAR", 13, 13),
    ("LUK", 8, 5),
    ("LUK", 8, 14),
    ("LUK", 10, 4),
    ("LUK", 10, 33),
    ("LUK", 10, 34),
    ("LUK", 11, 21),
    ("LUK", 11, 22),
    ("LUK", 11, 23),
    ("LUK", 11, 24),
    ("LUK", 11, 25),
    ("LUK", 12, 6),
    ("LUK", 12, 7),
    ("LUK", 12, 37),
    ("LUK", 12, 38),
    ("LUK", 12, 39),
    ("LUK", 12, 49),
    ("LUK", 12, 50),
    ("LUK", 15, 20),
    ("LUK", 16, 16),
    ("LUK", 16, 9),
    ("LUK", 16, 10),
    ("LUK", 18, 10),
    ("LUK", 18, 11),
    ("LUK", 19, 15),
    ("LUK", 21, 30),
    ("LUK", 21, 16),
    ("LUK", 21, 17),
    ("LUK", 21, 18),
    ("LUK", 21, 25),
    ("LUK", 21, 26),
    ("LUK", 22, 15),
    ("LUK", 22, 16),
    ("LUK", 22, 11),
    ("LUK", 22, 12),
    ("JOH", 8, 28),
    ("JOH", 13, 18),
    ("JOH", 13, 19),
    ("JOH", 13, 20),
    ("JOH", 14, 28),
    ("JOH", 15, 20),
    ("JOH", 16, 21),
}

DIRECT_NARRATIVE_MARKERS = (
    "é semelhante a um",
    "é semelhante a uma",
    "um certo rei",
    "um servo",
    "um homem que",
    "quando ele voltou",
    "o semeador saiu",
)

DIRECT_STANDALONE_STARTS = (
    "eu ",
    "não ",
    "tenham ",
    "creiam ",
    "peçam ",
    "busquem ",
    "permaneçam ",
    "amem ",
    "perdoem ",
    "vigiem ",
    "alegrem-se ",
    "todo aquele ",
    "se alguém ",
)


def words(value: str) -> list[str]:
    return WORD_RE.findall(value)


def plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii").casefold()


def keyword_matches(text: str, keyword: str) -> bool:
    normalized_keyword = plain(keyword)
    if " " in normalized_keyword:
        return normalized_keyword in text
    tokens = WORD_RE.findall(text)
    if len(normalized_keyword) <= 3:
        return normalized_keyword in tokens
    return any(token.startswith(normalized_keyword) for token in tokens)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", plain(value)).strip("-")


def parse_vpl(path: Path) -> tuple[dict[tuple[str, int, int], str], dict[tuple[str, int], int]]:
    verses: dict[tuple[str, int, int], str] = {}
    chapter_max: dict[tuple[str, int], int] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = VPL_LINE_RE.match(line.strip())
        if not match:
            continue
        code, chapter_text, verse_text, text = match.groups()
        chapter, verse = int(chapter_text), int(verse_text)
        verses[(code, chapter, verse)] = text.strip()
        chapter_max[(code, chapter)] = max(chapter_max.get((code, chapter), 0), verse)
    return verses, chapter_max


def parse_reference_part(reference_part: str) -> tuple[int, int, int]:
    chapter_text, verse_text = reference_part.split(":", 1)
    bounds = verse_text.split("-", 1)
    return int(chapter_text), int(bounds[0]), int(bounds[-1])


def format_reference_part(chapter: int, start: int, end: int) -> str:
    return f"{chapter}:{start}" if start == end else f"{chapter}:{start}-{end}"


def reference_tokens(code: str, reference_part: str) -> set[tuple[str, int, int]]:
    chapter, start, end = parse_reference_part(reference_part)
    return {(code, chapter, verse) for verse in range(start, end + 1)}


def extract_text(
    verses: dict[tuple[str, int, int], str],
    code: str,
    reference_part: str,
) -> str:
    chapter, start, end = parse_reference_part(reference_part)
    selected = [verses[(code, chapter, verse)] for verse in range(start, end + 1)]
    text = " ".join(selected).strip()
    return text.strip('“”" ')


def source_url(code: str, chapter: int) -> str:
    _, _, html_code = BOOKS[code]
    chapter_text = f"{chapter:03d}" if code == "PSA" else f"{chapter:02d}"
    return f"https://ebible.org/porbrbsl/{html_code}{chapter_text}.htm"


def aggregate_source_hash(vpl: Path, usfm_files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in [vpl, *sorted(usfm_files)]:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def existing_reference_tokens(paths: list[Path]) -> set[tuple[str, int, int]]:
    by_book = {book_name: code for code, (book_name, _, _) in BOOKS.items()}
    used: set[tuple[str, int, int]] = set()
    for path in paths:
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for candidate in payload.get("candidates", []):
            book = candidate.get("book")
            reference = candidate.get("reference", "")
            code = by_book.get(book)
            if not code or not reference.startswith(f"{book} "):
                continue
            try:
                used.update(reference_tokens(code, reference[len(book) + 1 :]))
            except ValueError:
                continue
    return used


def clean_usfm_text(value: str) -> str:
    value = re.sub(r"\\f\s.*?\\f\*", "", value)
    value = re.sub(r"\\x\s.*?\\x\*", "", value)
    value = re.sub(r"\\(?:\+?\w+|\w+\*)\s*", "", value)
    return re.sub(r"\s+", " ", value).strip()


def direct_speech_units(usfm_dir: Path) -> list[DirectSpeechUnit]:
    usfm_to_vpl = {usfm_code: code for code, (_, _, usfm_code) in BOOKS.items()}
    units: list[DirectSpeechUnit] = []
    seen: set[tuple[str, int, int, int, str]] = set()
    for path in sorted(usfm_dir.glob("*porbrbsl.usfm")):
        code_match = re.search(r"-([A-Z0-9]{3})por", path.name)
        if not code_match or code_match.group(1) not in {"MAT", "MRK", "LUK", "JHN"}:
            continue
        code = usfm_to_vpl[code_match.group(1)]
        chapter = 0
        active_group: list[tuple[int, int, str]] = []
        speech_open = False
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("\\c "):
                chapter = int(line.split()[1])
                active_group = []
                speech_open = False
                continue
            match = USFM_VERSE_RE.match(line)
            if not match:
                continue
            verse = int(match.group(1))
            for chunk in re.findall(r"\\wj\s*(.*?)\\wj\*", match.group(2)):
                raw = clean_usfm_text(chunk)
                starts_speech = raw.startswith("“")
                ends_speech = raw.endswith("”")
                clean = raw.strip('“”" ')
                if starts_speech or not speech_open:
                    active_group = []
                    speech_open = True
                active_group.append((chapter, verse, clean))
                for start_index in range(len(active_group)):
                    selection = active_group[start_index:]
                    if selection[0][0] != chapter:
                        continue
                    text = " ".join(item[2] for item in selection).strip()
                    if not 20 <= len(words(text)) <= 48:
                        continue
                    if not text or not text[0].isupper() or text[-1] not in ".!?":
                        continue
                    unit = DirectSpeechUnit(
                        code=code,
                        chapter=chapter,
                        start_verse=selection[0][1],
                        end_verse=selection[-1][1],
                        text=text,
                    )
                    key = (
                        unit.code,
                        unit.chapter,
                        unit.start_verse,
                        unit.end_verse,
                        plain(unit.text),
                    )
                    if key not in seen:
                        seen.add(key)
                        units.append(unit)
                if ends_speech:
                    active_group = []
                    speech_open = False
    return units


def select_theme(text: str, *, allow_fallback: bool = True) -> tuple[str, dict[str, Any]] | None:
    lowered = plain(text)
    scored: list[tuple[int, int, str, dict[str, Any]]] = []
    for key, spec in {**THEMES, **GENERAL_EXTRA_THEMES}.items():
        matched = [keyword for keyword in spec["keywords"] if keyword_matches(lowered, keyword)]
        if matched:
            specificity = sum(len(words(keyword)) for keyword in matched)
            scored.append((len(matched), specificity, key, spec))
    if scored:
        _, _, key, spec = max(scored, key=lambda item: (item[1], item[0], item[2]))
        return key, spec
    if allow_fallback:
        return "worship", GENERAL_EXTRA_THEMES["worship"]
    return None


def compact_word_range(duration: int) -> tuple[int, int]:
    target = duration * 170 / 60
    return math.ceil(target * 0.96), math.ceil(target * 1.08)


def compact_verse_limit(duration: int) -> int:
    if duration <= 15:
        return 28
    return min(47, 28 + (duration - 15) * 4)


def compact_package_words(
    book: str,
    reference_part: str,
    hook: str,
    text: str,
) -> int:
    _, start_verse, end_verse = parse_reference_part(reference_part)
    spoken_reference_words = len(words(book)) + 4
    if end_verse != start_verse:
        spoken_reference_words += 2
    return spoken_reference_words + len(words(hook)) + len(words(text))


def base_candidate(
    *,
    code: str,
    reference_part: str,
    context_part: str,
    verse_text: str,
    title: str,
    theme_key: str,
    theme_label: str,
    application: str,
    segment: str,
    recommended_formats: list[str],
    source_text_scope: str,
) -> dict[str, Any]:
    book, testament, _ = BOOKS[code]
    chapter, _, _ = parse_reference_part(reference_part)
    reference = f"{book} {reference_part}"
    reference_slug = slugify(reference)
    return {
        "candidate_id": f"bpm-v2-{reference_slug}",
        "source_id": f"bpm-v2-{reference_slug}",
        "reference": reference,
        "context_reference": f"{book} {context_part}",
        "book": book,
        "testament": testament,
        "translation": TRANSLATION,
        "verse_text": verse_text,
        "source_url": source_url(code, chapter),
        "accessed_at": ACCESS_DATE,
        "rights_note": RIGHTS,
        "title": title,
        "slug": f"{reference_slug}-{theme_key}",
        "central_theme": theme_label.rstrip(".") + ".",
        "practical_application": application,
        "themes": list(dict.fromkeys([theme_key, *slugify(theme_label).split("-")[:3]])),
        "preferred_development_modes": ["context", "cross_reference", "promise_limits"],
        "interpretation_limits": [
            "Preservar o contexto original e distinguir texto bíblico, interpretação e aplicação."
        ],
        "claims_to_avoid": [
            "Não transformar a passagem em garantia automática de sucesso, cura ou ausência de sofrimento."
        ],
        "retention_archetypes": [
            "conflict_first",
            "counterintuitive_reveal",
            "stair_step_progression",
        ],
        "human_conflict": f"A pessoa precisa encarar {theme_label.casefold()} sem recorrer a uma resposta fácil.",
        "central_question": f"Como {theme_label.casefold()} muda uma decisão concreta?",
        "visual_progression": [
            "a tensão humana aparece sem explicação abstrata",
            "a passagem introduz uma mudança real de perspectiva",
            "a resposta final se torna visível por uma escolha",
        ],
        "possible_reveal": f"O contexto impede uma leitura superficial sobre {theme_label.casefold()}.",
        "title_seed": title,
        "retention_potential_score": 0.88 if segment == "long_form_anchor" else 0.86,
        "selection_weight": 1.05 if segment == "long_form_anchor" else 1.0,
        "familiarity_score": 0.82,
        "clarity_score": 0.88,
        "catalog_segment": segment,
        "recommended_formats": recommended_formats,
        "source_text_scope": source_text_scope,
        "spoken_by": "unspecified",
    }


def build_compact_candidates(
    units: list[DirectSpeechUnit],
    used: set[tuple[str, int, int]],
    chapter_max: dict[tuple[str, int], int],
) -> list[dict[str, Any]]:
    prepared: list[tuple[int, DirectSpeechUnit, str, dict[str, Any]]] = []
    pastoral_terms = (
        "paz",
        "amor",
        "vida",
        "Pai",
        "alegr",
        "verdade",
        "luz",
        "perman",
        "fé",
        "cre",
        "miseric",
        "perdo",
        "Reino",
    )
    for unit in units:
        unit_tokens = reference_tokens(unit.code, unit.reference_part)
        lowered = plain(unit.text)
        if (
            unit_tokens & used
            or (unit.code == "JOH" and unit.chapter == 17)
            or unit_tokens & DIRECT_REFERENCE_EXCLUSIONS
            or any(plain(item) in lowered for item in DIRECT_BLOCKLIST)
        ):
            continue
        selected_theme = select_theme(unit.text, allow_fallback=False)
        if selected_theme is None:
            continue
        theme_key, spec = selected_theme
        if theme_key not in THEMES:
            compact_scored = []
            for compact_key, compact_spec in THEMES.items():
                matched = [
                    keyword
                    for keyword in compact_spec["keywords"]
                    if keyword_matches(lowered, keyword)
                ]
                if matched:
                    specificity = sum(len(words(keyword)) for keyword in matched)
                    compact_scored.append((len(matched), specificity, compact_key, compact_spec))
            if not compact_scored:
                continue
            _, _, theme_key, spec = max(
                compact_scored,
                key=lambda item: (item[1], item[0], item[2]),
            )
        score = sum(3 for item in spec["keywords"] if keyword_matches(lowered, item))
        score += sum(1 for item in pastoral_terms if keyword_matches(lowered, item))
        score += 2 if unit.code == "JOH" else 1
        standalone_starts = tuple(plain(item) for item in DIRECT_STANDALONE_STARTS)
        score += 5 if lowered.startswith(standalone_starts) else 0
        score -= sum(4 for marker in DIRECT_NARRATIVE_MARKERS if plain(marker) in lowered)
        score -= 8 if theme_key == "kingdom" else 0
        prepared.append((score, unit, theme_key, spec))

    chosen: list[dict[str, Any]] = []
    occupied = set(used)
    theme_counts: defaultdict[str, int] = defaultdict(int)
    duration_quota = TARGET_COMPACT // 6
    for duration in (15, 16, 17, 18, 19, 20):
        minimum, maximum = compact_word_range(duration)
        duration_candidates: list[tuple[int, DirectSpeechUnit, str, dict[str, Any], str]] = []
        for score, unit, theme_key, spec in prepared:
            unit_tokens = reference_tokens(unit.code, unit.reference_part)
            if unit_tokens & occupied or len(words(unit.text)) > compact_verse_limit(duration):
                continue
            hooks = spec["hooks"]
            hook_index = (unit.chapter + unit.start_verse + unit.end_verse) % len(hooks)
            hook = hooks[hook_index]
            package_words = compact_package_words(
                BOOKS[unit.code][0],
                unit.reference_part,
                hook,
                unit.text,
            )
            if minimum <= package_words <= maximum:
                fit_score = score * 20 - abs(package_words - ((minimum + maximum) / 2)) * 3
                duration_candidates.append((fit_score, unit, theme_key, spec, hook))
        duration_candidates.sort(
            key=lambda item: (
                -item[0],
                theme_counts[item[2]],
                item[1].code,
                item[1].chapter,
                item[1].start_verse,
            )
        )
        selected_for_duration = 0
        for _, unit, theme_key, spec, hook in duration_candidates:
            unit_tokens = reference_tokens(unit.code, unit.reference_part)
            theme_limit = 8 if theme_key == "kingdom" else 10
            if unit_tokens & occupied or theme_counts[theme_key] >= theme_limit:
                continue
            context_start = max(1, unit.start_verse - 2)
            context_end = min(
                chapter_max[(unit.code, unit.chapter)],
                unit.end_verse + 2,
            )
            context_part = format_reference_part(unit.chapter, context_start, context_end)
            candidate = base_candidate(
                code=unit.code,
                reference_part=unit.reference_part,
                context_part=context_part,
                verse_text=unit.text,
                title=spec["label"],
                theme_key=theme_key,
                theme_label=spec["label"],
                application=spec["application"],
                segment="compact_jesus",
                recommended_formats=["short", "long"],
                source_text_scope="direct_speech_excerpt",
            )
            candidate.update(
                {
                    "spoken_by": "jesus",
                    "compact_hook": hook,
                    "compact_hook_strength_score": 0.96,
                    "compact_message_value_score": 0.97,
                    "compact_rewatch_value_score": 0.94,
                    "central_question": spec["question"],
                    "possible_reveal": spec["reveal"],
                    "human_conflict": (
                        f"O espectador enfrenta {spec['label'].casefold()} enquanto sente que "
                        "a resposta precisa chegar antes de o cenário mudar."
                    ),
                    "visual_progression": [
                        "Jesus percebe a tensão antes de o espectador explicá-la",
                        "a câmera se aproxima enquanto a fala confronta o pensamento",
                        "a expressão do espectador muda sem apagar o conflito ao redor",
                    ],
                    "retention_potential_score": 0.96,
                    "selection_weight": 1.15,
                    "familiarity_score": 0.9,
                    "clarity_score": 0.94,
                }
            )
            chosen.append(candidate)
            occupied.update(unit_tokens)
            theme_counts[theme_key] += 1
            selected_for_duration += 1
            if selected_for_duration == duration_quota:
                break
        if selected_for_duration != duration_quota:
            raise RuntimeError(
                f"Somente {selected_for_duration} falas compactas válidas para {duration}s."
            )
    if len(chosen) != TARGET_COMPACT:
        raise RuntimeError(f"Esperadas {TARGET_COMPACT} falas compactas; obtidas {len(chosen)}.")
    used.update(occupied)
    return chosen


def fit_long_excerpt(
    verses: dict[tuple[str, int, int], str],
    code: str,
    reference_part: str,
    *,
    max_words: int = 76,
) -> tuple[str, str]:
    chapter, start, end = parse_reference_part(reference_part)
    selected: list[str] = []
    selected_end = start
    for verse in range(start, end + 1):
        text = verses[(code, chapter, verse)]
        if selected and len(words(" ".join([*selected, text]))) > max_words:
            break
        selected.append(text)
        selected_end = verse
    adjusted = format_reference_part(chapter, start, selected_end)
    return adjusted, " ".join(selected).strip().strip('“”" ')


def build_long_form_candidates(
    verses: dict[tuple[str, int, int], str],
    used: set[tuple[str, int, int]],
) -> list[dict[str, Any]]:
    by_testament: dict[str, dict[str, deque[tuple[str, str, str]]]] = {
        "old": defaultdict(deque),
        "new": defaultdict(deque),
    }
    for code, reference_part, context_part, title in LONG_FORM_PASSAGE_CANDIDATES:
        by_testament[BOOKS[code][1]][code].append((reference_part, context_part, title))

    chosen: list[dict[str, Any]] = []
    for testament in ("old", "new"):
        target = TARGET_LONG_FORM // 2
        selected_count = 0
        queues = by_testament[testament]
        while selected_count < target and any(queues.values()):
            progressed = False
            for code in BOOKS:
                if BOOKS[code][1] != testament or not queues[code]:
                    continue
                reference_part, context_part, title = queues[code].popleft()
                try:
                    adjusted_reference, text = fit_long_excerpt(verses, code, reference_part)
                except KeyError:
                    continue
                tokens = reference_tokens(code, adjusted_reference)
                if tokens & used:
                    continue
                theme_key, spec = select_theme(f"{title} {text}") or (
                    "worship",
                    GENERAL_EXTRA_THEMES["worship"],
                )
                candidate = base_candidate(
                    code=code,
                    reference_part=adjusted_reference,
                    context_part=context_part,
                    verse_text=text,
                    title=title,
                    theme_key=theme_key,
                    theme_label=spec["label"],
                    application=spec["application"],
                    segment="long_form_anchor",
                    recommended_formats=["long"],
                    source_text_scope=("verse_range" if "-" in adjusted_reference else "verse"),
                )
                candidate["audience"] = (
                    "pessoas que desejam compreender uma passagem em profundidade"
                )
                candidate["emotional_intent"] = (
                    "investigar contexto, escolhas e consequências sem simplificar o texto"
                )
                candidate["narration_tone"] = "cinematográfico, bíblico, analítico e pastoral"
                chosen.append(candidate)
                used.update(tokens)
                selected_count += 1
                progressed = True
                if selected_count == target:
                    break
            if not progressed:
                break
        if selected_count != target:
            raise RuntimeError(
                f"Somente {selected_count} âncoras longas válidas no testamento {testament}."
            )
    return chosen


def build_general_candidate(
    verses: dict[tuple[str, int, int], str],
    code: str,
    reference_part: str,
) -> dict[str, Any]:
    text = extract_text(verses, code, reference_part)
    theme_key, spec = select_theme(text) or ("worship", GENERAL_EXTRA_THEMES["worship"])
    book = BOOKS[code][0]
    title = f"{spec['label']} em {book}"
    return base_candidate(
        code=code,
        reference_part=reference_part,
        context_part=reference_part,
        verse_text=text,
        title=title,
        theme_key=theme_key,
        theme_label=spec["label"],
        application=spec["application"],
        segment="general",
        recommended_formats=["short", "long"],
        source_text_scope="verse_range" if "-" in reference_part else "verse",
    )


def thematic_fallbacks(
    verses: dict[tuple[str, int, int], str],
    used: set[tuple[str, int, int]],
) -> dict[str, deque[str]]:
    scored_by_book: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (code, chapter, verse), text in verses.items():
        if (code, chapter, verse) in used or not 8 <= len(words(text)) <= 38:
            continue
        selected_theme = select_theme(text, allow_fallback=False)
        if selected_theme is None:
            continue
        _, spec = selected_theme
        lowered = plain(text)
        score = sum(2 for keyword in spec["keywords"] if keyword_matches(lowered, keyword))
        if text.strip().endswith((".", "!", "?", "”")):
            score += 2
        scored_by_book[code].append((score, f"{chapter}:{verse}"))
    return {
        code: deque(
            reference for _, reference in sorted(items, key=lambda item: (-item[0], item[1]))
        )
        for code, items in scored_by_book.items()
    }


def build_general_candidates(
    verses: dict[tuple[str, int, int], str],
    used: set[tuple[str, int, int]],
) -> list[dict[str, Any]]:
    seed_queues = {
        code: deque(reference_list.split())
        for code, reference_list in GENERAL_REFERENCE_SEEDS.items()
    }
    fallback_queues = thematic_fallbacks(verses, used)
    chosen: list[dict[str, Any]] = []

    def consume(queues: dict[str, deque[str]]) -> None:
        while len(chosen) < TARGET_GENERAL and any(queues.values()):
            progressed = False
            for code in BOOKS:
                if not queues.get(code):
                    continue
                reference_part = queues[code].popleft()
                try:
                    tokens = reference_tokens(code, reference_part)
                    text = extract_text(verses, code, reference_part)
                except (KeyError, ValueError):
                    continue
                if tokens & used or not 6 <= len(words(text)) <= 80:
                    continue
                chosen.append(build_general_candidate(verses, code, reference_part))
                used.update(tokens)
                progressed = True
                if len(chosen) == TARGET_GENERAL:
                    break
            if not progressed:
                break

    consume(seed_queues)
    if len(chosen) < TARGET_GENERAL:
        consume(fallback_queues)
    if len(chosen) != TARGET_GENERAL:
        raise RuntimeError(f"Esperadas {TARGET_GENERAL} passagens gerais; obtidas {len(chosen)}.")
    return chosen


def validate_candidates(candidates: list[dict[str, Any]]) -> None:
    expected = TARGET_COMPACT + TARGET_GENERAL + TARGET_LONG_FORM
    if len(candidates) != expected:
        raise RuntimeError(f"Esperadas {expected} passagens; obtidas {len(candidates)}.")
    ids = [candidate["candidate_id"] for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise RuntimeError("O banco gerado possui candidate_id duplicado.")
    occupied: set[tuple[str, int, int]] = set()
    by_book = {book: code for code, (book, _, _) in BOOKS.items()}
    for candidate in candidates:
        code = by_book[candidate["book"]]
        reference_part = candidate["reference"][len(candidate["book"]) + 1 :]
        tokens = reference_tokens(code, reference_part)
        if tokens & occupied:
            raise RuntimeError(f"Referência sobreposta no banco: {candidate['reference']}")
        occupied.update(tokens)
        if candidate["catalog_segment"] == "compact_jesus":
            hook_words = len(words(candidate["compact_hook"]))
            if not 10 <= hook_words <= 12:
                raise RuntimeError(
                    f"Hook fora do contrato em {candidate['reference']}: {hook_words} palavras."
                )
            if candidate["spoken_by"] != "jesus":
                raise RuntimeError(f"Fala compacta sem spoken_by=jesus: {candidate['reference']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("vpl", type=Path)
    parser.add_argument("usfm_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--exclude-catalog", action="append", type=Path, default=[])
    args = parser.parse_args()

    verses, chapter_max = parse_vpl(args.vpl)
    gospel_files = [
        path
        for path in args.usfm_dir.glob("*porbrbsl.usfm")
        if any(code in path.name for code in ("MAT", "MRK", "LUK", "JHN"))
    ]
    if len(gospel_files) != 4:
        raise RuntimeError("A pasta USFM precisa conter os quatro Evangelhos da BPM.")

    used = existing_reference_tokens(args.exclude_catalog)
    compact = build_compact_candidates(direct_speech_units(args.usfm_dir), used, chapter_max)
    long_form = build_long_form_candidates(verses, used)
    general = build_general_candidates(verses, used)
    candidates = [*compact, *general, *long_form]
    validate_candidates(candidates)

    payload = {
        "schema_version": "1.0",
        "catalog_id": "bpm-curated-multiformat-v2",
        "translation": TRANSLATION,
        "publisher": "eBible.org",
        "source_url": SOURCE_URL,
        "rights_note": RIGHTS,
        "accessed_at": ACCESS_DATE,
        "source_artifact_url": f"{VPL_ARTIFACT_URL} + {USFM_ARTIFACT_URL}",
        "source_artifact_sha256": aggregate_source_hash(args.vpl, gospel_files),
        "source_revision": SOURCE_REVISION,
        "candidates": candidates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    counts = defaultdict(int)
    for candidate in candidates:
        counts[candidate["catalog_segment"]] += 1
    print(
        f"generated {len(candidates)} candidates at {args.output} ({dict(sorted(counts.items()))})"
    )


if __name__ == "__main__":
    main()
