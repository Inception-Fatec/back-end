"""
Testes adicionais — discord_bot.py
=====================================
Complementa test_qa.py existente focando nas linhas descobertas:
  - abreviar_nome          (~170, 173, 178, 197-201)
  - jira_get_sprint_issues (~77, 101-121)
  - coletar_e_responder    (~253-294)
  - responder_em_review    (~324)
  - verificar_sem_responsavel (~401, 404, 415-416)
  - on_message             (~438-469)
  - verificar_atualizacoes_jira extras (~550-590)
  - verificar_em_review extras (~600-635)

Rodar:
    coverage run -m pytest tests/ -v
    coverage report -m
"""

import pytest
import os
import sys
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

# ─── Ambiente ────────────────────────────────────────────────────────────────

os.environ.setdefault("JIRA_URL",      "https://test.atlassian.net")
os.environ.setdefault("JIRA_EMAIL",    "test@test.com")
os.environ.setdefault("JIRA_TOKEN",    "fake-token")
os.environ.setdefault("JIRA_PROJECT",  "SCRUM")
os.environ.setdefault("SPRINT_NAME",   "Sprint 4")
os.environ.setdefault("GITHUB_TOKEN",  "fake-github-token")
os.environ.setdefault("GITHUB_REPOS_PIPELINE", "Inception-Fatec/front-end")
os.environ.setdefault("GITHUB_REPOS_PRS",      "Inception-Fatec/front-end")
os.environ.setdefault("FRONTEND_PATH", "front-end")
os.environ.setdefault("DISCORD_BOT_TOKEN", "fake-discord-token")
os.environ.setdefault("GUILD_ID", "0")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import qa_bot.qa_metrics as qa

try:
    import qa_bot.discord_bot as bot
    BOT_DISPONIVEL = True
except Exception:
    BOT_DISPONIVEL = False

pytestmark = pytest.mark.skipif(not BOT_DISPONIVEL, reason="discord_bot.py não disponível")


# =============================================================================
# abreviar_nome
# =============================================================================

class TestAbreviarNome:

    def test_nome_completo(self):
        assert bot.abreviar_nome("Gabriel Fernando de Lima") == "Gabriel L."

    def test_dois_nomes(self):
        assert bot.abreviar_nome("Ana Silva") == "Ana S."

    def test_nome_simples(self):
        assert bot.abreviar_nome("Lucas") == "Lucas"

    def test_none_retorna_traco(self):
        assert bot.abreviar_nome(None) == "—"

    def test_traco_retorna_traco(self):
        assert bot.abreviar_nome("—") == "—"

    def test_capitaliza_primeiro_nome(self):
        assert bot.abreviar_nome("matheus castillo").startswith("Matheus")

    def test_string_vazia_retorna_traco(self):
        assert bot.abreviar_nome("") == "—"


# =============================================================================
# jira_get_sprint_issues
# =============================================================================

class TestJiraGetSprintIssues:

    def _resp(self, status_code=200, json_data=None, text=""):
        r = MagicMock()
        r.status_code = status_code
        r.json.return_value = json_data or {}
        r.text = text
        return r

    def test_retorna_issues_com_sprint_id(self):
        issues = [{"key": "SCRUM-1", "fields": {}}]
        with patch.object(qa, "get_sprint_id", return_value=42), \
             patch("requests.get", return_value=self._resp(200, {"issues": issues})):
            assert len(bot.jira_get_sprint_issues()) == 1

    def test_usa_fallback_sem_sprint_id(self):
        issues = [{"key": "SCRUM-1", "fields": {}}]
        with patch.object(qa, "get_sprint_id", return_value=None), \
             patch("requests.get", return_value=self._resp(200, {"issues": issues})):
            assert len(bot.jira_get_sprint_issues()) == 1

    def test_retorna_vazio_quando_api_falha(self, capsys):
        with patch.object(qa, "get_sprint_id", return_value=42), \
             patch("requests.get", return_value=self._resp(403, text="Forbidden")):
            assert bot.jira_get_sprint_issues() == []
        assert "403" in capsys.readouterr().out


