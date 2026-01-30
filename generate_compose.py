
import argparse
import tomli
import yaml
import sys

def generate_compose(scenario_path):
    with open(scenario_path, "rb") as f:
        scenario = tomli.load(f)

    # Configuración base
    compose = {
        "version": "3.8",
        "services": {},
        "networks": {
            "agent-network": {"driver": "bridge"}
        }
    }

    # 1. Configurar GREEN AGENT (Modo Simulación Mac)
    compose["services"]["green-agent"] = {
        # FUNCIONA MUY BIEN SIMULANDO EL MAC
        # "build": ".",  # <--- LA CLAVE: Usamos tus archivos locales, no la imagen de la nube
        # PARA TRABAJAR CON IMAGE
        "image": "ghcr.io/star-xai-protocol/capsbench:latest",
        "ports": ["9009:9009"],
        "environment": {
            "RECORD_MODE": "true",
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": "/app/src" # Asegura que Python encuentre la carpeta src
        },
        # IMPORTANTE: Como el Dockerfile arranca tu Agente por defecto,
        # aquí forzamos a que arranque el Servidor (Green Agent).
        "command": ["python", "-m", "src.green_agent"],
        
        "volumes": [
            "./replays:/app/src/replays",
            "./logs:/app/src/logs",
            "./results:/app/src/results"
        ],
        "healthcheck": {
            "test": ["CMD", "curl", "-f", "http://localhost:9009/status"],
            "interval": "5s",
            "timeout": "5s",
            "retries": 20,
            "start_period": "5s"
        },
        "networks": ["agent-network"]
    }

    # 2. Configurar PURPLE AGENT (Tu IA)
    compose["services"]["purple-agent"] = {
        # "build": ".", # Construye usando tu Dockerfile y requirements.txt
        # USAMOS LA IMAGEN NUEVA DEL REPO PURPLE:
        "image": "ghcr.io/star-xai-protocol/capsbench-purple:latest",
        
        "command": ["python", "purple_ai.py"], 
        "environment": {
            "SERVER_URL": "http://green-agent:9009",
            "GOOGLE_API_KEY": "${GOOGLE_API_KEY}"
        },
        "depends_on": {
            "green-agent": {"condition": "service_healthy"}
        },
        "networks": ["agent-network"]
    }

    # 3. Configurar CLIENT (Árbitro) -> DESACTIVADO PARA EVITAR ERRORES EN GITHUB
    # (No es necesario para que tu IA juegue y eliminas el ruido en los logs)
    
    # compose["services"]["agentbeats-client"] = {
    #     "image": "ghcr.io/agentbeats/agentbeats-client:v1.0.0",
    #     "volumes": [
    #         "./output:/app/output",
    #     ],
    #     # 🔑 ESTA ES LA CLAVE: Le decimos a Python dónde está el código
    #     "environment": {
    #         "PYTHONPATH": "/app/src"
    #     },
    #     # Creamos el config al vuelo para evitar errores de lectura
    #     "entrypoint": ["/bin/sh", "-c"],
    #     "command": [
    #         #1. INSTALAMOS LA LIBRERÍA QUE FALTA
    #         "pip install httpx && "  # 1. Instalamos httpx
    #         "pip install a2a && "    # 2. Instalamos a2a
    #
    #         # 2. CONFIGURAMOS Y EJECUTAMOS
    #         "echo '[green]' > /tmp/config.toml && "
    #         "echo 'name = \"Green Agent\"' >> /tmp/config.toml && "
    #         "echo 'endpoint = \"http://green-agent:9009\"' >> /tmp/config.toml && "
    #         "echo '' >> /tmp/config.toml && "
    #         "echo '[purple]' >> /tmp/config.toml && "
    #         "echo 'name = \"Purple Agent\"' >> /tmp/config.toml && "
    #         "echo 'endpoint = \"http://purple-agent:80\"' >> /tmp/config.toml && "
    #         "python -m agentbeats.client_cli /tmp/config.toml /app/output/results.json"
    #     ],
    #     "depends_on": {
    #         "green-agent": {"condition": "service_healthy"}
    #     },
    #     "networks": ["agent-network"]
    # }
    
    # Guardar el archivo final
    with open("docker-compose.yml", "w") as f:
        yaml.dump(compose, f, sort_keys=False)
    
    print("✅ docker-compose.yml generado correctamente.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, help="Path to scenario.toml")
    args = parser.parse_args()
    generate_compose(args.scenario)
