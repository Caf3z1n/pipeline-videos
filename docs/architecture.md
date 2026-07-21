# Arquitetura e contratos

## Decisão principal

O núcleo usa Python 3.12, Pydantic, Typer e FFmpeg. Cada vídeo é uma unidade autocontida; não há
banco de dados global. Essa escolha reduz infraestrutura, torna o estado legível e permite adicionar
uma API/UI depois sem reescrever regras de negócio.

A única coordenação global é `content-registry.json`, mantido em uma branch de um repositório Git
bare compartilhado. Ele indexa reservas e usos de passagens/temas, mas não substitui as pastas dos
projetos como fonte de verdade. Local e VPS fazem push fast-forward: em concorrência, o push
rejeitado recarrega o JSON e repete a seleção antes de criar o projeto. Indisponibilidade bloqueia a
seleção em vez de usar um cache local possivelmente divergente.

O registro distingue passagem principal de referências complementares e mantém ciclos separados
para `short` e `landscape`. Somente outra passagem principal do mesmo formato participa do bloqueio
de repetição; citações de apoio de vídeos longos podem reaparecer e continuam disponíveis como tema
principal de Short. O catálogo base é ampliado pelo banco curado BPM antes da seleção. Ao completar
todo o conjunto, um novo ciclo começa pela menor frequência de uso, preservando reservas ativas.
O banco `narrative_story` é uma camada opt-in: nomes e aliases filtram a história principal e expandem
seu pacote de fontes verificadas, sem colocar fragmentos narrativos na rotação automática comum ou
no perfil compacto.

```mermaid
flowchart LR
    B["Briefing e catálogo verificado"] --> S["Locução compacta determinística"]
    S --> R["Revisão bíblica determinística"]
    R --> C["Planejamento visual por blocos"]
    C --> I["Até 2 image workers"]
    C --> A["Lane principal: áudio, legendas e música"]
    C --> G["Rascunho compacto de metadados"]
    A --> L["Alinhamento e legendas"]
    I --> V["Base visual em cache"]
    L --> F["Composição final"]
    V --> F
    G --> Y["Pacote YouTube determinístico e avisos SEO"]
    F --> Y
    F --> Q["Guards técnicos, bíblicos e de publicação"]
    Q --> U["Upload privado ou não listado"]
    U --> P["Publicação ou agendamento explícito"]
```

## Estado e idempotência

`project.json` contém um `StageRecord` por etapa:

- `status`: pending, ready, running, waiting_approval, waiting_external, completed, failed, stale;
- `input_hash`: SHA-256 de entradas e configuração;
- `artifacts`: caminhos relativos esperados;
- `attempts`, provider, uso, erro e observação.

Uma etapa é reutilizada somente se o hash coincidir e todos os artefatos declarados existirem. O
flag `--force` ignora o cache, mas adaptadores versionados criam um novo candidato em vez de apagar o
aprovado.

## Aprovações

As aprovações ficam separadas do estado técnico. Um arquivo pode existir e ainda não estar aprovado.
Os checkpoints atuais são briefing, roteiro, revisão bíblica, imagens, metadados e render final.

O roteiro só é aprovado após uma revisão correspondente à mesma versão e sem bloqueio aberto. A
produção do candidato final não transforma automaticamente ativos em aprovados.

No modo `final_candidate_one_shot`, os bloqueios de consistência bíblica continuam valendo, mas as
aprovações humanas intermediárias não impedem `render/final.mp4`. O candidato usa as versões ativas
em qualidade V4 e não registra aprovação humana automaticamente.

## Contratos de conteúdo

- `Briefing`: passagem, tema, modo criativo, postura, política de hook, valor final, público,
  emoção, duração, tradução e limites. Aplicação é condicional ao modo.
- `SourceLibrary`: tipo, escopo da afirmação, fonte, acesso, verificação e direitos.
- `ScriptDocument`: seções narrativas, base bíblica e classificação das afirmações.
- `CreativeContract`: modo, postura, promessa, primeira fala, presença ou ausência de hook,
  resolução, valor de cada seção, componentes críticos e perfil determinístico de ritmo,
  derivados sem chamada externa.
