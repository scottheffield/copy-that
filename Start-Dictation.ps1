<#
.SYNOPSIS
    CopyThat - PowerShell launcher for local Windows dictation

.DESCRIPTION
    Runs CopyThat: local speech recognition with OpenVINO (Parakeet on GPU by default;
    Whisper models may use NPU or GPU). Hold Ctrl+Win to record; release to paste.
    Space locks recording, Escape cancels. Transcribed text is pasted into the active window.

.EXAMPLE
    # First-time setup (install deps, download exported models, detect NPU)
    .\Start-Dictation.ps1 -Setup

    # Normal usage
    .\Start-Dictation.ps1

    # Override settings
    .\Start-Dictation.ps1 -Device GPU -Model small -Language ru

    # Optional auto-submit (press Enter after dictation in the destination app)
    .\Start-Dictation.ps1 -AutoEnter

    # Download the larger cleanup candidate
    .\Start-Dictation.ps1 -Setup -CleanupModel 8b
#>

param(
    [switch]$Setup,
    [switch]$CLI,
    [ValidateSet("NPU", "GPU", "CPU")]
    [string]$Device,
    [ValidateSet("base", "small", "medium", "turbo", "parakeet")]
    [string]$Model,
    [string]$Language,
    [switch]$AutoEnter,
    [string]$Hotkey,
    [ValidateSet("8b", "4b", "1.7b")]
    [string]$CleanupModel = "4b",
    [string]$DataDir,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnginePath = Join-Path $ScriptDir "dictation_engine.py"
$AppPath = Join-Path $ScriptDir "app.py"
$VenvDir = Join-Path $ScriptDir '.venv'
if ($DataDir) { $env:COPYTHAT_DATA_DIR = [IO.Path]::GetFullPath($DataDir) }
elseif (-not $env:COPYTHAT_DATA_DIR) { $env:COPYTHAT_DATA_DIR = Join-Path $ScriptDir '.runtime' }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Banner {
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "  ║   CopyThat                           ║" -ForegroundColor Cyan
    Write-Host "  ║   Local dictation with OpenVINO      ║" -ForegroundColor Cyan
    Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
}

function Test-PythonAvailable {
    $pythonCmds = @("python", "python3", "py")
    foreach ($cmd in $pythonCmds) {
        try {
            $version = & $cmd --version 2>&1
            if ($version -match 'Python 3\.(1[0-9]|[2-9][0-9])') {
                return $cmd
            }
        } catch {}
    }
    return $null
}

function Get-PythonExe {
    # Use venv if it exists
    $venvPython = Join-Path (Join-Path $VenvDir "Scripts") "python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    
    # Otherwise find system Python
    $python = Test-PythonAvailable
    if (-not $python) {
        Write-Host "ERROR: Python 3.10+ is required." -ForegroundColor Red
        Write-Host "Install from: https://www.python.org/downloads/" -ForegroundColor Yellow
        Write-Host "Make sure to check 'Add Python to PATH' during installation." -ForegroundColor Yellow
        exit 1
    }
    return $python
}

# ---------------------------------------------------------------------------
# Check NPU driver
# ---------------------------------------------------------------------------
function Test-NPUDriver {
    Write-Host "Checking for Intel NPU..." -ForegroundColor Yellow
    
    # Check Windows Device Manager for NPU
    $npuDevice = Get-PnpDevice -FriendlyName "*NPU*" -ErrorAction SilentlyContinue
    if (-not $npuDevice) {
        $npuDevice = Get-PnpDevice -FriendlyName "*AI Boost*" -ErrorAction SilentlyContinue
    }
    if (-not $npuDevice) {
        # Sometimes listed as Neural Processing Unit
        $npuDevice = Get-PnpDevice -FriendlyName "*Neural*" -ErrorAction SilentlyContinue
    }

    if ($npuDevice) {
        Write-Host "  Found: $($npuDevice.FriendlyName)" -ForegroundColor Green
        Write-Host "  Status: $($npuDevice.Status)" -ForegroundColor Green
        
        # Check driver version
        $driver = Get-PnpDeviceProperty -InstanceId $npuDevice.InstanceId -KeyName "DEVPKEY_Device_DriverVersion" -ErrorAction SilentlyContinue
        if ($driver) {
            Write-Host "  Driver: $($driver.Data)" -ForegroundColor Green
        }
        return $true
    } else {
        Write-Host "  NPU device not found in Device Manager." -ForegroundColor Yellow
        Write-Host "  Only the Whisper models use the NPU; Parakeet, the default, does not need it." -ForegroundColor Yellow
        Write-Host "  If you expected one, this could mean:" -ForegroundColor Yellow
        Write-Host "    - Your CPU does not have an NPU" -ForegroundColor Yellow
        Write-Host "    - NPU driver needs to be installed via Windows Update" -ForegroundColor Yellow
        Write-Host "    - Or manually from Intel: https://www.intel.com/content/www/us/en/download/794734/" -ForegroundColor Yellow
        return $false
    }
}

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
function Invoke-Setup {
    Write-Banner

    # 1. Check Python
    Write-Host "Step 1: Checking Python..." -ForegroundColor Yellow
    $python = Test-PythonAvailable
    if (-not $python) {
        Write-Host "  Python 3.10+ not found!" -ForegroundColor Red
        Write-Host "  Install from: https://www.python.org/downloads/" -ForegroundColor Yellow
        exit 1
    }
    $version = & $python --version 2>&1
    Write-Host "  Found: $version" -ForegroundColor Green

    # 2. Create virtual environment
    Write-Host ""
    Write-Host "Step 2: Creating virtual environment..." -ForegroundColor Yellow
    if (-not (Test-Path $VenvDir)) {
        & $python -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment setup failed. Use a Python installation with ensurepip.' }
        Write-Host "  Created venv at: $VenvDir" -ForegroundColor Green
    } else {
        Write-Host "  Venv already exists at: $VenvDir" -ForegroundColor Green
    }
    
    $venvPython = Join-Path (Join-Path $VenvDir "Scripts") "python.exe"

    # 3. Check NPU: informational only. Parakeet, the default, runs on the GPU.
    Write-Host ""
    Write-Host "Step 3: Checking Intel NPU (optional)..." -ForegroundColor Yellow
    $null = Test-NPUDriver

    # 4. Run Python setup
    Write-Host ""
    Write-Host 'Step 4: Installing dependencies and downloading local models...' -ForegroundColor Yellow
    Write-Host "  This will take several minutes on first run." -ForegroundColor Yellow
    Write-Host ""
    
    & $venvPython -m pip install -r (Join-Path $ScriptDir 'requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    $setupArgs = @('--cleanup', $CleanupModel, '--configure')
    if ($Model) { $setupArgs += '--speech', $Model }
    & $venvPython (Join-Path $ScriptDir 'scripts\setup_models.py') @setupArgs
    if ($LASTEXITCODE -ne 0) { throw 'Model download failed.' }

    Write-Host ""
    Write-Host "Setup complete! Start dictating with:" -ForegroundColor Green
    Write-Host "  .\Start-Dictation.ps1" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Optional auto-submit (press Enter after paste):" -ForegroundColor Green
    Write-Host "  .\Start-Dictation.ps1 -AutoEnter" -ForegroundColor Cyan
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
function Start-Dictation {
    Write-Banner

    $python = Get-PythonExe

    # Build arguments
    $engineArgs = @()
    if ($Device)    { $engineArgs += "--device", $Device }
    if ($Model)     { $engineArgs += "--model", $Model }
    if ($Language)  { $engineArgs += "--language", $Language }
    if ($AutoEnter) { $engineArgs += "--auto-enter" }
    if ($Hotkey)    { $engineArgs += "--hotkey", $Hotkey }

    # Check if engine file exists
    if (-not (Test-Path $EnginePath)) {
        Write-Host "ERROR: dictation_engine.py not found at $EnginePath" -ForegroundColor Red
        Write-Host "Make sure both files are in the same directory." -ForegroundColor Yellow
        exit 1
    }

    # Check if model is set up
    $configFile = Join-Path $env:COPYTHAT_DATA_DIR "config.json"
    if (-not (Test-Path $configFile)) {
        throw 'First-time setup required. Run .\Start-Dictation.ps1 -Setup, then launch again.'
    }

    # Launch engine: GUI mode by default, -CLI for console-only mode
    if ($CLI) {
        & $python $EnginePath @engineArgs
    } else {
        & $python $AppPath @engineArgs
    }
    if ($LASTEXITCODE -ne 0) { throw 'Dictation exited with an error.' }
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if ($Help) {
    Get-Help $MyInvocation.MyCommand.Path -Detailed
    exit 0
}

if ($Setup) {
    Invoke-Setup
} else {
    Start-Dictation
}
