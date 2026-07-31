"""O laco da rodada: o que para o lote, o que so vira 'erro' e segue.

Um automador falso no lugar do Selenium â€” nenhum teste aqui abre o Chrome.
"""
import datetime
import logging

import pytest

import config
import ledger as ledger_mod
import legalone
import main
import planilha
from conftest import situacao_de

CNJ = "0000001-11.2025.8.05.0001"


def processo(cnj=CNJ, formato_ok=True, origem="2026!L2", cnj_original=None):
    return planilha.Processo(
        cnj=cnj, cnj_original=cnj_original or cnj, formato_ok=formato_ok,
        tipos_cobranca=["ENCERRAMENTO"], status_planilha=["Ativo"],
        linhas=[origem],
    )


def achou(id_legalone="111", status="Ativo"):
    return legalone.ResultadoBusca(True, id_legalone=id_legalone, status=status)


class AutomadorFalso:
    """Responde o que o teste mandar, e anota o que foi pedido a ele."""

    def __init__(self, buscas=None, ja_existentes=(), ao_cadastrar=None):
        self.buscas = buscas or {}
        self.ja_existentes = set(ja_existentes)
        self.ao_cadastrar = ao_cadastrar
        self.cadastrados: list[tuple[str, bool]] = []
        self.buscados: list[str] = []
        self.checagens_de_duplicata = 0
        self.abas_criadas = 0
        self.aba_sumiu = False

    def aba_viva(self):
        return not self.aba_sumiu

    def usar_aba_propria(self):
        self.abas_criadas += 1
        self.aba_sumiu = False

    def buscar_processo(self, cnj):
        self.buscados.append(cnj)
        resposta = self.buscas.get(cnj, legalone.ResultadoBusca(
            False, detalhe="nenhum resultado"))
        # BaseException, e nao Exception: KeyboardInterrupt e um dos casos
        # que os testes precisam simular.
        if isinstance(resposta, BaseException):
            raise resposta
        return resposta

    def tarefa_ja_existe(self, id_legalone, descricao):
        self.checagens_de_duplicata += 1
        return id_legalone in self.ja_existentes

    def cadastrar_tarefa(self, id_legalone, executar):
        if self.ao_cadastrar is not None:
            raise self.ao_cadastrar
        self.cadastrados.append((id_legalone, executar))
        return "cadastrada" if executar else "simulado (formulario preenchido, nao salvo)"


def rodada(automador, registro, perfil, **kw):
    return main.Rodada(automador, registro, perfil, **kw)


# --- caminho feliz -----------------------------------------------------------

def test_cadastro_grava_no_ledger_com_os_dados_da_planilha(registro, perfil):
    automador = AutomadorFalso({CNJ: achou()})
    r = rodada(automador, registro, perfil, executar=True)

    r.executar_fila([processo()])

    assert automador.cadastrados == [("111", True)]
    assert r.contagem == {"ok": 1, "erro": 0, "nao_encontrado": 0, "ja_existia": 0}
    linha = registro.con.execute(
        "SELECT situacao, id_legalone, detalhe, origem, tipo_cobranca, "
        "status_planilha FROM processos WHERE cnj = ?", (CNJ,)
    ).fetchone()
    assert linha == (ledger_mod.OK, "111", "cadastrada", "2026!L2",
                     "ENCERRAMENTO", "Ativo")
    assert r.codigo_saida() == main.SAIDA_OK


def test_simulacao_preenche_mas_nao_deixa_rastro(registro, perfil):
    automador = AutomadorFalso({CNJ: achou()})
    r = rodada(automador, registro, perfil, executar=False)

    r.executar_fila([processo()])

    # O formulario e preenchido (e o valor da simulacao), mas nada e gravado â€”
    # senao a rodada real seguinte pularia um processo que nunca foi cadastrado.
    assert automador.cadastrados == [("111", False)]
    assert situacao_de(registro, CNJ, perfil.descricao) is None
    assert r.dias_de_planilha() == []


