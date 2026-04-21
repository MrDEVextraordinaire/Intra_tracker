#!/bin/bash

python_sahla() {
    echo "[DEBUG] Starting python_sahla at $(date +'%H:%M:%S')"
    
    # 1. Define paths
    TARGET="/goinfre/$USER/python_stuff"
    VENV_BIN="$TARGET/venv/bin"
    SYS_PYTHON="/usr/bin/python3"
    export PLAYWRIGHT_BROWSERS_PATH="$TARGET/pw-browsers"
    
    echo "[DEBUG] TARGET path set to: $TARGET"
    echo "[DEBUG] VENV_BIN path set to: $VENV_BIN"

    RUN_INSTALL=false
    REQUIRED_LIBS="google-genai playwright pandas trafilatura openpyxl virtualenv"

    # 2. Check logic
    if [ "$1" = "reinstall" ]; then
        echo "[DEBUG] 'reinstall' flag detected. Forcing build."
        RUN_INSTALL=true
    elif [ ! -d "$TARGET/venv" ]; then
        echo "[DEBUG] No directory found at $TARGET/venv. Setting RUN_INSTALL=true."
        RUN_INSTALL=true
    else
        echo "[DEBUG] Valid environment found. Skipping installation steps."
    fi

    # 3. Build Environment
    if [ "$RUN_INSTALL" = true ]; then
        echo "[DEBUG] Entering Build Phase..."
        mkdir -p "$TARGET"
        
        if [ ! -d "$TARGET/venv" ] || [ ! -f "$VENV_BIN/python" ]; then
            echo "[DEBUG] Venv missing or broken. Executing: $SYS_PYTHON -m venv"
            $SYS_PYTHON -m venv "$TARGET/venv"
            
            if [ ! -x "$VENV_BIN/pip" ]; then
                echo "[DEBUG] ERR: Built-in venv pip not found. Trying virtualenv fallback."
                rm -rf "$TARGET/venv"
                $SYS_PYTHON -m pip install --user --upgrade virtualenv
                $SYS_PYTHON -m virtualenv "$TARGET/venv"
            fi
        fi

        echo "[DEBUG] Running Pip Updates..."
        "$VENV_BIN/pip" install --upgrade pip
        "$VENV_BIN/pip" install --upgrade $REQUIRED_LIBS
        
        echo "[DEBUG] Installing Playwright Chromium..."
        "$VENV_BIN/playwright" install chromium
        echo "[DEBUG] Build phase complete."
    fi

    # 5. Fix PATH
    echo "[DEBUG] Updating PATH..."
    export PATH="$VENV_BIN:$HOME/.local/bin:$PATH"
    echo "[DEBUG] Current Python: $(which python3)"

    # 6. VERIFICATION STEP
    echo "[DEBUG] Starting Library Verification..."
    ALL_INSTALLED=true

    # Special check for google-genai
    if ! "$VENV_BIN/python3" -c "import google.genai" &> /dev/null; then
         echo "[DEBUG] [FAIL] Library missing: google.genai"
         ALL_INSTALLED=false
    else
         echo "[DEBUG] [OK] google.genai found."
    fi

    # Check others
    for lib in playwright pandas trafilatura openpyxl; do
        if ! "$VENV_BIN/python3" -c "import $lib" &> /dev/null; then
            echo "[DEBUG] [FAIL] Library missing: $lib"
            ALL_INSTALLED=false
        else
            echo "[DEBUG] [OK] $lib found."
        fi
    done

    if [ "$ALL_INSTALLED" = true ]; then
        echo "[DEBUG] All libraries successfully verified."
    else
        echo "[DEBUG] WARNING: Verification failed for one or more components."
    fi

    # 7. Final actions
    cd ~/Downloads
    echo "[DEBUG] Moved to ~/Downloads. Setup complete."
}
python_sahla "$1"
