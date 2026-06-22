"""
QA Metrics Script - Inception IoT (Tecsus)
===========================================
Coleta métricas de qualidade do Jira e GitHub e exibe um checklist por sprint.

USO:
    pip install requests python-dateutil python-dotenv
    cp .env.example .env   # preencha com seus valores reais
    python qa_metrics.py

O script roda os testes automaticamente. Certifique-se de estar na raiz do
projeto (onde fica a pasta front-end/) ao executar.

Todas as credenciais e configurações sensíveis vêm de variáveis de ambiente
(.env local ou Secrets do GitHub Actions) — nada fica hardcoded no código.
"""

import requests
import base64
import subprocess
import sys
import os
import re
import argparse
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from dateutil import parser as dateparser

# Carrega .env se existir (não falha se python-dotenv não estiver instalado
# ou se o arquivo não existir — útil no GitHub Actions, que injeta env direto)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# =============================================================================
# CONFIG — tudo lido de variáveis de ambiente (.env ou Secrets do CI)
# =============================================================================

def _env(nome, default=""):
    return os.environ.get(nome, default)

def _env_list(nome, default=""):
    """Lê uma variável separada por vírgula e retorna lista, sem espaços extras."""
    valor = _env(nome, default)
    return [v.strip() for v in valor.split(",") if v.strip()]

JIRA_URL      = _env("JIRA_URL", "https://inceptionfatec.atlassian.net")
JIRA_EMAIL    = _env("JIRA_EMAIL")
JIRA_TOKEN    = _env("JIRA_TOKEN")
JIRA_PROJECT  = _env("JIRA_PROJECT", "SCRUM")
SPRINT_NAME   = _env("SPRINT_NAME", "Sprint 4")   # <-- atualize a cada sprint via .env

GITHUB_TOKEN          = _env("GITHUB_TOKEN")
GITHUB_REPOS_PIPELINE = _env_list("GITHUB_REPOS_PIPELINE", "Inception-Fatec/front-end,Inception-Fatec/back-end")
GITHUB_REPOS_PRS      = _env_list("GITHUB_REPOS_PRS", "Inception-Fatec/front-end,Inception-Fatec/back-end")

# Caminho para a pasta do front-end relativo a onde o script é executado
FRONTEND_PATH = _env("FRONTEND_PATH", "../../front-end")

# Branches que devem ser ignoradas na checagem de nomenclatura
BRANCHES_IGNORADAS = {"main", "master", "dev", "develop", "development", "staging", "homolog"}

# Padrão esperado de branch: SCRUM-id-nome-da-task
BRANCH_PATTERN = re.compile(r"^SCRUM-\d+-.+", re.IGNORECASE)

# Padrões de commit aceitos:
# 1. Conventional Commits:  tipo: #SCRUM-id descrição   ex: feat: #SCRUM-71 implement stations groups
# 2. Scrum direto:          Scrum {id} descrição         ex: Scrum 75 migrar infraestrutura para a aws
COMMIT_PATTERN = re.compile(
    r"^(?:"
    r"(feat|fix|docs|style|refactor|test|chore|ci)(\(.+\))?: #SCRUM-\d+ .+"  # Conventional
    r"|Scrum \d+ .+"                                                             # Scrum direto
    r")",
    re.IGNORECASE
)

# Metas de qualidade
METAS = {
    "taxa_branches_corretas"  : 90,   # % de branches no padrão
    "taxa_commits_corretos"   : 90,   # % de commits no padrão Conventional Commits
    "taxa_aprovacao_pr"       : 80,   # % de PRs aprovados sem CHANGES_REQUESTED
    "tempo_revisao_pr_horas"  : 24,   # horas
    "cobertura_testes"        : 70,   # % de statements
    "taxa_sucesso_pipeline"   : 90,   # % de runs com sucesso
    "tempo_pipeline_minutos"  : 1,    # minutos
}

# Email — lido do ambiente, usado por --email
EMAIL_REMETENTE     = _env("EMAIL_REMETENTE")
EMAIL_SENHA         = _env("EMAIL_SENHA")
EMAIL_DESTINATARIOS = _env_list("EMAIL_DESTINATARIOS")
EMAIL_ASSUNTO       = f"[QA] Relatório de Qualidade — {SPRINT_NAME}"

# Discord — webhook do canal, usado por --discord
DISCORD_WEBHOOK     = _env("DISCORD_WEBHOOK")

# =============================================================================
# HELPERS
# =============================================================================

def jira_headers():
    cred = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
    return {"Authorization": f"Basic {cred}", "Content-Type": "application/json"}

def github_headers():
    return {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}

def status(valor, meta, modo="maximo"):
    if valor is None:
        return "⚠️ "
    return "✅" if (valor <= meta if modo == "maximo" else valor >= meta) else "❌"

def github_get(repo, path, params=None):
    url = f"https://api.github.com/repos/{repo}/{path}"
    return requests.get(url, headers=github_headers(), params=params)

