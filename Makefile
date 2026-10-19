.PHONY: test demo seed-demo migrate verify-db backup restore

DB ?= careflow.db
BACKUP ?= careflow.backup.sqlite
RESTORE_TARGET ?= careflow.restore.sqlite

test:
	python -m pytest -q
demo:
	python -m careflow.demo
seed-demo:
	python -m careflow.seed --db careflow.db
migrate:
	python -m careflow.ops migrate --db "$(DB)"
verify-db:
	python -m careflow.ops verify --db "$(DB)"
backup:
	python -m careflow.ops backup --db "$(DB)" --output "$(BACKUP)"
restore:
	python -m careflow.ops restore --backup "$(BACKUP)" --target "$(RESTORE_TARGET)"
