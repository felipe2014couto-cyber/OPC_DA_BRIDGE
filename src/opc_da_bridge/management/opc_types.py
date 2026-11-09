"""Scalar DA output contracts shared by management validation and telemetry."""
import math

OUTPUT_VARTYPES = {"Double": 5, "Integer": 3, "String": 8, "Boolean": 11}
SOURCE_VARTYPES = dict(OUTPUT_VARTYPES, Float=4)
AUTO_SOURCE_VARTYPES = frozenset((2, 3, 4, 5, 8, 11, 16, 17, 18, 19, 20, 21, 22, 23))


def valid_scalar(data_type, value):
    if data_type == "Boolean":
        return type(value) is bool
    if data_type == "Integer":
        return type(value) is int and -2147483648 <= value <= 2147483647
    if data_type == "String":
        try:
            return isinstance(value, str) and len(value.encode('utf-16-le')) <= 8192
        except UnicodeError:
            return False
    if data_type == "Double":
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False
    return False
