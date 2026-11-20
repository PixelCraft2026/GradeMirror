"""Exact GM020 S7 ownership core; only the import changes for production packaging."""
from .. import winproc as win

def classify(compute,graphics,history,handles,query,wait):
    rows=[];foreign=[];unknown=[]
    for pid in sorted(set(compute)-set(graphics)):
        current=query(pid);key=win.identity_key(current);known=[v for v in history.values()if v['pid']==pid];decision=None;held_states=[]
        for ident in known:
            expected=win.identity_key(ident);held=handles.get(expected)
            if held is None:continue
            h,held_ident=held
            if win.identity_key(held_ident)!=expected:continue
            state=wait(h);held_states.append(dict(identity=ident,state=state))
        live=[v for v in held_states if v['state']==win.WAIT_TIMEOUT]
        invalid=[v for v in held_states if v['state']not in(win.WAIT_TIMEOUT,win.WAIT_OBJECT_0)]
        if invalid:decision='unknown_handle_wait'
        elif len(live)>1:decision='unknown_multiple_live_handles_for_pid'
        elif live:
            expected=win.identity_key(live[0]['identity'])
            decision='unknown_live_handle_identity_conflict'if key is not None and key!=expected else'owned_retained_live_handle'
        elif key is not None and any(win.identity_key(v)==key for v in known):decision='owned_fresh_identity'
        elif held_states:
            if current is None:decision='owned_exited_driver_lag'
            elif key is None:decision='unknown_exited_handle_current_identity'
            else:decision='foreign_reused_pid'
        if decision is None:
            if key is not None and any(win.identity_key(v)==key for v in known):decision='owned_fresh_identity'
            elif current is None and known:decision='owned_absent_driver_lag'
            elif key is None:decision='unknown_current_identity'
            else:decision='foreign_identity'
        if decision.startswith('unknown'):unknown.append(pid)
        elif decision.startswith('foreign'):foreign.append(pid)
        rows.append(dict(pid=pid,current=current,decision=decision,retained=held_states))
    return dict(complete=not unknown,foreign=foreign,unknown=unknown,rows=rows)
