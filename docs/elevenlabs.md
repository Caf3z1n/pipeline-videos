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

- default econômico: `eleven_flash_v2_5`;
- alternativa de qualidade long-form: `eleven_multilingual_v2`;
- output seguro para Free: `mp3_44100_128`;
- language code: `pt` quando suportado pelo modelo;
- voice ID configurável agora e compatível com clone autorizado no futuro.

Os [modelos atuais](https://elevenlabs.io/docs/overview/models) e limites efetivos podem mudar. A
API oficial `GET /v1/models` informa capacidade TTS, limites por usuário Free/pago e custo relativo.

## Segurança, custo e retry

- chave lida apenas de `ELEVENLABS_API_KEY`;
- voice ID de config do projeto ou `ELEVENLABS_VOICE_ID`; o padrão recomendado para novas
  produções é `oy1Z2MNJzUkfXcLIq8CS`;
- chamada bloqueada sem `--allow-external`;
- headers `request-id`, `character-cost`, `x-trace-id` e concorrência ficam nos metadados;
- áudio base64 e chave nunca ficam em logs;
- texto com caractere Unicode de substituição ou `?` no meio de palavra é bloqueado antes da API;
- retry com jitter somente para 429, 500 e 503;
- 400/401/402/403/404/422 falham imediatamente com mensagem sanitizada.

Consulte [Errors](https://elevenlabs.io/docs/eleven-api/resources/errors) e
[API authentication](https://elevenlabs.io/docs/api-reference/authentication).

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
