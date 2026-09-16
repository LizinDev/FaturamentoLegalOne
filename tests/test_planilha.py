"""Leitura da planilha de cobrancas."""
import pytest
from openpyxl import Workbook

import config
import planilha

# --- normalizacao ------------------------------------------------------------

@pytest.mark.parametrize("bruto, esperado", [
    ("0000632-63.2025.8.05.0154", "0000632-63.2025.8.05.0154"),
    ("  0000632-63.2025.8.05.0154  ", "0000632-63.2025.8.05.0154"),
    ("0000632-63.2025.8.05.0154\n", "0000632-63.2025.8.05.0154"),
])
def test_numero_no_padrao_passa_intacto(bruto, esperado):
    assert planilha.normalizar(bruto) == (esperado, True)


def test_ponto_no_lugar_do_primeiro_hifen_e_corrigido():
    # Erro de digitacao comum na planilha, recuperavel sem ambiguidade.
    assert planilha.normalizar("0064904.50.2019.8.05.0001") == (
        "0064904-50.2019.8.05.0001", True
    )


@pytest.mark.parametrize("bruto", ["", "sem numero", "123", "0064904-50.2019.8.05"])
def test_numero_quebrado_segue_marcado_como_fora_do_padrao(bruto):
    # Nao pode sumir em silencio: vai para a busca assim mesmo e sai no
    # relatorio marcado, para conferencia manual.
    numero, ok = planilha.normalizar(bruto)
    assert ok is False
    assert numero == bruto.strip()


# --- leitura -----------------------------------------------------------------

def _planilha(tmp_path, abas: dict, nome="cobrancas.xlsx"):
    """Cria um .xlsx: {aba: [linha, ...]} — a 1a linha e o cabecalho."""
    wb = Workbook()
    wb.remove(wb.active)
    for aba, linhas in abas.items():
        ws = wb.create_sheet(aba)
        for linha in linhas:
            ws.append(list(linha))
    caminho = tmp_path / nome
    wb.save(caminho)
    return caminho


CABECALHO = ("PROCESSO", "TIPO DE COBRANÇA", "STATUS LEGAL ONE")


def test_le_processos_na_ordem_de_aparicao(tmp_path):
    arq = _planilha(tmp_path, {"2026": [
        CABECALHO,
        ("0000001-11.2025.8.05.0001", "ENCERRAMENTO FINAL", "Ativo"),
        ("0000002-22.2025.8.05.0001", "ACORDO", "Arquivado"),
    ]})

    processos = planilha.ler(arq)

    assert [p.cnj for p in processos] == [
        "0000001-11.2025.8.05.0001", "0000002-22.2025.8.05.0001"
    ]
    assert processos[0].tipos_cobranca == ["ENCERRAMENTO FINAL"]
    assert processos[0].status_planilha == ["Ativo"]
    assert processos[0].origem == "2026!L2"


def test_numero_repetido_entre_abas_vira_um_processo_so(tmp_path):
    # A tarefa e cadastrada uma vez; a origem agregada vai para o relatorio.
    arq = _planilha(tmp_path, {
        "2025": [CABECALHO, ("0000001-11.2025.8.05.0001", "ACORDO", "Ativo")],
        "2026": [CABECALHO, ("0000001-11.2025.8.05.0001", "ENCERRAMENTO", "Baixado")],
    })

    processos = planilha.ler(arq)

    assert len(processos) == 1
    unico = processos[0]
    assert unico.origem == "2025!L2; 2026!L2"
    assert unico.tipos_cobranca == ["ACORDO", "ENCERRAMENTO"]
    assert unico.status_planilha == ["Ativo", "Baixado"]


def test_aba_sem_coluna_processo_e_ignorada(tmp_path):
    arq = _planilha(tmp_path, {
        "resumo": [("TOTAL", "VALOR"), (10, 20)],
        "2026": [CABECALHO, ("0000001-11.2025.8.05.0001", "ACORDO", "Ativo")],
    })

    assert [p.cnj for p in planilha.ler(arq)] == ["0000001-11.2025.8.05.0001"]


def test_linha_sem_numero_e_pulada(tmp_path):
    arq = _planilha(tmp_path, {"2026": [
        CABECALHO,
        (None, "ACORDO", "Ativo"),
        ("", "ACORDO", "Ativo"),
        ("0000001-11.2025.8.05.0001", "ACORDO", "Ativo"),
    ]})

    assert len(planilha.ler(arq)) == 1


def test_cabecalho_e_reconhecido_sem_ligar_para_caixa_e_espacos(tmp_path):
    arq = _planilha(tmp_path, {"2026": [
        ("  processo  ", "Tipo de Cobrança"),
        ("0000001-11.2025.8.05.0001", "ACORDO"),
    ]})

    processos = planilha.ler(arq)

    assert len(processos) == 1
    assert processos[0].tipos_cobranca == ["ACORDO"]


