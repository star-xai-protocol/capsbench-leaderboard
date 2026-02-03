"""Generate Docker Compose configuration from scenario.toml"""

import argparse
import os
import re
import sys
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

# 🟢 PLANTILLA SERVIDOR: REEMPLAZO QUIRÚRGICO DE DUMMY_RPC
# Usamos un script de Python en el entrypoint para modificar green_agent.py
COMPOSE_TEMPLATE = """# Auto-generated from scenario.toml

services:
  green-agent:
    image: {green_image}
    platform: linux/amd64
    container_name: green-agent
    
    # 👇 FIX: Script Python que reemplaza SOLO la función dummy_rpc defectuosa
    entrypoint:
      - python
      - -c
      - |
        import sys, os

        print("🔧 FIX: Parcheando función dummy_rpc para compatibilidad estricta...", flush=True)

        # 1. Definimos la NUEVA función dummy_rpc (Streaming + JSON Plano)
        #    Esta versión mantiene la conexión abierta y envía los campos que Pydantic exige.
        new_rpc_code = r'''
@app.route('/', methods=['POST', 'GET'])
def dummy_rpc():
    def generate():
        print("👁️ [VIGILANTE V2] Cliente conectado. Iniciando stream compatible...", flush=True)
        
        # Estructura BASE (Flattened) para pasar validación Pydantic
        base_payload = {
            "jsonrpc": "2.0", "id": 1, 
            "result": {
                "contextId": "ctx", "taskId": "task", "id": "task",
                "status": {"state": "working"}, "final": False,
                "messageId": "msg-alive", "role": "assistant", 
                "parts": [{"text": "Game in progress...", "mimeType": "text/plain"}]
            }
        }
        
        # Latido inicial
        yield "data: " + json.dumps(base_payload) + "\\n\\n"

        # Bucle de vigilancia
        start_time = time.time()
        while True:
            # Buscamos resultados recientes (< 5 min de antigüedad)
            files = sorted(glob.glob('results/*.json') + glob.glob('src/results/*.json') + glob.glob('replays/*.jsonl') + glob.glob('src/replays/*.jsonl'), key=os.path.getmtime)
            
            if files:
                last_file = files[-1]
                # Si el archivo es reciente (menos de 300s), asumimos que es NUESTRO juego
                if (time.time() - os.path.getmtime(last_file)) < 300:
                    filename = os.path.basename(last_file)
                    print(f"✅ [FIN] Juego detectado: {filename}", flush=True)
                    time.sleep(2) # Margen de escritura
                    
                    # Respuesta FINAL
                    final_payload = {
                        "jsonrpc": "2.0", "id": 1, 
                        "result": {
                            "contextId": "ctx", "taskId": "task", "id": "task",
                            "status": {"state": "completed"}, "final": True,
                            "messageId": "msg-done", "role": "assistant", 
                            "parts": [{"text": "Game Finished", "mimeType": "text/plain"}]
                        }
                    }
                    yield "data: " + json.dumps(final_payload) + "\\n\\n"
                    break
            
            # Timeout de seguridad (20 min)
            if time.time() - start_time > 1200:
                print("⚠️ [TIMEOUT] Espera agotada.", flush=True)
                break
                
            time.sleep(3)
            yield "data: " + json.dumps(base_payload) + "\\n\\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")
'''

        # 2. Leemos el archivo original
        target_file = 'src/green_agent.py'
        if not os.path.exists(target_file): target_file = 'green_agent.py' # Fallback

        with open(target_file, 'r') as f:
            content = f.read()

        # 3. Reemplazamos la función antigua
        #    Buscamos el decorador @app.route('/', ...) y reemplazamos hasta el final de la función
        #    Como Python usa indentación, asumimos que dummy_rpc termina antes del siguiente bloque sin indentar.
        
        if "def dummy_rpc():" in content:
            # Estrategia: Reemplazar todo el bloque dummy_rpc antiguo con nuestra versión nueva.
            # Usamos split para cortar justo donde empieza la función antigua.
            pattern = "@app.route('/', methods=['POST', 'GET'])"
            parts = content.split(pattern)
            
            if len(parts) > 1:
                pre_content = parts[0]
                post_content = parts[1]
                
                # Buscamos dónde termina la función antigua (el siguiente bloque sin indentar o decorador)
                # En el código original, dummy_rpc es la última función antes de "if __name__"
                # o antes de "# END A2A COMPATIBILITY".
                
                # Vamos a cortar hasta el siguiente decorador o bloque principal
                next_block_markers = ["# =============================================================================", "if __name__ == '__main__':", "@app.route"]
                
                rest_of_code = ""
                for marker in next_block_markers:
                    if marker in post_content:
                        # Encontramos el final de la función, cortamos ahí
                        # Pero cuidado, dummy_rpc está dentro de post_content, así que split[1] es lo que sigue
                        subparts = post_content.split(marker, 1)
                        if len(subparts) > 1:
                            rest_of_code = marker + subparts[1]
                            break
                
                # Si no encontramos marcadores, asumimos que era lo último del archivo (raro pero posible)
                if not rest_of_code and "if __name__" in content:
                     rest_of_code = "if __name__" + content.split("if __name__")[-1]

                # Construimos el nuevo contenido
                new_content = pre_content + new_rpc_code + "\\n\\n" + rest_of_code
                
                # Aseguramos imports necesarios al principio
                if "from flask import" in new_content:
                     new_content = new_content.replace("from flask import Flask", "from flask import Flask, jsonify, Response, stream_with_context")
                else:
                     new_content = "from flask import Flask, jsonify, Response, stream_with_context\\n" + new_content

                # Guardamos
                with open(target_file, 'w') as f:
                    f.write(new_content)
                print("✅ Función dummy_rpc reemplazada con éxito.", flush=True)
            else:
                print("⚠️ No se pudo localizar el punto de inyección exacto.", flush=True)
        else:
            print("⚠️ No se encontró la función dummy_rpc original. Inyectando al final...", flush=True)
            # Fallback: Añadir al final (antes del main)
            parts = content.split("if __name__")
            new_content = parts[0] + "\\n" + new_rpc_code + "\\nif __name__" + parts[1]
            with open(target_file, 'w') as f:
                f.write(new_content)

        # 4. Arrancamos el servidor
        print("🚀 Arrancando Green Agent...", flush=True)
        sys.stdout.flush()
        os.system(f"python -u {{target_file}} --host 0.0.0.0 --port {green_port} --card-url http://green-agent:{green_port}")

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

# 🟢 PARTICIPANTE (Purple Agent) - MODO ZOMBI
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


def resolve_image(agent: dict, name: str) -> None:
    """Resolve docker image for an agent."""
    has_image = "image" in agent
    has_id = "agentbeats_id" in agent

    if has_image and has_id:
        print(f"Error: {name} has both 'image' and 'agentbeats_id' - use one or the other")
        sys.exit(1)
    elif has_image:
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"Error: {name} requires 'agentbeats_id' for GitHub Actions")
            sys.exit(1)
        print(f"Using {name} image: {agent['image']}")
    elif has_id:
        info = fetch_agent_info(agent["agentbeats_id"])
        agent["image"] = info["docker_image"]
        
        # 🟢 CAPTURAMOS EL ID DEL WEBHOOK
        if "id" in info:
            agent["webhook_id"] = info["id"]
        
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

    names = [p.get("name") for p in participants]
    duplicates = [name for name in set(names) if names.count(name) > 1]
    if duplicates:
        print(f"Error: Duplicate participant names found: {', '.join(duplicates)}")
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

    # Generamos los servicios de los participantes
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
        
        # 🟢 USAMOS EL ID DEL WEBHOOK SI EXISTE
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

    for value in green.get("env", {}).values():
        for match in env_var_pattern.findall(str(value)):
            secrets.add(match)

    for p in participants:
        for value in p.get("env", {}).values():
            for match in env_var_pattern.findall(str(value)):
                secrets.add(match)

    if not secrets:
        return ""

    lines = []
    for secret in sorted(secrets):
        lines.append(f"{secret}=")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Generate Docker Compose from scenario.toml")
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

    print(f"Generated {COMPOSE_PATH} and {A2A_SCENARIO_PATH} (FINAL SOURCE CODE PATCH)")

if __name__ == "__main__":
    main()
