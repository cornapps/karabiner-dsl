"""Bounded containment proofs over the winner oracle's native predicates.

Only True is a coverage proof. False establishes noncoverage for an
unconditional later predicate; None means this bounded analysis has no proof.
In particular it does not solve arbitrary regex relationships, conjunction
satisfiability, temporal recognition, or unions of earlier mapping units.
Actions and lifecycle payloads are opaque, as in matching.match_manipulator.
"""

from dataclasses import asdict
from functools import lru_cache
from itertools import combinations

from .matching import _EVENT_KEYS, _MODIFIERS, _modifiers, match_manipulator


# This is the complete, small physical modifier domain of the shared matcher.
# Enumerating it reuses its acceptance semantics (including generic families),
# rather than maintaining a second implementation of modifier precedence.
_PHYSICAL = tuple(sorted(_MODIFIERS))
_MODIFIER_EVENTS = tuple(
    {"modifiers": list(items)}
    for size in range(len(_PHYSICAL) + 1)
    for items in combinations(_PHYSICAL, size)
)


@lru_cache(maxsize=256)
def _modifier_acceptance(mandatory, optional):
    spec = {"mandatory": list(mandatory), "optional": list(optional)}
    return sum(1 << index for index, event in enumerate(_MODIFIER_EVENTS)
               if _modifiers(spec, event)["status"] == "match")


def _prepare(manipulator):
    # The oracle retains every reason even when another predicate is decisive.
    # Missing runtime facts are expected here; any other unknown reason means
    # an unsupported predicate, including in an otherwise true OR alternative.
    # Validate positive predicate shapes: negation adds an aggregate unknown
    # reason when its runtime fact is missing, which is not a schema failure.
    probe = manipulator
    if isinstance(manipulator, dict) and isinstance(manipulator.get("conditions", []), list):
        positive_kinds = {"variable_unless": "variable_if", "device_unless": "device_if",
                          "frontmost_application_unless": "frontmost_application_if"}
        probe = {**manipulator, "conditions": [
            {**condition, "type": positive_kinds[condition["type"]]}
            if (isinstance(condition, dict) and isinstance(condition.get("type"), str)
                and condition["type"] in positive_kinds) else condition
            for condition in manipulator.get("conditions", [])]}
    # Even a supplied simultaneous list cannot resolve temporal recognition.
    result = match_manipulator(probe, {"modifiers": [], "simultaneous": []}, {})
    if any(reason["status"] == "unknown"
           and reason["path"] not in result["missing_facts"]
           for reason in result["reasons"]):
        return None
    source = manipulator["from"]
    selector = next(key for key in _EVENT_KEYS if key in source)
    modifiers = source.get("modifiers", {})
    mask = _modifier_acceptance(tuple(modifiers.get("mandatory", [])),
                                tuple(modifiers.get("optional", [])))
    return (selector, source[selector]), mask, manipulator.get("conditions", [])


def _same_value(left, right):
    return type(left) is type(right) and left == right


def _device_covers(earlier, later):
    # Alternatives are OR; literal fields within an identifier are AND.
    return all(any(all(key == "description" or (
        key in narrow and _same_value(value, narrow[key]))
        for key, value in broad.items()) for broad in earlier)
        for narrow in later)


def _device_disjoint(left, right):
    return all(any(key != "description" and key in b
                   and not _same_value(value, b[key])
                   for key, value in a.items())
               for a in left for b in right)


def _app_covers(earlier, later):
    # search("") and search(".*") accept every string, including newlines.
    # Anchored .* is deliberately not treated as universal. All patterns have
    # already passed the oracle's common-subset guard, including C++ brackets.
    return any(pattern in ("", ".*") for pattern in earlier) or set(later) <= set(earlier)


def _condition_covers(earlier, later):
    ekind, lkind = earlier["type"], later["type"]
    efamily, eneg = ekind.rsplit("_", 1)
    lfamily, lneg = lkind.rsplit("_", 1)
    if efamily != lfamily:
        return False  # No proof from this conjunct; not a disjointness claim.
    eneg, lneg = eneg == "unless", lneg == "unless"
    if efamily == "variable":
        if earlier["name"] != later["name"]:
            return False
        equal = _same_value(earlier["value"], later["value"])
        if eneg and not lneg:
            return not equal
        return eneg == lneg and equal
    if efamily == "device":
        evalues, lvalues = earlier["identifiers"], later["identifiers"]
        covers = _device_covers
        if eneg and not lneg:
            return _device_disjoint(evalues, lvalues)
    else:  # frontmost_application; unsupported families were rejected above.
        evalues = earlier.get("bundle_identifiers", [])
        lvalues = later.get("bundle_identifiers", [])
        covers = _app_covers
    if eneg != lneg:
        return False
    return covers(lvalues, evalues) if eneg else covers(evalues, lvalues)


