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
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

OK = "ok"
NAO_ENCONTRADO = "nao_encontrado"
AMBIGUO = "ambiguo"
ERRO = "erro"
JA_EXISTIA = "ja_existia"

# Situacoes que nao devem ser refeitas numa retomada normal.
CONCLUIDAS = {OK, JA_EXISTIA}

# Situacoes que pedem conferencia manual na planilha.
PENDENTES_ATENCAO = (NAO_ENCONTRADO, AMBIGUO)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS processos (
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
CREATE INDEX IF NOT EXISTS idx_situacao ON processos(situacao);
CREATE INDEX IF NOT EXISTS idx_tarefa ON processos(tarefa);
"""

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
        self.con.executescript(ESQUEMA)
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
            return

        logger.info("Migrando ledger para o esquema com chave (processo, tarefa)")
        # Colunas que talvez nao existam nas versoes mais antigas.
        tipo = "tipo_cobranca" if "tipo_cobranca" in colunas else "''"
        status = "status_planilha" if "status_planilha" in colunas else "''"

        self.con.execute("ALTER TABLE processos RENAME TO processos_antigo")
        self.con.executescript(ESQUEMA)
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
        self.con.commit()
        logger.info("Ledger migrado: %d registro(s) atribuidos a %r",
                    movidos, TAREFA_HISTORICA)

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
    ) -> None:
        # Reprocessar um processo ja cadastrado devolve "ja_existia" — que e
        # verdade daquela passada, mas apagaria o registro de que fomos nos que
        # cadastramos, e em que dia. Como o relatorio diario se apoia nisso, um
        # 'ok' nunca e rebaixado: mantem situacao, data e detalhe originais.
        self.con.execute(
            "INSERT INTO processos "
            "  (cnj, tarefa, situacao, id_legalone, detalhe, origem, "
            "   tipo_cobranca, status_planilha, quando) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(cnj, tarefa) DO UPDATE SET "
            "  situacao = CASE WHEN processos.situacao = 'ok' "
            "                   AND excluded.situacao = 'ja_existia' "
            "                  THEN processos.situacao ELSE excluded.situacao END, "
            "  quando   = CASE WHEN processos.situacao = 'ok' "
            "                   AND excluded.situacao = 'ja_existia' "
            "                  THEN processos.quando ELSE excluded.quando END, "
            "  detalhe  = CASE WHEN processos.situacao = 'ok' "
            "                   AND excluded.situacao = 'ja_existia' "
            "                  THEN processos.detalhe ELSE excluded.detalhe END, "
            "  id_legalone=excluded.id_legalone, origem=excluded.origem, "
            "  tipo_cobranca=excluded.tipo_cobranca, "
            "  status_planilha=excluded.status_planilha",
            (cnj, tarefa, situacao, id_legalone, detalhe, origem, tipo_cobranca,
             status_planilha, datetime.now().isoformat(timespec="seconds")),
        )
        self.con.commit()

    def situacao_de(self, cnj: str, tarefa: str) -> str | None:
        linha = self.con.execute(
            "SELECT situacao FROM processos WHERE cnj = ? AND tarefa = ?",
            (cnj, tarefa),
        ).fetchone()
        return linha[0] if linha else None

    def concluidos(self, tarefa: str) -> set[str]:
        marcas = ",".join("?" * len(CONCLUIDAS))
        return {
            r[0] for r in self.con.execute(
                f"SELECT cnj FROM processos "
                f"WHERE tarefa = ? AND situacao IN ({marcas})",
                (tarefa, *CONCLUIDAS),
            )
        }

    def todos(self, tarefa: str) -> set[str]:
        return {
            r[0] for r in self.con.execute(
                "SELECT cnj FROM processos WHERE tarefa = ?", (tarefa,)
            )
        }

    def esquecer(self, cnjs, tarefa: str) -> int:
        """Apaga registros para que o processo seja tentado de novo do zero.

        Usado quando o disjuntor dispara: os ultimos "nao encontrado" antes de
        uma queda de sessao sao falsos, e deixa-los gravados faria a retomada
        pular justamente os processos que nunca foram avaliados de verdade.
        """
        cnjs = list(cnjs)
        if not cnjs:
            return 0
        self.con.executemany(
            "DELETE FROM processos WHERE cnj = ? AND tarefa = ?",
            [(c, tarefa) for c in cnjs],
        )
        self.con.commit()
        return len(cnjs)

    def cadastrados_em(self, dia: str) -> list[tuple]:
        """Cadastros feitos por nos num dia (dia no formato AAAA-MM-DD)."""
        return self.con.execute(
            "SELECT cnj, tarefa, id_legalone, tipo_cobranca, status_planilha, "
            "       origem, quando "
            "FROM processos WHERE situacao = ? AND quando LIKE ? "
            "ORDER BY tarefa, quando",
            (OK, f"{dia}%"),
        ).fetchall()

    def dias_com_cadastro(self) -> list[str]:
        """Dias com pelo menos um cadastro, do mais recente para o mais antigo."""
        return [
            r[0] for r in self.con.execute(
                "SELECT DISTINCT substr(quando, 1, 10) FROM processos "
                "WHERE situacao = ? ORDER BY 1 DESC", (OK,)
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
        linhas = self.con.execute(
            f"SELECT cnj, tarefa, situacao, detalhe, tipo_cobranca, "
            f"       status_planilha, origem "
            f"FROM processos WHERE situacao IN ({marcas}) "
            f"ORDER BY tarefa, origem, cnj",
            PENDENTES_ATENCAO,
        ).fetchall()
        escrever_nao_encontrados(caminho, [
            {"cnj": c, "tarefa": tf, "situacao": s, "motivo": d,
             "tipo_cobranca": t, "status_planilha": st, "origem": o}
            for c, tf, s, d, t, st, o in linhas
        ])
        return len(linhas)

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def escrever_nao_encontrados(caminho: str | Path, itens: list[dict]) -> int:
    """Grava a lista de conferencia manual.

    Fica fora da classe porque uma simulacao nao toca no ledger e mesmo assim
    precisa produzir esta lista — e justamente com --so-buscar que se levanta
    quais processos nao existem no Legal One.
    """
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
