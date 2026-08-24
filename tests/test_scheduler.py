from src.core.scheduler import ChatMsg, Scheduler


def test_decision_preempts_mentioned_chat():
    s = Scheduler()
    s.push_chat(ChatMsg("救命", "A", mentioned=True))
    s.push_chat(ChatMsg("hi", "B"))
    s.pause_game()
    s.request_decision()
    t = s.next()
    assert t.kind == "decision" and t.priority == 100
    assert s.thinking is True


def test_mentioned_chat_preempts_normal():
    s = Scheduler()
    s.push_chat(ChatMsg("hi", "B"))
    s.push_chat(ChatMsg("救命", "A", mentioned=True))
    t = s.next()
    assert t.kind == "chat" and t.payload.user == "A"
    assert s.next().payload.user == "B"  # 普通随后


def test_no_chat_when_thinking():
    s = Scheduler()
    s.push_chat(ChatMsg("hi", "B"))
    s.pause_game()
    s.request_decision()
    s.next()  # 进入 thinking
    t = s.next()
    assert t.kind == "narrate"  # 思考间隙,弹幕被压住
    s.end_thinking()
    assert s.next().kind == "chat"  # 结束思考后处理弹幕


def test_idle_when_empty():
    s = Scheduler()
    assert s.next() is None


def test_normal_chat_only_when_not_thinking():
    s = Scheduler()
    s.push_chat(ChatMsg("hi", "B"))
    t = s.next()
    assert t.kind == "chat" and t.payload.text == "hi"
    assert s.next() is None
