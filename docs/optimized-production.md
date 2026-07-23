# Produção otimizada

## Objetivo

Conduzir uma produção completa com contexto compacto, escrita serializada, até dois workers de
imagem em paralelo e retomada segura por checkpoint.

## Fonte de verdade

Antes de criar, retomar ou renderizar:

1. leia `AGENTS.md` e o `README.md`;
2. para projeto existente, leia `project.json`;
3. leia briefing, fontes, decisões, roteiro, revisão bíblica e manifestos existentes;
4. execute `uv run pipeline-videos status <id-ou-pasta>`;
5. execute `uv run pipeline-videos run <id-ou-pasta> --dry-run`.

A pasta do projeto é a fonte de verdade. O chat serve apenas como coordenação.

## Escrita e isolamento

O agente principal é o único escritor de estado compartilhado. Cada produção local materializa
`audit/coordinator-job.json` e usa uma sessão isolada `local_video_coordinator`, com raciocínio médio
e `fork_turns=none`.

Especialistas e workers:

- recebem somente o contexto necessário;
- escrevem no staging declarado;
- não alteram `project.json`;
- não importam;
- não aprovam;
- não publicam.

## Ondas

```text
roteiro e revisão
→ áudio, imagens e metadados em lanes independentes
→ importações serializadas
→ legendas e render
→ validação
→ upload, somente quando solicitado
```

Áudio, música, legendas, imports, render e validação são determinísticos.

## Roteiro

Toda locução autoral usa `jesus_first_person_v1`, em Shorts e vídeos longos. Jesus fala em primeira
pessoa; citações literais mantêm aspas, fonte, tradução e referência. Dramatização e elaboração
criativa nunca são apresentadas como palavras documentadas de Jesus.

O filtro `human_authorial_voice_v1` bloqueia:

- comentários sobre o próprio relato;
- análise metalinguística da estrutura;
- frases que valorizam uma ação listando antes o que não aconteceu;
- enumerações de ausências;
- jargão editorial narrado.

Shorts fora do compacto usam um roteirista e validação incorporada de fontes, sem agente revisor.
O compacto sela deterministicamente hook, citação e referência e delega somente o plano visual.

Vídeos longos usam progressão por movimentos, reengajamentos, resolução coerente com o modo e uma
única ação de CTA. Citações bíblicas ficam distribuídas ao longo da narrativa.

## Planejamento visual

Todo projeto usa:

- `biblical-midcentury-gouache-v1`;
- `narration_block_visual_v1`;
- `scroll_stop_concrete_v1`;
- `unrestricted_scene_v1`.

`strategy.visual_scenes` é a autoridade. Os itens cobrem blocos contíguos da narração, preservam a
ordem e reconstroem integralmente cada seção. Cada descrição de cena é concreta, distinta e
classificada como `literal`, `contextual` ou `symbolic`.

A abertura mostra ação já em curso, sujeito ou objeto dominante e tensão, escolha ou contraste
coerente com a primeira frase.

## Imagens

Use no máximo dois workers simultâneos.

Para cada item:

1. reconstrua o prompt exatamente como declarado;
2. faça uma chamada de geração;
3. em `moderation_blocked`, repita o mesmo prompt uma vez;
4. após novo bloqueio, tente uma alternativa equivalente;
5. copie o arquivo ao `staging_path`.

A alternativa preserva identidade, idade aparente, rosto, cabelo, figurino, proporções, silhueta,
estilo, paleta, ambiente e sentido.

O coordenador executa `images import-batch` uma única vez. Não regenere item importado ou aprovado.

## Áudio

O pedido de produzir, criar ou retomar autoriza ElevenLabs com `--allow-external`. Não use serviço
externo em áudio manual, mock ou quando o usuário proibir.

A duração comum usa `preferred_target_v1`. A voz permanece em velocidade natural `1.0`; não use
silêncio, padding, `atempo` ou time-stretch para preencher a meta.

Projetos novos usam:

- narração em `-18 LUFS`;
- música em `-31 LUFS`;
- diferença de `13 LU`;
- true peak limitado antes do `amix`.

## Finalização de roteiro pré-aprovado

### Bancada editorial econômica

Ideação, aprovação de temas e aprovação de roteiros acontecem antes da criação de projetos. Consulte
`approved-scripts workflow-status`, faça a única pergunta indicada e aguarde a confirmação. A ordem
é fixa:

```text
tema sugerido
→ tema aprovado e salvo
→ lote de roteiros confirmado
→ uma escrita compartilhada com preflight de fontes
→ revisão humana dos textos
→ aprovação em massa por hash, sem modelo
→ produção solicitada separadamente
```

`draft-batch-prepare` somente grava a fila. Ele não cria projeto, coordenador, mídia ou chamada de
modelo. Quando o usuário confirmar a escrita, o lote inteiro aceita no máximo um roteirista e
nenhum revisor bíblico separado. O pacote entrega fontes, pontos obrigatórios e limites de
incerteza ao roteirista, que faz a conferência na mesma saída. O pacote já inclui CTA e SEO para
evitar especialistas adicionais.

O preflight da bancada bloqueia abertura em terceira pessoa, ausência de Jesus como eu lírico, hook
que não seja prefixo literal da narração, metalinguagem autoral, CTA com mais de doze palavras,
descrição sem as cinco hashtags contratadas, fonte não conferida, interpretação apresentada como
certeza apesar dos limites do tema ou nota inferior a 8 em hook, clareza ou payoff.

`draft-batch-approve` é uma transição determinística. Depois que o usuário aprovou os textos
exibidos, nenhuma revisão, pesquisa ou correção pode começar: confira o hash agregado e mude o
estado. Na mesma transação lógica, construa um `ApprovedShortScript` idempotente por texto no pool
remoto. Problemas de citação e de limite interpretativo precisam falhar antes de mover roteiro ou
tema, para não deixar aprovação parcial.

A liberação aceita o título exato:

```powershell
uv run pipeline-videos approved-scripts release `
  --title "<título aprovado>" `
  --authorize-external
```

Ela reserva o snapshot, cria um único job `approved_fast_v1` com upload privado e encerra. O
executor não abre roteiro, revisão, SEO nem plano visual; somente imagens, áudio, legendas, render,
validação e upload permanecem.

Não existe preflight de contagem de palavras ou janela de duração para um roteiro aprovado. O valor
de duração salvo é uma estimativa para organização; `approved_script_locked_v1` aceita a duração
natural produzida pela locução e a registra como efetiva. Desvio para menos ou para mais não cria
correção, nova chamada de modelo, nova síntese ou bloqueio. O hash protege o texto integral contra
qualquer tentativa de ajuste por tempo.

Quando `production_profile=approved_fast_v1`, todas as decisões editoriais já foram congeladas por
hash. O job reaproveita roteiro, CTA, fontes, revisão bíblica e blocos visuais sem abrir agentes de
roteiro, revisão ou crescimento. O único trabalho de modelo restante é a geração das imagens, em
até dois lotes paralelos e com contexto compacto. Título, descrição e cinco hashtags já pertencem
ao snapshot: três específicas do tema, `#biblia` e `#jesus`.

Essa lane não possui correção criativa automática: se o plano aprovado não passar pelo preflight,
o job preserva o pacote e falha para revisão, em vez de reescrever a narração aprovada.

## Metadados

Crie exatamente um `youtube_growth_specialist`, com raciocínio médio, quando a fila estiver
pendente. Ele produz somente:

- um título;
- descrição;
- oito a quinze tags;
- comentário fixado.

Não chama gerador de imagens e não altera ativos visuais.

## Fallbacks

Roteiro visual compacto e metadados aceitam no máximo uma correção agregada do modelo por etapa. Se
o preflight continuar inválido, execute o `fallback_command` declarado e prossiga.

Orçamentos de tokens são telemetria. Ao ultrapassar um orçamento, use o fallback determinístico da
etapa e continue até o candidato final.

## Render

O único render é o candidato final:

- resolução completa;
- H.264;
- CRF 12;
- preset `slow`;
- supersampling 3;
- movimento `smooth_pendulum`;
- transições `cinematic_mix` de 0,38 s;
- blur sigma 8.

## Validação

Em `completion_first_v1`, bloqueiam:

- integridade ou duração inválida da mídia;
- erro de fidelidade ou fonte bíblica;
- ativo ausente ou corrompido;
- risco de segurança ou privacidade;
- publicação ou custo externo não autorizado;
- mix de áudio fora do contrato.

SEO, estética, contagem ideal de palavras e pausas curtas geram avisos, sem iniciar loops ilimitados.

## Auditoria

Ao concluir, atualize de forma idempotente:

- `audit/production-metrics.json`;
- seção automática de `audit/report.md`;
- telemetria de tokens.

Preserve qualquer análise manual já existente.
