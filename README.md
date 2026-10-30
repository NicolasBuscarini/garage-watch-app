# garage-watch

Protótipo local T00a de captura para Garagem IA. O protótipo existe para validar segmentos, buffer circular e geração de clipes com uma fonte de desenvolvimento antes da compra da câmera.

## Estado

- Dispositivos locais `/dev/video0` e `/dev/video1` encontrados; `/dev/video0` foi aberto e entregou frames no health local, sem homologação de imagem ou desempenho.
- Fonte sintética: destinada a testes locais, não representa câmera real.
- Gravação no SSD local com quota/checksum; nenhuma integração NAS.
- `SEGMENT_RETENTION_SECONDS=15` remove segmentos não protegidos após 15 s; reduza `PRE_EVENT_SECONDS` se escolher uma retenção menor.
- `RECORD_AUDIO=false`: segmentos AVI/MJPEG são somente vídeo. O protótipo não acessa o microfone do notebook; áudio aguarda câmera com microfone e adaptador próprio.
- T00/T01 continuam pendentes e T02 permanece bloqueada.

O escopo e os critérios compartilhados estão em `../../docs/governanca/10_BACKLOG.md`, tarefa T00a, e nas decisões/arquitetura em `../../docs/`.

## Executar localmente

1. Copie `.env.example` para `.env`; ajuste `APP_UID`/`APP_GID` com os IDs do usuário que executará o container, `VIDEO_GID` para o grupo que acessa a webcam e `CAMERA_DEVICE_PATH` para o dispositivo local. Não compartilhe `.env`.
2. Crie a pasta de dados no host com seu usuário (`mkdir -p data`) e ajuste `HOST_DATA_DIR` se quiser outro local no SSD.
3. Execute `docker compose up --build`.
4. A API fica disponível somente em `http://127.0.0.1:${APP_PORT}`. `/health/live`, `/health/ready`, `/api/v1/status`, `/api/v1/clips`, `POST /api/v1/clips` e `/metrics` permitem verificar a captura e solicitar um clipe.

O serviço aceita `CAMERA_SOURCE=webcam` (padrão), `synthetic` (padrão visual local, sem dispositivo) ou `rtsp` somente no perfil opt-in `rtsp-integration`. `compose.simulated.yaml` remove o mapeamento de dispositivo para executar o perfil sintético com segurança. Testes automatizados também usam `SyntheticSource`. Para executar os testes sem câmera: `docker compose --env-file .env.example build`, depois `docker run --rm --user 0 -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/app -v "$PWD:/app:ro" -w /app --entrypoint python garage-watch-capture -m pytest -p no:cacheprovider`.

`POST /api/v1/clips` cria job durável e responde imediatamente; o clipe aparece após a janela pós-evento configurada. Clipes/segmentos são AVI/MJPEG neste experimento. Segmentos legados válidos são expurgados na inicialização conforme `SEGMENT_RETENTION_SECONDS`, exceto os protegidos por jobs pendentes. O endpoint local ainda não tem autenticação e não deve ser publicado na LAN ou internet.

## Laboratório de captura

### Perfil sem câmera (T00-S)

Para executar a fonte sintética sem exigir nem montar `/dev/video*`, use a sobreposição `compose.simulated.yaml`. Direcione os relatórios e mídia de teste para uma pasta descartável separada de `data/` (e ajuste UID/GID se necessário):

```bash
mkdir -p /tmp/garage-watch-simulation
HOST_DATA_DIR=/tmp/garage-watch-simulation APP_UID="$(id -u)" APP_GID="$(id -g)" docker compose --env-file .env.example -f compose.yaml -f compose.simulated.yaml run --rm capture python -m app.capture_lab --duration-seconds 60 --source synthetic
```

O Compose exige ainda variáveis de configuração de captura no `.env.example`, mas a fonte sintética não abre a câmera. A sobreposição remove o mapeamento do dispositivo; não altera o adaptador, protocolo nem destino de arquivo do produto. Remova manualmente apenas o diretório descartável acima depois de conferir o conteúdo. Não use `data/` existente como destino de ensaio.

