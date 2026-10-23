"""Pure selection policy: authenticated candidates, bounded preference wait, stable active route."""
MODES=('auto_bluetooth','auto_wifi','rfcomm','wifi')

def allowed(mode):
    if mode not in MODES:raise ValueError('Unknown connection mode')
    return ('rfcomm','wifi') if mode.startswith('auto_') else (mode,)

def choose(mode, ready, active=None, waiting_seconds=0):
    candidates=allowed(mode)
    if active in candidates and ready.get(active):return active
    preferred='wifi' if mode in ('wifi','auto_wifi') else 'rfcomm'
    if ready.get(preferred):return preferred
    if len(candidates)>1 and waiting_seconds>=5:
        other='rfcomm' if preferred=='wifi' else 'wifi'
        if ready.get(other):return other
    return None
