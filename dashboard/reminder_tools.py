"""MCP-facing reminder names and units; all effects use the shared reminder journal."""
from reminder_protocol import normalize, validate_status

NAMES = ('send_reminder', 'get_reminder_status')
SEND_FIELDS = {'reminder_id', 'title', 'body', 'template', 'vibrate', 'screen', 'sound', 'sound_source',
               'vibration', 'screen_seconds', 'ttl_seconds'}


def call(reminders, name, arguments):
    if not isinstance(arguments, dict): raise ValueError('Tool arguments must be an object')
    if name == 'send_reminder':
        if set(arguments) - SEND_FIELDS or not {'reminder_id', 'title', 'body'} <= set(arguments):
            raise ValueError('Provide reminder_id, title and body; unknown fields are rejected')
        params = dict(reminderId=arguments['reminder_id'], title=arguments['title'], body=arguments['body'])
        for public, internal in [('template','template'), ('vibrate','vibrate'), ('screen','screen'), ('screen_seconds','screenSeconds'),('sound','sound'),('sound_source','soundSource')]:
            if public in arguments: params[internal] = arguments[public]
        ttl = arguments.get('ttl_seconds', 60)
        if type(ttl) is not int or not 1 <= ttl <= 300: raise ValueError('ttl_seconds outside 1..300')
        params['ttlMs'] = ttl * 1000
        if 'vibration' in arguments:
            value = arguments['vibration']
            names = {'amplitude':'amplitude', 'pulse_ms':'pulseMs', 'gap_ms':'gapMs', 'count':'count'}
            if not isinstance(value, dict) or set(value) - set(names): raise ValueError('Unknown vibration fields')
            params['vibration'] = {names[k]:v for k,v in value.items()}
        row = reminders.send(normalize(params))
        ok = row['status'] in ('issued', 'displayed', 'acknowledged', 'notification_only_dnd')
        return dict(row, schemaVersion=1, ok=ok, operation=name,
                    source='shared_reminder_journal', automaticallyRetried=False)
    if name == 'get_reminder_status':
        if set(arguments) - {'reminder_id', 'fresh'} or 'reminder_id' not in arguments:
            raise ValueError('reminder_id required; only fresh is optional')
        validate_status(dict(reminderId=arguments['reminder_id']))
        fresh = arguments.get('fresh', False)
        if type(fresh) is not bool: raise ValueError('fresh must be boolean')
        row = reminders.status(arguments['reminder_id'], fresh)
        # Finding a saved failed attempt is still a successful local query.
        ok = bool(row.get('params') or row.get('receipt'))
        return dict(row, schemaVersion=1, ok=ok, operation=name,
                    source='watch_receipt_query' if fresh and ok else 'pc_reminder_journal' if not fresh else 'watch_query_not_completed')
    raise ValueError('Unknown reminder tool')
