"""
Bot do Discord — QA Inception IoT
===================================
Comandos de prefixo (!):
  !qa [sprint]       — relatório completo
  !relatorio [sprint]— igual a !qa
  !cobertura [sprint]— cobertura de testes
  !pipeline [sprint] — pipeline e PRs
  !branches [sprint] — branches e commits
  !documentos        — links dos docs
  !ajuda / !         — lista de comandos

Slash commands (/):
  /qa /relatorio /cobertura /pipeline /branches /documentos /ajuda

Tracking automático do Jira (a cada poucos minutos):
  - Tasks atualizadas desde a última verificação → posta no canal
  - Tasks sem atribuição → avisa diariamente
  - Tasks em review há mais de X horas → alerta no canal

SETUP:
  1. pip install discord.py requests python-dateutil python-dotenv
  2. Crie um bot em https://discord.com/developers/applications
     - Ative "Message Content Intent" em Bot → Privileged Gateway Intents
     - Copie o token do bot
  3. cp .env.example .env   e preencha com os valores reais
  4. python discord_bot.py

Todas as credenciais vêm do .env (ou de qa_metrics.py, que também lê do .env)
— nada fica hardcoded no código.
"""

import discord
from discord import app_commands
import asyncio
import sys
import os
import re
import json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_metrics as qa   # qa_metrics.py já carrega o .env e expõe JIRA_URL, JIRA_TOKEN etc.

# =============================================================================
# CONFIG — lido do ambiente (.env)
# =============================================================================

def _env(nome, default=""):
    return os.environ.get(nome, default)

def _env_list(nome, default=""):
    valor = _env(nome, default)
    return [v.strip() for v in valor.split(",") if v.strip()]

DISCORD_BOT_TOKEN   = _env("DISCORD_BOT_TOKEN")
GUILD_ID            = int(_env("GUILD_ID", "0")) or None

CANAIS_COMANDOS     = _env_list("CANAIS_COMANDOS", "qa_bot")  # canais que aceitam ! e /
CANAL_ALERTAS       = _env("CANAL_ALERTAS", "qa_bot")          # canal para alertas automáticos do Jira

# Jira — reaproveita o que qa_metrics.py já carregou do .env
JIRA_URL            = qa.JIRA_URL
JIRA_EMAIL          = qa.JIRA_EMAIL
JIRA_TOKEN          = qa.JIRA_TOKEN
JIRA_PROJECT        = qa.JIRA_PROJECT

HORAS_REVIEW_ALERTA = int(_env("HORAS_REVIEW_ALERTA", "24"))   # horas em review antes de alertar
HORA_AVISO_DIARIO   = int(_env("HORA_AVISO_DIARIO", "9"))      # hora (UTC) do aviso diário de tasks sem dono

ESTADO_FILE = _env("ESTADO_FILE", "jira_state.json")   # arquivo local para rastrear mudanças

# Documentos do projeto — podem ficar fixos no código (não são segredos),
# mas também podem ser sobrescritos via DOCUMENTOS_JSON no .env se preferir.
_docs_env = _env("DOCUMENTOS_JSON")
if _docs_env:
    DOCUMENTOS = json.loads(_docs_env)
