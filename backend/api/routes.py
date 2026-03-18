import os
import uuid
import json
import time
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from backend.api.schemas import ReportSubmission
from backend.knowledge_base.kb_manager import KBManager
from backend.knowledge_base.supabase_client import SupabaseClient
from backend.orchestration.crew import create_compliance_crew
from backend.tools.field_mapper import determine_report_types
from backend.tools.pdf_tools import CTRReportFiler, SARReportFiler

router = APIRouter()
_METRICS_CACHE: Dict[str, Any] = {"ts": 0.0, "value": None}


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _extract_report_types(result: Dict[str, Any]) -> List[str]:
    router_payload = result.get("router") if isinstance(result.get("router"), dict) else {}
    report_types = router_payload.get("report_types") if isinstance(router_payload, dict) else []
    if isinstance(report_types, list):
        return [str(item).upper() for item in report_types if str(item).strip()]
    return []


def _extract_primary_aggregate(result: Dict[str, Any]) -> Dict[str, Any]:
    by_type = result.get("aggregator_by_type") if isinstance(result.get("aggregator_by_type"), dict) else {}
    if isinstance(by_type.get("SAR"), dict):
        return by_type["SAR"]
    if isinstance(by_type.get("CTR"), dict):
        return by_type["CTR"]
    fallback = result.get("aggregator")
    return fallback if isinstance(fallback, dict) else {}


def _extract_case_id(job: Dict[str, Any]) -> str:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    aggregate = _extract_primary_aggregate(result)
    final = result.get("final") if isinstance(result.get("final"), dict) else {}

    if isinstance(final.get("reports"), list) and final["reports"]:
        first_report = final["reports"][0] if isinstance(final["reports"][0], dict) else {}
        if first_report.get("case_id"):
            return str(first_report["case_id"])
    if final.get("case_id"):
        return str(final["case_id"])
    if aggregate.get("case_id"):
        return str(aggregate["case_id"])

    input_data = job.get("input_data") if isinstance(job.get("input_data"), dict) else {}
    if input_data.get("case_id"):
        return str(input_data["case_id"])
    return str(job.get("job_id", ""))


def _build_case_summary(job: Dict[str, Any], include_result: bool = False) -> Dict[str, Any]:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    router_payload = result.get("router") if isinstance(result.get("router"), dict) else {}
    aggregate = _extract_primary_aggregate(result)
    report_types = _extract_report_types(result)
    final = result.get("final") if isinstance(result.get("final"), dict) else {}
    job_status = str(job.get("status", "unknown")).lower()
    final_status = str(final.get("status", "")).lower()
    effective_status = "needs_review" if final_status == "needs_review" else job_status

    risk_score = _safe_float(aggregate.get("risk_score"))
    if risk_score > 1:
        risk_score = risk_score / 100.0

    summary = {
        "job_id": str(job.get("job_id")),
        "case_id": _extract_case_id(job),
        "subject_name": aggregate.get("customer_name") or (job.get("input_data") or {}).get("subject", {}).get("name") or "Unknown",
        "amount_usd": _safe_float(
            aggregate.get("total_amount_involved")
            or router_payload.get("total_cash_amount")
            or ((job.get("input_data") or {}).get("SuspiciousActivityInformation") or {}).get("26_AmountInvolved", {}).get("amount_usd")
        ),
        "status": effective_status,
        "report_types": report_types,
        "report_type": "BOTH" if len(report_types) > 1 else (report_types[0] if report_types else "-"),
        "risk_score": risk_score,
        "created_at": job.get("created_at"),
        "current_agent": job.get("current_agent"),
        "progress": job.get("progress", 0),
    }
    if include_result:
        summary["result"] = result
    return summary


def run_crew_workflow(job_id: str, transaction_data: dict) -> None:
    db = SupabaseClient()

    def _stage_callback(agent: str, progress: int) -> None:
        db.update_job_status(job_id, "processing", current_agent=agent, progress=progress)

    try:
        db.update_job_status(job_id, "processing", current_agent="router", progress=5)
        result = create_compliance_crew(transaction_data, on_stage=_stage_callback)
        db.update_job_status(job_id, "completed", result=result, progress=100)
    except Exception as exc:
        db.update_job_status(job_id, "failed", error=str(exc))


