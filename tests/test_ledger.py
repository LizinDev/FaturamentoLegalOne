"""Checkpoint em SQLite: e ele que impede cadastrar duas vezes o mesmo processo."""
import csv
import sqlite3

import pytest

import ledger as ledger_mod
from conftest import situacao_de

FATURAMENTO = "FATURAMENTO FINAL"
DEFESA = "DEFESA FATURADA"
CNJ = "0000001-11.2025.8.05.0001"


def _ler_csv(caminho):
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f, delimiter=";"))


# --- gravacao ----------------------------------------------------------------

def test_registrar_e_consultar(registro):
    registro.registrar(CNJ, FATURAMENTO, ledger_mod.OK, "111", "cadastrada", "2026!L2")

    assert situacao_de(registro, CNJ, FATURAMENTO) == ledger_mod.OK
    assert situacao_de(registro, CNJ, DEFESA) is None


def test_mesmo_processo_recebe_as_duas_tarefas(registro):
    # A chave e (processo, tarefa): com chave so no processo, cadastrar
    # FATURAMENTO FINAL faria a rodada de DEFESA FATURADA pular este numero.
    registro.registrar(CNJ, FATURAMENTO, ledger_mod.OK)
    registro.registrar(CNJ, DEFESA, ledger_mod.OK)

    assert registro.todos(FATURAMENTO) == {CNJ}
    assert registro.todos(DEFESA) == {CNJ}
    assert dict(registro.resumo()) == {ledger_mod.OK: 2}


def test_ok_nao_e_rebaixado_para_ja_existia(registro):
    # Reprocessar um processo cadastrado por nos responde "ja tinha a tarefa" —
    # verdade daquela passada, mas sobrescrever apagaria o registro de que fomos
    # nos que cadastramos, e em que dia. A planilha diaria se apoia nisso.
    registro.registrar(CNJ, FATURAMENTO, ledger_mod.OK, "111", "cadastrada")
    quando = registro.con.execute(
        "SELECT quando FROM processos WHERE cnj = ?", (CNJ,)
    ).fetchone()[0]

    registro.registrar(CNJ, FATURAMENTO, ledger_mod.JA_EXISTIA, "111", "ja tinha")

    situacao, detalhe, agora = registro.con.execute(
        "SELECT situacao, detalhe, quando FROM processos WHERE cnj = ?", (CNJ,)
    ).fetchone()
    assert (situacao, detalhe, agora) == (ledger_mod.OK, "cadastrada", quando)


def test_valor_vazio_nao_apaga_valor_preenchido(registro):
    # Nem todo caminho tem todos os dados em maos; sem essa protecao a segunda
    # passada esvaziaria as colunas que a primeira tinha preenchido.
    registro.registrar(CNJ, FATURAMENTO, ledger_mod.OK, "111", "cadastrada",
                       "2026!L2", "ENCERRAMENTO", "Ativo", "0000001.11.2025.8.05.0001")

    registro.registrar(CNJ, FATURAMENTO, ledger_mod.ERRO, detalhe="timeout")

    id_lo, origem, tipo, status, original = registro.con.execute(
        "SELECT id_legalone, origem, tipo_cobranca, status_planilha, cnj_original "
        "FROM processos WHERE cnj = ?", (CNJ,)
    ).fetchone()
    assert (id_lo, origem, tipo, status) == ("111", "2026!L2", "ENCERRAMENTO", "Ativo")
    assert original == "0000001.11.2025.8.05.0001"


def test_erro_pode_virar_ok(registro):
    registro.registrar(CNJ, FATURAMENTO, ledger_mod.ERRO, detalhe="timeout")
    registro.registrar(CNJ, FATURAMENTO, ledger_mod.OK, "111", "cadastrada")

    assert situacao_de(registro, CNJ, FATURAMENTO) == ledger_mod.OK


def test_ja_existia_grava_normalmente_quando_nao_havia_ok(registro):
    registro.registrar(CNJ, FATURAMENTO, ledger_mod.JA_EXISTIA, "111", "ja tinha")

    assert situacao_de(registro, CNJ, FATURAMENTO) == ledger_mod.JA_EXISTIA


def test_recadastrada_tambem_nao_e_rebaixada_para_ja_existia(registro):
    # Recadastrar e cadastro nosso como qualquer outro: a data e o detalhe
    # precisam sobreviver a uma passada posterior que so olhou e viu que existe.
    registro.registrar(CNJ, FATURAMENTO, ledger_mod.RECADASTRADA, "111",
                       "cadastrada (ja tinha a tarefa)")

    registro.registrar(CNJ, FATURAMENTO, ledger_mod.JA_EXISTIA, "111", "ja tinha")

    situacao, detalhe = registro.con.execute(
        "SELECT situacao, detalhe FROM processos WHERE cnj = ?", (CNJ,)
    ).fetchone()
    assert (situacao, detalhe) == (ledger_mod.RECADASTRADA,
                                   "cadastrada (ja tinha a tarefa)")


