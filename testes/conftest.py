"""
Fixtures globais para os testes pytest.

Fornece mocks reutilizáveis para as APIs externas (Google, Jira)
e configurações isoladas de ambiente de teste.
"""

import logging
import os
import sys
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Garante que o diretório raiz do projeto está no sys.path
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# Impede que os testes escrevam na trilha de auditoria real
# (logs/auditoria/auditoria.log). Pré-configura o logger "auditoria" com um
# NullHandler: como utils.auditoria só adiciona handlers se ainda não houver,
# isso anula a escrita em arquivo/console durante os testes.
_aud_logger = logging.getLogger("auditoria")
_aud_logger.addHandler(logging.NullHandler())
_aud_logger.propagate = False

# Pré-mocka os módulos do Celery para evitar conexão real com Redis durante testes.
# Isso deve acontecer ANTES de qualquer importação dos módulos do projeto.
_mock_celery_app = MagicMock()
_mock_task = MagicMock()
_mock_task.delay.return_value = MagicMock(id="test-task-id")
_mock_tarefas = MagicMock(executar_backup=_mock_task)

sys.modules.setdefault("celery", MagicMock())
sys.modules.setdefault("worker.celery_app", _mock_celery_app)
sys.modules.setdefault("worker.tarefas", _mock_tarefas)

# Variáveis de ambiente mínimas para testes
os.environ.setdefault("GOOGLE_CREDENCIAIS_PATH", "config/credenciais/service-account.json")
os.environ.setdefault("GOOGLE_ADMIN_EMAIL", "admin@empresa.com")
os.environ.setdefault("VAULT_MATTER_ID", "matter-id-test")
os.environ.setdefault("DRIVE_PASTA_DESTINO_ID", "pasta-id-test")
os.environ.setdefault("JIRA_URL_BASE", "https://empresa.atlassian.net")
os.environ.setdefault("JIRA_EMAIL", "bot@empresa.com")
os.environ.setdefault("JIRA_API_TOKEN", "token-de-teste")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SQLITE_PATH", ":memory:")


# ─── Fixtures de APIs Google ───────────────────────────────────────────────

@pytest.fixture
def mock_vault_service(mocker):
    """Mock completo do serviço do Google Vault."""
    mock = MagicMock()
    mocker.patch("servicos.google_auth.obter_servico_vault", return_value=mock)
    return mock


@pytest.fixture
def mock_drive_service(mocker):
    """Mock do serviço do Google Drive."""
    mock = MagicMock()
    mocker.patch("servicos.google_auth.obter_servico_drive", return_value=mock)
    return mock


@pytest.fixture
def mock_admin_service(mocker):
    """Mock do Google Admin Directory."""
    mock = MagicMock()
    mocker.patch("servicos.google_auth.obter_servico_admin", return_value=mock)
    return mock


@pytest.fixture
def mock_storage_client(mocker):
    """Mock do Google Cloud Storage."""
    mock = MagicMock()
    mocker.patch("servicos.google_auth.obter_cliente_storage", return_value=mock)
    return mock


@pytest.fixture
def mock_google_chat(mocker):
    """Mock de todas as funções de notificação do Google Chat."""
    return mocker.patch("requests.post", return_value=MagicMock(status_code=200))


# ─── Fixtures de banco de dados ───────────────────────────────────────────

@pytest.fixture
def banco_teste(tmp_path):
    """Banco SQLite temporário isolado para cada teste."""
    from config import configuracoes
    from dados.banco import inicializar_banco, fechar_conexao_thread

    # Fecha conexão anterior para forçar nova conexão com o novo path
    fechar_conexao_thread()

    caminho = tmp_path / "test_backups.db"
    configuracoes.SQLITE_PATH = caminho

    inicializar_banco()

    yield caminho

    fechar_conexao_thread()


# ─── Fixtures de Flask ────────────────────────────────────────────────────

@pytest.fixture
def cliente_flask(banco_teste, mocker):
    """Cliente de teste do Flask com banco isolado."""
    # Evita iniciar Celery/Redis durante testes
    mocker.patch("worker.tarefas.executar_backup.delay", return_value=MagicMock(id="task-123"))

    from app.servidor import app
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