def github_get_all_pages(repo, path, params=None):
    """Busca todas as páginas de um endpoint paginado do GitHub."""
    params = params or {}
    params["per_page"] = 100
    resultados = []
    page = 1
    while True:
        params["page"] = page
        r = github_get(repo, path, params)
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        resultados.extend(data)
        if len(data) < 100:
            break
        page += 1
    return resultados

# =============================================================================
# COBERTURA — roda Jest automaticamente e parseia o output
# =============================================================================

def rodar_cobertura():
    """Executa pnpm test --coverage no front-end e retorna o output."""
    caminho_cwd    = os.path.join(os.getcwd(), FRONTEND_PATH)
    caminho_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), FRONTEND_PATH)

    if os.path.isdir(caminho_cwd):
        caminho = caminho_cwd
    elif os.path.isdir(caminho_script):
        caminho = caminho_script
    else:
        print(f"  [AVISO] Pasta '{FRONTEND_PATH}' nao encontrada.")
        print(f"          Tentado em: {caminho_cwd}")
        print(f"          Tentado em: {caminho_script}")
        print(f"          Execute o script a partir da raiz do projeto (onde fica a pasta front-end/).")
        return None

    print(f"  Rodando: pnpm test --coverage em {caminho}")
    try:
        resultado = subprocess.run(
            "pnpm test --coverage --coverageReporters=text --ci --forceExit",
            cwd=caminho,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            shell=True
        )
        output = resultado.stdout or ""
        if not output.strip():
            print("  [DEBUG] Output vazio — tentando comando alternativo...")
            resultado = subprocess.run(
                "npx jest --coverage --coverageReporters=text --ci --forceExit",
                cwd=caminho,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                shell=True
            )
            output = resultado.stdout or ""
        if not output.strip():
            print("  [ERRO] Nenhum output capturado. Verifique se o Jest está configurado no projeto.")
        return output
    except FileNotFoundError:
        print("  [ERRO] 'pnpm' não encontrado. Certifique-se de que o pnpm está instalado e no PATH.")
        return None
    except subprocess.TimeoutExpired:
        print("  [ERRO] Timeout ao rodar os testes (>180s).")
        return None

def parse_cobertura(output):
    """Parseia o output do Jest --coverage e retorna totais e arquivos críticos."""
    resultado = {"total": None, "arquivos": []}
    if not output:
        return resultado

    meta = METAS["cobertura_testes"]
    output = output.replace("\r\n", "\n").replace("\r", "\n")

    def extrair_float(s):
        m = re.search(r"[\d]+(?:\.[\d]+)?", s)
        return float(m.group()) if m else None

    diretorio_atual = ""

    for linha in output.splitlines():
        linha_strip = linha.strip()
        if not linha_strip or "|" not in linha_strip:
            continue

        partes = [p.strip() for p in linha_strip.split("|")]
        if len(partes) < 5:
            continue

        nome = partes[0].strip()

        stmts  = extrair_float(partes[1])
        branch = extrair_float(partes[2])
        funcs  = extrair_float(partes[3])
        lines  = extrair_float(partes[4])

        tem_extensao = bool(re.search(r"\.[a-z]+x?$", nome, re.IGNORECASE))

        if stmts is None:
            if nome and "%" not in nome and "Stmts" not in nome and "-----" not in nome and not tem_extensao:
                diretorio_atual = nome
            continue

        if "All files" in nome:
            resultado["total"] = {
                "stmts" : stmts,
                "branch": branch if branch is not None else 0,
                "funcs" : funcs  if funcs  is not None else 0,
                "lines" : lines  if lines  is not None else 0,
            }
            diretorio_atual = ""
        elif tem_extensao:
            nome_completo = f"{diretorio_atual}/{nome}" if diretorio_atual else nome
            uncovered = partes[5].strip() if len(partes) > 5 else ""
            resultado["arquivos"].append({
                "nome"     : nome_completo,
                "stmts"    : stmts,
                "branch"   : branch if branch is not None else 0,
                "funcs"    : funcs  if funcs  is not None else 0,
                "lines"    : lines  if lines  is not None else 0,
                "uncovered": uncovered,
                "critico"  : stmts < meta,
            })
        else:
            diretorio_atual = nome

    return resultado

# =============================================================================
# JIRA — tasks com descrição e critérios de aceitação
# =============================================================================