def test_filtro_por_tipo_de_cobranca(tmp_path):
    arq = _planilha(tmp_path, {"2026": [
        CABECALHO,
        ("0000001-11.2025.8.05.0001", "ENCERRAMENTO FINAL", "Ativo"),
        ("0000002-22.2025.8.05.0001", "ACORDO", "Ativo"),
    ]})

    processos = planilha.ler(arq, tipo_contem="encerramento")

    assert [p.cnj for p in processos] == ["0000001-11.2025.8.05.0001"]


def test_filtro_por_status_e_exato(tmp_path):
    arq = _planilha(tmp_path, {"2026": [
        CABECALHO,
        ("0000001-11.2025.8.05.0001", "ACORDO", "Ativo"),
        ("0000002-22.2025.8.05.0001", "ACORDO", "Inativo"),
    ]})

    processos = planilha.ler(arq, status_planilha="Ativo")

    assert [p.cnj for p in processos] == ["0000001-11.2025.8.05.0001"]


def test_abas_selecionadas(tmp_path):
    arq = _planilha(tmp_path, {
        "2025": [CABECALHO, ("0000001-11.2025.8.05.0001", "ACORDO", "Ativo")],
        "2026": [CABECALHO, ("0000002-22.2025.8.05.0001", "ACORDO", "Ativo")],
    })

    processos = planilha.ler(arq, abas=["2026"])

    assert [p.cnj for p in processos] == ["0000002-22.2025.8.05.0001"]


def test_aba_inexistente_avisa_quais_existem(tmp_path):
    arq = _planilha(tmp_path, {"2026": [CABECALHO]})

    with pytest.raises(ValueError, match="2027"):
        planilha.ler(arq, abas=["2027"])


def test_planilha_inexistente(tmp_path):
    with pytest.raises(FileNotFoundError):
        planilha.ler(tmp_path / "nao_existe.xlsx")


def test_numero_fora_do_padrao_e_lido_e_marcado(tmp_path):
    arq = _planilha(tmp_path, {"2026": [
        CABECALHO,
        ("processo em papel", "ACORDO", "Ativo"),
    ]})

    processos = planilha.ler(arq)

    assert len(processos) == 1
    assert processos[0].formato_ok is False
    assert processos[0].cnj_original == "processo em papel"


# --- tarefa vinda da propria linha -------------------------------------------

CNJ_A = "0000001-11.2025.8.05.0001"
CNJ_B = "0000002-22.2025.8.05.0001"


def test_tarefa_sai_da_coluna_tipo_de_cobranca(tmp_path):
    arq = _planilha(tmp_path, {"2026": [
        CABECALHO,
        (CNJ_A, "FATURAMENTO FINAL", "Ativo"),
        (CNJ_B, "DEFESA FATURADA", "Ativo"),
    ]})

    processos = planilha.ler(arq, tarefa_da_linha=config.tarefa_do_tipo)

    assert [(p.cnj, p.tarefa) for p in processos] == [
        (CNJ_A, "FATURAMENTO FINAL"), (CNJ_B, "DEFESA FATURADA"),
    ]


def test_mesmo_numero_com_as_duas_cobrancas_vira_dois_processos(tmp_path):
    # Sao etapas diferentes do mesmo caso: a defesa foi faturada e depois veio o
    # faturamento final. Deduplicar so pelo numero perderia uma das tarefas.
    arq = _planilha(tmp_path, {"2019-2020-2021": [
        CABECALHO,
        (CNJ_A, "DEFESA FATURADA", "Ativo"),
        (CNJ_A, "FATURAMENTO FINAL", "Ativo"),
    ]})

    processos = planilha.ler(arq, tarefa_da_linha=config.tarefa_do_tipo)

    assert [p.tarefa for p in processos] == ["DEFESA FATURADA", "FATURAMENTO FINAL"]
    assert {p.cnj for p in processos} == {CNJ_A}


def test_mesmo_numero_com_a_mesma_cobranca_continua_sendo_um_so(tmp_path):
    arq = _planilha(tmp_path, {"2019-2020-2021": [
        CABECALHO,
        (CNJ_A, "DEFESA FATURADA", "Ativo"),
        (CNJ_A, "DEFESA FATURADA", "Ativo"),
    ]})

    processos = planilha.ler(arq, tarefa_da_linha=config.tarefa_do_tipo)

    assert len(processos) == 1
    assert processos[0].origem == "2019-2020-2021!L2; 2019-2020-2021!L3"


