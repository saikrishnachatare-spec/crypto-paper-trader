# Crypto Paper Trader — Windows x64

This is the Windows x64 console executable for the existing Laya-powered **paper trader**. It only reads unauthenticated public Coinbase candle data. All fills and portfolio state are simulated locally; it never requests exchange credentials, accesses private exchange endpoints, or submits real orders.

## Requirements and first-time setup

- 64-bit Windows 10 or 11.
- Python 3.12 x64 and the Python Launcher (`py`). If needed, install Python from [python.org](https://www.python.org/downloads/windows/) and select the launcher option.
- Internet access for setup, Coinbase public candles, and the first Laya inference. The first inference downloads the Laya model from Hugging Face into the normal user cache; model download time and disk use depend on the current model files and connection.

1. Download `CryptoPaperTrader-Windows-x64.zip` from the [GitHub Releases page](https://github.com/saikrishnachatare-spec/crypto-paper-trader/releases) and extract it to a folder where you have write permission.
2. Open PowerShell in that folder and install the local CPU-only Laya runtime:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-windows.ps1
   ```

   This creates `.venv` beside the executable and installs the versions pinned in `requirements-laya.txt`. It does not install or configure exchange software or credentials. Rerun the script to repair/update that local environment.

## Run

```powershell
# Preview public market data and signals; no fills and no state writes
.\CryptoPaperTrader.exe --dry-run --symbols BTC-USD

# Run one simulated paper-trading cycle (defaults to BTC-USD and ETH-USD)
.\CryptoPaperTrader.exe

# Keep running on five-minute intervals; stop with Ctrl-C
.\CryptoPaperTrader.exe --loop --interval 300

# Show all options
.\CryptoPaperTrader.exe --help
```

The executable discovers the Laya CLI installed by setup in the adjacent `.venv`. The normal local portfolio is stored at `%LOCALAPPDATA%\CryptoPaperTrader\portfolio.json`; it is not included in the release zip. Use `--state <path>` to choose another local JSON portfolio. Other supported controls include `--initial-cash`, `--trade-fraction`, `--max-position-fraction`, `--fee-bps`, and `--slippage-bps`.

`--dry-run` fetches public candles and requests Laya signals, but does not apply fills or write portfolio state. A normal run simulates fills and updates the local virtual portfolio only. Market/model errors fail closed. Laya confidence is an uncalibrated diagnostic and does not control trade authorization or size. This educational simulator is not financial advice or a production trading system.

## What's included and licenses

The executable packages this project's Python application with Python 3.12 and the PyInstaller bootloader. Relevant license texts and notices are in `licenses/` in the zip. PyInstaller's bootloader exception permits use of its compiled bootloader in this application. The repository does not declare a separate project license.

Laya, PyTorch, their dependencies, and the Laya checkpoint are deliberately **not bundled or redistributed**. Setup downloads the pinned Laya and CPU PyTorch packages from their configured package indexes; the model downloads from Hugging Face on first inference. The upstream Laya project and typed-decisions model identify Apache-2.0 licensing. Their terms and versions are provided by those upstream sources and may change independently of this executable.
