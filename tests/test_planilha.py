"""Leitura da planilha de cobrancas."""
import pytest
from openpyxl import Workbook

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
