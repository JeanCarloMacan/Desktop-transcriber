# Crea (o quita, con -Quitar) el acceso directo que arranca el transcriptor al iniciar sesión en Windows.
# El acceso directo lanza el overlay sin consola (pythonw); el overlay arranca voice-core oculto si no está corriendo.
#
#   powershell -ExecutionPolicy Bypass -File scripts\instalar_inicio.ps1          # instalar
#   powershell -ExecutionPolicy Bypass -File scripts\instalar_inicio.ps1 -Quitar  # quitar
param([switch]$Quitar)

$raiz = Split-Path -Parent $PSScriptRoot
$acceso = Join-Path ([Environment]::GetFolderPath('Startup')) 'Transcriptor RT.lnk'

if ($Quitar) {
    if (Test-Path $acceso) { Remove-Item $acceso; "Quitado: $acceso" } else { "No estaba instalado: $acceso" }
    exit 0
}

$pythonw = Join-Path $raiz '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "No existe $pythonw (¿falta 'uv sync'?)" }

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($acceso)
$lnk.TargetPath = $pythonw
$lnk.Arguments = '-m overlay'
$lnk.WorkingDirectory = $raiz
$lnk.Description = 'Transcriptor RT: dictado en vivo (Ctrl+Alt+D dictar, Ctrl+Alt+O panel, Ctrl+Alt+V pegar)'
$lnk.WindowStyle = 7  # minimizado (pythonw no abre ventana de todos modos)
$lnk.Save()
"Creado: $acceso"
"Arranca: $pythonw -m overlay (en $raiz)"
