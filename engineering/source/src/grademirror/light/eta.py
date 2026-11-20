"""Advisory phase timing from actual worker events and exact-group history."""
from collections import OrderedDict
import math
import statistics
import time
from pathlib import Path

from ..common import GMError, atomic_json, strict_load
from ..cpu_replay.contract import digest
from ..desktop.i18n import tr
from ..live_json import read_live_json
from ..preview.eta import band
from ..store import locked

STAGES={
    "preflight":"Check inputs and resources","gpu_wait":"Wait for resources","read_source":"Read source",
    "read_reference":"Read reference","fetch_source":"Read source","fetch_reference":"Read reference",
    "input_prepare":"Prepare training inputs","fit":"Learn reference colors","reuse_fast_parameters":"Read saved parameters",
    "save_fast_recipe":"Save parameters","render":"Render at original size","H_area":"Build solver source",
    "T_float64":"Prepare solver target","P3_solve":"Solve fine coefficients","P3_expand":"Apply fine coefficients",
    "pair_range_scan":"Scan tone ranges","LLF_O":"Filter base","LLF_P":"Filter adjusted image","compose":"Compose result",
    "put_y":"Transfer result","host_handoff":"Transfer result","publish":"Publish result",
    "CUDA_EXIT_AND_HANDOFF":"Wait for worker and transfer result","P3_CONSTRUCTION_AND_SOLVE":"Build and solve fine coefficients",
    "CPU":"CPU postprocess","CPU_range":"CPU range scan","CPU_core":"CPU detail blocks",
    "encoding":"Encode output","mixing":"Blend export pixels","source_exif":"Preserve metadata",
    "metadata_verify":"Read back and validate output","export_publish":"Publish export",
    "preview":"Generate preview","preview_reference":"Read reference for preview",
    "preview_quantize":"Prepare preview pixels","preview_resize":"Resize preview proxy",
    "preview_transfer":"Transfer preview","release":"Release resident result","cleanup":"Release resident result",
}
STAGE_LABELS={'preflight': '检查输入与资源', 'gpu_wait': '等待资源', 'read_source': '读取原图', 'read_reference': '读取参考图', 'fetch_source': '读取原图', 'fetch_reference': '读取参考图', 'input_prepare': '准备训练输入', 'fit': '学习参考色彩', 'reuse_fast_parameters': '读取已存参数', 'save_fast_recipe': '保存参数', 'render': '生成原尺寸结果', 'H_area': '构建求解原图', 'T_float64': '准备求解目标', 'P3_solve': '求解精细系数', 'P3_expand': '应用精细系数', 'pair_range_scan': '扫描色调范围', 'LLF_O': '滤波基底', 'LLF_P': '滤波调整图像', 'compose': '合成结果', 'put_y': '传输结果', 'host_handoff': '传输结果', 'publish': '发布结果', 'CUDA_EXIT_AND_HANDOFF': '等待工作进程并交接结果', 'P3_CONSTRUCTION_AND_SOLVE': '构建并求解精细系数', 'CPU': 'CPU 后处理', 'CPU_range': 'CPU 色调扫描', 'CPU_core': 'CPU 细节块', 'encoding': '编码输出', 'mixing': '混合导出像素', 'source_exif': '保留元数据', 'metadata_verify': '读回并校验输出', 'export_publish': '发布导出', 'preview': '生成预览', 'preview_reference': '读取参考图预览', 'preview_quantize': '准备预览像素', 'preview_resize': '缩放代理预览', 'preview_transfer': '传输预览', 'release': '释放驻留结果', 'cleanup': '释放驻留结果'}
STAGES["save_fine_recipe"]="Save fine parameters"
STAGE_LABELS["save_fine_recipe"]="保存精细参数"
ALIASES=dict(sampling="fit",training="fit",fit_start="fit",render_start="render",
             R0="render",put_y="host_handoff",
             committing="publish",resident_prepared="publish",fit_render="input_prepare",
             CUDA_R0_FIT="input_prepare",CUDA_R0_REPLAY="reuse_fast_parameters",
             VALIDATING_RESULT="publish",ACTIVATING_RESIDENT_RESULT="publish",
             RESIDENT_READY="publish",NONRESIDENT="publish",
             **{"保留原图 EXIF":"source_exif","校验导出文件":"metadata_verify"})


