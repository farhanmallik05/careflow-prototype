test:
	python -m pytest -q
demo:
	python -m careflow.demo
seed-demo:
	python -m careflow.seed --db careflow.db
