#!/usr/bin/env bash
# Peak-memory check under Render's free-tier cap (512 MB). Runs the image with a hard memory limit,
# answers a handful of questions through /demo/ask, and prints peak usage from `docker stats`.
#   scripts/mem_check.sh [image] [extra docker run args...]
# Needs ANTHROPIC_API_KEY in the environment (a few cents at most). Never prints it.
set -euo pipefail
IMAGE="${1:-maint-rag:local}"; shift || true
NAME=mr-mem; PORT=7871
docker rm -f $NAME >/dev/null 2>&1 || true
docker run -d --name $NAME -m 512m --memory-swap 512m -p $PORT:7860 \
  -e ANTHROPIC_API_KEY -e APP_API_KEY=x -e DEMO_RATE_LIMIT=1000/hour "$@" "$IMAGE" >/dev/null
trap 'docker rm -f $NAME >/dev/null 2>&1' EXIT
LOG=$(mktemp)
( for _ in $(seq 1 75); do docker stats --no-stream --format '{{.MemUsage}}' $NAME; sleep 0.5; done > "$LOG" ) &
for _ in $(seq 1 60); do curl -sf localhost:$PORT/health >/dev/null && break; sleep 1; done
sleep 2; echo "idle: $(docker stats --no-stream --format '{{.MemUsage}}' $NAME)"
for q in "How do I verify zero energy state before working on a conveyor?" \
         "What is the most common root cause of VFD faults?" \
         "What supply air temperature does the DOE guide recommend?" \
         "Who won the 1998 World Cup?" "What are the lockout tagout steps?" \
         "How should UPS batteries be maintained?" "What causes high supply air temperature in a CRAC unit?" \
         "How do I handle a photo-eye sensor fault?"; do
  curl -s -o /dev/null -w "%{http_code} " -X POST localhost:$PORT/demo/ask -H 'content-type: application/json' -d "{\"question\":\"$q\"}"
done; echo
wait
awk '{u=$1; gsub(/[0-9.]/,"",u); n=$1+0; if(u=="GiB")n*=1024; if(n>m)m=n} END{printf "peak: %.0f MiB of 512\n", m}' "$LOG"
echo "oomkilled=$(docker inspect $NAME --format '{{.State.OOMKilled}}')"