def get_sprint_id():
    """Busca o ID da sprint atual pelo nome."""
    r = requests.get(f"{JIRA_URL}/rest/agile/1.0/board", headers=jira_headers())
    if r.status_code != 200:
        print(f"  [ERRO Jira boards] {r.status_code}: {r.text[:200]}")
        return None

    boards = r.json().get("values", [])
    board_id = None
    for b in boards:
        proj = b.get("location", {}).get("projectKey", "")
        if JIRA_PROJECT.lower() in b.get("name", "").lower() or JIRA_PROJECT.lower() in proj.lower():
            board_id = b["id"]
            break
    if not board_id and boards:
        board_id = boards[0]["id"]
    if not board_id:
        print("  [ERRO] Nenhum board encontrado no Jira.")
        return None

    r = requests.get(f"{JIRA_URL}/rest/agile/1.0/board/{board_id}/sprint", headers=jira_headers())
    if r.status_code != 200:
        print(f"  [ERRO Jira sprints] {r.status_code}: {r.text[:200]}")
        return None

    sprints = r.json().get("values", [])
    for sprint in sprints:
        # Use exact-name match (case-insensitive, trimmed) to avoid partial matches
        if sprint.get("name", "").strip().lower() == SPRINT_NAME.strip().lower():
            return sprint["id"]

    print(f"  [AVISO] Sprint '{SPRINT_NAME}' não encontrada. Sprints disponíveis:")
    for sprint in sprints:
        print(f"    - {sprint['name']} (id: {sprint['id']})")
    return None

def calcular_metricas_jira():
    """Coleta métricas do PO: % de tasks com descrição e com critérios de aceitação."""
    print("\n📋 Coletando dados do Jira...")
    sprint_id = get_sprint_id()

    if sprint_id:
        jql_base = f'project = {JIRA_PROJECT} AND sprint = {sprint_id}'
    else:
        jql_base = f'project = {JIRA_PROJECT} AND sprint in openSprints()'
        print("  [AVISO] Usando fallback: sprint aberta atual.")

    url = f"{JIRA_URL}/rest/api/3/search/jql"
    params = {
        "jql": jql_base,
        "fields": "summary,description,comment,issuetype",
        "maxResults": 100
    }
    r = requests.get(url, headers=jira_headers(), params=params)

    if r.status_code != 200:
        print(f"  [ERRO Jira tasks] {r.status_code}: {r.text[:300]}")
        return {
            "total_tasks": None,
            "tasks_sem_descricao": None,
            "taxa_descricao": None,
            "tasks_sem_criterios": None,
            "taxa_criterios": None,
        }

    issues = r.json().get("issues", [])
    total = len(issues)
    sem_descricao = 0
    sem_criterios = 0

    PALAVRAS_CRITERIOS = ["critério", "criterio", "acceptance", "ac:", "dado que", "quando", "então", "entao"]

    for issue in issues:
        fields = issue.get("fields", {})
        desc = fields.get("description")

        tem_descricao = False
        if desc:
            if isinstance(desc, dict):
                conteudo = str(desc)
                tem_descricao = len(conteudo) > 50
            else:
                tem_descricao = bool(str(desc).strip())

        if not tem_descricao:
            sem_descricao += 1

        tem_criterios = False
        texto_completo = str(desc).lower() if desc else ""

        comentarios = fields.get("comment", {}).get("comments", [])
        for c in comentarios:
            body = c.get("body", {})
            texto_completo += " " + str(body).lower()

        for palavra in PALAVRAS_CRITERIOS:
            if palavra in texto_completo:
                tem_criterios = True
                break

        if not tem_criterios:
            sem_criterios += 1

    taxa_desc = round(((total - sem_descricao) / total) * 100, 1) if total else None
    taxa_crit = round(((total - sem_criterios) / total) * 100, 1) if total else None

    print(f"  Total de tasks na sprint: {total}")
    print(f"  Tasks sem descrição: {sem_descricao}")
    print(f"  Tasks sem critérios de aceitação detectados: {sem_criterios}")

    return {
        "total_tasks"       : total,
        "tasks_sem_descricao": sem_descricao,
        "taxa_descricao"    : taxa_desc,
        "tasks_sem_criterios": sem_criterios,
        "taxa_criterios"    : taxa_crit,
    }

# =============================================================================
# GITHUB — Branches, Commits, Pipeline e Pull Requests
# =============================================================================

def get_sprint_dates():
    """Retorna (start_date, end_date) da sprint como objetos datetime com timezone."""
    r = requests.get(f"{JIRA_URL}/rest/agile/1.0/board", headers=jira_headers())
    if r.status_code != 200:
        return None, None

    boards = r.json().get("values", [])
    board_id = None
    for b in boards:
        proj = b.get("location", {}).get("projectKey", "")
        if JIRA_PROJECT.lower() in b.get("name", "").lower() or JIRA_PROJECT.lower() in proj.lower():
            board_id = b["id"]
            break
    if not board_id and boards:
        board_id = boards[0]["id"]
    if not board_id:
        return None, None

    r = requests.get(f"{JIRA_URL}/rest/agile/1.0/board/{board_id}/sprint", headers=jira_headers())
    if r.status_code != 200:
        return None, None

    for sprint in r.json().get("values", []):
        # Match sprint name exactly (case-insensitive, trimmed)
        if sprint.get("name", "").strip().lower() == SPRINT_NAME.strip().lower():
            start = dateparser.parse(sprint["startDate"]) if sprint.get("startDate") else None
            end   = dateparser.parse(sprint["endDate"])   if sprint.get("endDate")   else None
            if start and start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end and end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            print(f"  Sprint encontrada: {sprint['name']} | {start} → {end}")
            return start, end

    return None, None


