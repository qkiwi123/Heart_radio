"""Best-effort cleanup of this project's resources. Never edits business preferences."""
import json
import os
import subprocess
import time


def shutdown_project(root, transport, data_access, capability_service, widgets, server, grace=4):
    time.sleep(grace)
    errors=[]
    def receiver():
        result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'scripts/Stop-Receiver.ps1')],
                              cwd=root,capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise OSError('receiver cleanup did not confirm success')
    actions=[('measurements',data_access.cancel),('connections',transport.stop),
             ('capability',lambda:capability_service.stop(keep_standby=True)),('receiver',receiver),('widgets',widgets.close)]
    try:
        for name,action in actions:
            try:action()
            except Exception as exc:errors.append(dict(resource=name,error=type(exc).__name__))
        report=root/'runs/offline-shutdown-result.json'
        report.write_text(json.dumps(dict(reason='offline_timeout',completedAtMs=time.time_ns()//1_000_000,errors=errors)),encoding='utf-8')
    finally:server.shutdown()
    return errors
