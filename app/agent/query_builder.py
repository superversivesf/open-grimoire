"""FTS5 query construction: sanitization, stop-words, synonyms, fallback cascade."""
import re

STOP_WORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
    "at", "it", "is", "are", "was", "were", "be", "been", "being", "do",
    "does", "did", "can", "could", "would", "should", "will", "what", "how",
    "why", "when", "where", "which", "who", "i", "me", "my", "we", "you",
    "your", "not", "no", "as", "by", "from", "up", "down", "there", "that",
    "this", "these", "those", "about", "into", "than", "then", "if", "s",
}

SYNONYM_GROUPS = {
    "ac": {"ac", "armor class", "armour class"},
    "hp": {"hp", "hit points", "hit point"},
    "st": {"st", "save", "saves", "saving", "saving throw", "saving throws"},
    "dc": {"dc", "difficulty class", "difficulty classes"},
    "xp": {"xp", "experience points", "experience point"},
    "init": {"init", "initiative"},
    "tohit": {"tohit", "to hit", "attack roll", "attack rolls", "attack bonus"},
    "proficiency": {"proficiency", "proficiencies", "proficient", "prof", "proficiency bonus"},
    "spell": {"spell", "spellcasting", "slot", "slots", "cantrip", "spell slot", "spell slots"},
    "feat": {"feat", "feats"},
    "advantage": {"advantage", "adv", "disadvantage"},
    "concentration": {"concentration", "concentrating"},
    "ability": {"str", "dex",
                "strength", "dexterity", "constitution", "intelligence",
                "wisdom", "charisma"},
    "dmg": {"dmg", "damage", "damages"},
    "crit": {"crit", "critical", "critical hit", "critical hits"},
}

_TERM_RE = re.compile(r"[^a-z0-9]+")
_ATOMIC_RE = re.compile(r"\d+\.\d+e\b|\d+\.\d+|\d+[dD]\d+|[1-9]e\b", re.IGNORECASE)


def tokenize_terms(query: str) -> list[str]:
    """Split a raw query into clean lowercase terms, dropping stop words.

    Edition/dice tokens (3.5, 1d20, 5e) are protected from splitting and kept
    atomic; single-digit tokens are dropped (page-number/CR noise).
    """
    lowered = query.lower()
    atoms: dict[str, str] = {}

    def protect(m: re.Match[str]) -> str:
        key = f"zzat{len(atoms)}zz"
        atoms[key] = m.group(0)
        return key

    protected = _ATOMIC_RE.sub(protect, lowered)
    words = [w for w in _TERM_RE.split(protected) if w]
    out = []
    for w in words:
        if w in atoms:
            out.append(atoms[w])
        elif not (w.isdigit() and len(w) == 1):
            out.append(w)
    return [w for w in out if w not in STOP_WORDS]


def _group_lookup() -> dict[str, set[str]]:
    """Normalized member string -> group. Multi-word members keyed with single spaces."""
    lookup: dict[str, set[str]] = {}
    for group in SYNONYM_GROUPS.values():
        for member in group:
            key = " ".join(member.lower().split())
            lookup.setdefault(key, set(group))
    return lookup


_GROUP_LOOKUP = _group_lookup()


def expand_terms(terms: list[str], extra_synonyms: dict[str, list[str]] | None = None) -> list[set[str]]:
    """Expand each term (or adjacent term pair forming a known synonym phrase)
    into an OR-set of synonym tokens.

    extra_synonyms maps a term to additional per-collection keyword matches.
    """
    expanded: list[set[str]] = []
    i = 0
    while i < len(terms):
        term = terms[i]
        group = None
        ngram_consumed = False
        if i + 1 < len(terms) and " " not in term and " " not in terms[i + 1]:
            pair = f"{term} {terms[i + 1]}"
            if pair in _GROUP_LOOKUP:
                group = set(_GROUP_LOOKUP[pair])
                ngram_consumed = True
        if group is None:
            key = " ".join(term.lower().split())
            group = set(_GROUP_LOOKUP[key]) if key in _GROUP_LOOKUP else {term}
        for extra in (extra_synonyms or {}).get(term, []):
            group.add(extra)
        expanded.append(group)
        i += 2 if ngram_consumed else 1
    return expanded


def _quote(token: str) -> str | None:
    clean = re.sub(r"\s+", " ", token.lower().strip())
    if not clean or re.search(r'["\x00]', clean):
        return None
    return f'"{clean}"'


def build_and_query(expanded: list[set[str]]) -> str:
    """Implicit AND between term groups; OR inside each group."""
    groups = []
    for members in expanded:
        quoted = [q for m in sorted(members) if (q := _quote(m))]
        if quoted:
            groups.append("(" + " OR ".join(quoted) + ")")
    return " AND ".join(groups)


def build_or_query(expanded: list[set[str]], prefix: bool = False) -> str:
    """Everything OR'd; optional prefix wildcards on terms >= 4 chars."""
    tokens = set()
    for members in expanded:
        for m in members:
            q = _quote(m)
            if not q:
                continue
            if prefix and len(m) >= 4 and " " not in q:
                q = f"{q}*"
            tokens.add(q)
    return " OR ".join(sorted(tokens))


_PREFIX_BLACKLIST = {"feat", "init"}


def _allow_prefix(expanded: list[set[str]]) -> bool:
    """Prefix wildcards only for a single term whose stem is not a common
    English prefix (feat* -> feature, init* -> initial)."""
    if len(expanded) != 1:
        return False
    return not any(m in _PREFIX_BLACKLIST for m in expanded[0])


def build_query_cascade(terms: list[str], extra_synonyms: dict[str, list[str]] | None = None) -> list[str]:
    """Return FTS5 queries from strictest to loosest.

    1. AND of synonym groups (stop words removed)
    2. OR of everything
    3. OR of everything with prefix wildcards (single terms only)
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
    if _allow_prefix(expanded):
        prefix_query = build_or_query(expanded, prefix=True)
        if prefix_query not in cascade:
            cascade.append(prefix_query)
    return cascade
