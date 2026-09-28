# Handoff de supervisores Integral Mix

O fluxo de qualificacao e transferencia roda no SDR. O webhook do Pipefacil entrega a mensagem
ao agente. `interpret-turn` retorna fatos incrementais estruturados; `update-qualification`
mantém fatos e fontes, calcula o campo pendente e só cria um pedido de handoff quando as regras
locais confirmam que os dados obrigatórios estão completos. O grafo segue para
`supervisor-handoff-action`, que chama a camada application por uma dependência de execução. A
application reserva um supervisor no banco local, grava o outbox e processa a sincronização do
lead no Pipefacil e as notificações ao supervisor e ao lead pelo canal recebido no webhook.

## Atendimento conversacional

O prompt `integral-mix/responder-v2` preserva a persona da Raquel: português brasileiro, tom
acolhedor, profissional e objetivo, uma pergunta principal por vez, sem repetir dados já informados.
Blocos separados por linha em branco podem ser enviados como bolhas distintas no WhatsApp.

A interpretação estruturada valida o rótulo de contato enviado pelo Pipefacil e aproveita o nome
quando ele identifica claramente uma pessoa. Rótulos de empresa, frases ou identificadores deixam
o campo pendente para a Raquel perguntar. Se houver nome válido, ela segue ao próximo campo sem
repeti-lo. O rótulo bruto fica no contexto da execução e não é salvo no estado da conversa. O
restante da qualificação segue a ordem do workflow original para criadores e revendedores. Fatos
confirmados e corrigidos são mesclados no estado por código e sua fonte é registrada; o responder
recebe `known_facts` e `pending_goal`, sem decidir a completude nem montar o handoff. Perguntas sobre
preço, desconto, pagamento ou frete recebem uma resposta sem valores e depois a conversa retoma o
próximo campo pendente. Financeiro, vagas e compras usam somente os contatos aprovados no prompt.
O agente não dá orientação técnica de produto e não promete encaminhamento técnico: a integração
atual encaminha apenas leads comerciais completos ao supervisor regional.

## Dados e administracao

O Postgres configurado em `DATABASE_URL` armazena:

- `integral_mix_supervisors`: cadastro local, registros importados do NocoDB e o payload de origem;
- `integral_mix_supervisor_assignments`: reserva idempotente por negocio Pipefacil;
- `integral_mix_supervisor_handoffs`: outbox duravel, estado por etapa, tentativas, recibos e erro
  da ultima tentativa.

O painel autenticado em `/supervisores`, usando a sessao administrativa de `/settings`, permite
manter nome, telefone, categoria, regiao, volume acumulado, status ativo e pedido minimo. O worker
do proprio SDR busca o outbox ao iniciar e a cada cinco segundos; multiplas replicas podem rodar
em paralelo porque o Postgres reserva as operacoes com `SKIP LOCKED` e lease.

## Qualificacao e selecao

O agente retorna fatos incrementais validados; a regra de completude em código monta o pedido de
transferência. Os campos comuns sao nome, documento, atuacao, cidade e estado. Para criador,
tambem sao obrigatorios especie, frequencia e consumo. Para revendedor, sao obrigatorios nome da
loja, se trabalha com nutricao, categoria de produtos e volume; marcas atuais tambem sao
obrigatorias quando a resposta for sim.

A classificacao semantica validada do perfil escolhe um `routing_segment`; o backend converte esse
valor para a categoria de roteamento, sem deixar o modelo escolher o rótulo CRM diretamente:

- `AQUAMIX`: criacao de peixes ou camaroes;
- `PECUARIAS`: criacao de bovinos, caprinos ou ovinos;
- `AGRO`: criacao de equinos, suinos, aves ou coelhos;
- `PETSHOP`: varejo especializado em pets;
- `CANAL_PET_ALIMENTAR`: produtos pet em varejo alimentar;
- `AGROPECUARIAS`: revenda agropecuaria;
- `unknown`: fallback por regiao.

Para criadores, caprinos e ovinos sempre pertencem a `ruminant_creator` (`PECUARIAS`), nunca a
`agro_reseller` (`AGROPECUARIAS`).

A selecao ignora cadastros inativos, considera primeiro a mesma regiao e categoria, usa os
supervisores ativos da mesma regiao quando nao ha correspondencia de categoria e escolhe a menor
contagem de leads. Empates vao para quem recebeu lead ha mais tempo e depois para o menor ID local.
O algoritmo falha fechado se nao houver cobertura para o estado; nao encaminha para outra regiao.
O painel `/supervisores` exibe dinamicamente as UFs sem cobertura ativa para que a equipe corrija
o cadastro antes de depender do encaminhamento nessas localidades.

A reserva, o incremento do contador e a criacao do outbox sao uma unica transacao. A chave e
estavel por `deal.seq` do Pipefacil, portanto reentregas do mesmo lead nao criam nova atribuicao.
Depois da reserva, a aplicacao registra o supervisor no checkpoint da conversa para o agente nao
propor uma segunda transferencia.

## Integracao Pipefacil

O lead recebe os campos existentes no Pipefacil por `PATCH /api/v1/deals/{seq}`:
`cidade`, `estado`, `area_atuacao`, `documento`, `frequencia_de_compra`, `marcas_atuais`,
`produtos`, `valor_de_compra`, `especies`, `consumo`, `loja` e `supervisor`. No mesmo PATCH, o
negocio muda para a etapa comercial reconhecida dentro do funil atual, como `Enviado para o
comercial` ou `Encaminhado para supervisor`. A qualificacao nao troca o responsavel Pipefacil
(`responsibleUserId`): os membros retornados pela API nao tinham correspondencia exata de nome
com os 19 cadastros de supervisores importados. O campo personalizado `supervisor` preserva a
atribuicao legivel no lead.

O Pipefacil nao retornou o campo personalizado `trabalha_nutricao`, e a credencial de integracao
nao tem permissao para cria-lo. Quando esse dado se aplica ao revendedor, a sincronizacao o grava
em uma linha marcada nas observacoes do negocio, preservando o restante das observacoes e
substituindo a linha anterior do proprio SDR em retries.

O outbox executa e confirma, nessa ordem, sincronizacao do CRM, notificacao do supervisor e
notificacao do lead. Cada etapa concluida tem um recibo persistido. Falhas ficam pendentes com
backoff exponencial limitado a cinco minutos e sao retomadas pelo worker. O lead recebe uma
resposta transparente quando o handoff fica pendente ou nao ha cobertura; o agente nao afirma que
encaminhou antes da confirmacao. A atualizacao do CRM e idempotente. O envio de mensagem e no
maximo uma vez na maioria das falhas, mas a API de mensagem nao oferece chave de idempotencia: se o
Pipefacil aceitar uma mensagem e a aplicacao perder a resposta antes de salvar o recibo, o retry
pode enviar essa notificacao novamente.

O destino das mensagens usa o telefone do supervisor cadastrado e o telefone do contato do
webhook, junto do canal e do numero remetente Pipefacil recebidos no evento. A notificacao pode
permanecer pendente quando o canal rejeitar o envio, por exemplo fora das regras vigentes da
janela de conversa do WhatsApp.

Referencias do contrato consultado:
[atualizar negocio](https://developers.matchsales.com.br/api/resources/deals/methods/update-deal/),
[campos personalizados](https://developers.matchsales.com.br/api/resources/reference/methods/list-custom-fields/)
e [enviar mensagem](https://developers.matchsales.com.br/api/resources/conversations/methods/send-message/).