# --- filas -------------------------------------------------------------------

def test_concluidos_e_todos_separam_o_que_se_retenta(registro):
    registro.registrar("A", FATURAMENTO, ledger_mod.OK)
    registro.registrar("B", FATURAMENTO, ledger_mod.JA_EXISTIA)
    registro.registrar("C", FATURAMENTO, ledger_mod.ERRO)
    registro.registrar("D", FATURAMENTO, ledger_mod.NAO_ENCONTRADO)
    registro.registrar("E", DEFESA, ledger_mod.OK)
    registro.registrar("F", FATURAMENTO, ledger_mod.RECADASTRADA)

    # 'ja_existia' NAO entra: a orientacao e cadastrar de novo onde a tarefa ja
    # existe, entao um 'ja_existia' de rodada antiga volta para a fila com
    # --retentar. So o que nos cadastramos e que fica de fora.
    assert registro.concluidos(FATURAMENTO) == {"A", "F"}
    assert registro.todos(FATURAMENTO) == {"A", "B", "C", "D", "F"}
    assert registro.concluidos(DEFESA) == {"E"}


def test_esquecer_apaga_so_a_tarefa_indicada(registro):
    registro.registrar(CNJ, FATURAMENTO, ledger_mod.NAO_ENCONTRADO)
    registro.registrar(CNJ, DEFESA, ledger_mod.NAO_ENCONTRADO)

    assert registro.esquecer([CNJ], FATURAMENTO) == 1

    assert situacao_de(registro, CNJ, FATURAMENTO) is None
    assert situacao_de(registro, CNJ, DEFESA) == ledger_mod.NAO_ENCONTRADO


def test_esquecer_nunca_apaga_trabalho_confirmado(registro):
    # 'ok', 'recadastrada' e 'ja_existia' sao tarefas que existem no Legal One:
    # apagar por engano faria a retomada cadastrar a mesma tarefa de novo.
    registro.registrar("A", FATURAMENTO, ledger_mod.OK)
    registro.registrar("B", FATURAMENTO, ledger_mod.JA_EXISTIA)
    registro.registrar("C", FATURAMENTO, ledger_mod.NAO_ENCONTRADO)
    registro.registrar("D", FATURAMENTO, ledger_mod.RECADASTRADA)

    assert registro.esquecer(["A", "B", "C", "D"], FATURAMENTO) == 1

    assert registro.todos(FATURAMENTO) == {"A", "B", "D"}


def test_esquecer_lista_vazia(registro):
    assert registro.esquecer([], FATURAMENTO) == 0


# --- consultas do relatorio --------------------------------------------------

def test_cadastrados_em_filtra_por_dia_e_situacao(registro):
    registro.con.executemany(
        "INSERT INTO processos (cnj, tarefa, situacao, quando) VALUES (?, ?, ?, ?)",
        [
            ("A", FATURAMENTO, ledger_mod.OK, "2026-07-30T10:00:00"),
            ("B", FATURAMENTO, ledger_mod.OK, "2026-07-31T10:00:00"),
            ("C", FATURAMENTO, ledger_mod.JA_EXISTIA, "2026-07-30T11:00:00"),
            # Recadastro tambem criou tarefa naquele dia: entra na planilha.
            ("D", FATURAMENTO, ledger_mod.RECADASTRADA, "2026-07-30T12:00:00"),
        ],
    )
    registro.con.commit()

    linhas = registro.cadastrados_em("2026-07-30")
    assert [linha[0] for linha in linhas] == ["A", "D"]
    # A situacao vai junto: e o que marca a coluna "JA TINHA A TAREFA".
    assert [linha[-1] for linha in linhas] == [ledger_mod.OK,
                                               ledger_mod.RECADASTRADA]
    assert registro.dias_com_cadastro() == ["2026-07-31", "2026-07-30"]


def test_resumo_por_tarefa(registro):
    registro.registrar("A", FATURAMENTO, ledger_mod.OK)
    registro.registrar("B", FATURAMENTO, ledger_mod.ERRO)
    registro.registrar("C", DEFESA, ledger_mod.OK)

    assert dict(registro.resumo(FATURAMENTO)) == {ledger_mod.OK: 1, ledger_mod.ERRO: 1}
    assert dict(registro.resumo()) == {ledger_mod.OK: 2, ledger_mod.ERRO: 1}