def calcular_metricas_branches_commits(sprint_start, sprint_end):
    """Verifica nomenclatura de branches e commits dentro do período da sprint."""
    print("\n🌿 Verificando branches e commits da sprint...")

    total_branches = 0
    branches_invalidas = 0
    total_commits = 0
    commits_invalidos = 0

    for repo in GITHUB_REPOS_PIPELINE:
        prs_todos = github_get_all_pages(repo, "pulls", {"state": "closed"})
        prs_sprint = []
        for pr in prs_todos:
            merged_at = pr.get("merged_at")
            if not merged_at:
                continue
            merged_dt = dateparser.parse(merged_at)
            if merged_dt.tzinfo is None:
                merged_dt = merged_dt.replace(tzinfo=timezone.utc)
            if sprint_start and sprint_end:
                if not (sprint_start <= merged_dt <= sprint_end):
                    continue
            prs_sprint.append(pr)

        for pr in prs_sprint:
            branch_name = pr.get("head", {}).get("ref", "")
            if not branch_name or branch_name.lower() in BRANCHES_IGNORADAS:
                continue
            total_branches += 1
            if not BRANCH_PATTERN.match(branch_name):
                branches_invalidas += 1

        print(f"  [{repo}] {len(prs_sprint)} PRs na sprint → {total_branches} branches verificadas")

        params_commits = {}
        if sprint_start:
            params_commits["since"] = sprint_start.isoformat()
        if sprint_end:
            params_commits["until"] = sprint_end.isoformat()

        vistos = set()
        commits_repo = []
        for branch in ["main", "dev"]:
            params_branch = {**params_commits, "sha": branch}
            branch_commits = github_get_all_pages(repo, "commits", params_branch)
            for c in branch_commits:
                sha = c.get("sha", "")
                if sha and sha not in vistos:
                    vistos.add(sha)
                    commits_repo.append(c)

        for c in commits_repo:
            msg = c.get("commit", {}).get("message", "").split("\n")[0].strip()
            if msg.lower().startswith("merge "):
                continue
            total_commits += 1
            if not COMMIT_PATTERN.match(msg):
                commits_invalidos += 1

        print(f"  [{repo}] {len(commits_repo)} commits na sprint verificados (main + dev, sem duplicatas)")

    taxa_branches = round(((total_branches - branches_invalidas) / total_branches) * 100, 1) if total_branches > 0 else None
    taxa_commits  = round(((total_commits  - commits_invalidos)  / total_commits)  * 100, 1) if total_commits  > 0 else None

    return {
        "total_branches"    : total_branches,
        "branches_invalidas": branches_invalidas,
        "taxa_branches"     : taxa_branches,
        "total_commits"     : total_commits,
        "commits_invalidos" : commits_invalidos,
        "taxa_commits"      : taxa_commits,
    }