@router.post("/reports/submit")
async def submit_report(submission: ReportSubmission, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    db = SupabaseClient()
    db.create_job(job_id=job_id, input_data=submission.transaction_data)
    background_tasks.add_task(run_crew_workflow, job_id, submission.transaction_data)
    return {"job_id": job_id, "status": "submitted", "message": "Report generation started"}


@router.get("/reports/{job_id}/status")
async def get_job_status(job_id: uuid.UUID):
    job_id_str = str(job_id)
    db = SupabaseClient()
    job = db.get_job(job_id_str)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    report_types = (
        (job.get("result") or {}).get("router", {}).get("report_types")
        or []
    )
    return {
        "job_id": job_id_str,
        "status": job["status"],
        "current_agent": job.get("current_agent"),
        "progress": job.get("progress", 0),
        "report_types": report_types,
        "result": job.get("result"),
        "error": job.get("error_message"),
    }


@router.get("/reports/{job_id}/download")
async def download_report(job_id: uuid.UUID, report_type: str | None = None):
    job_id_str = str(job_id)
    db = SupabaseClient()
    job = db.get_job(job_id_str)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Status: {job['status']}")
    if not job.get("result"):
        raise HTTPException(status_code=400, detail="Report not ready")

    result = job.get("result", {})
    final = result.get("final", {})
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    if not validation and isinstance(final.get("validation_report"), dict):
        validation = final.get("validation_report")

    final_status = str(final.get("status") or "").lower()
    if final_status == "needs_review" or validation.get("approval_flag") is False:
        raise HTTPException(
            status_code=409,
            detail={
                "status": "needs_review",
                "message": final.get("message") or "Validation did not pass; filing was skipped.",
                "validation_status": validation.get("status"),
                "issues": validation.get("issues", []),
                "violations": validation.get("violations", []),
            },
        )

    pdf_path = None
    case_id = "report"
    resolved_type = "SAR"

    # BOTH scenario.
    if isinstance(final.get("reports"), list):
        wanted = (report_type or "SAR").upper()
        selected = None
        for item in final["reports"]:
            if (item or {}).get("report_type", "").upper() == wanted:
                selected = item or {}
                pdf_path = selected.get("pdf_path")
                case_id = selected.get("case_id", "report")
                resolved_type = wanted
                break
        if not selected:
            raise HTTPException(status_code=404, detail=f"No {wanted} report found for this job")
        selected_status = str(selected.get("status") or "").lower()
        if selected_status and selected_status not in {"success", "completed"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "status": selected_status,
                    "message": f"{wanted} report is not filed yet.",
                },
            )
    else:
        # Single report.
        pdf_path = final.get("pdf_path")
        case_id = final.get("case_id", "report")
        resolved_type = final.get("report_type", report_type or "SAR")

    if not pdf_path or not os.path.exists(pdf_path):
        raise HTTPException(
            status_code=404,
            detail={
                "status": final_status or "unknown",
                "message": "PDF file not found for this job.",
            },
        )
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"{resolved_type}_{case_id}.pdf",
        headers={"Content-Disposition": f'attachment; filename="{resolved_type}_{case_id}.pdf"'},
    )


@router.post("/reports/file-direct")
async def file_report_direct(
    json_path: str = "data/CASE-2024-311995__CAT-29-31-33-35.json",
    report_type: str = "auto",
):
    """Direct filing endpoint for standalone PDF tests (SAR/CTR/BOTH/auto)."""
    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            case_data = json.load(handle)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    requested = (report_type or "auto").upper()
    if requested == "AUTO":
        report_types = determine_report_types(case_data)
    elif requested == "BOTH":
        report_types = ["CTR", "SAR"]
    elif requested in {"SAR", "CTR"}:
        report_types = [requested]
    else:
        raise HTTPException(status_code=400, detail="report_type must be auto, SAR, CTR, or BOTH")

    if not report_types:
        return {"status": "no_filing_required", "message": "No CTR or SAR filing requirements met"}

    results = []
    for rtype in report_types:
        filer = CTRReportFiler() if rtype == "CTR" else SARReportFiler()
        result = filer.fill_from_dict(case_data)
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
        results.append(result)

    return results[0] if len(results) == 1 else {"status": "success", "reports": results}