# =============================================================================
# responder_em_review
# =============================================================================

class TestResponderEmReview:

    @pytest.mark.asyncio
    async def test_sem_tasks_em_review_envia_ok(self, tmp_path):
        original = bot.ESTADO_FILE
        bot.ESTADO_FILE = str(tmp_path / "estado.json")
        bot.salvar_estado({"SCRUM-1": {
            "status": "Em Andamento", "summary": "Task",
            "assignee": "Dev", "em_review_desde": None
        }})
        canal = AsyncMock()
        await bot.responder_em_review(canal)
        canal.send.assert_called()
        bot.ESTADO_FILE = original

    @pytest.mark.asyncio
    async def test_com_task_em_review_recente(self, tmp_path):
        original = bot.ESTADO_FILE
        bot.ESTADO_FILE = str(tmp_path / "estado.json")
        desde = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        bot.salvar_estado({"SCRUM-5": {
            "status": "In Review", "summary": "Review task",
            "assignee": "Dev", "em_review_desde": desde
        }})
        canal = AsyncMock()
        await bot.responder_em_review(canal)
        canal.send.assert_called()
        bot.ESTADO_FILE = original

    @pytest.mark.asyncio
    async def test_com_task_em_review_ha_muito_tempo(self, tmp_path):
        original = bot.ESTADO_FILE
        bot.ESTADO_FILE = str(tmp_path / "estado.json")
        desde = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        bot.salvar_estado({"SCRUM-7": {
            "status": "In Review", "summary": "Task parada",
            "assignee": "Dev", "em_review_desde": desde
        }})
        canal = AsyncMock()
        await bot.responder_em_review(canal)
        canal.send.assert_called_once()
        # O embed é passado como kwarg — inspeciona o objeto diretamente
        embed = canal.send.call_args.kwargs.get("embed") or canal.send.call_args[1].get("embed")
        assert embed is not None
        assert "review" in embed.title.lower() or "30" in str(embed.fields[0].value)
        bot.ESTADO_FILE = original

    @pytest.mark.asyncio
    async def test_data_invalida_ignorada_sem_erro(self, tmp_path):
        original = bot.ESTADO_FILE
        bot.ESTADO_FILE = str(tmp_path / "estado.json")
        bot.salvar_estado({"SCRUM-9": {
            "status": "In Review", "summary": "Task",
            "assignee": "Dev", "em_review_desde": "data_invalida"
        }})
        canal = AsyncMock()
        await bot.responder_em_review(canal)  # não deve explodir
        bot.ESTADO_FILE = original


# =============================================================================
# verificar_sem_responsavel
# =============================================================================

class TestVerificarSemResponsavel:

    @pytest.mark.asyncio
    async def test_sem_canal_nao_falha(self):
        with patch.object(bot, "get_canal_alertas", return_value=None):
            await bot.verificar_sem_responsavel()  # não deve lançar

    @pytest.mark.asyncio
    async def test_todas_com_dono_nao_posta(self):
        issues = [{"key": "SCRUM-1", "fields": {
            "summary": "Task com dono",
            "status": {"name": "Em Andamento"},
            "assignee": {"displayName": "Dev Um"},
            "priority": {"name": "Medium"},
        }}]
        canal = AsyncMock()
        with patch.object(bot, "get_canal_alertas", return_value=canal), \
             patch.object(bot, "jira_get_sprint_issues", return_value=issues):
            await bot.verificar_sem_responsavel()
        canal.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_com_task_sem_dono_posta_embed(self):
        issues = [{"key": "SCRUM-2", "fields": {
            "summary": "Task sem dono",
            "status": {"name": "A Fazer"},
            "assignee": None,
            "priority": {"name": "High"},
        }}]
        canal = AsyncMock()
        with patch.object(bot, "get_canal_alertas", return_value=canal), \
             patch.object(bot, "jira_get_sprint_issues", return_value=issues):
            await bot.verificar_sem_responsavel()
        canal.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_task_concluida_sem_dono_ignorada(self):
        issues = [{"key": "SCRUM-3", "fields": {
            "summary": "Done sem dono",
            "status": {"name": "Concluído"},
            "assignee": None,
            "priority": {"name": "Low"},
        }}]
        canal = AsyncMock()
        with patch.object(bot, "get_canal_alertas", return_value=canal), \
             patch.object(bot, "jira_get_sprint_issues", return_value=issues):
            await bot.verificar_sem_responsavel()
        canal.send.assert_not_called()


