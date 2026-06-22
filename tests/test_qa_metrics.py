"""
Testes adicionais — qa_metrics.py
===================================
Complementa test_qa.py existente focando nas linhas descobertas:
  - get_sprint_id        (linhas ~148-198)
  - calcular_metricas_jira (~222, 269-300)
  - get_sprint_dates     (~304-376)
  - calcular_metricas_branches_commits (~390-421)
  - calcular_metricas_github (~426-488)
  - rodar_cobertura      (~498-558)
  - imprimir_relatorio   (~584-635)
  - _validar_config, padrões regex

Rodar:
    coverage run -m pytest tests/ -v
    coverage report -m
"""

import pytest
import os
import sys
import subprocess
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

# =============================================================================
# HELPERS
# =============================================================================

def _resp(status_code=200, json_data=None, text=""):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data or {}
    r.text = text
    return r

def _board_resp():
    return {"values": [{"id": 1, "name": "SCRUM Board", "location": {"projectKey": "SCRUM"}}]}

def _sprint_resp():
    return {"values": [{"id": 42, "name": "Sprint 4",
                        "startDate": "2026-06-01T00:00:00.000Z",
                        "endDate":   "2026-06-14T23:59:59.000Z"}]}

def _sprint_dates():
    from dateutil import parser as dp
    s = dp.parse("2026-06-01T00:00:00Z").replace(tzinfo=timezone.utc)
    e = dp.parse("2026-06-14T23:59:59Z").replace(tzinfo=timezone.utc)
    return s, e


# =============================================================================
# get_sprint_id
# =============================================================================

class TestGetSprintId:

    def _side_effect(self, board_data, sprint_data):
        def fake(url, **kw):
            if "board" in url and "sprint" not in url:
                return _resp(200, board_data)
            return _resp(200, sprint_data)
        return fake

    def test_retorna_id_quando_sprint_encontrada(self):
        with patch("requests.get", side_effect=self._side_effect(_board_resp(), _sprint_resp())):
            assert qa.get_sprint_id() == 42

    def test_retorna_none_quando_board_falha(self):
        with patch("requests.get", return_value=_resp(401, text="Unauthorized")):
            assert qa.get_sprint_id() is None

    def test_retorna_none_quando_sprint_nao_encontrada(self):
        sprint = {"values": [{"id": 99, "name": "Sprint 99"}]}
        with patch("requests.get", side_effect=self._side_effect(_board_resp(), sprint)):
            assert qa.get_sprint_id() is None

    def test_usa_primeiro_board_quando_projeto_nao_bate(self):
        board = {"values": [{"id": 7, "name": "Outro", "location": {"projectKey": "OUTRO"}}]}
        with patch("requests.get", side_effect=self._side_effect(board, _sprint_resp())):
            assert qa.get_sprint_id() == 42

    def test_retorna_none_quando_sem_boards(self):
        with patch("requests.get", side_effect=self._side_effect({"values": []}, _sprint_resp())):
            assert qa.get_sprint_id() is None

    def test_retorna_none_quando_sprint_api_falha(self):
        def fake(url, **kw):
            if "board" in url and "sprint" not in url:
                return _resp(200, _board_resp())
            return _resp(500, text="Server Error")
        with patch("requests.get", side_effect=fake):
            assert qa.get_sprint_id() is None


# =============================================================================
# calcular_metricas_jira
# =============================================================================

