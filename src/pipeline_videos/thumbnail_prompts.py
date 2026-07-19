# ruff: noqa: E501


LONG_FORM_CINEMATIC_THUMBNAIL_PROMPT_V1 = """Crie uma imagem cinematográfica para thumbnail do YouTube no formato horizontal 16:9, baseada exclusivamente no seguinte tema bíblico:

TEMA DO VÍDEO:
[TEMA DO VÍDEO]

Interprete o tema e escolha automaticamente o personagem, a emoção, o gesto, o cenário e os elementos visuais que melhor representem a mensagem central.

A imagem deve apresentar um personagem principal do período bíblico, com aparência do Oriente Médio antigo, pele oliva ou morena, cabelos escuros, traços expressivos e roupas historicamente inspiradas em tecidos naturais, como túnica de linho, manto de lã, faixa de tecido e sandálias.

Quando o tema mencionar um personagem bíblico específico, represente esse personagem de maneira reconhecível e coerente com a narrativa. Quando o tema for uma reflexão abstrata, crie um personagem bíblico anônimo que simbolize emocionalmente a mensagem.

ESTILO VISUAL:

– Animação 3D cinematográfica premium de longa-metragem familiar;
– Personagem estilizado, porém detalhado e emocionalmente convincente;
– Olhos expressivos, rosto humano e proporções elegantes;
– Materiais ricos, tecidos com textura e iluminação realista;
– Qualidade visual de filme de animação;
– Sem copiar personagens existentes;
– Atmosfera épica, contemplativa, espiritual e emocional;
– Sensação de paz, esperança, reverência e presença divina.

CENÁRIO:

Crie uma paisagem bíblica ampla e majestosa, escolhida de acordo com o tema. Ela pode incluir vales da Judeia, montanhas distantes, colinas rochosas, caminhos antigos, oliveiras, ciprestes, campos secos, rios, lagos, aldeias de pedra, muralhas antigas ou cidades bíblicas ao longe.

A paisagem deve transmitir profundidade e grandiosidade, com várias camadas de montanhas e terreno desaparecendo suavemente no horizonte. Inclua atmosfera, névoa luminosa e perspectiva aérea para aumentar a sensação de escala.

ILUMINAÇÃO E CORES:

Use iluminação cinematográfica de amanhecer ou pôr do sol, com luz dourada atravessando as nuvens e iluminando o personagem pelas laterais.

Paleta principal:

– Dourado e âmbar na luz;
– Terracota e vinho nos mantos;
– Bege, creme e areia nas túnicas;
– Verde-oliva e sálvia na vegetação;
– Azul suave e violeta nas sombras;
– Tons quentes equilibrados com profundidade atmosférica fria.

COMPOSIÇÃO:

Posicione o personagem preferencialmente no lado esquerdo ou direito da imagem, ocupando aproximadamente 30% a 40% do quadro. Mostre o personagem do peito para cima, da cintura para cima ou em plano americano, grande o bastante para sua expressão ser compreendida em uma tela pequena.

O personagem deve olhar para a paisagem, para o horizonte, para a luz ou para algum elemento relacionado ao conflito do vídeo.

Deixe uma área visualmente limpa no lado oposto ao personagem para a futura inserção de texto na thumbnail.

Utilize composição cinematográfica, regra dos terços, linhas naturais conduzindo o olhar, silhueta clara e separação nítida entre personagem e fundo.

A imagem deve comunicar a ideia principal em menos de um segundo, equilibrando:

– Um personagem emocionalmente forte;
– Um gesto simples e simbólico;
– Uma paisagem épica;
– Uma iluminação espiritual;
– Um único conceito visual central.

Evite excesso de personagens, objetos pequenos, cenário confuso, elementos modernos, arquitetura contemporânea, texto, letras, logotipos, marcas-d’água, violência gráfica, mãos deformadas, membros extras ou aparência excessivamente infantil.

Resultado final: thumbnail bíblica épica, pacífica e emocional, com animação 3D cinematográfica premium, personagem expressivo, paisagem grandiosa, iluminação dourada e alta clareza visual para celular."""


def build_long_form_thumbnail_prompt(theme: str) -> str:
    normalized_theme = theme.strip()
    if not normalized_theme:
        raise ValueError("O tema do vídeo longo não pode ficar vazio.")
    return LONG_FORM_CINEMATIC_THUMBNAIL_PROMPT_V1.replace(
        "[TEMA DO VÍDEO]",
        normalized_theme,
        1,
    )
