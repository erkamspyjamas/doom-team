.PHONY: install lint type test quality clean lock

install:
	python -m pip install -e ".[dev]" -c constraints.txt

lint:
	ruff check .

type:
	mypy src

test:
	pytest

quality: lint type test

clean:
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache

lock:
	pip-compile -o constraints.txt pyproject.toml