- `BiblicalReview`: findings com severidade, evidência, resolução e aprovação.
- `SceneManifest`: narração, tempo, emoção, ambiente, enquadramento, função e prompt modular.
- `ImageManifest`: prompt/hash, versões, candidato ativo e versão aprovada.
- `AudioManifest`: segmentos, provider, uso e timestamps globais por palavra.
- `CaptionManifest`: blocos, índices cobertos, caminhos e validação.
- `YouTubeMetadata`: rascunho, alternativas, capítulos e visibilidade segura.
- `YouTubeGrowthPackage`: pacote final imutável, vinculado ao hash da fila e construído pelo Python
  a partir do rascunho compacto quando esse modo estiver ativo.
- `CompactVisualPlan` e `CompactGrowthDraft`: handoffs mínimos; o Python reconstrói os contratos
  completos e possui os fallbacks seguros.
- `YouTubeGrowthStrategy`: intenção de busca, palavras-chave, promessa, hipóteses e fontes oficiais.
- `ExperimentMemory`: hipótese, mudança, vídeos, período, métricas e conclusão cautelosa.
- `YouTubePublicationRecord`: ID remoto, canal, visibilidade e hashes dos ativos enviados.

## Render

1. O áudio define a timeline real.
2. Cada cena vira um segmento CFR com `scale`, `crop`, `zoompan` e fades discretos; até dois
   segmentos independentes podem ser processados em paralelo.
3. Os segmentos formam `render/work/review-candidate-base.mp4`, sem legenda e reutilizável.
4. O ASS é aplicado à base junto da narração.
5. O candidato final usa H.264/yuv420p, AAC, faststart, CRF 12, preset `slow` e supersampling 3.
6. FFprobe valida proporção, codecs, faixas, frame rate e duração do único MP4.

O ASS emite um evento por intervalo de palavra, mas mantém o bloco inteiro no mesmo lugar; só a cor
do termo ativo muda. A mudança de cena não interfere na timeline da legenda.

## Fronteiras de extensão

- `images.py`: fixture e handoff Codex; pode receber outro adapter autorizado.
- `audio.py`: mock para testes e ElevenLabs para produção.
- `render.py`: FFmpeg; uma futura implementação pode obedecer aos mesmos manifests.
- `growth.py`: fila, staging e importação serial do especialista de crescimento do YouTube.
- `publication.py`: OAuth, upload retomável e publicação YouTube; consome apenas artefatos aprovados.
- UI/API: deverá orquestrar as mesmas funções, nunca reinterpretar estado por conta própria.

## Handoffs econômicos

Os especialistas recebem contextos derivados e compactos. No perfil compacto, a importação valida o
handoff mínimo e reconstrói o pacote completo contra os arquivos-fonte do projeto:

- `script/shorts-context.json` reúne somente IDs dos blocos, trechos narrados e direção necessária
  para escolher cenas; o modelo não recebe autoridade para reescrever a locução;
- `youtube/growth-context.json` reúne somente promessa, passagem e contexto mínimo de busca;
- `images/jobs/image-worker-N.json` entrega a cada trabalhador somente seus próprios jobs e prompts;
- os JSON Schemas enviados às filas removem somente metadados de apresentação, preservando tipos,
  enums, referências e campos obrigatórios;
- os subagentes partem com `fork_turns=none`, evitando herdar o histórico inteiro da conversa;
- no perfil pintado de Shorts, não há bitmap de thumbnail nem frame zero artificial; o YouTube
  seleciona uma cena. O perfil 3D legado mantém a edição da abertura, e longos geram uma imagem
  horizontal limpa baseada somente no tema central;
- prompts de imagem armazenam o prefixo compartilhado uma vez e apenas o delta de cada cena;
- cada etapa de modelo admite uma correção agregada; persistindo erro, usa fallback determinístico.

## Autopilot retomável

`pipeline-videos autopilot <projeto>` executa todas as etapas determinísticas e serializadas que
estiverem prontas. Quando encontra geração embutida pendente, grava o checkpoint em
`logs/autopilot-state.json`, informa exatamente quais arquivos de staging faltam e termina sem
perder o progresso. Depois do preenchimento pelos trabalhadores, uma nova execução faz uma única
importação em lote e continua até o candidato final ou o próximo bloqueio.

O comando nunca cria duas escritas concorrentes no estado. A geração de imagens continua fora do
processo Python porque a ferramenta embutida pertence à sessão do Codex; áudio, música, legendas,
composição, validação e retomada são automatizados localmente.
