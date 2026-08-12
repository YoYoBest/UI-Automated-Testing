from __future__ import annotations

from typing import Any
from threading import Lock

from .data_pool import ConstrainedGenerator, GlobalDataPool
from .models import FieldDefinition
from .validation_repair import generate_repair_value, parse_validation_message


class DataStrategy:
    pool: GlobalDataPool
    form_code: str
    _unique_reservation_lock = Lock()
    _unique_reservations: dict[
        tuple[str, tuple[str, ...]], set[tuple[str, ...]]
    ] = {}

    def value_for(self, field: FieldDefinition, index: int) -> Any:
        raise NotImplementedError

    def declared_unique_repair_fields(
        self, submitted: dict[str, Any],
    ) -> tuple[str, ...]:
        fields: list[str] = []
        for spec in getattr(self.pool, "unique_constraints", ()):
            if spec.form_code != self.form_code:
                continue
            if not all(submitted.get(code) not in (None, "", []) for code in spec.field_codes):
                continue
            if spec.repair_field not in fields:
                fields.append(spec.repair_field)
        return tuple(fields)

    def declared_unique_constraints(self, submitted: dict[str, Any] | None = None):
        specs = tuple(
            spec for spec in getattr(self.pool, "unique_constraints", ())
            if spec.form_code == self.form_code
        )
        if submitted is None:
            return specs
        return tuple(
            spec for spec in specs
            if all(submitted.get(code) not in (None, "", []) for code in spec.field_codes)
        )

    def declared_unique_constraint_for_message(
        self, message: str, submitted: dict[str, Any],
    ):
        matches = tuple(
            spec for spec in self.declared_unique_constraints(submitted)
            if spec.matches_message(message)
        )
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _normalized_unique_key(values: tuple[Any, ...]) -> tuple[str, ...]:
        return tuple(str(value or "").strip() for value in values)

    def unique_key_is_reserved(self, spec, values: tuple[Any, ...]) -> bool:
        bucket = (spec.form_code, tuple(spec.field_codes))
        normalized = self._normalized_unique_key(values)
        with self._unique_reservation_lock:
            return normalized in self._unique_reservations.get(bucket, set())

    def reserve_unique_key(self, spec, values: tuple[Any, ...]) -> None:
        bucket = (spec.form_code, tuple(spec.field_codes))
        normalized = self._normalized_unique_key(values)
        with self._unique_reservation_lock:
            self._unique_reservations.setdefault(bucket, set()).add(normalized)

    def unique_repair_field(
        self, message: str, submitted: dict[str, Any],
    ) -> str:
        matches = [
            spec.repair_field
            for spec in getattr(self.pool, "unique_constraints", ())
            if spec.form_code == self.form_code
            and spec.matches_message(message)
            and all(code in submitted for code in spec.field_codes)
        ]
        return matches[0] if len(set(matches)) == 1 else ""

    def allocate_unique_value(
        self, field: FieldDefinition, current_value: Any,
    ) -> tuple[Any, dict[str, Any]]:
        constraint = parse_validation_message("值已存在", field)
        return self._generate_unique_value(constraint, field, current_value)

    def _generate_unique_value(
        self, constraint, field: FieldDefinition, current_value: Any,
    ) -> tuple[Any, dict[str, Any]]:
        key = (self.form_code, field.field_code)
        sequences = getattr(self, "_unique_repair_sequences", None)
        if sequences is None:
            sequences = self._unique_repair_sequences = {}
        bases = getattr(self, "_unique_repair_bases", None)
        if bases is None:
            bases = self._unique_repair_bases = {}
        bases.setdefault(key, current_value)
        sequences[key] = sequences.get(key, 0) + 1
        run_id = getattr(getattr(self, "generator", None), "run_id", "repair")
        value = generate_repair_value(
            constraint, field, bases[key], sequences[key], run_id
        )
        return value, {
            **constraint.to_dict(),
            "sequence": sequences[key],
            "fields": [field.field_code],
        }

    def repair_value(
        self, field: FieldDefinition, current_value: Any, message: str, attempt: int,
    ) -> tuple[Any, dict[str, Any]] | None:
        constraint = parse_validation_message(message, field)
        if constraint is None:
            return None
        if (
            constraint.minimum is not None
            and constraint.maximum is not None
            and constraint.minimum > constraint.maximum
        ):
            return None
        if constraint.kind == "unique":
            return self._generate_unique_value(
                constraint, field, current_value
            )
        run_id = getattr(getattr(self, "generator", None), "run_id", "repair")
        value = generate_repair_value(constraint, field, current_value, attempt, run_id)
        if constraint.kind == "pattern" and value == "":
            return None
        return value, constraint.to_dict()


class ProbeDataStrategy(DataStrategy):
    def __init__(
        self, pool: GlobalDataPool, run_id: str = "", *, form_code: str = "",
    ):
        self.pool = pool
        self.form_code = form_code
        self.generator = ConstrainedGenerator(pool.common, run_id)

    def value_for(self, field: FieldDefinition, index: int) -> Any:
        semantic = self.pool.semantic_for(field)
        return self.generator.generate(semantic, field, index) if semantic else self.generator.by_kind(field, index)


class StableDataStrategy(DataStrategy):
    def __init__(self, pool: GlobalDataPool, form_code: str, run_id: str = ""):
        self.pool = pool
        self.form_code = form_code
        self.generator = ConstrainedGenerator(pool.common, run_id)

    def value_for(self, field: FieldDefinition, index: int) -> Any:
        override = self.pool.override_value(self.form_code, field.field_code)
        if override is not None:
            return override
        collected = self.pool.collected_value(self.form_code, field.field_code)
        if collected is not None:
            return collected
        semantic = self.pool.semantic_for(field)
        candidate = self.pool.candidate(semantic)
        if candidate is not None:
            return candidate
        if field.default_value is not None:
            return field.default_value
        if "defaultValue" in field.props:
            return field.props["defaultValue"]
        return self.generator.generate(semantic, field, index) if semantic else self.generator.by_kind(field, index)


class StandardDataStrategy(ProbeDataStrategy):
    """Generate fresh values while requiring every editable field to be exercised."""

    strict_field_validation = True


def create_data_strategy(mode: str, pool: GlobalDataPool, form_code: str, run_id: str = "") -> DataStrategy:
    normalized = (mode or "probe").strip().lower()
    if normalized == "probe":
        return ProbeDataStrategy(pool, run_id, form_code=form_code)
    if normalized == "stable":
        return StableDataStrategy(pool, form_code, run_id)
    if normalized == "standard":
        return StandardDataStrategy(pool, run_id, form_code=form_code)
    raise ValueError(f"Unknown EI_DATA_MODE: {mode}; expected probe, stable or standard")
