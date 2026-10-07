# MaintainIQ task runner. `just` with no args lists recipes.
# Uses Git Bash on Windows so recipes read the same on every platform.
set windows-shell := ["bash", "-c"]
set dotenv-load := false

default:
    @just --list

# --- setup -------------------------------------------------------------

# Install backend + frontend dependencies
install:
    pip install -r requirements.txt
    cd frontend && npm install

# Populate machines + readings from the XJTU-SY feature cache (pass --data-dir=... to rebuild)
backfill *args:
    python -m src.ingestion.backfill {{args}}

# Seed demo login accounts (admin/supervisor/operator)
seed:
    python -m src.auth.seed

# Train the XJTU-SY RUL model and register it as active
train data_dir *args:
    python -m src.training.xjtu_rul --data-dir "{{data_dir}}" {{args}}

# First-time setup: deps, DB, demo users, built dashboard
setup: install backfill seed build

# --- run ---------------------------------------------------------------

# Build the React dashboard into frontend/dist
build:
    cd frontend && npm run build

# Serve API + built dashboard on http://localhost:8000
serve:
    uvicorn src.api.app:app --reload

# Frontend hot-reload dev server on :5173 (run `just serve` alongside)
dev:
    cd frontend && npm run dev

# Docker stack (app on :8000 + Mailpit inbox on :8025 + Mosquitto on :1883); bind-mounts ./maintainiq.db
docker:
    docker compose up --build

# --- live telemetry (M6, design/M6_LIVE_TELEMETRY.md) --------------------

# Start only the Mosquitto broker on localhost:1883 (detached)
broker:
    docker compose up -d mosquitto

# Stop the broker (its retained messages persist in the mosquitto-data volume)
broker-stop:
    docker compose stop mosquitto

# Publish/subscribe roundtrip against the broker to prove it is reachable
broker-check host="localhost" port="1883":
    python -c "import sys,time,threading,paho.mqtt.client as m; got=threading.Event(); c=m.Client(m.CallbackAPIVersion.VERSION2, client_id='maintainiq-broker-check'); c.on_message=lambda *a: got.set(); c.connect('{{host}}', {{port}}); c.subscribe('maintainiq/check', qos=1); c.loop_start(); time.sleep(0.5); c.publish('maintainiq/check', b'ping', qos=1); ok=got.wait(5); c.loop_stop(); c.disconnect(); print('broker OK' if ok else 'broker: no roundtrip within 5 s'); sys.exit(0 if ok else 1)"

# Simulated ESP32 fleet over MQTT (e.g. `just simulate --devices 3 --outage-every 60`)
simulate *args:
    python -m src.telemetry.simulator {{args}}

# Broker up, then print how to run the app + simulator against it
live: broker
    @echo "Broker on localhost:1883. In two terminals run:"
    @echo "  MQTT_BROKER_HOST=localhost just serve"
    @echo "  just simulate --devices 3 --outage-every 60"

# --- quality -----------------------------------------------------------

# Run all tests
test: test-back test-front

# Backend tests (extra args go to pytest, e.g. `just test-back -k pipeline`)
test-back *args:
    python -m pytest {{args}}

# Frontend tests (Vitest)
test-front:
    cd frontend && npm run test

# Frontend lint (oxlint)
lint:
    cd frontend && npm run lint
