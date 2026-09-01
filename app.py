"""
Painel de Pendencias - Compras de Componentes Eletronicos (Soto Company)

Programa de mesa (nao e pagina de internet): interface nativa em Tkinter,
com os dados guardados na nuvem em um banco Postgres do Supabase (nao mais
em SQLite local). Como o banco fica na nuvem, todas as instalacoes do
programa (em PCs diferentes) enxergam os mesmos pedidos automaticamente —
nao existe mais um passo manual de "sincronizar": toda vez que alguem
cadastra, edita ou exclui um pedido, a mudanca ja fica salva no Supabase
na hora.

Como seguranca extra, o programa tambem grava uma copia local (JSON) de
todas as tabelas na pasta "Backup", ao lado do executavel/instalacao —
automaticamente ao abrir o programa, ou a qualquer momento pelo botao
"Backup agora".

Para configurar a conexao, preencha "supabase_db_url" no arquivo
"config.json" (na pasta do programa) com a connection string do banco.
Veja as instrucoes no comentario acima da funcao get_conn(), mais abaixo.

Como iniciar: clique duas vezes em "Iniciar Painel.bat" (ou rode `python app.py`).
"""

import calendar
import hashlib
import json
import os
import re
import sys
import threading
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

try:
    import pystray
    from PIL import Image, ImageDraw
    BANDEJA_DISPONIVEL = True
except ImportError:
    BANDEJA_DISPONIVEL = False

try:
    from pypdf import PdfReader
    PDF_DISPONIVEL = True
except ImportError:
    PDF_DISPONIVEL = False

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

try:
    from PIL import Image as _ImagemPIL, ImageTk as _ImagemTk
    PIL_DISPONIVEL = True
except ImportError:
    PIL_DISPONIVEL = False

# --------------------------------------------------------------------------
# Caminhos e constantes
# --------------------------------------------------------------------------

# Quando empacotado com PyInstaller (sys.frozen), __file__ aponta para dentro
# do bundle, entao a pasta base precisa ser a do executavel para a
# configuracao ficar gravada ao lado do .exe (nao dentro do bundle interno,
# que pode ser reescrito a cada atualizacao do programa).
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    RECURSOS_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent
    RECURSOS_DIR = BASE_DIR
CONFIG_PATH = BASE_DIR / "config.json"
BACKUP_DIR = BASE_DIR / "Backup"

ICON_PATH = RECURSOS_DIR / "assets" / "icon.ico"
LOGO_ESCURA_PATH = RECURSOS_DIR / "assets" / "logo azul e preto.png"  # simbolo/marca em tons escuros, para fundos claros
LOGO_CLARA_PATH = RECURSOS_DIR / "assets" / "logo azul e branco.png"  # mesma marca em tons claros, para fundos escuros

ATTENTION_WINDOW_DAYS = 3  # janela (dias) para o indicador amarelo de "atencao"

# Senha mestra do administrador: quem souber essa senha pode criar novos
# usuarios (tela de cadastro) e excluir componentes (botao "Excluir
# marcados"). Cadastrar e editar pedidos continua liberado para qualquer
# usuario logado. Fica gravada em "config.json" (mesmo arquivo da connection
# string do banco, na chave "admin_senha") para nao ficar exposta no
# codigo-fonte. Para trocar a senha, edite essa chave no config.json de
# cada PC (ou copie o arquivo pronto para os outros PCs).

APP_VERSION = "V3.0"

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


_cache_logos = {}  # (caminho, altura) -> PhotoImage; guarda a referencia viva para o Tk nao descartar a imagem


def carregar_logo(caminho, altura):
    """Carrega a logo do disco e redimensiona para a altura pedida, mantendo
    a proporcao. Recorta a margem transparente ao redor antes de redimensionar,
    para a marca aproveitar melhor o espaco quando exibida bem pequena."""
    if not PIL_DISPONIVEL:
        return None
    chave = (str(caminho), altura)
    if chave in _cache_logos:
        return _cache_logos[chave]
    if not caminho.exists():
        return None
    try:
        imagem = _ImagemPIL.open(caminho)
        caixa = imagem.getbbox()
        if caixa:
            imagem = imagem.crop(caixa)
        largura = max(1, round(imagem.width * altura / imagem.height))
        imagem = imagem.resize((largura, altura), _ImagemPIL.LANCZOS)
        foto = _ImagemTk.PhotoImage(imagem)
    except Exception:
        return None
    _cache_logos[chave] = foto
    return foto


def rotulo_titulo_versionado(mestre, texto, bg):
    """Monta um titulo de pagina com a versao do app ao lado, discreta:
    fonte bem menor, italico, em outra cor — nao compete com o titulo."""
    linha = tk.Frame(mestre, bg=bg)
    tk.Label(linha, text=texto, font=FONTE_TITULO, bg=bg, fg=COR["ink"]).pack(side="left")
    tk.Label(
        linha, text=APP_VERSION, font=("Georgia", 9, "italic"), bg=bg, fg=COR["acento"],
    ).pack(side="left", padx=(6, 0), pady=(9, 0))
    return linha


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


def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"supabase_db_url": None}


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_admin_senha():
    return load_config().get("admin_senha")


# --------------------------------------------------------------------------
# Validacao
# --------------------------------------------------------------------------

class ValidationError(Exception):
    pass


# --------------------------------------------------------------------------
# Banco de dados (Postgres na nuvem, via Supabase)
# --------------------------------------------------------------------------
#
# O programa se conecta direto no banco Postgres do projeto Supabase (nao
# usa a API REST) — assim varios PCs, cada um com sua propria instalacao,
# leem e gravam no mesmo banco compartilhado, sem precisar de nenhum passo
# manual de sincronizacao.
#
# Como configurar (uma vez por PC, ou copie o config.json pronto para os
# outros PCs):
#   1) No site do Supabase, abra o projeto → Project Settings → Database.
#   2) Em "Connection string", copie a opcao "URI" (recomenda-se usar o
#      modo "Transaction pooler", porta 6543 — funciona melhor com varias
#      instalacoes do programa abrindo conexoes curtas ao mesmo tempo).
#   3) Cole essa URL no arquivo "config.json" (na pasta do programa), na
#      chave abaixo, substituindo [YOUR-PASSWORD] pela senha do banco. De
#      quebra, defina tambem a senha mestra do administrador na chave
#      "admin_senha":
#        {
#          "supabase_db_url": "postgresql://postgres.xxxx:SENHA@aws-0-...pooler.supabase.com:6543/postgres",
#          "admin_senha": "escolha-uma-senha-aqui"
#        }
#
# Internamente, o restante do codigo do app usa conn.execute(sql, params)
# com "?" no lugar dos parametros — igual ao estilo do sqlite3 — para que
# as funcoes de acesso a dados nao precisassem ser reescritas uma a uma na
# migracao. A classe abaixo e so uma fina camada de compatibilidade que
# troca "?" por "%s" (sintaxe do psycopg2) e devolve linhas como dicionario.

class _ConexaoPg:
    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_conn():
    if psycopg2 is None:
        raise ValidationError(
            "A biblioteca \"psycopg2-binary\" não está instalada. Abra um terminal "
            "na pasta do programa e rode:\npip install psycopg2-binary"
        )
    cfg = load_config()
    url = cfg.get("supabase_db_url")
    if not url:
        raise ValidationError(
            "Conexão com o banco de dados não configurada. Abra o arquivo \"config.json\" "
            "(na pasta do programa) e preencha \"supabase_db_url\" com a connection string "
            "do banco (no site do Supabase: Project Settings → Database → Connection string "
            "→ URI)."
        )
    try:
        pg_conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception as exc:
        raise ValidationError(f"Não foi possível conectar ao banco de dados na nuvem:\n\n{exc}")
    return _ConexaoPg(pg_conn)


