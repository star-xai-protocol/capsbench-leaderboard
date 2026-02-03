"""Generate Docker Compose configuration from scenario.toml"""

import argparse
import os
import re
import sys
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


# --- 🛠️ SCRIPT DE REPARACIÓN (CLONACIÓN SEGURA) ---
# Este script se ejecuta DENTRO del contenedor.
# Crea un nuevo archivo 'green_agent_fixed.py' basado en el original.
FIX_SCRIPT = r"""
import sys
import os
import time
import glob

print("🔧 [FIX] Iniciando clonación y reparación del servidor...", flush=True)

# 1. Localizar archivo original
original_file = 'src/green_agent.py'
if not os.path.exists(original_file):
    original_file = 'green_agent.py'

if not os.path.exists(original_file):
    print(f"❌ Error: No encuentro {original_file}", flush=True)
    sys.exit(1)

print(f"📄 Leyendo original: {original_file}", flush=True)
with open(original_file, 'r') as f:
    content = f.read()

# 2. Desactivar la ruta antigua '/' comentando su decorador
# Esto evita que Flask intente registrar dos funciones para la misma ruta.
if "@app.route('/', methods=['POST', 'GET'])" in content:
    content = content.replace("@app.route('/', methods=['POST', 'GET'])", "# ROUTE_DISABLED_BY_FIX")
    print("✅ Ruta antigua desactivada.", flush=True)
elif '@app.route("/", methods=[\'POST\', \'GET\'])' in content:
    content = content.replace('@app.route("/", methods=[\'POST\', \'GET\'])', "# ROUTE_DISABLED_BY_FIX")
    print("✅ Ruta antigua desactivada (comillas dobles).", flush=True)
else:
    print("⚠️ No se encontró la ruta exacta para desactivar. Intentando regex...", flush=True)
    import re
    content = re.sub(r"@app\.route\s*\(\s*['\"]/['\"]\s*,", "# @app.route('/',", content)

# 3. Código de la NUEVA función (Bloqueo / Long Polling)
# Incluye imports necesarios dentro de la función por seguridad.
new_logic = r'''
# --- LOGICA INYECTADA POR FIX ---
from flask import jsonify
import time, glob, os

@app.route('/', methods=['POST', 'GET'])
def blocking_rpc():
    print("🔒 [BLOQUEO] Cliente conectado. Esperando fin de partida...", flush=True)
    start_time = time.time()
    
    while True:
        # Buscar archivos de resultados recientes
        patterns = ['results/*.json', 'src/results/*.json', 'replays/*.jsonl', 'src/replays/*.jsonl']
        files = []
        for p in patterns:
            files.extend(glob.glob(p))
            
        if files:
            files.sort(key=os.path.getmtime)
            last_file = files[-1]
            
            # Si el archivo tiene menos de 10 min, es la partida actual
            if (time.time() - os.path.getmtime(last_file)) < 600:
                filename = os.path.basename(last_file)
                print(f"✅ [FIN] Detectado: {filename}. Liberando cliente.", flush=True)
                
                # JSON PLANO (Flattened) para AgentBeats Client
                return jsonify({
                    "jsonrpc": "2.0", "id": 1,
                    "result": {
                        "contextId": "ctx", "taskId": "task", "id": "task",
                        "status": {"state": "completed"}, "final": True,
                        "messageId": "msg-done", "role": "assistant",
                        "parts": [{"text": "Game Finished", "mimeType": "text/plain"}]
                    }
                })
        
        if time.time() - start_time > 1200:
            return jsonify({"error": "timeout_waiting_results"})
            
        time.sleep(5)
# -------------------------------
'''

# 4. Inyectar la nueva lógica
# La insertamos justo después de crear la app Flask para asegurar que 'app' existe.
if "app = Flask(__name__)" in content:
    parts = content.split("app = Flask(__name__)")
    new_content = parts[0] + "app = Flask(__name__)\n" + new_logic + "\n" + parts[1]
else:
    # Fallback: Inyectar después de los imports (asumiendo que 'app' se crea por ahí)
    print("⚠️ No encontré 'app = Flask', inyectando al final de los imports...", flush=True)
    new_content = "from flask import Flask, jsonify\n" + content + "\n" + new_logic

# 5. Guardar en un NUEVO archivo
fixed_file = 'src/green_agent_fixed.py'
with open(fixed_file, 'w') as f:
    f.write(new_content)

print(f"✅ Archivo parcheado guardado en: {fixed_file}", flush=True)

# 6. Ejecutar el nuevo archivo
print("🚀 Arrancando servidor FIXED...", flush=True)
sys.stdout.flush()
# Pasamos los mismos argumentos que recibió el script original
os.execvp("python", ["python", "-u", fixed_file] + sys.argv[1:])
"""


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
    
    # 👇 FIX: Script Python embebido que clona y parchea el servidor.
    entrypoint:
      - python
      - -c
      - |
{fix_script_indented}

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

# 🟢 PARTICIPANTE
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

    # Indentamos el script para que encaje en el YAML
    fix_script_indented = "\n".join(["        " + line for line in FIX_SCRIPT.splitlines()])

    return COMPOSE_TEMPLATE.format(
        green_image=green["image"],
        green_port=DEFAULT_PORT,
        green_env=format_env_vars(green.get("env", {})),
        green_depends=" []",  
        participant_services=participant_services,
        client_depends=" []", 
        fix_script_indented=fix_script_indented
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

    print(f"Generated {COMPOSE_PATH} and {A2A_SCENARIO_PATH} (FINAL CLONING FIX)")

if __name__ == "__main__":
    main()
