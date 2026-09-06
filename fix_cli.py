import sys

with open("src/bithumb_coin_trader/research_cli.py", "r") as f:
    content = f.read()

import re

# Fix audit_quality returns
audit_orig = """    print(f"Audit complete: status={report['status']} files={len(report['files_found'])} errors={len(report['errors'])}")
    print(f"Report written to: {report_path}")
    return 0 if report["status"] == "STRUCTURAL_AUDIT_PASS" else 2"""

audit_new = """    print(f"Audit complete: status={report['status']} files={len(report['files_found'])} errors={len(report['errors'])}")
    print(f"Report written to: {report_path}")
    if report["status"] == "INCOMPLETE":
        return 1
    elif report["status"] == "FAIL":
        return 2
    elif report["status"] == "STRUCTURAL_AUDIT_PASS":
        return 0
    else:
        return 2"""
content = content.replace(audit_orig, audit_new)

# Fix transform canonical empty rejection
transform_orig = """    transform_report = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "schema_version": args.schema_version,
        "files_found": [str(f.relative_to(input_dir)) for f in raw_files],
        "status": "PASS",
        "canonicalized_count": total_canonicalized,
        "rejected_count": total_rejected,
        "reject_reasons": reject_reasons,
    }
    (output_dir / "transform_report.json").write_text(json.dumps(transform_report, indent=2))
    return 0"""

transform_new = """    transform_report = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "schema_version": args.schema_version,
        "files_found": [str(f.relative_to(input_dir)) for f in raw_files],
        "status": "PASS" if total_canonicalized > 0 else "INCOMPLETE",
        "canonicalized_count": total_canonicalized,
        "rejected_count": total_rejected,
        "reject_reasons": reject_reasons,
    }
    (output_dir / "transform_report.json").write_text(json.dumps(transform_report, indent=2))
    
    if total_canonicalized == 0 and total_rejected == 0:
        print("ERROR: Empty input dataset.")
        return 1
        
    return 0"""
content = content.replace(transform_orig, transform_new)

with open("src/bithumb_coin_trader/research_cli.py", "w") as f:
    f.write(content)
