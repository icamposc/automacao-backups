# Requisitos de Integração — Jira (Atlassian) via WebHook

**Projeto:** Automação de Backups  
**Versão:** 1.0.0  
**Data:** 2026-04-01

---

## Sumário

1. [Visão Geral](#1-visão-geral)
2. [Fluxo dos Chamados no Jira](#2-fluxo-dos-chamados-no-jira)
3. [Requisitos do Chamado Filho (SPN)](#3-requisitos-do-chamado-filho-spn)
4. [Configuração do WebHook no Jira](#4-configuração-do-webhook-no-jira)
5. [Payload Enviado pelo WebHook](#5-payload-enviado-pelo-webhook)
6. [Ações da Automação no Jira](#6-ações-da-automação-no-jira)
7. [Requisitos de Segurança](#7-requisitos-de-segurança)
8. [Requisitos de Configuração da Conta de Serviço](#8-requisitos-de-configuração-da-conta-de-serviço)
9. [Tratamento de Erros](#9-tratamento-de-erros)
10. [Pontos em Aberto / Dúvidas](#10-pontos-em-aberto--dúvidas)

---

## 1. Visão Geral

Este documento descreve os requisitos necessários para que o Jira Service Management (Atlassian) se integre corretamente com o sistema de Automação de Backups via WebHook.

### Contexto

O processo de backup de colaboradores desligados é iniciado a partir de um chamado no projeto **ACCESS**. Uma automação do próprio Jira cria automaticamente um chamado filho no projeto **SPN** para representar a tarefa de backup. É este chamado **SPN** que dispara o WebHook para o sistema de automação e recebe todas as atualizações de progresso durante o processo.

### Diagrama do Fluxo

```
┌────────────────────────────────────────────────────────────┐
│                    JIRA SERVICE MANAGEMENT                 │
│                                                            │
│  Projeto ACCESS                  Projeto SPN               │
│  ┌─────────────────┐             ┌─────────────────┐       │
│  │ Chamado Pai     │  Automação  │ Chamado Filho   │       │
│  │ (Desligamento)  │────────────►│ (Backup)        │       │
│  │ ACCESS-XXX      │  cria filho │ SPN-XXX         │       │
│  └─────────────────┘             └────────┬────────┘       │
│                                           │                │
│                              WebHook POST │ (ao criar ou   │
│                                           │  ao transicionar│
│                                           │  para status   │
│                                           │  configurado)  │
└───────────────────────────────────────────┼────────────────┘
                                            │
                                            ▼
                               ┌────────────────────────┐
                               │  Sistema de Automação  │
                               │  (automacao-backups)   │
                               │                        │
                               │  1. Valida payload     │
                               │  2. Inicia backup      │
                               │  3. Atualiza Jira      │
                               └────────────────────────┘
```

---

## 2. Fluxo dos Chamados no Jira

### 2.1 Chamado Pai — Projeto ACCESS

| Atributo | Valor |
|---|---|
| **Projeto** | ACCESS |
| **Tipo** | A definir (ex: Solicitação de Desligamento) |
| **Criado por** | Equipe de RH ou gestor responsável |
| **Função no fluxo** | Origem do processo de desligamento; não recebe WebHook |

O chamado ACCESS deve conter ao menos:
- E-mail corporativo (Google Workspace) do colaborador desligado
- Nome completo do colaborador

### 2.2 Chamado Filho — Projeto SPN

| Atributo | Valor |
|---|---|
| **Projeto** | SPN |
| **Tipo** | A definir (ex: Sub-tarefa / Backup de Dados) |
| **Criado por** | Automação do Jira a partir do chamado ACCESS |
| **Função no fluxo** | Representa a tarefa de backup; recebe o WebHook e as atualizações de progresso |

A automação do Jira (Automation for Jira) deve, ao criar o chamado SPN, disparar o WebHook para o endpoint do sistema de automação.

### 2.3 Gatilho do WebHook

O WebHook é disparado quando o chamado SPN sofre uma **transição de status**.

| Evento | Status de origem | Status de destino |
|---|---|---|
| Operador move o chamado para iniciar o backup | Qualquer | A definir (ex: "Em Execução" ou equivalente no SPN) |

> **Observação:** a automação do Jira (Automation for Jira) deve monitorar a transição para o status que sinaliza início do backup e, ao detectá-la, disparar o WebHook via "Send web request".

---

## 3. Requisitos do Chamado Filho (SPN)

### 3.1 Campos Obrigatórios

O chamado SPN deve possuir os seguintes campos para que o WebHook seja corretamente processado pela automação:

| Campo | Tipo | Onde fica | Descrição |
|---|---|---|---|
| **Email Coorporativo** | Texto dentro da Descrição do formulário | Campo `Descrição` do chamado SPN | E-mail da conta Google Workspace; aparece no texto da Descrição no formato `*Email Coorporativo:* colaborador@empresa.com` |
| **Chave do ticket** | Gerado automaticamente pelo Jira | Final da URL do chamado | Identificador do chamado SPN (ex: `SPN-12345`); ex: `.../browse/SPN-12345` |

> **Observação:** o e-mail **não está em um campo separado** — ele faz parte do texto da Descrição do formulário do chamado SPN. Por isso, a extração do e-mail exige uma das abordagens descritas na seção 5.2.

### 3.2 Campos Opcionais (recomendados)

| Campo | Tipo | Origem | Descrição |
|---|---|---|---|
| **Nome do colaborador** | Campo de texto | Formulário ou chamado ACCESS | Utilizado em logs e comentários da automação |
| **Link para o chamado pai** | Link de issue | Automação | Referência ao chamado ACCESS de origem |

### 3.3 Status do Chamado SPN

A automação gerencia o status do chamado SPN em dois momentos:

| Momento | Ação | Status destino |
|---|---|---|
| WebHook recebido — backup iniciado | Transicionar chamado | **Em Análise** |
| Backup concluído com sucesso e conta excluída | Transicionar chamado + comentário final | **Resolvido** |
| Erro em qualquer etapa | Postar comentário de erro | Sem transição (permanece no status atual) |

> **Observação:** a automação usa a API REST do Jira para executar as transições. Os IDs das transições "Em Análise" e "Resolvido" precisam ser levantados via `GET /rest/api/3/issue/{ticket_id}/transitions` antes de colocar em produção.

---

## 4. Configuração do WebHook no Jira

### 4.1 Onde Configurar

O WebHook deve ser configurado na automação do Jira que cria o chamado SPN a partir do ACCESS. Utilizar o recurso **"Send web request"** (Enviar solicitação web) do **Automation for Jira**.

### 4.2 Parâmetros do WebHook

| Parâmetro | Valor |
|---|---|
| **URL** | `https://<servidor>/webhook/backup-desligado` |
| **Método HTTP** | `POST` |
| **Content-Type** | `application/json` |
| **Header de segurança** | `X-Hub-Signature: <assinatura HMAC-SHA256>` (ver seção 7) |

### 4.3 URL do Endpoint

O servidor ainda não está em produção. Para a fase de homologação/testes, utilizar um túnel para expor o servidor local à internet temporariamente:

| Ferramenta | Uso recomendado |
|---|---|
| **ngrok** | Testes rápidos e pontuais em ambiente de desenvolvimento |
| **Cloudflare Tunnel** | Ambiente de homologação mais estável |

Para produção, o servidor precisa estar acessível a partir dos IPs da Atlassian Cloud com HTTPS. Definir a estratégia de exposição (IP público, proxy reverso, etc.) antes do deploy.

---

## 5. Payload Enviado pelo WebHook

O corpo da requisição POST enviado pelo Jira deve seguir o formato JSON abaixo:

```json
{
    "email_colaborador": "colaborador@empresa.com",
    "ticket_id": "SPN-123",
    "nome_colaborador": "João da Silva"
}
```

### 5.1 Campos do Payload

| Campo | Tipo | Obrigatório | Descrição | Exemplo |
|---|---|---|---|---|
| `email_colaborador` | string | **Sim** | E-mail da conta Google Workspace do colaborador | `joao.silva@empresa.com` |
| `ticket_id` | string | **Sim** | Chave do chamado SPN no Jira | `SPN-123` |
| `nome_colaborador` | string | Não | Nome completo do colaborador (para logs e comentários) | `João da Silva` |

### 5.2 Como Mapear os Campos no Automation for Jira

Como o e-mail está **dentro do texto da Descrição** do chamado (não em um campo separado), existem duas abordagens para extraí-lo:

---

#### Opção A — Enviar a Descrição completa no payload (recomendada)

O Automation for Jira envia a Descrição inteira no payload. O sistema de automação extrai o e-mail via expressão regular.

**Payload configurado no Automation for Jira:**

```json
{
    "descricao": "{{issue.description}}",
    "ticket_id": "{{issue.key}}"
}
```

**Como o sistema extrai o e-mail:**

O texto da Descrição é uma sequência contínua sem quebras de linha entre os campos, no formato:
```
...Nome colaborador: NOME COMPLETOEmail Coorporativo: colaborador@empresa.com.brEmail pessoal:...
```

A automação aplica a regex abaixo para encontrar o e-mail. O TLD usa `[a-z]{2,6}` (minúsculo) para interromper o match no início do próximo campo, que sempre começa com letra maiúscula:
```
Email Coorporativo:\s*([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-z]{2,6})
```

**Validado com o chamado SPN-61176** — e-mail extraído corretamente: `alex.moreira@bulkylog.com.br`

> **Vantagem:** não requer nenhuma chamada extra à API do Jira.  
> **Formato garantido:** o formulário do chamado SPN impõe o formato padrão `Email Coorporativo:` — não há variação livre no preenchimento.

---

#### Opção B — Enviar apenas o `ticket_id` e buscar a Descrição via API

O WebHook envia somente a chave do chamado. O sistema faz uma chamada à API do Jira para buscar a Descrição e extrair o e-mail.

**Payload configurado no Automation for Jira:**

```json
{
    "ticket_id": "{{issue.key}}"
}
```

**Chamada que o sistema fará internamente:**

```
GET /rest/api/3/issue/{ticket_id}?fields=description
```

> **Vantagem:** payload minimalista; a extração do e-mail fica centralizada no sistema.  
> **Desvantagem:** adiciona uma chamada extra à API do Jira a cada backup iniciado; requer permissão de leitura no projeto SPN para a conta de serviço.

---

#### Comparativo

| Critério | Opção A (Descrição no payload) | Opção B (busca via API) |
|---|---|---|
| Chamadas extras à API | Nenhuma | 1 por backup |
| Complexidade de configuração no Jira | Baixa | Muito baixa |
| Sensibilidade ao formato da Descrição | Baixa (formato padrão garantido) | Baixa (mesma regex) |
| Configuração extra de permissão | Não | Sim (leitura no SPN) |

> **Decisão necessária:** definir qual opção será adotada. A **Opção A** é recomendada por ser mais simples e não depender de chamadas adicionais.

---

| Campo no payload | Variável Jira | Disponível em |
|---|---|---|
| `descricao` (Opção A) | `{{issue.description}}` | Texto da Descrição do chamado SPN |
| `ticket_id` | `{{issue.key}}` | Chave nativa do chamado (ex: `SPN-12345`) |

---

## 6. Ações da Automação no Jira

Após receber o WebHook, o sistema de automação realiza as seguintes ações no chamado SPN:

### 6.1 Comentários por Etapa

A automação posta comentários automáticos no chamado SPN em cada etapa do processo:

| Etapa | Quando | Comentário |
|---|---|---|
| **1 — Início** | Ao receber o WebHook | Confirma início do backup e lista as etapas |
| **2 — Criando exportações** | Ao criar exports no Vault | Informa criação das exportações de e-mail e Drive |
| **3 — Aguardando exportações** | Exports criados | Informa que aguarda conclusão (pode levar horas) |
| **4 — Baixando arquivos** | Exports concluídos | Informa início do download |
| **5 — Compactando** | Download concluído | Informa compactação em ZIP |
| **6 — Enviando para Drive** | Compactação concluída | Informa upload para Drive Compartilhado |
| **7 — Backup concluído** | Upload finalizado | Informa conclusão com link do arquivo no Drive |
| **8a — Verificando backup** | Antes da exclusão | Informa que verificará o backup antes de excluir a conta |
| **8b — Conta excluída** | Após confirmação | Confirma exclusão da conta Google Workspace |
| **Erro** | Em qualquer etapa | Descreve o erro e orienta ação manual |

### 6.2 Requisito de API do Jira

Para postar comentários, a automação utiliza a **API REST do Jira** (v3):

```
POST /rest/api/3/issue/{issueIdOrKey}/comment
```

Autenticação: **Basic Auth** com e-mail + API Token da conta de serviço.

### 6.3 Transição de Status

A automação gerencia o status do chamado SPN via API REST do Jira:

```
POST /rest/api/3/issue/{issueIdOrKey}/transitions
```

**Body:**
```json
{
    "transition": { "id": "<id_da_transicao>" }
}
```

**Transições necessárias:**

| Momento | Status destino | ID da transição |
|---|---|---|
| Backup iniciado (WebHook recebido) | **Em Análise** | Levantar antes da produção |
| Backup concluído e conta excluída | **Resolvido** | Levantar antes da produção |

**Como obter os IDs:**
```
GET /rest/api/3/issue/{qualquer_ticket_SPN}/transitions
```
Retorna a lista de transições disponíveis com seus IDs. Configurar os valores nas variáveis de ambiente antes do primeiro deploy.

---

## 7. Requisitos de Segurança

### 7.1 Validação de Assinatura (HMAC-SHA256)

**O que é:** o Jira e o servidor combinam uma "senha secreta" previamente. A cada WebHook enviado, o Jira assina a mensagem com essa senha. O servidor confere a assinatura antes de processar qualquer coisa — se não bater, a requisição é rejeitada com HTTP 401.

**Por que é importante:** sem essa validação, qualquer pessoa que descubra a URL do servidor consegue disparar um backup enviando um POST diretamente, sem passar pelo Jira.

**Como configurar:**

1. Definir uma senha secreta (string aleatória e longa, ex: gerada com `openssl rand -hex 32`)
2. No Jira, ao configurar o "Send web request", adicionar o header:
   ```
   X-Hub-Signature: sha256={{hmac("sha256", "<senha_secreta>", issue.description)}}
   ```
   *(a sintaxe exata depende da versão do Automation for Jira — verificar a documentação)*
3. Configurar a mesma senha na variável `JIRA_WEBHOOK_SEGREDO` no `.env` do servidor

**Recomendação:** habilitar desde o início, inclusive em homologação. A configuração é simples e evita retrabalho ao ir para produção.

### 7.2 Restrição de IP (recomendado)

Restringir o acesso ao endpoint `/webhook/backup-desligado` apenas para os IPs da Atlassian Cloud. A lista de IPs pode ser obtida em: [https://confluence.atlassian.com/cloud/security-for-connect-apps-952778861.html](https://confluence.atlassian.com/cloud/security-for-connect-apps-952778861.html)

### 7.3 HTTPS Obrigatório

O endpoint deve ser exposto exclusivamente via **HTTPS**. Conexões HTTP não criptografadas não devem ser aceitas em produção.

---

## 8. Requisitos de Configuração da Conta de Serviço

A conta de serviço do Jira utilizada pela automação para postar comentários deve ter as seguintes permissões no projeto SPN:

| Permissão | Necessária para |
|---|---|
| **Adicionar comentários** | Postar atualizações de progresso |
| **Visualizar issues** | Verificar se o chamado existe antes de comentar |
| **Transicionar issues** | (Opcional) Alterar status do chamado |

### 8.1 Geração do API Token

1. Acessar: `https://id.atlassian.com/manage-profile/security/api-tokens`
2. Criar um token com nome identificável (ex: `automacao-backups-prod`)
3. Armazenar o token na variável `JIRA_API_TOKEN` no `.env` do servidor
4. **Nunca versionar o token no repositório**

---

## 9. Tratamento de Erros

### 9.1 Falha no Recebimento do WebHook

| Cenário | Comportamento |
|---|---|
| Payload sem `email_colaborador` ou `ticket_id` | Retorna HTTP 400; nenhuma ação executada |
| Assinatura HMAC inválida | Retorna HTTP 401; nenhuma ação executada |
| Backup já em andamento para o mesmo e-mail | Retorna HTTP 200 com status `ja_em_processamento`; nenhuma nova execução |
| Erro interno no servidor | Retorna HTTP 500; log registrado |

### 9.2 Falha Durante o Processo de Backup

Se ocorrer erro em qualquer etapa após o início do processamento:
- A automação posta comentário de erro no chamado SPN com a descrição técnica
- O processo é interrompido
- Arquivos temporários são removidos
- A conta do colaborador **não** é excluída (segurança)

### 9.3 Falha ao Comentar no Jira

Erros na API do Jira (ex: token expirado, chamado inexistente) são registrados em log mas **não interrompem** o processo de backup. O backup segue normalmente.

---

## 10. Pontos em Aberto / Dúvidas

Os itens abaixo precisam ser confirmados antes da implementação:

| # | Questão | Status | Impacto |
|---|---|---|---|
| 1 | ~~Qual o gatilho do WebHook?~~ | **Respondido** | Transição de status do chamado SPN (opção B) — a automação Jira dispara o WebHook ao detectar a transição |
| 2 | ~~Qual o ID do campo "Email Coorporativo"?~~ | **Respondido** | O e-mail está dentro do texto da **Descrição** do chamado, no formato `*Email Coorporativo:* email@empresa.com` — extraído via regex (ver seção 5.2) |
| 3 | ~~A automação deve transicionar status ou apenas comentar?~~ | **Respondido** | Ambos — transiciona para **Em Análise** ao iniciar e para **Resolvido** ao concluir; posta comentários em todas as etapas |
| 4 | ~~Quais os status existentes no projeto SPN?~~ | **Respondido** | Indiferente — a automação utiliza apenas **Em Análise** e **Resolvido**; IDs das transições a levantar antes da produção |
| 5 | ~~O servidor está acessível pela internet?~~ | **Respondido** | Servidor ainda não está em produção — usar **ngrok** para testes e **Cloudflare Tunnel** para homologação; definir estratégia de exposição antes do deploy |
| 6 | ~~O que é HMAC e quando habilitar?~~ | **Respondido** | Assinatura de segurança que garante que só o Jira pode disparar backups — **habilitar desde o início**, inclusive em homologação (ver seção 7.1) |
| 7 | ~~O chamado SPN herda automaticamente o e-mail e nome do chamado ACCESS?~~ | **Respondido** | O e-mail vem do campo **"Email Coorporativo"** no formulário do chamado SPN; o `ticket_id` vem da URL (`SPN-XXXXX`) |