class TestCalcularMetricasJira:

    def _issue(self, key="SCRUM-1", descricao=None, com_criterio=False):
        desc = descricao if descricao is not None else {
            "type": "doc", "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "Descrição longa o suficiente para valer."}
            ]}]
        }
        comentarios = []
        if com_criterio:
            comentarios.append({"body": "critério de aceitação: dado que o usuário loga"})
        return {
            "key": key,
            "fields": {
                "summary": f"Task {key}",
                "description": desc,
                "issuetype": {"name": "Story"},
                "comment": {"comments": comentarios},
            }
        }

    def _mock(self, issues):
        def fake(url, **kw):
            if "board" in url and "sprint" not in url:
                return _resp(200, _board_resp())
            if "sprint" in url and "search" not in url and "jql" not in url:
                return _resp(200, _sprint_resp())
            return _resp(200, {"issues": issues})
        return patch("requests.get", side_effect=fake)

    def test_total_tasks(self):
        with self._mock([self._issue(f"SCRUM-{i}") for i in range(5)]):
            r = qa.calcular_metricas_jira()
        assert r["total_tasks"] == 5

    def test_detecta_sem_descricao(self):
        with self._mock([self._issue("SCRUM-1", descricao=""), self._issue("SCRUM-2")]):
            r = qa.calcular_metricas_jira()
        assert r["tasks_sem_descricao"] >= 1

    def test_taxa_descricao_calculada(self):
        with self._mock([self._issue("SCRUM-1"), self._issue("SCRUM-2", descricao="")]):
            r = qa.calcular_metricas_jira()
        assert r["taxa_descricao"] == 50.0

    def test_detecta_criterio_em_comentario(self):
        with self._mock([self._issue("SCRUM-1", com_criterio=True)]):
            r = qa.calcular_metricas_jira()
        assert r["taxa_criterios"] == 100.0

    def test_retorna_none_quando_api_falha(self):
        def fake(url, **kw):
            if "board" in url and "sprint" not in url:
                return _resp(200, _board_resp())
            if "sprint" in url and "search" not in url and "jql" not in url:
                return _resp(200, _sprint_resp())
            return _resp(403, text="Forbidden")
        with patch("requests.get", side_effect=fake):
            r = qa.calcular_metricas_jira()
        assert r["total_tasks"] is None

    def test_usa_fallback_sem_sprint_id(self):
        issues = [self._issue()]
        with patch.object(qa, "get_sprint_id", return_value=None), \
             patch("requests.get", return_value=_resp(200, {"issues": issues})):
            r = qa.calcular_metricas_jira()
        assert r["total_tasks"] == 1


# =============================================================================
# get_sprint_dates
# =============================================================================

class TestGetSprintDates:

    def test_retorna_datas_quando_encontrada(self):
        def fake(url, **kw):
            if "board" in url and "sprint" not in url:
                return _resp(200, _board_resp())
            return _resp(200, _sprint_resp())
        with patch("requests.get", side_effect=fake):
            start, end = qa.get_sprint_dates()
        assert start is not None and end is not None
        assert start.tzinfo is not None

    def test_retorna_none_quando_board_falha(self):
        with patch("requests.get", return_value=_resp(500)):
            start, end = qa.get_sprint_dates()
        assert start is None and end is None

    def test_retorna_none_sem_boards(self):
        with patch("requests.get", return_value=_resp(200, {"values": []})):
            start, end = qa.get_sprint_dates()
        assert start is None and end is None

    def test_retorna_none_sprint_nao_encontrada(self):
        sprint = {"values": [{"id": 99, "name": "Sprint 99",
                              "startDate": "2026-01-01T00:00:00.000Z",
                              "endDate":   "2026-01-14T23:59:59.000Z"}]}
        def fake(url, **kw):
            if "board" in url and "sprint" not in url:
                return _resp(200, _board_resp())
            return _resp(200, sprint)
        with patch("requests.get", side_effect=fake):
            start, end = qa.get_sprint_dates()
        assert start is None and end is None


# =============================================================================
# calcular_metricas_branches_commits
# =============================================================================

