"""Automacao do Legal One: busca de processo e cadastro da tarefa."""
import contextlib
import dataclasses
import json
import logging
import os
import time
import urllib.parse

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchWindowException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import catalogo
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


# O que a pagina diz depois do Salvar. Ver situacao_pos_salvar.
SALVOU = "salvou"
PEDIU_CONFIRMACAO = "pediu_confirmacao"
RECUSOU = "recusou"

# O formulario devolvido pelo servidor, com aviso ou erro. So chega aqui quem
# nao foi gravado: o cadastro aceito redireciona para outra pagina.
CAMINHO_FORMULARIO_DEVOLVIDO = "/processos/tarefas/edit"

# Trecho do aviso de data passada: "A data de 'Inicio' do compromisso ou de
# 'Conclusao' da tarefa e anterior a data atual. Deseja salvar mesmo assim?"
TRECHO_AVISO_DATA_PASSADA = "anterior à data atual"


def situacao_pos_salvar(url: str, aviso: str, erros: str) -> str | None:
    """Le a pagina depois do Salvar: SALVOU, PEDIU_CONFIRMACAO, RECUSOU ou None.

    None quer dizer "ainda nao da para afirmar" — o POST nao voltou, ou o
    formulario voltou e o aviso ainda nao apareceu. Separada do Selenium, como
    interpretar_busca, porque e aqui que se decide se um cadastro vira 'ok'.

    Ate a 1.8 o sucesso era "saiu de CreateFromProcesso". Mas o servidor devolve
    o formulario em /processos/tarefas/Edit quando recusa (erro de validacao) ou
    quando pede confirmacao (data anterior a hoje), e essa URL ja satisfazia a
    regra: testado em 29/09/2026 na pasta de teste, era esse o "cadastro
    fantasma" da virada do dia — o aviso ficava na tela sem resposta e o
    ledger gravava 'ok'. O cadastro aceito vai para outro endereco
    (/processos/compromissotarefa, que mostra "erro inesperado no servidor" mas
    com a tarefa gravada).
    """
    caminho = urllib.parse.urlparse(url).path.lower().rstrip("/")
    if "createfromprocesso" in caminho:
        return None
    if caminho.endswith(CAMINHO_FORMULARIO_DEVOLVIDO):
        # O aviso tem prioridade: com ele na tela o formulario ainda pode ser
        # gravado, entao nao e recusa.
        if aviso:
            return PEDIU_CONFIRMACAO
        if erros:
            return RECUSOU
        return None
    return SALVOU


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
    """Busca processos e cadastra a tarefa que receber em cada chamada.

    Nao guarda perfil nem data: quem decide a tarefa de cada processo e a
    rodada, que tambem grava no ledger o que foi enviado. Com o automador
    decidindo a data por conta propria, o ledger nao teria como saber qual foi.
    """

    def __init__(self, driver: webdriver.Chrome):
        self.driver = driver
        self.wait = WebDriverWait(driver, config.TIMEOUT_PADRAO)
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
        """O datepicker ignora eventos normais do Selenium; via JS funciona.

        Serve tambem para os campos de hora (HrInicio/HrFinal), que ficam
        colados ao datepicker e sao lidos pelo mesmo evento de change.
        """
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

    def _preencher_responsavel(self, nome: str) -> None:
        """Troca o envolvido padrao (o usuario logado) pelo responsavel da tarefa.

        nome e o nome completo, como o Legal One o escreve (a checagem do inicio
        da rodada ja o resolveu). Digita-lo inteiro deixa um resultado so na
        lista, que mostra no maximo 10.
        """
        campo = self.wait.until(EC.visibility_of_element_located((
            By.CSS_SELECTOR, "input[id*='__EnvolvidoText']"
        )))
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", campo
        )
        campo.clear()
        self.wait.until(lambda d: not campo.get_attribute("value"))
        campo.send_keys(nome)
        # Debounce do lookup do NovaJus: a busca so dispara depois da pausa e
        # nao ha estado no DOM para observar antes do ENTER.
        time.sleep(config.DEBOUNCE_DELAY)
        campo.send_keys(Keys.ENTER)

        # A linha e achada comparando o texto em Python, e nao montando um
        # XPath com o nome: um apostrofo no nome quebraria a expressao.
        def linha_do_nome(d):
            for td in d.find_elements(
                By.CSS_SELECTOR, "td[data-val-field='ContatoNome']"
            ):
                if td.is_displayed() and " ".join(td.text.split()) == nome:
                    return td
            return False

        self.wait.until(linha_do_nome).click()

        self.wait.until(lambda d: d.find_element(
            By.CSS_SELECTOR, "input[id*='__EnvolvidoText']"
        ).get_attribute("value") == nome)

    def _esperar_ajax(self) -> None:
        """Espera as requisicoes do jQuery da pagina terminarem.

        Escolher um subtipo dispara a sugestao de Prazo/Data de publicacao, que
        chega por ajax e pode recalcular inicio e fim. Escrever as datas antes
        dela voltar seria desfeito em silencio.
        """
        with contextlib.suppress(TimeoutException):
            self.wait.until(lambda d: d.execute_script(
                "return !window.jQuery || window.jQuery.active === 0;"
            ))

    def _selecionar_tipo(self, tarefa: "config.Tarefa") -> None:
        """Escolhe o tipo na arvore do lookup, se ele nao for o que ja vem.

        A arvore tem os subtipos escondidos ate o pai ser expandido, e cada
        linha tem o id do tipo (tr#tipo_4, tr#subtipo_9). Digitar o texto no
        campo nao serve: como no status, so o clique na linha vincula o id.
        """
        if not tarefa.tipo_id:
            # Tarefa que nao passou pela checagem: so confere o que ja vem, como
            # ate a 1.8 — nunca escolhe um tipo que ninguem conferiu.
            texto = self.driver.find_element(By.ID, "TipoText").get_attribute("value")
            if texto != tarefa.tipo:
                raise RuntimeError(
                    f"Tipo padrao mudou: esperava {tarefa.tipo!r}, veio {texto!r}"
                )
            return
        atual = self.driver.find_element(By.ID, "TipoId").get_attribute("value")
        if atual == tarefa.tipo_id:
            return

        botao = self.wait.until(EC.element_to_be_clickable((
            By.CSS_SELECTOR, "#lookup_tipo .lookup-button.lookup-show"
        )))
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", botao
        )
        botao.click()
        linha = self.wait.until(EC.presence_of_element_located((
            By.CSS_SELECTOR, f"tr[id='{tarefa.tipo_id}']"
        )))
        if not linha.is_displayed():
            # Subtipo: o pai vem recolhido. A classe child-of-<pai> diz qual e.
            pai = next((c.removeprefix("child-of-")
                        for c in (linha.get_attribute("class") or "").split()
                        if c.startswith("child-of-")), "")
            if not pai:
                raise RuntimeError(
                    f"tipo {tarefa.tipo_id} escondido e sem pai na arvore"
                )
            self.driver.find_element(
                By.CSS_SELECTOR, f"tr[id='{pai}'] .expander"
            ).click()
            self.wait.until(lambda d: linha.is_displayed())
        linha.find_element(By.TAG_NAME, "td").click()

        self.wait.until(lambda d: d.find_element(By.ID, "TipoId")
                        .get_attribute("value") == tarefa.tipo_id)
        self._esperar_ajax()

    def _conferir_datas(self, tarefa: "config.Tarefa") -> None:
        """Confere, logo antes do Salvar, que as datas sao as pedidas.

        Subtipo com contagem de prazo recalcula inicio e fim por conta propria.
        Uma tarefa gravada com data diferente da pedida e pior do que um erro,
        porque passa por cadastro certo.
        """
        pedidas = {"DtInicial": tarefa.data_inicio, "DtFinal": tarefa.data_fim,
                   "HrInicio": tarefa.hora_inicio, "HrFinal": tarefa.hora_fim}
        for campo, valor in pedidas.items():
            if valor is None:
                continue
            atual = self.driver.find_element(By.ID, campo).get_attribute("value")
            if atual != valor:
                raise RuntimeError(
                    f"o formulario trocou {campo}: pedi {valor!r}, ficou {atual!r}"
                )

    # --- listas para a checagem do inicio da rodada ---------------------------

    URL_TIPOS = "/config/TipoAndamentoCompromissoTarefa/LookupTreeTiposTarefa"
    # O mesmo endereco que o campo Nome dos envolvidos usa. Sem pageSize o
    # Legal One responde 500.
    URL_USUARIOS = ("/config/Usuarios/LookupGridUsuario"
                    "?ativosOnly=True&pageSize=50&term={}")

    def _buscar_json(self, caminho: str) -> dict:
        """GET num endpoint de lookup, com a sessao do Chrome.

        Vai por fetch dentro da pagina, e nao pelo Python, porque e a sessao do
        navegador que esta logada. Por isso a aba precisa estar no Legal One.
        """
        if not self.driver.current_url.startswith(config.BASE_URL):
            # Nao a raiz: ela redireciona para firm.legalone.com.br/home, outro
            # dominio, e o fetch dali pedia a lista ao host errado (voltava a
            # pagina HTML dele). A busca de processos fica no novajus.
            self._ir_para(config.URL_BUSCA.format(cnj=""))
        # URL absoluta: se mesmo assim a aba estiver noutro dominio, o fetch
        # falha como cross-origin, em vez de responder a pagina de outro site.
        caminho = config.BASE_URL + caminho
        resposta = self.driver.execute_async_script("""
        const [url, fim] = arguments;
        fetch(url, {credentials: 'same-origin',
                    headers: {'X-Requested-With': 'XMLHttpRequest'}})
          .then(r => r.text().then(t => fim({status: r.status, url: r.url, texto: t})))
          .catch(e => fim({status: 0, url: '', texto: String(e)}));
        """, caminho)
        final = (resposta.get("url") or "").lower()
        if "login" in final or "account/signin" in final:
            raise SessaoExpirada(
                "O Legal One redirecionou para a tela de login. "
                "Faca login no Chrome e repita o comando."
            )
        if resposta.get("status") != 200:
            raise RuntimeError(
                f"{caminho} respondeu {resposta.get('status')}: "
                f"{(resposta.get('texto') or '')[:200]}"
            )
        try:
            return json.loads(resposta["texto"])
        except ValueError:
            raise RuntimeError(f"{caminho} nao devolveu JSON")

    def listar_tipos(self) -> list["catalogo.Tipo"]:
        """A arvore inteira de tipos e subtipos de tarefa (~850 itens)."""
        return catalogo.tipos_da_arvore(self._buscar_json(self.URL_TIPOS)["Rows"])

    def buscar_usuarios(self, termo: str) -> list[str]:
        """Nomes dos usuarios ativos que a busca do Legal One casa com o termo.

        So o nome sai daqui: a resposta traz tambem CPF e e-mail, que a
        automacao nao usa e nao devem parar em log.
        """
        dados = self._buscar_json(
            self.URL_USUARIOS.format(urllib.parse.quote(termo))
        )
        return [str(linha["ContatoNome"]) for linha in dados.get("Rows", [])]

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

    # Onde o Legal One poe as mensagens de recusa. O span-error-validation-message
    # e o do erro de data/status ("O status selecionado nao pode ser
    # 'Pendente'..."), e ficou de fora ate a 1.8 — a recusa passava por sucesso.
    SELETOR_ERROS = (".field-validation-error, .validation-summary-errors, "
                     ".alert-danger, .span-error-validation-message")

    def _erros_de_validacao(self) -> str:
        # So conta mensagem visivel: o formulario pode trazer o span de erro
        # montado e escondido, e ler texto escondido recusaria um cadastro bom.
        return self.driver.execute_script("""
        return [...document.querySelectorAll(arguments[0])]
          .filter(e => e.getClientRects().length > 0)
          .map(e => e.innerText.trim()).filter(t => t).join(' | ');
        """, self.SELETOR_ERROS) or ""

    def _aviso_na_tela(self) -> str:
        """Texto do aviso modal do Legal One (Sim/Nao), ou "" se nao houver.

        E um popup do proprio site, e nao um alert do navegador: o Selenium nao
        o enxerga como alerta, e so a leitura do DOM o encontra.
        """
        return self.driver.execute_script("""
        const ok = document.getElementById('popup_ok');
        if (!ok || ok.getClientRects().length === 0) return '';
        const caixa = document.getElementById('popup_message')
          || document.getElementById('popup_container') || ok.parentElement;
        const texto = (caixa.innerText || '').replace(/\\s+/g, ' ').trim();
        return texto || '(aviso sem texto)';
        """) or ""

    def _responder_aviso(self, sim: bool) -> None:
        botao = "popup_ok" if sim else "popup_cancel"
        self.driver.execute_script(
            "document.getElementById(arguments[0]).click();", botao
        )

    def _esperar_resposta_do_salvar(self, confirmado: bool = False) -> str:
        """Espera o Legal One dizer o que fez com o Salvar (ver situacao_pos_salvar).

        confirmado: a espera e a de depois do Sim no aviso. Ai a pagina ainda e
        o formulario devolvido enquanto o novo POST viaja, entao ficar parado
        nela e incerteza (pode ter gravado), e nao recusa.
        """
        def ler(d):
            # No meio da navegacao do POST o script pode falhar por um instante
            # (a pagina velha ja se foi, a nova ainda nao montou). Isso nao e
            # resposta nenhuma: sem tolerar, um cadastro gravado viraria erro, e
            # o --retentar o gravaria de novo.
            try:
                return situacao_pos_salvar(
                    d.current_url, self._aviso_na_tela(), self._erros_de_validacao()
                )
            except WebDriverException:
                return None

        try:
            return self.wait.until(ler)
        except TimeoutException:
            pass
        erros = self._erros_de_validacao()
        if erros:
            raise RuntimeError(f"nao salvou: {erros}")
        if confirmado or "CreateFromProcesso" in self.driver.current_url:
            # O POST nem voltou: pode ter gravado. Ver SalvarIncerto.
            raise SalvarIncerto("nao salvou: formulario nao avancou")
        # O servidor devolveu o formulario, sem aviso nem erro legivel. Nao foi
        # gravado — e antes da 1.8 isto virava 'ok'.
        raise RuntimeError(
            "nao salvou: o Legal One devolveu o formulario sem mensagem legivel"
        )

    def cadastrar_tarefa(self, id_legalone: str, executar: bool,
                         tarefa: "config.Tarefa") -> str:
        """Preenche o formulario com a tarefa recebida. So salva se executar=True.

        Devolve uma descricao curta do que foi feito.
        """
        self._ir_para(config.URL_NOVA_TAREFA.format(id=id_legalone))

        self._preencher_descricao(tarefa.descricao)

        # O tipo vem antes das datas: um subtipo com contagem de prazo recalcula
        # inicio e fim, e as datas pedidas tem que ser escritas por cima disso.
        self._selecionar_tipo(tarefa)

        self._preencher_data("DtInicial", tarefa.data_inicio)
        self._preencher_data("DtFinal", tarefa.data_fim)
        # Sem hora pedida, fica a que o formulario sugere — o comportamento de
        # sempre. Hora so e escrita quando a tarefa traz uma.
        if tarefa.hora_inicio:
            self._preencher_data("HrInicio", tarefa.hora_inicio)
        if tarefa.hora_fim:
            self._preencher_data("HrFinal", tarefa.hora_fim)
        self._selecionar_status(tarefa.status)
        self._preencher_responsavel(tarefa.responsavel)
        self._conferir_datas(tarefa)

        erros = self._erros_de_validacao()
        if erros:
            raise RuntimeError(f"formulario com erro antes de salvar: {erros}")

        if not executar:
            return "simulado (formulario preenchido, nao salvo)"

        self._clicar_salvar()

        situacao = self._esperar_resposta_do_salvar()
        confirmou = False
        if situacao == PEDIU_CONFIRMACAO:
            aviso = self._aviso_na_tela()
            # So confirma o que foi pedido de proposito. Sem --data, uma data
            # passada so aparece num processo que atravessou a meia-noite: ai o
            # certo e virar erro e ser refeito com a data do dia, e nao gravar
            # uma tarefa com a data de ontem.
            if not (tarefa.confirmar_data_passada
                    and TRECHO_AVISO_DATA_PASSADA in aviso):
                self._responder_aviso(sim=False)
                raise RuntimeError(f"nao salvou: o Legal One pediu confirmacao: {aviso}")
            self._responder_aviso(sim=True)
            confirmou = True
            # Sem esperar o aviso sair, a leitura seguinte o acharia ainda na
            # tela e tomaria o mesmo aviso por um segundo.
            with contextlib.suppress(TimeoutException):
                self.wait.until(lambda d: not self._aviso_na_tela())
            situacao = self._esperar_resposta_do_salvar(confirmado=True)
            if situacao == PEDIU_CONFIRMACAO:
                segundo = self._aviso_na_tela()
                self._responder_aviso(sim=False)
                raise RuntimeError(
                    f"nao salvou: segundo aviso depois de confirmar: {segundo}"
                )

        if situacao == RECUSOU:
            raise RuntimeError(f"nao salvou: {self._erros_de_validacao()}")

        self._checar_sessao()
        if confirmou:
            return "cadastrada (data anterior a hoje confirmada)"
        return "cadastrada"
