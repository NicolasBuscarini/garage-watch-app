# Configuração do protótipo

Toda configuração do serviço é injetada por variáveis de ambiente. `.env.example` é um exemplo sem segredos; copie-o para `.env` e ajuste localmente. Compose falha se faltar uma variável obrigatória.

| Variável | Finalidade |
|---|---|
| `APP_PORT` | Porta do host, vinculada apenas a `127.0.0.1`; o serviço dentro do container escuta em 8080 |
| `APP_UID`, `APP_GID` | Usuário/grupo do host usados pelo processo no container para acessar a pasta persistente |
| `VIDEO_GID` | Grupo com permissão de leitura do dispositivo da webcam no host |
| `HOST_DATA_DIR` | Pasta no SSD do host montada como `/data` no container |
| `CAMERA_DEVICE_PATH` | Dispositivo local de vídeo, por exemplo `/dev/video0`; confirmar acesso/permissão no próprio host |
| `CAMERA_ID` | Identificador estável da câmera no armazenamento e métricas |
| `RECORD_AUDIO` | `false` mantém captura somente de vídeo. `true` é recusado até existir adaptador para o microfone da câmera; não usar o microfone do notebook como substituto |
| `FRAME_WIDTH`, `FRAME_HEIGHT`, `FRAME_RATE` | Formato solicitado ao adaptador OpenCV; dimensões aceitas até 4096×2160 e 1–60 fps; o dispositivo pode negociar valores diferentes |
| `SEGMENT_SECONDS` | Duração-alvo dos segmentos, maior que zero e até 60 segundos |
| `SEGMENT_RETENTION_SECONDS` | Idade máxima (1–3600 s) de segmento não protegido por job pendente. Deve comportar `PRE_EVENT_SECONDS` mais um segmento; o padrão de 15 s reduz arquivos no desenvolvimento |
| `PRE_EVENT_SECONDS`, `POST_EVENT_SECONDS` | Janela do clipe acionado manualmente, soma máxima 600 s; o corte segue limites de segmento/frame |
| `STORAGE_QUOTA_BYTES` | Cota para segmentos, clipes, jobs, manifestos e gaps persistidos |
| `MIN_FREE_BYTES` | Reserva mínima do filesystem host antes de iniciar novo segmento |
| `MAX_PENDING_CLIPS` | Máximo de jobs de clipe simultaneamente pendentes (1–100) |
| `MAX_CLIP_BYTES` | Limite do arquivo temporário produzido para um único clipe; deve ser menor ou igual à cota total |
| `CAPTURE_RETRY_SECONDS` | Intervalo de retry (até 60 s) após falha de abertura/leitura da webcam |

Não use endereços RTSP ou credenciais nesta versão: o adaptador RTSP ainda não existe.

O expurgo ocorre ao finalizar/criar segmentos e na inicialização. Segmentos legados válidos acima da retenção são removidos, temporários e órfãos são tratados na recuperação, e segmentos que intersectam jobs `collecting` permanecem até a montagem ou falha do job. Reduzir a retenção também reduz o pré-evento disponível antes de um novo pedido de clipe.

Os segmentos AVI/MJPEG atuais não carregam áudio. O host observado expõe apenas o microfone analógico do notebook, que permanece deliberadamente fora do container para não gravar som ambiente. Quando houver câmera com microfone, a captura exigirá adaptador de áudio, dispositivo/permissões explícitos, mux de mídia, limites de disco e testes de sincronização antes de `RECORD_AUDIO=true` ser suportado.
