# Publicação no YouTube

## Escopo

O pipeline separa:

- geração de metadados;
- upload privado ou não listado;
- publicação pública;
- agendamento.

No fluxo padrão, o especialista de crescimento gera somente título, descrição, tags e comentário
fixado. O perfil `biblical_cutout_explainer_7m_v1` é a exceção: o pacote do roteiro materializa os
metadados e produz localmente `youtube/thumbnail.png` e `youtube/thumbnail.jpg`. Gerar esses
arquivos não autoriza enviá-los.

## Segurança

Nunca envie, publique ou agende sem pedido explícito. Nunca leia, imprima ou versione tokens OAuth.
O primeiro upload aceita apenas `private` ou `unlisted`.

Upload privado automatizado:

- exige `render-review`;
- exige validação técnica concluída;
- usa `--automated-review`;
- não registra aprovação humana;
- termina após o envio privado.

Publicação pública ou agendada continua sendo uma ação separada.

## Preflight

O preflight verifica:

- render final existente e compatível com o hash aprovado;
- metadados e descrição compatíveis com o hash aprovado;
- validação final concluída;
- arquivo SRT quando legendas estiverem habilitadas;
- visibilidade inicial segura;
- identidade do projeto usada na confirmação.

Projetos demo ou com imagens fixture não podem ser enviados.

## Metadados

O título segue `Enfoque concreto | Passagem`. A passagem aparece uma vez, depois da barra. A
descrição começa diretamente pelo tema, usa texto único e não fala do próprio formato. Vídeos longos
incluem uma timeline iniciada por `00:00 - Abertura`.

`metadata.topics` contém de oito a quinze tags relevantes, distintas e sem `#`. O comentário fixado
faz uma pergunta específica ao tema ou passagem.

## Upload

```powershell
uv run pipeline-videos youtube upload <id-ou-pasta> `
  --allow-external `
  --confirm-upload <id-do-projeto>
```

Para um candidato privado automatizado:

```powershell
uv run pipeline-videos youtube upload <id-ou-pasta> `
  --allow-external `
  --confirm-upload <id-do-projeto> `
  --automated-review
```

O upload é idempotente para o mesmo render. Um render diferente exige `--force-new-upload`, que cria
um novo vídeo e arquiva localmente o registro anterior.

## Publicação

Publicação imediata:

```powershell
uv run pipeline-videos youtube publish <id-ou-pasta> `
  --allow-external `
  --confirm-video-id <video-id>
```

Agendamento exige data, hora e fuso explícitos. Um job remoto só pode agendar quando a requisição
original já trouxer `publication_mode=scheduled` e `publish_at`.

## Categorias

- vídeos longos: Educação (`27`);
- Shorts: Pessoas e blogs (`22`).

## Recuperação

O registro em `youtube/publication.json` guarda hashes e IDs de cada etapa. Em uma retomada, o
pipeline reutiliza o vídeo já enviado, atualiza metadados quando necessário e reenvia legendas
somente quando o hash mudou.
