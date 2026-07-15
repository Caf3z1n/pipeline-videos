# Áudio manual com alinhamento local

Este modo permite gerar a voz fora do pipeline, anexá-la na conversa e concluir legendas, cenas e
render sem depender da ElevenLabs.

## Fluxo

Crie o Short:

```powershell
uv run pipeline-videos new-short-audio-manual
```

Depois que roteiro, revisão e cenas estiverem prontos, o pipeline grava `audio/manual-queue.json` e
aguarda. O agente mostra todo o conteúdo de `script/narration.txt`. Gere uma única faixa de voz com
esse texto exato, sem títulos, instruções, música, efeitos, introdução ou encerramento adicional.

Salve o anexo e importe:

```powershell
uv run pipeline-videos audio import-manual <projeto> <arquivo-de-audio>
uv run pipeline-videos autopilot <projeto> --wait-external
```

Não use `--allow-external`. Nenhuma API é chamada.

## Formatos

O recomendado é WAV PCM mono 16-bit/48 kHz. Também são aceitos WAV, MP3, M4A, AAC, FLAC, OGG, Opus
e WebM que o FFmpeg consiga decodificar. O pipeline sempre cria `audio/narration.wav` no padrão
interno mono 16-bit/48 kHz.

## Alinhamento

O roteiro já é conhecido, portanto o pipeline não depende de uma transcrição livre. Um processo
isolado carrega WhisperX 3.8.6 e o modelo Wav2Vec2 português, alinha as palavras ao áudio e devolve
somente timestamps. O ambiente principal não recebe PyTorch ou as dependências pesadas do
alinhador.

Na primeira execução, `uv` baixa o ambiente e o modelo público para o cache do computador. As
execuções seguintes reutilizam o cache e podem funcionar sem internet. O computador atual não tem
CUDA/NVIDIA, então o alinhamento usa CPU.

O importador exige cobertura integral, timestamps positivos e ordem idêntica ao roteiro. Se o áudio
omitir, repetir ou reformular uma palavra, a etapa falha. Isso protege as legendas e as trocas de
cena contra sincronização simulada.
