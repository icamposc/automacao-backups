# Webhook e Mensagens do Jira

Documentação da estrutura de comunicação entre o Jira Service Management
e o sistema de Automação de Backups.

---

## O que o Jira precisa enviar (Webhook)

**Rota:** `POST /webhook/backup-desligado`

### Headers

| Header | Obrigatório | Descrição |
|---|---|---|
| `Content-Type` | Sim | `application/json` |
| `X-Hub-Signature` | Não* | Assinatura HMAC-SHA256 do corpo da requisição |

\* Obrigatório se `JIRA_WEBHOOK_SEGREDO` estiver configurado no `.env`

### Payload JSON

```json
{
    "email_colaborador": "colaborador@empresa.com",
    "ticket_id": "SPN-123",
    "nome_colaborador": "João da Silva"
}
```

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `email_colaborador` | string | Sim | E-mail da conta Google Workspace do colaborador desligado |
| `ticket_id` | string | Sim | Chave do ticket no Jira (ex: `SPN-123`) |
| `nome_colaborador` | string | Não | Nome completo do colaborador (usado em logs e comentários) |

### Respostas possíveis

| HTTP | Status | Significado |
|---|---|---|
| `200` | `iniciado` | Backup iniciado com sucesso em background |
| `200` | `ja_em_processamento` | Já existe backup ativo para esse e-mail |
| `400` | erro | Payload inválido (campo ausente ou e-mail mal formatado) |
| `401` | erro | Assinatura HMAC inválida |
| `500` | erro | Erro interno do servidor |

---

## Mensagens enviadas ao ticket do Jira (por etapa)

### Etapa 1/8 — Início

```
[Automação] Backup iniciado para: colaborador@empresa.com

Etapas em andamento:
1. Criando exportação de E-mails no Google Vault
2. Criando exportação do Drive no Google Vault

O processo pode levar algumas horas dependendo do volume de dados.
Atualizações serão postadas automaticamente neste ticket.
```

### Etapa 2/8 — Criação das exportações

```
[Automação] Atualização de progresso: Criando exportações de E-mail e Drive no Google Vault
```

### Etapa 3/8 — Monitoramento

```
[Automação] Atualização de progresso: Exportações criadas. Aguardando conclusão (pode levar algumas horas)...
```

### Etapa 4/8 — Download

```
[Automação] Atualização de progresso: Exportações concluídas. Baixando arquivos...
```

### Etapa 5/8 — Compactação

```
[Automação] Atualização de progresso: Compactando arquivos em ZIP...
```

### Etapa 6/8 — Upload

```
[Automação] Atualização de progresso: Enviando backup para Google Drive Compartilhado...
```

### Etapa 7/8 — Resultado do backup

```
[Automação] Backup CONCLUÍDO com sucesso para: colaborador@empresa.com

O arquivo de backup foi enviado para o Google Drive Compartilhado.
Link: https://drive.google.com/file/d/abc123/view?usp=drivesdk

Conteúdo do backup:
- E-mails (formato PST)
- Arquivos do Google Drive
```

### Etapa 8/8 — Exclusão da conta

**Comentário 1 — Progresso:**

```
[Automação] Atualização de progresso: Verificando backup no Drive Compartilhado antes de excluir a conta...
```

**Comentário 2 — Confirmação:**

```
[Automação] Conta excluída: colaborador@empresa.com
```

### Em caso de ERRO (qualquer etapa)

```
[Automação] ERRO no backup de: colaborador@empresa.com

Descrição do erro: {descrição técnica do erro}

Ação necessária: Verificar os logs do sistema e tentar novamente
se necessário. Contate a equipe de infraestrutura caso o erro persista.
```