def test_tarefa_que_ja_existia_nao_e_cadastrada_de_novo(registro, perfil):
    # Parte dos processos ja tem a tarefa, feita a mao antes de o programa rodar.
    automador = AutomadorFalso({CNJ: achou()}, ja_existentes=["111"])
    r = rodada(automador, registro, perfil, executar=True)

    r.executar_fila([processo()])

    assert automador.cadastrados == []
    assert r.contagem["ja_existia"] == 1
    assert r.cadastradas == 0  # nao consome cota
    assert situacao_de(registro, CNJ, perfil.descricao) == ledger_mod.JA_EXISTIA


def test_rapido_pula_a_checagem_de_duplicata(registro, perfil):
    automador = AutomadorFalso({CNJ: achou()}, ja_existentes=["111"])
    r = rodada(automador, registro, perfil, executar=True, rapido=True)

    r.executar_fila([processo()])

    assert automador.checagens_de_duplicata == 0
    assert automador.cadastrados == [("111", True)]


def test_so_buscar_nao_abre_formulario(registro, perfil):
    automador = AutomadorFalso({CNJ: achou()})
    r = rodada(automador, registro, perfil, so_buscar=True)

    r.executar_fila([processo()])

    assert automador.cadastrados == []
    assert automador.checagens_de_duplicata == 0
    assert r.contagem["ok"] == 1


def test_aba_fechada_e_recriada_sem_perder_o_processo(registro, perfil):
    automador = AutomadorFalso({CNJ: achou()})
    automador.aba_sumiu = True
    r = rodada(automador, registro, perfil, executar=True)

    r.executar_fila([processo()])

    assert automador.abas_criadas == 1
    assert automador.cadastrados == [("111", True)]


# --- nao encontrados ---------------------------------------------------------

def test_nao_encontrado_vai_para_a_lista_de_conferencia(registro, perfil):
    r = rodada(AutomadorFalso(), registro, perfil, executar=True)

    r.executar_fila([processo()])

    assert r.contagem["nao_encontrado"] == 1
    assert situacao_de(registro, CNJ, perfil.descricao) == ledger_mod.NAO_ENCONTRADO
    assert r.nao_encontrados[0]["motivo"] == "nenhum resultado"
    assert r.nao_encontrados[0]["origem"] == "2026!L2"


def test_numero_fora_do_padrao_e_dito_no_motivo(registro, perfil):
    r = rodada(AutomadorFalso(), registro, perfil, executar=True)

    r.executar_fila([processo(cnj="processo em papel", formato_ok=False)])

    assert r.nao_encontrados[0]["motivo"].endswith("(numero fora do padrao CNJ)")


def test_ambiguo_tem_situacao_propria(registro, perfil):
    ambiguo = legalone.ResultadoBusca(False, detalhe="ambiguo: ids ['1', '2']",
                                      ambiguo=True)
    r = rodada(AutomadorFalso({CNJ: ambiguo}), registro, perfil, executar=True)

    r.executar_fila([processo()])

    assert situacao_de(registro, CNJ, perfil.descricao) == ledger_mod.AMBIGUO


# --- o que para a fila, e o que nao para -------------------------------------

def test_um_processo_com_erro_nao_derruba_a_fila(registro, perfil):
    fila = [processo("A"), processo("B"), processo("C")]
    automador = AutomadorFalso({
        "A": achou("1"),
        "B": RuntimeError("timeout no formulario"),
        "C": achou("3"),
    })
    r = rodada(automador, registro, perfil, executar=True)

    r.executar_fila(fila)

    assert automador.buscados == ["A", "B", "C"]
    assert r.contagem == {"ok": 2, "erro": 1, "nao_encontrado": 0, "ja_existia": 0}
    assert situacao_de(registro, "B", perfil.descricao) == ledger_mod.ERRO
    assert r.codigo_saida() == main.SAIDA_OK