else:
    DOCUMENTOS = {
        "📋 Documentação Req Track"     : "https://docs.google.com/document/d/1SveRejJMDFMZskCmsxpyYDclOdltL78Y/edit?usp=sharing&ouid=114103810515329725935&rtpof=true&sd=true",
        "📋 Documentação QA"            : "https://docs.google.com/document/d/1J0d6aLbjInHy20qVlHGfvEs7gErh_-sc/edit?usp=sharing&ouid=105426065366462034056&rtpof=true&sd=true",
        "📋 Documentação BD"            : "https://docs.google.com/document/d/1NPva2Efv6gDTQ_jVhnkBdP7TCrVCZsoe/edit?usp=sharing&ouid=114103810515329725935&rtpof=true&sd=true",
        "📋 Documentação CI"            : "https://docs.google.com/document/d/1XLQre8B24ogd1xuWAElAIdrWZVnLpINEym229e92K98/edit?usp=sharing",
        "📋 Documentação Testes"        : "https://docs.google.com/document/d/1V9Ce7x6m5URqjF7O5Vz81uOLUPBR_j-i/edit?usp=sharing&ouid=114103810515329725935&rtpof=true&sd=true",
        "📋 Documentação Deploy"        : "https://docs.google.com/document/d/14kpMcASjsh78Stcyngdcw73aWle-6ohUeu8SzGLFsUQ/edit?usp=sharing",
        "📋 Documentação Monitoramento" : "https://docs.google.com/document/d/1agQSZKkl6gPUe-B6GQ55gG8P6jn2lIH4/edit?usp=sharing&ouid=114103810515329725935&rtpof=true&sd=true",
        "📋 Processo de Desenvolvimento": "https://docs.google.com/document/d/1b4mSGMyWPyq6AOTlBTp0pLTUoy2Yk7Q7oU-cBg2QVH0/edit?tab=t.in5l9kiute65",
        "📋 Manual Técnico"             : "https://docs.google.com/document/d/1mHYINMrKl6S2o8ZWdYdXjw90_4GgXR3t/edit?usp=sharing&ouid=114103810515329725935&rtpof=true&sd=true",
        "📁 Repositório Front-end"      : "https://github.com/Inception-Fatec/front-end",
        "📁 Repositório Back-end"       : "https://github.com/Inception-Fatec/back-end",
        "🗂️ Board Jira"                 : "https://inceptionfatec.atlassian.net",
        "⚙️ GitHub Actions"             : "https://github.com/Inception-Fatec/front-end/actions",
    }

# =============================================================================
# JIRA TRACKER — coleta e estado
# =============================================================================

def jira_get_sprint_issues():
    """Busca todas as tasks da sprint atual via Jira API."""
    import base64, requests as req
    cred = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
    headers = {"Authorization": f"Basic {cred}", "Content-Type": "application/json"}

    sprint_id = qa.get_sprint_id()
    if sprint_id:
        jql = f"project = {JIRA_PROJECT} AND sprint = {sprint_id} ORDER BY updated DESC"
    else:
        jql = f"project = {JIRA_PROJECT} AND sprint in openSprints() ORDER BY updated DESC"

    url = f"{JIRA_URL}/rest/api/3/search/jql"
    params = {
        "jql": jql,
        "fields": "summary,status,assignee,updated,priority,issuetype",
        "maxResults": 100
    }
    r = req.get(url, headers=headers, params=params)
    if r.status_code != 200:
        print(f"[JIRA TRACKER] Erro {r.status_code}: {r.text[:200]}")
        return []
    return r.json().get("issues", [])


