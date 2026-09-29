<#
Solta uma rodada longa e a religa ate cadastrar N tarefas.

Uma rodada de milhares de processos para no meio: disjuntor, Legal One fora
do ar por uns minutos. Este script repete o comando ate a meta, mas so quando
repetir adianta — pelo codigo de saida do main.py:

  0    a fila acabou antes da meta: nao ha mais o que fazer, encerra
  1    disjuntor ou Chrome: confere Chrome e Legal One e religa
  2    erro de uso: repetir nao muda nada, encerra
  3    sessao expirada: precisa de alguem fazendo login, encerra
  130  Ctrl+C: encerra

Cada tentativa pede so o que falta (--max-cadastros), contando no ledger os
cadastros feitos desde o inicio deste script.

O Python e solto com Start-Process, e nao como tarefa em segundo plano do
shell: em 02/09/2026 a rodada assim morreu tres vezes sem aviso. Pelo mesmo
motivo, solte o proprio script destacado:

  $a = @('-File','scripts\rodada_ate_a_meta.ps1','-Planilha','"Planilhas\Faturamento 2024.xlsx"',
         '-Meta','1000','--','--tarefa','auto')
  Start-Process powershell -ArgumentList $a -WindowStyle Hidden

O log do programa vai para logs\rodada_<data-hora>.log (o log sai no stderr).
Acompanhe com: Get-Content logs\rodada_*.log -Wait -Tail 20
#>
param(
    [Parameter(Mandatory)] [string]$Planilha,
    [Parameter(Mandatory)] [int]$Meta,
    [int]$Tentativas = 12,
    # Tudo depois de "--" vai para o main.py como esta (--tarefa, --abas...).
    [Parameter(ValueFromRemainingArguments)] [string[]]$Resto = @()
)

$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$inicio = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
$log = Join-Path $raiz ("logs\rodada_{0}.log" -f (Get-Date).ToString("yyyy-MM-dd_HHmm"))
$erroTmp = Join-Path $raiz "logs\rodada_atual.err"
$saidaTmp = Join-Path $raiz "logs\rodada_atual.out"
New-Item -ItemType Directory -Force (Join-Path $raiz "logs") | Out-Null

function Anotar($texto) {
    Add-Content $log ("[{0}] [meta] {1}" -f (Get-Date).ToString("HH:mm:ss"), $texto)
}

function Cadastrados-Desde-O-Inicio {
    # Sem aspas duplas no codigo: o PowerShell 5.1 as mutila ao passar para um
    # executavel nativo. A data vai por argumento.
    $py = "import sqlite3,sys;c=sqlite3.connect('data/ledger.sqlite3');" +
          "print(c.execute('SELECT COUNT(*) FROM processos WHERE situacao IN " +
          "(?,?) AND quando>=?',('ok','recadastrada',sys.argv[1])).fetchone()[0])"
    return [int](python -c $py $inicio)
}

function Responde($url, $segundos) {
    try { Invoke-WebRequest $url -TimeoutSec $segundos -UseBasicParsing | Out-Null; return $true }
    catch { return $false }
}

# Argumento com espaco precisa das aspas por dentro, senao o Start-Process o
# parte no espaco e o argparse recusa.
function Citar($a) { if ($a -match '\s') { return '"' + $a + '"' } else { return $a } }

Anotar "inicio: meta $Meta, planilha $Planilha, extras: $($Resto -join ' ')"
for ($t = 1; $t -le $Tentativas; $t++) {
    $feitos = Cadastrados-Desde-O-Inicio
    $falta = $Meta - $feitos
    Anotar "tentativa $t : $feitos cadastrado(s) desde o inicio, faltam $falta"
    if ($falta -le 0) { Anotar "META ATINGIDA"; break }

    if (-not (Responde "http://127.0.0.1:9222/json/version" 15)) {
        Anotar "PAREI: o Chrome de debug nao responde na 9222"; break
    }
    if (-not (Responde "https://firm.legalone.com.br/" 25)) {
        Anotar "Legal One fora do ar; espero 3 minutos"; Start-Sleep -Seconds 180; continue
    }

    $argumentos = @('src/main.py', '--planilha', (Citar $Planilha),
                    '--max-cadastros', "$falta", '--executar') + ($Resto | ForEach-Object { Citar $_ })
    $p = Start-Process -FilePath python -ArgumentList $argumentos -WorkingDirectory $raiz `
        -RedirectStandardError $erroTmp -RedirectStandardOutput $saidaTmp `
        -WindowStyle Hidden -PassThru -Wait
    Get-Content $erroTmp -ErrorAction SilentlyContinue | Add-Content $log
    Anotar "main.py saiu com codigo $($p.ExitCode)"

    switch ($p.ExitCode) {
        0   { Anotar "a fila acabou antes da meta"; $t = $Tentativas + 1 }
        2   { Anotar "PAREI: erro de uso (ver acima)"; $t = $Tentativas + 1 }
        3   { Anotar "PAREI: sessao expirada - faca login no Chrome e rode de novo"; $t = $Tentativas + 1 }
        130 { Anotar "PAREI: interrompida"; $t = $Tentativas + 1 }
        default { Start-Sleep -Seconds 20 }
    }
}
Remove-Item $erroTmp, $saidaTmp -ErrorAction SilentlyContinue
Anotar "encerrado: $(Cadastrados-Desde-O-Inicio) cadastro(s) desde o inicio"