# =============================================================================
# coletar_e_responder
# =============================================================================

class TestColetarEResponder:

    def _mocks_qa(self):
        jira = {"total_tasks": 5, "taxa_descricao": 80.0, "tasks_sem_descricao": 1,
                "taxa_criterios": 80.0, "tasks_sem_criterios": 1}
        dev  = {"total_branches": 5, "branches_invalidas": 0, "taxa_branches": 100.0,
                "total_commits": 10, "commits_invalidos": 0, "taxa_commits": 100.0}
        gh   = {"taxa_sucesso_pipeline": 90.0, "tempo_medio_pipeline": 0.5,
                "taxa_aprovacao_pr": 100.0, "tempo_medio_revisao_pr": 5.0}
        cov  = {"total": {"stmts": 88.0, "branch": 80.0, "funcs": 90.0, "lines": 90.0},
                "arquivos": []}
        return jira, dev, gh, cov

    def _patch_all(self, jira, dev, gh, cov):
        return (
            patch.object(qa, "calcular_metricas_jira", return_value=jira),
            patch.object(qa, "get_sprint_dates", return_value=(None, None)),
            patch.object(qa, "calcular_metricas_branches_commits", return_value=dev),
            patch.object(qa, "calcular_metricas_github", return_value=gh),
            patch.object(qa, "rodar_cobertura", return_value=""),
            patch.object(qa, "parse_cobertura", return_value=cov),
            patch.object(qa, "detectar_rotas_sem_cobertura", return_value=([], 5)),
        )

    @pytest.mark.asyncio
    async def test_relatorio_completo(self):
        jira, dev, gh, cov = self._mocks_qa()
        canal = AsyncMock()
        patches = self._patch_all(jira, dev, gh, cov)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await bot.coletar_e_responder(canal)
        assert canal.send.call_count >= 2

    @pytest.mark.asyncio
    async def test_secao_cobertura_sprint_atual(self):
        jira, dev, gh, cov = self._mocks_qa()
        canal = AsyncMock()
        patches = self._patch_all(jira, dev, gh, cov)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await bot.coletar_e_responder(canal, secao="cobertura")
        canal.send.assert_called()

    @pytest.mark.asyncio
    async def test_secao_cobertura_sprint_passada_avisa(self):
        jira, dev, gh, cov = self._mocks_qa()
        canal = AsyncMock()
        patches = self._patch_all(jira, dev, gh, cov)
        with patches[0], patches[1], patches[2], patches[3], \
             patch.object(qa, "rodar_cobertura", return_value="") as mock_cov, \
             patches[5], patches[6]:
            await bot.coletar_e_responder(canal, secao="cobertura", sprint="Sprint 1")
        mock_cov.assert_not_called()
        # Deve ter avisado que cobertura não está disponível para sprints passadas
        msgs = " ".join(str(c) for c in canal.send.call_args_list)
        assert "sprint" in msgs.lower() or "anterior" in msgs.lower() or "disponível" in msgs.lower()

    @pytest.mark.asyncio
    async def test_secao_pipeline(self):
        jira, dev, gh, cov = self._mocks_qa()
        canal = AsyncMock()
        patches = self._patch_all(jira, dev, gh, cov)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await bot.coletar_e_responder(canal, secao="pipeline")
        canal.send.assert_called()

    @pytest.mark.asyncio
    async def test_secao_branches(self):
        jira, dev, gh, cov = self._mocks_qa()
        canal = AsyncMock()
        patches = self._patch_all(jira, dev, gh, cov)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await bot.coletar_e_responder(canal, secao="branches")
        canal.send.assert_called()


    @pytest.mark.asyncio
    async def test_sprint_arg_restaura_sprint_original(self):
        """SPRINT_NAME deve voltar ao valor original após a chamada."""
        original = qa.SPRINT_NAME
        jira, dev, gh, cov = self._mocks_qa()
        canal = AsyncMock()
        patches = self._patch_all(jira, dev, gh, cov)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await bot.coletar_e_responder(canal, sprint="Sprint 99")
        assert qa.SPRINT_NAME == original


