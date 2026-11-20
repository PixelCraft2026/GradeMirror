"""Exact delegated R7 finite addendum, with immutable prior R5/R6 records."""
from pathlib import Path
from .first_contract import E,I,read
from ..cpu_replay.contract import binding,verify,need
SHA='bb9d0184bbef0975c2d3d6ab0c3338af91357a55ad49710e68edd4d6db62d06a'
def grant():
 prior=E/'completion-r6/science-addendum.json';need(binding(prior)['sha256']=='404709014630a92b865695efb80183328e10f7b8af50aea7bf31ba725573cba7' and binding(E/'completion-r5/science-addendum.json')['sha256']=='5c9dae6d8118b62595adaae36028f8cce72e9b78f1f13ee6facd7364c2edcf6e','PRIOR_ADDENDUM_PRESERVED')
 p=E/'completion-r7/science-addendum.json';need(binding(p)['sha256']==SHA,'R7_ADDENDUM_BYTES');a=read(p);expected=dict(R0_render=9,render_forward=1260,state_load=9,Field=10,source_decode=9,range_scan=5,CPU256_service=4,scalar_LLF_footprints=672,compositions=336,remap_pyramids=172704,D_calls=1213632,U_calls=2422560,P3_scalar_evaluations=2040000768)
 need(a['schema']=='gm057-r7-science-addendum-v1' and a['status']=='SELF_ALLOCATED_UNDER_USER_AUTHORITY' and Path(a['original_capacity']['path']).resolve()==I/'capacity.json' and all(a['original_capacity'][k]==binding(I/'capacity.json')[k] for k in ('bytes','sha256')) and Path(a['prior_addendum']['path']).resolve()==prior and all(a['prior_addendum'][k]==binding(prior)[k] for k in ('bytes','sha256')) and a['overrides']==expected and a['allocation']=='refine-query-closure-r7' and a['no_refund'] is True and a['clock_reset'] is False and a['numerical_resource_production_gates_changed'] is False,'R7_ADDENDUM_SCOPE');verify(a['authority']);need(read(a['authority']['path'])['status']=='USER_APPROVED','R7_DIRECT_AUTHORITY');return a
def caps():
 old=dict(read(I/'capacity.json')['science_total']);old.update(grant()['overrides']);return old