def test_exportar_csv(registro, tmp_path):
    registro.registrar(CNJ, FATURAMENTO, ledger_mod.OK, "111", "cadastrada", "2026!L2",
                       "ENCERRAMENTO", "Ativo")

    caminho = tmp_path / "relatorio.csv"
    assert registro.exportar_csv(caminho) == 1

    linhas = _ler_csv(caminho)
    assert linhas[0][:4] == ["PROCESSO", "TAREFA", "SITUACAO", "ID_LEGALONE"]
    assert linhas[1][:6] == [CNJ, FATURAMENTO, "ok", "111", "cadastrada",
                             "ENCERRAMENTO"]


# --- lista de conferencia manual ---------------------------------------------

def test_lista_de_conferencia_traz_so_o_que_precisa_de_gente(registro, tmp_path):
    registro.registrar("A", FATURAMENTO, ledger_mod.OK)
    registro.registrar("B", FATURAMENTO, ledger_mod.ERRO)
    registro.registrar("C", FATURAMENTO, ledger_mod.NAO_ENCONTRADO,
                       detalhe="nenhum resultado")
    registro.registrar("D", FATURAMENTO, ledger_mod.AMBIGUO, detalhe="ambiguo: ids")

    caminho = tmp_path / "nao_encontrados.csv"
    assert registro.exportar_nao_encontrados(caminho) == 2

    linhas = _ler_csv(caminho)
    assert [linha[0] for linha in linhas[1:]] == ["C", "D"]


def test_lista_de_conferencia_e_acumulada(registro, tmp_path):
    # Numa retomada os nao encontrados de ontem nem entram na fila: se a lista
    # saisse so da rodada, o levantamento anterior sumiria do arquivo.
    registro.registrar("ANTIGO", FATURAMENTO, ledger_mod.NAO_ENCONTRADO,
                       detalhe="nenhum resultado")
    registro.registrar("NOVO", FATURAMENTO, ledger_mod.NAO_ENCONTRADO,
                       detalhe="nenhum resultado")

    caminho = tmp_path / "nao_encontrados.csv"
    assert registro.exportar_nao_encontrados(caminho) == 2

    assert [linha[0] for linha in _ler_csv(caminho)[1:]] == ["ANTIGO", "NOVO"]


def test_lista_de_conferencia_mostra_o_numero_como_estava_na_planilha(
    registro, tmp_path
):
    # PESQUISADO_COMO so aparece quando o numero foi corrigido antes de buscar.
    registro.registrar(CNJ, FATURAMENTO, ledger_mod.NAO_ENCONTRADO,
                       detalhe="nenhum resultado",
                       cnj_original="0000001.11.2025.8.05.0001")
    registro.registrar("B", FATURAMENTO, ledger_mod.NAO_ENCONTRADO,
                       detalhe="nenhum resultado", cnj_original="B")

    caminho = tmp_path / "nao_encontrados.csv"
    registro.exportar_nao_encontrados(caminho)

    linhas = {linha[0]: linha for linha in _ler_csv(caminho)[1:]}
    assert linhas["0000001.11.2025.8.05.0001"][1] == CNJ
    assert linhas["B"][1] == ""


def test_ledger_antigo_sem_cnj_original_cai_no_numero_normalizado(registro, tmp_path):
    registro.con.execute(
        "INSERT INTO processos (cnj, tarefa, situacao, quando) VALUES (?, ?, ?, ?)",
        (CNJ, FATURAMENTO, ledger_mod.NAO_ENCONTRADO, "2026-07-30T10:00:00"),
    )
    registro.con.commit()

    caminho = tmp_path / "nao_encontrados.csv"
    registro.exportar_nao_encontrados(caminho)

    assert _ler_csv(caminho)[1][:2] == [CNJ, ""]


def test_escrever_nao_encontrados_aceita_itens_incompletos(tmp_path):
    caminho = tmp_path / "nao_encontrados.csv"

    assert ledger_mod.escrever_nao_encontrados(caminho, [{"cnj": "A"}]) == 1

    assert _ler_csv(caminho)[1] == ["A", "", "", "", "", "", "", ""]


# --- migracao ----------------------------------------------------------------

ESQUEMA_V0 = """
CREATE TABLE processos (
    cnj         TEXT PRIMARY KEY,
    situacao    TEXT NOT NULL,
    id_legalone TEXT,
    detalhe     TEXT,
    origem      TEXT,
    quando      TEXT NOT NULL
);
CREATE INDEX idx_situacao ON processos(situacao);
"""

