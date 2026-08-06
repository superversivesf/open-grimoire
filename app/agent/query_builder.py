"""FTS5 query construction: sanitization, stop-words, synonyms, fallback cascade."""
import re

STOP_WORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
    "at", "it", "is", "are", "was", "were", "be", "been", "being", "do",
    "does", "did", "can", "could", "would", "should", "will", "what", "how",
    "why", "when", "where", "which", "who", "i", "me", "my", "we", "you",
    "your", "not", "no", "as", "by", "from", "up", "down", "there", "that",
    "this", "these", "those", "about", "into", "than", "then", "if",
}

SYNONYM_GROUPS = {
    "ac": {"ac", "armor class", "armour class"},
    "hp": {"hp", "hit points", "hit point"},
    "st": {"st", "save", "saves", "saving throw", "saving throws"},
    "dc": {"dc", "difficulty class", "difficulty classes"},
    "xp": {"xp", "experience points", "experience point"},
    "init": {"init", "initiative"},
    "tohit": {"tohit", "to hit", "attack roll", "attack rolls", "attack bonus"},
    "proficiency": {"proficiency", "proficiencies", "proficient"},
    "spell": {"spell", "spellcasting"},
    "feat": {"feat", "feats"},
}

_TERM_RE = re.compile(r"[^a-z0-9]+")


def tokenize_terms(query: str) -> list[str]:
    """Split a raw query into clean lowercase terms, dropping stop words."""
    lowered = query.lower()
    words = [w for w in _TERM_RE.split(lowered) if w]
    return [w for w in words if w not in STOP_WORDS]


def _group_for(term: str) -> set | None:
    for group in SYNONYM_GROUPS.values():
        if term in group:
            return group
    return None


def expand_terms(terms: list[str], extra_synonyms: dict[str, list[str]] | None = None) -> list[set[str]]:
    """Expand each term into an OR-set of synonym tokens.

    extra_synonyms maps a term to additional per-collection keyword matches.
    """
    expanded = []
    for term in terms:
        group = _group_for(term)
        members = set(group) if group else {term}
        for extra in (extra_synonyms or {}).get(term, []):
            members.add(extra)
        expanded.append(members)
    return expanded


def _quote(token: str) -> str | None:
    clean = _TERM_RE.sub(" ", token).strip()
    return f'"{clean}"' if clean else None


def build_and_query(expanded: list[set[str]]) -> str:
    """Implicit AND between term groups; OR inside each group."""
    groups = []
    for members in expanded:
        quoted = [q for m in sorted(members) if (q := _quote(m))]
        if quoted:
            groups.append("(" + " OR ".join(quoted) + ")")
    return " ".join(groups)


def build_or_query(expanded: list[set[str]], prefix: bool = False) -> str:
    """Everything OR'd; optional prefix wildcards on terms >= 4 chars."""
    tokens = set()
    for members in expanded:
        for m in members:
            q = _quote(m)
            if not q:
                continue
            if prefix and len(m) >= 4:
                q = f"{q}*"
            tokens.add(q)
    return " OR ".join(sorted(tokens))


def build_query_cascade(terms: list[str], extra_synonyms: dict[str, list[str]] | None = None) -> list[str]:
    """Return FTS5 queries from strictest to loosest.

    1. AND of synonym groups (stop words removed)
    2. OR of everything
    3. OR of everything with prefix wildcards
    """
    if not terms:
        return []
    terms = [t for t in terms if t not in STOP_WORDS]
    if not terms:
        return []
    expanded = expand_terms(terms, extra_synonyms)
    and_query = build_and_query(expanded)
    if not and_query:
        return []
    cascade = [and_query]
    or_query = build_or_query(expanded)
    if or_query not in cascade:
        cascade.append(or_query)
    prefix_query = build_or_query(expanded, prefix=True)
    if prefix_query not in cascade:
        cascade.append(prefix_query)
    return cascade
