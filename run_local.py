#!/usr/bin/env python3
"""Local convenience runner.

Reads credentials from an OpenCode config file so the suites can be run
without re-exporting keys by hand. Optional: everyone else can export
environment variables and call the scripts directly.

Usage:
    python3 run_local.py verify     # run kpa_verify.py
    python3 run_local.py deep       # run kpa_deep_probe.py

Override the config location with OPENCODE_CONFIG.
"""

import json
import os
import runpy
import sys

HOME = os.path.expanduser("~")
DEFAULT_CONFIG = os.path.join(HOME, ".config/opencode/opencode.json")


def read_secret(reference):
    """Resolve an OpenCode {file:...} secret reference."""
    if reference.startswith("{file:"):
        path = reference[len("{file:"):].rstrip("}").strip()
        if not path.startswith("/") and not path.startswith("~"):
            path = os.path.join(HOME, path)
        with open(os.path.expanduser(path)) as handle:
            return handle.read().strip()
    return reference


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "verify"
    config_path = os.environ.get("OPENCODE_CONFIG", DEFAULT_CONFIG)
    with open(config_path) as handle:
        config = json.load(handle)

    providers = config.get("providers", config.get("provider", {}))
    provider = providers["kpa"]
    models = provider.get("models", {})

    claude_model = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
    gpt_model = os.environ.get("GPT_MODEL", "gpt-5-6-sol")

    claude_settings = models[claude_model].get("settings", {})
    gpt_settings = models[gpt_model].get("settings", {})
    provider_settings = provider.get("settings", {})

    os.environ["KPA_CLAUDE_URL"] = claude_settings["baseURL"]
    os.environ["KPA_CLAUDE_KEY"] = read_secret(claude_settings["apiKey"])
    os.environ["CLAUDE_MODEL"] = claude_model
    os.environ.setdefault("CLAUDE_AUTH", "x-api-key")

    os.environ["KPA_GPT_URL"] = gpt_settings.get(
        "baseURL", provider_settings.get("baseURL", ""))
    os.environ["KPA_GPT_KEY"] = read_secret(gpt_settings["apiKey"])
    os.environ["GPT_MODEL"] = gpt_model

    os.environ.setdefault("ONLY", "both")
    os.environ.setdefault("REPEATS", "3")
    os.environ.setdefault("NEEDLE_SENT", "2500")
    os.environ.setdefault("ROUNDS", "20")
    os.environ.setdefault("INTERVAL", "2")

    script = "kpa_verify.py" if mode == "verify" else "kpa_deep_probe.py"
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)
    sys.argv = [script]
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
