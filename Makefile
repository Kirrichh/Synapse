# Synapse project Makefile
# Usage: make init | make test | make lint | make audit | make test-golden

.PHONY: init test lint audit test-golden help

help:
	@echo "Synapse project targets:"
	@echo "  make init        — install pre-commit hook + verify Python >= 3.10"
	@echo "  make test        — run full pytest suite"
	@echo "  make lint        — run pre-commit gate (parse + coverage)"
	@echo "  make audit       — run corpus fallback audit and update report"
	@echo "  make test-golden — run alpha3e golden replay integration gate"

init:
	@python3 -c "import sys; assert sys.version_info >= (3,10), f'Python 3.10+ required, got {sys.version_info.major}.{sys.version_info.minor}'"
	@echo "Python version OK"
	@cp scripts/pre_commit_hook.py .git/hooks/pre-commit 2>/dev/null && chmod +x .git/hooks/pre-commit && echo "Pre-commit hook installed" || echo "Note: .git not found — skipping hook install (run from repo root)"

test:
	python3 -m pytest -q

lint:
	python3 scripts/pre_commit_hook.py

audit:
	python3 scripts/corpus_fallback_audit.py --output reports/corpus_fallback_alpha3e.json

test-golden:
	python3 -m pytest -q tests/test_golden_replay_alpha3e.py
