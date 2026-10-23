"""The state window is a separate fixed operation, not an expansion of old tools."""
def validate_motion_window(params):
    if (set(params) != {'sensorId', 'seconds'} or type(params['sensorId']) is not int
            or params['sensorId'] < 0 or type(params['seconds']) is not int or params['seconds'] != 30):
        raise ValueError('motion_window requires a sensor index and exactly 30 seconds')
