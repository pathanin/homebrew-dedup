param(
    [string]$Version = 'latest',
    [string]$Repository = 'pathanin/homebrew-dedup',
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\dedup'),
    [switch]$Force,
    [switch]$NoPathUpdate
)

$ErrorActionPreference = 'Stop'
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

function Normalize-PathEntry {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }

    return $Path.Trim().Trim('"').TrimEnd('\', '/').ToLowerInvariant()
}

function Test-PathEntry {
    param(
        [string]$Path,
        [string[]]$Entries
    )

    $normalized = Normalize-PathEntry $Path
    foreach ($entry in $Entries) {
        if (Normalize-PathEntry $entry -eq $normalized) {
            return $true
        }
    }

    return $false
}

function Split-PathList {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return @()
    }

    return @($Value -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Resolve-Python {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & py -3 -c "import sys"
        if ($LASTEXITCODE -eq 0) {
            return @{ Exe = 'py'; Args = @('-3') }
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & python -c "import sys"
        if ($LASTEXITCODE -eq 0) {
            return @{ Exe = 'python'; Args = @() }
        }
    }

    throw 'Python was not found. Install Python 3 or the Python launcher, then run this script again.'
}

function Invoke-Python {
    param(
        [hashtable]$Python,
        [string[]]$Arguments,
        [string]$FailureMessage
    )

    $allArgs = @()
    if ($Python.Args) {
        $allArgs += $Python.Args
    }

    if ($Arguments) {
        $allArgs += $Arguments
    }

    & $Python.Exe @allArgs
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Set-UserPathEntry {
    param([string]$Path)

    try {
        $userPath = [System.Environment]::GetEnvironmentVariable('Path', 'User')
        $entries = Split-PathList $userPath
        if (-not (Test-PathEntry $Path $entries)) {
            if ([string]::IsNullOrWhiteSpace($userPath)) {
                $newUserPath = $Path
            } else {
                $newUserPath = ($userPath.TrimEnd(';') + ';' + $Path)
            }

            [System.Environment]::SetEnvironmentVariable('Path', $newUserPath, 'User')
            return $true
        }

        return $false
    } catch {
        throw "Failed to update the user PATH: $($_.Exception.Message)"
    }
}

function Set-SessionPathEntry {
    param([string]$Path)

    $entries = Split-PathList $env:Path
    if (-not (Test-PathEntry $Path $entries)) {
        if ([string]::IsNullOrWhiteSpace($env:Path)) {
            $env:Path = $Path
        } else {
            $env:Path = ($Path + ';' + $env:Path)
        }
    }
}

try {
    if ($Version -eq 'latest') {
        $latestUrl = "https://api.github.com/repos/$Repository/releases/latest"
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Headers @{ 'User-Agent' = 'dedup-installer'; 'Accept' = 'application/vnd.github+json' } -Uri $latestUrl
            $release = $response.Content | ConvertFrom-Json
            if (-not $release.tag_name) {
                throw 'The GitHub API response did not include a release tag.'
            }

            $Version = [string]$release.tag_name
        } catch {
            throw "Failed to resolve the latest release from $latestUrl. $($_.Exception.Message)"
        }
    }

    $releasePageUrl = "https://github.com/$Repository/releases/tag/$Version"
    $zipUrl = "https://github.com/$Repository/archive/refs/tags/$Version.zip"
    $tempZip = Join-Path ([System.IO.Path]::GetTempPath()) ("dedup-$([guid]::NewGuid().ToString('N')).zip")
    $python = Resolve-Python

    try {
        Invoke-Python -Python $python -Arguments @('-m', 'pip', '--version') -FailureMessage 'pip is not available for the resolved Python installation.'
    } catch {
        Invoke-Python -Python $python -Arguments @('-m', 'ensurepip', '--upgrade') -FailureMessage 'Failed to bootstrap pip with ensurepip.'
        Invoke-Python -Python $python -Arguments @('-m', 'pip', '--version') -FailureMessage 'pip is still not available after running ensurepip.'
    }

    Invoke-Python -Python $python -Arguments @('-m', 'pip', 'install', '--user', '--upgrade', 'send2trash') -FailureMessage 'Failed to install or upgrade send2trash with pip.'

    try {
        Invoke-WebRequest -UseBasicParsing -Headers @{ 'User-Agent' = 'dedup-installer' } -Uri $zipUrl -OutFile $tempZip
    } catch {
        throw "Failed to download $zipUrl. $($_.Exception.Message)"
    }

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

    if ((Test-Path (Join-Path $InstallDir 'dedup.py')) -or (Test-Path (Join-Path $InstallDir 'dedup.cmd'))) {
        if (-not $Force) {
            $reply = Read-Host "An installation already exists in $InstallDir. Overwrite it? [y/N]"
            if ($reply -notmatch '^(y|yes)$') {
                throw 'Installation cancelled.'
            }
        }
    }

    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem

        $archive = [System.IO.Compression.ZipFile]::OpenRead($tempZip)
        try {
            foreach ($fileName in @('dedup.py', 'dedup.cmd')) {
                $entry = $archive.Entries | Where-Object { $_.Name -ieq $fileName } | Select-Object -First 1
                if (-not $entry) {
                    throw "The archive does not contain $fileName."
                }

                $targetPath = Join-Path $InstallDir $fileName
                $entry.ExtractToFile($targetPath, $true)
            }
        } finally {
            $archive.Dispose()
        }
    } catch {
        throw "Failed to extract dedup files from $zipUrl. $($_.Exception.Message)"
    }

    $userPathUpdated = $false
    if (-not $NoPathUpdate) {
        $userPathUpdated = Set-UserPathEntry -Path $InstallDir
    }

    Set-SessionPathEntry -Path $InstallDir

    Write-Host "dedup was installed to: $InstallDir"
    if ($NoPathUpdate) {
        Write-Host 'User PATH was not modified because -NoPathUpdate was specified.'
    } elseif ($userPathUpdated) {
        Write-Host "Added $InstallDir to the user PATH."
    } else {
        Write-Host "The user PATH already included $InstallDir."
    }

    Write-Host 'The current PowerShell session PATH was updated.'
    Write-Host "Release page: $releasePageUrl"
    Write-Host 'Reopen other terminals to pick up the PATH change.'
} catch {
    Write-Error $_.Exception.Message
    exit 1
} finally {
    if (Test-Path $tempZip) {
        Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
    }
}