def test_erro_guarda_motivo_origem_e_o_id_ja_encontrado(registro, perfil):
    # Se a busca chegou a achar o processo, o id fica gravado: e por ele que se
    # abre o caso a mao depois.
    automador = AutomadorFalso({CNJ: achou("111")},
                               ao_cadastrar=RuntimeError("Tipo padrao mudou"))
    r = rodada(automador, registro, perfil, executar=True)

    r.executar_fila([processo()])

    detalhe, origem, tipo, id_lo = registro.con.execute(
        "SELECT detalhe, origem, tipo_cobranca, id_legalone "
        "FROM processos WHERE cnj = ?", (CNJ,)
    ).fetchone()
    assert detalhe == "RuntimeError: Tipo padrao mudou"
    assert (origem, tipo, id_lo) == ("2026!L2", "ENCERRAMENTO", "111")


def test_erro_antes_de_achar_o_processo_nao_inventa_id(registro, perfil):
    automador = AutomadorFalso({CNJ: RuntimeError("timeout na busca")})
    r = rodada(automador, registro, perfil, executar=True)

    r.executar_fila([processo()])

    id_lo = registro.con.execute(
        "SELECT id_legalone FROM processos WHERE cnj = ?", (CNJ,)
    ).fetchone()[0]
    assert id_lo == ""


def test_registro_guarda_o_numero_como_estava_na_planilha(registro, perfil):
    # E por ele que se acha a linha de origem na conferencia manual.
    proc = processo(cnj=CNJ, cnj_original="0000001.11.2025.8.05.0001")
    r = rodada(AutomadorFalso(), registro, perfil, executar=True)

    r.executar_fila([proc])

    original = registro.con.execute(
        "SELECT cnj_original FROM processos WHERE cnj = ?", (CNJ,)
    ).fetchone()[0]
    assert original == "0000001.11.2025.8.05.0001"


def test_sessao_expirada_aborta_a_rodada_inteira(registro, perfil):
    # Sem isso, um logout no meio do lote transformaria todos os processos
    # restantes em 'erro' e queimaria a fila em silencio.
    fila = [processo("A"), processo("B"), processo("C")]
    automador = AutomadorFalso({
        "A": achou("1"),
        "B": legalone.SessaoExpirada("tela de login"),
        "C": achou("3"),
    })
    r = rodada(automador, registro, perfil, executar=True)

    r.executar_fila(fila)

    assert automador.buscados == ["A", "B"]
    assert r.sessao_expirada is not None
    assert situacao_de(registro, "B", perfil.descricao) is None  # nao vira 'erro'
    assert r.codigo_saida() == main.SAIDA_ABORTADA


def test_ctrl_c_encerra_limpo(registro, perfil):
    automador = AutomadorFalso({"A": achou("1"), "B": KeyboardInterrupt()})
    r = rodada(automador, registro, perfil, executar=True)

    r.executar_fila([processo("A"), processo("B")])

    assert r.interrompida is True
    assert situacao_de(registro, "A", perfil.descricao) == ledger_mod.OK
    assert r.codigo_saida() == main.SAIDA_INTERROMPIDA


def test_cota_do_dia_para_a_rodada(registro, perfil):
    fila = [processo(c) for c in "ABCD"]
    automador = AutomadorFalso({c: achou(c) for c in "ABCD"})
    r = rodada(automador, registro, perfil, executar=True, max_cadastros=2)

    r.executar_fila(fila)

    assert r.cadastradas == 2
    assert r.cota_atingida is True
    assert automador.buscados == ["A", "B"]
    assert r.codigo_saida() == main.SAIDA_OK


def test_nao_encontrado_e_ja_existente_nao_consomem_cota(registro, perfil):
    fila = [processo(c) for c in "ABCD"]
    automador = AutomadorFalso(
        {"A": achou("1"), "B": achou("2"), "D": achou("4")},  # C nao existe
        ja_existentes=["2"],
    )
    r = rodada(automador, registro, perfil, executar=True, max_cadastros=2)

    r.executar_fila(fila)

    assert automador.buscados == ["A", "B", "C", "D"]
    assert r.cadastradas == 2


