SHELL := /bin/bash

# A pre-push hook runs with GIT_DIR naming this repository; nothing here runs
# git on the host, and this keeps it that way should something start to.
unexport GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR \
         GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX

.PHONY: help ci-local ci-remote lint test check image conformance-same live sample fmt

PKG         := alpaca
# The retail pair's other half, whose suite is to be this one, byte for byte.
SIBLING     := ../meridian-tradier
SIBLING_PKG := tradier
# The seam is every spike's: rows.py byte for byte meridian-coinbase's.
SEAM        := ../meridian-coinbase/src/coinbase/rows.py
DOCKER      := DOCKER_BUILDKIT=1 docker
PY_VERSION  := 3.12
CHECK       := meridian-$(PKG)-check
PLUGIN_CHECK := meridian-$(PKG)-plugin-check
IMAGE       ?= $(PKG):local
BASE        ?= ghcr.io/open-meridian/plugin-python:0.18.0
SYMBOL      ?= SPY
FEED        ?= iex
# The key, passed into the container by name alone, so no value is ever on a
# command line; never in CI.
KEY_ENV     := -e APCA_API_KEY_ID -e APCA_API_SECRET_KEY
# The meridian check.yaml holds the plugin to, read from there so the two
# cannot drift.
MERIDIAN_VERSION := $(shell sed -n 's/^ *MERIDIAN_VERSION: *\([0-9][0-9.]*\).*/\1/p' .github/workflows/check.yaml)

help:
	@echo "  make ci-local          every gate: lint, tests, plugin check, the image, the suite unchanged"
	@echo "  make ci-remote         what CI would run: lint and tests"
	@echo "  make test              the tests on the synthetic exchanges, in a container"
	@echo "  make lint              ruff and mypy, strict"
	@echo "  make check             meridian plugin check --run-tests, in a container"
	@echo "  make image             build the plugin's image, as upload would, and check it"
	@echo "  make conformance-same  rows.py identical to the crypto spikes', the suite to $(SIBLING)'s"
	@echo "  make live              the live smoke test on Alpaca, with the environment's key (never in CI)"
	@echo "  make sample            print converted rows for SYMBOL=$(SYMBOL) FEED=$(FEED), with the environment's key"
	@echo "  make fmt               apply the formatter"

# Local green is the completion signal.
ci-local: ci-remote check image conformance-same
	@echo
	@echo "ci-local: GREEN"

ci-remote: lint test
	@echo
	@echo "ci-remote: GREEN"

lint:
	@$(DOCKER) build -f Dockerfile.check --target lint . >/dev/null 2>&1 \
		|| { echo "lint FAILED; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build -f Dockerfile.check --target lint --progress=plain ." >&2; exit 1; }
	@echo "lint OK: ruff and mypy (strict) clean"

test:
	@$(DOCKER) build -f Dockerfile.check --target test -t $(CHECK) . >/dev/null 2>&1 \
		|| { echo "test FAILED to build; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build -f Dockerfile.check --target test --progress=plain ." >&2; exit 1; }
	@docker run --rm --network none $(CHECK) python -m pytest -q -rs >.test.log 2>&1 \
		|| { echo "test FAILED. The last 40 lines, and the whole of it in .test.log:" >&2; \
		     tail -40 .test.log >&2; exit 1; }
	@echo "test OK, with no network: $$(tail -1 .test.log)"

# check.yaml's job, in a container: the meridian it pins holds the plugin to
# the framework's rules and runs its tests. The repository is mounted
# read-only, and the check writes nothing to it.
check:
	@[ -n "$(MERIDIAN_VERSION)" ] \
		|| { echo "check FAILED: .github/workflows/check.yaml pins no MERIDIAN_VERSION" >&2; exit 1; }
	@$(DOCKER) build -f Dockerfile.check --target check --build-arg MERIDIAN_VERSION=$(MERIDIAN_VERSION) \
		-t $(PLUGIN_CHECK) . >/dev/null 2>&1 \
		|| { echo "check FAILED to build; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build -f Dockerfile.check --target check --build-arg MERIDIAN_VERSION=$(MERIDIAN_VERSION) --progress=plain ." >&2; exit 1; }
	@out="$$(docker run --rm --network none -v "$(CURDIR)":/w:ro -e PYTHONDONTWRITEBYTECODE=1 \
		-e PYTEST_ADDOPTS="-p no:cacheprovider" $(PLUGIN_CHECK) meridian plugin check --run-tests 2>&1)" \
		|| { echo "check FAILED:" >&2; echo "$$out" >&2; exit 1; }
	@echo "check OK: meridian $(MERIDIAN_VERSION) plugin check --run-tests, every rule holds"

