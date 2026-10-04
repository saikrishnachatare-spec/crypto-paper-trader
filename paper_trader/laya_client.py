"""Batched adapter for the preinstalled local Laya CLI."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Mapping

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LAYA = "/home/ubuntu/.local/share/laya/.venv/bin/laya"
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
QUESTIONS_FILE = RESOURCE_ROOT / "questions.json"


@dataclass(frozen=True)
class ModelDecision:
    action: str
    confidence: float | None


class LayaClient:
    def __init__(self, executable: str | None = None, timeout: float = 180.0):
        configured = executable or os.environ.get("LAYA_CLI")
        if not configured and getattr(sys, "frozen", False):
            bundled_environment = Path(sys.executable).resolve().parent / ".venv" / "Scripts" / "laya.exe"
            configured = str(bundled_environment) if bundled_environment.is_file() else "laya"
        elif not configured and sys.platform == "win32":
            configured = "laya"
        elif not configured:
            configured = DEFAULT_LAYA
        self.executable = shutil.which(configured) or configured
        self.timeout = timeout
        self._warned_uncalibrated = False

    def predict(self, prompts: Mapping[str, str]) -> dict[str, ModelDecision]:
        if not prompts:
            return {}
        if not Path(self.executable).is_file():
            raise RuntimeError(f"Laya CLI not found: {self.executable}; set LAYA_CLI to its path")
        batch = "\n".join(prompts[symbol].replace("\n", " ") for symbol in prompts) + "\n"
        command = [
            self.executable,
            "--batch", "-",
            "--questions", str(QUESTIONS_FILE),
            "--json",
            "--device", "cpu",
            "--task", "typed_decisions",
        ]
        try:
            completed = subprocess.run(
                command,
                input=batch,
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Laya inference failed to start or timed out: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Laya exited with status {completed.returncode}: {detail[-2000:]}")
        if "uncalibrated" in completed.stderr.lower() and not self._warned_uncalibrated:
            print(
                "Laya warning: this checkpoint reports uncalibrated confidence values; "
                "they are displayed for diagnostics only and are not used to authorize trades.",
                file=sys.stderr,
            )
            self._warned_uncalibrated = True
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if len(lines) != len(prompts):
            raise RuntimeError(f"Laya returned {len(lines)} result lines for {len(prompts)} assets")

        decisions: dict[str, ModelDecision] = {}
        for symbol, line in zip(prompts, lines):
            try:
                result = json.loads(line)
                answer = result["answers"]["action"]
                action = str(answer["choice"]).upper()
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise RuntimeError(f"Could not parse Laya response for {symbol}: {line[:400]}") from exc
            if action not in {"BUY", "SELL", "HOLD"}:
                raise RuntimeError(f"Laya returned an unsupported action for {symbol}: {action!r}")
            probabilities = answer.get("probabilities") or {}
            confidence = probabilities.get(action)
            try:
                confidence = float(confidence) if confidence is not None else None
            except (TypeError, ValueError):
                confidence = None
            decisions[symbol] = ModelDecision(action, confidence)
        return decisions


def make_prompt(symbol: str, indicators: Mapping[str, float | int], quantity: float, avg_entry: float) -> str:
    """Create one compact, one-line state for Laya; no credentials or account data."""
    packet = {
        "symbol": symbol,
        "market_indicators": dict(indicators),
        "paper_position_quantity": quantity,
        "paper_average_entry_price": avg_entry,
    }
    return "Choose a cautious simulated action from this numeric market snapshot: " + json.dumps(packet, separators=(",", ":"))
