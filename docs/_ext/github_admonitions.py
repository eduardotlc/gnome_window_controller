"""
Render GitHub's ``> [!NOTE]`` alert blockquotes as Sphinx admonitions.

README.md and DEVELOPMENT.md are included into the documentation verbatim so there is one copy of
the prose rather than two that drift. They use GitHub's alert syntax, which MyST does not know:
without this it reaches the page as a blockquote with a literal ``[!NOTE]`` in the text.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar

from docutils import nodes
from sphinx.transforms import SphinxTransform

if TYPE_CHECKING:
    from sphinx.application import Sphinx

__all__ = ["GitHubAlertTransform", "setup"]

#: Alert name to the docutils admonition it becomes. These are the five GitHub defines.
ALERTS: dict[str, type[nodes.Admonition]] = {
    "NOTE": nodes.note,
    "TIP": nodes.tip,
    "IMPORTANT": nodes.important,
    "WARNING": nodes.warning,
    "CAUTION": nodes.caution,
}

#: Leading marker, whether the text follows on the same line or the next one.
MARKER = re.compile(r"^\[!(" + "|".join(ALERTS) + r")\]\s*", re.IGNORECASE)


class GitHubAlertTransform(SphinxTransform):
    """
    Convert alert-marked blockquotes into admonition nodes.

    Attributes
    ----------
    default_priority : int
        Runs after MyST has produced the doctree and before writing.

    """

    default_priority: ClassVar[int] = 500

    def apply(self, **kwargs: Any) -> None:
        """
        Rewrite every alert blockquote in the current document.

        Parameters
        ----------
        **kwargs : Any
            Unused; part of the transform interface.

        """
        for quote in list(self.document.findall(nodes.block_quote)):
            first = next(iter(quote.findall(nodes.paragraph)), None)
            if first is None or not first.children:
                continue

            head = first.children[0]
            if not isinstance(head, nodes.Text):
                continue

            match = MARKER.match(head.astext())
            if match is None:
                continue

            # Drop the marker, keeping whatever followed it on the same line. Going through
            # replace/remove rather than assigning into `children` matters: only the element API
            # parents the new node, and an unparented Text node crashes the smart-quotes
            # transform later in the pipeline.
            remainder = head.astext()[match.end() :]
            if remainder:
                first.replace(head, nodes.Text(remainder))
            else:
                first.remove(head)

            admonition = ALERTS[match.group(1).upper()]()
            admonition += quote.children
            quote.replace_self(admonition)


def setup(app: Sphinx) -> dict[str, Any]:
    """
    Register the transform.

    Parameters
    ----------
    app : sphinx.application.Sphinx
        The Sphinx application being configured.

    Returns
    -------
    dict
        Extension metadata; the transform is safe for parallel reads and writes.

    """
    app.add_transform(GitHubAlertTransform)
    return {
        "version": "1.0",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
