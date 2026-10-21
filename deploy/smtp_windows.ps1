[CmdletBinding()]
param(
    [ValidateSet('Guardar', 'Comprobar', 'EnviarPrueba')]
    [string]$Accion = 'Guardar'
)

$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -eq 'Desktop') {
    # Un proceso iniciado desde PowerShell 7 puede heredar sus modulos.
    # Windows PowerShell debe cargar primero los de su propio runtime.
    $runtimeModuleDirectory = [IO.Path]::Combine($PSHOME, 'Modules')
    $env:PSModulePath = $runtimeModuleDirectory + [IO.Path]::PathSeparator + $env:PSModulePath
}
$projectDirectory = Split-Path -Parent $PSScriptRoot
$credentialPath = Join-Path $projectDirectory '.env.smtp.credentials.xml'
$smtpAccount = 'daniel.varela@dvsolutionshn.com'

function Save-LocalSmtpCredential {
    param(
        [string]$Account,
        [securestring]$Password,
        [string]$Path
    )
    $temporaryPath = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    try {
        $savedCredential = New-Object System.Management.Automation.PSCredential($Account, $Password)
        $savedCredential | Export-Clixml -LiteralPath $temporaryPath -Force
        $verifiedCredential = Import-Clixml -LiteralPath $temporaryPath
        if ($verifiedCredential.UserName -ne $Account -or $verifiedCredential.GetNetworkCredential().Password.Length -ne 16) {
            throw 'No se pudo verificar el archivo cifrado.'
        }
        Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -ErrorAction SilentlyContinue
        }
        $savedCredential = $null
        $verifiedCredential = $null
    }
}

if ($Accion -eq 'Guardar') {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()
    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'DV Solutions - Guardar clave SMTP (corregido)'
    $form.ClientSize = New-Object System.Drawing.Size(540, 230)
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.ShowInTaskbar = $true
    $form.TopMost = $true
    $form.Add_Shown({ $this.Activate() })

    $label = New-Object System.Windows.Forms.Label
    $label.Location = New-Object System.Drawing.Point(20, 18)
    $label.Size = New-Object System.Drawing.Size(500, 95)
    $label.Text = "Cuenta principal: $smtpAccount`r`nRemitente: oasis@dvsolutionshn.com`r`n`r`nPegue la contrasena de APLICACION de Google (16 caracteres).`r`nSe guardara cifrada para su usuario de Windows. Este paso no envia correo."
    $form.Controls.Add($label)

    $passwordBox = New-Object System.Windows.Forms.TextBox
    $passwordBox.Location = New-Object System.Drawing.Point(20, 118)
    $passwordBox.Size = New-Object System.Drawing.Size(500, 25)
    $passwordBox.UseSystemPasswordChar = $true
    $form.Controls.Add($passwordBox)

    $saveButton = New-Object System.Windows.Forms.Button
    $saveButton.Text = 'Guardar clave cifrada'
    $saveButton.Location = New-Object System.Drawing.Point(190, 175)
    $saveButton.Size = New-Object System.Drawing.Size(180, 32)
    $saveButton.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.Controls.Add($saveButton)
    $form.AcceptButton = $saveButton

    $cancelButton = New-Object System.Windows.Forms.Button
    $cancelButton.Text = 'Cancelar'
    $cancelButton.Location = New-Object System.Drawing.Point(385, 175)
    $cancelButton.Size = New-Object System.Drawing.Size(135, 32)
    $cancelButton.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $form.Controls.Add($cancelButton)
    $form.CancelButton = $cancelButton
    try {
        $saved = $false
        do {
            $form.DialogResult = [System.Windows.Forms.DialogResult]::None
            $result = $form.ShowDialog()
            if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
                Write-Output 'CAPTURA_CANCELADA_SIN_ENVIO'
                break
            }
            $appPassword = $passwordBox.Text -replace '\s', ''
            if ($appPassword -notmatch '^[A-Za-z0-9]{16}$') {
                [void][System.Windows.Forms.MessageBox]::Show('Introduzca la contrasena de aplicacion de Google de 16 caracteres.', 'Revise la clave')
                $appPassword = $null
                continue
            }
            try {
                # Guardar fuera del callback WinForms mantiene el ambito del script.
                $securePassword = ConvertTo-SecureString $appPassword -AsPlainText -Force
                Save-LocalSmtpCredential -Account $smtpAccount -Password $securePassword -Path $credentialPath
                $saved = $true
                Write-Output 'CREDENCIAL_SMTP_GUARDADA_CIFRADA'
            }
            catch {
                # Solo codigos de diagnostico: nunca mensaje, variables o clave.
                Write-Output ('ERROR_GUARDADO_TIPO=' + $_.Exception.GetType().FullName)
                Write-Output ('ERROR_GUARDADO_ID=' + $_.FullyQualifiedErrorId)
                [void][System.Windows.Forms.MessageBox]::Show('No se pudo guardar el archivo cifrado. El diagnostico no contiene la clave; avisa en el chat para revisarlo.', 'Error al guardar')
            }
            finally {
                $appPassword = $null
                $securePassword = $null
            }
        } while (-not $saved)
    }
    finally {
        $passwordBox.Clear()
        $form.Dispose()
    }
    return
}

if (-not (Test-Path -LiteralPath $credentialPath -PathType Leaf)) {
    throw 'Falta la credencial SMTP. Ejecute este script con -Accion Guardar para introducirla en la ventana privada.'
}
$credential = Import-Clixml -LiteralPath $credentialPath
if ($credential -isnot [System.Management.Automation.PSCredential] -or $credential.UserName -ne $smtpAccount) {
    throw 'La credencial no pertenece a la cuenta principal configurada.'
}
$pythonPath = Join-Path $projectDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    $pythonPath = Join-Path $projectDirectory 'venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'No se encontro el entorno virtual Python del proyecto.'
}
$smtpEnvironment = @{
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = 'smtp.gmail.com'
    EMAIL_PORT = '587'
    EMAIL_USE_TLS = 'true'
    EMAIL_USE_SSL = 'false'
    EMAIL_TIMEOUT = '20'
    EMAIL_HOST_USER = $smtpAccount
    EMAIL_HOST_PASSWORD = $credential.GetNetworkCredential().Password
    DEFAULT_FROM_EMAIL = 'DV Solutions <oasis@dvsolutionshn.com>'
}
$previousEnvironment = @{}
foreach ($name in $smtpEnvironment.Keys) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    foreach ($name in $smtpEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $smtpEnvironment[$name], 'Process')
    }
    $managePath = Join-Path $projectDirectory 'manage.py'
    switch ($Accion) {
        'Comprobar' { & $pythonPath $managePath comprobar_smtp }
        'EnviarPrueba' { & $pythonPath $managePath comprobar_smtp --to 'daniel.varela@dvsolutionshn.com' }
    }
    $smtpExitCode = $LASTEXITCODE
}
finally {
    foreach ($name in $smtpEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
    $smtpEnvironment['EMAIL_HOST_PASSWORD'] = $null
    $credential = $null
}
if ($smtpExitCode -ne 0) {
    throw 'La operacion SMTP no se completo. Revise el diagnostico anterior.'
}
