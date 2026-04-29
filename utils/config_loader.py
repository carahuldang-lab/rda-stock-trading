"""Load config.yaml + .env into a single config dict."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any

import yaml
from dotenv import load_dotenv


def load_config(config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """Load YAML config + environment variables.

    .env values override config.yaml where applicable.
    """
    # Load .env first
    load_dotenv()

    cfg_file = Path(config_path)
    if not cfg_file.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with open(cfg_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Env overrides
    if os.getenv("TRADING_MODE"):
        config["account"]["trading_mode"] = os.getenv("TRADING_MODE")
    if os.getenv("LOG_LEVEL"):
        config["logging"]["level"] = os.getenv("LOG_LEVEL")

    return config