def init_db():
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pedidos (
            id SERIAL PRIMARY KEY,
            fornecedor TEXT NOT NULL,
            numero_pedido TEXT,
            numero_serie TEXT,
            componente TEXT NOT NULL,
            quantidade INTEGER NOT NULL DEFAULT 1,
            valor DOUBLE PRECISION NOT NULL DEFAULT 0,
            data_pedido TEXT,
            data_compra TEXT,
            previsao_entrega TEXT,
            data_chegada TEXT,
            data_chegada_indaiatuba TEXT,
            cancelado INTEGER NOT NULL DEFAULT 0,
            observacoes TEXT,
            criado_em TEXT NOT NULL,
            atualizado_em TEXT NOT NULL,
            criado_por TEXT,
            atualizado_por TEXT,
            finalizado INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # ADD COLUMN IF NOT EXISTS cobre instalacoes que ja tinham a tabela
    # pedidos criada antes deste campo existir (o CREATE TABLE acima so
    # roda de verdade em bancos novos).
    conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS numero_serie TEXT")
    conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS data_chegada_indaiatuba TEXT")
    conn.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS finalizado INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            nome_usuario TEXT NOT NULL,
            nome_completo TEXT,
            senha_hash TEXT NOT NULL,
            senha_salt TEXT NOT NULL,
            criado_em TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS usuarios_nome_usuario_lower_idx "
        "ON usuarios (LOWER(nome_usuario))"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historico (
            id SERIAL PRIMARY KEY,
            pedido_id INTEGER,
            acao TEXT NOT NULL,
            usuario TEXT NOT NULL,
            descricao TEXT,
            quando TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS equipamentos_aprovados (
            id SERIAL PRIMARY KEY,
            pedido_numero TEXT,
            cliente TEXT NOT NULL,
            os_numero TEXT NOT NULL,
            numero_serie TEXT,
            aprovado_em TEXT,
            liberacao_em TEXT,
            liberacao_efetiva_em TEXT,
            aguardando_peca INTEGER NOT NULL DEFAULT 0,
            tecnico_responsavel TEXT,
            criado_em TEXT NOT NULL,
            atualizado_em TEXT NOT NULL,
            criado_por TEXT,
            atualizado_por TEXT,
            finalizado INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "ALTER TABLE equipamentos_aprovados ADD COLUMN IF NOT EXISTS finalizado INTEGER NOT NULL DEFAULT 0"
    )
    conn.execute(
        "ALTER TABLE equipamentos_aprovados ADD COLUMN IF NOT EXISTS tecnico_responsavel TEXT"
    )
    # liberacao_em e o PRAZO (usado para calcular atraso); liberacao_efetiva_em
    # e a data real em que o laboratorio de fato liberou o equipamento —
    # mesmo padrao de previsao_entrega vs. data_chegada em "pedidos".
    conn.execute(
        "ALTER TABLE equipamentos_aprovados ADD COLUMN IF NOT EXISTS liberacao_efetiva_em TEXT"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orcamentos_enviados (
            id SERIAL PRIMARY KEY,
            os_numero TEXT,
            numero_serie TEXT,
            cliente TEXT NOT NULL,
            destinatarios TEXT,
            material_recebido TEXT,
            prazo_dias_uteis INTEGER,
            data_orcamento TEXT,
            criado_em TEXT NOT NULL,
            atualizado_em TEXT NOT NULL,
            criado_por TEXT,
            atualizado_por TEXT,
            finalizado INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def fazer_backup_local():
    """Exporta uma copia local (JSON) de todas as tabelas do Supabase para a
    pasta "Backup" ao lado do executavel/instalacao do programa. Serve como
    seguranca extra — o banco principal continua sendo o Supabase, isso e
    so uma foto do estado atual guardada no proprio PC."""
    BACKUP_DIR.mkdir(exist_ok=True)
    conn = get_conn()
    dados = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "pedidos": [dict(r) for r in conn.execute("SELECT * FROM pedidos").fetchall()],
        "usuarios": [dict(r) for r in conn.execute("SELECT * FROM usuarios").fetchall()],
        "historico": [dict(r) for r in conn.execute("SELECT * FROM historico").fetchall()],
        "equipamentos_aprovados": [
            dict(r) for r in conn.execute("SELECT * FROM equipamentos_aprovados").fetchall()
        ],
        "orcamentos_enviados": [
            dict(r) for r in conn.execute("SELECT * FROM orcamentos_enviados").fetchall()
        ],
    }
    conn.close()

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    destino = BACKUP_DIR / f"backup_supabase_{timestamp}.json"
    destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


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


def carregar_pedidos(apenas_finalizados=False):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM pedidos WHERE finalizado = ? ORDER BY LOWER(fornecedor)",
        (1 if apenas_finalizados else 0,),
    ).fetchall()
    conn.close()
    pedidos = []
    for row in rows:
        d = dict(row)
        d["cancelado"] = bool(d["cancelado"])
        d["finalizado"] = bool(d["finalizado"])
        d["indicador"] = compute_indicador(d)
        pedidos.append(d)
    return pedidos


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
        "SELECT id FROM usuarios WHERE LOWER(nome_usuario) = LOWER(?)", (nome_usuario,)
    ).fetchone()
    if existente:
        conn.close()
        raise ValidationError("Já existe um usuário cadastrado com esse nome.")

    salt_hex, hash_hex = _hash_senha(senha)
    conn.execute(
        """
        INSERT INTO usuarios (nome_usuario, nome_completo, senha_hash, senha_salt, criado_em)
        VALUES (?, ?, ?, ?, ?)
        """,
        (nome_usuario, nome_completo.strip(), hash_hex, salt_hex,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


def autenticar_usuario(nome_usuario, senha):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM usuarios WHERE LOWER(nome_usuario) = LOWER(?)", (nome_usuario.strip(),)
    ).fetchone()
    conn.close()
    if not row:
        return None
    _, hash_calculado = _hash_senha(senha, bytes.fromhex(row["senha_salt"]))
    if hash_calculado != row["senha_hash"]:
        return None
    return {
        "id": row["id"], "nome_usuario": row["nome_usuario"], "nome_completo": row["nome_completo"],
    }


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

    for campo in ("data_pedido", "data_compra", "previsao_entrega", "data_chegada", "data_chegada_indaiatuba"):
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
        "numero_serie": str(payload.get("numero_serie") or "").strip(),
        "componente": str(payload["componente"]).strip(),
        "quantidade": quantidade,
        "valor": valor_num,
        "data_pedido": payload.get("data_pedido") or None,
        "data_compra": payload.get("data_compra") or None,
        "previsao_entrega": payload.get("previsao_entrega") or None,
        "data_chegada": payload.get("data_chegada") or None,
        "data_chegada_indaiatuba": payload.get("data_chegada_indaiatuba") or None,
        "cancelado": 1 if payload.get("cancelado") else 0,
        "observacoes": str(payload.get("observacoes") or "").strip(),
    }


def inserir_pedido(payload, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    cursor = conn.execute(
        """
        INSERT INTO pedidos
            (fornecedor, numero_pedido, numero_serie, componente, quantidade, valor,
             data_pedido, data_compra, previsao_entrega, data_chegada, data_chegada_indaiatuba,
             cancelado, observacoes, criado_em, atualizado_em, criado_por, atualizado_por)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            payload["fornecedor"], payload["numero_pedido"], payload["numero_serie"], payload["componente"],
            payload["quantidade"], payload["valor"], payload["data_pedido"],
            payload["data_compra"], payload["previsao_entrega"], payload["data_chegada"],
            payload["data_chegada_indaiatuba"],
            payload["cancelado"], payload["observacoes"], agora, agora, usuario, usuario,
        ),
    )
    novo_id = cursor.fetchone()["id"]
    registrar_historico(
        conn, novo_id, "criado", usuario,
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
            fornecedor=?, numero_pedido=?, numero_serie=?, componente=?, quantidade=?, valor=?,
            data_pedido=?, data_compra=?, previsao_entrega=?, data_chegada=?, data_chegada_indaiatuba=?,
            cancelado=?, observacoes=?, atualizado_em=?, atualizado_por=?
        WHERE id=?
        """,
        (
            payload["fornecedor"], payload["numero_pedido"], payload["numero_serie"], payload["componente"],
            payload["quantidade"], payload["valor"], payload["data_pedido"],
            payload["data_compra"], payload["previsao_entrega"], payload["data_chegada"],
            payload["data_chegada_indaiatuba"],
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


def finalizar_pedido(pedido_id, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    pedido = conn.execute(
        "SELECT fornecedor, componente FROM pedidos WHERE id = ?", (pedido_id,)
    ).fetchone()
    conn.execute(
        "UPDATE pedidos SET finalizado = 1, atualizado_em = ?, atualizado_por = ? WHERE id = ?",
        (agora, usuario, pedido_id),
    )
    if pedido:
        registrar_historico(
            conn, pedido_id, "finalizado", usuario,
            f"{pedido['componente']} — {pedido['fornecedor']}",
        )
    conn.commit()
    conn.close()


def reabrir_pedido(pedido_id, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    pedido = conn.execute(
        "SELECT fornecedor, componente FROM pedidos WHERE id = ?", (pedido_id,)
    ).fetchone()
    conn.execute(
        "UPDATE pedidos SET finalizado = 0, atualizado_em = ?, atualizado_por = ? WHERE id = ?",
        (agora, usuario, pedido_id),
    )
    if pedido:
        registrar_historico(
            conn, pedido_id, "reaberto", usuario,
            f"{pedido['componente']} — {pedido['fornecedor']}",
        )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Equipamentos aprovados (aguardando peça / liberação)
# --------------------------------------------------------------------------

def validar_payload_equipamento(payload):
    if not str(payload.get("cliente", "")).strip():
        raise ValidationError("Informe o cliente.")
    if not str(payload.get("os_numero", "")).strip():
        raise ValidationError("Informe o número da OS.")

    for campo in ("aprovado_em", "liberacao_em", "liberacao_efetiva_em"):
        valor = payload.get(campo)
        if valor and parse_date(valor) is None:
            raise ValidationError(f"Data inválida em '{campo}'. Use o seletor de calendário.")

    return {
        "pedido_numero": str(payload.get("pedido_numero") or "").strip(),
        "cliente": str(payload["cliente"]).strip(),
        "os_numero": str(payload["os_numero"]).strip(),
        "numero_serie": str(payload.get("numero_serie") or "").strip(),
        "aprovado_em": payload.get("aprovado_em") or None,
        "liberacao_em": payload.get("liberacao_em") or None,
        "liberacao_efetiva_em": payload.get("liberacao_efetiva_em") or None,
        "aguardando_peca": 1 if payload.get("aguardando_peca") else 0,
        "tecnico_responsavel": str(payload.get("tecnico_responsavel") or "").strip(),
    }


def carregar_equipamentos(apenas_finalizados=False):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM equipamentos_aprovados WHERE finalizado = ? ORDER BY LOWER(cliente), os_numero",
        (1 if apenas_finalizados else 0,),
    ).fetchall()
    conn.close()
    equipamentos = []
    for row in rows:
        d = dict(row)
        d["aguardando_peca"] = bool(d["aguardando_peca"])
        d["finalizado"] = bool(d["finalizado"])
        equipamentos.append(d)
    return equipamentos


def inserir_equipamento(payload, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO equipamentos_aprovados
            (pedido_numero, cliente, os_numero, numero_serie, aprovado_em, liberacao_em,
             liberacao_efetiva_em, aguardando_peca, tecnico_responsavel,
             criado_em, atualizado_em, criado_por, atualizado_por)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["pedido_numero"], payload["cliente"], payload["os_numero"], payload["numero_serie"],
            payload["aprovado_em"], payload["liberacao_em"], payload["liberacao_efetiva_em"],
            payload["aguardando_peca"], payload["tecnico_responsavel"], agora, agora, usuario, usuario,
        ),
    )
    conn.commit()
    conn.close()


def atualizar_equipamento(equipamento_id, payload, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        """
        UPDATE equipamentos_aprovados SET
            pedido_numero=?, cliente=?, os_numero=?, numero_serie=?, aprovado_em=?, liberacao_em=?,
            liberacao_efetiva_em=?, aguardando_peca=?, tecnico_responsavel=?, atualizado_em=?, atualizado_por=?
        WHERE id=?
        """,
        (
            payload["pedido_numero"], payload["cliente"], payload["os_numero"], payload["numero_serie"],
            payload["aprovado_em"], payload["liberacao_em"], payload["liberacao_efetiva_em"],
            payload["aguardando_peca"], payload["tecnico_responsavel"], agora, usuario, equipamento_id,
        ),
    )
    conn.commit()
    conn.close()


def excluir_equipamento(equipamento_id, usuario):
    conn = get_conn()
    conn.execute("DELETE FROM equipamentos_aprovados WHERE id = ?", (equipamento_id,))
    conn.commit()
    conn.close()


def finalizar_equipamento(equipamento_id, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        "UPDATE equipamentos_aprovados SET finalizado = 1, atualizado_em = ?, atualizado_por = ? WHERE id = ?",
        (agora, usuario, equipamento_id),
    )
    conn.commit()
    conn.close()


def reabrir_equipamento(equipamento_id, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        "UPDATE equipamentos_aprovados SET finalizado = 0, atualizado_em = ?, atualizado_por = ? WHERE id = ?",
        (agora, usuario, equipamento_id),
    )
    conn.commit()
    conn.close()


def _tecnico_exibido(equipamento):
    """Nome do tecnico responsavel a mostrar: usa o campo editavel se
    preenchido, senao cai para quem criou o registro (equipamentos antigos,
    cadastrados antes desse campo existir)."""
    return equipamento.get("tecnico_responsavel") or equipamento.get("criado_por") or ""


def compute_indicador_aprovado(equipamento, hoje=None):
    """Mesmo padrao de cor+simbolo do indicador da pagina Pecas (compute_indicador),
    para o usuario daltonico distinguir os estados sem depender so da cor.
    Fica vermelho quando o equipamento NAO esta aguardando peca e o prazo
    de liberacao (liberacao_em) ja passou — ou seja, deveria ter sido
    liberado mas nao foi."""
    hoje = hoje or date.today()

    if equipamento.get("liberacao_efetiva_em"):
        return {"cor": "verde", "rotulo": "Liberado", "simbolo": "✔", "dias": 0}

    if equipamento["aguardando_peca"]:
        return {"cor": "cinza", "rotulo": "Aguardando peça", "simbolo": "○", "dias": 0}

    liberacao = parse_date(equipamento["liberacao_em"])
    if not liberacao:
        return {"cor": "cinza", "rotulo": "Sem prazo", "simbolo": "○", "dias": 0}

    dias_restantes = (liberacao - hoje).days
    if dias_restantes < 0:
        return {"cor": "vermelho", "rotulo": f"Atrasado ({-dias_restantes}d)", "simbolo": "▲",
                "dias": dias_restantes}
    if dias_restantes <= ATTENTION_WINDOW_DAYS:
        rotulo = "Vence hoje" if dias_restantes == 0 else f"Atenção ({dias_restantes}d)"
        return {"cor": "amarelo", "rotulo": rotulo, "simbolo": "◆", "dias": dias_restantes}
    return {"cor": "verde", "rotulo": "No prazo", "simbolo": "●", "dias": dias_restantes}


# --------------------------------------------------------------------------
# Orcamentos enviados (propostas OKSST aguardando aprovacao do cliente)
# --------------------------------------------------------------------------

def validar_payload_orcamento(payload):
    if not str(payload.get("cliente", "")).strip():
        raise ValidationError("Informe o cliente (empresa).")

    valor = payload.get("data_orcamento")
    if valor and parse_date(valor) is None:
        raise ValidationError("Data do orçamento inválida. Use o seletor de calendário.")

    prazo = str(payload.get("prazo_dias_uteis") or "").strip()
    prazo_num = None
    if prazo:
        try:
            prazo_num = int(prazo)
            if prazo_num < 0:
                raise ValueError
        except ValueError:
            raise ValidationError("Prazo (dias úteis) deve ser um número inteiro maior ou igual a zero.")

    return {
        "os_numero": str(payload.get("os_numero") or "").strip(),
        "numero_serie": str(payload.get("numero_serie") or "").strip(),
        "cliente": str(payload["cliente"]).strip(),
        "destinatarios": str(payload.get("destinatarios") or "").strip(),
        "material_recebido": str(payload.get("material_recebido") or "").strip(),
        "prazo_dias_uteis": prazo_num,
        "data_orcamento": payload.get("data_orcamento") or None,
    }


def carregar_orcamentos(apenas_finalizados=False):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM orcamentos_enviados WHERE finalizado = ? ORDER BY LOWER(cliente), os_numero",
        (1 if apenas_finalizados else 0,),
    ).fetchall()
    conn.close()
    orcamentos = []
    for row in rows:
        d = dict(row)
        d["finalizado"] = bool(d["finalizado"])
        orcamentos.append(d)
    return orcamentos


def inserir_orcamento(payload, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO orcamentos_enviados
            (os_numero, numero_serie, cliente, destinatarios, material_recebido,
             prazo_dias_uteis, data_orcamento, criado_em, atualizado_em, criado_por, atualizado_por)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["os_numero"], payload["numero_serie"], payload["cliente"], payload["destinatarios"],
            payload["material_recebido"], payload["prazo_dias_uteis"], payload["data_orcamento"],
            agora, agora, usuario, usuario,
        ),
    )
    conn.commit()
    conn.close()


def atualizar_orcamento(orcamento_id, payload, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        """
        UPDATE orcamentos_enviados SET
            os_numero=?, numero_serie=?, cliente=?, destinatarios=?, material_recebido=?,
            prazo_dias_uteis=?, data_orcamento=?, atualizado_em=?, atualizado_por=?
        WHERE id=?
        """,
        (
            payload["os_numero"], payload["numero_serie"], payload["cliente"], payload["destinatarios"],
            payload["material_recebido"], payload["prazo_dias_uteis"], payload["data_orcamento"],
            agora, usuario, orcamento_id,
        ),
    )
    conn.commit()
    conn.close()


def excluir_orcamento(orcamento_id, usuario):
    conn = get_conn()
    conn.execute("DELETE FROM orcamentos_enviados WHERE id = ?", (orcamento_id,))
    conn.commit()
    conn.close()


def finalizar_orcamento(orcamento_id, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        "UPDATE orcamentos_enviados SET finalizado = 1, atualizado_em = ?, atualizado_por = ? WHERE id = ?",
        (agora, usuario, orcamento_id),
    )
    conn.commit()
    conn.close()


def reabrir_orcamento(orcamento_id, usuario):
    agora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        "UPDATE orcamentos_enviados SET finalizado = 0, atualizado_em = ?, atualizado_por = ? WHERE id = ?",
        (agora, usuario, orcamento_id),
    )
    conn.commit()
    conn.close()


def extrair_texto_pdf(caminho):
    """Le todas as paginas de um PDF e devolve o texto concatenado."""
    leitor = PdfReader(caminho)
    return "\n".join(pagina.extract_text() or "" for pagina in leitor.pages)


def interpretar_orcamento_pdf(texto):
    """Le o texto de uma proposta OKSST (modelo Orkan) e tenta identificar os
    campos principais. Nao levanta erro se algo nao for encontrado — os
    campos ficam em branco e sao revisados/corrigidos pelo usuario antes de
    importar, igual ao fluxo de 'Importar de e-mail' em Pecas."""
    norm = re.sub(r"\s+", " ", texto or "").strip()

    def buscar(padrao):
        m = re.search(padrao, norm, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    os_numero = buscar(r"Proposta\s+N[ºo°]?\.?\s*(OKSST\s*[\w\-]+)")
    numero_serie = buscar(r"S[ée]rie\s+Orkan\s+N[ºo°]?\.?\s*([\w\-]+)")
    cliente = buscar(r"Empresa\s*:\s*(.+?)\s*Para\s*:")
    destinatarios = buscar(r"Para\s*:\s*(.+?)\s*Tel\.?\s*:")
    material_recebido = buscar(r"\*\s*MATERIAL\s+RECEBIDO\s*:\s*(.+?)\s*\*\*\s*DEFEITO")

    data_orcamento = ""
    m = re.search(r"Indaiatuba,\s*(\d{2}/\d{2}/\d{4})", norm, re.IGNORECASE)
    if m:
        try:
            data_orcamento = datetime.strptime(m.group(1), "%d/%m/%Y").date().isoformat()
        except ValueError:
            data_orcamento = ""

    prazo_dias_uteis = ""
    m = re.search(r"(\d+)\s*dias\s+[uú]teis", norm, re.IGNORECASE)
    if m:
        prazo_dias_uteis = m.group(1)

    return {
        "os_numero": os_numero,
        "numero_serie": numero_serie,
        "cliente": cliente,
        "destinatarios": destinatarios,
        "material_recebido": material_recebido,
        "data_orcamento": data_orcamento,
        "prazo_dias_uteis": prazo_dias_uteis,
    }


def resumo_material(texto, limite=48):
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto or "—"
    return texto[:limite].rstrip() + "…"


def separar_destinatarios(texto):
    """O campo 'destinatarios' guarda nomes e e-mails juntos, no formato do
    PDF ('Fulano / Ciclano - fulano@x.com; ciclano@x.com'). Separa em
    (nomes, emails) para a tela mostrar so o nome e o dialogo de edicao
    mostrar os dois em campos distintos."""
    texto = (texto or "").strip()
    if not texto:
        return "", ""
    partes = re.split(r"\s+-\s+", texto, maxsplit=1)
    if len(partes) == 2:
        return partes[0].strip(), partes[1].strip()
    if "@" in texto:
        return "", texto
    return texto, ""


def combinar_destinatarios(nomes, emails):
    nomes = (nomes or "").strip()
    emails = (emails or "").strip()
    if nomes and emails:
        return f"{nomes} - {emails}"
    return nomes or emails


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
    numero_serie = f"{serie_match.group(1).title()} Nº {serie_match.group(2)}" if serie_match else ""

    itens = list(ITEM_RE.finditer(texto))
    resultados = []
    for i, m in enumerate(itens):
        fim_busca = itens[i + 1].start() if i + 1 < len(itens) else len(texto)
        trecho = texto[m.end():fim_busca]
        url_match = URL_RE.search(trecho)
        link = url_match.group(0).rstrip(").,") if url_match else ""
        fornecedor = fornecedor_por_link(link) if link else ""

        notas = []
        if m.group("usar"):
            notas.append(f"Necessário usar {m.group('usar')} peça(s) no reparo")
        if link:
            notas.append(link)

        resultados.append({
            "fornecedor": fornecedor,
            "componente": m.group("componente"),
            "quantidade": m.group("qtd"),
            "numero_pedido": numero_pedido,
            "numero_serie": numero_serie,
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
# Dialogo de senha do administrador (criar usuario / excluir componentes)
# --------------------------------------------------------------------------

class DialogoSenhaAdmin(tk.Toplevel):
    """Pequeno dialogo modal que pede a senha mestra do administrador antes
    de uma ação sensível (excluir componentes). Ao fechar, o atributo
    .resultado indica se a senha informada confere."""

    def __init__(self, parent, motivo):
        super().__init__(parent)
        self.resultado = False
        self.title("Senha do administrador")
        self.configure(bg=COR["surface"])
        self.resizable(False, False)
        self.transient(parent)

        pad = tk.Frame(self, bg=COR["surface"], padx=20, pady=18)
        pad.pack(fill="both", expand=True)

        tk.Label(pad, text="Senha do administrador", font=FONTE_TITULO,
                 bg=COR["surface"], fg=COR["ink"]).pack(anchor="w")
        tk.Label(pad, text=motivo, font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"],
                 wraplength=320, justify="left").pack(anchor="w", pady=(4, 12))

        self.var_senha = tk.StringVar()
        entry = ttk.Entry(pad, textvariable=self.var_senha, show="*", width=28, font=FONTE_BASE)
        entry.pack(fill="x")
        entry.focus_set()

        botoes = tk.Frame(pad, bg=COR["surface"])
        botoes.pack(fill="x", pady=(16, 0))
        tk.Button(
            botoes, text="Cancelar", command=self._cancelar, bd=0, padx=16, pady=7,
            bg=COR["bg"], fg=COR["ink_muted"], font=FONTE_BASE, cursor="hand2",
        ).pack(side="left")
        tk.Button(
            botoes, text="Confirmar", command=self._confirmar, bd=0, padx=16, pady=7,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"],
        ).pack(side="right")

        self.bind("<Return>", lambda e: self._confirmar())
        self.bind("<Escape>", lambda e: self._cancelar())
        self.protocol("WM_DELETE_WINDOW", self._cancelar)

        self.grab_set()
        self.wait_window()

    def _confirmar(self):
        admin_senha = get_admin_senha()
        if not admin_senha or self.var_senha.get() != admin_senha:
            messagebox.showerror("Senha incorreta", "Senha de administrador incorreta.", parent=self)
            return
        self.resultado = True
        self.destroy()

    def _cancelar(self):
        self.resultado = False
        self.destroy()


def confirmar_senha_admin(parent, motivo):
    """Abre o diálogo de senha do administrador e retorna True se confirmada."""
    return DialogoSenhaAdmin(parent, motivo).resultado


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
            "numero_serie": tk.StringVar(value=(pedido or {}).get("numero_serie", "")),
            "componente": tk.StringVar(value=(pedido or {}).get("componente", "")),
            "quantidade": tk.StringVar(value=str((pedido or {}).get("quantidade", 1))),
            "valor": tk.StringVar(value=self._valor_inicial(pedido)),
            "data_pedido": tk.StringVar(value=(pedido or {}).get("data_pedido") or ""),
            "data_compra": tk.StringVar(value=(pedido or {}).get("data_compra") or ""),
            "previsao_entrega": tk.StringVar(value=(pedido or {}).get("previsao_entrega") or ""),
            "data_chegada": tk.StringVar(value=(pedido or {}).get("data_chegada") or ""),
            "data_chegada_indaiatuba": tk.StringVar(
                value=(pedido or {}).get("data_chegada_indaiatuba") or ""
            ),
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
        self._linha_texto(pad, "Nº de série do equipamento", "numero_serie", 5)

        tk.Label(pad, text="Nº do pedido", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=7, column=0, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["numero_pedido"], width=14, font=FONTE_DADOS).grid(
            row=8, column=0, sticky="w"
        )

        tk.Label(pad, text="Quantidade", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=7, column=1, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["quantidade"], width=10, font=FONTE_DADOS).grid(
            row=8, column=1, sticky="w"
        )

        tk.Label(pad, text="Valor total (R$)", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=7, column=2, columnspan=2, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["valor"], width=14, font=FONTE_DADOS).grid(
            row=8, column=2, columnspan=2, sticky="w"
        )

        self._linha_data(pad, "Data do pedido", "data_pedido", 9, coluna=0)
        self._linha_data(pad, "Data da compra/pagamento", "data_compra", 9, coluna=1)
        self._linha_data(pad, "Previsão de entrega", "previsao_entrega", 11, coluna=0)
        self._linha_data(pad, "Chegada SBC", "data_chegada", 11, coluna=1)
        self._linha_data(pad, "Chegada Indaiatuba", "data_chegada_indaiatuba", 13, coluna=0)

        ttk.Checkbutton(
            pad, text="Pedido cancelado", variable=self.vars["cancelado"],
        ).grid(row=15, column=0, columnspan=2, sticky="w", pady=(6, 0))

        tk.Label(pad, text="Observações", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=16, column=0, columnspan=4, sticky="w", pady=(10, 2)
        )
        self.txt_obs = tk.Text(pad, width=48, height=3, font=FONTE_BASE, wrap="word", relief="solid", bd=1)
        self.txt_obs.grid(row=17, column=0, columnspan=4, sticky="we")
        self.txt_obs.insert("1.0", (self.pedido or {}).get("observacoes") or "")

        botoes = tk.Frame(pad, bg=COR["surface"])
        botoes.grid(row=18, column=0, columnspan=4, sticky="e", pady=(18, 0))
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

        try:
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
        except ValidationError as exc:
            messagebox.showerror("Não foi possível salvar", str(exc), parent=self)
            return
        self.destroy()
        self.ao_salvar()


# --------------------------------------------------------------------------
# Dialogo de equipamento aprovado (aguardando peca / liberacao)
# --------------------------------------------------------------------------

class DialogoEquipamento(tk.Toplevel):
    def __init__(self, parent, ao_salvar, usuario, equipamento=None):
        super().__init__(parent)
        self.ao_salvar = ao_salvar
        self.usuario = usuario
        self.equipamento = equipamento
        self.title("Editar equipamento aprovado" if equipamento else "Novo equipamento aprovado")
        self.configure(bg=COR["surface"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.vars = {
            "pedido_numero": tk.StringVar(value=(equipamento or {}).get("pedido_numero", "")),
            "cliente": tk.StringVar(value=(equipamento or {}).get("cliente", "")),
            "os_numero": tk.StringVar(value=(equipamento or {}).get("os_numero", "")),
            "numero_serie": tk.StringVar(value=(equipamento or {}).get("numero_serie", "")),
            "aprovado_em": tk.StringVar(value=(equipamento or {}).get("aprovado_em") or ""),
            "liberacao_em": tk.StringVar(value=(equipamento or {}).get("liberacao_em") or ""),
            "liberacao_efetiva_em": tk.StringVar(
                value=(equipamento or {}).get("liberacao_efetiva_em") or ""
            ),
            "aguardando_peca": tk.BooleanVar(value=bool((equipamento or {}).get("aguardando_peca", False))),
            "tecnico_responsavel": tk.StringVar(
                value=(equipamento or {}).get("tecnico_responsavel") or (usuario if not equipamento else "")
            ),
        }

        self._construir_formulario()
        self.bind("<Escape>", lambda e: self.destroy())

    def _linha_data(self, mestre, rotulo, chave, linha, coluna=0, colspan=1):
        tk.Label(mestre, text=rotulo, font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=linha, column=coluna, columnspan=colspan, sticky="w", pady=(8, 2)
        )
        painel = tk.Frame(mestre, bg=COR["surface"])
        painel.grid(
            row=linha + 1, column=coluna, columnspan=colspan, sticky="we", pady=(0, 4),
            padx=(0, 10 if coluna < 2 else 0),
        )
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
        if self.equipamento.get("criado_por"):
            partes.append(f"Criado por {self.equipamento['criado_por']}")
        if (self.equipamento.get("atualizado_por")
                and self.equipamento.get("atualizado_por") != self.equipamento.get("criado_por")):
            partes.append(f"última alteração por {self.equipamento['atualizado_por']}")
        return " • ".join(partes)

    def _construir_formulario(self):
        if self.equipamento and (self.equipamento.get("criado_por") or self.equipamento.get("atualizado_por")):
            tk.Label(
                self, text=self._texto_autoria(), font=("Segoe UI", 8),
                bg=COR["surface"], fg=COR["ink_fraco"],
            ).pack(anchor="w", padx=20, pady=(10, 0))

        pad = tk.Frame(self, bg=COR["surface"], padx=20, pady=18)
        pad.pack(fill="both", expand=True)

        tk.Label(
            pad, text=("Editar equipamento aprovado" if self.equipamento else "Novo equipamento aprovado"),
            font=FONTE_TITULO, bg=COR["surface"], fg=COR["ink"],
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))

        tk.Label(pad, text="Cliente *", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["cliente"], width=32, font=FONTE_BASE).grid(
            row=2, column=0, columnspan=4, sticky="we"
        )

        tk.Label(pad, text="Pedido Nº", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=3, column=0, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["pedido_numero"], width=14, font=FONTE_DADOS).grid(
            row=4, column=0, sticky="w"
        )

        tk.Label(pad, text="OS. *", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=3, column=1, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["os_numero"], width=14, font=FONTE_DADOS).grid(
            row=4, column=1, sticky="w"
        )

        tk.Label(pad, text="Série (equipamento)", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=3, column=2, columnspan=2, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["numero_serie"], width=18, font=FONTE_DADOS).grid(
            row=4, column=2, columnspan=2, sticky="w"
        )

        self._linha_data(pad, "Aprovado em", "aprovado_em", 5, coluna=0)
        self._linha_data(pad, "Prazo de liberação", "liberacao_em", 5, coluna=1)
        self._linha_data(pad, "Liberado em (efetivo)", "liberacao_efetiva_em", 5, coluna=2, colspan=2)

        tk.Label(pad, text="Técnico responsável", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=7, column=0, columnspan=4, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["tecnico_responsavel"], width=32, font=FONTE_BASE).grid(
            row=8, column=0, columnspan=4, sticky="we"
        )

        ttk.Checkbutton(
            pad, text="Aguardando peça", variable=self.vars["aguardando_peca"],
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(10, 0))

        botoes = tk.Frame(pad, bg=COR["surface"])
        botoes.grid(row=10, column=0, columnspan=4, sticky="e", pady=(18, 0))
        tk.Button(
            botoes, text="Cancelar", command=self.destroy, bd=0, padx=16, pady=7,
            bg=COR["bg"], fg=COR["ink_muted"], font=FONTE_BASE, cursor="hand2",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            botoes, text="Salvar", command=self._salvar, bd=0, padx=16, pady=7,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"],
        ).pack(side="left")

    def _salvar(self):
        payload = {k: v.get() for k, v in self.vars.items()}
        try:
            dados = validar_payload_equipamento(payload)
        except ValidationError as exc:
            messagebox.showwarning("Verifique os dados", str(exc), parent=self)
            return

        try:
            if self.equipamento:
                atualizar_equipamento(self.equipamento["id"], dados, self.usuario)
            else:
                inserir_equipamento(dados, self.usuario)
        except ValidationError as exc:
            messagebox.showerror("Não foi possível salvar", str(exc), parent=self)
            return
        self.destroy()
        self.ao_salvar()


# --------------------------------------------------------------------------
# Dialogo de orcamento enviado (proposta OKSST) — criacao/edicao manual
# --------------------------------------------------------------------------

class DialogoOrcamento(tk.Toplevel):
    def __init__(self, parent, ao_salvar, usuario, orcamento=None):
        super().__init__(parent)
        self.ao_salvar = ao_salvar
        self.usuario = usuario
        self.orcamento = orcamento
        self.title("Editar orçamento enviado" if orcamento else "Novo orçamento enviado")
        self.configure(bg=COR["surface"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        o = orcamento or {}
        nomes, emails = separar_destinatarios(o.get("destinatarios"))
        self.vars = {
            "os_numero": tk.StringVar(value=o.get("os_numero") or ""),
            "numero_serie": tk.StringVar(value=o.get("numero_serie") or ""),
            "cliente": tk.StringVar(value=o.get("cliente") or ""),
            "prazo_dias_uteis": tk.StringVar(
                value=str(o["prazo_dias_uteis"]) if o.get("prazo_dias_uteis") is not None else ""
            ),
            "data_orcamento": tk.StringVar(value=o.get("data_orcamento") or ""),
            "destinatarios_nomes": tk.StringVar(value=nomes),
            "destinatarios_emails": tk.StringVar(value=emails),
        }

        self._construir_formulario()
        self.bind("<Escape>", lambda e: self.destroy())

    def _linha_data(self, mestre, rotulo, chave, linha, coluna=0):
        tk.Label(mestre, text=rotulo, font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=linha, column=coluna, sticky="w", pady=(8, 2)
        )
        painel = tk.Frame(mestre, bg=COR["surface"])
        painel.grid(row=linha + 1, column=coluna, sticky="we", pady=(0, 4), padx=(0, 10))
        entry = ttk.Entry(painel, textvariable=self.vars[chave], width=13, font=FONTE_DADOS)
        entry.pack(side="left")
        btn = tk.Button(
            painel, text="📅", bd=0, bg=COR["acento_tint"], fg=COR["acento_escuro"],
            activebackground=COR["acento"], cursor="hand2", font=("Segoe UI", 9),
        )
        btn.pack(side="left", padx=(4, 0))
        anexar_seletor_data(self, btn, self.vars[chave])

    def _campo_texto_longo(self, mestre, rotulo, linha, altura=2):
        tk.Label(mestre, text=rotulo, font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=linha, column=0, columnspan=4, sticky="w", pady=(8, 2)
        )
        texto = tk.Text(mestre, width=54, height=altura, font=FONTE_BASE, wrap="word", relief="solid", bd=1)
        texto.grid(row=linha + 1, column=0, columnspan=4, sticky="we")
        return texto

    def _construir_formulario(self):
        if self.orcamento and self.orcamento.get("criado_por"):
            partes = [f"Criado por {self.orcamento['criado_por']}"]
            if (self.orcamento.get("atualizado_por")
                    and self.orcamento.get("atualizado_por") != self.orcamento.get("criado_por")):
                partes.append(f"última alteração por {self.orcamento['atualizado_por']}")
            tk.Label(
                self, text=" • ".join(partes), font=("Segoe UI", 8),
                bg=COR["surface"], fg=COR["ink_fraco"],
            ).pack(anchor="w", padx=20, pady=(10, 0))

        pad = tk.Frame(self, bg=COR["surface"], padx=20, pady=18)
        pad.pack(fill="both", expand=True)

        tk.Label(
            pad, text=("Editar orçamento enviado" if self.orcamento else "Novo orçamento enviado"),
            font=FONTE_TITULO, bg=COR["surface"], fg=COR["ink"],
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))

        tk.Label(pad, text="Cliente (empresa) *", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["cliente"], width=54, font=FONTE_BASE).grid(
            row=2, column=0, columnspan=4, sticky="we"
        )

        tk.Label(pad, text="Proposta/OS (OKSST)", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=3, column=0, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["os_numero"], width=16, font=FONTE_DADOS).grid(
            row=4, column=0, sticky="w", padx=(0, 10)
        )

        tk.Label(pad, text="Série Orkan Nº", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=3, column=1, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["numero_serie"], width=12, font=FONTE_DADOS).grid(
            row=4, column=1, sticky="w", padx=(0, 10)
        )

        tk.Label(pad, text="Prazo (dias úteis)", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=3, column=2, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["prazo_dias_uteis"], width=8, font=FONTE_DADOS).grid(
            row=4, column=2, sticky="w"
        )

        self._linha_data(pad, "Data do orçamento", "data_orcamento", 5, coluna=0)

        tk.Label(pad, text="Para — nome(s)", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["destinatarios_nomes"], width=26, font=FONTE_BASE).grid(
            row=8, column=0, columnspan=2, sticky="we", padx=(0, 10)
        )
        tk.Label(pad, text="Para — e-mail(s)", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"]).grid(
            row=7, column=2, columnspan=2, sticky="w", pady=(8, 2)
        )
        ttk.Entry(pad, textvariable=self.vars["destinatarios_emails"], width=26, font=FONTE_BASE).grid(
            row=8, column=2, columnspan=2, sticky="we"
        )

        self.txt_material = self._campo_texto_longo(pad, "Material recebido", 9, altura=3)
        self.txt_material.insert("1.0", (self.orcamento or {}).get("material_recebido") or "")

        botoes = tk.Frame(pad, bg=COR["surface"])
        botoes.grid(row=11, column=0, columnspan=4, sticky="e", pady=(18, 0))
        tk.Button(
            botoes, text="Cancelar", command=self.destroy, bd=0, padx=16, pady=7,
            bg=COR["bg"], fg=COR["ink_muted"], font=FONTE_BASE, cursor="hand2",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            botoes, text="Salvar", command=self._salvar, bd=0, padx=16, pady=7,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"],
        ).pack(side="left")

    def _salvar(self):
        payload = {k: v.get() for k, v in self.vars.items()}
        payload["destinatarios"] = combinar_destinatarios(
            payload.pop("destinatarios_nomes"), payload.pop("destinatarios_emails")
        )
        payload["material_recebido"] = self.txt_material.get("1.0", "end-1c")
        try:
            dados = validar_payload_orcamento(payload)
        except ValidationError as exc:
            messagebox.showwarning("Verifique os dados", str(exc), parent=self)
            return

        try:
            if self.orcamento:
                atualizar_orcamento(self.orcamento["id"], dados, self.usuario)
            else:
                inserir_orcamento(dados, self.usuario)
        except ValidationError as exc:
            messagebox.showerror("Não foi possível salvar", str(exc), parent=self)
            return
        self.destroy()
        self.ao_salvar()


# --------------------------------------------------------------------------
# Barra de rolagem responsiva (some sozinha quando todo o conteudo cabe)
# --------------------------------------------------------------------------

class BarraRolagemAuto(ttk.Scrollbar):
    """Barra de rolagem (vertical ou horizontal) que so aparece quando o
    conteudo nao cabe inteiro na area visivel — em janelas grandes ela some
    sozinha, em janelas pequenas volta a aparecer. Precisa estar posicionada
    com grid() (nao pack) na primeira exibicao, pois usa grid_remove()/
    grid() para se esconder e reaparecer no mesmo lugar."""

    def set(self, lo, hi):
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.grid_remove()
        else:
            self.grid()
        super().set(lo, hi)


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
            "numero_serie": tk.StringVar(value=item.get("numero_serie", "")),
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

        tk.Label(card, text="Nº de série do equipamento", font=("Segoe UI", 8), bg=COR["surface"],
                 fg=COR["ink_fraco"]).grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Entry(card, textvariable=vars_["numero_serie"], font=FONTE_BASE, width=70).grid(
            row=4, column=0, columnspan=4, sticky="we", pady=(0, 2)
        )

        tk.Label(card, text="Observações", font=("Segoe UI", 8), bg=COR["surface"], fg=COR["ink_fraco"]).grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )
        ttk.Entry(card, textvariable=vars_["observacoes"], font=FONTE_BASE, width=70).grid(
            row=6, column=0, columnspan=4, sticky="we", pady=(0, 2)
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
                "numero_serie": v["numero_serie"].get(),
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

        self.destroy()
        self.ao_salvar()


# --------------------------------------------------------------------------
# Dialogo de texto bruto (mostra o texto cru extraido de um PDF, so leitura)
# --------------------------------------------------------------------------

class DialogoTextoBruto(tk.Toplevel):
    def __init__(self, parent, titulo, texto):
        super().__init__(parent)
        self.title(titulo)
        self.configure(bg=COR["surface"])
        self.transient(parent)
        self.geometry("620x420")

        pad = tk.Frame(self, bg=COR["surface"], padx=14, pady=14)
        pad.pack(fill="both", expand=True)
        caixa = tk.Frame(pad, highlightbackground=COR["borda"], highlightthickness=1)
        caixa.pack(fill="both", expand=True)
        txt = tk.Text(caixa, font=FONTE_DADOS, wrap="word", relief="flat", bd=0)
        scrollbar = ttk.Scrollbar(caixa, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=scrollbar.set)
        txt.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        txt.insert("1.0", texto or "(nenhum texto extraído)")
        txt.configure(state="disabled")

        tk.Button(
            pad, text="Fechar", command=self.destroy, bd=0, padx=16, pady=7,
            bg=COR["bg"], fg=COR["ink_muted"], font=FONTE_BASE, cursor="hand2",
        ).pack(anchor="e", pady=(10, 0))
        self.bind("<Escape>", lambda e: self.destroy())


# --------------------------------------------------------------------------
# Dialogo de importacao de orcamentos a partir de PDF (propostas OKSST)
# --------------------------------------------------------------------------

class DialogoImportarPDFOrcamento(tk.Toplevel):
    def __init__(self, parent, ao_salvar, usuario):
        super().__init__(parent)
        self.ao_salvar = ao_salvar
        self.usuario = usuario
        self.cards = []
        self.title("Importar orçamentos de PDF")
        self.configure(bg=COR["surface"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._construir()
        self.bind("<Escape>", lambda e: self.destroy())

    def _construir(self):
        pad = tk.Frame(self, bg=COR["surface"], padx=20, pady=18)
        pad.pack(fill="both", expand=True)

        tk.Label(pad, text="Importar orçamentos de PDF", font=FONTE_TITULO,
                 bg=COR["surface"], fg=COR["ink"]).pack(anchor="w")
        tk.Label(
            pad, text="Selecione uma ou mais propostas OKSST em PDF. Os campos identificados\n"
                      "ficam abaixo para revisar/corrigir antes de importar.",
            font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"], justify="left",
        ).pack(anchor="w", pady=(2, 10))

        tk.Button(
            pad, text="📄  Selecionar PDF(s)...", command=self._selecionar_arquivos, bd=0, padx=14, pady=7,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"],
        ).pack(anchor="e", pady=(0, 14))

        self.lbl_resultado = tk.Label(pad, text="", font=FONTE_BASE, bg=COR["surface"], fg=COR["ink_muted"])
        self.lbl_resultado.pack(anchor="w")

        self.quadro = QuadroRolavel(pad, altura=300, bg=COR["surface"])
        self.quadro.pack(fill="both", expand=True, pady=(8, 14))

        botoes = tk.Frame(pad, bg=COR["surface"])
        botoes.pack(fill="x")
        tk.Button(
            botoes, text="Cancelar", command=self.destroy, bd=0, padx=16, pady=7,
            bg=COR["bg"], fg=COR["ink_muted"], font=FONTE_BASE, cursor="hand2",
        ).pack(side="right", padx=(8, 0))
        self.btn_importar = tk.Button(
            botoes, text="Importar orçamentos", command=self._importar, bd=0, padx=16, pady=7,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"], state="disabled",
        )
        self.btn_importar.pack(side="right")

    def _selecionar_arquivos(self):
        caminhos = filedialog.askopenfilenames(
            parent=self, title="Selecionar propostas (PDF)", filetypes=[("Arquivos PDF", "*.pdf")],
        )
        if not caminhos:
            return

        erros = []
        identificados = 0
        for caminho in caminhos:
            try:
                texto = extrair_texto_pdf(caminho)
            except Exception as exc:
                erros.append(f"{Path(caminho).name}: {exc}")
                continue
            item = interpretar_orcamento_pdf(texto)
            item["arquivo"] = Path(caminho).name
            item["texto_bruto"] = texto
            self._criar_card(item)
            identificados += 1

        partes = []
        if identificados:
            partes.append(f"{identificados} PDF(s) lido(s). Revise os campos antes de importar.")
        if erros:
            partes.append("Não consegui ler: " + "; ".join(erros))
        self.lbl_resultado.configure(
            text=" ".join(partes), fg=COR["vermelho"] if erros and not identificados else COR["verde"],
        )
        if self.cards:
            self.btn_importar.configure(state="normal")

    def _criar_card(self, item):
        card = tk.Frame(self.quadro.interior, bg=COR["surface"], highlightbackground=COR["borda"],
                         highlightthickness=1, padx=10, pady=8)
        card.pack(fill="x", pady=4, padx=2)

        nomes, emails = separar_destinatarios(item.get("destinatarios"))
        vars_ = {
            "os_numero": tk.StringVar(value=item["os_numero"]),
            "numero_serie": tk.StringVar(value=item["numero_serie"]),
            "cliente": tk.StringVar(value=item["cliente"]),
            "prazo_dias_uteis": tk.StringVar(value=item["prazo_dias_uteis"]),
            "data_orcamento": tk.StringVar(value=item["data_orcamento"]),
            "destinatarios_nomes": tk.StringVar(value=nomes),
            "destinatarios_emails": tk.StringVar(value=emails),
        }

        topo = tk.Frame(card, bg=COR["surface"])
        topo.grid(row=0, column=0, columnspan=4, sticky="we")
        tk.Label(
            topo, text=item.get("arquivo") or "(arquivo)", font=FONTE_BASE_NEG,
            bg=COR["surface"], fg=COR["ink"],
        ).pack(side="left")
        registro = {"frame": card, "vars": vars_, "texto_bruto": item.get("texto_bruto", ""),
                    "arquivo": item.get("arquivo", "")}
        tk.Button(
            topo, text="Ver texto extraído", bd=0, bg=COR["surface"], fg=COR["acento_escuro"],
            font=("Segoe UI", 8), cursor="hand2", command=lambda: self._ver_texto_bruto(registro),
        ).pack(side="right", padx=(0, 10))
        tk.Button(
            topo, text="Remover", bd=0, bg=COR["surface"], fg=COR["vermelho"],
            font=("Segoe UI", 8), cursor="hand2", command=lambda: self._remover_card(registro),
        ).pack(side="right")

        def campo(rotulo, chave, coluna, largura=16):
            tk.Label(card, text=rotulo, font=("Segoe UI", 8), bg=COR["surface"], fg=COR["ink_fraco"]).grid(
                row=1, column=coluna, sticky="w", pady=(6, 0)
            )
            ttk.Entry(card, textvariable=vars_[chave], width=largura, font=FONTE_DADOS).grid(
                row=2, column=coluna, sticky="w", padx=(0, 10)
            )

        campo("Proposta/OS (OKSST)", "os_numero", 0, largura=16)
        campo("Série Orkan Nº", "numero_serie", 1, largura=12)
        campo("Data do orçamento (AAAA-MM-DD)", "data_orcamento", 2, largura=13)
        campo("Prazo (dias úteis)", "prazo_dias_uteis", 3, largura=6)

        tk.Label(card, text="Cliente (empresa)", font=("Segoe UI", 8), bg=COR["surface"],
                 fg=COR["ink_fraco"]).grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Entry(card, textvariable=vars_["cliente"], font=FONTE_BASE, width=70).grid(
            row=4, column=0, columnspan=4, sticky="we", pady=(0, 2)
        )

        tk.Label(card, text="Para — nome(s)", font=("Segoe UI", 8), bg=COR["surface"],
                 fg=COR["ink_fraco"]).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Entry(card, textvariable=vars_["destinatarios_nomes"], font=FONTE_BASE, width=34).grid(
            row=6, column=0, columnspan=2, sticky="we", padx=(0, 10), pady=(0, 2)
        )
        tk.Label(card, text="Para — e-mail(s)", font=("Segoe UI", 8), bg=COR["surface"],
                 fg=COR["ink_fraco"]).grid(row=5, column=2, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Entry(card, textvariable=vars_["destinatarios_emails"], font=FONTE_BASE, width=34).grid(
            row=6, column=2, columnspan=2, sticky="we", pady=(0, 2)
        )

        tk.Label(card, text="Material recebido", font=("Segoe UI", 8), bg=COR["surface"],
                 fg=COR["ink_fraco"]).grid(row=7, column=0, columnspan=4, sticky="w", pady=(6, 0))
        registro["txt_material"] = tk.Text(card, font=FONTE_BASE, width=70, height=2, wrap="word",
                                             relief="solid", bd=1)
        registro["txt_material"].grid(row=8, column=0, columnspan=4, sticky="we", pady=(0, 2))
        registro["txt_material"].insert("1.0", item.get("material_recebido", ""))

        self.cards.append(registro)

    def _ver_texto_bruto(self, registro):
        DialogoTextoBruto(self, f"Texto extraído — {registro['arquivo']}", registro["texto_bruto"])

    def _remover_card(self, registro):
        registro["frame"].destroy()
        self.cards.remove(registro)
        if not self.cards:
            self.btn_importar.configure(state="disabled")

    def _importar(self):
        erros = []
        importados = 0
        for registro in list(self.cards):
            v = registro["vars"]
            payload = {
                "os_numero": v["os_numero"].get(),
                "numero_serie": v["numero_serie"].get(),
                "cliente": v["cliente"].get(),
                "prazo_dias_uteis": v["prazo_dias_uteis"].get(),
                "data_orcamento": v["data_orcamento"].get(),
                "destinatarios": combinar_destinatarios(
                    v["destinatarios_nomes"].get(), v["destinatarios_emails"].get()
                ),
                "material_recebido": registro["txt_material"].get("1.0", "end-1c"),
            }
            try:
                dados = validar_payload_orcamento(payload)
                inserir_orcamento(dados, self.usuario)
                importados += 1
            except ValidationError as exc:
                erros.append(f"{registro['arquivo'] or v['cliente'].get() or '(sem nome)'}: {exc}")

        if erros:
            messagebox.showwarning(
                "Alguns orçamentos não foram importados",
                f"{importados} orçamento(s) importado(s).\n\nErros:\n" + "\n".join(erros),
                parent=self,
            )
        else:
            messagebox.showinfo(
                "Importação concluída", f"{importados} orçamento(s) importado(s) com sucesso.", parent=self
            )

        self.destroy()
        self.ao_salvar()


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
        self.title(f"Entrar — Painel de Pendências {APP_VERSION}")
        self.configure(bg=COR["borda"])
        self.resizable(False, False)
        aplicar_icone_janela(self)
        self.modo_cadastro = False

        self.var_usuario = tk.StringVar()
        self.var_senha = tk.StringVar()
        self.var_nome_completo = tk.StringVar()
        self.var_confirmar = tk.StringVar()
        self.var_senha_admin = tk.StringVar()

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

        logo_login = carregar_logo(LOGO_ESCURA_PATH, 84)
        if logo_login is not None:
            tk.Label(self.pad, image=logo_login, bg=COR["surface"]).pack(anchor="center", pady=(0, 12))
        rotulo_titulo_versionado(self.pad, "Painel de Pendências", COR["surface"]).pack(
            anchor="center", pady=(0, 14)
        )

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
            self._campo("Senha do administrador", self.var_senha_admin, mostrar="*")
            tk.Label(
                self.pad, text="Somente o administrador pode autorizar a criação de novos usuários.",
                font=("Segoe UI", 8), bg=COR["surface"], fg=COR["ink_fraco"], wraplength=280, justify="left",
            ).pack(anchor="w", pady=(2, 0))
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

    def _selecionar_aba(self, cadastro):
        if self.modo_cadastro == cadastro:
            return
        self.modo_cadastro = cadastro
        self._redesenhar()

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
        try:
            usuario = autenticar_usuario(nome, senha)
        except ValidationError as exc:
            messagebox.showerror("Não foi possível entrar", str(exc), parent=self)
            return
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

        admin_senha = get_admin_senha()
        if not admin_senha or self.var_senha_admin.get() != admin_senha:
            messagebox.showerror(
                "Senha de administrador incorreta",
                "Somente o administrador pode criar novos usuários. Peça para ele digitar a senha.",
                parent=self,
            )
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

FILTROS_APROVADOS = [
    ("todos", "Todos"),
    ("aguardando", "Aguardando peça"),
    ("liberado", "Liberado"),
]


class App(tk.Tk):
    def __init__(self, usuario_atual):
        super().__init__()
        self.usuario_atual = usuario_atual
        self.title(f"Painel de Pendências {APP_VERSION}")
        self.configure(bg=COR["bg"])
        aplicar_icone_janela(self)
        self.geometry("1220x720")
        self.minsize(1040, 600)
        self.state("zoomed")

        self.filtro_ativo = tk.StringVar(value="todos")
        self.busca = tk.StringVar()
        self.busca.trace_add("write", lambda *_: self._renderizar_tabela())
        self.marcados = set()

        self.filtro_aprovados_ativo = tk.StringVar(value="todos")
        self.busca_aprovados = tk.StringVar()
        self.busca_aprovados.trace_add("write", lambda *_: self._renderizar_tabela_aprovados())
        self.marcados_aprovados = set()

        self.busca_orcamentos = tk.StringVar()
        self.busca_orcamentos.trace_add("write", lambda *_: self._renderizar_tabela_orcamentos())
        self.marcados_orcamentos = set()

        self._configurar_estilos()
        self._montar_rodape_global()
        self._montar_sidebar()

        self.conteudo = tk.Frame(self, bg=COR["bg"])
        self.conteudo.pack(side="left", fill="both", expand=True)
        self.conteudo.grid_rowconfigure(0, weight=1)
        self.conteudo.grid_columnconfigure(0, weight=1)

        self.pagina_estoque = tk.Frame(self.conteudo, bg=COR["bg"])
        self.pagina_estoque.grid(row=0, column=0, sticky="nsew")
        self.pagina_aprovados = tk.Frame(self.conteudo, bg=COR["bg"])
        self.pagina_aprovados.grid(row=0, column=0, sticky="nsew")
        self.pagina_pecas_finalizados = tk.Frame(self.conteudo, bg=COR["bg"])
        self.pagina_pecas_finalizados.grid(row=0, column=0, sticky="nsew")
        self.pagina_aprovados_finalizados = tk.Frame(self.conteudo, bg=COR["bg"])
        self.pagina_aprovados_finalizados.grid(row=0, column=0, sticky="nsew")
        self.pagina_orcamentos = tk.Frame(self.conteudo, bg=COR["bg"])
        self.pagina_orcamentos.grid(row=0, column=0, sticky="nsew")
        self.pagina_orcamentos_finalizados = tk.Frame(self.conteudo, bg=COR["bg"])
        self.pagina_orcamentos_finalizados.grid(row=0, column=0, sticky="nsew")

        self._montar_topo()
        self._montar_kpis()
        self._montar_toolbar()
        self._montar_tabela()
        self._montar_rodape()

        self._montar_topo_aprovados()
        self._montar_toolbar_aprovados()
        self._montar_tabela_aprovados()
        self._montar_rodape_aprovados()

        self._montar_topo_orcamentos()
        self._montar_toolbar_orcamentos()
        self._montar_tabela_orcamentos()
        self._montar_rodape_orcamentos()

        self._montar_pagina_pecas_finalizados()
        self._montar_pagina_aprovados_finalizados()
        self._montar_pagina_orcamentos_finalizados()

        self.pedidos = []
        self.ordenar_coluna_atual = None
        self.ordenar_reverso = False

        self.equipamentos = []
        self.ordenar_coluna_aprovados_atual = None
        self.ordenar_reverso_aprovados = False

        self.orcamentos = []
        self.ordenar_coluna_orcamentos_atual = None
        self.ordenar_reverso_orcamentos = False

        self.pedidos_finalizados = []
        self.marcados_pecas_finalizados = set()
        self.equipamentos_finalizados = []
        self.marcados_aprovados_finalizados = set()
        self.orcamentos_finalizados = []
        self.marcados_orcamentos_finalizados = set()

        self.icone_bandeja = None
        self._configurar_bandeja()

        self._selecionar_pagina("estoque")
        self.atualizar_dados()
        self.atualizar_dados_aprovados()
        self.atualizar_dados_orcamentos()
        self._backup_automatico_ao_iniciar()

    # -- rodape fixo (botao Sair, visivel em qualquer pagina) ---------------

    def _montar_rodape_global(self):
        rodape = tk.Frame(self, bg=COR["bg"])
        rodape.pack(side="bottom", fill="x")
        tk.Frame(rodape, bg=COR["borda"], height=1).pack(side="top", fill="x")
        tk.Button(
            rodape, text="✖  Sair", command=self._sair_de_vez, bd=1, relief="solid",
            bg=COR["surface"], fg=COR["vermelho"], font=FONTE_BASE, padx=12, pady=6,
            cursor="hand2", highlightbackground=COR["borda"],
        ).pack(side="right", padx=16, pady=8)

    # -- menu lateral fixo (troca de pagina) --------------------------------

    def _montar_sidebar(self):
        sidebar = tk.Frame(self, bg=COR["acento_escuro"], width=90)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        logo_sidebar = carregar_logo(LOGO_CLARA_PATH, 58)
        if logo_sidebar is not None:
            tk.Label(sidebar, image=logo_sidebar, bg=COR["acento_escuro"]).pack(pady=(20, 0))

        self.sidebar_botoes = {}
        opcoes = [
            ("estoque", "📦", "Peças"), ("aprovados", "✅", "Aprovados"), ("orcamentos", "📄", "Orçamentos"),
        ]
        for chave, icone, rotulo in opcoes:
            item = tk.Frame(sidebar, bg=COR["acento_escuro"], cursor="hand2")
            item.pack(fill="x", pady=(18, 0))
            lbl_icone = tk.Label(item, text=icone, font=("Segoe UI", 22), bg=COR["acento_escuro"], fg="white")
            lbl_icone.pack(pady=(8, 2))
            lbl_texto = tk.Label(item, text=rotulo, font=("Segoe UI", 8, "bold"),
                                  bg=COR["acento_escuro"], fg="white")
            lbl_texto.pack(pady=(0, 8))
            for widget in (item, lbl_icone, lbl_texto):
                widget.bind("<Button-1>", lambda e, c=chave: self._selecionar_pagina(c))
            self.sidebar_botoes[chave] = (item, lbl_icone, lbl_texto)

    def _selecionar_pagina(self, pagina):
        self.pagina_ativa = pagina
        for chave, widgets in self.sidebar_botoes.items():
            cor_fundo = COR["acento"] if chave == pagina else COR["acento_escuro"]
            for widget in widgets:
                widget.configure(bg=cor_fundo)
        if pagina == "estoque":
            self.pagina_estoque.tkraise()
        elif pagina == "aprovados":
            self.pagina_aprovados.tkraise()
        else:
            self.pagina_orcamentos.tkraise()

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

    # -- backup local (copia de seguranca na pasta de instalacao) ----------

    def _backup_automatico_ao_iniciar(self):
        def tarefa():
            try:
                fazer_backup_local()
                self.after(0, lambda: self._mostrar_status_backup(True))
            except Exception:
                self.after(0, lambda: self._mostrar_status_backup(False))

        threading.Thread(target=tarefa, daemon=True).start()

    def _fazer_backup_manual(self):
        try:
            destino = fazer_backup_local()
        except ValidationError as exc:
            messagebox.showwarning("Backup não realizado", str(exc))
            return
        self._mostrar_status_backup(True)
        messagebox.showinfo("Backup concluído", f"Cópia local salva em:\n{destino}")

    def _mostrar_status_backup(self, sucesso):
        agora = datetime.now().strftime("%H:%M")
        if sucesso:
            texto, cor = f"✔ Backup local OK — {agora}", COR["verde"]
        else:
            texto, cor = f"✖ Backup local não realizado — {agora}", COR["vermelho"]
        self.status_backup_lbl.configure(text=texto, fg=cor)
        self.status_backup_aprovados_lbl.configure(text=texto, fg=cor)
        self.status_backup_orcamentos_lbl.configure(text=texto, fg=cor)

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
        topo = tk.Frame(self.pagina_estoque, bg=COR["bg"])
        topo.pack(fill="x", padx=24, pady=(20, 6))

        esquerda = tk.Frame(topo, bg=COR["bg"])
        esquerda.pack(side="left")
        rotulo_titulo_versionado(esquerda, "Painel de Pendências", COR["bg"]).pack(anchor="w")
        nome_exibido = self.usuario_atual.get("nome_completo") or self.usuario_atual["nome_usuario"]
        tk.Label(esquerda, text=f"Compras de componentes eletrônicos — Soto Company  •  Logado como {nome_exibido}",
                 font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_muted"]).pack(anchor="w")

        direita = tk.Frame(topo, bg=COR["bg"])
        direita.pack(side="right")
        # Como o banco agora fica na nuvem (compartilhado entre PCs), o botao
        # "Atualizar" busca o que outras pessoas cadastraram nesse meio tempo.
        self._botao_secundario(direita, "⟳  Atualizar", self.atualizar_dados).pack(side="left", padx=(0, 8))
        self._botao_secundario(direita, "Compras finalizadas", self._ir_para_pecas_finalizados).pack(
            side="left", padx=(0, 8)
        )
        self._botao_secundario(direita, "Histórico", self._abrir_historico).pack(side="left", padx=(0, 8))
        self._botao_secundario(direita, "Backup agora", self._fazer_backup_manual).pack(side="left")

    def _botao_secundario(self, mestre, texto, comando):
        return tk.Button(
            mestre, text=texto, command=comando, bd=1, relief="solid",
            bg=COR["surface"], fg=COR["ink"], font=FONTE_BASE, padx=12, pady=6,
            cursor="hand2", highlightbackground=COR["borda"],
        )

    def _montar_kpis(self):
        container = tk.Frame(self.pagina_estoque, bg=COR["bg"])
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
        barra = tk.Frame(self.pagina_estoque, bg=COR["bg"])
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
        container = tk.Frame(self.pagina_estoque, bg=COR["bg"])
        container.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        colunas = (
            "marcar", "status", "fornecedor", "componente", "numero_pedido", "numero_serie",
            "criado_por", "quantidade", "data_pedido", "data_compra", "previsao_entrega",
            "data_chegada", "data_chegada_indaiatuba",
        )
        titulos = {
            "marcar": "", "status": "Status", "fornecedor": "Fornecedor", "componente": "Componente",
            "numero_pedido": "Nº Pedido", "numero_serie": "Nº Série (equip.)", "criado_por": "Solicitado por",
            "quantidade": "Qtd", "data_pedido": "Data Pedido",
            "data_compra": "Data Compra", "previsao_entrega": "Previsão", "data_chegada": "Chegada SBC",
            "data_chegada_indaiatuba": "Chegada Indaiatuba",
        }
        larguras = {
            "marcar": 34, "status": 170, "fornecedor": 150, "componente": 190, "numero_pedido": 100,
            "numero_serie": 130, "criado_por": 120, "quantidade": 60, "data_pedido": 95,
            "data_compra": 95, "previsao_entrega": 95, "data_chegada": 95, "data_chegada_indaiatuba": 120,
        }

        self.tree = ttk.Treeview(container, columns=colunas, show="headings", selectmode="browse")
        for col in colunas:
            if col == "marcar":
                self.tree.heading(col, text="")
                self.tree.column(col, width=larguras[col], anchor="center", stretch=False)
                continue
            self.tree.heading(col, text=titulos[col], anchor="center", command=lambda c=col: self._ordenar_por(c))
            # stretch=False: colunas mantem a largura padrao mesmo com a janela
            # menor, em vez de espremer o texto ("encavalar") — quem quiser ver
            # as colunas que sairam da tela usa a barra de rolagem horizontal.
            self.tree.column(col, width=larguras[col], anchor="center", stretch=False)

        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        scrollbar_v = BarraRolagemAuto(container, orient="vertical", command=self.tree.yview)
        scrollbar_h = BarraRolagemAuto(container, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar_v.set, xscrollcommand=scrollbar_h.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar_v.grid(row=0, column=1, sticky="ns")
        scrollbar_h.grid(row=1, column=0, sticky="ew")

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

        acoes = tk.Frame(self.pagina_estoque, bg=COR["bg"])
        acoes.pack(fill="x", padx=24, pady=(0, 4))
        self._botao_secundario(acoes, "Editar selecionado", self._abrir_editar).pack(side="left")
        self._botao_secundario(acoes, "Excluir marcados", self._excluir_marcados).pack(side="left", padx=8)
        self._botao_secundario(acoes, "Finalizar marcados", self._finalizar_marcados).pack(side="left")

    def _montar_rodape(self):
        rodape = tk.Frame(self.pagina_estoque, bg=COR["bg"])
        rodape.pack(fill="x", padx=24, pady=(0, 14))
        self.rodape_lbl = tk.Label(rodape, text="", font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_fraco"])
        self.rodape_lbl.pack(anchor="w")
        self.status_backup_lbl = tk.Label(rodape, text="", font=("Segoe UI", 8), bg=COR["bg"], fg=COR["verde"])
        self.status_backup_lbl.pack(anchor="w")

    # -- dados / renderizacao ----------------------------------------------

    def atualizar_dados(self):
        try:
            self.pedidos = carregar_pedidos()
        except ValidationError as exc:
            messagebox.showerror("Erro ao carregar dados", str(exc))
            self.pedidos = []
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
                alvo = (
                    f"{p['fornecedor']} {p['componente']} {p['numero_pedido'] or ''} "
                    f"{p['numero_serie'] or ''} {p['criado_por'] or ''}"
                ).lower()
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
            # Padrao: data do pedido do mais velho para o mais novo. Pedidos
            # sem data de pedido ficam por ultimo.
            pedidos.sort(key=lambda p: parse_date(p["data_pedido"]) or date.max)

        for p in pedidos:
            ind = p["indicador"]
            valores = ["☑" if p["id"] in self.marcados else "☐"]
            valores.extend((
                f"{ind['simbolo']}  {ind['rotulo']}",
                p["fornecedor"], p["componente"], p["numero_pedido"] or "—",
                p["numero_serie"] or "—", p["criado_por"] or "—",
                p["quantidade"],
                formatar_data_br(p["data_pedido"]), formatar_data_br(p["data_compra"]),
                formatar_data_br(p["previsao_entrega"]), formatar_data_br(p["data_chegada"]),
                formatar_data_br(p["data_chegada_indaiatuba"]),
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
        chaves_data = {
            "data_pedido", "data_compra", "previsao_entrega", "data_chegada", "data_chegada_indaiatuba",
        }
        def chave(p):
            if coluna == "status":
                return p["indicador"]["rotulo"]
            if coluna in chaves_data:
                d = parse_date(p[coluna])
                return d or date.min
            if coluna == "quantidade":
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
        DialogoHistorico(self)

    def _excluir_marcados(self):
        if not self.marcados:
            messagebox.showinfo(
                "Nenhum pedido marcado",
                "Marque a caixinha (☐) na frente das linhas que deseja excluir.",
            )
            return

        if not confirmar_senha_admin(
            self, "Somente o administrador pode excluir componentes. Digite a senha de administrador:"
        ):
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
            self.atualizar_dados()

    def _finalizar_marcados(self):
        if not self.marcados:
            messagebox.showinfo(
                "Nenhum pedido marcado",
                "Marque a caixinha (☐) na frente das linhas que deseja finalizar.",
            )
            return

        marcados = [p for p in self.pedidos if p["id"] in self.marcados]
        linhas = "\n".join(f"- {p['componente']} ({p['fornecedor']})" for p in marcados[:10])
        if len(marcados) > 10:
            linhas += f"\n… e mais {len(marcados) - 10}"

        if messagebox.askyesno(
            "Finalizar pedidos marcados",
            f"Tem certeza que deseja finalizar {len(marcados)} pedido(s)?\n\n{linhas}\n\n"
            "Eles saem desta lista e vão para \"Compras finalizadas\" — dá pra trazer de volta por lá.",
        ):
            for p in marcados:
                finalizar_pedido(p["id"], self.usuario_atual["nome_usuario"])
                self.marcados.discard(p["id"])
            self.atualizar_dados()

    # -- submenu "Compras finalizadas" --------------------------------------

    def _montar_pagina_pecas_finalizados(self):
        pad = self.pagina_pecas_finalizados

        topo = tk.Frame(pad, bg=COR["bg"])
        topo.pack(fill="x", padx=24, pady=(20, 6))
        esquerda = tk.Frame(topo, bg=COR["bg"])
        esquerda.pack(side="left")
        rotulo_titulo_versionado(esquerda, "Compras de Componentes Finalizadas", COR["bg"]).pack(anchor="w")
        tk.Label(esquerda, text="Pedidos finalizados — não aparecem mais na lista principal.",
                 font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_muted"]).pack(anchor="w")
        direita = tk.Frame(topo, bg=COR["bg"])
        direita.pack(side="right")
        self._botao_secundario(direita, "← Voltar", self._voltar_de_pecas_finalizados).pack(
            side="left", padx=(0, 8)
        )
        self._botao_secundario(direita, "⟳  Atualizar", self.atualizar_dados_pecas_finalizados).pack(side="left")

        container = tk.Frame(pad, bg=COR["bg"])
        container.pack(fill="both", expand=True, padx=24, pady=(8, 8))

        colunas = ("marcar", "status", "fornecedor", "componente", "numero_pedido", "numero_serie", "criado_por")
        titulos = {
            "marcar": "", "status": "Status", "fornecedor": "Fornecedor", "componente": "Componente",
            "numero_pedido": "Nº Pedido", "numero_serie": "Nº Série (equip.)", "criado_por": "Solicitado por",
        }
        larguras = {
            "marcar": 34, "status": 170, "fornecedor": 150, "componente": 190, "numero_pedido": 100,
            "numero_serie": 130, "criado_por": 120,
        }

        self.tree_pecas_finalizados = ttk.Treeview(
            container, columns=colunas, show="headings", selectmode="browse"
        )
        for col in colunas:
            if col == "marcar":
                self.tree_pecas_finalizados.heading(col, text="")
                self.tree_pecas_finalizados.column(col, width=larguras[col], anchor="center", stretch=False)
                continue
            self.tree_pecas_finalizados.heading(col, text=titulos[col])
            self.tree_pecas_finalizados.column(col, width=larguras[col], anchor="w", stretch=False)

        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        scrollbar_v = BarraRolagemAuto(container, orient="vertical", command=self.tree_pecas_finalizados.yview)
        scrollbar_h = BarraRolagemAuto(container, orient="horizontal", command=self.tree_pecas_finalizados.xview)
        self.tree_pecas_finalizados.configure(yscrollcommand=scrollbar_v.set, xscrollcommand=scrollbar_h.set)
        self.tree_pecas_finalizados.grid(row=0, column=0, sticky="nsew")
        scrollbar_v.grid(row=0, column=1, sticky="ns")
        scrollbar_h.grid(row=1, column=0, sticky="ew")
        self.tree_pecas_finalizados.bind("<Button-1>", self._ao_clicar_pecas_finalizados, add="+")

        acoes = tk.Frame(pad, bg=COR["bg"])
        acoes.pack(fill="x", padx=24, pady=(0, 4))
        self._botao_secundario(acoes, "Reabrir marcados", self._reabrir_marcados_pecas).pack(side="left")

        rodape = tk.Frame(pad, bg=COR["bg"])
        rodape.pack(fill="x", padx=24, pady=(4, 14))
        self.rodape_pecas_finalizados_lbl = tk.Label(
            rodape, text="", font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_fraco"]
        )
        self.rodape_pecas_finalizados_lbl.pack(anchor="w")

    def _ir_para_pecas_finalizados(self):
        self.atualizar_dados_pecas_finalizados()
        self.pagina_pecas_finalizados.tkraise()

    def _voltar_de_pecas_finalizados(self):
        self.pagina_estoque.tkraise()

    def atualizar_dados_pecas_finalizados(self):
        try:
            self.pedidos_finalizados = carregar_pedidos(apenas_finalizados=True)
        except ValidationError as exc:
            messagebox.showerror("Erro ao carregar dados", str(exc))
            self.pedidos_finalizados = []
        self._renderizar_tabela_pecas_finalizados()

    def _renderizar_tabela_pecas_finalizados(self):
        self.tree_pecas_finalizados.delete(*self.tree_pecas_finalizados.get_children())
        for p in self.pedidos_finalizados:
            ind = p["indicador"]
            valores = ["☑" if p["id"] in self.marcados_pecas_finalizados else "☐"]
            valores.extend((
                f"{ind['simbolo']}  {ind['rotulo']}",
                p["fornecedor"], p["componente"], p["numero_pedido"] or "—",
                p["numero_serie"] or "—", p["criado_por"] or "—",
            ))
            self.tree_pecas_finalizados.insert("", "end", iid=str(p["id"]), values=tuple(valores))

        rodape = f"{len(self.pedidos_finalizados)} pedido(s) finalizado(s)"
        if self.marcados_pecas_finalizados:
            rodape += f"  •  {len(self.marcados_pecas_finalizados)} marcado(s) para reabrir"
        self.rodape_pecas_finalizados_lbl.configure(text=rodape)

    def _ao_clicar_pecas_finalizados(self, evento):
        if self.tree_pecas_finalizados.identify_region(evento.x, evento.y) != "cell":
            return
        if self.tree_pecas_finalizados.identify_column(evento.x) != "#1":
            return
        item = self.tree_pecas_finalizados.identify_row(evento.y)
        if not item:
            return
        pedido_id = int(item)
        if pedido_id in self.marcados_pecas_finalizados:
            self.marcados_pecas_finalizados.discard(pedido_id)
        else:
            self.marcados_pecas_finalizados.add(pedido_id)
        self._renderizar_tabela_pecas_finalizados()

    def _reabrir_marcados_pecas(self):
        if not self.marcados_pecas_finalizados:
            messagebox.showinfo(
                "Nenhum pedido marcado",
                "Marque a caixinha (☐) na frente das linhas que deseja reabrir.",
            )
            return
        marcados = [p for p in self.pedidos_finalizados if p["id"] in self.marcados_pecas_finalizados]
        if messagebox.askyesno(
            "Reabrir pedidos marcados",
            f"Trazer {len(marcados)} pedido(s) de volta para a lista principal?",
        ):
            for p in marcados:
                reabrir_pedido(p["id"], self.usuario_atual["nome_usuario"])
                self.marcados_pecas_finalizados.discard(p["id"])
            self.atualizar_dados_pecas_finalizados()

    # ========================================================================
    # Pagina "Aprovados" (equipamentos aprovados aguardando peca / liberacao)
    # ========================================================================

    def _montar_topo_aprovados(self):
        topo = tk.Frame(self.pagina_aprovados, bg=COR["bg"])
        topo.pack(fill="x", padx=24, pady=(20, 6))

        esquerda = tk.Frame(topo, bg=COR["bg"])
        esquerda.pack(side="left")
        rotulo_titulo_versionado(esquerda, "Equipamentos Aprovados", COR["bg"]).pack(anchor="w")
        nome_exibido = self.usuario_atual.get("nome_completo") or self.usuario_atual["nome_usuario"]
        tk.Label(
            esquerda, text=f"Aguardando peça / liberação — Soto Company  •  Logado como {nome_exibido}",
            font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_muted"],
        ).pack(anchor="w")

        direita = tk.Frame(topo, bg=COR["bg"])
        direita.pack(side="right")
        self._botao_secundario(direita, "⟳  Atualizar", self.atualizar_dados_aprovados).pack(side="left", padx=(0, 8))
        self._botao_secundario(direita, "Finalizados", self._ir_para_aprovados_finalizados).pack(side="left")

    def _montar_toolbar_aprovados(self):
        barra = tk.Frame(self.pagina_aprovados, bg=COR["bg"])
        barra.pack(fill="x", padx=24, pady=(6, 8))

        busca_frame = tk.Frame(barra, bg=COR["surface"], highlightbackground=COR["borda"],
                                highlightthickness=1)
        busca_frame.pack(side="left", ipady=4)
        tk.Label(busca_frame, text="🔍", bg=COR["surface"], fg=COR["ink_fraco"]).pack(side="left", padx=(8, 2))
        tk.Entry(busca_frame, textvariable=self.busca_aprovados, font=FONTE_BASE, width=28, bd=0,
                 bg=COR["surface"]).pack(side="left", padx=(0, 8), pady=4)

        chips = tk.Frame(barra, bg=COR["bg"])
        chips.pack(side="left", padx=16)
        self.chip_botoes_aprovados = {}
        for chave, rotulo in FILTROS_APROVADOS:
            btn = tk.Label(chips, text=rotulo, font=FONTE_BASE, padx=12, pady=5, cursor="hand2")
            btn.pack(side="left", padx=3)
            btn.bind("<Button-1>", lambda e, c=chave: self._selecionar_filtro_aprovados(c))
            self.chip_botoes_aprovados[chave] = btn
        self._atualizar_chips_aprovados()

        tk.Button(
            barra, text="+  Novo equipamento", command=self._abrir_novo_equipamento, bd=0, padx=16, pady=7,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"],
        ).pack(side="right")

    def _selecionar_filtro_aprovados(self, chave):
        self.filtro_aprovados_ativo.set(chave)
        self._atualizar_chips_aprovados()
        self._renderizar_tabela_aprovados()

    def _atualizar_chips_aprovados(self):
        ativo = self.filtro_aprovados_ativo.get()
        for chave, btn in self.chip_botoes_aprovados.items():
            if chave == ativo:
                btn.configure(bg=COR["acento"], fg="white", font=FONTE_BASE_NEG)
            else:
                btn.configure(bg=COR["surface"], fg=COR["ink_muted"], font=FONTE_BASE)

    def _montar_tabela_aprovados(self):
        container = tk.Frame(self.pagina_aprovados, bg=COR["bg"])
        container.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        colunas = (
            "marcar", "status", "pedido_numero", "cliente", "os_numero", "numero_serie",
            "aprovado_em", "liberacao_em", "liberacao_efetiva_em", "tecnico",
        )
        titulos = {
            "marcar": "", "status": "Status", "pedido_numero": "Pedido Nº", "cliente": "Cliente", "os_numero": "OS.",
            "numero_serie": "Série Orkan Nº", "aprovado_em": "Aprovado em", "liberacao_em": "Prazo liberação",
            "liberacao_efetiva_em": "Liberado em (efetivo)", "tecnico": "Técnico/Usuário",
        }
        larguras = {
            "marcar": 34, "status": 170, "pedido_numero": 100, "cliente": 140, "os_numero": 100, "numero_serie": 110,
            "aprovado_em": 100, "liberacao_em": 110, "liberacao_efetiva_em": 140, "tecnico": 130,
        }

        self.tree_aprovados = ttk.Treeview(container, columns=colunas, show="headings", selectmode="browse")
        for col in colunas:
            if col == "marcar":
                self.tree_aprovados.heading(col, text="")
                self.tree_aprovados.column(col, width=larguras[col], anchor="center", stretch=False)
                continue
            self.tree_aprovados.heading(
                col, text=titulos[col], anchor="center", command=lambda c=col: self._ordenar_por_aprovados(c)
            )
            self.tree_aprovados.column(col, width=larguras[col], anchor="center", stretch=False)

        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        scrollbar_v = BarraRolagemAuto(container, orient="vertical", command=self.tree_aprovados.yview)
        scrollbar_h = BarraRolagemAuto(container, orient="horizontal", command=self.tree_aprovados.xview)
        self.tree_aprovados.configure(yscrollcommand=scrollbar_v.set, xscrollcommand=scrollbar_h.set)
        self.tree_aprovados.grid(row=0, column=0, sticky="nsew")
        scrollbar_v.grid(row=0, column=1, sticky="ns")
        scrollbar_h.grid(row=1, column=0, sticky="ew")

        for cor_chave, cor_fundo in [
            ("verde", COR["verde_tint"]),
            ("amarelo", COR["amarelo_tint"]),
            ("vermelho", COR["vermelho_tint"]),
            ("cinza", COR["cinza_tint"]),
        ]:
            self.tree_aprovados.tag_configure(cor_chave, background=cor_fundo)

        self.tree_aprovados.bind("<Double-1>", lambda e: self._abrir_editar_equipamento())
        self.tree_aprovados.bind("<Button-1>", self._ao_clicar_tabela_aprovados, add="+")

        acoes = tk.Frame(self.pagina_aprovados, bg=COR["bg"])
        acoes.pack(fill="x", padx=24, pady=(0, 4))
        self._botao_secundario(acoes, "Editar selecionado", self._abrir_editar_equipamento).pack(side="left")
        self._botao_secundario(acoes, "Excluir marcados", self._excluir_marcados_aprovados).pack(
            side="left", padx=8
        )
        self._botao_secundario(acoes, "Finalizar marcados", self._finalizar_marcados_aprovados).pack(side="left")

    def _montar_rodape_aprovados(self):
        rodape = tk.Frame(self.pagina_aprovados, bg=COR["bg"])
        rodape.pack(fill="x", padx=24, pady=(0, 14))
        self.rodape_aprovados_lbl = tk.Label(rodape, text="", font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_fraco"])
        self.rodape_aprovados_lbl.pack(anchor="w")
        self.status_backup_aprovados_lbl = tk.Label(
            rodape, text="", font=("Segoe UI", 8), bg=COR["bg"], fg=COR["verde"]
        )
        self.status_backup_aprovados_lbl.pack(anchor="w")

    # -- dados / renderizacao (aprovados) -----------------------------------

    def atualizar_dados_aprovados(self):
        try:
            self.equipamentos = carregar_equipamentos()
        except ValidationError as exc:
            messagebox.showerror("Erro ao carregar dados", str(exc))
            self.equipamentos = []
        self._renderizar_tabela_aprovados()

    def _equipamentos_filtrados(self):
        termo = self.busca_aprovados.get().strip().lower()
        filtro = self.filtro_aprovados_ativo.get()
        resultado = []
        for e in self.equipamentos:
            if filtro == "aguardando" and not e["aguardando_peca"]:
                continue
            if filtro == "liberado" and e["aguardando_peca"]:
                continue
            if termo:
                alvo = (
                    f"{e['pedido_numero'] or ''} {e['cliente'] or ''} {e['os_numero'] or ''} "
                    f"{e['numero_serie'] or ''} {_tecnico_exibido(e)}"
                ).lower()
                if termo not in alvo:
                    continue
            resultado.append(e)
        return resultado

    def _renderizar_tabela_aprovados(self):
        self.tree_aprovados.delete(*self.tree_aprovados.get_children())
        equipamentos = self._equipamentos_filtrados()

        if self.ordenar_coluna_aprovados_atual:
            equipamentos = self._ordenar_lista_aprovados(
                equipamentos, self.ordenar_coluna_aprovados_atual, self.ordenar_reverso_aprovados
            )
        else:
            # Padrao: prazo de liberacao mais proximo do vencimento primeiro.
            # Equipamentos sem prazo definido ficam por ultimo.
            equipamentos.sort(key=lambda e: parse_date(e["liberacao_em"]) or date.max)

        for e in equipamentos:
            ind = compute_indicador_aprovado(e)
            valores = ["☑" if e["id"] in self.marcados_aprovados else "☐"]
            valores.extend((
                f"{ind['simbolo']}  {ind['rotulo']}",
                e["pedido_numero"] or "—", e["cliente"], e["os_numero"], e["numero_serie"] or "—",
                formatar_data_br(e["aprovado_em"]), formatar_data_br(e["liberacao_em"]),
                formatar_data_br(e["liberacao_efetiva_em"]), _tecnico_exibido(e) or "—",
            ))
            self.tree_aprovados.insert("", "end", iid=str(e["id"]), values=tuple(valores), tags=(ind["cor"],))

        total = len(equipamentos)
        rodape = f"{total} equipamento(s) exibido(s) de {len(self.equipamentos)} no total"
        if self.marcados_aprovados:
            rodape += f"  •  {len(self.marcados_aprovados)} marcado(s) para excluir"
        self.rodape_aprovados_lbl.configure(text=rodape)

    def _ao_clicar_tabela_aprovados(self, evento):
        if self.tree_aprovados.identify_region(evento.x, evento.y) != "cell":
            return
        if self.tree_aprovados.identify_column(evento.x) != "#1":  # coluna "marcar"
            return
        item = self.tree_aprovados.identify_row(evento.y)
        if not item:
            return
        equipamento_id = int(item)
        if equipamento_id in self.marcados_aprovados:
            self.marcados_aprovados.discard(equipamento_id)
        else:
            self.marcados_aprovados.add(equipamento_id)
        self._renderizar_tabela_aprovados()

    def _ordenar_por_aprovados(self, coluna):
        if self.ordenar_coluna_aprovados_atual == coluna:
            self.ordenar_reverso_aprovados = not self.ordenar_reverso_aprovados
        else:
            self.ordenar_coluna_aprovados_atual = coluna
            self.ordenar_reverso_aprovados = False
        self._renderizar_tabela_aprovados()

    @staticmethod
    def _ordenar_lista_aprovados(equipamentos, coluna, reverso):
        chaves_data = {"aprovado_em", "liberacao_em", "liberacao_efetiva_em"}
        def chave(e):
            if coluna == "status":
                return compute_indicador_aprovado(e)["rotulo"]
            if coluna in chaves_data:
                d = parse_date(e[coluna])
                return d or date.min
            if coluna == "tecnico":
                return _tecnico_exibido(e).lower()
            return str(e.get(coluna) or "").lower()
        return sorted(equipamentos, key=chave, reverse=reverso)

    # -- selecao / crud (aprovados) ------------------------------------------

    def _equipamento_selecionado(self):
        selecao = self.tree_aprovados.selection()
        if not selecao:
            messagebox.showinfo("Selecione um equipamento", "Clique em uma linha da tabela primeiro.")
            return None
        equipamento_id = int(selecao[0])
        return next((e for e in self.equipamentos if e["id"] == equipamento_id), None)

    def _abrir_novo_equipamento(self):
        DialogoEquipamento(
            self, ao_salvar=self.atualizar_dados_aprovados, usuario=self.usuario_atual["nome_usuario"]
        )

    def _abrir_editar_equipamento(self):
        equipamento = self._equipamento_selecionado()
        if equipamento:
            DialogoEquipamento(
                self, ao_salvar=self.atualizar_dados_aprovados, usuario=self.usuario_atual["nome_usuario"],
                equipamento=equipamento,
            )

    def _excluir_marcados_aprovados(self):
        if not self.marcados_aprovados:
            messagebox.showinfo(
                "Nenhum equipamento marcado",
                "Marque a caixinha (☐) na frente das linhas que deseja excluir.",
            )
            return

        if not confirmar_senha_admin(
            self, "Somente o administrador pode excluir equipamentos. Digite a senha de administrador:"
        ):
            return

        marcados = [e for e in self.equipamentos if e["id"] in self.marcados_aprovados]
        linhas = "\n".join(f"- OS. {e['os_numero']} ({e['cliente']})" for e in marcados[:10])
        if len(marcados) > 10:
            linhas += f"\n… e mais {len(marcados) - 10}"

        if messagebox.askyesno(
            "Excluir equipamentos marcados",
            f"Excluir {len(marcados)} equipamento(s)?\n\n{linhas}\n\nEsta ação não pode ser desfeita.",
        ):
            for e in marcados:
                excluir_equipamento(e["id"], self.usuario_atual["nome_usuario"])
                self.marcados_aprovados.discard(e["id"])
            self.atualizar_dados_aprovados()

    def _finalizar_marcados_aprovados(self):
        if not self.marcados_aprovados:
            messagebox.showinfo(
                "Nenhum equipamento marcado",
                "Marque a caixinha (☐) na frente das linhas que deseja finalizar.",
            )
            return

        marcados = [e for e in self.equipamentos if e["id"] in self.marcados_aprovados]
        linhas = "\n".join(f"- OS. {e['os_numero']} ({e['cliente']})" for e in marcados[:10])
        if len(marcados) > 10:
            linhas += f"\n… e mais {len(marcados) - 10}"

        if messagebox.askyesno(
            "Finalizar equipamentos marcados",
            f"Tem certeza que deseja finalizar {len(marcados)} equipamento(s)?\n\n{linhas}\n\n"
            "Eles saem desta lista e vão para \"Finalizados\" — dá pra trazer de volta por lá.",
        ):
            for e in marcados:
                finalizar_equipamento(e["id"], self.usuario_atual["nome_usuario"])
                self.marcados_aprovados.discard(e["id"])
            self.atualizar_dados_aprovados()

    # -- submenu "Equipamentos finalizados" ----------------------------------

    def _montar_pagina_aprovados_finalizados(self):
        pad = self.pagina_aprovados_finalizados

        topo = tk.Frame(pad, bg=COR["bg"])
        topo.pack(fill="x", padx=24, pady=(20, 6))
        esquerda = tk.Frame(topo, bg=COR["bg"])
        esquerda.pack(side="left")
        rotulo_titulo_versionado(esquerda, "Equipamentos Finalizados", COR["bg"]).pack(anchor="w")
        tk.Label(esquerda, text="Equipamentos finalizados — não aparecem mais na lista principal.",
                 font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_muted"]).pack(anchor="w")
        direita = tk.Frame(topo, bg=COR["bg"])
        direita.pack(side="right")
        self._botao_secundario(direita, "← Voltar", self._voltar_de_aprovados_finalizados).pack(
            side="left", padx=(0, 8)
        )
        self._botao_secundario(direita, "⟳  Atualizar", self.atualizar_dados_aprovados_finalizados).pack(
            side="left"
        )

        container = tk.Frame(pad, bg=COR["bg"])
        container.pack(fill="both", expand=True, padx=24, pady=(8, 8))

        colunas = ("marcar", "pedido_numero", "cliente", "os_numero", "numero_serie",
                   "aprovado_em", "liberacao_em", "liberacao_efetiva_em", "tecnico")
        titulos = {
            "marcar": "", "pedido_numero": "Pedido Nº", "cliente": "Cliente", "os_numero": "OS.",
            "numero_serie": "Série Orkan Nº", "aprovado_em": "Aprovado em", "liberacao_em": "Prazo liberação",
            "liberacao_efetiva_em": "Liberado em (efetivo)", "tecnico": "Técnico/Usuário",
        }
        larguras = {
            "marcar": 34, "pedido_numero": 100, "cliente": 140, "os_numero": 100, "numero_serie": 110,
            "aprovado_em": 100, "liberacao_em": 110, "liberacao_efetiva_em": 140, "tecnico": 130,
        }

        self.tree_aprovados_finalizados = ttk.Treeview(
            container, columns=colunas, show="headings", selectmode="browse"
        )
        for col in colunas:
            if col == "marcar":
                self.tree_aprovados_finalizados.heading(col, text="")
                self.tree_aprovados_finalizados.column(col, width=larguras[col], anchor="center", stretch=False)
                continue
            self.tree_aprovados_finalizados.heading(col, text=titulos[col])
            self.tree_aprovados_finalizados.column(col, width=larguras[col], anchor="w", stretch=False)

        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        scrollbar_v = BarraRolagemAuto(
            container, orient="vertical", command=self.tree_aprovados_finalizados.yview
        )
        scrollbar_h = BarraRolagemAuto(
            container, orient="horizontal", command=self.tree_aprovados_finalizados.xview
        )
        self.tree_aprovados_finalizados.configure(yscrollcommand=scrollbar_v.set, xscrollcommand=scrollbar_h.set)
        self.tree_aprovados_finalizados.grid(row=0, column=0, sticky="nsew")
        scrollbar_v.grid(row=0, column=1, sticky="ns")
        scrollbar_h.grid(row=1, column=0, sticky="ew")
        self.tree_aprovados_finalizados.bind("<Button-1>", self._ao_clicar_aprovados_finalizados, add="+")

        acoes = tk.Frame(pad, bg=COR["bg"])
        acoes.pack(fill="x", padx=24, pady=(0, 4))
        self._botao_secundario(acoes, "Reabrir marcados", self._reabrir_marcados_aprovados).pack(side="left")

        rodape = tk.Frame(pad, bg=COR["bg"])
        rodape.pack(fill="x", padx=24, pady=(4, 14))
        self.rodape_aprovados_finalizados_lbl = tk.Label(
            rodape, text="", font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_fraco"]
        )
        self.rodape_aprovados_finalizados_lbl.pack(anchor="w")

    def _ir_para_aprovados_finalizados(self):
        self.atualizar_dados_aprovados_finalizados()
        self.pagina_aprovados_finalizados.tkraise()

    def _voltar_de_aprovados_finalizados(self):
        self.pagina_aprovados.tkraise()

    def atualizar_dados_aprovados_finalizados(self):
        try:
            self.equipamentos_finalizados = carregar_equipamentos(apenas_finalizados=True)
        except ValidationError as exc:
            messagebox.showerror("Erro ao carregar dados", str(exc))
            self.equipamentos_finalizados = []
        self._renderizar_tabela_aprovados_finalizados()

    def _renderizar_tabela_aprovados_finalizados(self):
        self.tree_aprovados_finalizados.delete(*self.tree_aprovados_finalizados.get_children())
        for e in self.equipamentos_finalizados:
            valores = ["☑" if e["id"] in self.marcados_aprovados_finalizados else "☐"]
            valores.extend((
                e["pedido_numero"] or "—", e["cliente"], e["os_numero"], e["numero_serie"] or "—",
                formatar_data_br(e["aprovado_em"]), formatar_data_br(e["liberacao_em"]),
                formatar_data_br(e["liberacao_efetiva_em"]), _tecnico_exibido(e) or "—",
            ))
            self.tree_aprovados_finalizados.insert("", "end", iid=str(e["id"]), values=tuple(valores))

        rodape = f"{len(self.equipamentos_finalizados)} equipamento(s) finalizado(s)"
        if self.marcados_aprovados_finalizados:
            rodape += f"  •  {len(self.marcados_aprovados_finalizados)} marcado(s) para reabrir"
        self.rodape_aprovados_finalizados_lbl.configure(text=rodape)

    def _ao_clicar_aprovados_finalizados(self, evento):
        if self.tree_aprovados_finalizados.identify_region(evento.x, evento.y) != "cell":
            return
        if self.tree_aprovados_finalizados.identify_column(evento.x) != "#1":
            return
        item = self.tree_aprovados_finalizados.identify_row(evento.y)
        if not item:
            return
        equipamento_id = int(item)
        if equipamento_id in self.marcados_aprovados_finalizados:
            self.marcados_aprovados_finalizados.discard(equipamento_id)
        else:
            self.marcados_aprovados_finalizados.add(equipamento_id)
        self._renderizar_tabela_aprovados_finalizados()

    def _reabrir_marcados_aprovados(self):
        if not self.marcados_aprovados_finalizados:
            messagebox.showinfo(
                "Nenhum equipamento marcado",
                "Marque a caixinha (☐) na frente das linhas que deseja reabrir.",
            )
            return
        marcados = [e for e in self.equipamentos_finalizados if e["id"] in self.marcados_aprovados_finalizados]
        if messagebox.askyesno(
            "Reabrir equipamentos marcados",
            f"Trazer {len(marcados)} equipamento(s) de volta para a lista principal?",
        ):
            for e in marcados:
                reabrir_equipamento(e["id"], self.usuario_atual["nome_usuario"])
                self.marcados_aprovados_finalizados.discard(e["id"])
            self.atualizar_dados_aprovados_finalizados()

    # -- topo / toolbar / tabela (orcamentos) --------------------------------

    def _montar_topo_orcamentos(self):
        topo = tk.Frame(self.pagina_orcamentos, bg=COR["bg"])
        topo.pack(fill="x", padx=24, pady=(20, 6))

        esquerda = tk.Frame(topo, bg=COR["bg"])
        esquerda.pack(side="left")
        rotulo_titulo_versionado(esquerda, "Orçamentos Enviados", COR["bg"]).pack(anchor="w")
        tk.Label(esquerda, text="Propostas OKSST enviadas ao cliente, aguardando aprovação.",
                 font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_muted"]).pack(anchor="w")

        direita = tk.Frame(topo, bg=COR["bg"])
        direita.pack(side="right")
        self._botao_secundario(direita, "⟳  Atualizar", self.atualizar_dados_orcamentos).pack(
            side="left", padx=(0, 8)
        )
        self._botao_secundario(direita, "Finalizados", self._ir_para_orcamentos_finalizados).pack(side="left")

    def _montar_toolbar_orcamentos(self):
        barra = tk.Frame(self.pagina_orcamentos, bg=COR["bg"])
        barra.pack(fill="x", padx=24, pady=(6, 8))

        busca_frame = tk.Frame(barra, bg=COR["surface"], highlightbackground=COR["borda"],
                                highlightthickness=1)
        busca_frame.pack(side="left", ipady=4)
        tk.Label(busca_frame, text="🔍", bg=COR["surface"], fg=COR["ink_fraco"]).pack(side="left", padx=(8, 2))
        tk.Entry(busca_frame, textvariable=self.busca_orcamentos, font=FONTE_BASE, width=28, bd=0,
                 bg=COR["surface"]).pack(side="left", padx=(0, 8), pady=4)

        tk.Button(
            barra, text="+  Novo orçamento", command=self._abrir_novo_orcamento, bd=0, padx=16, pady=7,
            bg=COR["acento"], fg="white", font=FONTE_BASE_NEG, cursor="hand2",
            activebackground=COR["acento_escuro"],
        ).pack(side="right")
        self._botao_secundario(barra, "📄  Importar de PDF", self._abrir_importar_pdf_orcamento).pack(
            side="right", padx=(0, 8)
        )

    def _montar_tabela_orcamentos(self):
        container = tk.Frame(self.pagina_orcamentos, bg=COR["bg"])
        container.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        colunas = (
            "marcar", "os_numero", "numero_serie", "cliente", "destinatarios",
            "material_recebido", "prazo_dias_uteis", "data_orcamento",
        )
        titulos = {
            "marcar": "", "os_numero": "Proposta/OS", "numero_serie": "Série Orkan Nº", "cliente": "Cliente",
            "destinatarios": "Para (destinatários)", "material_recebido": "Material recebido",
            "prazo_dias_uteis": "Prazo", "data_orcamento": "Data do orçamento",
        }
        larguras = {
            "marcar": 34, "os_numero": 130, "numero_serie": 100, "cliente": 170, "destinatarios": 210,
            "material_recebido": 240, "prazo_dias_uteis": 100, "data_orcamento": 110,
        }
        # Colunas com texto que pode ser grande (destinatarios/material_recebido):
        # a celula mostra um resumo truncado e o texto completo aparece numa
        # dica ao passar o mouse (ver _mostrar_dica_orcamentos).
        self._colunas_com_dica_orcamentos = {"#5": "destinatarios", "#6": "material_recebido"}

        self.tree_orcamentos = ttk.Treeview(container, columns=colunas, show="headings", selectmode="browse")
        for col in colunas:
            if col == "marcar":
                self.tree_orcamentos.heading(col, text="")
                self.tree_orcamentos.column(col, width=larguras[col], anchor="center", stretch=False)
                continue
            self.tree_orcamentos.heading(
                col, text=titulos[col], anchor="center", command=lambda c=col: self._ordenar_por_orcamentos(c)
            )
            self.tree_orcamentos.column(col, width=larguras[col], anchor="center", stretch=False)

        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        scrollbar_v = BarraRolagemAuto(container, orient="vertical", command=self.tree_orcamentos.yview)
        scrollbar_h = BarraRolagemAuto(container, orient="horizontal", command=self.tree_orcamentos.xview)
        self.tree_orcamentos.configure(yscrollcommand=scrollbar_v.set, xscrollcommand=scrollbar_h.set)
        self.tree_orcamentos.grid(row=0, column=0, sticky="nsew")
        scrollbar_v.grid(row=0, column=1, sticky="ns")
        scrollbar_h.grid(row=1, column=0, sticky="ew")

        self._dica_orcamentos = None
        self.tree_orcamentos.bind("<Double-1>", lambda e: self._abrir_editar_orcamento())
        self.tree_orcamentos.bind("<Button-1>", self._ao_clicar_tabela_orcamentos, add="+")
        self.tree_orcamentos.bind("<Motion>", self._mostrar_dica_orcamentos)
        self.tree_orcamentos.bind("<Leave>", lambda e: self._esconder_dica_orcamentos())

        acoes = tk.Frame(self.pagina_orcamentos, bg=COR["bg"])
        acoes.pack(fill="x", padx=24, pady=(0, 4))
        self._botao_secundario(acoes, "Editar selecionado", self._abrir_editar_orcamento).pack(side="left")
        self._botao_secundario(acoes, "Excluir marcados", self._excluir_marcados_orcamentos).pack(
            side="left", padx=8
        )
        self._botao_secundario(acoes, "Finalizar marcados", self._finalizar_marcados_orcamentos).pack(side="left")

    def _montar_rodape_orcamentos(self):
        rodape = tk.Frame(self.pagina_orcamentos, bg=COR["bg"])
        rodape.pack(fill="x", padx=24, pady=(0, 14))
        self.rodape_orcamentos_lbl = tk.Label(rodape, text="", font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_fraco"])
        self.rodape_orcamentos_lbl.pack(anchor="w")
        self.status_backup_orcamentos_lbl = tk.Label(
            rodape, text="", font=("Segoe UI", 8), bg=COR["bg"], fg=COR["verde"]
        )
        self.status_backup_orcamentos_lbl.pack(anchor="w")

    # -- dados / renderizacao (orcamentos) -----------------------------------

    def atualizar_dados_orcamentos(self):
        try:
            self.orcamentos = carregar_orcamentos()
        except ValidationError as exc:
            messagebox.showerror("Erro ao carregar dados", str(exc))
            self.orcamentos = []
        self._renderizar_tabela_orcamentos()

    def _orcamentos_filtrados(self):
        termo = self.busca_orcamentos.get().strip().lower()
        if not termo:
            return list(self.orcamentos)
        resultado = []
        for o in self.orcamentos:
            alvo = (
                f"{o['os_numero'] or ''} {o['numero_serie'] or ''} {o['cliente'] or ''} "
                f"{o['destinatarios'] or ''} {o['material_recebido'] or ''}"
            ).lower()
            if termo in alvo:
                resultado.append(o)
        return resultado

    def _renderizar_tabela_orcamentos(self):
        self.tree_orcamentos.delete(*self.tree_orcamentos.get_children())
        orcamentos = self._orcamentos_filtrados()

        if self.ordenar_coluna_orcamentos_atual:
            orcamentos = self._ordenar_lista_orcamentos(
                orcamentos, self.ordenar_coluna_orcamentos_atual, self.ordenar_reverso_orcamentos
            )
        else:
            # Padrao: orcamento enviado mais recentemente primeiro. Sem data
            # ficam por ultimo.
            orcamentos.sort(key=lambda o: parse_date(o["data_orcamento"]) or date.min, reverse=True)

        for o in orcamentos:
            nomes, _ = separar_destinatarios(o["destinatarios"])
            valores = ["☑" if o["id"] in self.marcados_orcamentos else "☐"]
            valores.extend((
                o["os_numero"] or "—", o["numero_serie"] or "—", o["cliente"],
                resumo_material(nomes, 40), resumo_material(o["material_recebido"], 46),
                f"{o['prazo_dias_uteis']} dias úteis" if o["prazo_dias_uteis"] is not None else "—",
                formatar_data_br(o["data_orcamento"]),
            ))
            self.tree_orcamentos.insert("", "end", iid=str(o["id"]), values=tuple(valores))

        total = len(orcamentos)
        rodape = (
            f"{total} orçamento(s) exibido(s) de {len(self.orcamentos)} no total  •  "
            f"clique duas vezes numa linha para editar"
        )
        if self.marcados_orcamentos:
            rodape += f"  •  {len(self.marcados_orcamentos)} marcado(s) para excluir"
        self.rodape_orcamentos_lbl.configure(text=rodape)

    def _mostrar_dica_orcamentos(self, evento):
        coluna = self.tree_orcamentos.identify_column(evento.x)
        linha = self.tree_orcamentos.identify_row(evento.y)
        chave = self._colunas_com_dica_orcamentos.get(coluna)
        orcamento = next((o for o in self.orcamentos if str(o["id"]) == linha), None) if linha else None
        if chave == "destinatarios" and orcamento:
            texto, _ = separar_destinatarios(orcamento["destinatarios"])  # so nomes — e-mail fica so em "Editar"
        else:
            texto = (orcamento or {}).get(chave) if chave else None
        if not texto:
            self._esconder_dica_orcamentos()
            return

        if self._dica_orcamentos is None:
            self._dica_orcamentos = tk.Toplevel(self)
            self._dica_orcamentos.overrideredirect(True)
            self._dica_orcamentos.attributes("-topmost", True)
            self._dica_orcamentos_lbl = tk.Label(
                self._dica_orcamentos, font=FONTE_BASE, bg="#FFFFD9", fg=COR["ink"],
                justify="left", wraplength=360, padx=8, pady=4, relief="solid", bd=1,
            )
            self._dica_orcamentos_lbl.pack()

        self._dica_orcamentos_lbl.configure(text=texto)
        self._dica_orcamentos.geometry(f"+{evento.x_root + 16}+{evento.y_root + 12}")
        self._dica_orcamentos.deiconify()

    def _esconder_dica_orcamentos(self):
        if self._dica_orcamentos is not None:
            self._dica_orcamentos.withdraw()

    def _ao_clicar_tabela_orcamentos(self, evento):
        if self.tree_orcamentos.identify_region(evento.x, evento.y) != "cell":
            return
        if self.tree_orcamentos.identify_column(evento.x) != "#1":  # coluna "marcar"
            return
        item = self.tree_orcamentos.identify_row(evento.y)
        if not item:
            return
        orcamento_id = int(item)
        if orcamento_id in self.marcados_orcamentos:
            self.marcados_orcamentos.discard(orcamento_id)
        else:
            self.marcados_orcamentos.add(orcamento_id)
        self._renderizar_tabela_orcamentos()

    def _ordenar_por_orcamentos(self, coluna):
        if self.ordenar_coluna_orcamentos_atual == coluna:
            self.ordenar_reverso_orcamentos = not self.ordenar_reverso_orcamentos
        else:
            self.ordenar_coluna_orcamentos_atual = coluna
            self.ordenar_reverso_orcamentos = False
        self._renderizar_tabela_orcamentos()

    @staticmethod
    def _ordenar_lista_orcamentos(orcamentos, coluna, reverso):
        def chave(o):
            if coluna == "data_orcamento":
                return parse_date(o[coluna]) or date.min
            if coluna == "prazo_dias_uteis":
                return o[coluna] if o[coluna] is not None else -1
            return str(o.get(coluna) or "").lower()
        return sorted(orcamentos, key=chave, reverse=reverso)

    # -- selecao / crud (orcamentos) ------------------------------------------

    def _orcamento_selecionado(self):
        selecao = self.tree_orcamentos.selection()
        if not selecao:
            messagebox.showinfo("Selecione um orçamento", "Clique em uma linha da tabela primeiro.")
            return None
        orcamento_id = int(selecao[0])
        return next((o for o in self.orcamentos if o["id"] == orcamento_id), None)

    def _abrir_novo_orcamento(self):
        DialogoOrcamento(
            self, ao_salvar=self.atualizar_dados_orcamentos, usuario=self.usuario_atual["nome_usuario"]
        )

    def _abrir_editar_orcamento(self):
        orcamento = self._orcamento_selecionado()
        if orcamento:
            DialogoOrcamento(
                self, ao_salvar=self.atualizar_dados_orcamentos, usuario=self.usuario_atual["nome_usuario"],
                orcamento=orcamento,
            )

    def _abrir_importar_pdf_orcamento(self):
        if not PDF_DISPONIVEL:
            messagebox.showerror(
                "Recurso indisponível",
                "A biblioteca de leitura de PDF (pypdf) não está instalada nesta instalação do programa.",
            )
            return
        DialogoImportarPDFOrcamento(
            self, ao_salvar=self.atualizar_dados_orcamentos, usuario=self.usuario_atual["nome_usuario"]
        )

    def _excluir_marcados_orcamentos(self):
        if not self.marcados_orcamentos:
            messagebox.showinfo(
                "Nenhum orçamento marcado",
                "Marque a caixinha (☐) na frente das linhas que deseja excluir.",
            )
            return

        if not confirmar_senha_admin(
            self, "Somente o administrador pode excluir orçamentos. Digite a senha de administrador:"
        ):
            return

        marcados = [o for o in self.orcamentos if o["id"] in self.marcados_orcamentos]
        linhas = "\n".join(f"- {o['os_numero'] or '(sem OS)'} ({o['cliente']})" for o in marcados[:10])
        if len(marcados) > 10:
            linhas += f"\n… e mais {len(marcados) - 10}"

        if messagebox.askyesno(
            "Excluir orçamentos marcados",
            f"Excluir {len(marcados)} orçamento(s)?\n\n{linhas}\n\nEsta ação não pode ser desfeita.",
        ):
            for o in marcados:
                excluir_orcamento(o["id"], self.usuario_atual["nome_usuario"])
                self.marcados_orcamentos.discard(o["id"])
            self.atualizar_dados_orcamentos()

    def _finalizar_marcados_orcamentos(self):
        if not self.marcados_orcamentos:
            messagebox.showinfo(
                "Nenhum orçamento marcado",
                "Marque a caixinha (☐) na frente das linhas que deseja finalizar.",
            )
            return

        marcados = [o for o in self.orcamentos if o["id"] in self.marcados_orcamentos]
        linhas = "\n".join(f"- {o['os_numero'] or '(sem OS)'} ({o['cliente']})" for o in marcados[:10])
        if len(marcados) > 10:
            linhas += f"\n… e mais {len(marcados) - 10}"

        if messagebox.askyesno(
            "Finalizar orçamentos marcados",
            f"Tem certeza que deseja finalizar {len(marcados)} orçamento(s)?\n\n{linhas}\n\n"
            "Eles saem desta lista e vão para \"Finalizados\" — dá pra trazer de volta por lá.",
        ):
            for o in marcados:
                finalizar_orcamento(o["id"], self.usuario_atual["nome_usuario"])
                self.marcados_orcamentos.discard(o["id"])
            self.atualizar_dados_orcamentos()

    # -- submenu "Orcamentos finalizados" ------------------------------------

    def _montar_pagina_orcamentos_finalizados(self):
        pad = self.pagina_orcamentos_finalizados

        topo = tk.Frame(pad, bg=COR["bg"])
        topo.pack(fill="x", padx=24, pady=(20, 6))
        esquerda = tk.Frame(topo, bg=COR["bg"])
        esquerda.pack(side="left")
        rotulo_titulo_versionado(esquerda, "Orçamentos Finalizados", COR["bg"]).pack(anchor="w")
        tk.Label(esquerda, text="Orçamentos finalizados — não aparecem mais na lista principal.",
                 font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_muted"]).pack(anchor="w")
        direita = tk.Frame(topo, bg=COR["bg"])
        direita.pack(side="right")
        self._botao_secundario(direita, "← Voltar", self._voltar_de_orcamentos_finalizados).pack(
            side="left", padx=(0, 8)
        )
        self._botao_secundario(direita, "⟳  Atualizar", self.atualizar_dados_orcamentos_finalizados).pack(
            side="left"
        )

        container = tk.Frame(pad, bg=COR["bg"])
        container.pack(fill="both", expand=True, padx=24, pady=(8, 8))

        colunas = ("marcar", "os_numero", "numero_serie", "cliente", "destinatarios",
                   "material_recebido", "prazo_dias_uteis", "data_orcamento")
        titulos = {
            "marcar": "", "os_numero": "Proposta/OS", "numero_serie": "Série Orkan Nº", "cliente": "Cliente",
            "destinatarios": "Para (destinatários)", "material_recebido": "Material recebido",
            "prazo_dias_uteis": "Prazo", "data_orcamento": "Data do orçamento",
        }
        larguras = {
            "marcar": 34, "os_numero": 130, "numero_serie": 100, "cliente": 170, "destinatarios": 210,
            "material_recebido": 240, "prazo_dias_uteis": 100, "data_orcamento": 110,
        }

        self.tree_orcamentos_finalizados = ttk.Treeview(
            container, columns=colunas, show="headings", selectmode="browse"
        )
        for col in colunas:
            if col == "marcar":
                self.tree_orcamentos_finalizados.heading(col, text="")
                self.tree_orcamentos_finalizados.column(col, width=larguras[col], anchor="center", stretch=False)
                continue
            self.tree_orcamentos_finalizados.heading(col, text=titulos[col])
            self.tree_orcamentos_finalizados.column(col, width=larguras[col], anchor="w", stretch=False)

        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        scrollbar_v = BarraRolagemAuto(
            container, orient="vertical", command=self.tree_orcamentos_finalizados.yview
        )
        scrollbar_h = BarraRolagemAuto(
            container, orient="horizontal", command=self.tree_orcamentos_finalizados.xview
        )
        self.tree_orcamentos_finalizados.configure(yscrollcommand=scrollbar_v.set, xscrollcommand=scrollbar_h.set)
        self.tree_orcamentos_finalizados.grid(row=0, column=0, sticky="nsew")
        scrollbar_v.grid(row=0, column=1, sticky="ns")
        scrollbar_h.grid(row=1, column=0, sticky="ew")
        self.tree_orcamentos_finalizados.bind("<Button-1>", self._ao_clicar_orcamentos_finalizados, add="+")

        acoes = tk.Frame(pad, bg=COR["bg"])
        acoes.pack(fill="x", padx=24, pady=(0, 4))
        self._botao_secundario(acoes, "Reabrir marcados", self._reabrir_marcados_orcamentos).pack(side="left")

        rodape = tk.Frame(pad, bg=COR["bg"])
        rodape.pack(fill="x", padx=24, pady=(4, 14))
        self.rodape_orcamentos_finalizados_lbl = tk.Label(
            rodape, text="", font=FONTE_BASE, bg=COR["bg"], fg=COR["ink_fraco"]
        )
        self.rodape_orcamentos_finalizados_lbl.pack(anchor="w")

    def _ir_para_orcamentos_finalizados(self):
        self.atualizar_dados_orcamentos_finalizados()
        self.pagina_orcamentos_finalizados.tkraise()

    def _voltar_de_orcamentos_finalizados(self):
        self.pagina_orcamentos.tkraise()

    def atualizar_dados_orcamentos_finalizados(self):
        try:
            self.orcamentos_finalizados = carregar_orcamentos(apenas_finalizados=True)
        except ValidationError as exc:
            messagebox.showerror("Erro ao carregar dados", str(exc))
            self.orcamentos_finalizados = []
        self._renderizar_tabela_orcamentos_finalizados()

    def _renderizar_tabela_orcamentos_finalizados(self):
        self.tree_orcamentos_finalizados.delete(*self.tree_orcamentos_finalizados.get_children())
        for o in self.orcamentos_finalizados:
            nomes, _ = separar_destinatarios(o["destinatarios"])
            valores = ["☑" if o["id"] in self.marcados_orcamentos_finalizados else "☐"]
            valores.extend((
                o["os_numero"] or "—", o["numero_serie"] or "—", o["cliente"],
                resumo_material(nomes, 40), resumo_material(o["material_recebido"], 46),
                f"{o['prazo_dias_uteis']} dias úteis" if o["prazo_dias_uteis"] is not None else "—",
                formatar_data_br(o["data_orcamento"]),
            ))
            self.tree_orcamentos_finalizados.insert("", "end", iid=str(o["id"]), values=tuple(valores))

        rodape = f"{len(self.orcamentos_finalizados)} orçamento(s) finalizado(s)"
        if self.marcados_orcamentos_finalizados:
            rodape += f"  •  {len(self.marcados_orcamentos_finalizados)} marcado(s) para reabrir"
        self.rodape_orcamentos_finalizados_lbl.configure(text=rodape)

    def _ao_clicar_orcamentos_finalizados(self, evento):
        if self.tree_orcamentos_finalizados.identify_region(evento.x, evento.y) != "cell":
            return
        if self.tree_orcamentos_finalizados.identify_column(evento.x) != "#1":
            return
        item = self.tree_orcamentos_finalizados.identify_row(evento.y)
        if not item:
            return
        orcamento_id = int(item)
        if orcamento_id in self.marcados_orcamentos_finalizados:
            self.marcados_orcamentos_finalizados.discard(orcamento_id)
        else:
            self.marcados_orcamentos_finalizados.add(orcamento_id)
        self._renderizar_tabela_orcamentos_finalizados()

    def _reabrir_marcados_orcamentos(self):
        if not self.marcados_orcamentos_finalizados:
            messagebox.showinfo(
                "Nenhum orçamento marcado",
                "Marque a caixinha (☐) na frente das linhas que deseja reabrir.",
            )
            return
        marcados = [o for o in self.orcamentos_finalizados if o["id"] in self.marcados_orcamentos_finalizados]
        if messagebox.askyesno(
            "Reabrir orçamentos marcados",
            f"Trazer {len(marcados)} orçamento(s) de volta para a lista principal?",
        ):
            for o in marcados:
                reabrir_orcamento(o["id"], self.usuario_atual["nome_usuario"])
                self.marcados_orcamentos_finalizados.discard(o["id"])
            self.atualizar_dados_orcamentos_finalizados()


def main():
    try:
        init_db()
    except ValidationError as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Configuração necessária", str(exc))
        root.destroy()
        return

    login = TelaLogin()
    login.mainloop()
    usuario = login.usuario_autenticado
    if not usuario:
        return

    app = App(usuario_atual=usuario)
    app.mainloop()


if __name__ == "__main__":
    main()
