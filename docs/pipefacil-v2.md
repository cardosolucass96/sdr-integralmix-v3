# Integracao Pipefacil v2

Webhooks com `execution.protocolVersion=2` usam somente `/api/v1/ai-agent` para
consultar o negocio, historico, atualizar campos e etapa, responder ao lead e pausar a IA.
O destino e o canal das mensagens sao definidos pelo servidor. Execucao malformada nao
reverte para v1. Eventos sem `execution` mantem o contrato legado durante a preparacao.

Cada parte de resposta tem uma chave estavel por execucao. Antes de repetir um efeito,
a integracao consulta o recibo: `APPLIED` e reaproveitado; `RESERVED`/`UNCERTAIN` bloqueiam
novo envio e exigem conciliacao. Erros de transporte nunca provocam envio pela API antiga.
O contexto v2 e salvo no outbox de encaminhamento, sem credenciais. Tentativas posteriores
usam a mesma execucao, nunca outra geracao. Expiracao ou revogacao impede novos efeitos;
pedidos pendentes nessas condicoes precisam de intervencao operacional.

## Aviso ao supervisor

`PIPEFACIL_SUPERVISOR_API_KEY` deve ser uma chave separada de um membro autorizado do
mesmo workspace, com acesso ao canal e permissao de envio. A chave da Raquel nao serve
para essa finalidade. A aplicacao valida a execucao antes do aviso e usa o destinatario
fixado na distribuicao regional, sem aceitar telefone fornecido pelo modelo.

O endpoint publico de notificacao ao supervisor nao fornece recibos v2; permanece sujeito
as limitacoes de entrega da integracao antiga em falhas ambiguas de rede. Conferir o
historico antes de reprocessar um aviso cujo resultado seja desconhecido. Nao interpretar
um timeout como prova de que a mensagem nao foi enviada.

`PIPEFACIL_HANDOFF_PIPELINES` recebe um snapshot JSON dos funis com `id` e `stages` contendo
`id`, `name`, `order`, `isWon`, `isLost`. Atualize esse snapshot ao alterar o funil comercial.
Sem etapa reconhecida, o encaminhamento falha sem escolher outra etapa arbitrariamente.

A ordem do encaminhamento e: campos/etapa, aviso ao supervisor, confirmacao ao lead,
`handoff` v2. A ultima acao pausa a IA sem alterar o responsavel humano. A conclusao local
so e marcada depois de confirmar os recibos da mensagem final e da pausa.

## Publicacao e conversao

1. Rodar `make quality` e revisar o diff.
2. Configurar a chave separada e o snapshot dos funis no recurso Coolify existente.
3. Publicar o codigo mantendo compatibilidade com eventos v1, verificar `/ready`.
4. Validar um evento v2 controlado, incluindo repeticao sem duplicar mensagem.
5. Somente entao confirmar compatibilidade v2 no cadastro externo do Pipefacil.
6. Preparar a conversao, aguardar atendimentos em andamento e converter o workspace.
7. Validar com IA pausada antes de reativar os negocios aprovados.

Nao retornar ao codigo exclusivamente v1 depois da conversao. Para conter uma falha,
pausar a IA no CRM, preservar recibos e corrigir a integracao v2.
