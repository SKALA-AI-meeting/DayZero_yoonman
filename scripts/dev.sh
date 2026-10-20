#!/usr/bin/env zsh
# Restart every local Python process on the current code.
# Usage: scripts/dev.sh [up|down|status]   (default: up)
# Logs: .logs/<name>.log. OPENAI_API_KEY comes from the user's zsh startup, never from .env.
set -u
ROOT=${0:A:h:h}
cd "$ROOT"
PY=services/dayzero/.venv/bin/python
LOGS=.logs
mkdir -p $LOGS

typeset -A PROCS
PROCS=(
  backend      "services/dayzero/.venv/bin/uvicorn dayzero.backend.main:app --host 127.0.0.1 --port 8000 --no-access-log"
  business-mcp "$PY -m dayzero.mcp_servers.business"
  rag-mcp      "$PY -m dayzero.mcp_servers.rag"
  worker-ai    "$PY -m dayzero.workers.ai"
  worker-report "$PY -m dayzero.workers.report"
)
PATTERNS=('dayzero.backend.main:app' 'dayzero.mcp_servers.business' 'dayzero.mcp_servers.rag' 'dayzero.workers.ai' 'dayzero.workers.report')

down() {
  for p in $PATTERNS; do pkill -f "$p" 2>/dev/null; done
  for _ in {1..20}; do
    local alive=0
    for p in $PATTERNS; do pgrep -f "$p" >/dev/null && alive=1; done
    (( alive )) || return 0
    sleep 0.5
  done
  for p in $PATTERNS; do pkill -9 -f "$p" 2>/dev/null; done
}

status() {
  local names=(backend business-mcp rag-mcp worker-ai worker-report)
  local result=0
  for i in {1..5}; do
    if pgrep -f "$PATTERNS[$i]" >/dev/null; then
      printf '%-14s running\n' $names[$i]
    else
      printf '%-14s stopped\n' $names[$i]
      result=1
    fi
  done
  if curl -sf --max-time 2 localhost:8000/health >/dev/null; then
    echo "backend health: ok"
  else
    echo "backend health: FAIL"
    result=1
  fi
  return $result
}

up() {
  down
  docker compose up -d --wait postgres rabbitmq >/dev/null || { echo 'docker compose failed'; exit 1; }
  services/dayzero/.venv/bin/alembic -c services/dayzero/alembic.ini upgrade head >$LOGS/migrate.log 2>&1 \
    || { echo "migration failed: see $LOGS/migrate.log"; exit 1; }
  local hf=$(grep -E '^HF_HOME=' .env | cut -d= -f2-)
  [[ -n $hf ]] && export HF_HOME=$hf
  for name in backend business-mcp rag-mcp worker-ai worker-report; do
    if [[ $name == worker-* ]]; then
      nohup $PY scripts/with_zsh_api_key.py ${=PROCS[$name]} >$LOGS/$name.log 2>&1 &
    else
      nohup ${=PROCS[$name]} >$LOGS/$name.log 2>&1 &
    fi
  done
  for _ in {1..40}; do curl -sf --max-time 1 localhost:8000/health >/dev/null && break; sleep 0.5; done
  sleep 2
  local result=0
  status || result=$?
  echo "logs: $LOGS/*.log  (web: npm run dev --prefix apps/web)"
  return $result
}

case ${1:-up} in
  up) up ;;
  down) down; echo stopped ;;
  status) status ;;
  *) echo "usage: $0 [up|down|status]"; exit 1 ;;
esac
