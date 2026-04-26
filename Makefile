# Convenience aliases. We're a Python-uv project (no npm), so these map
# what resume-builder exposes via `npm run docs:check / docs:sync`.

.PHONY: docs-check docs-sync help

help:
	@echo "make targets:"
	@echo "  docs-check   audit docs vs code (claude-driven; ~\$$0.05-0.20)"
	@echo "  docs-sync    write doc updates to bring in sync (~\$$0.30-0.50)"

docs-check:
	@bash scripts/docs-sync.sh check

docs-sync:
	@bash scripts/docs-sync.sh apply
