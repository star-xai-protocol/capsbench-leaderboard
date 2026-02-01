"""Generate Docker Compose configuration from scenario.toml"""

import argparse
import os
import re
import sys

import base64
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

# --- CÓDIGO VIGILANTE MEJORADO (LEE Y ENVÍA) ---
VIGILANTE_CODE = r"""
# ==========================================
# PARCHE VIGILANTE: LEER Y ENVIAR
# ==========================================
import time
import glob
import json
import os
from flask import Response, stream_with_context, jsonify

# 1. EVITAR ERROR 404
@app.route('/.well-known/agent-card.json')
def agent_card_patched():
    return jsonify({
        "name": "Green Agent Patched",
        "version": "1.0.0",
        "capabilities": {"streaming": True},
        "skills": [{"id": "eval", "name": "Eval"}]
    })

# 2. RPC CON ENVÍO DE DATOS REALES
@app.route('/', methods=['POST', 'GET'])
def dummy_rpc_patched():
    def generate():
        print("👁️ VIGILANTE: Esperando resultados...", flush=True)
        while True:
            # Buscamos el archivo de resultados
            found = glob.glob('src/results/*.json') + glob.glob('results/*.json')
            
            if found:
                file_path = found[0]
                print(f"🏁 FIN DETECTADO: {file_path}", flush=True)
                time.sleep(5) # Espera técnica para asegurar escritura
                
                # --- LEEMOS EL ARCHIVO PARA ENVIARLO ---
                try:
                    with open(file_path, 'r') as f:
                        file_content = f.read()
                except Exception as e:
                    file_content = "{}"
                    print(f"Error leyendo archivo: {e}", flush=True)

                # Construimos el artefacto
                artifact = {
                    "name": os.path.basename(file_path),
                    "content": file_content
                }

                # Enviamos 'completed' CON EL ARCHIVO DENTRO
                yield 'data: ' + json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1, 
                    "result": {
                        "final": True, 
                        "status": {"state": "completed"},
                        "artifacts": [artifact]
                    }
                }) + '\n\n'
                break
            
            # Si no ha terminado, enviamos heartbeat
            yield 'data: ' + json.dumps({
                "jsonrpc": "2.0", 
                "id": 1, 
                "result": {
                    "final": False, 
                    "status": {"state": "working"}
                }
            }) + '\n\n'
            time.sleep(2)
            
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

# 3. ARRANQUE
if __name__ == "__main__":
    print("🟢 SERVIDOR (CON DATOS) INICIANDO...", flush=True)
    app.run(host="0.0.0.0", port=9009)
"""

# CODIFICAMOS EN BASE64 PARA EVITAR ERRORES DE SINTAXIS EN EL SHELL
VIGILANTE_PAYLOAD = base64.b64encode(VIGILANTE_CODE.encode('utf-8')).decode('utf-8')


COMPOSE_TEMPLATE = """# Auto-generated from scenario.toml

services:
  green-agent:
    image: ghcr.io/star-xai-protocol/capsbench:latest
    platform: linux/amd64
    container_name: green-agent
    
    # 🛡️ ESTRATEGIA: INYECCIÓN VIA BASE64 (INFALIBLE)
    # Codificamos el parche en base64 para que pase intacto a través del shell.
    entrypoint:
      - /bin/sh
      - -c
      - |
        echo '🔧 PREPARANDO SERVIDOR...'
        # Renombrar rutas viejas
        sed -i "s|@app.route('/',|@app.route('/old_root',|g" src/green_agent.py
        sed -i "s|@app.route('/.well-known/agent-card.json'|@app.route('/.well-known/old-card.json'|g" src/green_agent.py
        
        # Quitar el app.run original
        python -c "lines = [l for l in open('src/green_agent.py') if 'app.run' not in l]; open('src/green_agent.py','w').writelines(lines)"
        
        # Añadir el código nuevo DECODIFICANDO BASE64
        # Esto evita cualquier error de comillas o saltos de línea
        echo "{vigilante_payload}" | base64 -d >> src/green_agent.py
        
        echo '🚀 ARRANCANDO...'
        python -u src/green_agent.py
    
    command: []
    
    environment:
      - PORT=9009
      - LOG_LEVEL=INFO
    
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

        # AQUÍ ESTÁ EL CAMBIO CLAVE:
        # Añadimos 'sleep infinity' para que el contenedor NO SE CIERRE al terminar la partida.
        # Así da tiempo a que se guarden y suban los archivos.
        participant_services += f"""
  {name}:
    image: ghcr.io/star-xai-protocol/capsbench-purple:latest
    platform: linux/amd64
    container_name: {name}
    entrypoint: ["/bin/sh", "-c", "python -u purple_ai.py; echo '✅ PARTIDA FINALIZADA. DURMIENDO...'; sleep infinity"]
    {env_block}
    depends_on:
      - green-agent
    networks:
      - agent-network
"""

    final_compose = COMPOSE_TEMPLATE.format(
        participant_services=participant_services,
        vigilante_payload=VIGILANTE_PAYLOAD,  # <--- ESTO ES LO IMPORTANTE
        timestamp=int(time.time())
    )

    with open("docker-compose.yml", "w") as f:
        f.write(final_compose)
    
    shutil.copy(args.scenario, "a2a-scenario.toml")
    print("✅ CÓDIGO ACTUALIZADO: Sintaxis YAML arreglada (Flattened Payload).")

if __name__ == "__main__":
    main()
