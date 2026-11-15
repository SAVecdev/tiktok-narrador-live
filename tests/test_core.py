from narrador.config import Settings
from narrador.core import Event, Policy


def test_normalization_and_cooldown():
    policy = Policy(Settings())
    event, reason = policy.accept(Event('comment', '1', ' A\x00 ', 'hola https://evil.test', 0), 0)
    assert reason is None and event.text == 'hola' and event.name == 'A'
    assert policy.accept(Event('comment', '1', 'Changed', 'hola', 1), 1)[0] is None
    assert policy.accept(Event('comment', '2', 'A', 'hola', 1), 1)[0]
    assert policy.accept(Event('comment', '1', 'A', 'otra cosa', 10), 10)[0]
    assert policy.accept(Event('comment', '1', 'A', 'hola', 20), 20)[0] is None


def test_age_disabled_filter_and_literal_template():
    policy = Policy(Settings(mode='ignore', blocked_words='spam', greeting_template='{usuario} {x.a}'))
    assert policy.accept(Event('comment', '1', 'A', 'hello', 0), 0)[0] is None
    assert policy.accept(Event('join', '1', 'A', created=0), 31)[0] is None
    assert policy.accept(Event('join', '1', 'SPAM', created=31), 31)[0] is None
    assert policy.greeting(Event('join', '1', 'Ana')) == 'Ana {x.a}'


def test_caches_bounded_and_join_cooldown():
    policy = Policy(Settings())
    for number in range(2100):
        policy.accept(Event('join', str(number), 'A', created=0), 0)
    assert len(policy.joins) == 2000
    assert policy.accept(Event('join', '2099', 'A', created=299), 299)[0] is None
    assert policy.accept(Event('join', '2099', 'A', created=300), 300)[0]
