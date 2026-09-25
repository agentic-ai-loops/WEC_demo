# Development tasks. Settings come from .env; override on the command line,
# e.g. `make mcp-start MCP_PORT=9102 GRAPHQL_PORT=9101`.

-include .env

GRAPHQL_HOST ?= 0.0.0.0
GRAPHQL_PORT ?= 9001
MCP_HOST ?= 0.0.0.0
MCP_PORT ?= 9002
INTELLIW_RUN_DIR ?= .run
WORKSPACE ?= examples/whitby_eye_care

# The servers (via `svr`) read these from the environment.
export GRAPHQL_HOST GRAPHQL_PORT MCP_HOST MCP_PORT INTELLIW_RUN_DIR WORKSPACE

MCP_CLIENT_HOST := $(if $(filter 0.0.0.0 ::,$(MCP_HOST)),127.0.0.1,$(MCP_HOST))
MCP_URL := http://$(MCP_CLIENT_HOST):$(MCP_PORT)/mcp
MCP_LOG := $(INTELLIW_RUN_DIR)/mcp.log
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

.PHONY: help mcp-start mcp-stop mcp-restart mcp-status reimport claude

help:
	@echo "make mcp-start    start the MCP server (with GraphQL) in the background"
	@echo "make mcp-stop     stop it"
	@echo "make mcp-restart  stop, then start"
	@echo "make mcp-status   show server status"
	@echo "make reimport     rebuild examples/whitby_eye_care from digest.xml"
	@echo "make claude       start Claude Code (isolated, MCP only) connected to the server"
	@echo
	@echo "workspace: $(WORKSPACE)   mcp: $(MCP_URL)   log: $(MCP_LOG)"

mcp-start:
	@mkdir -p $(INTELLIW_RUN_DIR)
	@if uv run svr mcp --check >/dev/null 2>&1; then \
		echo "MCP server already running at $(MCP_URL)"; \
	else \
		echo "Starting MCP server with GraphQL (log: $(MCP_LOG))"; \
		nohup uv run svr mcp --start-graphql > $(MCP_LOG) 2>&1 & \
		for i in $$(seq 1 40); do uv run svr mcp --check >/dev/null 2>&1 && break; sleep 0.5; done; \
		uv run svr mcp --check || { echo "MCP server did not start; see $(MCP_LOG)"; exit 1; }; \
	fi

mcp-stop:
	@uv run svr mcp --stop

mcp-restart:
	-@uv run svr mcp --stop
	@$(MAKE) --no-print-directory mcp-start

mcp-status:
	@uv run svr status

# The import replaces the database file, so a running server is stopped first and
# started again afterwards (it would otherwise keep serving the old file).
reimport:
	@running=0; \
	if uv run svr mcp --check >/dev/null 2>&1; then running=1; uv run svr mcp --stop; fi; \
	uv run python examples/import_whitby_digest.py --force && \
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
