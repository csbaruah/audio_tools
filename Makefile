PYTHON ?= python

.PHONY: test release release-windows android clean

test:
	$(PYTHON) scripts/run_tests.py --python "$(PYTHON)"

release: release-windows

release-windows: test
	$(PYTHON) make_release.py

android:
	@echo "Android build is not configured yet; choose the app framework and build command first."
	@exit 2

clean:
	$(PYTHON) make_release.py clean
