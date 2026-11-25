"""Números por extenso compostos (pt/es/fr) na conferência do que vai na tela."""

from __future__ import annotations

from pathlib import Path

import pytest

from broll_bot.spelled import spelled_values


@pytest.mark.parametrize("text,lang,expected", [
    ("algo perto de setecentos milhões de pessoas", "pt", {700, 700_000_000}),
    ("bateria que dura até vinte e oito horas", "pt", {28}),
    ("em dois mil e quinze", "pt", {2015}),
    ("mil novecentos e oitenta e cinco", "pt", {1985}),
    ("cerca de cento e quarenta mil casas", "pt", {140_000}),
    ("dez mil e quinhentos reais", "pt", {10_500}),
    ("um milhão e duzentos mil", "pt", {1_200_000}),
    ("dois vírgula cinco por cento", "pt", {2.5}),
    ("treinta y dos, dos mil quinientos", "es", {32, 2500}),
    ("quatre-vingt-dix euros, deux cents, vingt et un, deux mille vingt-quatre", "fr", {90, 200, 21, 2024}),
])
def test_spelled_values(text, lang, expected):
    assert expected <= spelled_values(text, lang)


def test_portuguese_dos_is_not_two_and_english_is_ignored():
    assert spelled_values("a casa dos Moser", "pt") == set()
    assert spelled_values("twenty-eight hours", "en") == set()


def test_coherence_reads_portuguese_compounds():
    from broll_bot.coherence import numbers_in
    from broll_bot.cues import set_language

    set_language("pt")
    try:
        assert {28.0, 2015.0} <= numbers_in("Em dois mil e quinze, a bateria dura vinte e oito horas.")
    finally:
        set_language("en")


def test_new_templates_reach_the_planner_even_with_an_old_config_yaml(tmp_path):
    """O config.yaml tinha a própria lista de estilos e ela substituía a do código: os 8 templates novos
    (abertura, agenda, ano, processo, verbete...) eram pedidos pelo diretor e descartados sem aviso."""
    from broll_bot.config import TemplatesConfig, load_config

    real = load_config(Path(__file__).resolve().parent.parent / "config.yaml", env_file=None).templates
    assert set(TemplatesConfig().styles) <= set(real.styles)
    old = tmp_path / "config.yaml"
    old.write_text("templates:\n  styles: [comparison, stat]\n  styles_off: [vhs]\n", encoding="utf-8")
    styles = load_config(old, env_file=None).templates.styles
    assert styles[:2] == ["comparison", "stat"] and "agenda" in styles and "opener" in styles and "vhs" not in styles


def test_e_only_joins_a_smaller_part():
    """Tijolo de plástico: "entre cento e cinquenta e duzentos e vinte graus" virava 370."""
    assert spelled_values("entre cento e cinquenta e duzentos e vinte graus", "pt") == {150, 220}
    assert spelled_values("de dois e três metros", "pt") == {2, 3}
    assert spelled_values("mil novecentos e oitenta e cinco", "pt") == {1985}
