#!/usr/bin/env bash
# Vibe Remote Installation Script
# Usage: curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash
#
# Prerequisites: None! uv will be installed automatically and manages Python for you.

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
VIBE_BIN_PATH=""
VIBE_TOOL_BIN_DIR=""
ORIGINAL_PATH="$PATH"

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

path_contains_dir() {
    local path_value="$1"
    local target_dir="$2"

    case ":$path_value:" in
        *":$target_dir:"*) return 0 ;;
        *) return 1 ;;
    esac
}

ensure_writable_dir() {
    local dir="$1"

    if [ -z "$dir" ]; then
        return 1
    fi

    if [ ! -d "$dir" ]; then
        mkdir -p "$dir" 2>/dev/null || return 1
    fi

    [ -d "$dir" ] && [ -w "$dir" ]
}

is_absolute_dir() {
    case "$1" in
        /*) return 0 ;;
        *) return 1 ;;
    esac
}

is_sbin_dir() {
    case "$1" in
        */sbin) return 0 ;;
        *) return 1 ;;
    esac
}

is_transient_bin_dir() {
    local dir="$1"

    case "$dir" in
        */.venv/bin|*/venv/bin|*/env/bin|*/.pyenv/shims|*/.pyenv/versions/*/bin|*/.local/share/mise/installs/*/bin|*/.mise/installs/*/bin)
            return 0
            ;;
    esac

    if [ -n "${VIRTUAL_ENV:-}" ] && [ "$dir" = "${VIRTUAL_ENV%/}/bin" ]; then
        return 0
    fi

    if [ -n "${CONDA_PREFIX:-}" ] && [ "$dir" = "${CONDA_PREFIX%/}/bin" ]; then
        return 0
    fi

    if [ -n "${PYENV_ROOT:-}" ]; then
        case "$dir" in
            "${PYENV_ROOT%/}"/shims) return 0 ;;
            "${PYENV_ROOT%/}"/versions/*/bin) return 0 ;;
        esac
    fi

    if [ -n "${MISE_DATA_DIR:-}" ]; then
        case "$dir" in
            "${MISE_DATA_DIR%/}"/installs/*/bin) return 0 ;;
        esac
    fi

    return 1
}

choose_tool_bin_dir() {
    local dir
    local fallback_sbin_dir=""

    local old_ifs="$IFS"
    IFS=":"
    for dir in $ORIGINAL_PATH; do
        if [ -n "$dir" ] && is_absolute_dir "$dir" && ! is_transient_bin_dir "$dir" && ensure_writable_dir "$dir"; then
            if is_sbin_dir "$dir"; then
                if [ -z "$fallback_sbin_dir" ]; then
                    fallback_sbin_dir="$dir"
                fi
                continue
            fi
            IFS="$old_ifs"
            echo "$dir"
            return 0
        fi
    done
    IFS="$old_ifs"

    local preferred_dirs=(
        "$HOME/.local/bin"
        "$HOME/bin"
        "/usr/local/bin"
        "/opt/homebrew/bin"
    )

    for dir in "${preferred_dirs[@]}"; do
        if is_absolute_dir "$dir" && ensure_writable_dir "$dir"; then
            echo "$dir"
            return 0
        fi
    done

    if [ -n "$fallback_sbin_dir" ]; then
        echo "$fallback_sbin_dir"
        return 0
    fi

    return 1
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

uv_tool_install() {
    if [ -n "$VIBE_TOOL_BIN_DIR" ]; then
        UV_TOOL_BIN_DIR="$VIBE_TOOL_BIN_DIR" uv tool install "$@"
    else
        uv tool install "$@"
    fi
}

install_package_candidate() {
    local package_spec="$1"
    shift

    if [ "$package_spec" = "$PACKAGE_NAME" ]; then
        uv_tool_install "$package_spec" --force --refresh "$@" 2>/dev/null
    else
        uv_tool_install "$package_spec" --force "$@" 2>/dev/null
    fi
}

resolve_vibe_on_original_path() {
    PATH="$ORIGINAL_PATH" command -v vibe 2>/dev/null || true
}

is_vibe_immediately_available() {
    local resolved_vibe

    if [ -z "$VIBE_BIN_PATH" ]; then
        return 1
    fi

    resolved_vibe="$(resolve_vibe_on_original_path)"
    [ -n "$resolved_vibe" ] && [ "$resolved_vibe" = "$VIBE_BIN_PATH" ]
}

# Install uv if not present
install_uv() {
    if command_exists uv; then
        success "uv is already installed"
        return 0
    fi
    
    info "Installing uv (will also manage Python automatically)..."
    
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

# Install vibe-remote using uv (uv auto-downloads Python if needed)
install_vibe() {
    info "Installing vibe-remote (Python will be downloaded automatically if needed)..."
    local install_package_spec="${VIBE_INSTALL_PACKAGE_SPEC:-}"

    VIBE_TOOL_BIN_DIR="$(choose_tool_bin_dir || true)"
    if [ -n "$VIBE_TOOL_BIN_DIR" ]; then
        info "Installing vibe command into $VIBE_TOOL_BIN_DIR"
    else
        warn "Could not find a writable directory in PATH; you may need a new shell before 'vibe' is available"
    fi

    if [ -n "$install_package_spec" ]; then
        if install_package_candidate "$install_package_spec"; then
            success "vibe-remote installed successfully (from custom package spec)"
            return 0
        fi

        error "Failed to install vibe-remote from custom package spec: $install_package_spec"
    fi
    
    # uv tool install will auto-download Python if not available
    # --force: reinstall even if already installed
    # --refresh: refresh package cache to get latest version
    # Try in order: PyPI -> China mirror (tsinghua) -> GitHub
    if install_package_candidate "$PACKAGE_NAME"; then
        success "vibe-remote installed successfully (from PyPI)"
    elif install_package_candidate "$PACKAGE_NAME" --index-url https://pypi.tuna.tsinghua.edu.cn/simple; then
        success "vibe-remote installed successfully (from Tsinghua mirror)"
    elif install_package_candidate "git+https://github.com/${REPO}.git"; then
        success "vibe-remote installed successfully (from GitHub)"
    else
        error "Failed to install vibe-remote from all sources"
    fi
}

# Verify installation
verify_installation() {
    info "Verifying installation..."
    
    # Refresh PATH
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if [ -n "$VIBE_TOOL_BIN_DIR" ]; then
        export PATH="$VIBE_TOOL_BIN_DIR:$PATH"
    fi
    
    if command_exists vibe; then
        VIBE_BIN_PATH="$(command -v vibe)"
        success "vibe command is available"
        echo ""
        "$VIBE_BIN_PATH" --help 2>/dev/null || true
        return 0
    fi
    
    # Check common install locations
    local vibe_locations=(
        "$HOME/.local/bin/vibe"
        "$HOME/.cargo/bin/vibe"
    )
    
    for loc in "${vibe_locations[@]}"; do
        if [ -f "$loc" ]; then
            VIBE_BIN_PATH="$loc"
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
    local vibe_dir
    vibe_dir="$(dirname "${VIBE_BIN_PATH:-$HOME/.local/bin/vibe}")"

    echo ""
    echo -e "${GREEN}Installation complete!${NC}"
    echo ""
    echo -e "${BLUE}Next steps:${NC}"
    if is_vibe_immediately_available; then
        echo "  1. Run 'vibe' to open the setup wizard"
        echo "  2. Choose your chat platform and agent backend"
        echo "  3. Enable a channel or DM and send your first task"
        echo "  4. Optional: run 'vibe remote' to open the Web UI from another device"
    else
        echo "  1. Run 'export PATH=\"${vibe_dir}:\$PATH\"' in your shell"
        echo "  2. Run 'vibe' to open the setup wizard"
        echo "  3. Choose your chat platform and agent backend"
        echo "  4. Enable a channel or DM and send your first task"
        echo "  5. Optional: run 'vibe remote' to open the Web UI from another device"
    fi
    echo ""
    echo -e "${BLUE}Quick commands:${NC}"
    echo "  vibe          - Start Vibe Remote (service + web UI)"
    echo "  vibe remote   - Set up remote Web UI access"
    echo "  vibe status   - Check service status"
    echo "  vibe stop     - Stop all services"
    echo "  vibe doctor   - Run diagnostics"
    echo ""
    echo -e "${BLUE}Uninstall:${NC}"
    echo "  uv tool uninstall vibe-remote    # if installed with uv"
    echo "  pip uninstall vibe-remote        # if installed with pip"
    echo "  rm -rf ~/.vibe_remote            # remove config and data"
    echo ""
    echo -e "${BLUE}If 'vibe' is still not found:${NC}"
    echo "  ${VIBE_BIN_PATH:-$HOME/.local/bin/vibe}"
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
    
    # Install uv (which manages Python automatically)
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