# --- disjuntor ---------------------------------------------------------------

def test_disjuntor_para_a_rodada_e_devolve_os_suspeitos_para_a_fila(
    registro, perfil, monkeypatch
):
    # Uma sessao pode morrer sem redirecionar para o login: a busca passa a nao
    # achar nada e a rodada marcaria milhares de processos como inexistentes.
    monkeypatch.setattr(config, "MAX_NAO_ENCONTRADOS_SEGUIDOS", 3)
    fila = [processo(c) for c in "ABCDE"]
    r = rodada(AutomadorFalso(), registro, perfil, executar=True)

    r.executar_fila(fila)

    assert r.disjuntor is True
    assert r.codigo_saida() == main.SAIDA_ABORTADA
    # Os registros suspeitos saem do ledger: na retomada eles voltam para a
    # fila em vez de ficarem marcados como resolvidos.
    assert registro.todos(perfil.descricao) == set()
    assert r.nao_encontrados == []
    assert r.contagem["nao_encontrado"] == 0


def test_disjuntor_conta_seguidos_e_nao_o_total(registro, perfil, monkeypatch):
    monkeypatch.setattr(config, "MAX_NAO_ENCONTRADOS_SEGUIDOS", 3)
    fila = [processo(c) for c in "ABCDE"]
    # Um achado no meio zera a contagem.
    automador = AutomadorFalso({"C": achou("3")})
    r = rodada(automador, registro, perfil, executar=True)

    r.executar_fila(fila)

    assert r.disjuntor is False
    assert automador.buscados == ["A", "B", "C", "D", "E"]
    assert r.contagem["nao_encontrado"] == 4


def test_disjuntor_em_simulacao_nao_mexe_no_ledger(registro, perfil, monkeypatch):
    monkeypatch.setattr(config, "MAX_NAO_ENCONTRADOS_SEGUIDOS", 2)
    registro.registrar("A", perfil.descricao, ledger_mod.OK, "9", "cadastrada")
    r = rodada(AutomadorFalso(), registro, perfil, executar=False)

    r.executar_fila([processo("A"), processo("B")])

    assert r.disjuntor is True
    assert situacao_de(registro, "A", perfil.descricao) == ledger_mod.OK


# --- pos-rodada --------------------------------------------------------------

def test_dias_de_planilha_cobre_a_rodada_que_atravessa_a_meia_noite(registro, perfil):
    r = rodada(AutomadorFalso({CNJ: achou()}), registro, perfil, executar=True)
    r.executar_fila([processo()])
    hoje = datetime.date.today().isoformat()
    r.dias_cadastrados.add("2026-07-30")

    dias = r.dias_de_planilha()

    assert set(dias) == {"2026-07-30", hoje}
    assert dias == sorted(dias)


def test_rodada_sem_cadastro_nao_gera_planilha_do_dia(registro, perfil):
    # Todos ja tinham a tarefa: nao ha planilha do dia para refazer.
    automador = AutomadorFalso({CNJ: achou()}, ja_existentes=["111"])
    r = rodada(automador, registro, perfil, executar=True)

    r.executar_fila([processo()])

    assert r.dias_de_planilha() == []


def test_resumir_nao_explode_com_a_rodada_vazia(registro, perfil, caplog):
    caplog.set_level(logging.INFO)

    rodada(AutomadorFalso(), registro, perfil).resumir("nao_encontrados.csv", 0)

    assert "Resumo desta rodada" in caplog.text


@pytest.mark.parametrize("kw, esperado", [
    ({}, main.SAIDA_OK),
    ({"disjuntor": True}, main.SAIDA_ABORTADA),
    ({"interrompida": True}, main.SAIDA_INTERROMPIDA),
])
def test_codigo_de_saida(registro, perfil, kw, esperado):
    r = rodada(AutomadorFalso(), registro, perfil)
    for atributo, valor in kw.items():
        setattr(r, atributo, valor)

    assert r.codigo_saida() == esperado
