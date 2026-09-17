from unittest.mock import Mock

from devfeed_aggregator.scheduler_health import HEARTBEAT_KEY, SchedulerHealth
from redis.exceptions import ConnectionError


def test_long_cycles_keep_heartbeat_but_stalls_expire():
    clock = Mock(return_value=0)
    connection = Mock()
    health = SchedulerHealth(connection, clock)
    clock.return_value = 180
    health.pulse()
    assert connection.set.call_args.args[0] == HEARTBEAT_KEY
    assert connection.set.call_args.kwargs["ex"] == 120
    health.completed()
    clock.return_value = 350
    health.pulse()
    assert connection.set.call_count == 2
    clock.return_value = 481
    health.pulse()
    assert connection.set.call_count == 2


def test_heartbeat_recovers_from_redis_loss():
    connection = Mock()
    connection.set.side_effect = [ConnectionError(), True]
    health = SchedulerHealth(connection)
    health.pulse()
    health.pulse()
    assert connection.set.call_count == 2
