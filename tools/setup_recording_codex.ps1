param([string]$Archive)

$ErrorActionPreference = 'Stop'
$version = '0.166.0'
$sha256 = 'bf67d9662ed539f305849b74ad6420cc7b23aa2be07753710ca3db9134a0246d'
$name = 'codex-x86_64-pc-windows-msvc.exe'
$root = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'WinCro/gpt_runtime'))
$target = Join-Path $root $version
$stage = Join-Path $root ('staging-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
if (-not $Archive) {
    $Archive = Join-Path $stage 'codex.zip'
    Invoke-WebRequest "https://github.com/openai/codex/releases/download/rust-v$version/codex-x86_64-pc-windows-msvc.exe.zip" -OutFile $Archive
}
if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha256) {
    throw 'Codex archive integrity mismatch; installation refused'
}
$payload = Join-Path $stage 'payload'
Expand-Archive -LiteralPath $Archive -DestinationPath $payload
$binary = Join-Path $payload $name
if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) { throw 'Codex executable missing' }
if (Test-Path -LiteralPath $target) {
    throw "Installation already exists; preserved without overwriting: $target"
}
# Verify both paths before atomically promoting this private versioned directory.
$prefix = $root.TrimEnd('\') + '\'
foreach ($path in @($payload, $target, $stage)) {
    if (-not [IO.Path]::GetFullPath($path).StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Runtime installation path escaped its private directory'
    }
}
$before = (Get-FileHash -LiteralPath $binary).Hash
Move-Item -LiteralPath $payload -Destination $target
$installed = Join-Path $target $name
if ($before -ne (Get-FileHash -LiteralPath $installed).Hash) {
    throw 'Installed Codex integrity mismatch'
}
if ((Split-Path -Leaf $stage) -notmatch '^staging-[0-9a-f]{32}$') { throw 'Invalid staging path' }
Remove-Item -LiteralPath $stage -Recurse -Force
Write-Output $installed
Write-Output 'Select this executable in WinCro connection settings. Global Codex and authentication were not changed.'