def test_cobranca_sem_tarefa_correspondente_e_pulada(tmp_path):
    # A coluna e texto livre: as abas novas trazem dezenas de variantes que nao
    # sao tarefa nenhuma. Chutar uma delas cadastraria a tarefa errada.
    arq = _planilha(tmp_path, {"2023": [
        CABECALHO,
        (CNJ_A, "CONTESTAÇÃO BOTICÁRIO", "Ativo"),
        (CNJ_B, "DEFESA FATURADA", "Ativo"),
    ]})

    processos = planilha.ler(arq, tarefa_da_linha=config.tarefa_do_tipo)

    assert [p.cnj for p in processos] == [CNJ_B]


@pytest.mark.parametrize("escrito", ["Defesa Faturada", "  defesa   faturada  "])
def test_cobranca_casa_sem_ligar_para_caixa_e_espacos(tmp_path, escrito):
    arq = _planilha(tmp_path, {"2026": [CABECALHO, (CNJ_A, escrito, "Ativo")]})

    processos = planilha.ler(arq, tarefa_da_linha=config.tarefa_do_tipo)

    assert [p.tarefa for p in processos] == ["DEFESA FATURADA"]


def test_coluna_tarefa_vale_como_tipo_de_cobranca(tmp_path):
    # A planilha de 2022 chama a coluna de "TAREFA". Sem o alias a aba inteira
    # e lida como se a coluna estivesse vazia e todas as linhas sao puladas.
    arq = _planilha(tmp_path, {"2022": [
        ("PROCESSO", "STATUS ESPAIDER", "STATUS E-LAW", "TAREFA"),
        (CNJ_A, None, None, "DEFESA FATURADA"),
        (CNJ_B, None, None, "FATURAMENTO FINAL"),
    ]})

    processos = planilha.ler(arq, tarefa_da_linha=config.tarefa_do_tipo)

    assert [(p.cnj, p.tarefa) for p in processos] == [
        (CNJ_A, "DEFESA FATURADA"), (CNJ_B, "FATURAMENTO FINAL"),
    ]


def test_coluna_tarefa_para_lancar_vale_como_tipo_de_cobranca(tmp_path):
    # O nome que a planilha de 2026 usa.
    arq = _planilha(tmp_path, {"2026": [
        ("PROCESSO", "TAREFA PARA LANÇAR"),
        (CNJ_A, "DEFESA FATURADA"),
        (CNJ_B, "FATURAMENTO FINAL"),
    ]})

    processos = planilha.ler(arq, tarefa_da_linha=config.tarefa_do_tipo)

    assert [(p.cnj, p.tarefa) for p in processos] == [
        (CNJ_A, "DEFESA FATURADA"), (CNJ_B, "FATURAMENTO FINAL"),
    ]


def test_tipo_de_cobranca_ganha_de_tarefa_quando_as_duas_existem(tmp_path):
    # Nome canonico primeiro: a coluna velha manda onde ela estiver preenchida.
    arq = _planilha(tmp_path, {"2026": [
        ("PROCESSO", "TIPO DE COBRANÇA", "TAREFA"),
        (CNJ_A, "DEFESA FATURADA", "FATURAMENTO FINAL"),
    ]})

    processos = planilha.ler(arq, tarefa_da_linha=config.tarefa_do_tipo)

    assert [p.tarefa for p in processos] == ["DEFESA FATURADA"]


def test_tarefa_preenche_onde_tipo_de_cobranca_esta_vazia(tmp_path):
    arq = _planilha(tmp_path, {"2026": [
        ("PROCESSO", "TIPO DE COBRANÇA", "TAREFA"),
        (CNJ_A, None, "DEFESA FATURADA"),
        (CNJ_B, "  ", "FATURAMENTO FINAL"),
    ]})

    processos = planilha.ler(arq, tarefa_da_linha=config.tarefa_do_tipo)

    assert [p.tarefa for p in processos] == ["DEFESA FATURADA", "FATURAMENTO FINAL"]


def test_filtro_por_tipo_alcanca_a_coluna_tarefa(tmp_path):
    arq = _planilha(tmp_path, {"2022": [
        ("PROCESSO", "TAREFA"),
        (CNJ_A, "DEFESA FATURADA"),
        (CNJ_B, "FATURAMENTO FINAL"),
    ]})

    processos = planilha.ler(arq, tipo_contem="defesa")

    assert [p.cnj for p in processos] == [CNJ_A]


def test_sem_a_funcao_a_leitura_e_a_de_sempre(tmp_path):
    arq = _planilha(tmp_path, {"2026": [
        CABECALHO,
        (CNJ_A, "DEFESA FATURADA", "Ativo"),
        (CNJ_A, "FATURAMENTO FINAL", "Ativo"),
    ]})

    processos = planilha.ler(arq)

    assert len(processos) == 1
    assert processos[0].tarefa == ""
