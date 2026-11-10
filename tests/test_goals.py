from __future__ import annotations

import json

from zaya.controller.goals import GoalTracker, goal_block


def _tracker(tmp_path):
    path = tmp_path / "goals.json"
    path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "key": "emprego",
                        "label": "Emprego em tecnologia",
                        "keywords": ["vaga", "currículo", "upwork"],
                        "next_step": "buscar vagas e adaptar o currículo",
                    },
                    {
                        "key": "bioinfo",
                        "label": "Doutorado em Bioinformática",
                        "keywords": ["bioinformática", "doutorado"],
                        "next_step": "ver edital e orientador",
                    },
                    {
                        "key": "muay",
                        "label": "Muay Thai",
                        "keywords": ["muay thai", "treino"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return GoalTracker(path)


def test_detecta_meta_por_palavra_chave(tmp_path):
    tracker = _tracker(tmp_path)
    assert [g.key for g in tracker.detect("preciso adaptar o currículo para a vaga")] == [
        "emprego"
    ]
    assert [g.key for g in tracker.detect("quero entrar no doutorado em Bioinformática")] == [
        "bioinfo"
    ]


def test_detecta_sem_acento(tmp_path):
    tracker = _tracker(tmp_path)
    assert [g.key for g in tracker.detect("vou treinar muay thai hoje")] == ["muay"]


def test_texto_generico_nao_detecta(tmp_path):
    tracker = _tracker(tmp_path)
    assert tracker.detect("qual é a previsão do tempo?") == []


def test_goal_block_menciona_proximo_passo(tmp_path):
    tracker = _tracker(tmp_path)
    goals = tracker.detect("vaga de emprego")
    block = goal_block(goals)
    assert "Emprego em tecnologia" in block
    assert "buscar vagas e adaptar o currículo" in block


def test_arquivo_ausente_nao_quebra(tmp_path):
    tracker = GoalTracker(tmp_path / "nao-existe.json")
    assert tracker.all() == []
    assert tracker.detect("doutorado") == []
