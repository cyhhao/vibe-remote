#!/usr/bin/env bash
# Vibe Remote Installation Script
# Usage: curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
REPO="cyhhao/vibe-remote"
PACKAGE_NAME="vibe-remote"
MIN_PYTHON_VERSION="3.9"

print_banner() {
    echo -e "${BLUE}"
    cat << 'EOF'
 __     __ _  _             ____                       _       
 \ \   / /(_)| |__    ___  |  _ \  ___  _ __ ___   ___ | |_  ___ 
  \ \ / / | || '_ \  / _ \ | |_) |/ _ \| '_ ` _ \ / _ \| __|/ _ \
   \ V /  | || |_) ||  __/ |  _ <|  __/| | | | | | (_) | |_|  __/
    \_/   |_||_.__/  \___| |_| \_\\___||_| |_| |_|\___/ \__|\___|
EOF
    echo -e "${NC}"
    echo -e "${GREEN}Local-first agent runtime for Slack${NC}"
    echo ""
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# Detect OS
detect_os() {
    case "$(uname -s)" in
        Linux*)     OS="linux";;
        Darwin*)    OS="macos";;
        CYGWIN*|MINGW*|MSYS*) OS="windows";;
        *)          OS="unknown";;
    esac
    echo "$OS"
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Version comparison: returns 0 if $1 >= $2
version_gte() {
    [ "$(printf '%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

# Get Python version
get_python_version() {
    local python_cmd="$1"
    $python_cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null
}

# Find suitable Python
find_python() {
    local python_candidates=("python3" "python" "python3.12" "python3.11" "python3.10" "python3.9")
    
    for cmd in "${python_candidates[@]}"; do
        if command_exists "$cmd"; then
            local version
            version=$(get_python_version "$cmd")
            if [ -n "$version" ] && version_gte "$version" "$MIN_PYTHON_VERSION"; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

# Install uv if not present
install_uv() {
    if command_exists uv; then
        success "uv is already installed"
        return 0
    fi
    
    info "Installing uv (Python package manager)..."
    
    local os
    os=$(detect_os)
    
    case "$os" in
        macos|linux)
            curl -LsSf https://astral.sh/uv/install.sh | sh
            # Add to PATH for current session
            export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
            ;;
        windows)
            powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
            ;;
        *)
            error "Unsupported operating system"
            ;;
    esac
    
    if command_exists uv; then
        success "uv installed successfully"
    else
        # Try to find it in common locations
        if [ -f "$HOME/.local/bin/uv" ]; then
            export PATH="$HOME/.local/bin:$PATH"
            success "uv installed successfully"
        elif [ -f "$HOME/.cargo/bin/uv" ]; then
            export PATH="$HOME/.cargo/bin:$PATH"
            success "uv installed successfully"
        else
            error "Failed to install uv. Please install it manually: https://docs.astral.sh/uv/"
        fi
    fi
}

# Install vibe-remote
install_vibe() {
    info "Installing vibe-remote..."
    
    if command_exists uv; then
        # Use uv tool install (recommended)
        uv tool install "$PACKAGE_NAME" --force 2>/dev/null || \
        uv tool install "git+https://github.com/${REPO}.git" --force
    else
        # Fallback to pip
        local python_cmd
        python_cmd=$(find_python)
        if [ -z "$python_cmd" ]; then
            error "Python ${MIN_PYTHON_VERSION}+ is required but not found. Please install Python first."
        fi
        
        info "Using $python_cmd (uv not found, falling back to pip)"
        $python_cmd -m pip install --user "$PACKAGE_NAME" 2>/dev/null || \
        $python_cmd -m pip install --user "git+https://github.com/${REPO}.git"
    fi
    
    success "vibe-remote installed successfully"
}

# Verify installation
verify_installation() {
    info "Verifying installation..."
    
    # Refresh PATH
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    
    if command_exists vibe; then
        success "vibe command is available"
        echo ""
        vibe --help 2>/dev/null || vibe doctor
        return 0
    fi
    
    # Check common install locations
    local vibe_locations=(
        "$HOME/.local/bin/vibe"
        "$HOME/.cargo/bin/vibe"
        "$(python3 -m site --user-base 2>/dev/null)/bin/vibe"
    )
    
    for loc in "${vibe_locations[@]}"; do
        if [ -f "$loc" ]; then
            warn "vibe installed at $loc but not in PATH"
            echo ""
            echo -e "${YELLOW}Add this to your shell config (.bashrc, .zshrc, etc.):${NC}"
            echo -e "  export PATH=\"$(dirname "$loc"):\$PATH\""
            echo ""
            return 0
        fi
    done
    
    error "Installation verification failed. vibe command not found."
}

# Print next steps
print_next_steps() {
    echo ""
    echo -e "${GREEN}Installation complete!${NC}"
    echo ""
    echo -e "${BLUE}Next steps:${NC}"
    echo "  1. Run 'vibe' to start the setup wizard"
    echo "  2. Configure your Slack app tokens in the web UI"
    echo "  3. Enable channels and start chatting with AI agents"
    echo ""
    echo -e "${BLUE}Quick commands:${NC}"
    echo "  vibe          - Start Vibe Remote (service + web UI)"
    echo "  vibe status   - Check service status"
    echo "  vibe stop     - Stop all services"
    echo "  vibe doctor   - Run diagnostics"
    echo ""
    echo -e "${BLUE}Uninstall:${NC}"
    echo "  uv tool uninstall vibe-remote    # if installed with uv"
    echo "  pip uninstall vibe-remote        # if installed with pip"
    echo "  rm -rf ~/.vibe_remote            # remove config and data"
    echo ""
    echo -e "${BLUE}Documentation:${NC}"
    echo "  https://github.com/${REPO}#readme"
    echo ""
}

# Main installation flow
main() {
    print_banner
    
    local os
    os=$(detect_os)
    info "Detected OS: $os"
    
    # Check Python
    local python_cmd
    python_cmd=$(find_python) || true
    if [ -n "$python_cmd" ]; then
        local version
        version=$(get_python_version "$python_cmd")
        success "Found Python $version ($python_cmd)"
    else
        warn "Python ${MIN_PYTHON_VERSION}+ not found, will try to use uv's managed Python"
    fi
    
    # Install uv
    install_uv
    
    # Install vibe-remote
    install_vibe
    
    # Verify
    verify_installation
    
    # Done
    print_next_steps
}

# Run main
main "$@"
