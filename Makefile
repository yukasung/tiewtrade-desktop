.PHONY: run verify build clean-build sign-build notarize check-signature

run:
	PYTHONPATH=src .venv/bin/python -m tiewtrade.desktop_main

# Everything that has to pass before a build is worth making.
verify:
	PYTHONPATH=src .venv/bin/python -m pytest -q
	.venv/bin/python -m ruff check src tests
	.venv/bin/python -m ruff format --check src tests
	.venv/bin/python -m mypy
	git diff --check
	PYTHONPATH=src .venv/bin/python -m tiewtrade.paper_replay_main \
	  tests/fixtures/btcusdt_5m_tracer.csv \
	  --capital-per-entry 250 \
	  --max-entries 4

# Leaves dist/TiewTrade.app, ad-hoc signed: it runs on this machine and
# nowhere else. Runs verify first, because a bundle built from code that does
# not pass is a bundle nobody can trust.
build: verify clean-build
	.venv/bin/python -m PyInstaller \
	  --noconfirm \
	  --distpath dist \
	  --workpath build \
	  packaging/tiewtrade.spec

clean-build:
	rm -rf build dist

# A bundle other machines will open. Needs a Developer ID Application
# certificate in the login keychain; `security find-identity -v -p codesigning`
# lists the name to pass.
#
#   make sign-build TIEWTRADE_SIGN_IDENTITY="Developer ID Application: NAME (TEAMID)"
#
# PyInstaller signs every collected binary as it builds; the bundle itself has
# to be signed afterwards, and only that outer signature carries the Hardened
# Runtime that notarization requires.
sign-build:
	@test -n "$(TIEWTRADE_SIGN_IDENTITY)" || \
	  { echo "TIEWTRADE_SIGN_IDENTITY is not set — see the comment above this target in the Makefile"; exit 1; }
	TIEWTRADE_SIGN_IDENTITY=$(TIEWTRADE_SIGN_IDENTITY) $(MAKE) build
	codesign --force --timestamp --options runtime \
	  --entitlements packaging/entitlements.plist \
	  --sign "$(TIEWTRADE_SIGN_IDENTITY)" \
	  dist/TiewTrade.app
	$(MAKE) check-signature

check-signature:
	codesign --verify --deep --strict --verbose=2 dist/TiewTrade.app
	spctl --assess --type exec --verbose=4 dist/TiewTrade.app

# Apple has to see the bundle before other machines will trust it. Store the
# credentials once, then pass the profile name:
#
#   xcrun notarytool store-credentials tiewtrade \
#     --apple-id APPLE_ID --team-id TEAMID --password APP_SPECIFIC_PASSWORD
#   make notarize TIEWTRADE_NOTARY_PROFILE=tiewtrade
#
# Stapling writes the ticket into the bundle so it opens offline too.
notarize:
	@test -n "$(TIEWTRADE_NOTARY_PROFILE)" || \
	  { echo "TIEWTRADE_NOTARY_PROFILE is not set — see the comment above this target in the Makefile"; exit 1; }
	@test -d dist/TiewTrade.app || \
	  { echo "dist/TiewTrade.app is missing — run make sign-build first"; exit 1; }
	ditto -c -k --keepParent dist/TiewTrade.app dist/TiewTrade.zip
	xcrun notarytool submit dist/TiewTrade.zip \
	  --keychain-profile "$(TIEWTRADE_NOTARY_PROFILE)" \
	  --wait
	xcrun stapler staple dist/TiewTrade.app
	xcrun stapler validate dist/TiewTrade.app
	$(MAKE) check-signature
