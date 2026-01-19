# Vibe Remote Installation Script for Windows
# Usage: irm https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.ps1 | iex

$ErrorActionPreference = "Stop"

# Configuration
$REPO = "cyhhao/vibe-remote"
$PACKAGE_NAME = "vibe-remote"
$MIN_PYTHON_VERSION = [Version]"3.9"

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

function Get-PythonVersion {
    param([string]$PythonCmd)
    try {
        $version = & $PythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        return [Version]$version
    } catch {
        return $null
    }
}

function Find-Python {
    $candidates = @("python", "python3", "py -3")
    
    foreach ($cmd in $candidates) {
        if (Test-Command $cmd.Split()[0]) {
            $version = Get-PythonVersion $cmd
            if ($version -and $version -ge $MIN_PYTHON_VERSION) {
                return $cmd
            }
        }
    }
    return $null
}

function Install-Uv {
    if (Test-Command "uv") {
        Write-Success "uv is already installed"
        return
    }
    
    Write-Info "Installing uv (Python package manager)..."
    
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

function Install-Vibe {
    Write-Info "Installing vibe-remote..."
    
    if (Test-Command "uv") {
        try {
            & uv tool install $PACKAGE_NAME --force 2>$null
        } catch {
            & uv tool install "git+https://github.com/$REPO.git" --force
        }
    } else {
        $pythonCmd = Find-Python
        if (-not $pythonCmd) {
            Write-Error "Python $MIN_PYTHON_VERSION+ is required but not found. Please install Python first."
        }
        
        Write-Info "Using $pythonCmd (uv not found, falling back to pip)"
        try {
            & $pythonCmd -m pip install --user $PACKAGE_NAME 2>$null
        } catch {
            & $pythonCmd -m pip install --user "git+https://github.com/$REPO.git"
        }
    }
    
    Write-Success "vibe-remote installed successfully"
}

function Test-Installation {
    Write-Info "Verifying installation..."
    
    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path += ";$env:USERPROFILE\.local\bin"
    
    if (Test-Command "vibe") {
        Write-Success "vibe command is available"
        Write-Host ""
        & vibe doctor
        return $true
    }
    
    # Check common install locations
    $vibeLocations = @(
        "$env:USERPROFILE\.local\bin\vibe.exe",
        "$env:APPDATA\Python\Scripts\vibe.exe"
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
    Write-Host "  uv tool uninstall vibe-remote    # if installed with uv"
    Write-Host "  pip uninstall vibe-remote        # if installed with pip"
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
    
    # Check Python
    $pythonCmd = Find-Python
    if ($pythonCmd) {
        $version = Get-PythonVersion $pythonCmd
        Write-Success "Found Python $version ($pythonCmd)"
    } else {
        Write-Warning "Python $MIN_PYTHON_VERSION+ not found, will try to use uv's managed Python"
    }
    
    # Install uv
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