# =============================================================================
# on_message
# =============================================================================

class TestOnMessage:

    def _msg(self, content, channel_name="qa_bot"):
        """Cria uma mensagem fake. author != client.user é garantido por id diferente."""
        usuario_bot = MagicMock()
        usuario_bot.__eq__ = lambda s, o: False  # nunca é o próprio bot

        msg = MagicMock()
        msg.author = MagicMock()
        msg.author.__eq__ = lambda s, o: False
        msg.channel = MagicMock()
        msg.channel.name = channel_name
        msg.channel.send = AsyncMock()
        msg.content = content
        return msg

    @pytest.mark.asyncio
    async def test_ajuda(self):
        msg = self._msg("!ajuda")
        with patch.object(bot, "canal_permitido", return_value=True):
            await bot.on_message(msg)
        msg.channel.send.assert_called()

    @pytest.mark.asyncio
    async def test_help(self):
        msg = self._msg("!help")
        with patch.object(bot, "canal_permitido", return_value=True):
            await bot.on_message(msg)
        msg.channel.send.assert_called()

    @pytest.mark.asyncio
    async def test_ponto_de_exclamacao(self):
        msg = self._msg("!")
        with patch.object(bot, "canal_permitido", return_value=True):
            await bot.on_message(msg)
        msg.channel.send.assert_called()

    @pytest.mark.asyncio
    async def test_documentos(self):
        msg = self._msg("!documentos")
        with patch.object(bot, "canal_permitido", return_value=True):
            await bot.on_message(msg)
        msg.channel.send.assert_called()

    @pytest.mark.asyncio
    async def test_comando_desconhecido(self):
        msg = self._msg("!inexistente")
        with patch.object(bot, "canal_permitido", return_value=True):
            await bot.on_message(msg)
        call_text = str(msg.channel.send.call_args)
        assert "não reconhecido" in call_text or "ajuda" in call_text.lower()

    @pytest.mark.asyncio
    async def test_canal_nao_permitido_ignora(self):
        msg = self._msg("!qa", channel_name="geral")
        with patch.object(bot, "canal_permitido", return_value=False):
            await bot.on_message(msg)
        msg.channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_mensagem_propria_ignorada(self):
        """Simula message.author == client.user retornando True via mock do módulo."""
        autor_bot = MagicMock()
        msg = MagicMock()
        msg.author = autor_bot

        mock_client = MagicMock()
        mock_client.user = autor_bot  # mesmo objeto → autor_bot == mock_client.user é True

        with patch("qa_bot.discord_bot.client", mock_client):
            await bot.on_message(msg)
        # Se saiu cedo (author == client.user), channel.send nunca foi chamado
        msg.channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_comando_jira(self):
        msg = self._msg("!jira")
        with patch.object(bot, "canal_permitido", return_value=True), \
             patch.object(bot, "responder_status_jira", new=AsyncMock()) as mock_fn:
            await bot.on_message(msg)
        mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_comando_sem_dono(self):
        msg = self._msg("!sem-dono")
        with patch.object(bot, "canal_permitido", return_value=True), \
             patch.object(bot, "responder_sem_dono", new=AsyncMock()) as mock_fn:
            await bot.on_message(msg)
        mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_comando_em_review(self):
        msg = self._msg("!em-review")
        with patch.object(bot, "canal_permitido", return_value=True), \
             patch.object(bot, "responder_em_review", new=AsyncMock()) as mock_fn:
            await bot.on_message(msg)
        mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_comando_qa_com_sprint(self):
        msg = self._msg("!qa 3")
        with patch.object(bot, "canal_permitido", return_value=True), \
             patch.object(bot, "coletar_e_responder", new=AsyncMock()) as mock_fn:
            await bot.on_message(msg)
        mock_fn.assert_called_once()
        _, kwargs = mock_fn.call_args
        assert kwargs.get("sprint") == "Sprint 3"

    @pytest.mark.asyncio
    async def test_comando_relatorio(self):
        msg = self._msg("!relatorio")
        with patch.object(bot, "canal_permitido", return_value=True), \
             patch.object(bot, "coletar_e_responder", new=AsyncMock()) as mock_fn:
            await bot.on_message(msg)
        mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_comando_cobertura(self):
        msg = self._msg("!cobertura")
        with patch.object(bot, "canal_permitido", return_value=True), \
             patch.object(bot, "coletar_e_responder", new=AsyncMock()) as mock_fn:
            await bot.on_message(msg)
        mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_comando_pipeline(self):
        msg = self._msg("!pipeline")
        with patch.object(bot, "canal_permitido", return_value=True), \
             patch.object(bot, "coletar_e_responder", new=AsyncMock()) as mock_fn:
            await bot.on_message(msg)
        mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_comando_branches(self):
        msg = self._msg("!branches")
        with patch.object(bot, "canal_permitido", return_value=True), \
             patch.object(bot, "coletar_e_responder", new=AsyncMock()) as mock_fn:
            await bot.on_message(msg)
        mock_fn.assert_called_once()


