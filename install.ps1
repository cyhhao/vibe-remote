# Vibe Remote Installation Script for Windows
# Usage: irm https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.ps1 | iex
#
# Prerequisites: None! uv will be installed automatically and manages Python for you.

$ErrorActionPreference = "Stop"

# Configuration
$REPO = "cyhhao/vibe-remote"
$PACKAGE_NAME = "vibe-remote"
$TSINGHUA_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"

function Write-Banner {
    Write-Host @"
 __     __ _  _             ____                       _       
 \ \   / /(_)| |__    ___  |  _ \  ___  _ __ ___   ___ | |_  ___ 
  \ \ / / | || '_ \  / _ \ | |_) |/ _ \| '_ `` _ \ / _ \| __|/ _ \
   \ V /  | || |_) ||  __/ |  _ <|  __/| | | | | | (_) | |_|  __/
    \_/   |_||_.__/  \___| |_| \_\\___||_| |_| |_|\___/ \__|\___|
"@ -ForegroundColor Blue
    Write-Host "Local-first agent runtime for Slack" -ForegroundColor Green
    Write-Host ""
}

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] " -ForegroundColor Blue -NoNewline
    Write-Host $Message
}

function Write-Success {
    param([string]$Message)
    Write-Host "[OK] " -ForegroundColor Green -NoNewline
    Write-Host $Message
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARN] " -ForegroundColor Yellow -NoNewline
    Write-Host $Message
}

function Write-Error {
    param([string]$Message)
    Write-Host "[ERROR] " -ForegroundColor Red -NoNewline
    Write-Host $Message
    exit 1
}

function Test-Command {
    param([string]$Command)
    $null = Get-Command $Command -ErrorAction SilentlyContinue
    return $?
}

function Install-Uv {
    if (Test-Command "uv") {
        Write-Success "uv is already installed"
        return
    }
    
    Write-Info "Installing uv (will also manage Python automatically)..."
    
    try {
        irm https://astral.sh/uv/install.ps1 | iex
        
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        
        if (Test-Command "uv") {
            Write-Success "uv installed successfully"
        } else {
            # Check common locations
            $uvPath = "$env:USERPROFILE\.local\bin\uv.exe"
            if (Test-Path $uvPath) {
                $env:Path += ";$env:USERPROFILE\.local\bin"
                Write-Success "uv installed successfully"
            } else {
                throw "uv not found after installation"
            }
        }
    } catch {
        Write-Error "Failed to install uv. Please install it manually: https://docs.astral.sh/uv/"
    }
}