def _universal(condition):
    return (condition["type"] == "frontmost_application_if"
            and any(pattern in ("", ".*")
                    for pattern in condition.get("bundle_identifiers", [])))


def _covers(earlier, later):
    if earlier is None or later is None:
        return None
    ekey, emodifiers, econditions = earlier
    lkey, lmodifiers, lconditions = later
    if ekey != lkey or lmodifiers & ~emodifiers:
        # Without solving conjunction satisfiability, a restricted later rule
        # might accept no contexts. Only claim a counterexample when it is free
        # of conditions; in that case the modifier mask supplies a witness.
        return False if not lconditions and lmodifiers else None
    # Each earlier AND conjunct must follow from a later conjunct, or be
    # universal itself. Extra later conjuncts can only narrow eligibility.
    if all(_universal(condition) or any(_condition_covers(condition, narrow)
                                      for narrow in lconditions)
           for condition in econditions):
        return True
    return None


def manipulator_covers(earlier, later):
    """Prove later eligibility implies earlier eligibility, or return uncertainty.

    Both arguments are native generated manipulator dictionaries. True proves
    containment across supported concrete event/context values. False is a
    proven counterexample for an unconditional later rule; None covers every
    unsupported predicate and every relationship outside the bounded proof.
    No missing variable is assigned a default value. Processing eligibility is
    shared by both rules at the complex-modification stage.
    """
    return _covers(_prepare(earlier), _prepare(later))


def find_shadowed(manipulators, origins=None):
    """Return JSON-serializable warnings in actual generated output order.

    origins is Compiler.origins, a flat parallel list of ManipulatorOrigin
    dataclasses. All alternatives of a unit must each have a proven coverer
    from ONE other unit, at a smaller generated index. Same-unit variants and
    shared contributing units never shadow themselves. Separate earlier units
    are not combined to prove coverage in this bounded implementation.

    Records contain unit_id, source ({line, column, path} or None), indices,
    scope (unit/manipulator), and covered_by. Each covered_by entry contains
    index, unit_id, source, and covers (the later indices it proves covered).
    Indices are zero-based. Without a known unit origin, each manipulator is
    reported separately with scope=manipulator and unit_id=None; this makes no
    whole-source-unit claim. Invalid parallel metadata raises ValueError.
    """
    manipulators = list(manipulators)
    if origins is None:
        origins = [None] * len(manipulators)
    else:
        origins = list(origins)
        if len(origins) != len(manipulators):
            raise ValueError("origins must be parallel to manipulators")
        if any(origin is not None and origin.index != index
               for index, origin in enumerate(origins)):
            raise ValueError("origin indices must match generated output order")

    def unit_ids(index):
        origin = origins[index]
        if origin is None:
            return set()
        return {unit for unit in (origin.unit_id, *origin.contributing_units)
                if unit is not None}

    def location(index):
        origin = origins[index]
        return {"unit_id": origin.unit_id if origin else None,
                "source": asdict(origin.source) if origin and origin.source else None}

    groups = {}
    for index, origin in enumerate(origins):
        key = ("unit", origin.unit_id) if origin and origin.unit_id is not None else ("index", index)
        groups.setdefault(key, []).append(index)
    prepared = [_prepare(manipulator) for manipulator in manipulators]
    warnings = []
    for key, indices in groups.items():
        later_units = set().union(*(unit_ids(index) for index in indices))
        for earlier_key, earlier_indices in groups.items():
            if earlier_indices[0] >= indices[0]:
                break
            if key == earlier_key or any(later_units & unit_ids(index) for index in earlier_indices):
                continue
            proof = {}
            for index in indices:
                covering = next((prior for prior in earlier_indices if prior < index
                                 and _covers(prepared[prior], prepared[index]) is True), None)
                if covering is None:
                    break
                proof.setdefault(covering, []).append(index)
            else:
                warnings.append({**location(indices[0]), "indices": indices,
                                 "scope": "unit" if key[0] == "unit" else "manipulator",
                                 "covered_by": [{"index": prior, **location(prior), "covers": covered}
                                                for prior, covered in proof.items()]})
                break
    return warnings
