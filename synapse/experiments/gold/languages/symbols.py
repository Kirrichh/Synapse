"""Kinds recorded in native Gold binding transports."""
from enum import Enum


class NativeSymbolKind(str, Enum):
    MODULE = "MODULE"
    FUNCTION = "FUNCTION"
    CLASS = "CLASS"
    METHOD = "METHOD"
    TYPE = "TYPE"
    LABEL = "LABEL"
    VIEW = "VIEW"
    ATTRIBUTE = "ATTRIBUTE"
    ELEMENT = "ELEMENT"
    RULE = "RULE"
