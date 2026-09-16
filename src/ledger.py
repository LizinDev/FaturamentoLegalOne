"""Registro persistente do que ja foi cadastrado.

Numa rodada de milhares de processos a execucao vai ser interrompida — queda de
rede, sessao expirada, reboot. O ledger e o que permite retomar sem cadastrar
nada duas vezes: cada processo e gravado e commitado assim que termina.

A chave e (processo, tarefa), nao so o processo: o mesmo numero pode estar nas
duas planilhas e precisar das duas tarefas. Com chave so no processo, cadastrar
FATURAMENTO FINAL faria a rodada de DEFESA FATURADA pular aquele processo em
silencio.
"""
import csv
import logging
import sqlite3
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

OK = "ok"
NAO_ENCONTRADO = "nao_encontrado"
AMBIGUO = "ambiguo"
ERRO = "erro"
JA_EXISTIA = "ja_existia"
# A tarefa ja estava la e foi cadastrada de novo: a orientacao de operacao para
# esse caso e "pode agendar novamente, vamos pecar pelo excesso". Fica numa
# situacao propria, e nao junto com 'ok', para o relatorio distinguir o excesso.
RECADASTRADA = "recadastrada"

# Tarefas que este programa criou no Legal One. Uma retomada com --retentar pula
# so estas: um 'ja_existia' de rodada antiga e justamente um caso que a
# orientacao atual manda cadastrar, entao ele volta para a fila.
NOSSOS_CADASTROS = {OK, RECADASTRADA}

# Trabalho confirmado no Legal One, que o disjuntor nunca pode apagar.
CONCLUIDAS = {OK, RECADASTRADA, JA_EXISTIA}

# Situacoes que pedem conferencia manual na planilha.
PENDENTES_ATENCAO = (NAO_ENCONTRADO, AMBIGUO)

# As mesmas situacoes escritas para dentro do SQL de registrar(), onde nao da
# para usar parametro: elas aparecem num CASE, e nao numa comparacao de valor.
_SQL_NOSSOS_CADASTROS = ", ".join(f"'{s}'" for s in sorted(NOSSOS_CADASTROS))

# A trava de rebaixamento de registrar(): um cadastro nosso ja confirmado no
# Legal One so pode ser reescrito por outro cadastro nosso. Qualquer outra
# observacao posterior — 'ja_existia', mas tambem 'nao_encontrado', 'ambiguo' e
# 'erro' — nao desfaz a tarefa que ja foi criada, entao nao pode apagar o
# registro dela. Rebaixado, o par sairia de NOSSOS_CADASTROS e de CONCLUIDAS:
# --retentar o cadastraria de novo e esquecer() poderia apaga-lo do ledger.
_SQL_NAO_REBAIXAR = (
    f"processos.situacao IN ({_SQL_NOSSOS_CADASTROS}) "
    f"AND excluded.situacao NOT IN ({_SQL_NOSSOS_CADASTROS})"
)

ESQUEMA_TABELA = """
CREATE TABLE IF NOT EXISTS processos (
    cnj             TEXT NOT NULL,
    tarefa          TEXT NOT NULL,
    situacao        TEXT NOT NULL,
    id_legalone     TEXT,
    detalhe         TEXT,
    origem          TEXT,
    tipo_cobranca   TEXT,
    status_planilha TEXT,
    cnj_original    TEXT,
    quando          TEXT NOT NULL,
    PRIMARY KEY (cnj, tarefa)
)
"""

ESQUEMA_INDICES = (
    "CREATE INDEX IF NOT EXISTS idx_situacao ON processos(situacao)",
    "CREATE INDEX IF NOT EXISTS idx_tarefa ON processos(tarefa)",
)

# Colunas acrescentadas depois que ja havia ledger em producao. Sao opcionais,
# entao entram com ALTER TABLE em vez de recriar a tabela.
COLUNAS_NOVAS = [("cnj_original", "TEXT")]

# Antes de existirem duas tarefas, o ledger so guardava FATURAMENTO FINAL.
TAREFA_HISTORICA = "FATURAMENTO FINAL"


