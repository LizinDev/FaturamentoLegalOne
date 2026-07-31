"""Interpretacao da grade de resultados do Legal One.

E aqui que se decide em qual pasta a tarefa vai ser cadastrada — errar isso
significa tarefa duplicada ou tarefa no processo errado.
"""
import config
import legalone

CNJ = "0003850-54.2026.8.16.0188"


def _linha(id_legalone, numero, tipo="Processo", status="Ativo"):
    """Monta uma linha da grade com as colunas nas posicoes reais."""
    cels = [""] * 5
    cels[config.COL_STATUS] = status
    cels[config.COL_TIPO] = tipo
    cels[config.COL_PROCESSO] = numero
    return {"cels": cels, "href": f"/processos/processos/details/{id_legalone}"}


def test_sem_resultado():
    r = legalone.interpretar_busca([], CNJ)

    assert r.encontrado is False
    assert r.detalhe == "nenhum resultado"
    assert r.ambiguo is False


def test_resultado_que_nao_e_o_numero_exato_nao_conta():
    # A busca do Legal One tambem casa pasta, envolvido e numeros parciais.
    r = legalone.interpretar_busca([_linha("1", "0000001-11.2025.8.05.0001")], CNJ)

    assert r.encontrado is False
    assert r.detalhe == "1 resultado(s), nenhum com o numero exato"


def test_escolhe_a_pasta_do_tipo_processo():
    # Verificado em producao: este CNJ tem 3 pastas (1 Processo + 2 Recursos) e
    # a tarefa vai so para a principal.
    r = legalone.interpretar_busca([
        _linha("100", CNJ, tipo="Recurso"),
        _linha("101", CNJ, tipo="Processo", status="Arquivado"),
        _linha("102", CNJ, tipo="Recurso"),
    ], CNJ)

    assert r.encontrado is True
    assert r.id_legalone == "101"
    assert r.status == "Arquivado"


def test_so_recurso_ou_incidente_nao_e_chute():
    r = legalone.interpretar_busca([
        _linha("100", CNJ, tipo="Recurso"),
        _linha("102", CNJ, tipo="Incidente"),
    ], CNJ)

    assert r.encontrado is False
    assert r.detalhe == "nenhuma pasta do tipo Processo (achei: Incidente, Recurso)"


def test_duas_pastas_do_tipo_processo_e_ambiguo():
    r = legalone.interpretar_busca([
        _linha("100", CNJ),
        _linha("101", CNJ),
    ], CNJ)

    assert r.encontrado is False
    assert r.ambiguo is True
    assert r.detalhe == "ambiguo: 2 pastas do tipo Processo, ids ['100', '101']"


def test_a_mesma_pasta_repetida_na_grade_nao_e_ambigua():
    r = legalone.interpretar_busca([_linha("100", CNJ), _linha("100", CNJ)], CNJ)

    assert r.encontrado is True
    assert r.id_legalone == "100"


def test_celula_do_processo_traz_a_pasta_na_segunda_linha():
    linha = _linha("100", f"{CNJ}\nPasta do cliente X")

    r = legalone.interpretar_busca([linha], CNJ)

    assert r.encontrado is True


def test_link_sem_id_numerico_e_ignorado():
    linha = _linha("100", CNJ)
    linha["href"] = "/processos/processos/details/novo"

    assert legalone.interpretar_busca([linha], CNJ).encontrado is False


def test_linha_com_menos_celulas_nao_quebra():
    linha = {"cels": ["so uma"], "href": "/processos/processos/details/100"}

    r = legalone.interpretar_busca([linha], CNJ)

    assert r.encontrado is False
    assert r.detalhe == "1 resultado(s), nenhum com o numero exato"


def test_href_absoluto_com_querystring():
    linha = _linha("100", CNJ)
    linha["href"] = f"{config.BASE_URL}/processos/processos/details/100?aba=dados"

    assert legalone.interpretar_busca([linha], CNJ).id_legalone == "100"
