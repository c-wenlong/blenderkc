# blenderkc: the two venv steps and the tests. The CLI itself needs only python3.
PY ?= 3.13
UV ?= uv

.PHONY: deps narration-deps test

# .venv: bpy and Pillow for the renderer and the overlays (Python 3.13 exactly)
deps:
	@command -v $(UV) >/dev/null || { echo "make deps: uv is not on PATH (https://docs.astral.sh/uv/)" >&2; exit 1; }
	@[ -x .venv/bin/python ] || $(UV) venv --python $(PY) .venv
	$(UV) pip install --python .venv/bin/python --compile-bytecode -r requirements.txt
	@shasum -a 256 requirements.txt 2>/dev/null | cut -d' ' -f1 > .venv/.blenderkc-requirements.sha256 || sha256sum requirements.txt | cut -d' ' -f1 > .venv/.blenderkc-requirements.sha256
	.venv/bin/python -c "import bpy, PIL; print('bpy', bpy.app.version_string, 'Pillow', PIL.__version__)"

# gnosia-audio into the same .venv, at the commit requirements-narration.txt
# pins; GNOSIA_AUDIO_SRC=<checkout> installs a local copy to try a change
narration-deps:
	@command -v $(UV) >/dev/null || { echo "make narration-deps: uv is not on PATH" >&2; exit 1; }
	@[ -x .venv/bin/python ] || $(UV) venv --python $(PY) .venv
	@if [ -n "$(GNOSIA_AUDIO_SRC)" ]; then \
	  $(UV) pip install --python .venv/bin/python --compile-bytecode --reinstall-package gnosia-audio "$(GNOSIA_AUDIO_SRC)"; \
	else \
	  $(UV) pip install --python .venv/bin/python --compile-bytecode --reinstall-package gnosia-audio -r requirements-narration.txt; \
	fi
	.venv/bin/gnosia-audio --version

# the CLI's tests on python3; the library's (test_lib_*) need bpy, so .venv
test:
	python3 -m unittest discover -s tests
	@if [ -x .venv/bin/python ]; then .venv/bin/python -m unittest discover -s tests -p 'test_lib_*.py'; fi
