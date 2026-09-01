# Minimal makefile for Sphinx documentation.

SPHINXOPTS    ?= -n
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build

.PHONY: help html strict serve clean Makefile

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

# Warnings are errors: a broken cross-reference or a missing include should fail the build.
strict:
	@$(SPHINXBUILD) -b html "$(SOURCEDIR)" "$(BUILDDIR)/html" -n -W --keep-going $(O)

serve: html
	@python -m http.server --directory "$(BUILDDIR)/html" 8000

%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
