"""Sphinx configuration for AgentOBS documentation."""
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

from __future__ import annotations

import sys
from pathlib import Path

# Make the source package importable from docs/
sys.path.insert(0, str(Path("..").resolve()))

from tracium import __version__

# ---------------------------------------------------------------------------
# Project information
# ---------------------------------------------------------------------------

project = "AgentOBS"
copyright = "2026, AgentOBS Contributors"
author = "AgentOBS Contributors"
release = __version__
version = __version__

# ---------------------------------------------------------------------------
# General configuration
# ---------------------------------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",        # auto-generate API docs from docstrings
    "sphinx.ext.autosummary",    # summary tables for modules/classes
    "sphinx.ext.napoleon",       # Google-style + NumPy-style docstring support
    "sphinx.ext.viewcode",       # "Source" links on every API page
    "sphinx.ext.intersphinx",    # cross-links to Python stdlib docs
    "sphinx.ext.todo",           # .. todo:: directives
    "sphinx.ext.githubpages",    # CNAME + .nojekyll for GitHub Pages
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# ---------------------------------------------------------------------------
# Napoleon (docstring style)
# ---------------------------------------------------------------------------

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_use_ivar = True
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_preprocess_types = True

# ---------------------------------------------------------------------------
# Autodoc
# ---------------------------------------------------------------------------

autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__, __bool__, __len__",
    "undoc-members": False,
    "exclude-members": "__weakref__, __dict__, __slots__",
    "show-inheritance": True,
}
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"
autosummary_generate = True

# ---------------------------------------------------------------------------
# Intersphinx
# ---------------------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# ---------------------------------------------------------------------------
# HTML output — pydata-sphinx-theme (Apache / NumPy / Pandas style)
# ---------------------------------------------------------------------------

html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]
html_logo = None  # set to "_static/logo.png" when a logo is available

html_theme_options = {
    "show_toc_level": 2,
    "navbar_align": "left",
    "header_links_before_dropdown": 6,
    "navigation_with_keys": True,
    "show_nav_level": 2,
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/veerarag1973/agentobs",
            "icon": "fa-brands fa-github",
            "type": "fontawesome",
        },
        {
            "name": "PyPI",
            "url": "https://pypi.org/project/agentobs/",
            "icon": "fa-solid fa-box",
            "type": "fontawesome",
        },
    ],
    "logo": {
        "text": "AgentOBS",
        "alt_text": "AgentOBS",
    },
    "footer_start": ["copyright"],
    "footer_end": ["sphinx-version"],
    "pygments_light_style": "tango",
    "pygments_dark_style": "monokai",
}

html_context = {
    "github_user": "veerarag1973",
    "github_repo": "agentobs",
    "github_version": "main",
    "doc_path": "docs",
}

# ---------------------------------------------------------------------------
# Miscellaneous
# ---------------------------------------------------------------------------

todo_include_todos = True
nitpicky = False
