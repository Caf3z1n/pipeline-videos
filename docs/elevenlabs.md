# ElevenLabs

Pesquisa verificada em 13/07/2026 apenas na documentação oficial.

## Endpoint

```http
POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps
Content-Type: application/json
xi-api-key: <segredo local>
```

Referência: [Create speech with timing](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps).

A resposta contém áudio base64, `alignment` e `normalized_alignment` em nível de caractere. O
adapter prefere o alinhamento normalizado e deriva palavras usando o primeiro início e último fim de
cada token. Depois converte o áudio para WAV mono 48 kHz e soma o offset real de cada cena.

## Configuração escolhida

- modelo padrão: `eleven_multilingual_v2` (`Eleven Multilingual v2`);
- output seguro para Free: `mp3_44100_128`;
- language code: `pt` quando suportado pelo modelo;
- `jesus_1`: voz padrão `qfOmqDJTQP75MDYJvxKc`;
- `jesus_2`: variação `rtrpEL7BeMd5aTru4HPq`.
- `matheus`: narração expressiva `36rVQA1AOIPwpA3Hg1tC`.

## Prompting Eleven v3 no perfil cutout

`biblical_cutout_explainer_7m_v1` usa `eleven_v3_strategic_tags_v1` com a voz Matheus e estabilidade
Natural. O pipeline mantém `script/narration.txt` sem marcações e cria `audio/tts-prompt.json` para
a chamada de síntese. Esse prompt:

- preserva todas as palavras e a ordem do roteiro;
- usa poucas tags de voz contextuais: `[curious]`, `[thoughtful]` e `[surprised]`;
- nunca usa tags de cena, música ou efeito ambiental;
- não usa SSML `<break>`, que não é suportado pelo Eleven v3;
- registra o texto enviado e seu hash, enquanto o alinhamento final remove as tags.

Referência oficial:
[Prompting Eleven v3](https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices#prompting-eleven-v3).

Os [modelos atuais](https://elevenlabs.io/docs/overview/models) e limites efetivos podem mudar. A
API oficial `GET /v1/models` informa capacidade TTS, limites por usuário Free/pago e custo relativo.

## Segurança, custo e retry

- chave lida apenas de `ELEVENLABS_API_KEY`;
- voice ID de config do projeto ou `ELEVENLABS_VOICE_ID`; o padrão para novas produções é
  `jesus_1`;
- os presets podem ser substituídos no ambiente por `ELEVENLABS_VOICE_JESUS_1_ID` e
  `ELEVENLABS_VOICE_JESUS_2_ID`; `matheus` usa `ELEVENLABS_VOICE_MATHEUS_ID`;
- `ELEVENLABS_MODEL_ID` pode substituir o modelo padrão;
- chamada bloqueada sem `--allow-external`;
- headers `request-id`, `character-cost`, `x-trace-id` e concorrência ficam nos metadados;
- áudio base64 e chave nunca ficam em logs;
- texto com caractere Unicode de substituição ou `?` no meio de palavra é bloqueado antes da API;
- retry com jitter somente para 429, 500 e 503;
- 400/401/402/403/404/422 falham imediatamente com mensagem sanitizada.

Consulte [Errors](https://elevenlabs.io/docs/eleven-api/resources/errors) e
[API authentication](https://elevenlabs.io/docs/api-reference/authentication).

Selecione uma variação por projeto sem copiar IDs manualmente:

```powershell
uv run pipeline-videos audio configure <projeto> --voice-variant jesus_1
uv run pipeline-videos audio configure <projeto> --voice-variant jesus_2
```

## Plano gratuito e publicação

A [página oficial de preços](https://elevenlabs.io/pricing/api?price.platform=api) indicava, na data
da pesquisa, 10.000 créditos mensais no Free, sem rollover. Ela também apontava restrições para uso
comercial. Antes de monetizar ou publicar, confirme termos, atribuição e licença atuais. O projeto não
ativa assinatura, pay-as-you-go ou cobrança.

## Segmentação

O MVP envia uma requisição por cena. Isso permite corrigir um trecho sem pagar novamente por toda a
narração. Uma evolução deve usar `previous_text`/`next_text` ou request IDs para continuidade de voz,
respeitando a orientação oficial para
[textos longos](https://elevenlabs.io/docs/overview/capabilities/text-to-speech).
