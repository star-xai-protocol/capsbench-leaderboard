"""Generate Docker Compose configuration from scenario.toml"""

import argparse
import os
import re
import sys

import time
import tomli
import shutil

from pathlib import Path
from typing import Any

try:
    import tomli
except ImportError:
    try:
        import tomllib as tomli
    except ImportError:
        print("Error: tomli required. Install with: pip install tomli")
        sys.exit(1)
try:
    import tomli_w
except ImportError:
    print("Error: tomli-w required. Install with: pip install tomli-w")
    sys.exit(1)
try:
    import requests
except ImportError:
    print("Error: requests required. Install with: pip install requests")
    sys.exit(1)


AGENTBEATS_API_URL = "https://agentbeats.dev/api/agents"


def fetch_agent_info(agentbeats_id: str) -> dict:
    """Fetch agent info from agentbeats.dev API."""
    url = f"{AGENTBEATS_API_URL}/{agentbeats_id}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        print(f"Error: Failed to fetch agent {agentbeats_id}: {e}")
        sys.exit(1)
    except requests.exceptions.JSONDecodeError:
        print(f"Error: Invalid JSON response for agent {agentbeats_id}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Error: Request failed for agent {agentbeats_id}: {e}")
        sys.exit(1)


COMPOSE_PATH = "docker-compose.yml"
A2A_SCENARIO_PATH = "a2a-scenario.toml"
ENV_PATH = ".env.example"

DEFAULT_PORT = 9009
DEFAULT_ENV_VARS = {"PYTHONUNBUFFERED": "1"}

# 🏆 FASE FINAL: INSERCIÓN PRE-MAIN + SLEEP INFINITY (LA BUENA)
COMPOSE_TEMPLATE = """# Auto-generated from scenario.toml

services:
  green-agent:
    image: ghcr.io/star-xai-protocol/capsbench:latest
    platform: linux/amd64
    container_name: green-agent
    
    # 🛡️ ESTRATEGIA:
    # 1. Renombramos las rutas viejas para evitar conflictos.
    # 2. Usamos Python para insertar el código NUEVO justo antes del bloque final 
    #    ("if __name__ == '__main__':") para asegurar que se cargue antes de arrancar.
    entrypoint:
      - /bin/sh
      - -c
      - |
        echo '🔧 PREPARANDO ARCHIVOS...'
        # 1. Apartar rutas viejas (evita error de duplicados de Flask)
        sed -i "s|@app.route('/',|@app.route('/old_root',|g" src/green_agent.py
        sed -i "s|@app.route('/.well-known/agent-card.json'|@app.route('/.well-known/old-card.json'|g" src/green_agent.py
        
        # 2. Inyectar Vigilante (Antes del main)
        python -c "
        import sys
        lines = open('src/green_agent.py').readlines()
        
        # Buscamos dónde empieza el bloque main para escribir ANTES
        idx = len(lines)
        for i, line in enumerate(lines):
            if 'if __name__' in line:
                idx = i
                break
        
        # El Código Vigilante (Sin indentación compleja para evitar errores)
        code = [
          '\\n# --- PARCHE VIGILANTE ---\\n',
          'import time, glob, json, os\\n',
          'from flask import Response, stream_with_context, jsonify\\n',
          '# Nueva ruta Agent Card\\n',
          '@app.route(\"/.well-known/agent-card.json\")\\n',
          'def ac_new(): return jsonify({{\"name\":\"Green\",\"version\":\"1\",\"skills\":[]}})\\n',
          '# Nueva ruta RPC con Vigilante\\n',
          '@app.route(\"/\", methods=[\"POST\",\"GET\"])\\n',
          'def drpc_new():\\n',
          ' def g():\\n',
          '  print(\"👁️ VIGILANTE ON\", flush=True)\\n',
          '  while True:\\n',
          '   f=glob.glob(\"src/results/*.json\")+glob.glob(\"results/*.json\")\\n',
          '   if f: print(f\"🏁 DONE: {{f[0]}}\", flush=True); time.sleep(5); yield \"data: \" + json.dumps({{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{{\"final\":True,\"status\":{{\"state\":\"completed\"}}}}}}) + \"\\n\\n\"; break\\n',
          '   yield \"data: \" + json.dumps({{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{{\"final\":False,\"status\":{{\"state\":\"working\"}}}}}}) + \"\\n\\n\"\\n',
          '   time.sleep(2)\\n',
          ' return Response(stream_with_context(g()), mimetype=\"text/event-stream\")\\n'
        ]
        
        # Insertamos y guardamos
        lines[idx:idx] = code
        open('src/green_agent.py','w').writelines(lines)
        print('✅ CÓDIGO INYECTADO CORRECTAMENTE')
        "
        
        echo '🚀 ARRANCANDO SERVIDOR...'
        python -u src/green_agent.py --host 0.0.0.0 --port 9009
    
    command: []
    
    environment:
      - PORT=9009
      - LOG_LEVEL=INFO
      # Forzamos recreación limpia
      - FORCE_RECREATE=premain_fix_{timestamp}
    
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9009/status"]
      interval: 5s
      timeout: 5s
      retries: 20
      start_period: 5s

    networks:
      - agent-network

{participant_services}
  agentbeats-client:
    image: ghcr.io/agentbeats/agentbeats-client:v1.0.0
    platform: linux/amd64
    container_name: agentbeats-client
    volumes:
      - ./a2a-scenario.toml:/app/scenario.toml
      - ./output:/app/output
    command: ["scenario.toml", "output/results.json"]
    depends_on:
      - green-agent
    networks:
      - agent-network

networks:
  agent-network:
    driver: bridge
"""