def calcular_metricas_github(sprint_start, sprint_end):
    if not GITHUB_TOKEN:
        return None
    print("\n🐙 Coletando dados do GitHub (pipeline e PRs)...")

    def dentro_da_sprint(dt_str):
        if not dt_str:
            return False
        dt = dateparser.parse(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if sprint_start and sprint_end:
            return sprint_start <= dt <= sprint_end
        return True

    todos_runs = []
    for repo in GITHUB_REPOS_PIPELINE:
        params_runs = {"per_page": 100}
        if sprint_start:
            params_runs["created"] = f">={sprint_start.strftime('%Y-%m-%d')}"
        r = github_get(repo, "actions/runs", params_runs)
        if r.status_code == 200:
            runs = [run for run in r.json().get("workflow_runs", [])
                    if dentro_da_sprint(run.get("created_at"))]
            todos_runs.extend(runs)
            print(f"  [{repo}] {len(runs)} runs na sprint")
        else:
            print(f"  [ERRO Actions {repo}] {r.status_code}")

    concluidos = [run for run in todos_runs if run["status"] == "completed"]
    taxa_sucesso = tempo_medio_pipeline = None
    if concluidos:
        sucessos = sum(1 for r in concluidos if r["conclusion"] == "success")
        taxa_sucesso = round((sucessos / len(concluidos)) * 100, 1)
        duracoes = [
            (dateparser.parse(r["updated_at"]) - dateparser.parse(r["created_at"])).total_seconds() / 60
            for r in concluidos if r.get("created_at") and r.get("updated_at")
        ]
        if duracoes:
            tempo_medio_pipeline = round(sum(duracoes) / len(duracoes), 1)

    todos_merged, aprovados_sem_changes, tempos_revisao = [], 0, []
    for repo in GITHUB_REPOS_PRS:
        prs_todos = github_get_all_pages(repo, "pulls", {"state": "closed"})
        merged = [pr for pr in prs_todos
                  if pr.get("merged_at") and dentro_da_sprint(pr["merged_at"])]
        print(f"  [{repo}] {len(merged)} PRs merged na sprint")
        todos_merged.extend(merged)

        for pr in merged:
            rr = github_get(repo, f"pulls/{pr['number']}/reviews")
            if rr.status_code == 200:
                if not any(rv["state"] == "CHANGES_REQUESTED" for rv in rr.json()):
                    aprovados_sem_changes += 1
            if pr.get("created_at") and pr.get("merged_at"):
                horas = (dateparser.parse(pr["merged_at"]) - dateparser.parse(pr["created_at"])).total_seconds() / 3600
                tempos_revisao.append(horas)

    taxa_aprovacao      = round((aprovados_sem_changes / len(todos_merged)) * 100, 1) if todos_merged else None
    tempo_medio_revisao = round(sum(tempos_revisao)    / len(tempos_revisao),    1) if tempos_revisao else None

    return {
        "taxa_sucesso_pipeline"  : taxa_sucesso,
        "tempo_medio_pipeline"   : tempo_medio_pipeline,
        "taxa_aprovacao_pr"      : taxa_aprovacao,
        "tempo_medio_revisao_pr" : tempo_medio_revisao,
    }

# =============================================================================
# DETECÇÃO DE ROTAS SEM COBERTURA
# =============================================================================

def detectar_rotas_sem_cobertura(cov_data):
    """Compara os route.ts existentes em FRONTEND_PATH/app/api com os do Jest."""
    base_cwd    = os.path.join(os.getcwd(), FRONTEND_PATH)
    base_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), FRONTEND_PATH)
    base        = base_cwd if os.path.isdir(base_cwd) else base_script

    caminho_api = os.path.join(base, "app", "api")
    if not os.path.isdir(caminho_api):
        return [], 0

    def normalizar_para_api(path):
        p = path.replace(os.sep, "/")
        idx = p.find("/api/")
        if idx != -1:
            return p[idx + 5:]
        parts = p.rstrip("/").split("/")
        return "/".join(parts[-2:]) if len(parts) >= 2 else p

    rotas_existentes = {}
    for root, dirs, files in os.walk(caminho_api):
        for f in files:
            if f.startswith("route."):
                abs_path  = os.path.join(root, f)
                rel_display = os.path.relpath(abs_path, base).replace(os.sep, "/")
                rel_norm    = normalizar_para_api(abs_path)
                rotas_existentes[rel_norm] = rel_display

    cobertas_norm = {normalizar_para_api(arq["nome"]) for arq in cov_data.get("arquivos", [])}

    sem_cobertura = sorted(
        display for norm, display in rotas_existentes.items()
        if norm not in cobertas_norm
    )
    return sem_cobertura, len(rotas_existentes)


# =============================================================================
# RELATÓRIO — geração de texto, email e Discord
# =============================================================================

