.PHONY: compile sync

compile: requirements.txt requirements-dev.txt

requirements.txt: requirements.in
	uv pip compile $< -o $@ --no-header

requirements-dev.txt: requirements-dev.in requirements.in
	uv pip compile $< -o $@ --no-header

sync:
	uv pip sync requirements-dev.txt
