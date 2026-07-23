# Roadmap incremental

## Marco 1 — concluído

- projetos autocontidos e retomáveis;
- CLI, schemas, hashes, versões, aprovações e dry-run;
- revisão bíblica e fontes;
- cenas e prompts modulares;
- fila Codex/fixtures de imagem;
- mock para testes e ElevenLabs para produção;
- timestamps por palavra, ASS/SRT/VTT;
- render final/prévia e validação FFprobe;
- metadados e memória de experimentos.
- organização datada em vídeos longos/Shorts e especialista de crescimento com staging.

## Marco 2 — produção assistida

- primeira voz ElevenLabs autorizada e testes A/B de modelo/configuração;
- imagens reais com fichas e âncoras de personagem;
- regeneração editorial por trecho com contexto TTS adjacente;
- fonte OFL empacotada e licenciada para render portátil;
- música/efeitos apenas com licença registrada no manifesto;
- perfis 16:9, shorts e cortes de divulgação.

## Marco 3 — YouTube com aprovação — concluído

- OAuth e secrets fora dos projetos;
- upload inicialmente privado ou não listado;
- legenda e agendamento como etapas separadas;
- resumo final de custo e checklist antes de qualquer publicação;
- idempotency key para impedir upload duplicado.

## Marco 4 — aprendizado orientado por dados

- importar impressões, CTR, retenção, duração média, watch time, inscritos e fontes de tráfego;
- relacionar métricas aos manifests editoriais e visuais;
- registrar hipótese, mudança, período, vídeos, resultado e limitações;
- propor experimentos, não conclusões causais exageradas;
- comparar temas, abertura, duração, ritmo, título, estilo e frequência;
- analisar exemplos públicos sem copiar conteúdo ou identidade.

`analytics/experiments.json` já usa um schema compatível com essa evolução.
