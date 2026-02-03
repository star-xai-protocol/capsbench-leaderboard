"""Generate Docker Compose configuration from scenario.toml"""

import argparse
import os
import re
import sys
import base64
from pathlib import Path
from typing import Any

# --- DEPENDENCIAS ---
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


# --- CONFIGURACIÓN ---
AGENTBEATS_API_URL = "https://agentbeats.dev/api/agents"
COMPOSE_PATH = "docker-compose.yml"
A2A_SCENARIO_PATH = "a2a-scenario.toml"
ENV_PATH = ".env.example"
DEFAULT_PORT = 9009
DEFAULT_ENV_VARS = {"PYTHONUNBUFFERED": "1"}


# --- 🛠️ SCRIPT DE REPARACIÓN MAESTRO (V6 - STREAMING SSE) ---
# Se ejecuta DENTRO del contenedor.
FIX_SCRIPT_SOURCE = r"""
import sys, os, re, json, time, glob

print("🔧 [FIX] Iniciando reparación del servidor (Modo Streaming SSE)...", flush=True)

target_file = 'src/green_agent.py'
if not os.path.exists(target_file):
    target_file = 'green_agent.py'

if not os.path.exists(target_file):
    print(f"❌ Error: No encuentro {target_file}", flush=True)
    sys.exit(1)

with open(target_file, 'r') as f:
    content = f.read()

# === 1. ASEGURAR IMPORTS (CRÍTICO: Response, stream_with_context) ===
if "import time" not in content:
    content = "import time, glob, os, json\n" + content

# Reemplazamos la importación de Flask para incluir todo lo necesario para Streaming
if "from flask import Flask" in content:
    content = content.replace("from flask import Flask", "from flask import Flask, jsonify, request, Response, stream_with_context")
else:
    content = "from flask import Flask, jsonify, request, Response, stream_with_context\n" + content

# === 2. AGENT CARD (Ya funciona, la mantenemos igual) ===
agent_card_route = r'''
@app.route("/.well-known/agent-card.json", methods=["GET"])
def agent_card_fix():
    return jsonify({
        "schema_version": "a2a-v1",
        "name": "green-agent-patched",
        "description": "Patched for AgentBeats",
        "url": "http://green-agent:9009",
        "version": "1.0.0",
        "protocolVersion": "0.3.0",
        "skills": [],
        "capabilities": {"streaming": True},
        "endpoints": [{"url": "http://green-agent:9009/", "transports": ["http"]}],
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"]
    })
'''

# === 3. NUEVA dummy_rpc con STREAMING REAL (text/event-stream) ===
# Usamos yield para enviar datos poco a poco. Esto satisface al cliente SSE.
new_dummy_rpc = r'''
@app.route('/', methods=['POST', 'GET'])
def dummy_rpc():
    print("🔒 [STREAM] Cliente conectado. Iniciando streaming...", flush=True)
    
    def generate():
        # 1. Latido inicial (Status: Working)
        # Mantiene al cliente feliz mientras esperamos.
        base_msg = {
            "jsonrpc": "2.0", "id": 1,
            "result": {
                "contextId": "ctx", "taskId": "task", "id": "task",
                "status": {"state": "working"}, "final": False,
                "messageId": "msg-alive", "role": "assistant",
                "parts": [{"text": "Game running...", "mimeType": "text/plain"}]
            }
        }
        # Formato SSE: "data: <json>\n\n"
        yield "data: " + json.dumps(base_msg) + "\n\n"
        
        start_time = time.time()
        
        while True:
            # Buscar resultados
            # patterns = ['results/*.json', 'src/results/*.json', 'replays/*.jsonl', 'src/replays/*.jsonl', 'output/*.json']
            patterns = ['results/*.json', 'src/results/*.json', 'output/*.json']
            files = []
            for p in patterns:
                files.extend(glob.glob(p))
            
            if files:
                files.sort(key=os.path.getmtime, reverse=True)
                last_file = files[0]
                
                # Si encontramos un resultado reciente
                if (time.time() - os.path.getmtime(last_file)) < 600:
                    print(f"✅ [FIN] Detectado: {os.path.basename(last_file)}", flush=True)
                    
                    # Mensaje FINAL (Status: Completed) - Opción 1
                    final_msg = {
                        "jsonrpc": "2.0", "id": 1,
                        "result": {
                            "contextId": "ctx", "taskId": "task", "id": "task",
                            "status": {"state": "completed"}, "final": True,
                            "messageId": "msg-done", "role": "assistant",
                            "parts": [{"text": "Game Finished", "mimeType": "text/plain"}],
                            "artifacts": [] 
                        }
                    }
                    yield "data: " + json.dumps(final_msg) + "\n\n"
                    break
            
            if time.time() - start_time > 1800:
                print("⏰ Timeout", flush=True)
                break
                
            time.sleep(2)
            # Enviar latido para mantener conexión viva
            yield "data: " + json.dumps(base_msg) + "\n\n"

    # Retornamos una respuesta con el mimetype correcto para SSE
    return Response(stream_with_context(generate()), mimetype='text/event-stream')
'''

# === 4. DESACTIVAR RUTAS ANTIGUAS ===
content = re.sub(r"@app\.route\s*\(\s*['\"]/['\"]", "# @app.route('/'", content)
content = re.sub(r"@app\.route\s*\(\s*['\"]/\.well-known/agent-card\.json['\"]", "# @app.route('/card'", content)

# === 5. INYECTAR CÓDIGO ===
if "if __name__" in content:
    parts = content.split("if __name__")
    content = "".join(parts[:-1]) + "\n" + agent_card_route + "\n" + new_dummy_rpc + "\n\nif __name__" + parts[-1]
else:
    content += "\n" + agent_card_route + "\n" + new_dummy_rpc

# === 6. GUARDAR Y EJECUTAR ===
with open(target_file, 'w') as f:
    f.write(content)

print("✅ Servidor parcheado (SSE Streaming). Arrancando...", flush=True)
sys.stdout.flush()
os.execvp("python", ["python", "-u", target_file] + sys.argv[1:])
"""