# =============================================================================
# verificar_atualizacoes_jira — casos extras
# =============================================================================

class TestVerificarAtualizacoesExtras:

    @pytest.mark.asyncio
    async def test_nova_task_salva_no_estado(self, tmp_path):
        original = bot.ESTADO_FILE
        bot.ESTADO_FILE = str(tmp_path / "estado.json")
        issues = [{"key": "SCRUM-99", "fields": {
            "summary": "Nova task", "status": {"name": "A Fazer"},
            "assignee": None, "updated": "2026-06-10T10:00:00.000Z",
            "priority": {"name": "Low"},
        }}]
        canal = AsyncMock()
        with patch.object(bot, "get_canal_alertas", return_value=canal), \
             patch.object(bot, "jira_get_sprint_issues", return_value=issues):
            await bot.verificar_atualizacoes_jira(bootstrap=True)
        assert "SCRUM-99" in bot.carregar_estado()
        bot.ESTADO_FILE = original

    @pytest.mark.asyncio
    async def test_transicao_para_review_registra_timestamp(self, tmp_path):
        original = bot.ESTADO_FILE
        bot.ESTADO_FILE = str(tmp_path / "estado.json")
        bot.salvar_estado({"SCRUM-10": {
            "status": "Em Andamento", "summary": "Task",
            "assignee": "Dev", "updated": "2026-06-01T10:00:00.000Z",
            "em_review_desde": None,
        }})
        issues = [{"key": "SCRUM-10", "fields": {
            "summary": "Task", "status": {"name": "In Review"},
            "assignee": {"displayName": "Dev"},
            "updated": "2026-06-05T10:00:00.000Z",
            "priority": {"name": "Medium"},
        }}]
        canal = AsyncMock()
        with patch.object(bot, "get_canal_alertas", return_value=canal), \
             patch.object(bot, "jira_get_sprint_issues", return_value=issues):
            await bot.verificar_atualizacoes_jira(bootstrap=False)
        assert bot.carregar_estado()["SCRUM-10"]["em_review_desde"] is not None
        bot.ESTADO_FILE = original

    @pytest.mark.asyncio
    async def test_mantem_timestamp_existente_em_review(self, tmp_path):
        original = bot.ESTADO_FILE
        bot.ESTADO_FILE = str(tmp_path / "estado.json")
        data_original = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        bot.salvar_estado({"SCRUM-20": {
            "status": "In Review", "summary": "Task",
            "assignee": "Dev", "updated": "2026-06-03T10:00:00.000Z",
            "em_review_desde": data_original,
        }})
        issues = [{"key": "SCRUM-20", "fields": {
            "summary": "Task", "status": {"name": "In Review"},
            "assignee": {"displayName": "Dev"},
            "updated": "2026-06-03T10:00:00.000Z",
            "priority": {"name": "Medium"},
        }}]
        canal = AsyncMock()
        with patch.object(bot, "get_canal_alertas", return_value=canal), \
             patch.object(bot, "jira_get_sprint_issues", return_value=issues):
            await bot.verificar_atualizacoes_jira(bootstrap=False)
        assert bot.carregar_estado()["SCRUM-20"]["em_review_desde"] == data_original
        bot.ESTADO_FILE = original

    @pytest.mark.asyncio
    async def test_mudanca_de_status_posta_embed(self, tmp_path):
        original = bot.ESTADO_FILE
        bot.ESTADO_FILE = str(tmp_path / "estado.json")
        bot.salvar_estado({"SCRUM-30": {
            "status": "Em Andamento", "summary": "Task",
            "assignee": "Dev", "updated": "2026-06-01T00:00:00.000Z",
            "em_review_desde": None,
        }})
        issues = [{"key": "SCRUM-30", "fields": {
            "summary": "Task", "status": {"name": "Concluído"},
            "assignee": {"displayName": "Dev"},
            "updated": "2026-06-06T00:00:00.000Z",
            "priority": {"name": "Medium"},
        }}]
        canal = AsyncMock()
        with patch.object(bot, "get_canal_alertas", return_value=canal), \
             patch.object(bot, "jira_get_sprint_issues", return_value=issues):
            n = await bot.verificar_atualizacoes_jira(bootstrap=False)
        assert n == 1
        canal.send.assert_called_once()
        bot.ESTADO_FILE = original


