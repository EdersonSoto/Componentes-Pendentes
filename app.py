"""
Painel de Pendencias - Compras de Componentes Eletronicos (Soto Company)

Programa de mesa (nao e pagina de internet): interface nativa em Tkinter,
dados gravados localmente em SQLite. Nao precisa de instalacao nem de conexao
com a internet para funcionar no dia a dia. Para copiar o banco para uma
pasta local (ex.: uma pasta sincronizada do Google Drive/OneDrive) como
backup, use o botao "Backup agora" na barra de ferramentas.

Tambem e possivel enviar o backup direto para o Google Drive via API, sem
depender do cliente de sincronizacao instalado (botao "Nuvem (Google
Drive)") — util para manter duas instalacoes do programa, em PCs
diferentes, atualizadas uma com a outra. Veja as instrucoes de configuracao
no comentario acima da secao "Backup na nuvem (Google Drive)".

Como iniciar: clique duas vezes em "Iniciar Painel.bat" (ou rode `python app.py`).
"""

import calendar
import hashlib
import os
import re
import secrets
import shutil
import smtplib
import sqlite3
import string
import sys
import tempfile
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

import json

try:
    import pystray
    from PIL import Image, ImageDraw
    BANDEJA_DISPONIVEL = True
except ImportError:
    BANDEJA_DISPONIVEL = False

try:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2.credentials import Credentials as GoogleCredentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build as google_build
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    GOOGLE_DRIVE_DISPONIVEL = True
except ImportError:
    GOOGLE_DRIVE_DISPONIVEL = False

# --------------------------------------------------------------------------
# Caminhos e constantes
# --------------------------------------------------------------------------

# Quando empacotado com PyInstaller (sys.frozen), __file__ aponta para dentro
# do bundle, entao a pasta base precisa ser a do executavel para dados,
# config e credenciais ficarem gravados ao lado do .exe (nao dentro do
# bundle interno, que pode ser reescrito a cada atualizacao do programa).
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    RECURSOS_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent
    RECURSOS_DIR = BASE_DIR
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "componentes.db"
CONFIG_PATH = BASE_DIR / "config.json"
DATA_DIR.mkdir(exist_ok=True)

ICON_PATH = RECURSOS_DIR / "assets" / "icon.ico"

CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = DATA_DIR / "token_drive.json"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DRIVE_FOLDER_NOME = "Painel de Pendencias - Backups"

ATTENTION_WINDOW_DAYS = 3  # janela (dias) para o indicador amarelo de "atencao"

MESES_PT = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]
DIAS_SEMANA_PT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

# --------------------------------------------------------------------------
# Paleta de cores (tema unico, claro, acento petroleo/verde-azulado)
#
# As cores de status usam a paleta Okabe-Ito (segura para daltonismo): em
# vez do tradicional vermelho/amarelo/verde — que fica ambiguo para quem
# tem protanopia/deuteranopia (dificuldade em distinguir vermelho e verde,
# o tipo mais comum) — o "bom/no prazo" usa AZUL em vez de verde, o que
# mantem contraste solido com o vermelho para qualquer tipo de daltonismo.
# Cada status tambem tem um simbolo proprio (nao so a cor), reforcando a
# leitura por forma alem de cor.
# --------------------------------------------------------------------------

COR = {
    "bg": "#EEF2F1",
    "surface": "#FFFFFF",
    "borda": "#D7DEDC",
    "ink": "#12232A",
    "ink_muted": "#5C6B70",
    "ink_fraco": "#8A9A9B",
    "acento": "#0E7A88",
    "acento_escuro": "#0B5F6A",
    "acento_tint": "#DCEEEF",
    "verde": "#0B6FB0",
    "verde_tint": "#E1EDF9",
    "amarelo": "#D98E00",
    "amarelo_tint": "#FBEEDA",
    "vermelho": "#C1272D",
    "vermelho_tint": "#FBE4E1",
    "laranja": "#7A4B00",
    "laranja_tint": "#F1E6D2",
    "cinza": "#6B7678",
    "cinza_tint": "#E7EBEB",
    "selecao": "#5B3A9E",
    "selecao_tint": "#EAE3F5",
}

FONTE_TITULO = ("Segoe UI Semibold", 17)
FONTE_SECAO = ("Segoe UI Semibold", 10)
FONTE_BASE = ("Segoe UI", 10)
FONTE_BASE_NEG = ("Segoe UI", 10, "bold")
FONTE_KPI_VALOR = ("Segoe UI Semibold", 20)
FONTE_KPI_ROTULO = ("Segoe UI", 9)
FONTE_DADOS = ("Consolas", 10)
FONTE_DADOS_HEAD = ("Segoe UI Semibold", 9)


def aplicar_icone_janela(janela):
    if ICON_PATH.exists():
        try:
            janela.iconbitmap(str(ICON_PATH))
        except tk.TclError:
            pass


# --------------------------------------------------------------------------
# Icone da bandeja do sistema (perto do relogio, estilo MSN/ICQ)
# --------------------------------------------------------------------------

def _hex_para_rgb(hex_cor):
    hex_cor = hex_cor.lstrip("#")
    return tuple(int(hex_cor[i:i + 2], 16) for i in (0, 2, 4))


def gerar_icone_bandeja(cor_hex):
    """Desenha um icone circular simples (como o status do ICQ/MSN) na cor informada."""
    tamanho = 64
    img = Image.new("RGBA", (tamanho, tamanho), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(img)
    margem = 4
    desenho.ellipse(
        [margem, margem, tamanho - margem, tamanho - margem],
        fill=_hex_para_rgb(cor_hex),
        outline=_hex_para_rgb(COR["surface"]),
        width=3,
    )
    return img


# --------------------------------------------------------------------------
# Banco de dados
# --------------------------------------------------------------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fornecedor TEXT NOT NULL,
            numero_pedido TEXT,
            componente TEXT NOT NULL,
            quantidade INTEGER NOT NULL DEFAULT 1,
            valor REAL NOT NULL DEFAULT 0,
            data_pedido TEXT,
            data_compra TEXT,
            previsao_entrega TEXT,
            data_chegada TEXT,
            cancelado INTEGER NOT NULL DEFAULT 0,
            observacoes TEXT,
            criado_em TEXT NOT NULL,
            atualizado_em TEXT NOT NULL
        )
        """
    )

    colunas_pedidos = {row["name"] for row in conn.execute("PRAGMA table_info(pedidos)")}
    if "criado_por" not in colunas_pedidos:
        conn.execute("ALTER TABLE pedidos ADD COLUMN criado_por TEXT")
    if "atualizado_por" not in colunas_pedidos:
        conn.execute("ALTER TABLE pedidos ADD COLUMN atualizado_por TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome_usuario TEXT NOT NULL UNIQUE,
            nome_completo TEXT,
            senha_hash TEXT NOT NULL,
            senha_salt TEXT NOT NULL,
            criado_em TEXT NOT NULL
        )
        """
    )

    colunas_usuarios = {row["name"] for row in conn.execute("PRAGMA table_info(usuarios)")}
    if "is_admin" not in colunas_usuarios:
        conn.execute("ALTER TABLE usuarios ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        primeiro = conn.execute("SELECT id FROM usuarios ORDER BY id LIMIT 1").fetchone()
        if primeiro:
            conn.execute("UPDATE usuarios SET is_admin = 1 WHERE id = ?", (primeiro["id"],))

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER,
            acao TEXT NOT NULL,
            usuario TEXT NOT NULL,
            descricao TEXT,
            quando TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"pasta_backup": None}


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Regra do indicador de atraso
# --------------------------------------------------------------------------

def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def compute_indicador(row, hoje=None):
    hoje = hoje or date.today()
    previsao = parse_date(row["previsao_entrega"])
    chegada = parse_date(row["data_chegada"])

    if row["cancelado"]:
        return {"cor": "cinza", "rotulo": "Cancelado", "grupo": "cancelado", "simbolo": "⊘", "dias": 0}

    if chegada:
        if previsao and chegada > previsao:
            return {"cor": "laranja", "rotulo": "Entregue com atraso", "grupo": "entregue", "simbolo": "◐",
                    "dias": (chegada - previsao).days}
        return {"cor": "verde", "rotulo": "Entregue no prazo", "grupo": "entregue", "simbolo": "✔", "dias": 0}

    if not previsao:
        return {"cor": "cinza", "rotulo": "Sem previsão", "grupo": "pendente", "simbolo": "○", "dias": 0}

    dias_restantes = (previsao - hoje).days
    if dias_restantes < 0:
        return {"cor": "vermelho", "rotulo": f"Atrasado ({-dias_restantes}d)", "grupo": "pendente", "simbolo": "▲",
                "dias": dias_restantes}
    if dias_restantes <= ATTENTION_WINDOW_DAYS:
        rotulo = "Chega hoje" if dias_restantes == 0 else f"Atenção ({dias_restantes}d)"
        return {"cor": "amarelo", "rotulo": rotulo, "grupo": "pendente", "simbolo": "◆", "dias": dias_restantes}
    return {"cor": "verde", "rotulo": "No prazo", "grupo": "pendente", "simbolo": "●", "dias": dias_restantes}


def carregar_pedidos():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM pedidos ORDER BY fornecedor COLLATE NOCASE"
    ).fetchall()
    conn.close()
    pedidos = []
    for row in rows:
        d = dict(row)
        d["cancelado"] = bool(d["cancelado"])
        d["indicador"] = compute_indicador(d)
        pedidos.append(d)
    return pedidos


# --------------------------------------------------------------------------
# Validacao
# --------------------------------------------------------------------------

class ValidationError(Exception):
    pass


# --------------------------------------------------------------------------
# Usuarios (login / cadastro)
# --------------------------------------------------------------------------

def _hash_senha(senha, salt=None):
    salt = salt or os.urandom(16)
    hash_ = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, 200_000)
    return salt.hex(), hash_.hex()


