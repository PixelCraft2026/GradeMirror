"""Application control plane; importing it starts no worker or numerical runtime."""
CPU_ENABLED=False
AUTO_ENABLED=False
PRODUCTION_ENABLED=False

def plan(request,snapshot=None,*,qualification=None,workspace=None):
    from .planner import plan as make
    return make(request,snapshot,qualification=qualification,workspace=workspace)

def submit(plan,request_key,workspace,*,experimental=False,qualification=None):
    from .service import submit as run
    return run(plan,request_key,workspace,experimental=experimental,qualification=qualification)
