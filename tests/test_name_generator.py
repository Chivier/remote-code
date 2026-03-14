"""
Tests for head/name_generator.py
"""

import pytest
from head.name_generator import (
    generate_name,
    is_valid_name,
    ADJECTIVES,
    NOUNS,
)


class TestGenerateName:
    def test_returns_string(self):
        name = generate_name()
        assert isinstance(name, str)

    def test_format_two_words_hyphenated(self):
        name = generate_name()
        parts = name.split("-")
        assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}: {name}"

    def test_adjective_from_list(self):
        name = generate_name()
        adj, noun = name.split("-")
        assert adj in ADJECTIVES, f"Adjective '{adj}' not in ADJECTIVES list"

    def test_noun_from_list(self):
        name = generate_name()
        adj, noun = name.split("-")
        assert noun in NOUNS, f"Noun '{noun}' not in NOUNS list"

    def test_lowercase(self):
        name = generate_name()
        assert name == name.lower()

    def test_avoids_existing_names(self):
        existing = {"bright-falcon", "calm-river"}
        name = generate_name(existing_names=existing)
        assert name not in existing

    def test_avoids_many_existing_names(self):
        # Generate a large set of existing names
        existing = set()
        for _ in range(100):
            existing.add(generate_name(existing))
        # All names should be unique
        assert len(existing) == 100

    def test_empty_existing_set(self):
        name = generate_name(existing_names=set())
        assert isinstance(name, str)
        assert "-" in name

    def test_none_existing_set(self):
        name = generate_name(existing_names=None)
        assert isinstance(name, str)
        assert "-" in name

    def test_fallback_with_number(self):
        # Create a situation where all combinations are "taken"
        # by mocking with a huge existing set - but since the pool is ~43K,
        # we test the mechanism by setting max_attempts=0
        name = generate_name(existing_names=set(), max_attempts=0)
        # Should have a number suffix
        parts = name.split("-")
        assert len(parts) == 3
        assert parts[2].isdigit()

    def test_different_names_generated(self):
        """Generate multiple names and verify we don't always get the same one."""
        names = {generate_name() for _ in range(20)}
        # With random selection from 43K combos, 20 names should be mostly unique
        assert len(names) >= 10

    def test_word_lists_not_empty(self):
        assert len(ADJECTIVES) > 50
        assert len(NOUNS) > 50

    def test_word_lists_all_lowercase(self):
        for adj in ADJECTIVES:
            assert adj == adj.lower(), f"Adjective '{adj}' is not lowercase"
        for noun in NOUNS:
            assert noun == noun.lower(), f"Noun '{noun}' is not lowercase"

    def test_word_lists_no_hyphens(self):
        """Words themselves shouldn't contain hyphens (the separator)."""
        for adj in ADJECTIVES:
            assert "-" not in adj, f"Adjective '{adj}' contains hyphen"
        for noun in NOUNS:
            assert "-" not in noun, f"Noun '{noun}' contains hyphen"

    def test_word_lists_no_duplicates(self):
        assert len(ADJECTIVES) == len(set(ADJECTIVES)), "Duplicate adjectives found"
        assert len(NOUNS) == len(set(NOUNS)), "Duplicate nouns found"


class TestIsValidName:
    def test_valid_two_word(self):
        assert is_valid_name("bright-falcon") is True

    def test_valid_three_word(self):
        assert is_valid_name("my-test-project") is True

    def test_valid_with_digits(self):
        assert is_valid_name("test-run-1") is True

    def test_invalid_single_word(self):
        assert is_valid_name("falcon") is False

    def test_invalid_empty(self):
        assert is_valid_name("") is False

    def test_invalid_uppercase(self):
        assert is_valid_name("Bright-Falcon") is False

    def test_invalid_spaces(self):
        assert is_valid_name("bright falcon") is False

    def test_invalid_special_chars(self):
        assert is_valid_name("bright_falcon") is False

    def test_invalid_too_long(self):
        assert is_valid_name("a" * 25 + "-" + "b" * 26) is False

    def test_valid_generated_name(self):
        name = generate_name()
        assert is_valid_name(name) is True

    def test_invalid_leading_hyphen(self):
        assert is_valid_name("-bright-falcon") is False

    def test_invalid_trailing_hyphen(self):
        assert is_valid_name("bright-falcon-") is False

    def test_invalid_double_hyphen(self):
        assert is_valid_name("bright--falcon") is False