O laboratório executa a mesma captura do serviço por uma duração limitada e produz um relatório JSON local com formato solicitado/observado, frames, FPS observado, lacunas, reconexões, segmentos, descarte, CPU do processo, pico de RSS e bytes locais. Ele não envia mídia, não registra caminho/dispositivo da câmera e não inclui URL ou credenciais.

Use primeiro a fonte contratual, sem webcam:

```bash
docker compose run --rm capture python -m app.capture_lab --duration-seconds 60 --source synthetic
```

Para exercitar reconexão determinística no ensaio sintético:

```bash
docker compose run --rm capture python -m app.capture_lab --duration-seconds 60 --source synthetic --synthetic-fail-after-frames 10 --synthetic-failures 2
```

Depois de conferir permissões e enquadramento, a webcam local pode ser medida com `--source camera`. Pare o serviço `capture` antes de iniciar o laboratório: o diretório de dados aceita somente uma captura por vez para proteger segmentos, clipes e temporários. O processo retorna código diferente de zero se não receber frames. Relatórios concluídos ficam em `data/reports/`; no máximo os 20 mais recentes são preservados e, se a cota não comportar o relatório, ele ainda é emitido no stdout com `report_file: null` sem apagar relatórios existentes.

O ensaio sintético não homologa câmera, RTSP, Wi-Fi, NAS, temperatura ou operação contínua. O ensaio com webcam confirma somente as condições observadas durante aquela execução local.

### Evidência integrada real (29/10/2026)

Neolink → `garage-watch-platform` funcionou no main da E1: capture ready, segmentos H.264 gravados no spool, e playlist/segmento autenticados HTTP 200. `ffprobe` do segmento: H.264 High, 2304×1296, yuv420p, 15 fps. Isso não valida continuidade no browser nem latência. A tentativa sequencial Neolink → T00a abriu e recebeu frames, mas OpenCV/FFmpeg disparou timeouts de leitura de 5 s e reconectou repetidamente (78 frames, 7 gaps, 6 reconexões na janela observada); não é aceite de estabilidade. O serviço T00a foi parado e o stream platform foi restaurado como único consumidor. Não houve teste concorrente, e substream continua fora do ensaio.

### Perfil experimental com a E1 via Neolink

O perfil opcional `rtsp-integration` lê o main stream RTSP local por OpenCV/FFmpeg, reduz os frames ao formato configurado e mantém o armazenamento T00a AVI/MJPEG. É um caminho de laboratório separado do serviço webcam padrão, não uma declaração de suporte a RTSP genérico. O Neolink é dependência de laboratório reverse-engineered (AGPL-3.0-or-later); os repositórios continuam independentes. A URI não aceita usuário/senha, query ou fragmento e não é registrada. O consumidor e o painel permanecem publicados somente em loopback.

Para usar o adaptador, crie uma única vez a rede Docker interna compartilhada `garage-watch-rtsp-lab` (`docker network create --internal garage-watch-rtsp-lab`), configure e inicie primeiro o Neolink conforme `../neolink/docs/garage-watch-e1-validation.md`, depois pare o serviço `capture` webcam e inicie `capture-rtsp` com `CAMERA_RTSP_URL=rtsp://neolink:8554/garage/mainStream`. Não execute `capture` e `capture-rtsp` simultaneamente: compartilham o volume local e o lock exclusivo. A porta HTTP continua loopback (`RTSP_APP_PORT`, padrão 8083). O `garage-watch-platform` pode usar a mesma rede com `CAPTURE_PROFILE=ffmpeg-rtsp` e a mesma URI; execute apenas um consumidor por vez até a capacidade de sessões concorrentes do bridge/câmera ser validada. O substream não está confirmado; use somente main. Este perfil pode consumir mais CPU ao decodificar e recodificar para MJPEG.

Descrição das variáveis obrigatórias e limites: [`docs/configuration.md`](docs/configuration.md). Desenho e limitações: [`docs/architecture.md`](docs/architecture.md).
