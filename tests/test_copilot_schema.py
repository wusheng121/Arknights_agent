from src.game.copilot_schema import Action, CopilotDoc, GroupSpec, OperSpec


def test_action_to_maa_minimal_deploy():
    a = Action(type="Deploy", name="史尔特尔", location=(4, 5), direction="Left")
    d = a.to_maa()
    assert d["type"] == "Deploy"
    assert d["name"] == "史尔特尔"
    assert d["location"] == [4, 5]
    assert d["direction"] == "Left"
    assert "doc" not in d
    assert "skill_usage" not in d
    assert "kills" not in d  # 默认 0 被省略
    assert "cooling" not in d  # 默认 -1 被省略


def test_action_to_maa_drops_defaults():
    a = Action(type="SpeedUp")
    assert a.to_maa() == {"type": "SpeedUp"}


def test_action_skill_usage_kept_when_set():
    a = Action(type="SkillUsage", skill_usage=1)
    d = a.to_maa()
    assert d == {"type": "SkillUsage", "skill_usage": 1}


def test_copilot_doc_roundtrip():
    doc = CopilotDoc(
        stage_name="1-7",
        opers=[OperSpec(name="棘刺", skill=3, skill_usage=1)],
        groups=[GroupSpec(name="群奶", opers=[OperSpec(name="夜莺", skill=3)])],
        actions=[Action(type="Deploy", name="棘刺", location=(5, 5), direction="Right")],
    )
    m = doc.to_maa()
    assert m["stage_name"] == "1-7"
    assert m["opers"][0]["name"] == "棘刺"
    assert m["groups"][0]["name"] == "群奶"
    assert m["actions"][0]["location"] == [5, 5]
    assert m["minimum_required"] == "v6.7.0"
