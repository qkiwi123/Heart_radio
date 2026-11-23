"""Wire commands for native health reads and the opt-in foreground stress test."""

NOW_METRICS = ('activity', 'wear', 'current_sleep')
WINDOW_METRICS = ('activity', 'historical_sleep', 'heart_rate')
HISTORY_METRICS = (*NOW_METRICS, 'historical_sleep', 'heart_rate', 'stress')
HEALTH_OPS = {'health_now', 'health_history', 'health_stress'}
# Includes the watch's 9-second wear check and its 65-second page deadline.
STRESS_REPLY_SECONDS = 80
WEEK_SECONDS = 7 * 24 * 60 * 60


def validate_health_request(op, params):
    if op == 'health_stress':
        if params != {}:
            raise ValueError('The stock stress test takes no wire parameters')
        return
    if op == 'health_now':
        if set(params) != {'metric'} or params['metric'] not in NOW_METRICS:
            raise ValueError('Invalid current health request')
        return
    if set(params) != {'metric', 'from', 'to'} or params['metric'] not in WINDOW_METRICS:
        raise ValueError('Invalid health history request')
    start, end = params['from'], params['to']
    if (type(start) is not int or type(end) is not int or not 0 < start <= end <= 2**31 - 1
            or end - start >= 86400 or start % 60 or end % 60):
        raise ValueError('Invalid health history window')


def health_log_summary(result):
    """Keep reply metadata in transport logs; durable values live in the health store."""
    return {key: result[key] for key in ('id', 'op', 'status', 'metric', 'pcRoundTripMs') if key in result}