class TestCalcularMetricasBranchesCommits:

    def _pr(self, branch, merged_at="2026-06-05T10:00:00Z"):
        return {"head": {"ref": branch}, "merged_at": merged_at, "number": 1}

    def _commit(self, msg, sha=None):
        return {"sha": sha or msg[:8].replace(" ", "_"), "commit": {"message": msg}}

    def _patch_github(self, prs, commits):
        """
        Retorna side_effect que serve os mesmos prs/commits para qualquer repo.
        Usa contador para detectar paginação: segunda chamada ao mesmo path retorna [].
        """
        call_counts = {}

        def fake(url, **kw):
            key = url.split("github.com/repos/")[1] if "repos/" in url else url
            call_counts[key] = call_counts.get(key, 0) + 1
            # paginação: segunda chamada retorna lista vazia para parar
            if call_counts[key] > 1:
                return _resp(200, [])
            if "pulls" in url:
                return _resp(200, prs)
            if "commits" in url:
                return _resp(200, commits)
            return _resp(200, [])

        return patch("requests.get", side_effect=fake)

    def test_branch_correta_100_pct(self):
        prs = [self._pr("SCRUM-71-implement-feature")]
        commits = [self._commit("feat: #SCRUM-71 implement feature", "abc12345")]
        with self._patch_github(prs, commits):
            r = qa.calcular_metricas_branches_commits(*_sprint_dates())
        assert r["taxa_branches"] == 100.0

    def test_branch_invalida_reduz_taxa(self):
        prs = [self._pr("minha-feature-sem-scrum")]
        commits = [self._commit("feat: #SCRUM-1 test", "aaa11111")]
        with self._patch_github(prs, commits):
            r = qa.calcular_metricas_branches_commits(*_sprint_dates())
        assert r["taxa_branches"] < 100.0
        assert r["branches_invalidas"] >= 1

    def test_commit_convencional_aceito(self):
        prs = [self._pr("SCRUM-1-feat")]
        commits = [self._commit("feat: #SCRUM-1 adicionar funcionalidade X", "bbb22222")]
        with self._patch_github(prs, commits):
            r = qa.calcular_metricas_branches_commits(*_sprint_dates())
        assert r["taxa_commits"] == 100.0

    def test_commit_scrum_direto_aceito(self):
        prs = [self._pr("SCRUM-75-infra")]
        commits = [self._commit("Scrum 75 migrar infraestrutura para aws", "ccc33333")]
        with self._patch_github(prs, commits):
            r = qa.calcular_metricas_branches_commits(*_sprint_dates())
        assert r["taxa_commits"] == 100.0

    def test_commit_merge_ignorado(self):
        prs = [self._pr("SCRUM-1-feat")]
        commits = [
            self._commit("Merge branch 'main' into dev", "merge000"),
            self._commit("feat: #SCRUM-1 real commit",   "real1111"),
        ]
        with self._patch_github(prs, commits):
            r = qa.calcular_metricas_branches_commits(*_sprint_dates())
        assert r["taxa_commits"] == 100.0

    def test_pr_fora_da_sprint_ignorado(self):
        prs = [self._pr("SCRUM-1-feat", merged_at="2025-01-01T00:00:00Z")]
        with self._patch_github(prs, []):
            r = qa.calcular_metricas_branches_commits(*_sprint_dates())
        assert r["total_branches"] == 0
        assert r["taxa_branches"] is None

    def test_branches_ignoradas_nao_contam(self):
        """main e dev devem ser puladas; só a branch SCRUM conta."""
        prs = [
            self._pr("main"),
            self._pr("dev"),
            self._pr("SCRUM-1-feat"),
        ]
        with self._patch_github(prs, []):
            r = qa.calcular_metricas_branches_commits(*_sprint_dates())
        assert r["total_branches"] == 1
        assert r["taxa_branches"] == 100.0

    def test_sem_sprint_datas_aceita_todos(self):
        prs = [self._pr("SCRUM-1-feat")]
        commits = [self._commit("feat: #SCRUM-1 test", "ddd44444")]
        with self._patch_github(prs, commits):
            r = qa.calcular_metricas_branches_commits(None, None)
        assert r["total_commits"] >= 0

    def test_commit_invalido_reduz_taxa(self):
        prs = [self._pr("SCRUM-1-feat")]
        commits = [
            self._commit("feat: #SCRUM-1 valido",  "good1111"),
            self._commit("adicionei coisa aleatoria", "bad11111"),
        ]
        with self._patch_github(prs, commits):
            r = qa.calcular_metricas_branches_commits(*_sprint_dates())
        assert r["taxa_commits"] == 50.0
        assert r["commits_invalidos"] == 1


# =============================================================================
# calcular_metricas_github
# =============================================================================

