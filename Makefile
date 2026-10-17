PYTHON ?= python

.PHONY: test release release-windows android clean

test:
	$(PYTHON) -m pytest -q
	$(PYTHON) -m black --check tests modaud.py make_release.py

release: release-windows

release-windows: test
	$(PYTHON) make_release.py

android:
	@echo "Android build is not configured yet; choose the app framework and build command first."
	@exit 2

clean:
	$(PYTHON) make_release.py clean
