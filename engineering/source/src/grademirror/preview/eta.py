"""Advisory ETA based on work events and matching successful stage observations."""
import hashlib
import math
import os
import statistics
import time

from ..common import GMError, atomic_json, canonical_json, strict_load
from ..store import locked

STAGES = {
    "fit": ("preflight", "read_source", "read_reference", "fit", "save_fast_recipe", "render", "publish"),
    "replay": ("preflight", "read_source", "render", "publish"),
    "export": ("encoding", "metadata_verify"),
}
ALIASES = {"fit_start": "fit", "sampling": "fit", "training": "fit", "render_start": "render",
           "resident_prepared": "publish", "committing": "metadata_verify",
           "保留原图 EXIF": "metadata_verify", "校验导出文件": "metadata_verify"}
LABELS = dict(preflight="检查输入和资源", read_source="读取原图", read_reference="读取参考图",
              fit="学习参考色彩", save_fast_recipe="保存快速配方", render="生成原尺寸结果",
              publish="发布结果并生成预览", encoding="混合并编码", metadata_verify="校验并保存文件")
HISTORY_LIMIT = 200
HISTORY_BYTES = 1024**2


def signature(context, operation):
    # Hardware groups are exact-match labels, with no clock or core scaling.
    cpu = os.environ.get("PROCESSOR_IDENTIFIER", "")
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0]
        except OSError:
            pass
    if not cpu:
        return None
    h = context["headers"]
    plan = dict(version=context["version"], runtime=context["runtime"], math=context["profile"],
                build=context["executor_build"]["sha256"], operation=operation, quality="fast", backend="CPU",
                cpu=cpu, threads=4, logical_cores=os.cpu_count(), source=h["source"],
                reference=h["reference"] if operation == "fit" else None,
                tile=512, updates=1000 if operation == "fit" else 0)
    return hashlib.sha256(canonical_json(plan)).hexdigest()


def read_history(path, diagnostic=lambda message: None):
    try:
        if not path.is_file():
            return []
        if path.stat().st_size > HISTORY_BYTES:
            raise ValueError("ETA history is too large")
        data = strict_load(path)
        if data["schema"] != "gm065-eta-history-v1" or type(data["rows"]) is not list:
            raise ValueError("ETA history version")
        rows = data["rows"]
        for row in rows:
            if (set(row) != {"group", "operation", "stages"} or type(row["group"]) is not str
                    or len(row["group"]) != 64 or row["operation"] not in STAGES
                    or type(row["stages"]) is not dict or not row["stages"].keys() <= set(STAGES[row["operation"]])
                    or any(type(t) not in (int, float) or not math.isfinite(t) or not 0 < t <= 14400
                           for t in row["stages"].values())):
                raise ValueError("ETA history values")
        return rows[-HISTORY_LIMIT:]
    except (GMError, OSError, TypeError, KeyError, ValueError) as error:
        diagnostic(str(error))
        return []


def save_history(path, group, operation, stages, diagnostic=lambda message: None):
    if not group or not stages:
        return
    try:
        with locked(path.with_suffix(".lock"), timeout=1):
            errors=[]
            rows = read_history(path, errors.append)
            if errors:
                diagnostic(errors[0])
                return  # Preserve the corrupt bytes for diagnosis.
            rows.append(dict(group=group, operation=operation,
                             stages={k: round(v, 3) for k, v in stages.items() if .001 <= v <= 14400}))
            atomic_json(path, dict(schema="gm065-eta-history-v1", rows=rows[-HISTORY_LIMIT:]))
    except (GMError, OSError, ValueError) as error:
        diagnostic(str(error))


def band(seconds):
    return (max(1, math.floor(seconds*.55)), max(2, math.ceil(seconds*1.85)))


def duration(bounds):
    lo, hi = bounds
    if hi < 60:
        return f"{lo}–{hi} 秒"
    return f"{max(1, lo//60)}–{max(2, math.ceil(hi/60))} 分钟"