def gerar_linhas_relatorio(jira_data, dev_data, github_data, cov_data):
    """Retorna uma lista de strings com o relatório completo (sem emojis de cor)."""
    linhas = []
    sep = "=" * 60

    linhas += [sep, f"  RELATÓRIO QA — {SPRINT_NAME.upper()}",
               f"  Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}", sep]

    linhas.append("\nJIRA — Métricas do PO")
    total = jira_data.get("total_tasks")
    if total is None:
        linhas.append("  ⚠️  Não foi possível coletar tasks do Jira.")
    else:
        linhas.append(f"  Total de tasks na sprint: {total}")
        taxa_desc = jira_data.get("taxa_descricao")
        sem_desc  = jira_data.get("tasks_sem_descricao", 0)
        linhas.append(f"  {status(taxa_desc, 80, 'minimo')} Tasks com descrição: "
                      f"{taxa_desc if taxa_desc is not None else 'N/A'}% "
                      f"({sem_desc} sem descrição | meta: ≥ 80%)")
        taxa_crit = jira_data.get("taxa_criterios")
        sem_crit  = jira_data.get("tasks_sem_criterios", 0)
        linhas.append(f"  {status(taxa_crit, 80, 'minimo')} Tasks com critérios de aceitação: "
                      f"{taxa_crit if taxa_crit is not None else 'N/A'}% "
                      f"({sem_crit} sem critérios | meta: ≥ 80%)")

    linhas.append("\nGITHUB — Branches e Commits")
    if not dev_data:
        linhas.append("  ⚠️  Não foi possível coletar dados de branches/commits.")
    else:
        taxa_b = dev_data["taxa_branches"]
        linhas.append(f"  {status(taxa_b, METAS['taxa_branches_corretas'], 'minimo')} Branches no padrão: "
                      f"{taxa_b if taxa_b is not None else 'N/A'}% (meta: ≥ {METAS['taxa_branches_corretas']}%)")
        taxa_c = dev_data["taxa_commits"]
        linhas.append(f"  {status(taxa_c, METAS['taxa_commits_corretos'], 'minimo')} Commits no padrão: "
                      f"{taxa_c if taxa_c is not None else 'N/A'}% (meta: ≥ {METAS['taxa_commits_corretos']}%)")

    linhas.append("\nGITHUB — Pipeline & Pull Requests")
    if not github_data:
        linhas.append("  ⚠️  Token do GitHub não configurado.")
    else:
        taxa_pip   = github_data["taxa_sucesso_pipeline"]
        tempo_pip  = github_data["tempo_medio_pipeline"]
        taxa_pr    = github_data["taxa_aprovacao_pr"]
        tempo_pr   = github_data["tempo_medio_revisao_pr"]
        linhas.append(f"  {status(taxa_pip, METAS['taxa_sucesso_pipeline'], 'minimo')} Taxa sucesso pipeline: "
                      f"{taxa_pip if taxa_pip is not None else 'N/A'}% (meta: ≥ {METAS['taxa_sucesso_pipeline']}%)")
        linhas.append(f"  {status(tempo_pip, METAS['tempo_pipeline_minutos'], 'maximo')} Tempo médio pipeline: "
                      f"{tempo_pip if tempo_pip is not None else 'N/A'} min (meta: ≤ {METAS['tempo_pipeline_minutos']} min)")
        linhas.append(f"  {status(taxa_pr, METAS['taxa_aprovacao_pr'], 'minimo')} Taxa aprovação de PR: "
                      f"{taxa_pr if taxa_pr is not None else 'N/A'}% (meta: ≥ {METAS['taxa_aprovacao_pr']}%)")
        linhas.append(f"  {status(tempo_pr, METAS['tempo_revisao_pr_horas'], 'maximo')} Tempo médio revisão PR: "
                      f"{tempo_pr if tempo_pr is not None else 'N/A'}h (meta: ≤ {METAS['tempo_revisao_pr_horas']}h)")

    linhas.append("\nJEST — Cobertura de Testes")
    meta_cov = METAS["cobertura_testes"]
    if cov_data and cov_data["total"]:
        t = cov_data["total"]
        linhas.append(f"  {status(t['stmts'], meta_cov, 'minimo')} Cobertura geral (Statements): "
                      f"{t['stmts']}% (meta: ≥ {meta_cov}%)")
        linhas.append(f"     Branch: {t['branch']}% | Funções: {t['funcs']}% | Linhas: {t['lines']}%")
        criticos = [a for a in cov_data["arquivos"] if a["critico"]]
        if criticos:
            linhas.append(f"  ⚠️  Arquivos abaixo da meta ({meta_cov}%):")
            for arq in criticos:
                uncovered = arq.get("uncovered", "")
                unc_str = f" — linhas: {uncovered}" if uncovered else ""
                linhas.append(f"     ❌ {arq['nome']}: {arq['stmts']}% statements{unc_str}")
        else:
            linhas.append("  ✅ Todos os arquivos acima da meta individual.")
        sem_cobertura, total_rotas = detectar_rotas_sem_cobertura(cov_data)
        arquivos_cobertos = total_rotas - len(sem_cobertura)
        pct_rotas = round((arquivos_cobertos / total_rotas) * 100, 1) if total_rotas else 100.0
        st_rotas = "✅" if pct_rotas >= METAS["cobertura_testes"] else "❌"
        linhas.append(f"  {st_rotas} Rotas com cobertura: {pct_rotas}% ({arquivos_cobertos}/{total_rotas} routes cobertos)")
        if sem_cobertura:
            linhas.append("  🔍 Rotas sem nenhum teste:")
            for rota in sem_cobertura:
                linhas.append(f"     ⬜ {rota}")
    elif cov_data and cov_data.get("sprint_passada"):
        linhas.append("  ℹ️  Cobertura não disponível para sprints anteriores.")
    else:
        linhas.append("  ⚠️  Não foi possível coletar cobertura.")

    linhas += [f"\n{sep}",
               "  Legenda: ✅ Meta atingida  ❌ Meta não atingida  ⚠️  Verificar manualmente",
               sep]
    return linhas


