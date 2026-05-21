-include .env
export

.PHONY: requirements
requirements:
	$(MAKE) -C requirements compile

.PHONY: install
install:
	$(MAKE) -C requirements sync

.PHONY: setup-notice
setup-notice:
	uv run scripts/setup-notice.py

.PHONY: dev
dev:
	uv run scripts/fishing-conditions-notice.py --dev

.PHONY: clean
clean:
	rm -rf build