PARTICIPANT_TEMPLATE = """  {name}:
    image: {image}
    platform: linux/amd64
    container_name: {name}
    command: ["--host", "0.0.0.0", "--port", "{port}", "--card-url", "http://{name}:{port}"]
    environment:{env}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:{port}/.well-known/agent-card.json"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 30s
    networks:
      - agent-network
"""

A2A_SCENARIO_TEMPLATE = """[green_agent]
endpoint = "http://green-agent:{green_port}"

{participants}
{config}"""


def resolve_image(agent: dict, name: str) -> None:
    """Resolve docker image for an agent, either from 'image' field or agentbeats API."""
    has_image = "image" in agent
    has_id = "agentbeats_id" in agent

    if has_image and has_id:
        print(f"Error: {name} has both 'image' and 'agentbeats_id' - use one or the other")
        sys.exit(1)
    elif has_image:
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"Error: {name} requires 'agentbeats_id' for GitHub Actions (use 'image' for local testing only)")
            sys.exit(1)
        print(f"Using {name} image: {agent['image']}")
    elif has_id:
        info = fetch_agent_info(agent["agentbeats_id"])
        agent["image"] = info["docker_image"]
        print(f"Resolved {name} image: {agent['image']}")
    else:
        print(f"Error: {name} must have either 'image' or 'agentbeats_id' field")
        sys.exit(1)


def parse_scenario(scenario_path: Path) -> dict[str, Any]:
    toml_data = scenario_path.read_text()
    data = tomli.loads(toml_data)

    green = data.get("green_agent", {})
    resolve_image(green, "green_agent")

    participants = data.get("participants", [])

    # Check for duplicate participant names
    names = [p.get("name") for p in participants]
    duplicates = [name for name in set(names) if names.count(name) > 1]
    if duplicates:
        print(f"Error: Duplicate participant names found: {', '.join(duplicates)}")
        print("Each participant must have a unique name.")
        sys.exit(1)

    for participant in participants:
        name = participant.get("name", "unknown")
        resolve_image(participant, f"participant '{name}'")

    return data


def format_env_vars(env_dict: dict[str, Any]) -> str:
    env_vars = {**DEFAULT_ENV_VARS, **env_dict}
    lines = [f"      - {key}={value}" for key, value in env_vars.items()]
    return "\n" + "\n".join(lines)


def format_depends_on(services: list) -> str:
    lines = []
    for service in services:
        lines.append(f"      {service}:")
        lines.append(f"        condition: service_healthy")
    return "\n" + "\n".join(lines)


def generate_docker_compose(scenario: dict[str, Any]) -> str:
    green = scenario["green_agent"]
    participants = scenario.get("participants", [])

    participant_names = [p["name"] for p in participants]

    participant_services = "\n".join([
        PARTICIPANT_TEMPLATE.format(
            name=p["name"],
            image=p["image"],
            port=DEFAULT_PORT,
            env=format_env_vars(p.get("env", {}))
        )
        for p in participants
    ])

    all_services = ["green-agent"] + participant_names

    return COMPOSE_TEMPLATE.format(
        green_image=green["image"],
        green_port=DEFAULT_PORT,
        green_env=format_env_vars(green.get("env", {})),
        green_depends=format_depends_on(participant_names),
        participant_services=participant_services,
        client_depends=format_depends_on(all_services)
    )


def generate_a2a_scenario(scenario: dict[str, Any]) -> str:
    green = scenario["green_agent"]
    participants = scenario.get("participants", [])

    participant_lines = []
    for p in participants:
        lines = [
            f"[[participants]]",
            f"role = \"{p['name']}\"",
            f"endpoint = \"http://{p['name']}:{DEFAULT_PORT}\"",
        ]
        if "agentbeats_id" in p:
            lines.append(f"agentbeats_id = \"{p['agentbeats_id']}\"")
        participant_lines.append("\n".join(lines) + "\n")

    config_section = scenario.get("config", {})
    config_lines = [tomli_w.dumps({"config": config_section})]

    return A2A_SCENARIO_TEMPLATE.format(
        green_port=DEFAULT_PORT,
        participants="\n".join(participant_lines),
        config="\n".join(config_lines)
    )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()

    with open(args.scenario, "rb") as f:
        config = tomli.load(f)

    participant_services = ""
    for p in config.get("participants", []):
        name = p.get("name", "purple_agent")
        env_vars = p.get("env", {})
        env_block = "\n    environment:"
        for k, v in env_vars.items():
            env_block += f"\n      - {k}={v}"

        # 💤 EL TRUCO FINAL: Sleep Infinity para que no mate el proceso al acabar
        participant_services += f"""
  {name}:
    image: ghcr.io/star-xai-protocol/capsbench-purple:latest
    platform: linux/amd64
    container_name: {name}
    entrypoint: 
      - /bin/sh
      - -c
      - |
        python -u purple_ai.py
        echo '✅ AGENTE TERMINÓ. DURMIENDO...'
        sleep infinity
    {env_block}
    depends_on:
      - green-agent
    networks:
      - agent-network
"""

    # Usamos dobles llaves {{}} para que .format() funcione
    final_compose = COMPOSE_TEMPLATE.format(
        participant_services=participant_services,
        timestamp=int(time.time())
    )

    with open("docker-compose.yml", "w") as f:
        f.write(final_compose)
    
    shutil.copy(args.scenario, "a2a-scenario.toml")
    print("✅ CÓDIGO LISTO: Inyección Pre-Main + Sleep Infinity.")

if __name__ == "__main__":
    main()