class TestCalcularMetricasGithub:

    def _run(self, status="completed", conclusion="success",
             created="2026-06-05T08:00:00Z", updated="2026-06-05T08:02:00Z"):
        return {"status": status, "conclusion": conclusion,
                "created_at": created, "updated_at": updated}

    def _pr(self, number=1, created="2026-06-05T08:00:00Z", merged="2026-06-05T10:00:00Z"):
        return {"number": number, "created_at": created, "merged_at": merged}

    def test_retorna_none_sem_token(self):
        with patch.object(qa, "GITHUB_TOKEN", ""):
            r = qa.calcular_metricas_github(*_sprint_dates())
        assert r is None

    def test_taxa_sucesso_50_pct(self):
        runs = [self._run("completed", "success"), self._run("completed", "failure")]

        def fake(url, **kw):
            if "actions/runs" in url:
                return _resp(200, {"workflow_runs": runs})
            if "reviews" in url:
                return _resp(200, [])
            return _resp(200, [])

        with patch("requests.get", side_effect=fake):
            r = qa.calcular_metricas_github(*_sprint_dates())
        assert r["taxa_sucesso_pipeline"] == 50.0

    def test_tempo_medio_revisao(self):
        prs = [self._pr(1, "2026-06-05T08:00:00Z", "2026-06-05T10:00:00Z")]  # 2h

        def fake(url, **kw):
            if "actions/runs" in url:
                return _resp(200, {"workflow_runs": []})
            if "reviews" in url:
                return _resp(200, [])
            return _resp(200, prs)

        with patch("requests.get", side_effect=fake):
            r = qa.calcular_metricas_github(*_sprint_dates())
        assert r["tempo_medio_revisao_pr"] == 2.0

    def test_pr_com_changes_requested_nao_aprovado(self):
        prs = [self._pr(1), self._pr(2)]
        reviews_rejected = [{"state": "CHANGES_REQUESTED"}]
        call_n = {"n": 0}

        def fake(url, **kw):
            if "actions/runs" in url:
                return _resp(200, {"workflow_runs": []})
            if "reviews" in url:
                call_n["n"] += 1
                return _resp(200, reviews_rejected if call_n["n"] % 2 == 0 else [])
            return _resp(200, prs)

        with patch("requests.get", side_effect=fake):
            r = qa.calcular_metricas_github(*_sprint_dates())
        assert r["taxa_aprovacao_pr"] == 50.0

    def test_sem_runs_concluidos_retorna_none_pipeline(self):
        def fake(url, **kw):
            if "actions/runs" in url:
                return _resp(200, {"workflow_runs": [self._run("in_progress", None)]})
            return _resp(200, [])

        with patch("requests.get", side_effect=fake):
            r = qa.calcular_metricas_github(*_sprint_dates())
        assert r["taxa_sucesso_pipeline"] is None

    def test_erro_actions_nao_quebra(self):
        def fake(url, **kw):
            if "actions/runs" in url:
                return _resp(403)
            return _resp(200, [])

        with patch("requests.get", side_effect=fake):
            r = qa.calcular_metricas_github(*_sprint_dates())
        assert r is not None

    def test_tempo_medio_pipeline_calculado(self):
        runs = [self._run("completed", "success",
                          "2026-06-05T08:00:00Z", "2026-06-05T08:01:00Z")]  # 1 min

        def fake(url, **kw):
            if "actions/runs" in url:
                return _resp(200, {"workflow_runs": runs})
            return _resp(200, [])

        with patch("requests.get", side_effect=fake):
            r = qa.calcular_metricas_github(*_sprint_dates())
        assert r["tempo_medio_pipeline"] == 1.0


# =============================================================================
# rodar_cobertura
# =============================================================================

class TestRodarCobertura:

    def test_pasta_nao_encontrada_retorna_none(self, capsys):
        with patch("os.path.isdir", return_value=False):
            assert qa.rodar_cobertura() is None
        assert "AVISO" in capsys.readouterr().out

    def test_timeout_retorna_none(self, capsys):
        with patch("os.path.isdir", return_value=True), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pnpm", 180)):
            assert qa.rodar_cobertura() is None

    def test_pnpm_nao_encontrado_retorna_none(self, capsys):
        with patch("os.path.isdir", return_value=True), \
             patch("subprocess.run", side_effect=FileNotFoundError):
            assert qa.rodar_cobertura() is None

    def test_output_vazio_tenta_npx(self):
        mock_pnpm = MagicMock(stdout="")
        mock_npx  = MagicMock(stdout="All files | 90 | 90 | 90 | 90 |")
        with patch("os.path.isdir", return_value=True), \
             patch("subprocess.run", side_effect=[mock_pnpm, mock_npx]) as mock_sub:
            qa.rodar_cobertura()
        assert mock_sub.call_count == 2

    def test_retorna_output_quando_sucesso(self):
        mock_proc = MagicMock(stdout="All files | 90 | 90 | 90 | 90 |")
        with patch("os.path.isdir", return_value=True), \
             patch("subprocess.run", return_value=mock_proc):
            result = qa.rodar_cobertura()
        assert result is not None and "All files" in result


# =============================================================================
# imprimir_relatorio
# =============================================================================

