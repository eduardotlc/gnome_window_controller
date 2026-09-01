"""
Sphinx configuration for the gnome_window_controller documentation.

The package imports PyGObject lazily, so every module here can be imported -- and therefore
documented -- on a builder with no GNOME, no session bus and no ``gi`` installed. Nothing needs
mocking.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from importlib.metadata import version as installed_version
from pathlib import Path

DOCS = Path(__file__).parent
sys.path.insert(0, str(DOCS / "_ext"))

# -- Project ---------------------------------------------------------------------------------

project = "Gnome Window Controller"
author = "eduardotlc"
copyright = f"{datetime.now(tz=UTC):%Y}, {author}"
release = installed_version("gnome-window-controller")
version = ".".join(release.split(".")[:2])

# -- General ---------------------------------------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinx_copybutton",
    "sphinxarg.ext",
    "github_admonitions",
]

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
templates_path = ["_templates"]

# -- Markdown --------------------------------------------------------------------------------

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "linkify",
    "smartquotes",
    "substitution",
    "tasklist",
]
myst_heading_anchors = 3

# -- Autodoc ---------------------------------------------------------------------------------

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
}
# Every docstring already documents its parameter types in a numpydoc Parameters section, so
# repeating them in the signature is noise.
autodoc_typehints = "none"
autodoc_preserve_defaults = True
autosummary_generate = True

napoleon_google_docstring = False
napoleon_numpy_docstring = True
napoleon_use_rtype = False
# Attributes become :ivar: fields on the class rather than separate py:attribute objects, which
# is what stops autodoc and the numpydoc "Attributes" section describing each dataclass field
# twice.
napoleon_use_ivar = True
# Leave types as written. Preprocessing splits `dict[int, dict[str, Any]]` on its commas and then
# fails to resolve each fragment as a class.
napoleon_preprocess_types = False

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# Nitpicky mode is on for the strict build so a cross-reference that goes stale fails it. What
# follows is everything that can never resolve and is not a mistake, kept narrow on purpose: a
# broken reference to a real public name still has to be an error.
nitpick_ignore_regex = [
    # PyGObject is absent here by design and ships no object inventory.
    ("py:.*", r"gi\..*"),
    # Private helpers are referenced from docstrings but deliberately not documented.
    ("py:.*", r".*\b_[a-z].*"),
    # numpydoc type *prose*, which reads well but is not a type: "int, optional",
    # "bool, default True", "sequence of str", "file-like".
    ("py:.*", r"optional"),
    ("py:.*", r"[Dd]efault .*"),
    ("py:.*", r"sequence|iterable|callable|file-like"),
    # Choice sets such as {"auto", "always", "never"}, which napoleon splits on the commas.
    ("py:.*", r'[^a-zA-Z]*["{}].*'),
    # Module constants named as a parameter's type, e.g. "default MONITOR_ORDER". CamelCase
    # class names are unaffected, so a genuinely broken class reference still fails.
    ("py:.*", r"_?[A-Z][A-Z0-9_]+"),
]

# -- HTML ------------------------------------------------------------------------------------

html_theme = "furo"
html_title = f"{project} {release}"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_copy_source = False
html_show_sphinx = False

# The accent is the extension's own default border colour, so the docs and the highlight it
# documents look like the same project.
_ACCENT = "#993c5a"
_ACCENT_LIGHT = "#c4718c"

html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
    "light_css_variables": {
        "color-brand-primary": _ACCENT,
        "color-brand-content": _ACCENT,
        "color-admonition-title--note": _ACCENT,
        "font-stack--monospace": "'JetBrains Mono', 'Fira Code', ui-monospace, monospace",
    },
    "dark_css_variables": {
        "color-brand-primary": _ACCENT_LIGHT,
        "color-brand-content": _ACCENT_LIGHT,
        "color-admonition-title--note": _ACCENT_LIGHT,
        "font-stack--monospace": "'JetBrains Mono', 'Fira Code', ui-monospace, monospace",
    },
    "source_repository": "https://github.com/eduardotlc/gnome_window_controller/",
    "source_branch": "main",
    "source_directory": "docs/",
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/eduardotlc/gnome_window_controller",
            "html": (
                '<svg stroke="currentColor" fill="currentColor" stroke-width="0" '
                'viewBox="0 0 16 16"><path fill-rule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 '
                "3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 "
                "0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01"
                "1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 "
                "0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 "
                "1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 "
                "2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 "
                '1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.012 8.012 0 0 0 16 8c0-4.42-3.58-8-8-8z">'
                "</path></svg>"
            ),
            "class": "",
        },
    ],
}

# -- Copy button -----------------------------------------------------------------------------

copybutton_prompt_text = r"\$ |>>> |\.\.\. "
copybutton_prompt_is_regexp = True