def normalized(stage):
    return ALIASES.get(stage,stage)


_emitted=OrderedDict()


def emit(path, stage, done=None, total=None):
    # Publish true worker events, bounded to four ordinary writes per second.
    now=time.monotonic()
    prior=_emitted.get(path)
    if prior and prior[0]==stage and now-prior[1]<.25 and done not in (0,total):
        return
    try:
        atomic_json(path,dict(stage=stage,done=done,total=total,stamp=now))
        _emitted[path]=(stage,now)
        _emitted.move_to_end(path)
        if len(_emitted)>256:
            _emitted.popitem(last=False)
    except (OSError,ValueError):
        pass


def history_group(plan, action, *, build, hardware):
    profile=plan["numeric_profile"]
    roles={name:dict(lock=row["lock"],python=row["python"],versions=row["versions"])
           for name,row in plan["environment"].items()}
    geometry=lambda info:{n:info.get(n) for n in ("width","height","bits","format")}
    device=plan.get("device") or {}
    return digest(dict(build=build,action=action,operation=plan["operation"],quality=plan["quality"],
        backend=plan["selection"]["backend"],profile=profile,roles=roles,threads=4,
        hardware=hardware,device={k:device.get(k) for k in ("uuid","driver_version")},
        source=geometry(plan["source"]),reference=geometry(plan["reference"]),
        saved_post=bool(plan.get("saved_gpu_post") or plan.get("saved_mixed_post"))))


def read_history(path, warn=lambda _:None):
    try:
        if not path.is_file():
            return []
        if path.stat().st_size>1024**2:
            raise ValueError("ETA history exceeds limit")
        value=strict_load(path)
        if value["schema"]!="gm066-phase-history-v1" or type(value["rows"]) is not list:
            raise ValueError("ETA history schema")
        rows=value["rows"]
        for r in rows:
            if set(r)!={"group","phases"} or type(r["group"]) is not str or len(r["group"])!=64 or type(r["phases"]) is not dict:
                raise ValueError("ETA history row")
            if any(display_stage(k) not in STAGES or type(v) not in (int,float) or not math.isfinite(v) or not 0<v<=14400
                   for k,v in r["phases"].items()):
                raise ValueError("ETA history phase")
        return rows[-200:]
    except (GMError,OSError,ValueError,KeyError,TypeError) as error:
        warn(str(error))
        return []


def save_history(path, group, phases, warn=lambda _:None):
    if not group or not phases:
        return
    try:
        with locked(path.with_suffix(".lock"),timeout=.2):
            errors=[]
            rows=read_history(path,errors.append)
            if errors:
                warn(errors[0])
                return
            rows.append(dict(group=group,phases={k:v for k,v in phases.items() if display_stage(k) in STAGES and 0<v<=14400}))
            atomic_json(path,dict(schema="gm066-phase-history-v1",rows=rows[-200:]))
    except (GMError,OSError,ValueError) as error:
        warn(str(error))


def save_phase_sidecar(path, timings):
    try:
        atomic_json(path,timings)
    except (GMError,OSError,ValueError):
        # ETA remains advisory even if its optional sidecar cannot be written.
        pass


def owner_timing(path, started):
    from .runtime import operation
    if operation() is not None:
        save_phase_sidecar(Path(path)/"light-owner-times.json",dict(publish=time.perf_counter()-started))


def new_export_timings(result):
    if (result.get("status") == "SUCCEEDED" and not result.get("idempotent_replay")
            and not result.get("already_exported")):
        return result.get("light_phase_seconds")
    return None


def collect_timings(path, backend):
    path = Path(path)
    result = read_live_json(path / "compute-result.json")
    timings = {}
    if backend == "gpu":
        timings.update(result.get("postprocess_times") or {})
        timings.update(result.get("light_worker_seconds") or {})
        for stage in ("fit", "render", "host_handoff", "input_prepare"):
            value = result.get(stage + "_seconds")
            if stage not in timings and value and (stage != "fit" or result.get("fit_count") == 1):
                timings[stage] = value
    elif backend == "cpu":
        timings.update(read_live_json(path / "session/light-host-times.json") or {})
    timing = read_live_json(path / "light-owner-times.json")
    for stage, value in timing.items():
        if stage in timings and stage == "publish":
            timings[stage] += value
        else:
            timings[stage] = value
    return {name: value for name, value in timings.items() if display_stage(name) in STAGES
            and type(value) in (int, float) and math.isfinite(value) and 0 < value <= 14400}


