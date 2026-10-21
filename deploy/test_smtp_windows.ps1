[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$sourcePath = Join-Path $PSScriptRoot 'smtp_windows.ps1'
$fixtureDirectory = Join-Path $projectDirectory ('tmp\smtp-windows-regression-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($fixtureDirectory)
$fixtureScriptPath = Join-Path $fixtureDirectory 'child.ps1'
$fixtureCredentialPath = Join-Path $fixtureDirectory 'credential.fixture.xml'
$fixtureOutputPath = Join-Path $fixtureDirectory 'stdout.log'
$fixtureErrorPath = Join-Path $fixtureDirectory 'stderr.log'
$windowsPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'

# Solo datos ficticios. No se muestra una ventana, no se usa la credencial real
# y no se conecta a SMTP. El hijo reproduce el lanzamiento del formulario.
$fixtureScript = @'
param([string]$SourcePath, [string]$FixturePath)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$stage = 'extract-source'
try {
    $parseErrors = $null
    $parseTokens = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$parseTokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw 'Invalid helper source.' }
    $runtimePrefix = $ast.Find({
        param($node)
        $node -is [Management.Automation.Language.IfStatementAst] -and
        $node.Extent.Text -match '^if \(\$PSVersionTable.PSEdition'
    }, $true)
    if (-not $runtimePrefix) { throw 'Missing compatible runtime module path.' }
    Invoke-Expression $runtimePrefix.Extent.Text
    $saveFunction = $ast.Find({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Save-LocalSmtpCredential'
    }, $true)
    if (-not $saveFunction) { throw 'Missing save function.' }
    Invoke-Expression $saveFunction.Extent.Text

    $stage = 'save-and-roundtrip'
    $fixturePassword = 'abcdefghijklmnop'
    $secureFixture = ConvertTo-SecureString $fixturePassword -AsPlainText -Force
    Save-LocalSmtpCredential -Account 'daniel.varela@dvsolutionshn.com' -Password $secureFixture -Path $FixturePath
    $roundTrip = Import-Clixml -LiteralPath $FixturePath
    if ($roundTrip.UserName -ne 'daniel.varela@dvsolutionshn.com' -or
        $roundTrip.GetNetworkCredential().Password -ne $fixturePassword) {
        throw 'Encrypted credential roundtrip failed.'
    }

    $stage = 'no-plaintext'
    $previousCiphertext = [IO.File]::ReadAllText($FixturePath)
    if ($previousCiphertext.Contains($fixturePassword)) {
        throw 'The fixture password was not encrypted.'
    }

    $stage = 'preserve-previous-on-failure'
    $expectedFailure = $false
    try {
        $invalidFixture = ConvertTo-SecureString 'short' -AsPlainText -Force
        Save-LocalSmtpCredential -Account 'daniel.varela@dvsolutionshn.com' -Password $invalidFixture -Path $FixturePath
    }
    catch { $expectedFailure = $true }
    if (-not $expectedFailure -or [IO.File]::ReadAllText($FixturePath) -ne $previousCiphertext) {
        throw 'A failed save did not preserve the previous file.'
    }
    $leftoverFiles = [IO.Directory]::GetFiles([IO.Path]::GetDirectoryName($FixturePath), 'credential.fixture.xml.*.tmp')
    if ($leftoverFiles.Length -ne 0) { throw 'A failed save left temporary credentials.' }

    Write-Output 'SMTP_WINDOWS_REGRESSION_OK'
}
catch {
    Write-Output ('SMTP_WINDOWS_REGRESSION_FAIL_STAGE=' + $stage)
    Write-Output ('SMTP_WINDOWS_REGRESSION_FAIL_TYPE=' + $_.Exception.GetType().FullName)
    Write-Output ('SMTP_WINDOWS_REGRESSION_FAIL_ID=' + $_.FullyQualifiedErrorId)
    exit 1
}
'@

try {
    Set-Content -LiteralPath $fixtureScriptPath -Value $fixtureScript -Encoding UTF8
    $fixtureArguments = @(
        '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File',
        ('"' + $fixtureScriptPath + '"'),
        ('"' + $sourcePath + '"'),
        ('"' + $fixtureCredentialPath + '"')
    )
    $fixtureProcess = Start-Process -FilePath $windowsPowerShell -WindowStyle Hidden `
        -ArgumentList $fixtureArguments -RedirectStandardOutput $fixtureOutputPath `
        -RedirectStandardError $fixtureErrorPath -PassThru
    if (-not $fixtureProcess.WaitForExit(15000)) {
        $fixtureProcess.Kill()
        throw 'The isolated SMTP regression process timed out.'
    }
    $fixtureOutput = [IO.File]::ReadAllText($fixtureOutputPath)
    if ($fixtureProcess.ExitCode -ne 0 -or $fixtureOutput -notmatch 'SMTP_WINDOWS_REGRESSION_OK') {
        Write-Output $fixtureOutput
        throw 'The isolated SMTP regression failed.'
    }
    Write-Output 'SMTP Windows: lanzamiento WinPS 5.1, cifrado, recuperacion y preservacion ante fallo correctos.'
}
finally {
    # El directorio es exclusivo de este test. No se elimina nada recursivamente.
    foreach ($fixtureFile in [IO.Directory]::GetFiles($fixtureDirectory)) {
        [IO.File]::Delete($fixtureFile)
    }
    [IO.Directory]::Delete($fixtureDirectory)
}
