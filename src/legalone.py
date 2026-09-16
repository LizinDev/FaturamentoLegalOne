"""Automacao do Legal One: busca de processo e cadastro da tarefa."""
import contextlib
import dataclasses
import datetime
import logging
import os
import time
import urllib.parse

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchWindowException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config

logger = logging.getLogger(__name__)


class SessaoExpirada(Exception):
    """O Legal One devolveu a tela de login — abortar a rodada inteira.

    Sem isso, uma sessao expirada no meio do lote transformaria todos os
    processos restantes em 'erro' e queimaria a fila em silencio.
    """


class SalvarIncerto(RuntimeError):
    """O Salvar foi clicado, mas o formulario nao avancou.

    Diferente das outras falhas, aqui a tarefa pode ter sido gravada: em
    15/09/2026, processos que deram este erro e foram recadastrados no
    --retentar ficaram com a tarefa em dobro. Quem trata o erro guarda quantas
    tarefas havia antes, para a retentativa conferir se o Salvar pegou.
    """


@dataclasses.dataclass
class ResultadoBusca:
    """O que a busca por um numero CNJ encontrou."""

    encontrado: bool
    id_legalone: str = ""
    status: str = ""
    detalhe: str = ""
    # Achou mais de uma pasta do tipo Processo com o mesmo numero: e caso de
    # conferencia manual, e nao de "processo inexistente".
    ambiguo: bool = False


def _coluna(cels: list[str], i: int) -> str:
    return cels[i].strip() if i < len(cels) else ""


def interpretar_busca(linhas: list[dict], cnj: str) -> ResultadoBusca:
    """Transforma as linhas da grade de resultados no processo escolhido.

    Separada do Selenium para poder ser testada com as linhas na mao — e aqui
    que moram as regras que decidem em qual pasta a tarefa vai ser cadastrada.
    """
    candidatos = []
    for linha in linhas:
        cels = linha.get("cels") or []
        caminho = urllib.parse.urlparse(linha.get("href") or "").path
        ident = caminho.rstrip("/").split("/")[-1]
        if not ident.isdigit():
            continue

        # A celula do processo traz o CNJ na 1a linha e a pasta na 2a.
        numero = _coluna(cels, config.COL_PROCESSO).split("\n")[0].strip()
        candidatos.append({
            "id": ident,
            "numero": numero,
            "tipo": _coluna(cels, config.COL_TIPO),
            "status": _coluna(cels, config.COL_STATUS),
        })

    # Descarta linhas cujo numero nao e exatamente o buscado (a busca do
    # Legal One tambem casa pasta, envolvido e numeros parciais).
    exatos = [c for c in candidatos if c["numero"] == cnj]
    if not exatos:
        return ResultadoBusca(
            False,
            detalhe=(f"{len(candidatos)} resultado(s), nenhum com o numero exato"
                     if candidatos else "nenhum resultado"),
        )

    # Recurso e incidente repetem o CNJ do processo principal; cadastrar
    # neles criaria tarefa duplicada para a mesma cobranca.
    processos = [c for c in exatos if c["tipo"] == config.TIPO_ACEITO]
    if not processos:
        tipos = ", ".join(sorted({c["tipo"] for c in exatos}))
        return ResultadoBusca(
            False,
            detalhe=f"nenhuma pasta do tipo {config.TIPO_ACEITO} (achei: {tipos})",
        )

    ids = {c["id"] for c in processos}
    if len(ids) > 1:
        return ResultadoBusca(
            False,
            detalhe=f"ambiguo: {len(ids)} pastas do tipo {config.TIPO_ACEITO}, "
                    f"ids {sorted(ids)}",
            ambiguo=True,
        )

    escolhido = processos[0]
    return ResultadoBusca(
        True,
        id_legalone=escolhido["id"],
        status=escolhido["status"],
    )


def conectar() -> webdriver.Chrome:
    """Conecta ao Chrome ja aberto em modo debug (nunca abre outra instancia)."""
    # Em maquinas com chromedriver antigo instalado via chocolatey, o binario do
    # PATH ganha do Selenium Manager e quebra com Chrome novo. Tirando esses
    # diretorios do PATH, o Selenium Manager baixa a versao compativel.
    os.environ["PATH"] = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep)
        if "chocolatey" not in p.lower()
    )

    opcoes = Options()
    opcoes.add_experimental_option("debuggerAddress", config.DEBUG_ADDRESS)
    driver = webdriver.Chrome(options=opcoes)
    logger.info("Conectado ao Chrome em %s", config.DEBUG_ADDRESS)
    return driver