def phase_chain(backend, operation, quality="fast", *, saved_post=False):
    """Ordered phases which belong to this operation and backend."""
    prefix=("preflight",)
    if operation=="preview":
        return ("preview","preview_reference","preview_quantize","preview_resize","preview_transfer")
    if operation=="export":
        return ("mixing","encoding","source_exif","metadata_verify","export_publish")
    if backend=="mixed":
        prep=("fetch_reference","input_prepare","fit") if operation=="fit" and not saved_post else ("reuse_fast_parameters",)
        solve=("P3_CONSTRUCTION_AND_SOLVE",) if operation=="fit" and not saved_post else ()
        return prefix+("fetch_source",)+prep+("render","CUDA_EXIT_AND_HANDOFF")+solve+("CPU_core","publish")
    cpu=backend=="cpu"
    source,reference=("read_source","read_reference") if cpu else ("fetch_source","fetch_reference")
    if quality=="fast":
        fit=("fit","save_fast_recipe") if cpu else ("input_prepare","fit","save_fast_recipe")
        prepare=(reference,)+fit if operation=="fit" else ("reuse_fast_parameters",)
        return prefix+(source,)+prepare+("render","host_handoff","publish")
    if cpu and operation=="fit":
        fast=tuple("fast:"+name for name in phase_chain("cpu","fit"))
        return fast+tuple("fine:"+name for name in phase_chain("cpu","refine","fine"))
    tail=("CPU",) if cpu else ("P3_expand","pair_range_scan","LLF_O","LLF_P","compose")
    if operation=="replay":
        return prefix+(source,"reuse_fast_parameters","render")+tail+("host_handoff","publish")
    if cpu:
        prepare=("reuse_fast_parameters","render",reference)
    elif operation=="fit":
        prepare=(reference,"input_prepare","fit","render")
    else:
        prepare=(reference,"reuse_fast_parameters","render")
    solve=("P3_CONSTRUCTION_AND_SOLVE",) if cpu else ("H_area","T_float64","P3_solve")
    if saved_post and backend=="gpu":
        solve=()
        tail=tuple(stage for stage in tail if stage!="pair_range_scan")
    save=() if cpu else ("save_fine_recipe",)
    return prefix+(source,)+prepare+solve+tail+save+("host_handoff","publish")


def display_stage(stage):
    return stage.removeprefix("fast:").removeprefix("fine:") if isinstance(stage,str) else stage


class PhaseDurations:
    def __init__(self):
        self.stage=None
        self.since=None
        self.phases={}

    def mark(self,stage):
        stage=normalized(stage)
        if stage==self.stage:
            return
        now=time.perf_counter()
        if self.stage in STAGES and self.since is not None and now>self.since:
            self.phases[self.stage]=self.phases.get(self.stage,0.)+now-self.since
        self.stage,self.since=stage,now

    def completed(self):
        self.mark(None)
        return self.phases.copy()


