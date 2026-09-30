# Development tasks. Settings come from .env; override on the command line,
# e.g. `make mcp-start SVR_PORT=8100`.

-include .env

SVR_HOST ?= 0.0.0.0
SVR_PORT ?= 8000
INTELLIW_RUN_DIR ?= run
WORKSPACE ?= workspaces/whitby_eye_care
REDIS_URL ?= redis://localhost:6379/0
# rq-dashboard: its own port, never the server's
DASHBOARD_HOST ?= 127.0.0.1
DASHBOARD_PORT ?= 9181
DASHBOARD_USERNAME ?=
DASHBOARD_PASSWORD ?=

# The server (`svr start`) and the worker (`jobs worker`) read these from the environment.
export SVR_HOST SVR_PORT INTELLIW_RUN_DIR WORKSPACE REDIS_URL

CLIENT_HOST := $(if $(filter 0.0.0.0 ::,$(SVR_HOST)),127.0.0.1,$(SVR_HOST))
SVR_URL := http://$(CLIENT_HOST):$(SVR_PORT)
MCP_URL := $(SVR_URL)/mcp
SVR_LOG := $(INTELLIW_RUN_DIR)/svr.log
WORKER_LOG := $(INTELLIW_RUN_DIR)/worker.log
DASHBOARD_LOG := $(INTELLIW_RUN_DIR)/dashboard.log
DASHBOARD_PID := $(INTELLIW_RUN_DIR)/dashboard.pid
DASHBOARD_CLIENT_HOST := $(if $(filter 0.0.0.0 ::,$(DASHBOARD_HOST)),127.0.0.1,$(DASHBOARD_HOST))
DASHBOARD_URL := http://$(DASHBOARD_CLIENT_HOST):$(DASHBOARD_PORT)
REDIS_CONTAINER ?= intelliw-redis
REDIS_PORT ?= 6379
# The owner agent runs isolated from this repository: in an empty directory outside the
# home directory (no CLAUDE.md discovery, fresh auto-memory, its own session history),
# with every built-in tool disabled (no file access, no shell) and only the intelliw MCP
# server connected. Read-only tools are pre-approved; graphql_mutate asks before changes.
CLAUDE_DIR ?= $(or $(TMPDIR),/tmp)/intelliw-owner-agent
CLAUDE_ALLOWED := mcp__intelliw__graphql_query,mcp__intelliw__graphql_schema
CLAUDE_MODEL ?= sonnet
CLAUDE_PROMPT := You are the assistant of a small-business owner. Manage the business's web \
presence only through the intelliw MCP tools; ask the owner before making changes.
CLAUDE_ARGS ?=

.PHONY: help mcp-start mcp-stop mcp-restart mcp-status reimport claude \
	redis-start redis-stop worker-start worker-stop worker-status \
	dashboard-start dashboard-stop dashboard-status

help:
	@echo "make mcp-start    start the server (/mcp, /graphql, /health) in the background"
	@echo "make mcp-stop     stop it"
	@echo "make mcp-restart  stop, then start"
	@echo "make mcp-status   show server status"
	@echo "make reimport     rebuild workspaces/whitby_eye_care from digest.xml"
	@echo "make claude       start Claude Code (isolated, MCP only) connected to the server"
	@echo "make redis-start  start Redis (docker: $(REDIS_CONTAINER)) for the re-render queue"
	@echo "make redis-stop   stop it"
	@echo "make worker-start start the re-render worker in the background"
	@echo "make worker-stop  stop it (also: make worker-status)"
	@echo "make dashboard-start  start rq-dashboard (the queue in a browser) in the background"
	@echo "make dashboard-stop   stop it (also: make dashboard-status)"
	@echo
	@echo "workspace: $(WORKSPACE)   server: $(SVR_URL)   log: $(SVR_LOG)"
	@echo "dashboard: $(DASHBOARD_URL)   redis: $(REDIS_URL)"

mcp-start:
	@mkdir -p $(INTELLIW_RUN_DIR)
	@if uv run svr status >/dev/null 2>&1; then \
		echo "Server already running at $(SVR_URL)"; \
	else \
		echo "Starting the server at $(SVR_URL) (log: $(SVR_LOG))"; \
		nohup uv run svr start > $(SVR_LOG) 2>&1 & \
		for i in $$(seq 1 40); do uv run svr status >/dev/null 2>&1 && break; sleep 0.5; done; \
		uv run svr status || { echo "The server did not start; see $(SVR_LOG)"; exit 1; }; \
	fi

mcp-stop:
	@uv run svr stop

mcp-restart:
	-@uv run svr stop
	@$(MAKE) --no-print-directory mcp-start

mcp-status:
	@uv run svr status

# The import replaces the database file, so a running server is stopped first and
# started again afterwards (it would otherwise keep serving the old file).
reimport:
	@running=0; \
	if uv run svr status >/dev/null 2>&1; then running=1; uv run svr stop; fi; \
	uv run python workspaces/import_whitby_digest.py --force && \
	if [ $$running = 1 ]; then $(MAKE) --no-print-directory mcp-start; fi