# The image `meridian plugin upload` builds from this Dockerfile: it starts as
# 65532, imports itself, finds New York's time zone (the exchange day), and
# holds nothing of this repository but the installed package.
image:
	@$(DOCKER) build --build-arg BASE=$(BASE) -t $(IMAGE) . >/dev/null 2>&1 \
		|| { echo "image FAILED; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build --build-arg BASE=$(BASE) --progress=plain ." >&2; exit 1; }
	@[ "$$(docker inspect -f '{{.Config.User}}' $(IMAGE))" = "65532" ] \
		|| { echo "image FAILED: it does not run as 65532" >&2; exit 1; }
	@docker run --rm --network none --entrypoint python $(IMAGE) -c \
		"import meridian, zoneinfo, $(PKG).__main__, $(PKG).page, $(PKG).rows; zoneinfo.ZoneInfo('America/New_York')" \
		|| { echo "image FAILED: it does not import the SDK and itself, or has no New York time zone" >&2; exit 1; }
	@[ -z "$$(docker run --rm --network none --entrypoint sh $(IMAGE) -c 'ls -A /plugin')" ] \
		|| { echo "image FAILED: /plugin holds files; only the installed package belongs" >&2; exit 1; }
	@echo "image OK: $(IMAGE) on $(BASE), $$(docker image inspect -f '{{.Size}}' $(IMAGE) | awk '{printf "%.0f MB", $$1/1e6}')"

# The seam is one module in every spike: rows.py byte for byte meridian-
# coinbase's (and so meridian-kraken's). The retail pair's suite holds
# unchanged only if it is the same suite: tests/test_conformance.py byte for
# byte meridian-tradier's, once that is checked out beside this one.
conformance-same:
	@if [ ! -f "$(SEAM)" ]; then echo "conformance-same SKIPPED: no $(SEAM) beside this one"; exit 0; fi; \
	diff -u src/$(PKG)/rows.py $(SEAM) >&2 \
		|| { echo "conformance-same FAILED: rows.py differs from $(SEAM)" >&2; exit 1; }; \
	if [ ! -d "$(SIBLING)" ]; then \
		echo "conformance-same OK: rows.py is the crypto spikes', byte for byte; no $(SIBLING) to hold the suite to yet"; exit 0; fi; \
	diff -u tests/test_conformance.py $(SIBLING)/tests/test_conformance.py >&2 \
		|| { echo "conformance-same FAILED: tests/test_conformance.py differs from $(SIBLING)'s" >&2; exit 1; }; \
	echo "conformance-same OK: the seam is every spike's and the suite $(SIBLING)'s, byte for byte"

# Opt in, never in CI, never by an agent: Alpaca's real endpoints with the
# developer's key, taken from the environment by name; converted and checked;
# the real responses written under recordings/, which git ignores.
live:
	@[ -n "$$APCA_API_KEY_ID" ] && [ -n "$$APCA_API_SECRET_KEY" ] \
		|| { echo "live needs APCA_API_KEY_ID and APCA_API_SECRET_KEY in the environment" >&2; exit 2; }
	@$(DOCKER) build -f Dockerfile.check --target test -t $(CHECK) . >/dev/null 2>&1 \
		|| { echo "live FAILED to build" >&2; exit 1; }
	@mkdir -p recordings
	@docker run --rm $(KEY_ENV) -v "$(CURDIR)/recordings":/w/recordings $(CHECK) \
		python -m pytest -q -s --live -p no:cacheprovider tests/test_live.py

sample:
	@[ -n "$$APCA_API_KEY_ID" ] && [ -n "$$APCA_API_SECRET_KEY" ] \
		|| { echo "sample needs APCA_API_KEY_ID and APCA_API_SECRET_KEY in the environment" >&2; exit 2; }
	@$(DOCKER) build -f Dockerfile.check --target test -t $(CHECK) . >/dev/null 2>&1 \
		|| { echo "sample FAILED to build" >&2; exit 1; }
	@mkdir -p recordings
	@docker run --rm $(KEY_ENV) -v "$(CURDIR)/recordings":/w/recordings $(CHECK) \
		python tests/sample_live.py --symbol "$(SYMBOL)" --feed "$(FEED)" --raw-dir /w/recordings

# Applied in a container and written back, because the host has no toolchain.
fmt:
	@docker run --rm -v "$(CURDIR)":/w -w /w python:$(PY_VERSION)-slim \
		sh -c 'pip install -q ruff >/dev/null 2>&1; python -m ruff check --fix src tests >/dev/null; python -m ruff format src tests'
	@echo "fmt: applied"
