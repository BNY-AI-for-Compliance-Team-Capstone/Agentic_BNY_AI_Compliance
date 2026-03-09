import os
import json
import glob
from dotenv import load_dotenv
from utils.ctr_rules_engine import CTRRuleChecker
from utils.sar_rules_engine import SARRuleChecker
from utils.llm_evaluator import evaluate_narrative
from utils.scoring import calculate_score

def generate_validation_report(violations: list, category_scores: dict, report_type: str) -> str:
    """Generate a detailed human‑readable report as a single string."""
    lines = []
    lines.append("=" * 60)
    lines.append("VALIDATION REPORT")
    lines.append("=" * 60)
    lines.append(f"Report Type: {report_type}")
    lines.append(f"Total Violations: {len(violations)}")
    lines.append("")

    lines.append("CATEGORY SCORES:")
    for cat, score in category_scores.items():
        lines.append(f"  - {cat.capitalize()}: {score}%")
    lines.append("")

    if violations:
        lines.append("VIOLATIONS:")
        # Group by severity
        for severity in ["critical", "high", "medium", "low"]:
            sev_violations = [v for v in violations if v["severity"] == severity]
            if sev_violations:
                lines.append(f"  {severity.upper()} ({len(sev_violations)}):")
                for v in sev_violations:
                    lines.append(f"    - {v['rule_id']}: {v['message']}")
        lines.append("")
    else:
        lines.append("No violations found. Report is fully compliant.\n")

    lines.append("RECOMMENDATIONS:")
    recs = set()
    for v in violations:
        msg = v["message"]
        if "missing" in msg.lower():
            recs.add(f"Complete missing field: {msg}")
        elif "invalid" in msg.lower() or "format" in msg.lower():
            recs.add(f"Correct format error: {msg}")
        elif "prohibited" in msg.lower():
            recs.add(f"Remove prohibited words: {msg}")
        elif "LLM" in msg:
            recs.add(f"Improve narrative: {msg}")
    if not recs:
        recs.add("No specific recommendations – all checks passed.")
    for rec in sorted(recs):
        lines.append(f"  - {rec}")
    lines.append("=" * 60)
    return "\n".join(lines)

def main():
    load_dotenv()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.join(base_dir, "data", "input_data")
    output_dir = os.path.join(base_dir, "data", "output_data")
    validation_dir = os.path.join(base_dir, "data", "validation_data")

    os.makedirs(output_dir, exist_ok=True)

    input_files = glob.glob(os.path.join(input_dir, "*.json"))
    if not input_files:
        print("No input JSON files found in data/input_data.")
        return
    input_path = input_files[0]
    print(f"Processing: {input_path}")

    with open(input_path, 'r', encoding='utf-8') as f:
        input_data = json.load(f)

    report_type = input_data.get("report_type")
    if not report_type:
        print("Error: Input JSON does not contain 'report_type' field.")
        return

    rules_path = os.path.join(validation_dir, "validation_rules.json")
    with open(rules_path, 'r', encoding='utf-8') as f:
        all_rules = json.load(f)

    relevant_rules = [r for r in all_rules if r.get("report_type") == report_type]

    if report_type == "CTR":
        checker = CTRRuleChecker(input_data, relevant_rules)
    elif report_type == "SAR":
        checker = SARRuleChecker(input_data, relevant_rules)
    else:
        print(f"Unsupported report type: {report_type}")
        return

    violations = checker.check_all()

    narrative_score = None
    if report_type == "SAR" and input_data.get("narrative"):
        llm_result = evaluate_narrative(input_data["narrative"])
        narrative_score = llm_result.get("score", 50)

        for elem in llm_result.get("missing_elements", []):
            violations.append({
                "rule_id": "SAR-QUALITY-001",
                "severity": "medium",
                "message": f"Narrative missing element: {elem}"
            })
        if llm_result.get("comments"):
            violations.append({
                "rule_id": "SAR-QUALITY-002",
                "severity": "low",
                "message": f"LLM comment: {llm_result['comments']}"
            })

    score_result = calculate_score(violations, relevant_rules, report_type)

    if report_type == "SAR" and narrative_score is not None:
        # Override narrative category with LLM score
        score_result["scores"]["narrative"] = narrative_score
        score_result["validation_score"] = sum(score_result["scores"].values()) / len(score_result["scores"])
        score_result["validation_score"] = round(score_result["validation_score"], 2)

    validation_report_text = generate_validation_report(
        violations,
        score_result["scores"],
        report_type
    )

    output = {
        "case_id": input_data.get("case_id", "UNKNOWN"),
        "report_type": report_type,
        "status": score_result["status"],
        "pass_or_not": score_result["pass_or_not"],
        "validation_score": score_result["validation_score"],
        "scores": score_result["scores"],
        "validation_report": validation_report_text,
        "generate_times": 1,
        "current_best_score": 0.0
    }

    output_filename = f"{output['case_id']}.json"
    output_path = os.path.join(output_dir, output_filename)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Validation completed. Output saved to {output_path}")

if __name__ == "__main__":
    main()