claude: mcp-start
	@mkdir -p "$(CLAUDE_DIR)"
	@printf '{"mcpServers": {"intelliw": {"type": "http", "url": "%s"}}}\n' "$(MCP_URL)" \
		> "$(CLAUDE_DIR)/mcp.json"
	cd "$(CLAUDE_DIR)" && claude --model $(CLAUDE_MODEL) \
		--tools "" \
		--mcp-config mcp.json --strict-mcp-config \
		--allowedTools "$(CLAUDE_ALLOWED)" \
		--append-system-prompt "$(CLAUDE_PROMPT)" \
		$(CLAUDE_ARGS)

# Redis for the re-render queue (rq), from the pulled `redis` image.
redis-start:
	@if docker ps --format '{{.Names}}' | grep -qx $(REDIS_CONTAINER); then \
		echo "Redis already running ($(REDIS_CONTAINER))"; \
	elif docker ps -a --format '{{.Names}}' | grep -qx $(REDIS_CONTAINER); then \
		docker start $(REDIS_CONTAINER) >/dev/null && echo "Redis started ($(REDIS_CONTAINER))"; \
	else \
		docker run -d --name $(REDIS_CONTAINER) -p 127.0.0.1:$(REDIS_PORT):6379 redis >/dev/null \
			&& echo "Redis started ($(REDIS_CONTAINER), port $(REDIS_PORT))"; \
	fi

redis-stop:
	@docker stop $(REDIS_CONTAINER) >/dev/null && echo "Redis stopped ($(REDIS_CONTAINER))"

# The worker re-renders every design after each mutation (sites in $(INTELLIW_RUN_DIR)/sites/).
worker-start:
	@mkdir -p $(INTELLIW_RUN_DIR)
	@if uv run jobs status >/dev/null 2>&1; then \
		echo "Worker already running"; \
	else \
		echo "Starting the re-render worker (log: $(WORKER_LOG))"; \
		nohup uv run jobs worker > $(WORKER_LOG) 2>&1 & \
		for i in $$(seq 1 20); do uv run jobs status >/dev/null 2>&1 && break; sleep 0.5; done; \
		uv run jobs status || { echo "The worker did not start; see $(WORKER_LOG)"; exit 1; }; \
	fi

worker-stop:
	@uv run jobs stop

worker-status:
	@uv run jobs status

# rq-dashboard: the re-render queue in a browser (queues, jobs and results, workers).
# It can delete and requeue jobs: set DASHBOARD_USERNAME / DASHBOARD_PASSWORD when
# DASHBOARD_HOST is reachable from other machines.
dashboard-start:
	@mkdir -p $(INTELLIW_RUN_DIR)
	@if [ "$(DASHBOARD_PORT)" = "$(SVR_PORT)" ]; then \
		echo "DASHBOARD_PORT ($(DASHBOARD_PORT)) must differ from SVR_PORT"; exit 1; fi
	@if [ -f $(DASHBOARD_PID) ] && kill -0 $$(cat $(DASHBOARD_PID)) 2>/dev/null; then \
		echo "Dashboard already running at $(DASHBOARD_URL) (pid $$(cat $(DASHBOARD_PID)))"; \
	else \
		echo "Starting rq-dashboard at $(DASHBOARD_URL) (log: $(DASHBOARD_LOG))"; \
		RQ_DASHBOARD_USERNAME="$(DASHBOARD_USERNAME)" RQ_DASHBOARD_PASSWORD="$(DASHBOARD_PASSWORD)" \
		nohup .venv/bin/rq-dashboard --bind $(DASHBOARD_HOST) --port $(DASHBOARD_PORT) \
			--redis-url $(REDIS_URL) > $(DASHBOARD_LOG) 2>&1 & echo $$! > $(DASHBOARD_PID); \
		for i in $$(seq 1 20); do curl -s -o /dev/null $(DASHBOARD_URL)/ && break; sleep 0.5; done; \
		curl -s -o /dev/null -w "dashboard: HTTP %{http_code}\n" $(DASHBOARD_URL)/ \
			|| { echo "rq-dashboard did not start; see $(DASHBOARD_LOG)"; exit 1; }; \
	fi

dashboard-stop:
	@if [ -f $(DASHBOARD_PID) ] && kill $$(cat $(DASHBOARD_PID)) 2>/dev/null; then \
		rm -f $(DASHBOARD_PID); echo "Dashboard stopped"; \
	else \
		rm -f $(DASHBOARD_PID); echo "Dashboard is not running"; exit 1; \
	fi

dashboard-status:
	@if [ -f $(DASHBOARD_PID) ] && kill -0 $$(cat $(DASHBOARD_PID)) 2>/dev/null; then \
		echo "Dashboard running at $(DASHBOARD_URL) (pid $$(cat $(DASHBOARD_PID)))"; \
	else \
		echo "Dashboard is not running"; exit 1; \
	fi
