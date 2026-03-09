PYTHON   := python3
PID_FILE := .csm.pid
CONFIG   := config/config.yaml

.PHONY: init-shm run stop clean-shm snapshot help

## init-shm   — Create and initialise the POSIX shared memory segment
init-shm:
	$(PYTHON) -m shm.shm_init --config $(CONFIG)

## run         — Start all system components in the background
run:
	$(PYTHON) -m main --config $(CONFIG) & echo $$! > $(PID_FILE)
	@echo "CSM started (pid=$$(cat $(PID_FILE)))"

## stop        — Send SIGTERM to the running process
stop:
	@if [ -f $(PID_FILE) ]; then \
		kill $$(cat $(PID_FILE)) && rm -f $(PID_FILE) && echo "CSM stopped"; \
	else \
		echo "No PID file found — is CSM running?"; \
	fi

## clean-shm   — Safely remove the POSIX shared memory segment
clean-shm:
	$(PYTHON) -m shm.shm_cleaner --config $(CONFIG)

## snapshot    — Write a single spread snapshot and exit
snapshot:
	$(PYTHON) -m spread_reader.spread_runner --config $(CONFIG) --once

## help        — Show this help message
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## /  /'
