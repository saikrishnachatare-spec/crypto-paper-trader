"""Entry point used by the Windows frozen executable."""
from paper_trader.app import main


if __name__ == "__main__":
    raise SystemExit(main())