def carregar_estado():
    if not os.path.exists(ESTADO_FILE):
        return {}
    try:
        with open(ESTADO_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def salvar_estado(estado):
    with open(ESTADO_FILE, "w") as f:
        json.dump(estado, f, indent=2)


def status_emoji(status_name):
    s = (status_name or "").lower()
    if "conclu" in s or "done" in s:    return "✅"
    if "andamento" in s or "progress" in s: return "🔄"
    if "review" in s:                   return "🔍"
    if "fazer" in s or "to do" in s:    return "📋"
    return "❓"


def priority_emoji(priority):
    p = (priority or "").lower()
    if "highest" in p or "critical" in p: return "🔴"
    if "high" in p:    return "🟠"
    if "medium" in p:  return "🟡"
    if "low" in p:     return "🟢"
    return "⚪"


def jira_link(key):
    return f"{JIRA_URL}/browse/{key}"


def abreviar_nome(nome):
    """
    Abrevia um nome completo para 'Primeiro S.' (primeiro nome + inicial do último sobrenome).
    Ex: 'Lucas Martins dos Santos Carmo' -> 'Lucas C.'
        'matheus castillo' -> 'Matheus C.'
        'Gabriel Fernando de Lima' -> 'Gabriel L.'
        '—' -> '—'
    """
    if not nome or nome == "—":
        return "—"
    partes = nome.strip().split()
    if len(partes) == 1:
        return partes[0].capitalize()
    primeiro = partes[0].capitalize()
    ultimo   = partes[-1]
    # Ignora preposições comuns caso sejam a última palavra (raro, mas seguro)
    if ultimo.lower() in ("de", "da", "do", "dos", "das") and len(partes) > 2:
        ultimo = partes[-2]
    return f"{primeiro} {ultimo[0].upper()}."


# =============================================================================
# DISCORD — setup
# =============================================================================

intents = discord.Intents.default()
intents.message_content = True
client  = discord.Client(intents=intents)
tree    = app_commands.CommandTree(client)


def canal_permitido(channel_name):
    return not CANAIS_COMANDOS or channel_name in CANAIS_COMANDOS


async def get_canal_alertas():
    for guild in client.guilds:
        canal = discord.utils.get(guild.text_channels, name=CANAL_ALERTAS)
        if canal:
            return canal
    return None


# =============================================================================
# HELPERS DE RESPOSTA
# =============================================================================

def formatar_blocos(linhas):
    blocos, atual = [], "```\n"
    for linha in linhas:
        if len(atual) + len(linha) + 5 > 1990:
            blocos.append(atual + "```")
            atual = "```\n"
        atual += linha + "\n"
    if atual.strip() != "```":
        blocos.append(atual + "```")
    return blocos


def montar_ajuda():
    return (
        "**📋 Comandos do Bot QA — Inception IoT**\n"
        "```"
        "\nComandos de métricas (aceita número da sprint como argumento):"
        "\n  !qa / !relatorio [N]  — Relatório completo da sprint"
        "\n  !cobertura [N]        — Cobertura de testes (Jest)"
        "\n  !pipeline [N]         — Pipeline CI/CD e Pull Requests"
        "\n  !branches [N]         — Branches e commits no padrão"
        "\n"
        "\nComandos do Jira:"
        f"\n  !jira                 — Status atual de todas as tasks da sprint"
        f"\n  !sem-dono             — Tasks sem responsável atribuído"
        f"\n  !em-review            — Tasks em revisão há mais de {HORAS_REVIEW_ALERTA}h"
        "\n"
        "\nOutros:"
        "\n  !documentos           — Links dos documentos e repositórios"
        "\n  !ajuda / !            — Esta mensagem"
        "\n"
        "\nTodos os comandos também estão disponíveis como /slash commands."
        f"\nAlertas automáticos rodam periodicamente no canal #{CANAL_ALERTAS}."
        "```"
    )


def montar_documentos():
    linhas = ["**📂 Documentos do Projeto — Inception IoT**\n"]
    for nome, link in DOCUMENTOS.items():
        linhas.append(f"{nome}\n{link}" if link else f"{nome}\n_link não configurado_")
    return "\n\n".join(linhas)


async def coletar_e_responder(channel, secao=None, sprint=None):
    sprint_label = sprint or qa.SPRINT_NAME
    is_sprint_atual = sprint is None or sprint.strip().lower() == qa.SPRINT_NAME.strip().lower()

    await channel.send(f"⏳ Coletando métricas da **{sprint_label}**...")

    sprint_original = qa.SPRINT_NAME
    if sprint:
        qa.SPRINT_NAME = sprint

    try:
        loop = asyncio.get_event_loop()
        jira_data                = await loop.run_in_executor(None, qa.calcular_metricas_jira)
        sprint_start, sprint_end = await loop.run_in_executor(None, qa.get_sprint_dates)
        dev_data                 = await loop.run_in_executor(None, lambda: qa.calcular_metricas_branches_commits(sprint_start, sprint_end))
        github_data              = await loop.run_in_executor(None, lambda: qa.calcular_metricas_github(sprint_start, sprint_end))

        if secao == "cobertura" and not is_sprint_atual:
            await channel.send("ℹ️ Cobertura não disponível para sprints anteriores — os testes refletem o estado atual do código.")
            return

        if secao in (None, "cobertura") and is_sprint_atual:
            cov_output = await loop.run_in_executor(None, qa.rodar_cobertura)
            cov_data   = qa.parse_cobertura(cov_output)
        else:
            cov_data = {"total": None, "arquivos": [], "sprint_passada": not is_sprint_atual}

        linhas = qa.gerar_linhas_relatorio(jira_data, dev_data, github_data, cov_data)

        if secao == "cobertura":
            linhas = _filtrar_secao(linhas, "JEST")
        elif secao == "pipeline":
            linhas = _filtrar_secao(linhas, "Pipeline")
        elif secao == "branches":
            linhas = _filtrar_secao(linhas, "Branches")

        for bloco in formatar_blocos(linhas):
            await channel.send(bloco)

    except Exception as e:
        await channel.send(f"❌ Erro ao coletar métricas: `{e}`")
    finally:
        qa.SPRINT_NAME = sprint_original


def _filtrar_secao(linhas, palavra_chave):
    cabecalho = linhas[:4]
    dentro, secao = False, []
    for linha in linhas[4:]:
        if palavra_chave.lower() in linha.lower():
            dentro = True
        elif dentro and any(kw in linha for kw in ["JIRA", "GITHUB", "JEST", "=" * 10]) and palavra_chave.lower() not in linha.lower():
            break
        if dentro:
            secao.append(linha)
    return cabecalho + secao + [linhas[-1]]


def _parsear_sprint_arg(resto):
    if not resto:
        return None
    m = re.search(r"\d+", resto)
    return f"Sprint {m.group()}" if m else resto.title()


# =============================================================================
# TRACKER AUTOMÁTICO DO JIRA
# =============================================================================

async def verificar_atualizacoes_jira(bootstrap=False):
    canal = await get_canal_alertas()
    if not canal and not bootstrap:
        return 0

    loop    = asyncio.get_event_loop()
    issues  = await loop.run_in_executor(None, jira_get_sprint_issues)
    estado  = carregar_estado()
    agora   = datetime.now(timezone.utc)
    mudancas = []

    for issue in issues:
        key    = issue["key"]
        fields = issue["fields"]
        status = fields["status"]["name"]
        updated_str = fields.get("updated", "")
        assignee = fields.get("assignee", {})
        assignee_nome = abreviar_nome(assignee.get("displayName", "Sem responsável")) if assignee else "Sem responsável"

        anterior = estado.get(key, {})
        status_anterior = anterior.get("status")

        if not bootstrap and status_anterior and status_anterior != status:
            mudancas.append({
                "key"     : key,
                "summary" : fields["summary"],
                "de"      : status_anterior,
                "para"    : status,
                "assignee": assignee_nome,
                "priority": fields.get("priority", {}).get("name", ""),
            })

        estado[key] = {
            "status"      : status,
            "assignee"    : assignee_nome,
            "updated"     : updated_str,
            "summary"     : fields["summary"],
            "em_review_desde": (
                anterior.get("em_review_desde")
                if "review" in status.lower() and anterior.get("em_review_desde")
                else (agora.isoformat() if "review" in status.lower() else None)
            ),
        }

    salvar_estado(estado)

    if bootstrap:
        print(f"[TRACKER] Bootstrap: {len(issues)} tasks salvas no estado inicial. Monitorando mudanças...")
        return 0

    if mudancas:
        embed = discord.Embed(
            title=f"🔔 {len(mudancas)} task(s) atualizadas — {qa.SPRINT_NAME}",
            color=0x2E5FAC,
            timestamp=agora,
        )
        for m in mudancas[:10]:
            prio = priority_emoji(m["priority"])
            embed.add_field(
                name=f"{prio} [{m['key']}]({jira_link(m['key'])}) {m['summary'][:55]}",
                value=f"{status_emoji(m['de'])} {m['de']} → {status_emoji(m['para'])} **{m['para']}**\n👤 {m['assignee']}",
                inline=False,
            )
        embed.set_footer(text="Jira · Inception IoT")
        await canal.send(embed=embed)

    return len(mudancas)


async def verificar_em_review():
    canal = await get_canal_alertas()
    if not canal:
        return

    estado  = carregar_estado()
    agora   = datetime.now(timezone.utc)
    paradas = []

    for key, dados in estado.items():
        if "review" not in (dados.get("status") or "").lower():
            continue
        desde_str = dados.get("em_review_desde")
        if not desde_str:
            continue
        try:
            desde = datetime.fromisoformat(desde_str)
            horas = (agora - desde).total_seconds() / 3600
            if horas >= HORAS_REVIEW_ALERTA:
                paradas.append({
                    "key"    : key,
                    "summary": dados.get("summary", ""),
                    "assignee": dados.get("assignee", "Sem responsável"),
                    "horas"  : round(horas, 1),
                })
        except Exception:
            continue

    if not paradas:
        return

    embed = discord.Embed(
        title=f"⏰ {len(paradas)} task(s) em review há mais de {HORAS_REVIEW_ALERTA}h",
        description="@here Essas tasks precisam de atenção:",
        color=0xE65100,
        timestamp=agora,
    )
    for p in sorted(paradas, key=lambda x: x["horas"], reverse=True)[:10]:
        embed.add_field(
            name=f"🔍 [{p['key']}]({jira_link(p['key'])}) {p['summary'][:55]}",
            value=f"👤 {p['assignee']} · ⏱️ há **{p['horas']}h** em review",
            inline=False,
        )
    embed.set_footer(text="Jira · Inception IoT")
    await canal.send(embed=embed)


async def verificar_sem_responsavel():
    canal = await get_canal_alertas()
    if not canal:
        return

    loop   = asyncio.get_event_loop()
    issues = await loop.run_in_executor(None, jira_get_sprint_issues)
    sem_dono = [
        i for i in issues
        if not i["fields"].get("assignee")
        and "conclu" not in i["fields"]["status"]["name"].lower()
        and "done"   not in i["fields"]["status"]["name"].lower()
    ]

    if not sem_dono:
        return

    embed = discord.Embed(
        title=f"⚠️ {len(sem_dono)} task(s) sem responsável — {qa.SPRINT_NAME}",
        description="@here Tasks abertas sem ninguém atribuído:",
        color=0xB71C1C,
        timestamp=datetime.now(timezone.utc),
    )
    for i in sem_dono[:10]:
        fields = i["fields"]
        prio   = priority_emoji(fields.get("priority", {}).get("name", ""))
        embed.add_field(
            name=f"{prio} [{i['key']}]({jira_link(i['key'])}) {fields['summary'][:60]}",
            value=f"{status_emoji(fields['status']['name'])} {fields['status']['name']}",
            inline=False,
        )
    embed.set_footer(text="Jira · Inception IoT")
    await canal.send(embed=embed)


# =============================================================================
# COMANDOS MANUAIS DO JIRA (!jira, !sem-dono, !em-review)
# =============================================================================

async def responder_status_jira(channel):
    await channel.send("⏳ Buscando tasks do Jira...")
    loop   = asyncio.get_event_loop()
    issues = await loop.run_in_executor(None, jira_get_sprint_issues)

    if not issues:
        await channel.send("⚠️ Nenhuma task encontrada na sprint atual.")
        return

    por_status = {}
    for i in issues:
        s = i["fields"]["status"]["name"]
        por_status.setdefault(s, []).append(i)

    embed = discord.Embed(
        title=f"📌 Tasks da sprint — {qa.SPRINT_NAME}",
        color=0x2E5FAC,
        timestamp=datetime.now(timezone.utc),
    )

    for status, lista in sorted(por_status.items()):
        txt = ""
        for i in lista:
            f       = i["fields"]
            assignee = f.get("assignee", {})
            nome     = abreviar_nome(assignee.get("displayName", "—")) if assignee else "—"
            prio     = priority_emoji(f.get("priority", {}).get("name", ""))
            txt     += f"{prio} [{i['key']}]({jira_link(i['key'])}) {f['summary'][:50]}\n  👤 {nome}\n"
        if txt:
            embed.add_field(
                name=f"{status_emoji(status)} {status} ({len(lista)})",
                value=txt[:1020],
                inline=False,
            )

    embed.set_footer(text=f"Total: {len(issues)} tasks · Jira · Inception IoT")
    await channel.send(embed=embed)


async def responder_sem_dono(channel):
    await channel.send("⏳ Buscando tasks sem responsável...")
    loop   = asyncio.get_event_loop()
    issues = await loop.run_in_executor(None, jira_get_sprint_issues)

    sem_dono = [
        i for i in issues
        if not i["fields"].get("assignee")
        and "conclu" not in i["fields"]["status"]["name"].lower()
        and "done"   not in i["fields"]["status"]["name"].lower()
    ]

    if not sem_dono:
        embed = discord.Embed(title="✅ Todas as tasks têm responsável!", color=0x2E7D32, timestamp=datetime.now(timezone.utc))
        await channel.send(embed=embed)
        return

    embed = discord.Embed(
        title=f"⚠️ {len(sem_dono)} task(s) sem responsável",
        color=0xB71C1C,
        timestamp=datetime.now(timezone.utc),
    )
    for i in sem_dono:
        f    = i["fields"]
        prio = priority_emoji(f.get("priority", {}).get("name", ""))
        embed.add_field(
            name=f"{prio} [{i['key']}]({jira_link(i['key'])}) {f['summary'][:60]}",
            value=f"{status_emoji(f['status']['name'])} {f['status']['name']}",
            inline=False,
        )
    embed.set_footer(text="Jira · Inception IoT")
    await channel.send(embed=embed)


async def responder_em_review(channel):
    estado  = carregar_estado()
    agora   = datetime.now(timezone.utc)
    paradas = []

    for key, dados in estado.items():
        if "review" not in (dados.get("status") or "").lower():
            continue
        desde_str = dados.get("em_review_desde")
        if not desde_str:
            continue
        try:
            desde = datetime.fromisoformat(desde_str)
            horas = (agora - desde).total_seconds() / 3600
            paradas.append({
                "key"     : key,
                "summary" : dados.get("summary", ""),
                "assignee": dados.get("assignee", "—"),
                "horas"   : round(horas, 1),
            })
        except Exception:
            continue

    if not paradas:
        embed = discord.Embed(title=f"✅ Nenhuma task em review há mais de {HORAS_REVIEW_ALERTA}h", color=0x2E7D32, timestamp=agora)
        await channel.send(embed=embed)
        return

    embed = discord.Embed(
        title=f"🔍 Tasks em review — {len(paradas)} encontradas",
        color=0xE65100,
        timestamp=agora,
    )
    for p in sorted(paradas, key=lambda x: x["horas"], reverse=True):
        alerta = " ⚠️" if p["horas"] >= HORAS_REVIEW_ALERTA else ""
        embed.add_field(
            name=f"🔍 [{p['key']}]({jira_link(p['key'])}) {p['summary'][:55]}{alerta}",
            value=f"👤 {p['assignee']} · ⏱️ há **{p['horas']}h** em review",
            inline=False,
        )
    embed.set_footer(text=f"Alerta automático: >{HORAS_REVIEW_ALERTA}h · Jira · Inception IoT")
    await channel.send(embed=embed)


# =============================================================================
# LOOP AUTOMÁTICO
# =============================================================================

INTERVALO_TRACKER_SEGUNDOS = int(_env("INTERVALO_TRACKER_SEGUNDOS", "300"))  # 5 minutos por padrão

async def loop_tracker():
    await client.wait_until_ready()
    ultimo_aviso_diario = None
    primeira_execucao   = not os.path.exists(ESTADO_FILE) or os.path.getsize(ESTADO_FILE) <= 2

    if primeira_execucao:
        print("[TRACKER] Sem estado salvo — fazendo bootstrap silencioso...")
        try:
            await verificar_atualizacoes_jira(bootstrap=True)
        except Exception as e:
            print(f"[TRACKER] Erro no bootstrap: {e}")
        await asyncio.sleep(INTERVALO_TRACKER_SEGUNDOS)

    while not client.is_closed():
        agora = datetime.now(timezone.utc)
        print(f"[TRACKER] {agora.strftime('%H:%M UTC')} — verificando Jira...")

        try:
            n = await verificar_atualizacoes_jira()
            print(f"[TRACKER] {n} mudanças detectadas" if n else "[TRACKER] Sem mudanças")
        except Exception as e:
            print(f"[TRACKER] Erro em atualizacoes: {e}")

        try:
            await verificar_em_review()
        except Exception as e:
            print(f"[TRACKER] Erro em review: {e}")

        hoje = agora.date()
        if agora.hour == HORA_AVISO_DIARIO and ultimo_aviso_diario != hoje:
            try:
                await verificar_sem_responsavel()
                ultimo_aviso_diario = hoje
            except Exception as e:
                print(f"[TRACKER] Erro sem_responsavel: {e}")

        await asyncio.sleep(INTERVALO_TRACKER_SEGUNDOS)


# =============================================================================
# SLASH COMMANDS
# =============================================================================

@tree.command(name="qa", description="Relatório completo de qualidade da sprint")
@app_commands.describe(sprint="Número da sprint (ex: 3). Padrão: sprint atual")
async def slash_qa(interaction: discord.Interaction, sprint: int = None):
    await interaction.response.defer()
    sprint_str = f"Sprint {sprint}" if sprint else None
    await coletar_e_responder(interaction.followup, sprint=sprint_str)

@tree.command(name="relatorio", description="Relatório completo de qualidade da sprint")
@app_commands.describe(sprint="Número da sprint (ex: 3). Padrão: sprint atual")
async def slash_relatorio(interaction: discord.Interaction, sprint: int = None):
    await interaction.response.defer()
    sprint_str = f"Sprint {sprint}" if sprint else None
    await coletar_e_responder(interaction.followup, sprint=sprint_str)

@tree.command(name="cobertura", description="Cobertura de testes Jest da sprint atual")
async def slash_cobertura(interaction: discord.Interaction):
    await interaction.response.defer()
    await coletar_e_responder(interaction.followup, secao="cobertura")

@tree.command(name="pipeline", description="Métricas de pipeline e Pull Requests")
@app_commands.describe(sprint="Número da sprint (ex: 3). Padrão: sprint atual")
async def slash_pipeline(interaction: discord.Interaction, sprint: int = None):
    await interaction.response.defer()
    sprint_str = f"Sprint {sprint}" if sprint else None
    await coletar_e_responder(interaction.followup, secao="pipeline", sprint=sprint_str)

@tree.command(name="branches", description="Branches e commits no padrão")
@app_commands.describe(sprint="Número da sprint (ex: 3). Padrão: sprint atual")
async def slash_branches(interaction: discord.Interaction, sprint: int = None):
    await interaction.response.defer()
    sprint_str = f"Sprint {sprint}" if sprint else None
    await coletar_e_responder(interaction.followup, secao="branches", sprint=sprint_str)

@tree.command(name="jira", description="Status atual de todas as tasks da sprint no Jira")
async def slash_jira(interaction: discord.Interaction):
    await interaction.response.defer()
    await responder_status_jira(interaction.followup)

@tree.command(name="sem-dono", description="Tasks da sprint sem responsável atribuído")
async def slash_sem_dono(interaction: discord.Interaction):
    await interaction.response.defer()
    await responder_sem_dono(interaction.followup)

@tree.command(name="em-review", description="Tasks em revisão há mais tempo que o limite configurado")
async def slash_em_review(interaction: discord.Interaction):
    await interaction.response.defer()
    await responder_em_review(interaction.followup)

@tree.command(name="documentos", description="Links dos documentos e repositórios do projeto")
async def slash_documentos(interaction: discord.Interaction):
    await interaction.response.send_message(montar_documentos())

@tree.command(name="ajuda", description="Lista todos os comandos disponíveis")
async def slash_ajuda(interaction: discord.Interaction):
    await interaction.response.send_message(montar_ajuda())


# =============================================================================
# PREFIXO ! — on_message
# =============================================================================

@client.event
async def on_message(message):
    if message.author == client.user:
        return
    if not canal_permitido(message.channel.name):
        return

    partes  = message.content.strip().split(None, 1)
    comando = partes[0].lower() if partes else ""
    resto   = partes[1].strip() if len(partes) > 1 else ""

    sprint_arg = _parsear_sprint_arg(resto) if resto else None

    if comando in ("!qa", "!relatorio"):
        await coletar_e_responder(message.channel, sprint=sprint_arg)
    elif comando == "!cobertura":
        await coletar_e_responder(message.channel, secao="cobertura", sprint=sprint_arg)
    elif comando == "!pipeline":
        await coletar_e_responder(message.channel, secao="pipeline", sprint=sprint_arg)
    elif comando == "!branches":
        await coletar_e_responder(message.channel, secao="branches", sprint=sprint_arg)
    elif comando == "!jira":
        await responder_status_jira(message.channel)
    elif comando == "!sem-dono":
        await responder_sem_dono(message.channel)
    elif comando == "!em-review":
        await responder_em_review(message.channel)
    elif comando == "!documentos":
        await message.channel.send(montar_documentos())
    elif comando in ("!ajuda", "!help", "!"):
        await message.channel.send(montar_ajuda())
    elif comando.startswith("!"):
        await message.channel.send(
            f"❓ Comando `{message.content.strip()}` não reconhecido.\n\n" + montar_ajuda()
        )


# =============================================================================
# EVENTS
# =============================================================================

@client.event
async def on_ready():
    print(f"✅ Bot conectado como {client.user}")
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
        print(f"✅ Slash commands sincronizados no servidor {GUILD_ID}")
    else:
        await tree.sync()
        print("✅ Slash commands sincronizados globalmente (pode demorar até 1h)")

    client.loop.create_task(loop_tracker())
    print(f"✅ Tracker Jira iniciado — verificação a cada {INTERVALO_TRACKER_SEGUNDOS//60} min · aviso diário às {HORA_AVISO_DIARIO}h UTC")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKEN não configurado. Preencha no arquivo .env (veja .env.example).")
        sys.exit(1)
    client.run(DISCORD_BOT_TOKEN)