class Ledger:
    """Estado de cada par (processo, tarefa), em SQLite."""

    def __init__(self, caminho: str | Path):
        self.caminho = str(caminho)
        self.con = sqlite3.connect(self.caminho)
        # WAL aguenta melhor uma interrupcao brusca no meio da rodada.
        self.con.execute("PRAGMA journal_mode=WAL")
        self._migrar()
        # Os indices vem depois da migracao de proposito: enquanto a tabela
        # antiga existe, os indices dela ocupam esses mesmos nomes e o
        # IF NOT EXISTS viraria um no-op silencioso.
        self.con.execute(ESQUEMA_TABELA)
        for indice in ESQUEMA_INDICES:
            self.con.execute(indice)
        self.con.commit()

    def _migrar(self) -> None:
        """Leva um ledger de versao antiga para o esquema com chave composta."""
        tabelas = {
            r[0] for r in self.con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "processos" not in tabelas:
            return

        colunas = [c[1] for c in self.con.execute("PRAGMA table_info(processos)")]

        if "tarefa" in colunas:
            self._acrescentar_colunas(colunas)
            return

        logger.info("Migrando ledger para o esquema com chave (processo, tarefa)")
        # Colunas que talvez nao existam nas versoes mais antigas.
        tipo = "tipo_cobranca" if "tipo_cobranca" in colunas else "''"
        status = "status_planilha" if "status_planilha" in colunas else "''"

        # Tudo numa transacao so: uma queda no meio da copia deixaria o
        # historico de milhares de cadastros pela metade. O SQLite versiona
        # tambem o DDL, entao o ALTER/CREATE/DROP entram junto.
        self.con.execute("BEGIN IMMEDIATE")
        try:
            self.con.execute("ALTER TABLE processos RENAME TO processos_antigo")
            self.con.execute(ESQUEMA_TABELA)
            self.con.execute(
                f"INSERT INTO processos "
                f"  (cnj, tarefa, situacao, id_legalone, detalhe, origem, "
                f"   tipo_cobranca, status_planilha, quando) "
                f"SELECT cnj, ?, situacao, id_legalone, detalhe, origem, "
                f"       {tipo}, {status}, quando FROM processos_antigo",
                (TAREFA_HISTORICA,),
            )
            movidos = self.con.execute("SELECT COUNT(*) FROM processos").fetchone()[0]
            self.con.execute("DROP TABLE processos_antigo")
        except Exception:
            self.con.rollback()
            raise
        self.con.commit()
        logger.info("Ledger migrado: %d registro(s) atribuidos a %r",
                    movidos, TAREFA_HISTORICA)

    def _acrescentar_colunas(self, colunas: list[str]) -> None:
        """Poe no lugar colunas opcionais que o ledger ainda nao tenha."""
        for nome, tipo in COLUNAS_NOVAS:
            if nome not in colunas:
                self.con.execute(
                    f"ALTER TABLE processos ADD COLUMN {nome} {tipo}"
                )
                logger.info("Ledger: coluna %r acrescentada", nome)
        self.con.commit()

    def registrar(
        self,
        cnj: str,
        tarefa: str,
        situacao: str,
        id_legalone: str = "",
        detalhe: str = "",
        origem: str = "",
        tipo_cobranca: str = "",
        status_planilha: str = "",
        cnj_original: str = "",
    ) -> None:
        # Duas protecoes na reescrita de um registro que ja existe:
        #
        # 1. Reprocessar um processo ja cadastrado devolve "ja_existia" — que e
        #    verdade daquela passada, mas apagaria o registro de que fomos nos
        #    que cadastramos, e em que dia. Como o relatorio diario se apoia
        #    nisso, um cadastro nosso ('ok' ou 'recadastrada') nunca e
        #    rebaixado: mantem situacao, data e detalhe. Vale para qualquer
        #    situacao que nao seja outro cadastro nosso — uma busca que falha
        #    depois nao desfaz a tarefa criada. Ver _SQL_NAO_REBAIXAR.
        # 2. Nem todo caminho tem todos os dados em maos (um erro no meio do
        #    cadastro nao sabe o tipo de cobranca, por exemplo). Valor vazio
        #    nunca sobrescreve valor preenchido, senao a segunda passada
        #    esvaziaria as colunas que a primeira tinha preenchido.
        self.con.execute(
            "INSERT INTO processos "
            "  (cnj, tarefa, situacao, id_legalone, detalhe, origem, "
            "   tipo_cobranca, status_planilha, cnj_original, quando) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(cnj, tarefa) DO UPDATE SET "
            f"  situacao = CASE WHEN {_SQL_NAO_REBAIXAR} "
            "                  THEN processos.situacao ELSE excluded.situacao END, "
            f"  quando   = CASE WHEN {_SQL_NAO_REBAIXAR} "
            "                  THEN processos.quando ELSE excluded.quando END, "
            f"  detalhe  = CASE WHEN {_SQL_NAO_REBAIXAR} "
            "                  THEN processos.detalhe ELSE excluded.detalhe END, "
            "  id_legalone     = COALESCE(NULLIF(excluded.id_legalone, ''), "
            "                             processos.id_legalone), "
            "  origem          = COALESCE(NULLIF(excluded.origem, ''), "
            "                             processos.origem), "
            "  tipo_cobranca   = COALESCE(NULLIF(excluded.tipo_cobranca, ''), "
            "                             processos.tipo_cobranca), "
            "  status_planilha = COALESCE(NULLIF(excluded.status_planilha, ''), "
            "                             processos.status_planilha), "
            "  cnj_original    = COALESCE(NULLIF(excluded.cnj_original, ''), "
            "                             processos.cnj_original)",
            (cnj, tarefa, situacao, id_legalone, detalhe, origem, tipo_cobranca,
             status_planilha, cnj_original,
             datetime.now().isoformat(timespec="seconds")),
        )
        self.con.commit()

    def detalhe(self, cnj: str, tarefa: str) -> str:
        """Detalhe gravado para o par, ou "" se ele nunca passou pela rodada.

        A rodada le antes de reescrever: e ali que um Salvar incerto deixou
        quantas tarefas havia antes do clique.
        """
        linha = self.con.execute(
            "SELECT detalhe FROM processos WHERE cnj = ? AND tarefa = ?",
            (cnj, tarefa),
        ).fetchone()
        return (linha[0] or "") if linha else ""

    def concluidos(self, tarefa: str) -> set[str]:
        """O que --retentar pula: as tarefas que nos cadastramos."""
        marcas = ",".join("?" * len(NOSSOS_CADASTROS))
        return {
            r[0] for r in self.con.execute(
                f"SELECT cnj FROM processos "
                f"WHERE tarefa = ? AND situacao IN ({marcas})",
                (tarefa, *NOSSOS_CADASTROS),
            )
        }

    def todos(self, tarefa: str) -> set[str]:
        return {
            r[0] for r in self.con.execute(
                "SELECT cnj FROM processos WHERE tarefa = ?", (tarefa,)
            )
        }

    def esquecer(self, cnjs: Iterable[str], tarefa: str) -> int:
        """Apaga registros para que o processo seja tentado de novo do zero.

        Usado quando o disjuntor dispara: os ultimos "nao encontrado" antes de
        uma queda de sessao sao falsos, e deixa-los gravados faria a retomada
        pular justamente os processos que nunca foram avaliados de verdade.

        So apaga o que ainda esta pendente. Uma situacao de CONCLUIDAS e trabalho
        confirmado no Legal One: apagar por engano faria a retomada cadastrar a
        mesma tarefa de novo. Devolve quantos registros sairam de fato.
        """
        cnjs = list(cnjs)
        if not cnjs:
            return 0
        marcas = ",".join("?" * len(CONCLUIDAS))
        cursor = self.con.executemany(
            f"DELETE FROM processos "
            f"WHERE cnj = ? AND tarefa = ? AND situacao NOT IN ({marcas})",
            [(c, tarefa, *CONCLUIDAS) for c in cnjs],
        )
        apagados = cursor.rowcount
        self.con.commit()
        return apagados

    def cadastrados_em(self, dia: str) -> list[tuple]:
        """Cadastros feitos por nos num dia (dia no formato AAAA-MM-DD).

        Inclui os recadastros: eles tambem criaram tarefa naquele dia, e a
        planilha do supervisor precisa mostra-los — marcados como tal.
        """
        marcas = ",".join("?" * len(NOSSOS_CADASTROS))
        return self.con.execute(
            f"SELECT cnj, tarefa, id_legalone, tipo_cobranca, status_planilha, "
            f"       origem, quando, situacao "
            f"FROM processos WHERE situacao IN ({marcas}) AND quando LIKE ? "
            f"ORDER BY tarefa, quando",
            (*NOSSOS_CADASTROS, f"{dia}%"),
        ).fetchall()

    def dias_com_cadastro(self) -> list[str]:
        """Dias com pelo menos um cadastro, do mais recente para o mais antigo."""
        marcas = ",".join("?" * len(NOSSOS_CADASTROS))
        return [
            r[0] for r in self.con.execute(
                f"SELECT DISTINCT substr(quando, 1, 10) FROM processos "
                f"WHERE situacao IN ({marcas}) ORDER BY 1 DESC",
                (*NOSSOS_CADASTROS,)
            )
        ]

    def resumo(self, tarefa: str | None = None) -> Counter:
        if tarefa:
            return Counter(dict(self.con.execute(
                "SELECT situacao, COUNT(*) FROM processos "
                "WHERE tarefa = ? GROUP BY situacao", (tarefa,)
            )))
        return Counter(dict(self.con.execute(
            "SELECT situacao, COUNT(*) FROM processos GROUP BY situacao"
        )))

    def exportar_csv(self, caminho: str | Path) -> int:
        linhas = self.con.execute(
            "SELECT cnj, tarefa, situacao, id_legalone, detalhe, tipo_cobranca, "
            "       status_planilha, origem, quando "
            "FROM processos ORDER BY tarefa, situacao, cnj"
        ).fetchall()
        with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
            escritor = csv.writer(f, delimiter=";")
            escritor.writerow(
                ["PROCESSO", "TAREFA", "SITUACAO", "ID_LEGALONE", "DETALHE",
                 "TIPO_COBRANCA", "STATUS_PLANILHA", "ORIGEM", "QUANDO"]
            )
            escritor.writerows(linhas)
        logger.info("Relatorio salvo: %s (%d linha[s])", caminho, len(linhas))
        return len(linhas)

    def exportar_nao_encontrados(self, caminho: str | Path) -> int:
        """CSV so com o que precisa de conferencia manual na planilha."""
        marcas = ",".join("?" * len(PENDENTES_ATENCAO))
        # O numero como estava na planilha e o que serve para procurar a linha
        # de origem; ledger antigo nao guardava, e ai cai no normalizado.
        linhas = self.con.execute(
            f"SELECT COALESCE(NULLIF(cnj_original, ''), cnj), cnj, tarefa, "
            f"       situacao, detalhe, tipo_cobranca, status_planilha, origem "
            f"FROM processos WHERE situacao IN ({marcas}) "
            f"ORDER BY tarefa, origem, cnj",
            PENDENTES_ATENCAO,
        ).fetchall()
        escrever_nao_encontrados(caminho, [
            {"cnj": orig, "cnj_busca": c, "tarefa": tf, "situacao": s,
             "motivo": d, "tipo_cobranca": t, "status_planilha": st, "origem": o}
            for orig, c, tf, s, d, t, st, o in linhas
        ])
        return len(linhas)

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def escrever_nao_encontrados(caminho: str | Path, itens: Iterable[dict]) -> int:
    """Grava a lista de conferencia manual.

    Fica fora da classe porque uma simulacao nao toca no ledger e mesmo assim
    precisa produzir esta lista — e justamente com --so-buscar que se levanta
    quais processos nao existem no Legal One.
    """
    itens = list(itens)
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        escritor = csv.writer(f, delimiter=";")
        escritor.writerow(
            ["PROCESSO", "PESQUISADO_COMO", "TAREFA", "SITUACAO", "MOTIVO",
             "TIPO_COBRANCA", "STATUS_PLANILHA", "ORIGEM"]
        )
        for it in itens:
            original = it.get("cnj", "")
            busca = it.get("cnj_busca", "") or original
            escritor.writerow([
                original,
                busca if busca != original else "",
                it.get("tarefa", ""), it.get("situacao", ""), it.get("motivo", ""),
                it.get("tipo_cobranca", ""), it.get("status_planilha", ""),
                it.get("origem", ""),
            ])
    logger.info("Lista de conferencia salva: %s (%d processo[s])", caminho, len(itens))
    return len(itens)