class AutomadorLegalOne:
    """Busca processos e cadastra a tarefa do perfil recebido."""

    def __init__(self, driver: webdriver.Chrome, data_tarefa: str | None,
                 perfil: "config.PerfilTarefa"):
        self.driver = driver
        self.wait = WebDriverWait(driver, config.TIMEOUT_PADRAO)
        self.data_tarefa = data_tarefa
        self.perfil = perfil
        self._aba: str | None = None

    # --- infraestrutura ------------------------------------------------------

    MARCA_ABA = "FATURAMENTO_LEGALONE"

    def usar_aba_propria(self) -> None:
        """Trabalha numa aba dedicada, sem mexer nas abas abertas pelo usuario."""
        handles = self.driver.window_handles

        # O handle guardado vem primeiro porque o Chrome limpa window.name em
        # navegacao entre sites: confiar so na marca faria o programa abrir uma
        # aba nova a cada checagem e encher o Chrome do usuario de abas.
        if self._aba in handles:
            self.driver.switch_to.window(self._aba)
            return

        for handle in handles:
            try:
                self.driver.switch_to.window(handle)
                if self.driver.execute_script("return window.name;") == self.MARCA_ABA:
                    self._aba = handle
                    return
            except Exception as e:
                # Aba fechada entre o window_handles e o switch_to, ou que nao
                # aceita script. Nao e motivo para parar — a busca continua e,
                # se nenhuma servir, abre-se uma aba nova logo abaixo. Fica em
                # debug so para nao esconder um Chrome caindo aos pedacos.
                logger.debug("Aba %s ignorada na procura: %s: %s",
                             handle, type(e).__name__, e)
                continue

        self.driver.switch_to.new_window("tab")
        self.driver.execute_script("window.name = arguments[0];", self.MARCA_ABA)
        self._aba = self.driver.current_window_handle
        logger.info("Aba dedicada criada")

    def aba_viva(self) -> bool:
        """A aba de trabalho ainda existe? (o usuario pode ter fechado)"""
        try:
            self.driver.execute_script("return 1;")
            return True
        except NoSuchWindowException:
            return False
        except Exception:
            return False

    def _checar_sessao(self) -> None:
        url = self.driver.current_url.lower()
        if "login" in url or "account/signin" in url:
            raise SessaoExpirada(
                "O Legal One redirecionou para a tela de login. "
                "Faca login no Chrome e retome a rodada (o progresso esta salvo)."
            )

    def _ir_para(self, url: str) -> None:
        self.driver.get(url)
        self._checar_sessao()

    def _esperar_carregar(self) -> None:
        """Espera a pagina terminar de carregar, sem tratar o timeout como falha.

        Uma requisicao pendente em segundo plano nao impede de ler a grade; se a
        pagina realmente nao veio, quem trata e a leitura seguinte.
        """
        with contextlib.suppress(TimeoutException):
            self.wait.until(lambda d: d.execute_script(
                "return document.readyState === 'complete';"
            ))

    # --- busca ---------------------------------------------------------------

    def buscar_processo(self, cnj: str) -> ResultadoBusca:
        """Acha o id interno do processo a partir do numero CNJ."""
        self._ir_para(config.URL_BUSCA.format(cnj=urllib.parse.quote(cnj)))
        self._esperar_carregar()

        # Varre todas as tabelas da pagina, e nao so a primeira: a grade de
        # resultados nem sempre e a primeira tabela do DOM (filtros e paineis
        # laterais tambem usam <table>), e olhar so uma delas devolveria "nenhum
        # resultado" para um processo que existe. Linha sem link de processo e
        # descartada, entao varrer a mais nao inventa candidato.
        # O Set evita contar duas vezes a mesma linha quando ha tabela aninhada.
        resultado = self.driver.execute_script("""
        const sentinela = !!document.querySelector('#Search');
        const vistas = new Set();
        const linhas = [];
        for (const tabela of document.querySelectorAll('table')) {
          for (const tr of tabela.querySelectorAll('tbody tr')) {
            if (vistas.has(tr)) continue;
            vistas.add(tr);
            const link = [...tr.querySelectorAll('a')]
              .map(a => a.getAttribute('href') || '')
              .find(h => h.includes('/processos/processos/details/'));
            if (!link) continue;
            linhas.push({
              cels: [...tr.querySelectorAll('td')].map(td => td.innerText.trim()),
              href: link,
            });
          }
        }
        return {sentinela: sentinela, linhas: linhas};
        """)

        if not resultado["sentinela"]:
            raise RuntimeError(
                "a pagina de busca de processos nao carregou como esperado; "
                "nao da para afirmar que o processo nao existe"
            )
        return interpretar_busca(resultado["linhas"], cnj)

    def tarefa_ja_existe(self, id_legalone: str, descricao: str) -> bool:
        """Diz se o processo ja tem uma tarefa com essa descricao."""
        return self.contar_tarefas(id_legalone, descricao) > 0

    def contar_tarefas(self, id_legalone: str, descricao: str) -> int:
        """Quantas tarefas com essa descricao o processo tem.

        Rede de seguranca para o caso de o ledger ter sido perdido/recriado, e
        base da conferencia de um Salvar incerto (ver SalvarIncerto): se a
        contagem subiu desde antes do clique, a tarefa foi gravada.

        A lista do Legal One demora a mostrar uma tarefa recem-salva — contar
        logo depois do Salvar da falso "nao gravou". Por isso a conferencia so
        acontece na retentativa, nunca no mesmo processo.

        A busca vai por ?Search= na propria URL: a grade de compromissos e
        paginada, entao procurar o texto na pagina inteira daria falso negativo
        sempre que a tarefa caisse da segunda pagina em diante.
        """
        self._ir_para(
            f"{config.BASE_URL}/processos/Processos/DetailsCompromissosTarefas/"
            f"{id_legalone}?Search={urllib.parse.quote(descricao)}"
        )
        self._esperar_carregar()

        # Le as linhas da grade, e nao o texto da pagina: a descricao tambem
        # aparece dentro do <script> que monta a confirmacao de exclusao, e
        # casar com aquilo daria falso positivo — que aqui significa pular um
        # processo que precisava da tarefa.
        # Quando o filtro nao casa nada, o Legal One nem renderiza a grade. Por
        # isso a ausencia da grade so e aceita como "nao tem a tarefa" se a
        # pagina de compromissos/tarefas de fato carregou — o campo de busca da
        # aba serve de sentinela.
        resultado = self.driver.execute_script("""
        const sentinela = !!document.querySelector('#Search');
        for (const t of document.querySelectorAll('table')) {
          const cab = [...t.querySelectorAll('thead th, thead td')]
            .map(e => e.innerText.trim());
          const i = cab.findIndex(c => /Descri/i.test(c));
          if (i < 0) continue;
          return {sentinela: sentinela, descricoes: [...t.querySelectorAll('tbody tr')]
            .map(tr => {
              const c = tr.querySelectorAll('td');
              return i < c.length ? c[i].innerText.replace(/\\s+/g, ' ').trim() : '';
            })};
        }
        return {sentinela: sentinela, descricoes: null};
        """)

        descricoes = resultado["descricoes"]
        if descricoes is None:
            if not resultado["sentinela"]:
                # A pagina nao e a que esperavamos. Supor que a tarefa nao existe
                # criaria duplicata, entao falha alto: o processo vira 'erro' e
                # aparece no relatorio para conferencia.
                raise RuntimeError(
                    "a aba de compromissos/tarefas nao carregou como esperado; "
                    "nao da para checar duplicata"
                )
            return 0

        alvo = descricao.strip().upper()
        # A celula traz o link "Ver envolvidos" grudado na descricao.
        return sum(1 for texto in descricoes
                   if texto.upper().replace("VER ENVOLVIDOS", "").strip() == alvo)

    # --- preenchimento -------------------------------------------------------

    def _preencher_data(self, campo_id: str, data: str) -> None:
        """O datepicker ignora eventos normais do Selenium; via JS funciona."""
        campo = self.wait.until(EC.visibility_of_element_located((By.ID, campo_id)))
        self.driver.execute_script("arguments[0].value = arguments[1];", campo, data)
        self.driver.execute_script(
            "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", campo
        )

    def _selecionar_status(self, status: str) -> None:
        """Escolhe o status pela lista do lookup.

        Digitar o texto preenche StatusText mas deixa StatusId vazio — so o
        clique na linha vincula o id. E o casamento tem que ser exato: "Nao
        cumprido" contem "Cumprido" como substring.
        """
        botao = self.wait.until(EC.element_to_be_clickable((
            By.CSS_SELECTOR,
            "#LookupStatusCompromissoTarefa .lookup-button.lookup-show",
        )))
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", botao
        )
        botao.click()

        linha = self.wait.until(EC.element_to_be_clickable((
            By.XPATH,
            "//div[contains(@class,'lookup-dropdown')]"
            f"//td[@data-val-field='Value' and normalize-space()='{status}']",
        )))
        linha.click()

        esperado = config.STATUS_VALIDOS.get(status)
        self.wait.until(lambda d: (
            d.find_element(By.ID, "StatusText").get_attribute("value") == status
            and d.find_element(By.ID, "StatusId").get_attribute("value") == esperado
        ))

    def _preencher_responsavel(self, busca: str, esperado: str) -> None:
        """Troca o envolvido padrao (o usuario logado) pelo responsavel da tarefa."""
        campo = self.wait.until(EC.visibility_of_element_located((
            By.CSS_SELECTOR, "input[id*='__EnvolvidoText']"
        )))
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", campo
        )
        campo.clear()
        self.wait.until(lambda d: not campo.get_attribute("value"))
        campo.send_keys(busca)
        # Debounce do lookup do NovaJus: a busca so dispara depois da pausa e
        # nao ha estado no DOM para observar antes do ENTER.
        time.sleep(config.DEBOUNCE_DELAY)
        campo.send_keys(Keys.ENTER)

        linha = self.wait.until(EC.element_to_be_clickable((
            By.XPATH,
            f"//td[@data-val-field='ContatoNome' and normalize-space()='{esperado}']",
        )))
        linha.click()

        self.wait.until(lambda d: d.find_element(
            By.CSS_SELECTOR, "input[id*='__EnvolvidoText']"
        ).get_attribute("value") == esperado)

    def _preencher_descricao(self, descricao: str) -> None:
        """Digita a descricao, repetindo se o formulario apagar o texto.

        A descricao e o que identifica a tarefa depois — inclusive para a
        checagem de duplicata. Se um caractere se perder, a tarefa nasce com o
        texto errado e a rodada seguinte nao reconhece que ela ja existe.
        """
        escrito = ""
        for _ in range(config.TENTATIVAS_DESCRICAO):
            campo = self.wait.until(
                EC.visibility_of_element_located((By.ID, "Descricao"))
            )
            campo.clear()
            campo.send_keys(descricao)
            # O apagao vem do script da pagina terminando de montar o
            # formulario, um instante depois; conferir na hora nao pega.
            time.sleep(config.DEBOUNCE_DELAY)
            escrito = self.driver.find_element(By.ID, "Descricao").get_attribute("value")
            if escrito == descricao:
                return
        raise RuntimeError(f"campo Descricao ficou {escrito!r}, esperava {descricao!r}")

    def _fechar_aviso_pendo(self) -> bool:
        """Fecha o aviso in-app do Legal One (Pendo), se houver um na tela.

        O aviso fica no canto de baixo, por cima do Salvar, e volta a cada
        pagina ate alguem dispensar. Em 16/09/2026 ele fez todo processo falhar
        com o clique interceptado e o disjuntor disparar a cada reinicio. So
        clica em botao de dispensa ("Ok, entendi" ou o X): o aviso tambem traz
        botoes que abrem outras paginas.
        """
        return bool(self.driver.execute_script("""
        // offsetParent nao serve: e null em elemento position:fixed, que e
        // justamente como o aviso fica ancorado no canto da tela.
        const visivel = e => e && e.getClientRects().length > 0
          && getComputedStyle(e).visibility !== 'hidden';
        for (const guia of document.querySelectorAll(
            '#pendo-guide-container, ._pendo-step-container-styles')) {
          if (!visivel(guia)) continue;
          const botao = [...guia.querySelectorAll('button, ._pendo-close-guide')]
            .find(b => visivel(b) && (b.classList.contains('_pendo-close-guide')
                   || /^\\s*ok,?\\s*entendi\\s*$/i.test(b.innerText || '')));
          if (botao) { botao.click(); return true; }
        }
        return false;
        """))

    def _esperar_mascara_sumir(self) -> None:
        """Espera a mascara de carregamento dos lookups sair da frente do Salvar."""
        with contextlib.suppress(TimeoutException):
            self.wait.until(lambda d: not d.execute_script("""
            return [...document.querySelectorAll('.modal-mask')]
              .some(m => m.getClientRects().length > 0
                         && getComputedStyle(m).visibility !== 'hidden');
            """))

    def _clicar_salvar(self) -> None:
        botao = self.wait.until(EC.element_to_be_clickable((
            By.XPATH, "//button[@name='ButtonSave' and @value='0']"
        )))
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", botao
        )
        if self._fechar_aviso_pendo():
            logger.info("Aviso do Legal One (Pendo) dispensado")
        try:
            botao.click()
        except ElementClickInterceptedException:
            # Clique interceptado nao chega ao botao, entao repetir nao grava
            # duas vezes. Uma segunda chance so, depois de limpar a frente de
            # novo: se ainda falhar, o processo vira erro como antes. A espera
            # da mascara fica so aqui, e nao antes de todo clique, para nao
            # pagar o timeout inteiro por processo se uma mascara ficar presa.
            if self._fechar_aviso_pendo():
                logger.info("Aviso do Legal One (Pendo) dispensado")
            self._esperar_mascara_sumir()
            botao.click()

    def _erros_de_validacao(self) -> str:
        return self.driver.execute_script("""
        return [...document.querySelectorAll(
          '.field-validation-error, .validation-summary-errors, .alert-danger')]
          .map(e => e.innerText.trim()).filter(t => t).join(' | ');
        """) or ""

    def cadastrar_tarefa(self, id_legalone: str, executar: bool,
                         perfil: "config.PerfilTarefa | None" = None) -> str:
        """Preenche o formulario da tarefa. So salva se executar=True.

        perfil sobrepoe o do automador — e assim que uma rodada unica cadastra
        tarefas diferentes, uma por linha da planilha. Devolve uma descricao
        curta do que foi feito.
        """
        perfil = perfil or self.perfil
        self._ir_para(config.URL_NOVA_TAREFA.format(id=id_legalone))

        self._preencher_descricao(perfil.descricao)

        # Tipo e datas ja vem certos do formulario; confirmamos em vez de
        # reescrever, para nao desfazer o vinculo de TipoId.
        tipo = self.driver.find_element(By.ID, "TipoText").get_attribute("value")
        if tipo != perfil.tipo:
            raise RuntimeError(
                f"Tipo padrao mudou: esperava {perfil.tipo!r}, veio {tipo!r}"
            )

        data_tarefa = self.data_tarefa or datetime.date.today().strftime("%d/%m/%Y")
        self._preencher_data("DtInicial", data_tarefa)
        self._preencher_data("DtFinal", data_tarefa)
        self._selecionar_status(perfil.status)
        self._preencher_responsavel(
            perfil.responsavel_busca, perfil.responsavel_esperado
        )

        erros = self._erros_de_validacao()
        if erros:
            raise RuntimeError(f"formulario com erro antes de salvar: {erros}")

        if not executar:
            return "simulado (formulario preenchido, nao salvo)"

        self._clicar_salvar()

        # Sucesso = sai do formulario de criacao. Se continuar nele, a pagina
        # tem o motivo da recusa — ou o Salvar gravou e so a navegacao travou.
        try:
            self.wait.until(lambda d: "CreateFromProcesso" not in d.current_url)
        except TimeoutException:
            erros = self._erros_de_validacao()
            if erros:
                raise RuntimeError(f"nao salvou: {erros}")
            raise SalvarIncerto("nao salvou: formulario nao avancou")

        self._checar_sessao()
        return "cadastrada"
