"""Manual experimental CUDA-R0 / CPU-LLD10 saved-recipe replay."""
BACKEND_ID = 'cuda-r0-cpu-lld10-v1'
CONTRACT_VERSION = 'gm021-hybrid-replay-v1'
MANIFEST_SCHEMA = 'hybrid-replay-manifest-1.0.0'
LEASE_SCHEMA = 'hybrid-lease-v1'
VERSION_FIELDS = frozenset(('lease_schema_version', 'backend_id',
                           'execution_contract_sha256', 'execution_manifest_sha256'))