def enviar_email(linhas):
    """Envia o relatório por email via Gmail SMTP."""
    if not EMAIL_REMETENTE or not EMAIL_SENHA or not EMAIL_DESTINATARIOS:
        print("  [AVISO] Email não configurado. Preencha EMAIL_REMETENTE, EMAIL_SENHA e EMAIL_DESTINATARIOS no .env.")
        return

    corpo_texto = "\n".join(linhas)

    def linha_html(l):
        l_esc = l.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if "✅" in l:
            return f'<span style="color:#2e7d32">{l_esc}</span>'
        if "❌" in l:
            return f'<span style="color:#c62828">{l_esc}</span>'
        if "⚠️" in l:
            return f'<span style="color:#e65100">{l_esc}</span>'
        if l.startswith("="):
            return f'<b>{l_esc}</b>'
        return l_esc

    corpo_html = (
        "<html><body><pre style='font-family:monospace;font-size:14px;line-height:1.6'>"
        + "\n".join(linha_html(l) for l in linhas)
        + "</pre></body></html>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = EMAIL_ASSUNTO
    msg["From"]    = EMAIL_REMETENTE
    msg["To"]      = ", ".join(EMAIL_DESTINATARIOS)
    msg.attach(MIMEText(corpo_texto, "plain", "utf-8"))
    msg.attach(MIMEText(corpo_html,  "html",  "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_REMETENTE, EMAIL_SENHA)
            smtp.sendmail(EMAIL_REMETENTE, EMAIL_DESTINATARIOS, msg.as_string())
        print(f"  ✅ Email enviado para: {', '.join(EMAIL_DESTINATARIOS)}")
    except Exception as e:
        print(f"  ❌ Erro ao enviar email: {e}")


def enviar_discord(linhas):
    """Envia o relatório para o Discord via webhook."""
    if not DISCORD_WEBHOOK:
        print("  [AVISO] Discord não configurado. Preencha DISCORD_WEBHOOK no .env.")
        return

    blocos = []
    atual = "```\n"
    for linha in linhas:
        if len(atual) + len(linha) + 5 > 1990:
            blocos.append(atual + "```")
            atual = "```\n"
        atual += linha + "\n"
    if atual.strip() != "```":
        blocos.append(atual + "```")

    for bloco in blocos:
        payload = {"content": bloco}
        r = requests.post(DISCORD_WEBHOOK, json=payload)
        if r.status_code not in (200, 204):
            print(f"  ❌ Erro ao enviar Discord: {r.status_code} {r.text[:100]}")
            return
    print(f"  ✅ Relatório enviado para o Discord ({len(blocos)} mensagem(ns)).")


# =============================================================================
# RELATÓRIO FINAL (console)
# =============================================================================

def imprimir_relatorio(jira_data, dev_data, github_data, cov_data):
    linha = "=" * 60
    print(f"\n{linha}")
    print(f"  📊 CHECKLIST QA — {SPRINT_NAME.upper()}")
    print(f"  Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(linha)

    print("\n🔵 JIRA — Métricas do PO")
    total = jira_data.get("total_tasks")
    if total is None:
        print("  ⚠️  Não foi possível coletar tasks do Jira.")
    else:
        print(f"  Total de tasks na sprint: {total}")
        taxa_desc = jira_data.get("taxa_descricao")
        sem_desc  = jira_data.get("tasks_sem_descricao", 0)
        print(f"  {status(taxa_desc, 80, 'minimo')} Tasks com descrição: "
              f"{taxa_desc if taxa_desc is not None else 'N/A'}% "
              f"({sem_desc} sem descrição | meta: ≥ 80%)")
        taxa_crit = jira_data.get("taxa_criterios")
        sem_crit  = jira_data.get("tasks_sem_criterios", 0)
        print(f"  {status(taxa_crit, 80, 'minimo')} Tasks com critérios de aceitação: "
              f"{taxa_crit if taxa_crit is not None else 'N/A'}% "
              f"({sem_crit} sem critérios | meta: ≥ 80%)")

    print("\n🌿 GITHUB — Branches e Commits")
    if not dev_data:
        print("  ⚠️  Não foi possível coletar dados de branches/commits.")
    else:
        taxa_b = dev_data["taxa_branches"]
        print(f"  {status(taxa_b, METAS['taxa_branches_corretas'], 'minimo')} Branches no padrão: "
              f"{taxa_b if taxa_b is not None else 'N/A'}% (meta: ≥ {METAS['taxa_branches_corretas']}%)")
        taxa_c = dev_data["taxa_commits"]
        print(f"  {status(taxa_c, METAS['taxa_commits_corretos'], 'minimo')} Commits no padrão Conventional: "
              f"{taxa_c if taxa_c is not None else 'N/A'}% (meta: ≥ {METAS['taxa_commits_corretos']}%)")

    print("\n🟣 GITHUB — Pipeline & Pull Requests")
    if not github_data:
        print("  ⚠️  Token do GitHub não configurado.")
    else:
        taxa_pip = github_data["taxa_sucesso_pipeline"]
        print(f"  {status(taxa_pip, METAS['taxa_sucesso_pipeline'], 'minimo')} Taxa de sucesso da pipeline: "
              f"{taxa_pip if taxa_pip is not None else 'N/A'}% (meta: ≥ {METAS['taxa_sucesso_pipeline']}%)")
        tempo_pip = github_data["tempo_medio_pipeline"]
        print(f"  {status(tempo_pip, METAS['tempo_pipeline_minutos'], 'maximo')} Tempo médio da pipeline: "
              f"{tempo_pip if tempo_pip is not None else 'N/A'} min (meta: ≤ {METAS['tempo_pipeline_minutos']} min)")
        taxa_pr = github_data["taxa_aprovacao_pr"]
        print(f"  {status(taxa_pr, METAS['taxa_aprovacao_pr'], 'minimo')} Taxa de aprovação de PR: "
              f"{taxa_pr if taxa_pr is not None else 'N/A'}% (meta: ≥ {METAS['taxa_aprovacao_pr']}%)")
        tempo_pr = github_data["tempo_medio_revisao_pr"]
        print(f"  {status(tempo_pr, METAS['tempo_revisao_pr_horas'], 'maximo')} Tempo médio de revisão de PR: "
              f"{tempo_pr if tempo_pr is not None else 'N/A'}h (meta: ≤ {METAS['tempo_revisao_pr_horas']}h)")

    print("\n🟢 JEST — Cobertura de Testes")
    meta_cov = METAS["cobertura_testes"]
    if cov_data and cov_data["total"]:
        t = cov_data["total"]
        print(f"  {status(t['stmts'], meta_cov, 'minimo')} Cobertura geral (Statements): "
              f"{t['stmts']}% (meta: ≥ {meta_cov}%)")
        print(f"     Branch: {t['branch']}% | Funções: {t['funcs']}% | Linhas: {t['lines']}%")
        criticos = [a for a in cov_data["arquivos"] if a["critico"]]
        if criticos:
            print(f"\n  ⚠️  Arquivos abaixo da meta ({meta_cov}%):")
            for arq in criticos:
                uncovered = arq.get("uncovered", "")
                unc_str = f" — linhas: {uncovered}" if uncovered else ""
                print(f"     ❌ {arq['nome']}: {arq['stmts']}% statements{unc_str}")
        else:
            print("  ✅ Todos os arquivos acima da meta individual.")
        sem_cobertura, total_rotas = detectar_rotas_sem_cobertura(cov_data)
        arquivos_cobertos = total_rotas - len(sem_cobertura)
        pct_rotas = round((arquivos_cobertos / total_rotas) * 100, 1) if total_rotas else 100.0
        st_rotas = "✅" if pct_rotas >= METAS["cobertura_testes"] else "❌"
        print(f"\n  {st_rotas} Rotas com cobertura: {pct_rotas}% ({arquivos_cobertos}/{total_rotas} routes cobertos)")
        if sem_cobertura:
            print(f"  🔍 Rotas sem nenhum teste:")
            for rota in sem_cobertura:
                print(f"     ⬜ {rota}")
    else:
        print(f"  ⚠️  Não foi possível coletar cobertura. Verifique se a pasta '{FRONTEND_PATH}' existe.")

    print(f"\n{linha}")
    print("  Legenda: ✅ Meta atingida  ❌ Meta não atingida  ⚠️  Verificar manualmente")
    print(linha)

# =============================================================================
# MAIN
# =============================================================================

def _validar_config():
    """Avisa (sem travar) sobre variáveis essenciais ausentes."""
    faltando = []
    if not JIRA_EMAIL:   faltando.append("JIRA_EMAIL")
    if not JIRA_TOKEN:   faltando.append("JIRA_TOKEN")
    if not GITHUB_TOKEN: faltando.append("GITHUB_TOKEN")
    if faltando:
        print(f"⚠️  Variáveis de ambiente ausentes: {', '.join(faltando)}")
        print("    Verifique seu arquivo .env (veja .env.example) ou os Secrets do GitHub Actions.\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QA Metrics — Inception IoT")
    parser.add_argument("--email",   action="store_true", help="Envia o relatório por email ao final")
    parser.add_argument("--discord", action="store_true", help="Envia o relatório para o Discord ao final")
    args = parser.parse_args()

    _validar_config()

    print("🚀 Iniciando coleta de métricas QA...")

    jira_data                = calcular_metricas_jira()
    sprint_start, sprint_end = get_sprint_dates()
    dev_data                 = calcular_metricas_branches_commits(sprint_start, sprint_end)
    github_data              = calcular_metricas_github(sprint_start, sprint_end)

    print("\n🟢 Rodando testes de cobertura...")
    cov_output = rodar_cobertura()
    cov_data   = parse_cobertura(cov_output)

    imprimir_relatorio(jira_data, dev_data, github_data, cov_data)

    linhas = gerar_linhas_relatorio(jira_data, dev_data, github_data, cov_data)

    if args.email or args.discord:
        print()
    if args.email:
        print("📧 Enviando email...")
        enviar_email(linhas)
    if args.discord:
        print("💬 Enviando para o Discord...")
        enviar_discord(linhas)