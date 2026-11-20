"""GM064 fixed-domain incremental RAM policy; no GPU capacity credit."""
from ..cpu_replay.contract import need
G=1024**3
POLICY='gm064-incremental-ram-v1'

def margin(p):
    need(type(p) is int and 0<=p<2**63,'PORTABLE_RAM_PREDICTION_UNKNOWN')
    return min(1_000_000_000,max(536_870_912,(p+4)//5))

def evaluate(facts,ram,commit,*,occupied_ram=0,occupied_commit=0,cold=False):
    values=(ram,commit,occupied_ram,occupied_commit)
    need(all(type(x) is int and 0<=x<2**63 for x in values),'PORTABLE_PREDICTION_UNKNOWN')
    dr=max(0,ram-occupied_ram);dc=max(0,commit-occupied_commit)
    physical=facts['physical']['available_bytes'];headroom=facts['commit']['headroom_bytes']
    known=all(type(x) is int and 0<=x<2**63 for x in (physical,headroom))
    required_ram=dr+margin(dr);required_commit=max(4*G if cold else 0,dc+G)
    reasons=[]
    if not known:reasons.append('RESOURCE_UNKNOWN')
    else:
        if physical<required_ram:reasons.append('PHYSICAL_INCREMENT_AND_MARGIN')
        if headroom<required_commit:reasons.append('SYSTEM_COMMIT_INCREMENT_AND_MARGIN')
    return dict(policy=POLICY,additional_ram_bytes=dr,additional_commit_bytes=dc,physical_margin_bytes=margin(dr),
        required_ram_bytes=required_ram,required_commit_bytes=required_commit,physical_available_bytes=physical,
        system_commit_headroom_bytes=headroom,legacy_availablePageFile=facts.get('legacy_process_commit_available'),
        cold=cold,resource_feasible=not reasons,reasons=reasons,unknown=not known,
        occupied_ram_already_excluded_from_available=True,VRAM_credit_bytes=0)
