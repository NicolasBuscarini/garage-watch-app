# Instruções do projeto garage-watch

## Autoridade e escopo
- Este repositório é um protótipo local da tarefa T00a. A autoridade compartilhada de produto e arquitetura está em `../../docs/` do repositório de contexto.
- T00 e T01 continuam incompletas; este protótipo não as conclui nem desbloqueia T02.
- Implementar somente captura de desenvolvimento por webcam/fonte sintética, segmentação, buffer circular, clipes por gatilho manual e arquivo em disco local. Não adicionar IA, app móvel, acesso remoto, automação física ou NAS real nesta tarefa.
- Não alegar homologação de RTSP, câmera, Wi-Fi, NAS, desempenho 24/7 ou segurança de produção. Interfaces locais devem manter a troca futura da fonte e do arquivo possível sem transportar tipos de fornecedor ao domínio.

## Engenharia
- Manter um serviço Python modular, configuração somente por variáveis de ambiente injetadas pelo Docker Compose e dependências/imagens fixadas.
- Não versionar `.env`, credenciais, mídia pessoal ou artefatos. Usar somente mídia sintética nos testes automatizados.
- Limitar filas e disco; nunca descartar segmentos referenciados ou clipes pendentes silenciosamente. Persistir identidade, UTC, lacunas e checksum; reconexão cria novo `stream_epoch`; recuperar parciais no reinício.
- `SEGMENT_RETENTION_SECONDS` expurga segmentos não protegidos, inclusive legados válidos no startup. O valor deve comportar a janela pré-evento e um segmento; não apagar mídia referenciada por job `collecting`.
- Testes devem cobrir fluxo real interno e fronteiras com fonte sintética contratual, incluindo falhas de cota e recuperação. Separar evidência automatizada de teste em equipamento físico.
- Atualizar este README e `docs/` locais conforme comportamento/configuração; mudanças de arquitetura exigem registrar alinhamento no contexto compartilhado.

## Verificações
- Executar `docker compose config` (quando disponível), testes e lint/type checks configurados no projeto. Não instalar dependências globalmente nem exigir webcam para os testes.
