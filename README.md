# Xerox

Xerox is a UI-first local website cloner.

You run one install command, one launch command, and then work from a local browser UI:
- paste a URL
- choose `page` or `crawl`
- watch live logs
- open the cloned result
- download discovered links
- revisit local job history

![Xerox UI](docs/screenshots/xerox-ui.png)

## Supported Setup

- macOS terminal
- Linux bash
- Windows PowerShell
- Windows Command Prompt
- Python `3.10+`

## Modes

- `page`: clone one page and the assets needed to reproduce it locally
- `crawl`: start from one URL, follow internal navigation, and rewrite local links between saved pages

## Quick Start

### macOS / Linux

```bash
git clone https://github.com/legitsdev/Xerox.git
cd Xerox
./install.sh
./xerox
```

### Windows PowerShell

```powershell
git clone https://github.com/legitsdev/Xerox.git
cd Xerox
.\install.ps1
.\xerox.ps1
```

### Windows Command Prompt

```bat
git clone https://github.com/legitsdev/Xerox.git
cd Xerox
install.bat
xerox.bat
```

If you prefer SSH:

```bash
git clone git@github.com:legitsdev/Xerox.git
```

## What The Installer Does

- picks a working Python `3.10+` interpreter
- creates a repo-local `.venv`
- upgrades `pip`
- installs Xerox in editable mode
- installs Playwright Chromium
- runs a smoke check

## What The Launcher Does

- starts the Xerox local web UI
- uses `127.0.0.1:4173` or the next free port
- opens the dashboard in your browser automatically
- runs from the repo-local `.venv` without manual activation

## Repo Commands

Main flow:

```bash
./install.sh
./xerox
```

Secondary Python entrypoint:

```bash
python -m xerox --no-open
```

Editable install into the current environment:

```bash
pip install -e .
```

## Local Data

Xerox stores job history and outputs in your OS user data folder.

Typical locations:
- macOS: `~/Library/Application Support/xerox`
- Linux: `~/.local/share/xerox`
- Windows: `%APPDATA%\xerox`

Each job stores:
- cloned site files
- `site_report.txt`
- `result.json`
- `job.log`
- `found_links.txt`

## Troubleshooting

### `Python 3.10+ was not found`

Install Python 3.10 or newer, then rerun the install command for your OS.

On macOS/Linux, if you already know the exact interpreter path you want to use:

```bash
XEROX_PYTHON=/path/to/python3.12 ./install.sh
```

### `Playwright Chromium is not installed` or browser startup fails

Rerun the installer first.

Manual recovery:

```bash
./.venv/bin/python -m playwright install chromium
```

On Windows:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

### Linux system packages

Some Linux distros require extra OS libraries for Playwright.
If the installer prints missing package errors, install the packages requested by Playwright and rerun `./install.sh`.

## Notes

- Some sites use anti-bot or challenge pages. Xerox includes waits and browser fingerprint adjustments, but no site can be guaranteed.
- Large or highly dynamic sites may still need tuning.
- `crawl` is intentionally conservative and does not try to submit forms or authenticate.

## License

MIT
