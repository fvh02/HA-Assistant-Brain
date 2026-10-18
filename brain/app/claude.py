"""Ask Claude for structured JSON: through the Inkbox Claude Gateway's bridge, or with an own token."""

import json
import logging
import os
import re
import subprocess
import urllib.error
import urllib.request

log = logging.getLogger("claude")

CLAUDE = os.environ.get("BRAIN_CLAUDE", "claude")
TIMEOUT = 900
BRIDGE_KEY = os.environ.get("BRAIN_BRIDGE_KEY_FILE", "/share/claude-bridge/key")
LOGIN_HINT = ("Claude ist nicht erreichbar. Installiere und starte das Inkbox Claude Gateway (ab 0.12.0) – das "
              "Gedächtnis fragt Claude dann darüber – oder trage in den Optionen einen eigenen Claude-Token ein.")


class ClaudeError(Exception):
    def __init__(self, message: str, temporary: bool = False):
        super().__init__(message)
        self.temporary = temporary  # Claude unreachable: not the info's fault, it waits without counting a try


def source(bridge_url: str) -> str:
    """Where Claude is asked: own token, the gateway's bridge, or nowhere."""
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "token"
    if bridge_url and os.path.exists(BRIDGE_KEY):
        return "gateway"
    return "missing"


def _login_error(text: str) -> bool:
    low = text.lower()
    return "not logged in" in low or "/login" in low or "invalid api key" in low or "oauth" in low and "expired" in low


def _run_local(prompt: str, schema: dict, model: str) -> str:
    cmd = [CLAUDE, "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(schema), "--max-turns", "6"]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, cwd="/tmp", stdin=subprocess.DEVNULL)
    except FileNotFoundError as err:
        raise ClaudeError("Claude Code ist nicht installiert") from err
    except subprocess.TimeoutExpired as err:
        raise ClaudeError("Claude hat zu lange gebraucht") from err
    if proc.returncode != 0 and not proc.stdout.strip():
        err = (proc.stderr or "").strip()
        raise ClaudeError(LOGIN_HINT if _login_error(err) else f"Claude-Fehler: {err[-300:]}")
    return proc.stdout


def _run_bridge(bridge_url: str, prompt: str, schema: dict, model: str) -> str:
    try:
        key = open(BRIDGE_KEY).read().strip()
    except OSError as err:
        raise ClaudeError(LOGIN_HINT, temporary=True) from err
    body = json.dumps({"prompt": prompt, "schema": schema, "max_turns": 6, "model": model}).encode()
    req = urllib.request.Request(bridge_url.rstrip("/") + "/ask", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Bridge-Key": key})
    try:
        # The gateway is inside the add-on network: never through a proxy.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=TIMEOUT + 60) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as err:
        if err.code == 403:
            raise ClaudeError("Das Gateway lehnt das Gedächtnis ab – starte erst das Gateway und dann das Gedächtnis neu",
                              temporary=True) from err
        text = err.read().decode(errors="replace")
        try:
            text = json.loads(text).get("error") or text
        except ValueError:
            pass
        raise ClaudeError(LOGIN_HINT if _login_error(text) else f"Claude-Fehler über das Gateway: {text[-300:]}") from err
    except (urllib.error.URLError, OSError) as err:
        raise ClaudeError(f"Inkbox Claude Gateway nicht erreichbar ({bridge_url}) – läuft es (ab 0.12.0)?", temporary=True) from err


def ask(prompt: str, schema: dict, model: str = "", bridge_url: str = "") -> dict:
    """Ask Claude without any tools and return the JSON object that matches the schema."""
    where = source(bridge_url)
    if where == "missing":
        raise ClaudeError(LOGIN_HINT, temporary=True)
    out = _run_bridge(bridge_url, prompt, schema, model) if where == "gateway" else _run_local(prompt, schema, model)
    try:
        envelope = json.loads(out)
    except ValueError as err:
        raise ClaudeError("Antwort von Claude nicht lesbar") from err
    if envelope.get("is_error"):
        text = str(envelope.get("result"))
        raise ClaudeError(LOGIN_HINT if _login_error(text) else f"Claude-Fehler: {text[:300]}")
    result = envelope.get("structured_output")
    if not isinstance(result, dict):
        match = re.search(r"\{.*\}", str(envelope.get("result") or ""), re.S)
        if not match:
            raise ClaudeError("Claude hat kein Ergebnis geliefert")
        try:
            result = json.loads(match.group(0))
        except ValueError as err:
            raise ClaudeError("Antwort von Claude nicht lesbar") from err
    if envelope.get("total_cost_usd"):
        log.info("Claude: %.4f $", envelope["total_cost_usd"])
    return result
