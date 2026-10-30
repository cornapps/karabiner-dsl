"""Source provenance carried beside, never inside, generated Karabiner JSON."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceLocation:
    line: int
    column: int = 1
    path: str | None = None


@dataclass(frozen=True)
class MacroCall:
    name: str
    call: SourceLocation | None
    definition: SourceLocation | None


@dataclass(frozen=True)
class Origin:
    source: SourceLocation | None
    unit_id: str | None = None
    # For expanded actions: outer call, nested calls, definition header/step.
    related: tuple[SourceLocation, ...] = ()
    macro_calls: tuple[MacroCall, ...] = ()


@dataclass
class SourceUnit:
    id: str
    kind: str
    source: SourceLocation
    group_id: str | None = None
    declarations: list[SourceLocation] = field(default_factory=list)

    @property
    def position(self):
        return (self.source.line, self.source.column)


class SourceRecord(dict):
    """IR dictionary whose provenance survives deepcopy without becoming a key."""

    def __init__(self, value=(), *, origin=None):
        super().__init__(value)
        self.origin = origin


def origin_of(record):
    return getattr(record, "origin", None) or Origin(None)


def carry_origin(value, source):
    return SourceRecord(value, origin=origin_of(source))


@dataclass(frozen=True)
class ActionOrigin:
    # Field path relative to the manipulator, including array indices.
    path: tuple[str | int, ...]
    origin: Origin
    generator: str


@dataclass(frozen=True)
class ManipulatorOrigin:
    index: int
    rule_index: int
    manipulator_index: int
    origin: Origin
    generator: str
    internal_order: int
    actions: tuple[ActionOrigin, ...] = ()
    # Repeated legacy sublayers share one generated activation. All their
    # distinct source units are retained here; origin is the first header.
    contributing_units: tuple[str, ...] = ()

    @property
    def unit_id(self):
        return self.origin.unit_id

    @property
    def source(self):
        return self.origin.source

    @property
    def known(self):
        return self.source is not None