class Estimate:
    def __init__(self, *, clock=time.monotonic, history=(), phases=()):
        self.clock=clock
        self.stage=None
        self.stage_start=None
        self.last=None
        self.last_stamp=None
        self.samples=[]
        self.history=history
        self.phases=phases
        self.position=-1
        self.last_received=clock()

    def set_context(self, history, phases):
        self.history,self.phases=history,phases
        self.position=phases.index(self.stage) if self.stage in phases else -1

    def observe(self, stage, done, total, stamp):
        stage=normalized(stage)
        stamped=type(stamp) in (int,float) and math.isfinite(stamp) and stamp>=0
        if stamped and self.last_stamp is not None and stamp<=self.last_stamp:
            return
        if stage!=self.stage:
            if stage in self.phases:
                position=self.phases.index(stage)
                if position<self.position:
                    return
                self.position=position
            self.stage,self.last,self.samples=stage,None,[]
            self.stage_start=stamp if stamped else None
            self.last_received=self.clock()
        valid=(type(done) is int and type(total) is int and 0<=done<=total and total>0 and stamped)
        if valid and self.last:
            old_done,old_total,old_stamp=self.last
            if done<=old_done or total!=old_total:
                return
            self.samples.append((stamp-old_stamp)/(done-old_done))
            self.samples=self.samples[-6:]
        if valid:
            self.last=(done,total,stamp)
        if stamped:
            self.last_stamp=stamp
            self.last_received=self.clock()

    def bounds(self):
        now=self.clock()
        if now-self.last_received>30:
            return None
        if self.last and self.last[0]==self.last[1]:
            return None
        if self.last and len(self.samples)>=2:
            return band(statistics.median(self.samples)*(self.last[1]-self.last[0]))
        values=[row[self.stage] for row in self.history if self.stage in row]
        if len(values)>=2 and self.stage_start is not None:
            remaining=statistics.median(values[-8:])-max(0.,now-self.stage_start)
            if remaining>0:
                return band(remaining)

    def overall(self):
        if (self.stage not in self.phases or self.stage=="gpu_wait" or
                self.clock()-self.last_received>30):
            return None
        if self.last and self.last[0]==self.last[1]:
            remaining=(0,0)
        else:
            remaining=self.bounds()
        if remaining is None:
            return None
        low,high=remaining
        for stage in self.phases[self.position+1:]:
            rows=[row[stage] for row in self.history if stage in row]
            if len(rows)<2:
                return None
            lo,hi=band(statistics.median(rows[-8:]))
            low+=lo
            high+=hi
        return low,high

    def _duration(self,bounds):
        lo,hi=bounds
        return (tr("{0}–{1} 秒",lo,hi) if hi<60
                else tr("{0}–{1} 分钟",max(0,lo//60),math.ceil(hi/60)))

    def text(self):
        label=STAGE_LABELS.get(display_stage(self.stage))
        if self.stage=="gpu_wait":
            return tr("等待资源")
        if not label:
            return tr("暂无新进度") if self.clock()-self.last_received>30 else None
        parts=[tr(label)]
        if self.last:
            parts.append(tr("{0}/{1}",*self.last[:2]))
        if self.clock()-self.last_received>30:
            parts.append(tr("暂无新进度"))
            return tr(" · ").join(parts)
        bounds=self.bounds()
        overall=self.overall()
        if bounds:
            parts.append(tr("本阶段约 {0}",self._duration(bounds)))
        if overall:
            parts.append(tr("整体剩余约 {0}",self._duration(overall)))
        return tr(" · ").join(parts)


def mixed_progress(path,row):
    stage=row.get("detail_stage")
    if stage in ("CUDA_R0_FIT","CUDA_R0_REPLAY","CUDA R0"):
        live=read_live_json(Path(path)/"R0"/"progress.json")
        if live.get("stage") in ("before_fit","update") or type(live.get("update")) is int and live.get("stage")!="update_check":
            return dict(stage="fit",done=live.get("update"),total=live.get("total_updates"),stamp=live.get("gm066_monotonic"))
        if live.get("stage")=="render":
            return live
    if stage=="CPU":
        live=read_live_json(Path(path)/"CPU"/"progress.json")
        plan=read_live_json(Path(path)/"execution-plan.json")
        core=live.get("core")
        shape=(plan.get("source_identity") or {}).get("shape")
        rows=plan.get("core_rows")
        done=total=None
        if (isinstance(core,list) and len(core)==2 and all(type(x) is int for x in core)
                and isinstance(shape,list) and shape and type(shape[0]) is int and type(rows) is int and rows>0
                and 0<=core[0]<core[1]<=shape[0] and core[0]%rows==0):
            done,total=core[0]//rows,(shape[0]+rows-1)//rows
        return dict(stage="CPU_core",done=done,total=total,
                    stamp=live.get("gm066_monotonic") or row.get("gm066_monotonic"))
    return dict(stage=normalized(stage),stamp=row.get("gm066_monotonic"))