function Invoke-NativeCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()

    try {
        $process = Start-Process -FilePath $FilePath `
            -ArgumentList $Arguments `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -ErrorAction Stop

        $stdout = if (Test-Path $stdoutPath) { [System.IO.File]::ReadAllText($stdoutPath) } else { "" }
        $stderr = if (Test-Path $stderrPath) { [System.IO.File]::ReadAllText($stderrPath) } else { "" }
        $capturedOutput = @()

        foreach ($streamOutput in @($stdout, $stderr)) {
            $trimmedOutput = $streamOutput.Trim()
            if ($trimmedOutput) {
                $capturedOutput += $trimmedOutput
            }
        }

        return @{
            Success = ($process.ExitCode -eq 0)
            ExitCode = $process.ExitCode
            Output = ($capturedOutput -join [System.Environment]::NewLine).Trim()
        }
    } catch {
        $capturedOutput = @()

        foreach ($path in @($stdoutPath, $stderrPath)) {
            if (Test-Path $path) {
                $streamOutput = [System.IO.File]::ReadAllText($path).Trim()
                if ($streamOutput) {
                    $capturedOutput += $streamOutput
                }
            }
        }

        $errorText = ($_ | Out-String).Trim()
        if ($errorText) {
            $capturedOutput += $errorText
        }

        return @{
            Success = $false
            ExitCode = 1
            Output = ($capturedOutput -join [System.Environment]::NewLine).Trim()
        }
    } finally {
        foreach ($path in @($stdoutPath, $stderrPath)) {
            if (Test-Path $path) {
                Remove-Item $path -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Invoke-UvToolInstallAttempt {
    param([string[]]$Arguments)

    return Invoke-NativeCommand -FilePath "uv" -Arguments (@("tool", "install") + $Arguments)
}

function Install-Vibe {
    Write-Info "Installing vibe-remote (Python will be downloaded automatically if needed)..."

    $customPackageSpec = $env:VIBE_INSTALL_PACKAGE_SPEC

    if ($customPackageSpec) {
        Write-Info "Trying custom package spec..."
        $result = Invoke-UvToolInstallAttempt -Arguments @($customPackageSpec, "--force")
        if ($result.Success) {
            Write-Success "vibe-remote installed successfully (from custom package spec)"
            return
        }

        $failureMessage = "Failed to install vibe-remote from custom package spec"
        if ($result.ExitCode -ne $null) {
            $failureMessage += " (exit code $($result.ExitCode))"
        }
        if ($result.Output) {
            $failureMessage += ":`n$($result.Output)"
        }

        Write-Error $failureMessage
    }

    $attempts = @(
        @{
            Name = "PyPI"
            Arguments = @($PACKAGE_NAME, "--force", "--refresh")
        },
        @{
            Name = "Tsinghua mirror"
            Arguments = @($PACKAGE_NAME, "--force", "--refresh", "--index-url", $TSINGHUA_INDEX_URL)
        },
        @{
            Name = "GitHub"
            Arguments = @("git+https://github.com/$REPO.git", "--force")
        }
    )
    $failures = @()

    foreach ($attempt in $attempts) {
        Write-Info "Trying $($attempt.Name)..."
        $result = Invoke-UvToolInstallAttempt -Arguments $attempt.Arguments
        if ($result.Success) {
            Write-Success "vibe-remote installed successfully (from $($attempt.Name))"
            return
        }

        $failureMessage = "- $($attempt.Name) failed"
        if ($result.ExitCode -ne $null) {
            $failureMessage += " (exit code $($result.ExitCode))"
        }

        if ($result.Output) {
            $failureMessage += ":`n$($result.Output)"
        }

        $failures += $failureMessage
    }

    Write-Error "Failed to install vibe-remote from all sources.`n$($failures -join "`n`n")"
}

function Test-Installation {
    Write-Info "Verifying installation..."
    
    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path += ";$env:USERPROFILE\.local\bin"
    
    if (Test-Command "vibe") {
        Write-Success "vibe command is available"
        Write-Host ""
        & vibe --help
        return $true
    }
    
    # Check common install locations
    $vibeLocations = @(
        "$env:USERPROFILE\.local\bin\vibe.exe"
    )
    
    foreach ($loc in $vibeLocations) {
        if (Test-Path $loc) {
            Write-Warning "vibe installed at $loc but not in PATH"
            Write-Host ""
            Write-Host "Add this directory to your PATH:" -ForegroundColor Yellow
            Write-Host "  $(Split-Path $loc)"
            Write-Host ""
            return $true
        }
    }
    
    Write-Error "Installation verification failed. vibe command not found."
}

function Write-NextSteps {
    Write-Host ""
    Write-Host "Installation complete!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Blue
    Write-Host "  1. Run 'vibe' to start the setup wizard"
    Write-Host "  2. Configure your Slack app tokens in the web UI"
    Write-Host "  3. Enable channels and start chatting with AI agents"
    Write-Host ""
    Write-Host "Quick commands:" -ForegroundColor Blue
    Write-Host "  vibe          - Start Vibe Remote (service + web UI)"
    Write-Host "  vibe status   - Check service status"
    Write-Host "  vibe stop     - Stop all services"
    Write-Host "  vibe doctor   - Run diagnostics"
    Write-Host ""
    Write-Host "Uninstall:" -ForegroundColor Blue
    Write-Host "  uv tool uninstall vibe-remote"
    Write-Host "  Remove-Item -Recurse ~\.vibe_remote  # remove config and data"
    Write-Host ""
    Write-Host "Documentation:" -ForegroundColor Blue
    Write-Host "  https://github.com/$REPO#readme"
    Write-Host ""
}

# Main installation flow
function Main {
    Write-Banner
    
    Write-Info "Detected OS: Windows"
    
    # Install uv (which manages Python automatically)
    Install-Uv
    
    # Install vibe-remote
    Install-Vibe
    
    # Verify
    Test-Installation
    
    # Done
    Write-NextSteps
}

# Run main
Main