class ETA:
    def __init__(self, operation, *, clock=time.monotonic):
        self.operation = operation
        self.order = STAGES[operation]
        self.clock = clock
        self.started = self.entered = clock()
        self.stage = self.order[0]
        self.completed = {}
        self.samples = []
        self.latest = None
        self.last_seen = self.entered
        self.known = {}
        self.state = "running"
        self.tile_total = None
        self.saw_worker_event = False

    def configure(self, context, rows):
        group = signature(context, self.operation)
        source = context["headers"]["source"]
        self.tile_total = math.ceil(source["width"]/512)*math.ceil(source["height"]/512)
        # GM063's saved component prior is scoped to its own exact runtime.
        # There is no bridge from that runtime identity to this preview build.
        # Match the declared scope before considering any of its component terms.
        from .contract import ROOT
        p = ROOT / "eta-prior.json"
        try:
            prior = strict_load(p)
            self.prior_status = ("compatible_component_only" if prior["runtime_bound"] ==
                                 context["runtime"].get("inventory_sha256")
                                 and prior["backend"] == "CPU" and prior["threads"] == 4
                                 and all(prior[k] == source[k] for k in ("width","height","bits"))
                                 else "incompatible_saved_runtime_or_scope")
        except (OSError, KeyError, TypeError, ValueError):
            self.prior_status = "unavailable"
        for stage in self.order:
            values = [r["stages"][stage] for r in rows if r["group"] == group and
                      r["operation"] == self.operation and stage in r["stages"]]
            if values:
                self.known[stage] = statistics.median(values[-8:])
        return group

    def update(self, row):
        if self.state != "running":
            return
        raw = row.get("stage")
        if raw not in LABELS and raw not in ALIASES:
            return
        stage = ALIASES.get(raw, raw)
        if stage not in self.order:
            return
        position, previous = self.order.index(stage), self.order.index(self.stage)
        if position < previous:
            return
        now = self.clock()
        if position > previous:
            if position == previous+1:
                self.completed[self.stage] = now-self.entered
            self.stage, self.entered, self.last_seen = stage, now, now
            self.samples, self.latest = [], None
        if raw == "sampling":
            update = row.get("update")
            done, total = (update-1 if type(update) is int else None), 1000
        elif raw == "training":
            done, total = row.get("update"), 1000
        elif raw == "render":
            done, total = row.get("completed_tiles"), row.get("total_tiles")
            if self.tile_total is None or total != self.tile_total:
                return
        else:
            return
        stamp = row.get("elapsed")
        if not (type(done) is int and type(total) is int and 0 <= done <= total
                and type(stamp) in (int, float) and math.isfinite(stamp) and stamp >= 0):
            return
        if self.latest:
            old_stamp, old_done, old_total = self.latest
            if stamp <= old_stamp or done <= old_done or total != old_total:
                return
            self.samples.append((stamp-old_stamp)/(done-old_done))
            self.samples = self.samples[-6:]
        self.latest = stamp, done, total
        self.last_seen = now
        self.saw_worker_event = True

    def finish(self, state):
        self.state = state
        if state == "complete" and self.stage == self.order[-1]:
            self.completed[self.stage] = self.clock()-self.entered

    def text(self):
        if self.state == "complete":
            return "已完成 · 剩余 0 秒"
        if self.state != "running":
            return {"cancel": "正在取消并等待退出", "failed": "操作未完成", "waiting": "等待资源"}[self.state]
        now = self.clock()
        count = f" · {self.latest[1]}/{self.latest[2]}" if self.latest else ""
        prefix = f"{LABELS[self.stage]}{count} · 已用 {max(0,int(now-self.started))} 秒"
        if now-self.last_seen > 30:
            return prefix + " · 暂无新进度，剩余时间暂不可估"
        current = None
        if self.latest and len(self.samples) >= 2 and self.latest[1] < self.latest[2]:
            current = statistics.median(self.samples) * (self.latest[2]-self.latest[1])
        elif not self.latest and self.stage in self.known:
            left = self.known[self.stage]-(now-self.entered)
            if left > 0:
                current = left
        if current is None:
            return prefix + " · 正在估算剩余时间"
        future = self.order[self.order.index(self.stage)+1:]
        if all(stage in self.known for stage in future):
            return prefix + " · 整体预计还需约 " + duration(band(current+sum(self.known[s] for s in future)))
        return prefix + " · 本阶段预计还需约 " + duration(band(current)) + "，整体时间暂不可估"
