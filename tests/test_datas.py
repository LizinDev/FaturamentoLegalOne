"""Datas e horas da tarefa: o que se escreve, o que vai ao formulario, e o que o
Legal One recusaria (regras levantadas na pasta de teste em 29/09/2026)."""
import datetime

import pytest

from datas import Agenda, Datas, ler, ler_so_data

HOJE = datetime.date(2026, 9, 29)
RECUSADOS = {"Pendente"}


# --- leitura -----------------------------------------------------------------

@pytest.mark.parametrize("escrito, esperado", [
    ("29/09/2026", ("29/09/2026", None)),
    ("29/09/2026 09:00", ("29/09/2026", "09:00:00")),
    ("  29/09/2026   9:05:30 ", ("29/09/2026", "09:05:30")),
    ("09:00", (None, "09:00:00")),
    ("9h", (None, "09:00:00")),
    ("9h30", (None, "09:30:00")),
    ("", (None, None)),
    (None, (None, None)),
    # O que o openpyxl devolve para celulas de data e hora.
    (datetime.datetime(2026, 9, 29, 16, 0), ("29/09/2026", "16:00:00")),
    (datetime.datetime(2026, 9, 29), ("29/09/2026", None)),  # data sem hora
    (datetime.date(2026, 9, 29), ("29/09/2026", None)),
    (datetime.time(8, 15), (None, "08:15:00")),
])
def test_ler(escrito, esperado):
    assert ler(escrito) == esperado


@pytest.mark.parametrize("escrito", [
    "31/02/2026", "2026-09-29", "29/09", "29/09/2026 25:00", "amanha",
    "29/09/2026 09:00 extra",
])
def test_ler_recusa(escrito):
    with pytest.raises(ValueError):
        ler(escrito)


def test_publicacao_nao_leva_hora():
    assert ler_so_data("25/09/2026") == "25/09/2026"
    with pytest.raises(ValueError, match="nao leva hora"):
        ler_so_data("25/09/2026 10:00")
    with pytest.raises(ValueError, match="e hora"):
        ler_so_data("10:00")


def test_erro_de_leitura_diz_o_campo():
    with pytest.raises(ValueError, match="conclusao"):
        Agenda.de_textos(inicio="29/09/2026", fim="30/13/2026")


# --- combinacao e resolucao --------------------------------------------------

def test_sem_nada_e_hoje_o_dia_inteiro_na_hora_do_formulario():
    # O comportamento de sempre: hoje, inicio e fim no mesmo dia, hora sugerida.
    assert Agenda().resolver(HOJE) == Datas("29/09/2026", None, "29/09/2026", None)
    assert not Agenda().escolhida


def test_so_inicio_vale_para_os_dois():
    assert Agenda(inicio="01/10/2026").resolver(HOJE) == \
        Datas("01/10/2026", None, "01/10/2026", None)


def test_hora_de_inicio_sozinha_dura_trinta_minutos():
    assert Agenda(hora_inicio="16:00:00").resolver(HOJE) == \
        Datas("29/09/2026", "16:00:00", "29/09/2026", "16:30:00")


def test_hora_de_fim_sozinha_comeca_trinta_minutos_antes():
    # Sem isto, o formulario poria no inicio a proxima hora cheia (as 13h,
    # 14:00), depois de uma conclusao pedida para as 10:00.
    assert Agenda(fim="29/09/2026", hora_fim="10:00:00").resolver(HOJE) == \
        Datas("29/09/2026", "09:30:00", "29/09/2026", "10:00:00")


def test_tarefa_as_23h50_sem_conclusao_termina_no_dia_seguinte():
    assert Agenda(inicio="29/09/2026", hora_inicio="23:50:00").resolver(HOJE) == \
        Datas("29/09/2026", "23:50:00", "30/09/2026", "00:20:00")


def test_conclusao_pedida_no_mesmo_dia_nao_vira_o_dia():
    datas = Agenda(inicio="29/09/2026", hora_inicio="23:50:00",
                   fim="29/09/2026").resolver(HOJE)
    assert (datas.fim, datas.hora_fim) == ("29/09/2026", "23:59:00")


def test_varios_dias_ficam_com_a_hora_do_formulario():
    assert Agenda(inicio="29/09/2026", fim="02/10/2026").resolver(HOJE) == \
        Datas("29/09/2026", None, "02/10/2026", None)


def test_linha_ganha_das_flags_campo_a_campo():
    linha = Agenda(inicio="01/10/2026", publicacao="25/09/2026")
    flags = Agenda(inicio="05/10/2026", hora_inicio="09:00:00",
                   disponibilizacao="24/09/2026")

    assert linha.sobre(flags) == Agenda(
        inicio="01/10/2026", hora_inicio="09:00:00",
        publicacao="25/09/2026", disponibilizacao="24/09/2026")


def test_escolhida_so_com_inicio_ou_conclusao():
    # Publicacao e hora sozinhas nao escolhem o dia: o aviso de data passada
    # nao deve ser confirmado por causa delas.
    assert not Agenda(hora_inicio="09:00:00", publicacao="25/09/2026").escolhida
    assert Agenda(fim="30/09/2026").escolhida


# --- o que o Legal One recusaria ---------------------------------------------

def _problemas(datas, status="Cumprido"):
    return datas.problemas(HOJE, status, RECUSADOS)


def test_inicio_depois_da_conclusao():
    assert "depois da conclusao" in _problemas(
        Datas("02/10/2026", None, "01/10/2026", None))[0]
    assert "depois da conclusao" in _problemas(
        Datas("01/10/2026", "15:00:00", "01/10/2026", "14:00:00"))[0]


def test_mesmo_dia_sem_as_duas_horas_nao_compara_hora():
    assert _problemas(Datas("01/10/2026", "15:00:00", "01/10/2026", None)) == []


def test_pendente_com_conclusao_no_passado():
    ontem = Datas("28/09/2026", None, "28/09/2026", None)

    assert "Pendente" in _problemas(ontem, "Pendente")[0]
    # Cumprido e aceito (com o aviso confirmado).
    assert _problemas(ontem, "Cumprido") == []
    # Inicio no passado com conclusao hoje: a regra olha a conclusao.
    assert _problemas(Datas("28/09/2026", None, "29/09/2026", None), "Pendente") == []