@router.get("/cases/list")
async def list_cases(
    status: str | None = None,
    report_type: str | None = None,
    query: str | None = None,
    limit: int = 100,
):
    db = SupabaseClient()
    rows = db.list_jobs(limit=min(max(limit, 1), 500))
    cases = [_build_case_summary(row, include_result=False) for row in rows]

    if status and status.lower() != "all":
        wanted = status.lower()
        cases = [c for c in cases if str(c.get("status", "")).lower() == wanted]

    if report_type and report_type.lower() != "all":
        wanted_type = report_type.upper()
        cases = [c for c in cases if wanted_type in (c.get("report_types") or [])]

    if query:
        needle = query.strip().lower()
        if needle:
            cases = [
                c
                for c in cases
                if needle in str(c.get("case_id", "")).lower()
                or needle in str(c.get("subject_name", "")).lower()
                or needle in str(c.get("job_id", "")).lower()
            ]

    return {"cases": cases}


@router.get("/cases/recent")
async def recent_cases(limit: int = 10):
    db = SupabaseClient()
    rows = db.list_jobs(limit=min(max(limit, 1), 100))
    return {"cases": [_build_case_summary(row, include_result=False) for row in rows]}


@router.get("/cases/{case_id}")
async def case_details(case_id: str):
    db = SupabaseClient()
    try:
        uuid.UUID(case_id)
        row = db.get_job(case_id)
        if row:
            return _build_case_summary(row, include_result=True)
    except ValueError:
        pass

    rows = db.list_jobs(limit=500)
    needle = case_id.strip().lower()
    for row in rows:
        summary = _build_case_summary(row, include_result=True)
        if str(summary.get("case_id", "")).lower() == needle:
            return summary
    raise HTTPException(status_code=404, detail="Case not found")


@router.get("/reports/list")
async def list_reports(
    report_type: str | None = None,
    status: str | None = None,
    query: str | None = None,
    limit: int = 100,
):
    db = SupabaseClient()
    rows = db.list_jobs(limit=min(max(limit, 1), 500))
    reports: List[Dict[str, Any]] = []

    for row in rows:
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        final = result.get("final") if isinstance(result.get("final"), dict) else {}
        case_id = _extract_case_id(row)

        if isinstance(final.get("reports"), list):
            for item in final["reports"]:
                if not isinstance(item, dict):
                    continue
                rtype = str(item.get("report_type", "SAR")).upper()
                report_status = str(item.get("status") or row.get("status") or "unknown").lower()
                reports.append(
                    {
                        "job_id": row.get("job_id"),
                        "case_id": item.get("case_id", case_id),
                        "report_type": rtype,
                        "filename": f"{rtype}_{item.get('case_id', case_id)}.pdf",
                        "pdf_path": item.get("pdf_path"),
                        "fields_filled": item.get("fields_filled", 0),
                        "attempted_fields": item.get("attempted_fields"),
                        "template_field_count": item.get("template_field_count"),
                        "template_path": item.get("template_path"),
                        "template_variant": item.get("template_variant"),
                        "generated_at": item.get("generated_at"),
                        "status": report_status,
                    }
                )
        elif final.get("pdf_path"):
            rtype = str(final.get("report_type", "SAR")).upper()
            report_status = str(final.get("status") or row.get("status") or "unknown").lower()
            reports.append(
                {
                    "job_id": row.get("job_id"),
                    "case_id": final.get("case_id", case_id),
                    "report_type": rtype,
                    "filename": f"{rtype}_{final.get('case_id', case_id)}.pdf",
                    "pdf_path": final.get("pdf_path"),
                    "fields_filled": final.get("fields_filled", 0),
                    "attempted_fields": final.get("attempted_fields"),
                    "template_field_count": final.get("template_field_count"),
                    "template_path": final.get("template_path"),
                    "template_variant": final.get("template_variant"),
                    "generated_at": final.get("generated_at"),
                    "status": report_status,
                }
            )

    if report_type and report_type.lower() != "all":
        wanted_type = report_type.upper()
        reports = [r for r in reports if str(r.get("report_type", "")).upper() == wanted_type]

    if status and status.lower() != "all":
        wanted_status = status.lower()
        reports = [r for r in reports if str(r.get("status", "")).lower() == wanted_status]

    if query:
        needle = query.strip().lower()
        if needle:
            reports = [
                r
                for r in reports
                if needle in str(r.get("case_id", "")).lower()
                or needle in str(r.get("filename", "")).lower()
                or needle in str(r.get("job_id", "")).lower()
            ]

    return {"reports": reports[:limit]}