def criar_usuario(nome_usuario, senha, nome_completo=""):
    nome_usuario = nome_usuario.strip()
    if not nome_usuario:
        raise ValidationError("Informe um nome de usuário.")
    if len(senha) < 4:
        raise ValidationError("A senha deve ter pelo menos 4 caracteres.")

    conn = get_conn()
    existente = conn.execute(
        "SELECT id FROM usuarios WHERE nome_usuario = ? COLLATE NOCASE", (nome_usuario,)
    ).fetchone()
    if existente:
        conn.close()
        raise ValidationError("Já existe um usuário cadastrado com esse nome.")

    eh_primeiro_usuario = conn.execute("SELECT COUNT(*) AS c FROM usuarios").fetchone()["c"] == 0
    salt_hex, hash_hex = _hash_senha(senha)
    conn.execute(
        """
        INSERT INTO usuarios (nome_usuario, nome_completo, senha_hash, senha_salt, criado_em, is_admin)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (nome_usuario, nome_completo.strip(), hash_hex, salt_hex,
         datetime.now().isoformat(timespec="seconds"), 1 if eh_primeiro_usuario else 0),
    )
    conn.commit()
    conn.close()


def autenticar_usuario(nome_usuario, senha):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM usuarios WHERE nome_usuario = ? COLLATE NOCASE", (nome_usuario.strip(),)
    ).fetchone()
    conn.close()
    if not row:
        return None
    _, hash_calculado = _hash_senha(senha, bytes.fromhex(row["senha_salt"]))
    if hash_calculado != row["senha_hash"]:
        return None
    return {
        "id": row["id"], "nome_usuario": row["nome_usuario"], "nome_completo": row["nome_completo"],
        "is_admin": bool(row["is_admin"]),
    }


# --------------------------------------------------------------------------
# Codigo de autorizacao por e-mail (controle de quem pode se cadastrar)
# --------------------------------------------------------------------------
#
# O codigo e enviado automaticamente pelo proprio programa (SMTP, em
# segundo plano) direto para sototechnologycompany@gmail.com — quem esta
# se cadastrando NUNCA ve o codigo na tela, so o administrador (dono
# daquela caixa de entrada). Por isso precisa de uma conta remetente com
# "senha de app" do Gmail configurada em config.json:
#   "email_smtp_remetente": "conta@gmail.com"
#   "email_smtp_senha_app": "senha de app de 16 caracteres (nao a senha normal)"
# Gere a senha de app em myaccount.google.com/apppasswords (exige
# verificacao em duas etapas ativada na conta remetente).

EMAIL_AUTORIZACAO_DESTINO = "sototechnologycompany@gmail.com"
CODIGO_AUTORIZACAO_VALIDADE_MINUTOS = 15


def gerar_codigo_autorizacao():
    alfabeto = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(8))


def enviar_codigo_autorizacao(codigo, nome_usuario_solicitado):
    cfg = load_config()
    remetente = cfg.get("email_smtp_remetente")
    senha_app = cfg.get("email_smtp_senha_app")
    if not remetente or not senha_app:
        raise ValidationError(
            "Envio de e-mail não configurado. Abra o arquivo \"config.json\" (na pasta do "
            "programa) e preencha \"email_smtp_remetente\" e \"email_smtp_senha_app\" "
            "(uma senha de app do Gmail, não a senha normal)."
        )

    mensagem = EmailMessage()
    mensagem["Subject"] = f"Código de autorização — {nome_usuario_solicitado}"
    mensagem["From"] = remetente
    mensagem["To"] = EMAIL_AUTORIZACAO_DESTINO
    mensagem.set_content(
        f"Solicitação de cadastro no Painel de Pendências.\n\n"
        f"Usuário solicitado: {nome_usuario_solicitado}\n"
        f"Código de autorização: {codigo}\n\n"
        f"Válido por {CODIGO_AUTORIZACAO_VALIDADE_MINUTOS} minutos.\n"
        "Repasse esse código para a pessoa por outro meio (telefone, WhatsApp etc.) "
        "para autorizar o cadastro."
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(remetente, senha_app)
        smtp.send_message(mensagem)


# --------------------------------------------------------------------------
# Historico (quem alterou o que)
# --------------------------------------------------------------------------

def registrar_historico(conn, pedido_id, acao, usuario, descricao):
    conn.execute(
        "INSERT INTO historico (pedido_id, acao, usuario, descricao, quando) VALUES (?, ?, ?, ?, ?)",
        (pedido_id, acao, usuario or "—", descricao, datetime.now().isoformat(timespec="seconds")),
    )


def carregar_historico(limite=500):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM historico ORDER BY quando DESC, id DESC LIMIT ?", (limite,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def validar_payload(payload):
    if not str(payload.get("fornecedor", "")).strip():
        raise ValidationError("Informe o fornecedor.")
    if not str(payload.get("componente", "")).strip():
        raise ValidationError("Informe o componente.")

    for campo in ("data_pedido", "data_compra", "previsao_entrega", "data_chegada"):
        valor = payload.get(campo)
        if valor and parse_date(valor) is None:
            raise ValidationError(f"Data inválida em '{campo}'. Use o seletor de calendário.")

    try:
        quantidade = int(payload.get("quantidade") or 1)
        if quantidade < 1:
            raise ValueError
    except (ValueError, TypeError):
        raise ValidationError("Quantidade deve ser um número inteiro maior que zero.")

    try:
        valor_texto = str(payload.get("valor") or "0").replace(".", "").replace(",", ".")
        valor_num = float(valor_texto)
        if valor_num < 0:
            raise ValueError
    except (ValueError, TypeError):
        raise ValidationError("Valor deve ser numérico e não-negativo.")

    return {
        "fornecedor": str(payload["fornecedor"]).strip(),
        "numero_pedido": str(payload.get("numero_pedido") or "").strip(),
        "componente": str(payload["componente"]).strip(),
        "quantidade": quantidade,
        "valor": valor_num,
        "data_pedido": payload.get("data_pedido") or None,
        "data_compra": payload.get("data_compra") or None,
        "previsao_entrega": payload.get("previsao_entrega") or None,
        "data_chegada": payload.get("data_chegada") or None,
        "cancelado": 1 if payload.get("cancelado") else 0,
        "observacoes": str(payload.get("observacoes") or "").strip(),
    }


def inserir_pedido(payload, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    cursor = conn.execute(
        """
        INSERT INTO pedidos
            (fornecedor, numero_pedido, componente, quantidade, valor,
             data_pedido, data_compra, previsao_entrega, data_chegada,
             cancelado, observacoes, criado_em, atualizado_em, criado_por, atualizado_por)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["fornecedor"], payload["numero_pedido"], payload["componente"],
            payload["quantidade"], payload["valor"], payload["data_pedido"],
            payload["data_compra"], payload["previsao_entrega"], payload["data_chegada"],
            payload["cancelado"], payload["observacoes"], agora, agora, usuario, usuario,
        ),
    )
    registrar_historico(
        conn, cursor.lastrowid, "criado", usuario,
        f"{payload['componente']} — {payload['fornecedor']}",
    )
    conn.commit()
    conn.close()


def atualizar_pedido(pedido_id, payload, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        """
        UPDATE pedidos SET
            fornecedor=?, numero_pedido=?, componente=?, quantidade=?, valor=?,
            data_pedido=?, data_compra=?, previsao_entrega=?, data_chegada=?,
            cancelado=?, observacoes=?, atualizado_em=?, atualizado_por=?
        WHERE id=?
        """,
        (
            payload["fornecedor"], payload["numero_pedido"], payload["componente"],
            payload["quantidade"], payload["valor"], payload["data_pedido"],
            payload["data_compra"], payload["previsao_entrega"], payload["data_chegada"],
            payload["cancelado"], payload["observacoes"], agora, usuario, pedido_id,
        ),
    )
    registrar_historico(
        conn, pedido_id, "editado", usuario,
        f"{payload['componente']} — {payload['fornecedor']}",
    )
    conn.commit()
    conn.close()