ESQUEMA_V1 = """
CREATE TABLE processos (
    cnj             TEXT PRIMARY KEY,
    situacao        TEXT NOT NULL,
    id_legalone     TEXT,
    detalhe         TEXT,
    origem          TEXT,
    tipo_cobranca   TEXT,
    status_planilha TEXT,
    quando          TEXT NOT NULL
);
CREATE INDEX idx_situacao ON processos(situacao);
"""

# Ja com chave composta, mas antes de existir a coluna cnj_original.
ESQUEMA_V2 = """
CREATE TABLE processos (
    cnj             TEXT NOT NULL,
    tarefa          TEXT NOT NULL,
    situacao        TEXT NOT NULL,
    id_legalone     TEXT,
    detalhe         TEXT,
    origem          TEXT,
    tipo_cobranca   TEXT,
    status_planilha TEXT,
    quando          TEXT NOT NULL,
    PRIMARY KEY (cnj, tarefa)
);
"""


def _criar_ledger_antigo(caminho, esquema, valores):
    con = sqlite3.connect(caminho)
    con.executescript(esquema)
    marcas = ",".join("?" * len(valores))
    con.execute(f"INSERT INTO processos VALUES ({marcas})", valores)
    con.commit()
    con.close()


@pytest.mark.parametrize("esquema, valores", [
    (ESQUEMA_V0, (CNJ, "ok", "111", "cadastrada", "2026!L2", "2026-07-30T10:00:00")),
    (ESQUEMA_V1, (CNJ, "ok", "111", "cadastrada", "2026!L2", "ENCERRAMENTO",
                  "Ativo", "2026-07-30T10:00:00")),
])
def test_ledger_antigo_e_migrado_com_os_registros_preservados(
    tmp_path, esquema, valores
):
    caminho = tmp_path / "ledger.sqlite3"
    _criar_ledger_antigo(caminho, esquema, valores)

    with ledger_mod.Ledger(caminho) as led:
        # Os registros existentes viram FATURAMENTO FINAL, a unica tarefa que
        # existia antes de o ledger ter chave composta.
        assert situacao_de(led, CNJ, ledger_mod.TAREFA_HISTORICA) == ledger_mod.OK
        assert led.todos(ledger_mod.TAREFA_HISTORICA) == {CNJ}

        tabelas = {r[0] for r in led.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "processos_antigo" not in tabelas

        # O indice tem que acabar apontando para a tabela nova: o nome dele
        # sobrevive ao RENAME e um IF NOT EXISTS cedo demais viraria no-op.
        indices = dict(led.con.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='index'"
        ))
        assert indices.get("idx_situacao") == "processos"
        assert indices.get("idx_tarefa") == "processos"

        # E o ledger migrado continua utilizavel.
        led.registrar(CNJ, DEFESA, ledger_mod.OK)
        assert dict(led.resumo()) == {ledger_mod.OK: 2}


def test_coluna_nova_entra_sem_recriar_a_tabela(tmp_path):
    caminho = tmp_path / "ledger.sqlite3"
    _criar_ledger_antigo(
        caminho, ESQUEMA_V2,
        (CNJ, FATURAMENTO, "ok", "111", "cadastrada", "2026!L2", "ENCERRAMENTO",
         "Ativo", "2026-07-30T10:00:00"),
    )

    with ledger_mod.Ledger(caminho) as led:
        colunas = [c[1] for c in led.con.execute("PRAGMA table_info(processos)")]
        assert "cnj_original" in colunas
        assert situacao_de(led, CNJ, FATURAMENTO) == ledger_mod.OK
        led.registrar(CNJ, FATURAMENTO, ledger_mod.OK, cnj_original="X")


def test_migracao_e_idempotente(tmp_path):
    caminho = tmp_path / "ledger.sqlite3"
    _criar_ledger_antigo(
        caminho, ESQUEMA_V0,
        (CNJ, "ok", "111", "cadastrada", "2026!L2", "2026-07-30T10:00:00"),
    )

    for _ in range(3):
        with ledger_mod.Ledger(caminho) as led:
            assert led.todos(ledger_mod.TAREFA_HISTORICA) == {CNJ}


def test_ledger_novo_nao_dispara_migracao(tmp_path, caplog):
    caminho = tmp_path / "ledger.sqlite3"
    with ledger_mod.Ledger(caminho):
        pass
    with ledger_mod.Ledger(caminho):
        pass

    assert "Migrando ledger" not in caplog.text