# =============================================================================
# verificar_em_review — extras
# =============================================================================

class TestVerificarEmReviewExtras:

    @pytest.mark.asyncio
    async def test_multiplas_tasks_paradas(self, tmp_path):
        original = bot.ESTADO_FILE
        bot.ESTADO_FILE = str(tmp_path / "estado.json")
        desde = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        bot.salvar_estado({
            "SCRUM-A": {"status": "In Review", "summary": "Task A",
                        "assignee": "Dev A", "em_review_desde": desde},
            "SCRUM-B": {"status": "In Review", "summary": "Task B",
                        "assignee": "Dev B", "em_review_desde": desde},
        })
        canal = AsyncMock()
        with patch.object(bot, "get_canal_alertas", return_value=canal):
            await bot.verificar_em_review()
        canal.send.assert_called_once()
        # Inspeciona o embed enviado diretamente
        embed = canal.send.call_args.kwargs.get("embed") or canal.send.call_args[1].get("embed")
        assert embed is not None
        fields_text = " ".join(f.name for f in embed.fields)
        assert "SCRUM-A" in fields_text or "SCRUM-B" in fields_text
        bot.ESTADO_FILE = original

    @pytest.mark.asyncio
    async def test_task_sem_timestamp_ignorada(self, tmp_path):
        original = bot.ESTADO_FILE
        bot.ESTADO_FILE = str(tmp_path / "estado.json")
        bot.salvar_estado({"SCRUM-C": {
            "status": "In Review", "summary": "Task C",
            "assignee": "Dev", "em_review_desde": None,
        }})
        canal = AsyncMock()
        with patch.object(bot, "get_canal_alertas", return_value=canal):
            await bot.verificar_em_review()
        canal.send.assert_not_called()
        bot.ESTADO_FILE = original