class TestImprimirRelatorio:

    def _jira(self, total=10, sem_desc=1, sem_crit=2):
        return {"total_tasks": total,
                "taxa_descricao": round((total - sem_desc) / total * 100, 1),
                "tasks_sem_descricao": sem_desc,
                "taxa_criterios": round((total - sem_crit) / total * 100, 1),
                "tasks_sem_criterios": sem_crit}

    def _dev(self, tb=100.0, tc=90.0):
        return {"taxa_branches": tb, "taxa_commits": tc}

    def _github(self, tp=95.0, tmp=0.5, tpr=100.0, tr=8.0):
        return {"taxa_sucesso_pipeline": tp, "tempo_medio_pipeline": tmp,
                "taxa_aprovacao_pr": tpr, "tempo_medio_revisao_pr": tr}

    def _cov(self, stmts=88.0, critico=False):
        return {
            "total": {"stmts": stmts, "branch": 80.0, "funcs": 90.0, "lines": 88.0},
            "arquivos": [{"nome": "app/api/login/route.ts", "stmts": stmts,
                          "branch": 80.0, "funcs": 90.0, "lines": 88.0,
                          "uncovered": "10-20" if critico else "", "critico": critico}]
        }

    def test_completo_sem_excecao(self, capsys):
        with patch.object(qa, "detectar_rotas_sem_cobertura", return_value=([], 5)):
            qa.imprimir_relatorio(self._jira(), self._dev(), self._github(), self._cov())
        assert "QA" in capsys.readouterr().out

    def test_com_arquivo_critico(self, capsys):
        with patch.object(qa, "detectar_rotas_sem_cobertura", return_value=(["app/api/users/route.ts"], 2)):
            qa.imprimir_relatorio(self._jira(), self._dev(50.0, 50.0),
                                  self._github(70.0, 5.0, 60.0, 30.0), self._cov(40.0, True))
        assert "❌" in capsys.readouterr().out

    def test_sem_github_data(self, capsys):
        qa.imprimir_relatorio(self._jira(), self._dev(), None, {"total": None, "arquivos": []})
        assert "configurado" in capsys.readouterr().out

    def test_sem_jira_data(self, capsys):
        qa.imprimir_relatorio({"total_tasks": None}, self._dev(), None, {"total": None, "arquivos": []})
        out = capsys.readouterr().out
        assert "possível" in out or "Jira" in out

    def test_sem_dev_data(self, capsys):
        qa.imprimir_relatorio(self._jira(), None, None, {"total": None, "arquivos": []})
        out = capsys.readouterr().out
        assert "branches" in out.lower() or "GITHUB" in out

    def test_sem_cobertura(self, capsys):
        qa.imprimir_relatorio(self._jira(), self._dev(), None, {"total": None, "arquivos": []})
        out = capsys.readouterr().out
        assert "cobertura" in out.lower() or "JEST" in out

    def test_rotas_sem_cobertura_exibidas(self, capsys):
        with patch.object(qa, "detectar_rotas_sem_cobertura",
                          return_value=(["app/api/users/route.ts"], 2)):
            qa.imprimir_relatorio(self._jira(), self._dev(), self._github(), self._cov())
        assert "users" in capsys.readouterr().out


# =============================================================================
# _validar_config
# =============================================================================

class TestValidarConfig:

    def test_avisa_variaveis_ausentes(self, capsys):
        with patch.object(qa, "JIRA_EMAIL", ""), \
             patch.object(qa, "JIRA_TOKEN", ""), \
             patch.object(qa, "GITHUB_TOKEN", ""):
            qa._validar_config()
        assert "ausentes" in capsys.readouterr().out

    def test_sem_aviso_quando_tudo_configurado(self, capsys):
        with patch.object(qa, "JIRA_EMAIL", "a@b.com"), \
             patch.object(qa, "JIRA_TOKEN", "tok"), \
             patch.object(qa, "GITHUB_TOKEN", "gh"):
            qa._validar_config()
        assert "ausentes" not in capsys.readouterr().out


# =============================================================================
# padrões regex
# =============================================================================

class TestPadroesRegex:

    def test_branch_aceita_scrum(self):
        assert qa.BRANCH_PATTERN.match("SCRUM-71-implement-login")
        assert qa.BRANCH_PATTERN.match("scrum-1-fix")

    def test_branch_rejeita_sem_scrum(self):
        assert not qa.BRANCH_PATTERN.match("feature/login")
        assert not qa.BRANCH_PATTERN.match("minha-feature")

    def test_commit_aceita_feat(self):
        assert qa.COMMIT_PATTERN.match("feat: #SCRUM-71 implement stations groups")

    def test_commit_aceita_fix(self):
        assert qa.COMMIT_PATTERN.match("fix: #SCRUM-10 corrigir bug")

    def test_commit_aceita_chore(self):
        assert qa.COMMIT_PATTERN.match("chore: #SCRUM-5 atualizar deps")

    def test_commit_aceita_scrum_direto(self):
        assert qa.COMMIT_PATTERN.match("Scrum 75 migrar infraestrutura para aws")

    def test_commit_rejeita_sem_padrao(self):
        assert not qa.COMMIT_PATTERN.match("adicionei feature")
        assert not qa.COMMIT_PATTERN.match("update")
        assert not qa.COMMIT_PATTERN.match("WIP: stuff")

    def test_branches_ignoradas(self):
        assert "main"    in qa.BRANCHES_IGNORADAS
        assert "dev"     in qa.BRANCHES_IGNORADAS
        assert "master"  in qa.BRANCHES_IGNORADAS
        assert "staging" in qa.BRANCHES_IGNORADAS