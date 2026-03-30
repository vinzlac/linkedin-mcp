#!/usr/bin/env python3
"""Affiche les tools exposés par le serveur MCP local.

Modes:
- défaut: rendu lisible (nom, paramètres, description)
- --short: résumé compact
- --json: sortie JSON structurée
"""

from __future__ import annotations

import json
import argparse
import shutil
import subprocess
import sys
import textwrap
from typing import Any


INIT_MSG = {
    "jsonrpc": "2.0",
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "pretty-tools-cli", "version": "1.0.0"},
    },
    "id": 1,
}

TOOLS_MSG = {
    "jsonrpc": "2.0",
    "method": "tools/list",
    "params": {},
    "id": 2,
}


def _run_tools_list() -> list[dict[str, Any]]:
    payload = f"{json.dumps(INIT_MSG)}\n{json.dumps(TOOLS_MSG)}\n"

    process = subprocess.run(
        ["uv", "run", "python", "-c", "from linkedin_mcp.server import main; main()"],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )

    if process.returncode != 0:
        raise RuntimeError(
            "Le serveur MCP a échoué.\n"
            f"stderr:\n{process.stderr.strip()}\n\nstdout:\n{process.stdout.strip()}"
        )

    responses: list[dict[str, Any]] = []
    for line in process.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and message.get("jsonrpc") == "2.0":
            responses.append(message)

    tools_response = next((r for r in responses if r.get("id") == 2), None)
    if not tools_response:
        raise RuntimeError(
            "Réponse tools/list introuvable. "
            "Vérifie que le serveur démarre correctement."
        )

    tools = tools_response.get("result", {}).get("tools", [])
    if not isinstance(tools, list):
        raise RuntimeError("Format inattendu pour result.tools")
    return tools


def _fmt_list(items: list[str]) -> str:
    if not items:
        return "-"
    return ", ".join(items)


def _extract_params(tool: dict[str, Any]) -> tuple[list[str], list[str]]:
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []

    required_params = [name for name in required if name in props]
    optional_params = [name for name in props.keys() if name not in required]
    return required_params, optional_params


def _normalize_tool(tool: dict[str, Any]) -> dict[str, Any]:
    required_params, optional_params = _extract_params(tool)
    return {
        "name": tool.get("name", ""),
        "description": (tool.get("description") or "").strip(),
        "required_params": required_params,
        "optional_params": optional_params,
        "input_schema": tool.get("inputSchema") or {},
        "output_schema": tool.get("outputSchema") or {},
    }


def _print_tools(tools: list[dict[str, Any]]) -> None:
    width = max(90, min((shutil.get_terminal_size((120, 40)).columns - 2), 140))
    separator = "─" * width

    print("\nTOOLS MCP DISPONIBLES")
    print(separator)
    print(f"Total: {len(tools)}\n")

    for index, tool in enumerate(tools, start=1):
        name = tool.get("name", "<sans nom>")
        description = (tool.get("description") or "").strip() or "Sans description"
        required_params, optional_params = _extract_params(tool)

        print(f"{index}. {name}")
        print(f"   Requis    : {_fmt_list(required_params)}")
        print(f"   Optionnels: {_fmt_list(optional_params)}")
        print("   Description:")

        for paragraph in description.splitlines():
            paragraph = paragraph.rstrip()
            if not paragraph:
                print("   ")
                continue
            wrapped = textwrap.fill(
                paragraph,
                width=width - 6,
                initial_indent="      ",
                subsequent_indent="      ",
            )
            print(wrapped)

        print(separator)


def _print_tools_short(tools: list[dict[str, Any]]) -> None:
    print("TOOLS MCP (mode court)")
    print("======================")
    for tool in tools:
        name = tool.get("name", "<sans nom>")
        required_params, optional_params = _extract_params(tool)
        req = _fmt_list(required_params)
        opt = _fmt_list(optional_params)
        print(f"- {name} | requis: {req} | optionnels: {opt}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Affiche les tools MCP exposés par le serveur local "
            "avec un format lisible."
        )
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Affiche une sortie JSON structurée.",
    )
    parser.add_argument(
        "--short",
        action="store_true",
        help="Affiche une sortie compacte (une ligne par tool).",
    )
    return parser.parse_args()


def main() -> int:
    try:
        args = _parse_args()
        tools = _run_tools_list()

        if args.json:
            data = {
                "count": len(tools),
                "tools": [_normalize_tool(tool) for tool in tools],
            }
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0

        if args.short:
            _print_tools_short(tools)
            return 0

        _print_tools(tools)
        return 0
    except Exception as exc:  # pragma: no cover - script utilitaire
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