def excluir_pedido(pedido_id, usuario):
    conn = get_conn()
    pedido = conn.execute("SELECT fornecedor, componente FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    conn.execute("DELETE FROM pedidos WHERE id = ?", (pedido_id,))
    if pedido:
        registrar_historico(
            conn, pedido_id, "excluído", usuario,
            f"{pedido['componente']} — {pedido['fornecedor']}",
        )
    conn.commit()
    conn.close()


def fazer_backup(pasta_destino):
    if not pasta_destino:
        raise ValidationError("Nenhuma pasta de backup configurada. Clique em \"Escolher pasta\".")
    destino_dir = Path(pasta_destino)
    if not destino_dir.exists():
        raise ValidationError("A pasta de backup configurada não existe mais.")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    destino = destino_dir / f"componentes_backup_{timestamp}.db"

    conn = get_conn()
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    conn.close()

    shutil.copy2(DB_PATH, destino)
    return str(destino)


def criar_copia_temp_do_banco():
    conn = get_conn()
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    conn.close()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    tmp = Path(tempfile.gettempdir()) / f"componentes_backup_{timestamp}.db"
    shutil.copy2(DB_PATH, tmp)
    return tmp


# --------------------------------------------------------------------------
# Backup na nuvem (Google Drive)
# --------------------------------------------------------------------------
#
# Usa a API oficial do Google Drive (nao depende do app "Google Drive para
# Desktop" estar instalado/sincronizado). Escopo minimo "drive.file": o
# programa so enxerga a pasta de backup que ele mesmo cria, nada mais do
# Drive do usuario. Para funcionar, e preciso um arquivo "credentials.json"
# (OAuth Client tipo "Desktop app", criado gratuitamente em
# https://console.cloud.google.com/apis/credentials) salvo ao lado do
# app.py. O mesmo credentials.json deve ser copiado para os outros PCs; cada
# PC autoriza a propria conta Google uma vez (botao "Conectar conta Google"),
# e o token fica salvo em data/token_drive.json.


class DriveError(Exception):
    pass


def drive_conta_conectada():
    """Checagem rapida (sem rede) se ja existe um token salvo neste PC."""
    return GOOGLE_DRIVE_DISPONIVEL and TOKEN_PATH.exists()


def _obter_credenciais_existentes():
    if not TOKEN_PATH.exists():
        return None
    creds = GoogleCredentials.from_authorized_user_file(str(TOKEN_PATH), DRIVE_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds if creds and creds.valid else None


def conectar_conta_google():
    """Fluxo interativo: abre o navegador para o usuario autorizar. So deve
    ser chamado a partir de uma acao explicita do usuario (botao Conectar)."""
    if not CREDENTIALS_PATH.exists():
        raise DriveError(
            "Arquivo \"credentials.json\" não encontrado ao lado do app.py.\n\n"
            "Crie um OAuth Client (tipo \"Desktop app\") gratuito em "
            "console.cloud.google.com/apis/credentials, baixe o JSON e salve "
            "com o nome \"credentials.json\" nesta pasta. Copie o mesmo arquivo "
            "para os outros PCs onde o programa for usado."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), DRIVE_SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _obter_servico_drive(interativo=False):
    if not GOOGLE_DRIVE_DISPONIVEL:
        raise DriveError(
            "As bibliotecas do Google Drive não estão instaladas. Rode:\n"
            "pip install google-auth google-auth-oauthlib google-api-python-client"
        )
    creds = _obter_credenciais_existentes()
    if not creds:
        if not interativo:
            raise DriveError("Conta Google ainda não conectada.")
        creds = conectar_conta_google()
    return google_build("drive", "v3", credentials=creds, cache_discovery=False)


def _obter_ou_criar_pasta_drive(servico, cfg):
    folder_id = cfg.get("drive_folder_id")
    if folder_id:
        try:
            info = servico.files().get(fileId=folder_id, fields="id, trashed").execute()
            if not info.get("trashed"):
                return folder_id
        except Exception:
            pass  # pasta nao existe mais / sem acesso: procura/recria abaixo

    # Procura por nome antes de criar uma pasta nova. Isso permite que outro PC,
    # conectando com a MESMA conta Google mas sem o config.json local (drive_folder_id),
    # encontre a pasta que ja existe em vez de criar uma segunda vazia.
    resposta = servico.files().list(
        q=f"name='{DRIVE_FOLDER_NOME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id)",
        pageSize=1,
    ).execute()
    encontrados = resposta.get("files", [])
    if encontrados:
        folder_id = encontrados[0]["id"]
        cfg["drive_folder_id"] = folder_id
        save_config(cfg)
        return folder_id

    resultado = servico.files().create(
        body={"name": DRIVE_FOLDER_NOME, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    folder_id = resultado["id"]
    cfg["drive_folder_id"] = folder_id
    save_config(cfg)
    return folder_id


def enviar_backup_para_drive(caminho_arquivo, interativo=False):
    """Envia um arquivo de backup para a pasta do app no Google Drive.
    Retorna o modifiedTime (ISO) do arquivo criado."""
    servico = _obter_servico_drive(interativo=interativo)
    cfg = load_config()
    folder_id = _obter_ou_criar_pasta_drive(servico, cfg)
    media = MediaFileUpload(str(caminho_arquivo), mimetype="application/octet-stream", resumable=False)
    arquivo = servico.files().create(
        body={"name": Path(caminho_arquivo).name, "parents": [folder_id]},
        media_body=media,
        fields="id, modifiedTime",
    ).execute()
    cfg = load_config()
    cfg["drive_ultimo_modificado_conhecido"] = arquivo["modifiedTime"]
    save_config(cfg)
    return arquivo["modifiedTime"]


def buscar_ultimo_backup_na_nuvem(interativo=False):
    servico = _obter_servico_drive(interativo=interativo)
    cfg = load_config()
    folder_id = _obter_ou_criar_pasta_drive(servico, cfg)
    resposta = servico.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        orderBy="modifiedTime desc",
        pageSize=1,
        fields="files(id, name, modifiedTime)",
    ).execute()
    arquivos = resposta.get("files", [])
    return (servico, arquivos[0]) if arquivos else (servico, None)


def baixar_backup_da_nuvem(servico, file_id, destino):
    request = servico.files().get_media(fileId=file_id)
    with open(destino, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        concluido = False
        while not concluido:
            _status, concluido = downloader.next_chunk()
    return destino


def sincronizar_com_a_nuvem_ao_iniciar():
    """Chamado uma vez ao abrir o programa (so em segundo plano, sem abrir
    navegador). Se ja existir no Drive um backup mais novo do que o ultimo
    que este PC conhece (ou seja, um backup feito em outro PC), baixa e
    substitui o banco local, guardando uma copia de seguranca do banco
    anterior. Retorna uma mensagem para exibir ao usuario, ou None."""
    if not drive_conta_conectada():
        return None
    try:
        cfg = load_config()
        servico, arquivo = buscar_ultimo_backup_na_nuvem(interativo=False)
        if not arquivo:
            return None
        conhecido = cfg.get("drive_ultimo_modificado_conhecido") or ""
        if arquivo["modifiedTime"] <= conhecido:
            return None

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        copia_seguranca = DATA_DIR / f"componentes_pre_sync_{timestamp}.db"
        conn = get_conn()
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        conn.close()
        shutil.copy2(DB_PATH, copia_seguranca)

        baixar_backup_da_nuvem(servico, arquivo["id"], DB_PATH)

        cfg["drive_ultimo_modificado_conhecido"] = arquivo["modifiedTime"]
        save_config(cfg)
        return (
            "Uma versão mais recente do banco foi encontrada no Google Drive "
            "(feita em outro PC) e foi baixada automaticamente.\n\n"
            f"A versão anterior deste PC foi guardada em:\n{copia_seguranca}"
        )
    except Exception:
        return None


def sincronizar_com_a_nuvem_ao_sair():
    """Chamado ao fechar o programa (so em segundo plano, sem abrir
    navegador). Envia uma copia atual do banco para o Drive, se ja houver
    uma conta conectada."""
    if not drive_conta_conectada():
        return
    try:
        tmp = criar_copia_temp_do_banco()
        try:
            enviar_backup_para_drive(tmp, interativo=False)
        finally:
            tmp.unlink(missing_ok=True)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Formatacao
# --------------------------------------------------------------------------

def formatar_moeda(valor):
    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "§").replace(".", ",").replace("§", ".")
    return f"R$ {texto}"


def formatar_data_br(iso):
    d = parse_date(iso)
    return d.strftime("%d/%m/%Y") if d else "—"


def formatar_data_hora_local(iso):
    """Formata um timestamp local (ex.: datetime.now().isoformat(), sem fuso)
    salvo no banco para 'dd/mm/aaaa HH:MM'."""
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return "—"


def formatar_data_hora_iso(iso):
    """Formata um timestamp ISO 8601 (ex.: retornado pela API do Google Drive,
    '2026-07-29T12:34:56.000Z') para 'dd/mm/aaaa HH:MM' em horario local."""
    if not iso:
        return "—"
    try:
        texto = iso.replace("Z", "+00:00")
        momento = datetime.fromisoformat(texto).astimezone()
        return momento.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return "—"


# --------------------------------------------------------------------------
# Importacao de pedido a partir de texto de e-mail colado
# --------------------------------------------------------------------------

OS_RE = re.compile(r"\bOS\.?\s*\d+(?:[./]\d+)*", re.IGNORECASE)
SERIE_RE = re.compile(r"S[ÉE]RIE\s+([A-ZÀ-Ú]+)\s+N[ºo°]?\.?\s*([\w-]+)", re.IGNORECASE)
ITEM_RE = re.compile(
    r"(?P<qtd>\d+)\s*p[çc]s\.?\s+(?P<componente>[^\s(]+)"
    r"(?:\s*\(\s*usar\s*(?P<usar>\d+)\s*pe[çc]a[s]?\s*\))?",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+")

FORNECEDORES_CONHECIDOS = {
    "mouser.com": "Mouser",
    "digikey.com": "Digi-Key",
    "digikey.com.br": "Digi-Key",
    "newark.com": "Newark/Farnell",
    "farnell.com": "Farnell",
    "aliexpress.com": "AliExpress",
    "arrow.com": "Arrow Electronics",
    "lcsc.com": "LCSC",
}


def fornecedor_por_link(url):
    dominio = urlparse(url).netloc.lower()
    dominio = re.sub(r"^(www\.|br\.|pt\.|us\.)+", "", dominio)
    if dominio in FORNECEDORES_CONHECIDOS:
        return FORNECEDORES_CONHECIDOS[dominio]
    partes = dominio.split(".")
    if len(partes) >= 2:
        return partes[-2].capitalize()
    return dominio.capitalize()


def interpretar_email(texto):
    """Extrai peças pendentes de compra de um texto de e-mail colado.

    Reconhece o padrao usado nos pedidos da empresa: assunto com numero de OS
    e, no corpo, uma linha "NN pcs CODIGO (usar N peca)" seguida do link do
    fornecedor para cada componente.
    """
    os_match = OS_RE.search(texto)
    numero_pedido = os_match.group(0).strip() if os_match else ""

    serie_match = SERIE_RE.search(texto)
    serie_nota = f"Série {serie_match.group(1).title()} Nº {serie_match.group(2)}" if serie_match else ""

    itens = list(ITEM_RE.finditer(texto))
    resultados = []
    for i, m in enumerate(itens):
        fim_busca = itens[i + 1].start() if i + 1 < len(itens) else len(texto)
        trecho = texto[m.end():fim_busca]
        url_match = URL_RE.search(trecho)
        link = url_match.group(0).rstrip(").,") if url_match else ""
        fornecedor = fornecedor_por_link(link) if link else ""

        notas = []
        if serie_nota:
            notas.append(serie_nota)
        if m.group("usar"):
            notas.append(f"Necessário usar {m.group('usar')} peça(s) no reparo")
        if link:
            notas.append(link)

        resultados.append({
            "fornecedor": fornecedor,
            "componente": m.group("componente"),
            "quantidade": m.group("qtd"),
            "numero_pedido": numero_pedido,
            "observacoes": " · ".join(notas),
        })
    return resultados


# --------------------------------------------------------------------------
# Seletor de calendario (popup nativo, sem dependencias externas)
# --------------------------------------------------------------------------

class SeletorData(tk.Toplevel):
    def __init__(self, parent, entry_var, data_inicial=None):
        super().__init__(parent)
        self.entry_var = entry_var
        self.overrideredirect(True)
        self.configure(bg=COR["borda"], padx=1, pady=1)

        ref = parse_date(data_inicial) or date.today()
        self.ano = ref.year
        self.mes = ref.month

        self.corpo = tk.Frame(self, bg=COR["surface"])
        self.corpo.pack(fill="both", expand=True)

        self._montar_cabecalho()
        self.grade = tk.Frame(self.corpo, bg=COR["surface"])
        self.grade.pack(padx=10, pady=(0, 10))
        self._montar_grade()

        self.bind("<FocusOut>", lambda e: self._fechar_se_fora())
        self.after(50, lambda: self.focus_set())

    def _fechar_se_fora(self):
        self.after(120, self._checar_foco)

    def _checar_foco(self):
        try:
            if self.focus_get() is None:
                self.destroy()
        except tk.TclError:
            pass

    def _montar_cabecalho(self):
        cab = tk.Frame(self.corpo, bg=COR["surface"])
        cab.pack(fill="x", padx=10, pady=10)
        tk.Button(
            cab, text="‹", command=self._mes_anterior, bd=0, bg=COR["surface"],
            fg=COR["acento"], font=("Segoe UI", 12, "bold"), activebackground=COR["acento_tint"],
            cursor="hand2",
        ).pack(side="left")
        self.lbl_titulo = tk.Label(
            cab, text="", font=FONTE_SECAO, bg=COR["surface"], fg=COR["ink"]
        )
        self.lbl_titulo.pack(side="left", expand=True)
        tk.Button(
            cab, text="›", command=self._mes_seguinte, bd=0, bg=COR["surface"],
            fg=COR["acento"], font=("Segoe UI", 12, "bold"), activebackground=COR["acento_tint"],
            cursor="hand2",
        ).pack(side="right")

    def _mes_anterior(self):
        self.mes -= 1
        if self.mes < 1:
            self.mes = 12
            self.ano -= 1
        self._montar_grade()

    def _mes_seguinte(self):
        self.mes += 1
        if self.mes > 12:
            self.mes = 1
            self.ano += 1
        self._montar_grade()

    def _montar_grade(self):
        for widget in self.grade.winfo_children():
            widget.destroy()
        self.lbl_titulo.configure(text=f"{MESES_PT[self.mes - 1]} de {self.ano}")

        for col, nome in enumerate(DIAS_SEMANA_PT):
            tk.Label(
                self.grade, text=nome, font=("Segoe UI", 8, "bold"),
                fg=COR["ink_fraco"], bg=COR["surface"], width=4,
            ).grid(row=0, column=col, pady=(0, 4))

        hoje = date.today()
        cal = calendar.Calendar(firstweekday=0)
        linha = 1
        for semana in cal.monthdayscalendar(self.ano, self.mes):
            for col, dia in enumerate(semana):
                if dia == 0:
                    tk.Label(self.grade, text="", bg=COR["surface"], width=4).grid(row=linha, column=col)
                    continue
                eh_hoje = date(self.ano, self.mes, dia) == hoje
                btn = tk.Label(
                    self.grade, text=str(dia), width=4, font=FONTE_BASE,
                    bg=COR["acento_tint"] if eh_hoje else COR["surface"],
                    fg=COR["ink"], cursor="hand2", pady=4,
                )
                btn.grid(row=linha, column=col, padx=1, pady=1)
                btn.bind("<Button-1>", lambda e, d=dia: self._selecionar(d))
                btn.bind("<Enter>", lambda e, w=btn: w.configure(bg=COR["acento"], fg="white"))
                btn.bind("<Leave>", lambda e, w=btn, ht=eh_hoje: w.configure(
                    bg=COR["acento_tint"] if ht else COR["surface"], fg=COR["ink"]
                ))
            linha += 1

    def _selecionar(self, dia):
        escolhida = date(self.ano, self.mes, dia)
        self.entry_var.set(escolhida.isoformat())
        self.destroy()


def anexar_seletor_data(parent, botao, entry_var):
    def abrir():
        x = botao.winfo_rootx()
        y = botao.winfo_rooty() + botao.winfo_height()
        popup = SeletorData(parent, entry_var, entry_var.get())
        popup.geometry(f"+{x}+{y}")
    botao.configure(command=abrir)


# --------------------------------------------------------------------------
# Dialogo de novo pedido / edicao
# --------------------------------------------------------------------------

class DialogoPedido(tk.Toplevel):
    def __init__(self, parent, ao_salvar, usuario, pedido=None):
        super().__init__(parent)
        self.ao_salvar = ao_salvar
        self.usuario = usuario
        self.pedido = pedido
        self.title("Editar pedido" if pedido else "Novo pedido")
        self.configure(bg=COR["surface"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.vars = {
            "fornecedor": tk.StringVar(value=(pedido or {}).get("fornecedor", "")),
            "numero_pedido": tk.StringVar(value=(pedido or {}).get("numero_pedido", "")),
            "componente": tk.StringVar(value=(pedido or {}).get("componente", "")),
            "quantidade": tk.StringVar(value=str((pedido or {}).get("quantidade", 1))),
            "valor": tk.StringVar(value=self._valor_inicial(pedido)),
            "data_pedido": tk.StringVar(value=(pedido or {}).get("data_pedido") or ""),
            "data_compra": tk.StringVar(value=(pedido or {}).get("data_compra") or ""),
            "previsao_entrega": tk.StringVar(value=(pedido or {}).get("previsao_entrega") or ""),
            "data_chegada": tk.StringVar(value=(pedido or {}).get("data_chegada") or ""),
            "cancelado": tk.BooleanVar(value=bool((pedido or {}).get("cancelado", False))),
        }

        self._construir_formulario()
        self.bind("<Escape>", lambda e: self.destroy())

    @staticmethod
    def _valor_inicial(pedido):
        if not pedido:
            return ""
        texto = f"{pedido.get('valor', 0):,.2f}"
        return texto.replace(",", "§").replace(".", ",").replace("§", ".")

    def _linha_texto(self, mestre, rotulo, chave, linha, largura=32):
        tk.Label(mestre, text=rotulo, font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=linha, column=0, sticky="w", pady=(8, 2)
        )
        entry = ttk.Entry(mestre, textvariable=self.vars[chave], width=largura, font=FONTE_BASE)
        entry.grid(row=linha, column=1, columnspan=3, sticky="we", pady=(8, 2))
        return entry

    def _linha_data(self, mestre, rotulo, chave, linha, coluna=0):
        tk.Label(mestre, text=rotulo, font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=linha, column=coluna, sticky="w", pady=(8, 2)
        )
        painel = tk.Frame(mestre, bg=COR["surface"])
        painel.grid(row=linha + 1, column=coluna, sticky="we", pady=(0, 4), padx=(0, 10 if coluna == 0 else 0))
        entry = ttk.Entry(painel, textvariable=self.vars[chave], width=13, font=FONTE_DADOS)
        entry.pack(side="left")
        btn = tk.Button(
            painel, text="📅", bd=0, bg=COR["acento_tint"], fg=COR["acento_escuro"],
            activebackground=COR["acento"], cursor="hand2", font=("Segoe UI", 9),
        )
        btn.pack(side="left", padx=(4, 0))
        anexar_seletor_data(self, btn, self.vars[chave])

    def _texto_autoria(self):
        partes = []
        if self.pedido.get("criado_por"):
            partes.append(f"Criado por {self.pedido['criado_por']}")
        if self.pedido.get("atualizado_por") and self.pedido.get("atualizado_por") != self.pedido.get("criado_por"):
            partes.append(f"última alteração por {self.pedido['atualizado_por']}")
        return " • ".join(partes)

    def _construir_formulario(self):
        if self.pedido and (self.pedido.get("criado_por") or self.pedido.get("atualizado_por")):
            tk.Label(
                self, text=self._texto_autoria(), font=("Segoe UI", 8),
                bg=COR["surface"], fg=COR["ink_fraco"],
            ).pack(anchor="w", padx=20, pady=(10, 0))

        pad = tk.Frame(self, bg=COR["surface"], padx=20, pady=18)
        pad.pack(fill="both", expand=True)

        tk.Label(
            pad, text=("Editar pedido" if self.pedido else "Novo pedido"),
            font=FONTE_TITULO, bg=COR["surface"], fg=COR["ink"],
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))

        self._linha_texto(pad, "Fornecedor *", "fornecedor", 1)
        self._linha_texto(pad, "Componente *", "componente", 3)

        tk.Label(pad, text="Nº do pedido", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=5, column=0, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["numero_pedido"], width=14, font=FONTE_DADOS).grid(
            row=6, column=0, sticky="w"
        )

        tk.Label(pad, text="Quantidade", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=5, column=1, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["quantidade"], width=10, font=FONTE_DADOS).grid(
            row=6, column=1, sticky="w"
        )

        tk.Label(pad, text="Valor total (R$)", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=5, column=2, columnspan=2, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["valor"], width=14, font=FONTE_DADOS).grid(
            row=6, column=2, columnspan=2, sticky="w"
        )

        self._linha_data(pad, "Data do pedido", "data_pedido", 7, coluna=0)
        self._linha_data(pad, "Data da compra/pagamento", "data_compra", 7, coluna=1)
        self._linha_data(pad, "Previsão de entrega", "previsao_entrega", 9, coluna=0)
        self._linha_data(pad, "Data de chegada real", "data_chegada", 9, coluna=1)

        ttk.Checkbutton(
            pad, text="Pedido cancelado", variable=self.vars["cancelado"],
        ).grid(row=11, column=0, columnspan=2, sticky="w", pady=(6, 0))

        tk.Label(pad, text="Observações", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=12, column=0, columnspan=4, sticky="w", pady=(10, 2)
        )
        self.txt_obs = tk.Text(pad, width=48, height=3, font=FONTE_BASE, wrap="word", relief="solid", bd=1)
        self.txt_obs.grid(row=13, column=0, columnspan=4, sticky="we")
        self.txt_obs.insert("1.0", (self.pedido or {}).get("observacoes") or "")

        botoes = tk.Frame(pad, bg=COR["surface"])
        botoes.grid(row=14, column=0, columnspan=4, sticky="e", pady=(18, 0))
        tk.Button(
            botoes, text="Cancelar", command=self.destroy, bd=0, padx=16, pady=7,
            bg=COR["bg"], fg=COR["ink_muted"], font=FONTE_BASE, cursor="hand2",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            botoes, text="Salvar pedido", command=self._salvar, bd=0, padx=16, pady=7,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"],
        ).pack(side="left")

    def _salvar(self):
        payload = {k: v.get() for k, v in self.vars.items()}
        payload["observacoes"] = self.txt_obs.get("1.0", "end").strip()
        try:
            dados = validar_payload(payload)
        except ValidationError as exc:
            messagebox.showwarning("Verifique os dados", str(exc), parent=self)
            return

        if self.pedido:
            atualizar_pedido(self.pedido["id"], dados, self.usuario)
        else:
            if not messagebox.askyesno(
                "Confirmar cadastro",
                f"Cadastrar o pedido de \"{dados['componente']}\" ({dados['fornecedor']})?",
                parent=self,
            ):
                return
            inserir_pedido(dados, self.usuario)
            threading.Thread(target=sincronizar_com_a_nuvem_ao_sair, daemon=True).start()
        self.destroy()
        self.ao_salvar()


# --------------------------------------------------------------------------
# Quadro rolavel (lista de cartoes com barra de rolagem)
# --------------------------------------------------------------------------

class QuadroRolavel(tk.Frame):
    def __init__(self, master, altura=220, **kwargs):
        super().__init__(master, **kwargs)
        bg = kwargs.get("bg", COR["surface"])
        self.canvas = tk.Canvas(self, height=altura, bg=bg, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.interior = tk.Frame(self.canvas, bg=bg)
        self.interior.bind(
            "<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.create_window((0, 0), window=self.interior, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")


# --------------------------------------------------------------------------
# Dialogo de importacao de pedido a partir de e-mail
# --------------------------------------------------------------------------

class DialogoImportarEmail(tk.Toplevel):
    def __init__(self, parent, ao_salvar, usuario):
        super().__init__(parent)
        self.ao_salvar = ao_salvar
        self.usuario = usuario
        self.cards = []
        self.title("Importar pedido de e-mail")
        self.configure(bg=COR["surface"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._construir()
        self.bind("<Escape>", lambda e: self.destroy())

    def _construir(self):
        pad = tk.Frame(self, bg=COR["surface"], padx=20, pady=18)
        pad.pack(fill="both", expand=True)

        tk.Label(pad, text="Importar pedido de e-mail", font=FONTE_TITULO,
                 bg=COR["surface"], fg=COR["ink"]).pack(anchor="w")
        tk.Label(
            pad, text="Copie o assunto e o corpo do e-mail de compra e cole abaixo.",
            font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"],
        ).pack(anchor="w", pady=(2, 10))

        self.txt_email = tk.Text(pad, width=86, height=10, font=FONTE_BASE, wrap="word",
                                  relief="solid", bd=1)
        self.txt_email.pack(fill="x")

        tk.Button(
            pad, text="Interpretar e-mail", command=self._interpretar, bd=0, padx=14, pady=7,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"],
        ).pack(anchor="e", pady=(10, 14))

        self.lbl_resultado = tk.Label(pad, text="", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"])
        self.lbl_resultado.pack(anchor="w")

        self.quadro = QuadroRolavel(pad, altura=260, bg=COR["surface"])
        self.quadro.pack(fill="both", expand=True, pady=(8, 14))

        botoes = tk.Frame(pad, bg=COR["surface"])
        botoes.pack(fill="x")
        tk.Button(
            botoes, text="Cancelar", command=self.destroy, bd=0, padx=16, pady=7,
            bg=COR["bg"], fg=COR["ink_muted"], font=FONTE_BASE, cursor="hand2",
        ).pack(side="right", padx=(8, 0))
        self.btn_importar = tk.Button(
            botoes, text="Importar pedidos", command=self._importar, bd=0, padx=16, pady=7,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"], state="disabled",
        )
        self.btn_importar.pack(side="right")

    def _interpretar(self):
        texto = self.txt_email.get("1.0", "end")
        itens = interpretar_email(texto)

        for card in self.cards:
            card["frame"].destroy()
        self.cards = []

        if not itens:
            self.lbl_resultado.configure(
                text="Não consegui identificar peças nesse texto. Confira o formato "
                     "(\"NN pçs CÓDIGO\" + link) ou cadastre manualmente.",
                fg=COR["vermelho"],
            )
            self.btn_importar.configure(state="disabled")
            return

        self.lbl_resultado.configure(
            text=f"{len(itens)} peça(s) identificada(s). Revise os campos antes de importar.",
            fg=COR["verde"],
        )
        for item in itens:
            self._criar_card(item)
        self.btn_importar.configure(state="normal")

    def _criar_card(self, item):
        card = tk.Frame(self.quadro.interior, bg=COR["surface"], highlightbackground=COR["borda"],
                         highlightthickness=1, padx=10, pady=8)
        card.pack(fill="x", pady=4, padx=2)

        vars_ = {
            "fornecedor": tk.StringVar(value=item["fornecedor"]),
            "componente": tk.StringVar(value=item["componente"]),
            "quantidade": tk.StringVar(value=str(item["quantidade"])),
            "numero_pedido": tk.StringVar(value=item["numero_pedido"]),
            "observacoes": tk.StringVar(value=item["observacoes"]),
        }

        topo = tk.Frame(card, bg=COR["surface"])
        topo.grid(row=0, column=0, columnspan=4, sticky="we")
        tk.Label(
            topo, text=item["componente"] or "(componente)", font=FONTE_BASE_NEG,
            bg=COR["surface"], fg=COR["ink"],
        ).pack(side="left")

        registro = {"frame": card, "vars": vars_}
        remover_btn = tk.Button(
            topo, text="Remover", bd=0, bg=COR["surface"], fg=COR["vermelho"],
            font=("Segoe UI", 8), cursor="hand2", command=lambda: self._remover_card(registro),
        )
        remover_btn.pack(side="right")

        def campo(rotulo, chave, coluna, largura=16):
            tk.Label(card, text=rotulo, font=("Segoe UI", 8), bg=COR["surface"], fg=COR["ink_fraco"]).grid(
                row=1, column=coluna, sticky="w", pady=(6, 0)
            )
            ttk.Entry(card, textvariable=vars_[chave], width=largura, font=FONTE_DADOS).grid(
                row=2, column=coluna, sticky="w", padx=(0, 10)
            )

        campo("Fornecedor", "fornecedor", 0)
        campo("Componente", "componente", 1)
        campo("Quantidade", "quantidade", 2, largura=6)
        campo("Nº do pedido", "numero_pedido", 3)

        tk.Label(card, text="Observações", font=("Segoe UI", 8), bg=COR["surface"], fg=COR["ink_fraco"]).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )
        ttk.Entry(card, textvariable=vars_["observacoes"], font=FONTE_BASE, width=70).grid(
            row=4, column=0, columnspan=4, sticky="we", pady=(0, 2)
        )

        self.cards.append(registro)

    def _remover_card(self, registro):
        registro["frame"].destroy()
        self.cards.remove(registro)
        if not self.cards:
            self.btn_importar.configure(state="disabled")

    def _importar(self):
        hoje = date.today().isoformat()
        erros = []
        importados = 0
        for registro in list(self.cards):
            v = registro["vars"]
            payload = {
                "fornecedor": v["fornecedor"].get(),
                "componente": v["componente"].get(),
                "quantidade": v["quantidade"].get(),
                "valor": "0",
                "numero_pedido": v["numero_pedido"].get(),
                "data_pedido": hoje,
                "data_compra": None,
                "previsao_entrega": None,
                "data_chegada": None,
                "cancelado": False,
                "observacoes": v["observacoes"].get(),
            }
            try:
                dados = validar_payload(payload)
                inserir_pedido(dados, self.usuario)
                importados += 1
            except ValidationError as exc:
                erros.append(f"{v['componente'].get() or '(sem código)'}: {exc}")

        if erros:
            messagebox.showwarning(
                "Alguns pedidos não foram importados",
                f"{importados} pedido(s) importado(s).\n\nErros:\n" + "\n".join(erros),
                parent=self,
            )
        else:
            messagebox.showinfo(
                "Importação concluída", f"{importados} pedido(s) importado(s) com sucesso.", parent=self
            )

        if importados:
            threading.Thread(target=sincronizar_com_a_nuvem_ao_sair, daemon=True).start()

        self.destroy()
        self.ao_salvar()


# --------------------------------------------------------------------------
# Dialogo de configuracao do backup na nuvem (Google Drive)
# --------------------------------------------------------------------------

class DialogoDrive(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Backup na nuvem (Google Drive)")
        self.configure(bg=COR["surface"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._construir()
        self.bind("<Escape>", lambda e: self.destroy())

    def _construir(self):
        pad = tk.Frame(self, bg=COR["surface"], padx=20, pady=18)
        pad.pack(fill="both", expand=True)

        tk.Label(pad, text="Backup na nuvem (Google Drive)", font=FONTE_TITULO,
                 bg=COR["surface"], fg=COR["ink"]).pack(anchor="w")
        tk.Label(
            pad, text="Depois de conectar, o programa baixa automaticamente a versão\n"
                      "mais nova ao abrir (se ela tiver vindo de outro PC) e envia uma\n"
                      "cópia para o Drive sempre que você fizer um backup ou fechar o programa.",
            font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"], justify="left",
        ).pack(anchor="w", pady=(4, 14))

        self.lbl_status = tk.Label(pad, text="", font=FONTE_BASE_NEG, bg=COR["surface"])
        self.lbl_status.pack(anchor="w")
        self.lbl_detalhe = tk.Label(pad, text="", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"],
                                     justify="left")
        self.lbl_detalhe.pack(anchor="w", pady=(2, 16))

        botoes = tk.Frame(pad, bg=COR["surface"])
        botoes.pack(fill="x")
        tk.Button(
            botoes, text="Fechar", command=self.destroy, bd=0, padx=16, pady=7,
            bg=COR["bg"], fg=COR["ink_muted"], font=FONTE_BASE, cursor="hand2",
        ).pack(side="right")
        self.btn_conectar = tk.Button(
            botoes, text="Conectar conta Google", command=self._conectar, bd=0, padx=16, pady=7,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"],
        )
        self.btn_conectar.pack(side="right", padx=(0, 8))

        self._atualizar_status()

    def _atualizar_status(self):
        if not GOOGLE_DRIVE_DISPONIVEL:
            self.lbl_status.configure(text="Bibliotecas do Google Drive não instaladas", fg=COR["vermelho"])
            self.lbl_detalhe.configure(
                text="Rode: pip install google-auth google-auth-oauthlib google-api-python-client"
            )
            self.btn_conectar.configure(state="disabled")
            return

        if drive_conta_conectada():
            cfg = load_config()
            ultimo = cfg.get("drive_ultimo_modificado_conhecido")
            self.lbl_status.configure(text="Conectado", fg=COR["verde"])
            self.lbl_detalhe.configure(
                text=("Último backup sincronizado: " + formatar_data_hora_iso(ultimo)) if ultimo
                else "Ainda sem backups na nuvem. Clique em \"Backup agora\" para enviar o primeiro."
            )
            self.btn_conectar.configure(text="Reconectar / trocar conta")
        elif CREDENTIALS_PATH.exists():
            self.lbl_status.configure(text="Não conectado", fg=COR["amarelo"])
            self.lbl_detalhe.configure(text="Clique em \"Conectar conta Google\" e autorize no navegador.")
            self.btn_conectar.configure(text="Conectar conta Google")
        else:
            self.lbl_status.configure(text="Não configurado", fg=COR["amarelo"])
            self.lbl_detalhe.configure(
                text="Falta o arquivo \"credentials.json\" ao lado do app.py.\n"
                     "Crie um OAuth Client (tipo Desktop app) gratuito em\n"
                     "console.cloud.google.com/apis/credentials e salve-o com esse nome."
            )
            self.btn_conectar.configure(text="Conectar conta Google")

    def _conectar(self):
        self.btn_conectar.configure(state="disabled", text="Abrindo navegador...")

        def tarefa():
            erro = None
            mensagem_sync = None
            try:
                conectar_conta_google()
                # Logo apos conectar, ja verifica se existe um backup mais novo
                # na pasta do Drive (ex.: feito em outro PC) e baixa na hora,
                # sem precisar fechar e reabrir o programa.
                mensagem_sync = sincronizar_com_a_nuvem_ao_iniciar()
            except Exception as exc:
                erro = str(exc)
            self.after(0, lambda: self._apos_conectar(erro, mensagem_sync))

        threading.Thread(target=tarefa, daemon=True).start()

    def _apos_conectar(self, erro, mensagem_sync=None):
        self.btn_conectar.configure(state="normal")
        if erro:
            messagebox.showwarning("Não foi possível conectar", erro, parent=self)
        elif mensagem_sync:
            self.master.atualizar_dados()
            messagebox.showinfo("Conectado", mensagem_sync, parent=self)
        else:
            messagebox.showinfo("Conectado", "Conta Google conectada com sucesso.", parent=self)
        self._atualizar_status()


# --------------------------------------------------------------------------
# Dialogo de historico (quem alterou o que)
# --------------------------------------------------------------------------

class DialogoHistorico(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Histórico de alterações")
        self.configure(bg=COR["surface"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._construir()
        self.bind("<Escape>", lambda e: self.destroy())

    def _construir(self):
        pad = tk.Frame(self, bg=COR["surface"], padx=20, pady=18)
        pad.pack(fill="both", expand=True)

        tk.Label(pad, text="Histórico de alterações", font=FONTE_TITULO,
                 bg=COR["surface"], fg=COR["ink"]).pack(anchor="w")
        tk.Label(
            pad, text="Últimos registros de quem criou, editou ou excluiu cada pedido.",
            font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"],
        ).pack(anchor="w", pady=(2, 12))

        quadro = QuadroRolavel(pad, altura=380, bg=COR["surface"])
        quadro.pack(fill="both", expand=True)

        registros = carregar_historico()
        if not registros:
            tk.Label(quadro.interior, text="Nenhuma alteração registrada ainda.",
                     font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).pack(anchor="w", pady=8)
        else:
            for reg in registros:
                linha = tk.Frame(quadro.interior, bg=COR["surface"], highlightbackground=COR["borda"],
                                  highlightthickness=1, padx=10, pady=6)
                linha.pack(fill="x", pady=2, padx=2)
                tk.Label(
                    linha, text=f"{formatar_data_hora_local(reg['quando'])}  •  {reg['usuario']}  •  {reg['acao']}",
                    font=FONTE_BASE_NEG, bg=COR["surface"], fg=COR["ink"],
                ).pack(anchor="w")
                if reg["descricao"]:
                    tk.Label(linha, text=reg["descricao"], font=FONTE_BASE,
                             bg=COR["surface"], fg=COR["ink_muted"]).pack(anchor="w")

        tk.Button(
            pad, text="Fechar", command=self.destroy, bd=0, padx=16, pady=7,
            bg=COR["bg"], fg=COR["ink_muted"], font=FONTE_BASE, cursor="hand2",
        ).pack(anchor="e", pady=(14, 0))


# --------------------------------------------------------------------------
# Tela de login / cadastro
# --------------------------------------------------------------------------

class TelaLogin(tk.Tk):
    def __init__(self):
        super().__init__()
        self.usuario_autenticado = None
        self.title("Entrar — Painel de Pendências")
        self.configure(bg=COR["borda"])
        self.resizable(False, False)
        aplicar_icone_janela(self)
        self.modo_cadastro = False
        self.codigo_pendente = None
        self.codigo_gerado_em = None

        self.var_usuario = tk.StringVar()
        self.var_senha = tk.StringVar()
        self.var_nome_completo = tk.StringVar()
        self.var_confirmar = tk.StringVar()
        self.var_codigo = tk.StringVar()

        self.pad = tk.Frame(self, bg=COR["surface"], padx=28, pady=24)
        self.pad.pack(padx=1, pady=1)
        self._redesenhar()
        self.bind("<Return>", lambda e: self._confirmar())

    def _centralizar(self):
        self.update_idletasks()
        largura = self.winfo_reqwidth()
        altura = self.winfo_reqheight()
        x = (self.winfo_screenwidth() - largura) // 2
        y = (self.winfo_screenheight() - altura) // 2
        self.geometry(f"{largura}x{altura}+{x}+{y}")

    def _campo(self, rotulo, var, mostrar=None):
        tk.Label(self.pad, text=rotulo, font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).pack(
            anchor="w", pady=(8, 2)
        )
        entry = ttk.Entry(self.pad, textvariable=var, width=30, font=FONTE_BASE, show=mostrar or "")
        entry.pack(fill="x")
        return entry

    def _redesenhar(self):
        for w in self.pad.winfo_children():
            w.destroy()

        tk.Label(self.pad, text="Painel de Pendências", font=FONTE_TITULO,
                 bg=COR["surface"], fg=COR["ink"]).pack(anchor="w")
        tk.Label(self.pad, text="Soto Company", font=FONTE_BASE,
                 bg=COR["surface"], fg=COR["ink_muted"]).pack(anchor="center", fill="x", pady=(0, 14))

        self._campo("Usuário", self.var_usuario)
        self._campo("Senha", self.var_senha, mostrar="*")

        tk.Button(
            self.pad, text="Entrar", command=self._entrar, bd=0, padx=16, pady=8,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"],
        ).pack(fill="x", pady=(16, 0))

        if self.modo_cadastro:
            self._campo("Nome completo", self.var_nome_completo)
            self._campo("Confirmar senha", self.var_confirmar, mostrar="*")
            self._campo_codigo()
            tk.Button(
                self.pad, text="Criar conta", command=self._cadastrar, bd=0, padx=16, pady=8,
                bg=COR["verde"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            ).pack(fill="x", pady=(12, 0))
            link = tk.Label(
                self.pad, text="Voltar", font=FONTE_BASE, bg=COR["surface"], fg=COR["acento"], cursor="hand2",
            )
            link.pack(anchor="center", pady=(10, 0))
            link.bind("<Button-1>", lambda e: self._selecionar_aba(False))
        else:
            link = tk.Label(
                self.pad, text="Cadastrar", font=FONTE_BASE, bg=COR["surface"], fg=COR["acento"], cursor="hand2",
            )
            link.pack(anchor="center", pady=(14, 0))
            link.bind("<Button-1>", lambda e: self._selecionar_aba(True))

        self._centralizar()

    def _campo_codigo(self):
        tk.Label(self.pad, text="Código de autorização", font=FONTE_BASE, bg=COR["surface"],
                 fg=COR["ink_muted"]).pack(anchor="w", pady=(8, 2))
        linha = tk.Frame(self.pad, bg=COR["surface"])
        linha.pack(fill="x")
        ttk.Entry(linha, textvariable=self.var_codigo, width=16, font=FONTE_DADOS).pack(side="left")
        tk.Button(
            linha, text="Enviar código", command=self._enviar_codigo, bd=0, padx=10, pady=4,
            bg=COR["acento_tint"], fg=COR["acento_escuro"], font=("Segoe UI", 9), cursor="hand2",
        ).pack(side="left", padx=(8, 0))
        tk.Label(
            self.pad, text="Peça para o administrador conferir o e-mail e informar o código.",
            font=("Segoe UI", 8), bg=COR["surface"], fg=COR["ink_fraco"],
        ).pack(anchor="w", pady=(2, 0))

    def _selecionar_aba(self, cadastro):
        if self.modo_cadastro == cadastro:
            return
        self.modo_cadastro = cadastro
        self._redesenhar()

    def _enviar_codigo(self):
        nome = self.var_usuario.get().strip()
        if not nome:
            messagebox.showwarning(
                "Informe o usuário", "Preencha o campo \"Usuário\" antes de enviar o código.", parent=self
            )
            return
        codigo = gerar_codigo_autorizacao()
        try:
            enviar_codigo_autorizacao(codigo, nome)
        except Exception as exc:
            messagebox.showerror("Não foi possível enviar o código", str(exc), parent=self)
            return
        self.codigo_pendente = codigo
        self.codigo_gerado_em = datetime.now()
        messagebox.showinfo(
            "Código enviado",
            f"O código foi enviado para {EMAIL_AUTORIZACAO_DESTINO}.\n\nPeça o código para o "
            "administrador e cole no campo abaixo.",
            parent=self,
        )

    def _confirmar(self):
        if self.modo_cadastro:
            self._cadastrar()
        else:
            self._entrar()

    def _entrar(self):
        nome = self.var_usuario.get().strip()
        senha = self.var_senha.get()
        if not nome or not senha:
            messagebox.showwarning("Preencha os campos", "Informe usuário e senha.", parent=self)
            return
        usuario = autenticar_usuario(nome, senha)
        if not usuario:
            messagebox.showerror("Login inválido", "Usuário ou senha incorretos.", parent=self)
            return
        self.usuario_autenticado = usuario
        self.destroy()

    def _cadastrar(self):
        nome = self.var_usuario.get().strip()
        senha = self.var_senha.get()
        if senha != self.var_confirmar.get():
            messagebox.showwarning("Senhas diferentes", "A senha e a confirmação não são iguais.", parent=self)
            return

        if not self.codigo_pendente:
            messagebox.showwarning(
                "Envie o código primeiro",
                "Clique em \"Enviar código\" e cole o código recebido por e-mail.",
                parent=self,
            )
            return
        if datetime.now() - self.codigo_gerado_em > timedelta(minutes=CODIGO_AUTORIZACAO_VALIDADE_MINUTOS):
            messagebox.showwarning(
                "Código expirado", "O código expirou. Clique em \"Enviar código\" novamente.", parent=self
            )
            return
        if self.var_codigo.get().strip().upper() != self.codigo_pendente:
            messagebox.showerror("Código incorreto", "O código informado não confere.", parent=self)
            return

        try:
            criar_usuario(nome, senha, self.var_nome_completo.get())
        except ValidationError as exc:
            messagebox.showwarning("Não foi possível cadastrar", str(exc), parent=self)
            return
        self.usuario_autenticado = autenticar_usuario(nome, senha)
        self.destroy()


# --------------------------------------------------------------------------
# Aplicativo principal
# --------------------------------------------------------------------------

FILTROS = [
    ("todos", "Todos"),
    ("pendente_atrasado", "Atrasados"),
    ("pendente_atencao", "Atenção"),
    ("pendente_prazo", "No prazo"),
    ("entregue", "Entregues"),
    ("cancelado", "Cancelados"),
]


class App(tk.Tk):
    def __init__(self, usuario_atual):
        super().__init__()
        self.usuario_atual = usuario_atual
        self.title("Painel de Pendências — Compras de Componentes Eletrônicos")
        self.configure(bg=COR["bg"])
        aplicar_icone_janela(self)
        self.geometry("1220x720")
        self.minsize(1040, 600)
        self.state("zoomed")

        self.filtro_ativo = tk.StringVar(value="todos")
        self.busca = tk.StringVar()
        self.busca.trace_add("write", lambda *_: self._renderizar_tabela())
        self.marcados = set()

        self._configurar_estilos()
        self._montar_topo()
        self._montar_kpis()
        self._montar_toolbar()
        self._montar_tabela()
        self._montar_rodape()

        self.pedidos = []
        self.ordenar_coluna_atual = None
        self.ordenar_reverso = False
        self.icone_bandeja = None
        self._configurar_bandeja()
        self.atualizar_dados()
        self._sincronizar_nuvem_ao_iniciar()
        self._backup_automatico_ao_iniciar()

    # -- bandeja do sistema (perto do relogio) -----------------------------

    def _configurar_bandeja(self):
        if not BANDEJA_DISPONIVEL:
            # pystray/Pillow nao instalados: fecha a janela normalmente encerra o programa.
            self.protocol("WM_DELETE_WINDOW", self._encerrar_app)
            return

        self.protocol("WM_DELETE_WINDOW", self._minimizar_para_bandeja)

        menu = pystray.Menu(
            pystray.MenuItem("Abrir painel", self._tray_abrir, default=True),
            pystray.MenuItem("Sair", self._tray_sair),
        )
        self.icone_bandeja = pystray.Icon(
            "painel_pendencias", gerar_icone_bandeja(COR["verde"]), "Painel de Pendências", menu
        )
        threading.Thread(target=self.icone_bandeja.run, daemon=True).start()

    def _minimizar_para_bandeja(self):
        self.withdraw()

    def _tray_abrir(self, icon=None, item=None):
        self.after(0, self._restaurar_janela)

    def _restaurar_janela(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _tray_sair(self, icon=None, item=None):
        self.after(0, self._sair_de_vez)

    def _sair_de_vez(self):
        if self.icone_bandeja is not None:
            self.icone_bandeja.stop()
        self._encerrar_app()

    def _encerrar_app(self):
        sincronizar_com_a_nuvem_ao_sair()
        self.destroy()

    def _atualizar_icone_bandeja(self, atrasados, atencao):
        if self.icone_bandeja is None:
            return
        if atrasados:
            cor = COR["vermelho"]
        elif atencao:
            cor = COR["amarelo"]
        else:
            cor = COR["verde"]
        self.icone_bandeja.icon = gerar_icone_bandeja(cor)
        self.icone_bandeja.title = (
            f"Painel de Pendências — {atrasados} atrasado(s), {atencao} em atenção"
        )

    # -- estilos ---------------------------------------------------------

    def _configurar_estilos(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("Treeview", background=COR["surface"], fieldbackground=COR["surface"],
                         foreground=COR["ink"], rowheight=30, font=FONTE_DADOS, borderwidth=0)
        style.configure("Treeview.Heading", background=COR["bg"], foreground=COR["ink_muted"],
                         font=FONTE_DADOS_HEAD, borderwidth=0, relief="flat")
        style.map("Treeview.Heading", background=[("active", COR["acento_tint"])])
        # O estado nativo "selected" do ttk sobrepoe qualquer cor definida via
        # tag (a tag de status da linha some quando ela e selecionada), entao
        # a cor de selecao precisa ser configurada aqui, nao so via tag.
        style.map("Treeview", background=[("selected", COR["selecao"])],
                  foreground=[("selected", "white")])
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        style.configure("TEntry", fieldbackground="white", padding=5)
        style.configure("TCheckbutton", background=COR["surface"], font=FONTE_BASE)

    # -- topo / kpis / toolbar -------------------------------------------

    def _montar_topo(self):
        topo = tk.Frame(self, bg=COR["bg"])
        topo.pack(fill="x", padx=24, pady=(20, 6))

        esquerda = tk.Frame(topo, bg=COR["bg"])
        esquerda.pack(side="left")
        tk.Label(esquerda, text="Painel de Pendências", font=FONTE_TITULO,
                 bg=COR["bg"], fg=COR["ink"]).pack(anchor="w")
        nome_exibido = self.usuario_atual.get("nome_completo") or self.usuario_atual["nome_usuario"]
        tk.Label(esquerda, text=f"Compras de componentes eletrônicos — Soto Company  •  Logado como {nome_exibido}",
                 font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_muted"]).pack(anchor="w")

        direita = tk.Frame(topo, bg=COR["bg"])
        direita.pack(side="right")
        if self.usuario_atual.get("is_admin"):
            self._botao_secundario(direita, "Escolher pasta de backup", self._escolher_pasta).pack(
                side="left", padx=(0, 8)
            )
            self._botao_secundario(direita, "Backup agora", self._fazer_backup).pack(side="left", padx=(0, 8))
            self._botao_secundario(direita, "Nuvem (Google Drive)", self._abrir_config_drive).pack(
                side="left", padx=(0, 8)
            )
            self._botao_secundario(direita, "Histórico", self._abrir_historico).pack(side="left")

    def _botao_secundario(self, mestre, texto, comando):
        return tk.Button(
            mestre, text=texto, command=comando, bd=1, relief="solid",
            bg=COR["surface"], fg=COR["ink"], font=FONTE_BASE, padx=12, pady=6,
            cursor="hand2", highlightbackground=COR["borda"],
        )

    def _montar_kpis(self):
        container = tk.Frame(self, bg=COR["bg"])
        container.pack(fill="x", padx=24, pady=10)
        for i in range(4):
            container.grid_columnconfigure(i, weight=1, uniform="kpi")

        self.kpi_labels = {}
        specs = [
            ("abertos", "Pedidos em aberto", COR["acento"]),
            ("valor_aberto", "Valor em aberto", COR["acento"]),
            ("atrasados", "Atrasados agora", COR["vermelho"]),
            ("atencao", "Chegando em breve (≤3 dias)", COR["amarelo"]),
        ]
        for col, (chave, rotulo, cor) in enumerate(specs):
            card = tk.Frame(container, bg=COR["surface"])
            card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 10, 0))
            tk.Frame(card, bg=cor, width=4).pack(side="left", fill="y")
            interior = tk.Frame(card, bg=COR["surface"])
            interior.pack(side="left", fill="both", expand=True, padx=14, pady=12)
            valor_lbl = tk.Label(interior, text="—", font=FONTE_KPI_VALOR, bg=COR["surface"], fg=COR["ink"])
            valor_lbl.pack(anchor="w")
            tk.Label(interior, text=rotulo.upper(), font=FONTE_KPI_ROTULO,
                     bg=COR["surface"], fg=COR["ink_fraco"]).pack(anchor="w", pady=(2, 0))
            self.kpi_labels[chave] = valor_lbl

    def _montar_toolbar(self):
        barra = tk.Frame(self, bg=COR["bg"])
        barra.pack(fill="x", padx=24, pady=(6, 8))

        busca_frame = tk.Frame(barra, bg=COR["surface"], highlightbackground=COR["borda"],
                                highlightthickness=1)
        busca_frame.pack(side="left", ipady=4)
        tk.Label(busca_frame, text="🔍", bg=COR["surface"], fg=COR["ink_fraco"]).pack(side="left", padx=(8, 2))
        tk.Entry(busca_frame, textvariable=self.busca, font=FONTE_BASE, width=28, bd=0,
                 bg=COR["surface"]).pack(side="left", padx=(0, 8), pady=4)

        chips = tk.Frame(barra, bg=COR["bg"])
        chips.pack(side="left", padx=16)
        self.chip_botoes = {}
        for chave, rotulo in FILTROS:
            btn = tk.Label(chips, text=rotulo, font=FONTE_BASE, padx=12, pady=5, cursor="hand2")
            btn.pack(side="left", padx=3)
            btn.bind("<Button-1>", lambda e, c=chave: self._selecionar_filtro(c))
            self.chip_botoes[chave] = btn
        self._atualizar_chips()

        tk.Button(
            barra, text="+  Novo pedido", command=self._abrir_novo, bd=0, padx=16, pady=7,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"],
        ).pack(side="right")
        self._botao_secundario(barra, "✉  Importar de e-mail", self._abrir_importar_email).pack(
            side="right", padx=(0, 8)
        )

    def _selecionar_filtro(self, chave):
        self.filtro_ativo.set(chave)
        self._atualizar_chips()
        self._renderizar_tabela()

    def _atualizar_chips(self):
        ativo = self.filtro_ativo.get()
        for chave, btn in self.chip_botoes.items():
            if chave == ativo:
                btn.configure(bg=COR["acento"], fg="white", font=FONTE_BASE_NEG)
            else:
                btn.configure(bg=COR["surface"], fg=COR["ink_muted"], font=FONTE_BASE)

    # -- tabela ------------------------------------------------------------

    def _montar_tabela(self):
        container = tk.Frame(self, bg=COR["bg"])
        container.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        self.pode_excluir = bool(self.usuario_atual.get("is_admin"))

        colunas = (("marcar",) if self.pode_excluir else ()) + (
            "status", "fornecedor", "componente", "numero_pedido", "quantidade",
            "valor", "data_pedido", "data_compra", "previsao_entrega", "data_chegada",
        )
        titulos = {
            "marcar": "", "status": "Status", "fornecedor": "Fornecedor", "componente": "Componente",
            "numero_pedido": "Nº Pedido", "quantidade": "Qtd", "valor": "Valor (R$)",
            "data_pedido": "Data Pedido", "data_compra": "Data Compra",
            "previsao_entrega": "Previsão", "data_chegada": "Chegada",
        }
        larguras = {
            "marcar": 34, "status": 170, "fornecedor": 150, "componente": 190, "numero_pedido": 100,
            "quantidade": 60, "valor": 110, "data_pedido": 95, "data_compra": 95,
            "previsao_entrega": 95, "data_chegada": 95,
        }

        self.tree = ttk.Treeview(container, columns=colunas, show="headings", selectmode="browse")
        for col in colunas:
            if col == "marcar":
                self.tree.heading(col, text="")
                self.tree.column(col, width=larguras[col], anchor="center", stretch=False)
                continue
            ancora = "e" if col in ("quantidade", "valor") else "w"
            self.tree.heading(col, text=titulos[col], command=lambda c=col: self._ordenar_por(c))
            self.tree.column(col, width=larguras[col], anchor=ancora)

        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for cor_chave, cor_fundo, cor_texto in [
            ("verde", COR["verde_tint"], COR["verde"]),
            ("amarelo", COR["amarelo_tint"], COR["amarelo"]),
            ("vermelho", COR["vermelho_tint"], COR["vermelho"]),
            ("laranja", COR["laranja_tint"], COR["laranja"]),
            ("cinza", COR["cinza_tint"], COR["ink_fraco"]),
        ]:
            self.tree.tag_configure(cor_chave, background=cor_fundo)
            self.tree.tag_configure(f"{cor_chave}_status", foreground=cor_texto)

        self.tree.bind("<Double-1>", lambda e: self._abrir_editar())
        self.tree.bind("<Button-1>", self._ao_clicar_tabela, add="+")

        acoes = tk.Frame(self, bg=COR["bg"])
        acoes.pack(fill="x", padx=24, pady=(0, 4))
        self._botao_secundario(acoes, "Editar selecionado", self._abrir_editar).pack(side="left")
        if self.pode_excluir:
            self._botao_secundario(acoes, "Excluir marcados", self._excluir_marcados).pack(side="left", padx=8)

    def _montar_rodape(self):
        rodape = tk.Frame(self, bg=COR["bg"])
        rodape.pack(fill="x", padx=24, pady=(0, 14))
        self.rodape_lbl = tk.Label(rodape, text="", font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_fraco"])
        self.rodape_lbl.pack(anchor="w")
        self.status_backup_lbl = tk.Label(
            rodape, text="", font=("Segoe UI", 8), bg=COR["bg"], fg=COR["verde"]
        )
        self.status_backup_lbl.pack(anchor="w")

    # -- dados / renderizacao ----------------------------------------------

    def atualizar_dados(self):
        self.pedidos = carregar_pedidos()
        self._atualizar_kpis()
        self._renderizar_tabela()

    def _atualizar_kpis(self):
        abertos = [p for p in self.pedidos if p["indicador"]["grupo"] == "pendente"]
        atrasados = [p for p in abertos if p["indicador"]["cor"] == "vermelho"]
        atencao = [p for p in abertos if p["indicador"]["cor"] == "amarelo"]
        valor_aberto = sum(p["valor"] for p in abertos)

        self.kpi_labels["abertos"].configure(text=str(len(abertos)))
        self.kpi_labels["valor_aberto"].configure(text=formatar_moeda(valor_aberto))
        self.kpi_labels["atrasados"].configure(text=str(len(atrasados)))
        self.kpi_labels["atencao"].configure(text=str(len(atencao)))
        self._atualizar_icone_bandeja(len(atrasados), len(atencao))

    def _pedidos_filtrados(self):
        termo = self.busca.get().strip().lower()
        filtro = self.filtro_ativo.get()
        resultado = []
        for p in self.pedidos:
            ind = p["indicador"]
            if filtro == "pendente_atrasado" and not (ind["grupo"] == "pendente" and ind["cor"] == "vermelho"):
                continue
            if filtro == "pendente_atencao" and not (ind["grupo"] == "pendente" and ind["cor"] == "amarelo"):
                continue
            if filtro == "pendente_prazo" and not (ind["grupo"] == "pendente" and ind["cor"] == "verde"):
                continue
            if filtro == "entregue" and ind["grupo"] != "entregue":
                continue
            if filtro == "cancelado" and ind["grupo"] != "cancelado":
                continue
            if termo:
                alvo = f"{p['fornecedor']} {p['componente']} {p['numero_pedido'] or ''}".lower()
                if termo not in alvo:
                    continue
            resultado.append(p)
        return resultado

    def _renderizar_tabela(self):
        self.tree.delete(*self.tree.get_children())
        pedidos = self._pedidos_filtrados()

        if self.ordenar_coluna_atual:
            pedidos = self._ordenar_lista(pedidos, self.ordenar_coluna_atual, self.ordenar_reverso)
        else:
            ordem_severidade = {"vermelho": 0, "amarelo": 1, "laranja": 2, "verde": 3, "cinza": 4}
            pedidos.sort(key=lambda p: (ordem_severidade.get(p["indicador"]["cor"], 9), p["indicador"]["dias"]))

        for p in pedidos:
            ind = p["indicador"]
            valores = []
            if self.pode_excluir:
                valores.append("☑" if p["id"] in self.marcados else "☐")
            valores.extend((
                f"{ind['simbolo']}  {ind['rotulo']}",
                p["fornecedor"], p["componente"], p["numero_pedido"] or "—",
                p["quantidade"], formatar_moeda(p["valor"]),
                formatar_data_br(p["data_pedido"]), formatar_data_br(p["data_compra"]),
                formatar_data_br(p["previsao_entrega"]), formatar_data_br(p["data_chegada"]),
            ))
            self.tree.insert("", "end", iid=str(p["id"]), values=tuple(valores), tags=(ind["cor"],))

        total = len(pedidos)
        rodape = (
            f"{total} pedido(s) exibido(s) de {len(self.pedidos)} no total  •  "
            f"clique duas vezes numa linha para editar"
        )
        if self.marcados:
            rodape += f"  •  {len(self.marcados)} marcado(s) para excluir"
        self.rodape_lbl.configure(text=rodape)

    def _ao_clicar_tabela(self, evento):
        if not self.pode_excluir:
            return
        if self.tree.identify_region(evento.x, evento.y) != "cell":
            return
        if self.tree.identify_column(evento.x) != "#1":  # coluna "marcar"
            return
        item = self.tree.identify_row(evento.y)
        if not item:
            return
        pedido_id = int(item)
        if pedido_id in self.marcados:
            self.marcados.discard(pedido_id)
        else:
            self.marcados.add(pedido_id)
        self._renderizar_tabela()

    def _ordenar_por(self, coluna):
        if self.ordenar_coluna_atual == coluna:
            self.ordenar_reverso = not self.ordenar_reverso
        else:
            self.ordenar_coluna_atual = coluna
            self.ordenar_reverso = False
        self._renderizar_tabela()

    @staticmethod
    def _ordenar_lista(pedidos, coluna, reverso):
        chaves_data = {"data_pedido", "data_compra", "previsao_entrega", "data_chegada"}
        def chave(p):
            if coluna == "status":
                return p["indicador"]["rotulo"]
            if coluna in chaves_data:
                d = parse_date(p[coluna])
                return d or date.min
            if coluna in ("quantidade", "valor"):
                return p[coluna]
            return str(p.get(coluna) or "").lower()
        return sorted(pedidos, key=chave, reverse=reverso)

    # -- selecao / crud ------------------------------------------------------

    def _pedido_selecionado(self):
        selecao = self.tree.selection()
        if not selecao:
            messagebox.showinfo("Selecione um pedido", "Clique em uma linha da tabela primeiro.")
            return None
        pedido_id = int(selecao[0])
        return next((p for p in self.pedidos if p["id"] == pedido_id), None)

    def _abrir_novo(self):
        DialogoPedido(self, ao_salvar=self.atualizar_dados, usuario=self.usuario_atual["nome_usuario"])

    def _abrir_importar_email(self):
        DialogoImportarEmail(self, ao_salvar=self.atualizar_dados, usuario=self.usuario_atual["nome_usuario"])

    def _abrir_editar(self):
        pedido = self._pedido_selecionado()
        if pedido:
            DialogoPedido(
                self, ao_salvar=self.atualizar_dados, usuario=self.usuario_atual["nome_usuario"], pedido=pedido
            )

    def _abrir_historico(self):
        if not self.usuario_atual.get("is_admin"):
            return
        DialogoHistorico(self)

    def _excluir_marcados(self):
        if not self.pode_excluir:
            return
        if not self.marcados:
            messagebox.showinfo(
                "Nenhum pedido marcado",
                "Marque a caixinha (☐) na frente das linhas que deseja excluir.",
            )
            return

        marcados = [p for p in self.pedidos if p["id"] in self.marcados]
        linhas = "\n".join(f"- {p['componente']} ({p['fornecedor']})" for p in marcados[:10])
        if len(marcados) > 10:
            linhas += f"\n… e mais {len(marcados) - 10}"

        if messagebox.askyesno(
            "Excluir pedidos marcados",
            f"Excluir {len(marcados)} pedido(s)?\n\n{linhas}\n\nEsta ação não pode ser desfeita.",
        ):
            for p in marcados:
                excluir_pedido(p["id"], self.usuario_atual["nome_usuario"])
                self.marcados.discard(p["id"])
            threading.Thread(target=sincronizar_com_a_nuvem_ao_sair, daemon=True).start()
            self.atualizar_dados()

    # -- backup ------------------------------------------------------------

    def _escolher_pasta(self):
        pasta = filedialog.askdirectory(title="Selecione a pasta de backup (ex.: uma pasta do Google Drive)")
        if pasta:
            cfg = load_config()
            cfg["pasta_backup"] = pasta
            save_config(cfg)
            messagebox.showinfo("Pasta configurada", f"Backups serão salvos em:\n{pasta}")

    def _fazer_backup(self):
        cfg = load_config()
        try:
            destino = fazer_backup(cfg.get("pasta_backup"))
        except ValidationError as exc:
            messagebox.showwarning("Backup não realizado", str(exc))
            return
        messagebox.showinfo("Backup concluído", f"Arquivo salvo em:\n{destino}")

        if drive_conta_conectada():
            threading.Thread(target=lambda: enviar_backup_para_drive(destino), daemon=True).start()

    def _backup_automatico_ao_iniciar(self):
        cfg = load_config()
        pasta = cfg.get("pasta_backup")
        if not pasta:
            self._mostrar_status_backup(False)
            return

        def tarefa():
            try:
                destino = fazer_backup(pasta)
            except ValidationError:
                self.after(0, lambda: self._mostrar_status_backup(False))
                return
            if drive_conta_conectada():
                try:
                    enviar_backup_para_drive(destino)
                except Exception:
                    pass
            self.after(0, lambda: self._mostrar_status_backup(True))

        threading.Thread(target=tarefa, daemon=True).start()

    def _mostrar_status_backup(self, sucesso):
        agora = datetime.now().strftime("%H:%M")
        if sucesso:
            self.status_backup_lbl.configure(text=f"✔ Backup OK — {agora}", fg=COR["verde"])
        else:
            self.status_backup_lbl.configure(text=f"✖ Backup não realizado — {agora}", fg=COR["vermelho"])

    def _abrir_config_drive(self):
        if not self.usuario_atual.get("is_admin"):
            return
        DialogoDrive(self)

    def _sincronizar_nuvem_ao_iniciar(self):
        def tarefa():
            mensagem = sincronizar_com_a_nuvem_ao_iniciar()
            if mensagem:
                self.after(0, lambda: self._apos_sincronizar_ao_iniciar(mensagem))

        threading.Thread(target=tarefa, daemon=True).start()

    def _apos_sincronizar_ao_iniciar(self, mensagem):
        self.atualizar_dados()
        messagebox.showinfo("Sincronizado com o Google Drive", mensagem)


def main():
    init_db()

    login = TelaLogin()
    login.mainloop()
    usuario = login.usuario_autenticado
    if not usuario:
        return

    app = App(usuario_atual=usuario)
    app.mainloop()


if __name__ == "__main__":
    main()
