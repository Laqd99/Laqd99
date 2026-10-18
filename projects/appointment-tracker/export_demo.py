"""Export the appointment UI as a self-contained, browser-local demo."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_ROOT.parents[1] / "docs" / "index.html"


def inline_source(source: str, tag: str) -> str:
    """Keep a source string from prematurely closing its HTML container."""
    return re.sub(rf"</{tag}", lambda match: "<\\/" + match.group()[2:], source, flags=re.I)


def export_demo(output: Path) -> Path:
    web = PROJECT_ROOT / "web"
    markup = (web / "index.html").read_text(encoding="utf-8")
    stylesheet = inline_source((web / "styles.css").read_text(encoding="utf-8"), "style")
    adapter = inline_source((web / "demo-store.js").read_text(encoding="utf-8"), "script")
    application = inline_source((web / "app.js").read_text(encoding="utf-8"), "script")
    replacements = {
        '<link rel="stylesheet" href="/styles.css">': f"<style>\n{stylesheet}\n</style>",
        '    <script src="/app.js" defer></script>\n': "",
        'href="/" aria-label="Care Schedule home"': 'href="#main-content" aria-label="Care Schedule home"',
        '<title>Care Schedule · Appointment tracker</title>': '<title>Care Schedule · Browser demo</title>',
        'Built with Python &amp; a focus on the details.': 'Browser demo · fictional data stays in your browser.',
        '</body>': f"<script>\n{adapter}\n</script>\n<script>\n{application}\n</script>\n  </body>",
    }
    for original, replacement in replacements.items():
        if markup.count(original) != 1:
            raise ValueError(f"Expected exactly one export marker: {original}")
        markup = markup.replace(original, replacement, 1)
    policy = (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"
    )
    markup = markup.replace(
        '<meta charset="utf-8">',
        '<meta charset="utf-8">\n    '
        f'<meta http-equiv="Content-Security-Policy" content="{policy}">\n    '
        '<link rel="icon" href="data:,">',
        1,
    )
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markup, encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    try:
        output = export_demo(arguments.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Could not export browser demo: {error}\n")
    print(f"Browser demo saved to {output}")


if __name__ == "__main__":
    main()