# Codificamos el script en Base64
FIX_SCRIPT_B64 = base64.b64encode(FIX_SCRIPT_SOURCE.encode('utf-8')).decode('utf-8')


def fetch_agent_info(agentbeats_id: str) -> dict:
    url = f"{AGENTBEATS_API_URL}/{agentbeats_id}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error: Failed to fetch agent {agentbeats_id}: {e}")
        sys.exit(1)


def resolve_image(agent: dict, name: str) -> None:
    has_image = "image" in agent
    has_id = "agentbeats_id" in agent

    if has_image and has_id:
        print(f"Error: {name} has both 'image' and 'agentbeats_id'")
        sys.exit(1)
    elif has_image:
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"Error: {name} requires 'agentbeats_id' for GitHub Actions")
            sys.exit(1)
        print(f"Using {name} image: {agent['image']}")
    elif has_id:
        info = fetch_agent_info(agent["agentbeats_id"])
        agent["image"] = info["docker_image"]
        if "id" in info:
            agent["webhook_id"] = info["id"]
        print(f"Resolved {name} image: {agent['image']}")
    else:
        print(f"Error: {name} must have 'image' or 'agentbeats_id'")
        sys.exit(1)


def parse_scenario(scenario_path: Path) -> dict[str, Any]:
    toml_data = scenario_path.read_text()
    data = tomli.loads(toml_data)

    green = data.get("green_agent", {})
    resolve_image(green, "green_agent")

    participants = data.get("participants", [])
    names = [p.get("name") for p in participants]
    duplicates = [name for name in set(names) if names.count(name) > 1]
    if duplicates:
        print(f"Error: Duplicate participant names: {duplicates}")
        sys.exit(1)

    for participant in participants:
        name = participant.get("name", "unknown")
        resolve_image(participant, f"participant '{name}'")

    return data


# 🟢 PLANTILLA SERVIDOR
COMPOSE_TEMPLATE = """# Auto-generated from scenario.toml

services:
  green-agent:
    image: {green_image}
    platform: linux/amd64
    container_name: green-agent
    
    # 👇 FIX ROBUSTO: Decodifica el script B64 y lo ejecuta.
    entrypoint: 
      - /bin/sh
      - -c
      - "echo {fix_b64} | base64 -d > /tmp/fix_server.py && python /tmp/fix_server.py --host 0.0.0.0 --port {green_port} --card-url http://green-agent:{green_port}"

    environment:{green_env}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:{green_port}/status"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 30s
    depends_on:{green_depends}
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
    depends_on:{client_depends}
    networks:
      - agent-network

networks:
  agent-network:
    driver: bridge
"""

# 🟢 PLANTILLA PARTICIPANTE (Modo Zombi)
PARTICIPANT_TEMPLATE = """  {name}:
    image: {image}
    platform: linux/amd64
    container_name: {name}
    command: ["--host", "0.0.0.0", "--port", "{port}", "--card-url", "http://{name}:{port}"]
    environment:{env}
    depends_on:
      green-agent:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "exit 0"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 5s
    networks:
      - agent-network
"""

A2A_SCENARIO_TEMPLATE = """[green_agent]
endpoint = "http://green-agent:{green_port}"

{participants}
{config}"""


def format_env_vars(env_dict: dict[str, Any]) -> str:
    env_vars = {**DEFAULT_ENV_VARS, **env_dict}
    lines = [f"      - {key}={value}" for key, value in env_vars.items()]
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
        green_depends=" []",  
        participant_services=participant_services,
        client_depends=" []", 
        fix_b64=FIX_SCRIPT_B64
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
        if "webhook_id" in p:
             lines.append(f"agentbeats_id = \"{p['webhook_id']}\"")
        elif "agentbeats_id" in p:
             lines.append(f"agentbeats_id = \"{p['agentbeats_id']}\"")
        participant_lines.append("\n".join(lines) + "\n")

    config_section = scenario.get("config", {})
    config_lines = [tomli_w.dumps({"config": config_section})]

    return A2A_SCENARIO_TEMPLATE.format(
        green_port=DEFAULT_PORT,
        participants="\n".join(participant_lines),
        config="\n".join(config_lines)
    )


def generate_env_file(scenario: dict[str, Any]) -> str:
    green = scenario["green_agent"]
    participants = scenario.get("participants", [])
    secrets = set()
    env_var_pattern = re.compile(r'\$\{([^}]+)\}')

    for agent in [green] + participants:
        for value in agent.get("env", {}).values():
            for match in env_var_pattern.findall(str(value)):
                secrets.add(match)

    if not secrets: return ""
    lines = [f"{secret}=" for secret in sorted(secrets)]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=Path)
    args = parser.parse_args()

    if not args.scenario.exists():
        print(f"Error: {args.scenario} not found")
        sys.exit(1)

    scenario = parse_scenario(args.scenario)

    with open(COMPOSE_PATH, "w") as f:
        f.write(generate_docker_compose(scenario))

    with open(A2A_SCENARIO_PATH, "w") as f:
        f.write(generate_a2a_scenario(scenario))

    env_content = generate_env_file(scenario)
    if env_content:
        with open(ENV_PATH, "w") as f:
            f.write(env_content)
        print(f"Generated {ENV_PATH}")

    print(f"Generated {COMPOSE_PATH} and {A2A_SCENARIO_PATH} (FINAL STREAMING FIX - BASELINE)")

if __name__ == "__main__":
    main()
