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

O serviço exige uma webcam acessível pelo container para ficar pronto. Testes automatizados usam `SyntheticSource` e não abrem o dispositivo. Para executar os testes sem câmera: `docker compose --env-file .env.example build`, depois `docker run --rm --user 0 -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/app -v "$PWD:/app:ro" -w /app --entrypoint python garage-watch-capture -m pytest -p no:cacheprovider`.

`POST /api/v1/clips` cria job durável e responde imediatamente; o clipe aparece após a janela pós-evento configurada. Clipes/segmentos são AVI/MJPEG neste experimento. Segmentos legados válidos são expurgados na inicialização conforme `SEGMENT_RETENTION_SECONDS`, exceto os protegidos por jobs pendentes. O endpoint local ainda não tem autenticação e não deve ser publicado na LAN ou internet.

Descrição das variáveis obrigatórias e limites: [`docs/configuration.md`](docs/configuration.md). Desenho e limitações: [`docs/architecture.md`](docs/architecture.md).
