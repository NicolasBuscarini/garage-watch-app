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
| `FRAME_WIDTH`, `FRAME_HEIGHT`, `FRAME_RATE` | Formato solicitado ao adaptador OpenCV; dimensões aceitas até 4096×2160 e 1–60 fps; o dispositivo pode negociar valores diferentes |
| `SEGMENT_SECONDS` | Duração-alvo dos segmentos, maior que zero e até 60 segundos |
| `BUFFER_SECONDS` | Janela de segmentos mantida circularmente (1–3600 s), exceto segmentos fixados por jobs pendentes |
| `PRE_EVENT_SECONDS`, `POST_EVENT_SECONDS` | Janela do clipe acionado manualmente, soma máxima 600 s; o corte segue limites de segmento/frame |
| `STORAGE_QUOTA_BYTES` | Cota para segmentos, clipes, jobs, manifestos e gaps persistidos |
| `MIN_FREE_BYTES` | Reserva mínima do filesystem host antes de iniciar novo segmento |
| `MAX_PENDING_CLIPS` | Máximo de jobs de clipe simultaneamente pendentes (1–100) |
| `MAX_CLIP_BYTES` | Limite do arquivo temporário produzido para um único clipe; deve ser menor ou igual à cota total |
| `CAPTURE_RETRY_SECONDS` | Intervalo de retry (até 60 s) após falha de abertura/leitura da webcam |

Não use endereços RTSP ou credenciais nesta versão: o adaptador RTSP ainda não existe.
