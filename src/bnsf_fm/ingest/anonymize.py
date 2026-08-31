"""Strip co-worker identities at the door.

Corrigo exports carry technician identity in whatever form the tenant
configured — sometimes an employee id, sometimes a full name in an
"Assigned To" column. Either way it arrives, it stops here.

`Roster.identify` turns that raw value into an opaque surrogate id and
**discards the original**. Only the person running the tool keeps a real name.
The result is that the local database physically cannot leak a co-worker's
identity: not hidden behind a flag, not one CLI typo away from disclosure —
simply not present.

Two decisions worth stating, because both look like oversights otherwise:

**The surrogate id is an unsalted hash.** A salt would have to live in the
same database as the hashes it protects, so anyone able to read the hashes
could read the salt. It would add ceremony and no security. What actually
protects people here is that the name was never written down.

**Sequential labels are allocated once and persisted**, not derived by sorting
on each load. If labels were recomputed, a single new hire would renumber
everyone — "Tech 3" would silently mean a different person than it did last
month, quietly invalidating every comparison made over time. See
`Store.assign_labels`, which allocates only to technicians that do not already
have a label.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# Long enough that a collision across a facility's roster is not a practical
# concern, short enough to stay readable in a database row.
SURROGATE_LENGTH = 16


def normalize_identity(raw: str) -> str:
    """Fold a name or employee id to a stable matching key.

    Handles the spelling drift between Corrigo screens: "Shaw, Bryan" and
    "Bryan  Shaw" and "bryan shaw" must all resolve to one person, or that
    person is silently split across several rows in every report.
    """
    text = re.sub(r"\s+", " ", raw.strip().lower())
    if "," in text:
        # "Last, First" -> "first last"
        parts = [p.strip() for p in text.split(",", 1)]
        if len(parts) == 2 and parts[1]:
            text = f"{parts[1]} {parts[0]}"
    # Drop anything that is not a letter, digit or single space: punctuation,
    # employee-id prefixes with dashes, trailing role suffixes in parentheses.
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def surrogate_id(raw: str) -> str:
    """Opaque, stable id derived from a name or employee id."""
    key = normalize_identity(raw)
    return hashlib.sha256(key.encode()).hexdigest()[:SURROGATE_LENGTH]


@dataclass
class Identity:
    """The anonymized result of looking up one raw technician value."""

    id: str
    is_self: bool
    name: str | None  # populated only when is_self is True


@dataclass
class Roster:
    """Maps raw technician values to surrogate ids, discarding names.

    `me` is matched against the raw value in the export — pass a full name or
    an employee id, whichever appears in the technician column. Matching is
    normalized, so "Shaw, Bryan" in the export matches `--me "Bryan Shaw"`.

    `my_display_name` is what reports show for you. It defaults to whatever
    you passed as `me`, which is the least surprising behavior.
    """

    me: str | None = None
    my_display_name: str | None = None
    _seen: dict[str, Identity] = field(default_factory=dict, init=False)
    _self_matched: bool = field(default=False, init=False)

    @property
    def my_id(self) -> str | None:
        return surrogate_id(self.me) if self.me else None

    @property
    def matched_self(self) -> bool:
        """Whether any row in the data actually matched `me`.

        Checked after a load: a `--me` value that matched nothing is almost
        always a spelling mismatch against the export, and silently producing
        a scorecard with no "you" in it would be worse than saying so.
        """
        return self._self_matched

    def identify(self, raw: str | None) -> Identity | None:
        """Resolve one raw technician value. Returns None for blanks."""
        if raw is None:
            return None
        text = raw.strip()
        if not text or normalize_identity(text) == "":
            return None

        key = normalize_identity(text)
        cached = self._seen.get(key)
        if cached is not None:
            return cached

        is_self = self.me is not None and key == normalize_identity(self.me)
        if is_self:
            self._self_matched = True
        identity = Identity(
            id=surrogate_id(text),
            is_self=is_self,
            # This is the line that does the work: for anyone but you, the
            # real value is dropped rather than carried forward.
            name=(self.my_display_name or self.me or text) if is_self else None,
        )
        self._seen[key] = identity
        return identity

    def distinct_count(self) -> int:
        return len(self._seen)
