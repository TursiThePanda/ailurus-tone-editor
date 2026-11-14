# Build the Windows app folder and its installer into dist\.
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# Needs the dev venv (.venv with pyinstaller; Python 3.12 - python-rtmidi has no newer
# Windows wheel) and Inno Setup 6 (ISCC.exe; winget installs it per user).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = if ($env:PY) { $env:PY } else { ".venv\Scripts\python.exe" }
$Version = & $Py -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
$Build = try { git rev-list --count HEAD } catch { "0" }
if (-not $Build) { $Build = "0" }

# Windows version resource (Properties -> Details): four numbers, the 4th = commit count
$Parts = ($Version.Split(".") + @("0", "0", "0"))[0..2] + @($Build)
$Tuple = ($Parts -join ", ")
New-Item -ItemType Directory -Force build | Out-Null
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($Tuple), prodvers=($Tuple)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'David Urban'),
    StringStruct('FileDescription', 'Ailurus Tone Editor'),
    StringStruct('FileVersion', '$Version.$Build'),
    StringStruct('ProductName', 'Ailurus Tone Editor'),
    StringStruct('ProductVersion', '$Version'),
    StringStruct('LegalCopyright', '(c) 2026 David Urban. PolyForm Noncommercial 1.0.0.'),
    StringStruct('OriginalFilename', 'Ailurus Tone Editor.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@ | Set-Content -Encoding UTF8 build\version_info.txt

Remove-Item -Recurse -Force "build\pyinstaller", "dist\Ailurus Tone Editor" -ErrorAction SilentlyContinue
& $Py -m PyInstaller --noconfirm --clean --workpath build\pyinstaller --distpath dist packaging\ailurus-tone.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Inno Setup 6 (ISCC.exe) not found" }
& $Iscc /Q "/DAppVersion=$Version" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

$Setup = "dist\Ailurus-Tone-Editor-$Version-windows-setup.exe"
$Hash = (Get-FileHash -Algorithm SHA256 $Setup).Hash.ToLower()
"$Hash  $(Split-Path -Leaf $Setup)" | Set-Content -Encoding ascii -NoNewline "$Setup.sha256"
"built: $Setup ($Version.$Build)"
"$Hash  $(Split-Path -Leaf $Setup)"
