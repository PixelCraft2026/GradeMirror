"""Use the original cross-Store admission, with portable exact-exit proof."""
from pathlib import Path
from ..gm004a_resident import ResidentAdmission
from ..common import strict_load
from ..store import locked
from ..winproc import identity_alive
from ..winproc import identity_key
from .contract import authority


class PortableAdmission(ResidentAdmission):
    def renew_idle(self,spec,before,after,*,verified=None):
        from .contract import need
        from ..adaptive_execution.residency import context
        if verified is None:
            verified=context(spec)
        need(verified is not None and Path(verified['pool_root'])==self.root,'KEEP_ALIVE_VERSION_NOT_SUPPORTED')
        need(set(before)==set(after) and
             {k:v for k,v in before.items() if k!='expires_at'}==
             {k:v for k,v in after.items() if k!='expires_at'},'LEASE_IMMUTABLE_FIELDS')
        b=before['binding']
        with locked(self.lock_path):
            row=self._current_token(b['pool_token'])
            need(row['state']=='BOUND' and row['job_id']==spec['job_id']==b['job_id'] and
                 row['attempt_id']==spec['attempt_id']==b['attempt_id'] and
                 row['lease_handle_id']==b['handle_id'] and row['owner']==b['owner'] and
                 row['lease_expires_at']==before['expires_at'],'POOL_RENEW_IDENTITY')
            session=Path(row['session_path'])
            need(row['host']==strict_load(session/'host-process.json') and
                 strict_load(session/'host-entry.json')==b['host'],'POOL_RENEW_HOST_IDENTITY')
            self._write(dict(row,lease_expires_at=after['expires_at']))

    def for_exact_operation(self, spec, session, token=None):
        """Do not let current-token reconciliation erase startup provenance."""
        from .contract import need
        with locked(self.lock_path):
            row = self._read()
            if token and (not row or row['token'] != token):
                p = self.root / 'receipts' / (token + '.json')
                row = strict_load(p) if p.is_file() else None
            if row and row['job_id'] == spec['job_id'] and row['attempt_id'] == spec['attempt_id']:
                need(Path(row['session_path']).resolve() == Path(session).resolve(), 'POOL_SESSION_IDENTITY')
                return row
            if not token:
                matches=[]
                for path in (self.root / 'receipts').glob('*.json'):
                    old=strict_load(path)
                    if old['job_id']==spec['job_id'] and old['attempt_id']==spec['attempt_id']:
                        need(Path(old['session_path']).resolve()==Path(session).resolve(),'POOL_SESSION_IDENTITY')
                        matches.append(old)
                need(len(matches)<=1,'POOL_OPERATION_AMBIGUOUS')
                if matches:return matches[0]
            return None

    def finalize(self, spec, session, token, proof):
        from .contract import need, verify
        import time
        need(proof['token'] == token and proof['job_id'] == spec['job_id'] and
             proof['attempt_id'] == spec['attempt_id'] and
             Path(proof['session_path']).resolve() == Path(session).resolve() and
             proof['tree_empty'] is True, 'POOL_EXIT_PROOF')
        need((proof['no_child_started'] is True and proof['actual_wait'] is False and not proof['identities']) or
             (proof['no_child_started'] is False and proof['actual_wait'] is True and
              all(r['exited'] is True for r in proof['identities'])), 'POOL_WAIT_PROOF')
        if proof.get('proof_source')=='USER_ADOPTED_EXACT_OUTER_JOB':
            from ..cpu_first_fit.outer_exit import verify_adopted
            verify_adopted(proof,spec,session,token)
        elif proof.get('qualification_abort'):
            # Failure-only test cleanup: never a normal host receipt or PASS.
            from .lifecycle import context
            need(spec['semantics'].get('TEST_ONLY') is True and context(spec, cleanup=True).get('TEST_ONLY') is True, 'ABORT_TEST_CONTEXT_REQUIRED')
            verify(proof['outer_wait'])
            raw = strict_load(Path(proof['outer_wait']['path']))
            need(all(raw[k] is True for k in ('actual_all_wait','actual_root_wait','tree_empty','exact')) and
                 raw['actual_members'] == len(raw['records']) and
                 all(r['actual_handle_wait'] is True and r['job_member_verified'] is True for r in raw['records']), 'ABORT_ACTUAL_TREE_UNKNOWN')
            known = {identity_key(r) for r in raw['records']}
            launched = strict_load(Path(session) / 'owner-launch.json')
            need(identity_key(launched['identity']) in known and all(identity_key(r['identity']) in known for r in proof['identities']), 'ABORT_OWNERSHIP_UNKNOWN')
            need(all(identity_alive(r['identity']) is False for r in proof['identities']), 'ABORT_MEMBER_STILL_LIVE')
        elif not proof['no_child_started']:
            verify(proof['owner_cleanup'])
            need(all(identity_alive(r['identity']) is False for r in proof['identities']), 'POOL_IDENTITY_NOT_EXITED')
            need(all(r['cannot_reopen'] is True for r in proof['mappings']), 'POOL_MAPPING_NOT_CLOSED')
        else:
            startup = strict_load(Path(session).parent / 'startup.json')
            need(startup['phase'] == 'NOT_STARTED' and
                 not any((Path(session) / n).exists() for n in ('owner-launch.json','owner.json','host-process.json','host-entry.json')),
                 'POOL_NO_CHILD_UNPROVEN')
        with locked(self.lock_path):
            row = self._read()
            if row and row['token'] != token:
                old = strict_load(self.root / 'receipts' / (token + '.json'))
                need(old['token'] == token and old['state'] == 'FREE', 'OLD_POOL_TOKEN_NOT_FREE')
                return old
            row = self._current_token(token)
            if row['state'] != 'FREE':
                row.update(state='FREE', freed_at=time.time(), free_reason='PUBLIC_LIFECYCLE_RECLAIM',
                           owner_alive=False, host_alive=False)
                self._write(row)
            return row

    @staticmethod
    def _exit_proven(session):
        p = Path(session) / 'owner-cleanup.json'
        if not p.is_file():
            return False
        r = strict_load(p)
        return (r.get('actual_wait') is True and r.get('owned_tree_empty') is True
                and r.get('actual_host_handle_wait') is True and
                identity_key(r.get('actual_host_identity')) == identity_key(strict_load(Path(session) / 'host-entry.json')))


def pool(spec=None):
    if spec and spec.get('semantics',spec).get('instance_context'):
        from .lifecycle import context
        c=context(spec,cleanup=True)
        from ..preview.contract import enabled as preview_enabled
        if preview_enabled(c):
            return PortableAdmission(Path(c['pool_root']))
        if c.get('schema') in ('cpu-application-execution-v3','cpu-application-execution-v4','cpu-application-execution-v5'):return PortableAdmission(Path(c['pool_root']))
    root = Path(authority()['output_root']) / 'resident-pool'
    if spec and spec['semantics'].get('instance_context'):
        from ..cpu_first_fit.contract import context
        return PortableAdmission(Path(context(spec, cleanup=True)['pool_root']))
    if spec and spec['semantics'].get('TEST_ONLY') is True:
        from .lifecycle import context
        context(spec, cleanup=True)
        root = Path(spec['semantics']['test_pool'])
    return PortableAdmission(root)
