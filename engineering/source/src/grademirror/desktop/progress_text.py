"""User-facing task stages; unknown totals remain indeterminate."""

from .i18n import tr, render, source_text
import math
import time

STAGES = {
    "accepted": tr("任务已提交，正在准备"), "preflight": tr("检查输入与资源"),
    "fit_render": tr("准备计算并读取图像"), "fetch_source": tr("读取原图"),
    "fetch_reference": tr("读取调色参考"), "input_prepare": tr("准备训练输入"),
    "fit": tr("学习参考图色彩"), "render": tr("生成原尺寸结果"),
    "reuse_fast_parameters": tr("复用快速仿色参数"),
    "H_area": tr("精细处理：建立参考对应关系"), "T_float64": tr("精细处理：准备参考颜色"),
    "P3_solve": tr("精细处理：求解局部颜色校正"), "P3_expand": tr("精细处理：应用局部颜色校正"),
    "pair_range_scan": tr("精细处理：检查颜色范围"), "LLF_O": tr("精细细节处理（1/2）"),
    "LLF_P": tr("精细细节处理（2/2）"), "compose": tr("合成精细结果"),
    "put_y": tr("传递结果并检查完整性"), "host_handoff": tr("传递结果并检查完整性"),
    "source_exif": tr("保留原图 EXIF"), "metadata_verify": tr("校验导出文件"),
    "保留原图 EXIF": tr("保留原图 EXIF"), "校验导出文件": tr("校验导出文件"),
    "encoding": tr("编码并写入图片"), "committing": tr("校验并保存结果"),
    "terminal": tr("正在结束任务"), "gpu_wait": tr("等待 GPU 资源"),
}


def task_progress_text(kind, row, *, wall_now=None):
    label = {"fit": tr("正在处理新图"), "replay": tr("正在载入已有结果"), "export": tr("正在导出图片")}.get(kind, tr("正在处理任务"))
    state = row.get("state")
    if state in {"FAILED", "CANCELLED", "SUCCEEDED", "CANCEL_REQUESTED"}:
        return {"FAILED": tr("任务失败"), "CANCELLED": tr("任务已取消"),
                "SUCCEEDED": tr("导出已完成，正在确认结果") if kind == "export" else tr("计算已完成，正在接收结果"),
                "CANCEL_REQUESTED": tr("正在取消任务并释放资源…")}[state] + (
                    " · " + str(row["error_code"]) if state == "FAILED" and row.get("error_code") else "")
    stage_key = row.get("detail_stage") or row.get("stage")
    stage = STAGES.get(stage_key, tr("正在处理，等待阶段反馈"))
    waiting = row.get("gpu_wait") or {}
    free, required = waiting.get("free_mib"), waiting.get("required_free_mib")
    if stage_key == "gpu_wait" and type(free) is int and type(required) is int:
        stage = tr("等待疑似计算进程结束") if waiting.get("reason") == "COMPUTE_PROCESS_CANDIDATE" else tr("等待显存")
        stage += tr('：当前 {0:.2f} GiB（{1} MiB），需 {2:.2f} GiB（{3} MiB）', free/1024, free, required/1024, required)
        elapsed, limit = waiting.get("wait_seconds"), waiting.get("wait_limit_s")
        if all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in (elapsed, limit)):
            stage += tr(' · 已等待 {0}/{1} 秒 · 尚未开始计算，可取消', int(elapsed), int(limit))
    progress = row.get("stage_progress") or {}
    done, total = progress.get("done"), progress.get("total")
    if (stage_key == "fit" and progress.get("unit") == "updates" and progress.get("scope") == "stage"
            and type(done) is int and type(total) is int and 0 <= done <= total and total > 0):
        stage += tr(' · {0}/{1} 步（学习阶段 {2}%）', done, total, 100*done//total)
        if done == total:
            stage += tr(" · 后续仍需生成与保存结果")
    started = row.get("stage_started_wall")
    now = time.time() if wall_now is None else wall_now
    if type(started) in (int, float) and math.isfinite(started) and 0 < started <= now:
        stage += tr(' · 本阶段 {0} 秒', int(now-started))
    if row.get("progress_warning"):
        stage += tr(" · 计数暂不可读取，可取消等待")
    return tr("{0} · {1}",label,stage)