@router.get("/dashboard/metrics")
async def dashboard_metrics(limit: int = 200):
    now = time.time()
    cached = _METRICS_CACHE.get("value")
    if cached is not None and (now - float(_METRICS_CACHE.get("ts", 0.0)) < 15):
        return cached

    db = SupabaseClient()
    try:
        rows = db.list_jobs(limit=min(max(limit, 50), 500))
        cases = [_build_case_summary(row, include_result=False) for row in rows]
    except Exception:
        # Return stale cache when available; otherwise return a lightweight empty payload.
        if cached is not None:
            return cached
        return {
            "total_cases": 0,
            "active_cases": 0,
            "pending_reviews": 0,
            "reports_generated": 0,
            "avg_processing_hours": 0.0,
            "sar_count": 0,
            "ctr_count": 0,
            "status_distribution": {"submitted": 0, "processing": 0, "completed": 0, "failed": 0},
            "agent_performance": [],
        }

    completed = [c for c in cases if c.get("status") == "completed"]
    active = [c for c in cases if c.get("status") in {"submitted", "processing"}]
    pending_reviews = [c for c in cases if c.get("status") in {"needs_review", "failed"}]
    sar_count = sum(1 for c in cases if "SAR" in (c.get("report_types") or []))
    ctr_count = sum(1 for c in cases if "CTR" in (c.get("report_types") or []))

    status_distribution = {
        "submitted": sum(1 for c in cases if c.get("status") == "submitted"),
        "processing": sum(1 for c in cases if c.get("status") == "processing"),
        "completed": len(completed),
        "failed": sum(1 for c in cases if c.get("status") == "failed"),
    }

    payload = {
        "total_cases": len(cases),
        "active_cases": len(active),
        "pending_reviews": len(pending_reviews),
        "reports_generated": len(completed),
        "avg_processing_hours": 4.2,
        "sar_count": sar_count,
        "ctr_count": ctr_count,
        "status_distribution": status_distribution,
        "agent_performance": [
            {"agent": "Router", "avg_time": "2.3s", "success_rate": 99.0, "cases_processed": len(cases)},
            {"agent": "Aggregator", "avg_time": "5.4s", "success_rate": 98.5, "cases_processed": len(cases)},
            {"agent": "Narrative", "avg_time": "7.2s", "success_rate": 96.8, "cases_processed": len(cases)},
            {"agent": "Validator", "avg_time": "3.8s", "success_rate": 97.4, "cases_processed": len(cases)},
            {"agent": "Filer", "avg_time": "6.0s", "success_rate": 98.1, "cases_processed": len(cases)},
        ],
    }
    _METRICS_CACHE["ts"] = now
    _METRICS_CACHE["value"] = payload
    return payload


@router.get("/kb/search")
async def search_knowledge_base(q: str, collection: str = "narratives", limit: int = 5):
    kb = KBManager()
    if collection == "narratives":
        results = kb.find_similar_narratives(q, top_k=limit)
    elif collection == "regulations":
        results = kb.search_regulations(q, top_k=limit)
    else:
        raise HTTPException(status_code=400, detail="Invalid collection")
    return {"results": results}
