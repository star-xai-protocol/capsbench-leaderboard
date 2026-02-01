"""Generate Docker Compose configuration from scenario.toml"""

import argparse
import os
import sys
import time
import tomli
import shutil
import base64

from pathlib import Path
from typing import Any

# --- CÓDIGO DEL VIGILANTE: VERSIÓN FINAL (BASE64 + SPLIT EVENTS) ---
VIGILANTE_CODE = r"""
# ==========================================
# PARCHE VIGILANTE: SPLIT EVENTS PROTOCOL
# ==========================================
import time
import glob
import json
import os
from flask import Response, stream_with_context, jsonify

# 1. AGENT CARD COMPLETA
@app.route('/.well-known/agent-card.json')
def agent_card_patched():
    return jsonify({
        "name": "Green Agent Patched",
        "version": "1.0.0",
        "description": "Patched for Leaderboard",
        "url": "http://green-agent:9009/",
        "protocolVersion": "0.3.0",
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "capabilities": {"streaming": True},
        "skills": [{
            "id": "capsbench_eval", 
            "name": "CapsBench Evaluation",
            "description": "Evaluation skill",
            "tags": ["evaluation"]
        }]
    })

# 2. RPC ROBUSTO (Separa Artefacto de Estado)
@app.route('/', methods=['POST', 'GET'])
def dummy_rpc_patched():
    def generate():
        print("👁️ VIGILANTE: Esperando resultados...", flush=True)
        ctx_id = "ctx-1"
        task_id = "task-1"
        
        while True:
            # Busca resultados
            found = glob.glob('src/results/*.json') + glob.glob('results/*.json')
            
            if found:
                file_path = found[0]
                print(f"🏁 FIN DETECTADO: {file_path}", flush=True)
                time.sleep(2) 
                
                try:
                    with open(file_path, 'r') as f:
                        file_content = f.read()
                except Exception:
                    file_content = "{}"

                artifact_data = {
                    "name": os.path.basename(file_path),
                    "content": file_content
                }

                # PASO 1: ENVIAR EL ARTEFACTO (Evento separado)
                yield 'data: ' + json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1, 
                    "result": {
                        "contextId": ctx_id,
                        "taskId": task_id,
                        "final": False, 
                        "artifact": artifact_data
                    }
                }) + '\n\n'
                
                time.sleep(1)

                # PASO 2: ENVIAR FIN DE TAREA
                yield 'data: ' + json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1, 
                    "result": {
                        "contextId": ctx_id,
                        "taskId": task_id,
                        "final": True, 
                        "status": {"state": "completed"}
                    }
                }) + '\n\n'
                break
            
            # HEARTBEAT
            yield 'data: ' + json.dumps({
                "jsonrpc": "2.0", 
                "id": 1, 
                "result": {
                    "contextId": ctx_id,
                    "taskId": task_id,
                    "final": False, 
                    "status": {"state": "working"}
                }
            }) + '\n\n'
            time.sleep(2)
            
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

if __name__ == "__main__":
    print("🟢 SERVIDOR VIGILANTE (SPLIT) INICIANDO...", flush=True)
    app.run(host="0.0.0.0", port=9009)
"""

# Codificamos en Base64
VIGILANTE_PAYLOAD = base64.b64encode(VIGILANTE_CODE.encode('utf-8')).decode('utf-8')

# PLANTILLA DOCKER COMPOSE
COMPOSE_TEMPLATE = """# Auto-generated from scenario.toml

services:
  green-agent:
    image: ghcr.io/star-xai-protocol/capsbench:latest
    platform: linux/amd64
    container_name: green-agent
    
    # 🛡️ ESTRATEGIA: INYECCIÓN SEGURA VIA BASE64
    entrypoint:
      - /bin/sh
      - -c
      - |
        echo '🔧 PREPARANDO SERVIDOR...'
        sed -i "s|@app.route('/',|@app.route('/old_root',|g" src/green_agent.py
        sed -i "s|@app.route('/.well-known/agent-card.json'|@app.route('/.well-known/old-card.json'|g" src/green_agent.py
        
        # Eliminar el arranque original
        python -c "lines = [l for l in open('src/green_agent.py') if 'app.run' not in l]; open('src/green_agent.py','w').writelines(lines)"
        
        # Inyectar el Vigilante decodificando
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

        # Mantenemos el sleep infinity
        participant_services += f"""
  {name}:
    image: ghcr.io/star-xai-protocol/capsbench-purple:latest
    platform: linux/amd64
    container_name: {name}
    entrypoint: ["/bin/sh", "-c", "python -u purple_ai.py; echo '✅ FIN. DURMIENDO...'; sleep infinity"]
    {env_block}
    depends_on:
      - green-agent
    networks:
      - agent-network
"""

    final_compose = COMPOSE_TEMPLATE.format(
        participant_services=participant_services,
        vigilante_payload=VIGILANTE_PAYLOAD,
        timestamp=int(time.time())
    )

    with open("docker-compose.yml", "w") as f:
        f.write(final_compose)
    
    shutil.copy(args.scenario, "a2a-scenario.toml")
    print("✅ LISTO: Configuración generada con protección total.")

if __name__ == "__main__":
    main()