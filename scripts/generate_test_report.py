"""Generate test report for MCP smoke tests.

Runs the MCP smoke tests and generates a comprehensive report with
success percentage and error details.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Add project root to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def run_tests() -> dict[str, Any]:
    """Run MCP smoke tests and parse results.

    Returns:
        Dictionary with test results
    """
    # Ensure MCP env vars are set
    env = os.environ.copy()
    if not env.get("MCP_DB_SERVER_URL"):
        env["MCP_DB_SERVER_URL"] = "http://localhost:8080"
    if not env.get("MCP_FS_SERVER_URL"):
        env["MCP_FS_SERVER_URL"] = "http://localhost:8081"

    # Run pytest with JSON output
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/mcp/test_mcp_smoke.py",
        "-v",
        "--tb=short",
        "--json-report",
        "--json-report-file=/tmp/pytest_report.json",
    ]

    try:
        result = subprocess.run(
            cmd,
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        stdout = result.stdout
        stderr = result.stderr
        return_code = result.returncode
    except subprocess.TimeoutExpired:
        return {
            "error": "Tests timed out after 5 minutes",
            "passed": 0,
            "failed": 0,
            "total": 0,
            "success_rate": 0.0,
        }
    except Exception as e:
        return {
            "error": f"Failed to run tests: {e!s}",
            "passed": 0,
            "failed": 0,
            "total": 0,
            "success_rate": 0.0,
        }

    # Parse pytest output
    lines = stdout.split("\n")
    passed = 0
    failed = 0
    errors = []
    test_results = []

    for line in lines:
        if "PASSED" in line:
            passed += 1
            test_name = line.split("::")[-1].split()[0] if "::" in line else "unknown"
            test_results.append({"name": test_name, "status": "PASSED"})
        elif "FAILED" in line:
            failed += 1
            test_name = line.split("::")[-1].split()[0] if "::" in line else "unknown"
            test_results.append({"name": test_name, "status": "FAILED"})
            # Try to extract error message
            error_msg = line.strip()
            errors.append({"test": test_name, "error": error_msg})

    total = passed + failed
    success_rate = (passed / total * 100) if total > 0 else 0.0

    return {
        "passed": passed,
        "failed": failed,
        "total": total,
        "success_rate": success_rate,
        "return_code": return_code,
        "test_results": test_results,
        "errors": errors,
        "stdout": stdout,
        "stderr": stderr,
    }


def generate_report(results: dict[str, Any]) -> str:
    """Generate formatted test report.

    Args:
        results: Test results dictionary

    Returns:
        Formatted report string
    """
    report_lines = [
        "=" * 80,
        "MCP SMOKE TESTS - FINAL REPORT",
        "=" * 80,
        "",
    ]

    # Summary
    total = results.get("total", 0)
    passed = results.get("passed", 0)
    failed = results.get("failed", 0)
    success_rate = results.get("success_rate", 0.0)

    report_lines.extend([
        "SUMMARY",
        "-" * 80,
        f"Total Tests:     {total}",
        f"Passed:          {passed}",
        f"Failed:          {failed}",
        f"Success Rate:    {success_rate:.1f}%",
        "",
    ])

    # Test Results
    if results.get("test_results"):
        report_lines.extend([
            "TEST RESULTS",
            "-" * 80,
        ])
        for test in results["test_results"]:
            status_icon = "✅" if test["status"] == "PASSED" else "❌"
            report_lines.append(f"{status_icon} {test['name']}: {test['status']}")
        report_lines.append("")

    # Errors
    if results.get("errors"):
        report_lines.extend([
            "ERRORS",
            "-" * 80,
        ])
        for error in results["errors"]:
            report_lines.append(f"❌ {error['test']}:")
            report_lines.append(f"   {error['error']}")
            report_lines.append("")
    elif failed > 0:
        report_lines.extend([
            "ERRORS",
            "-" * 80,
            "See full output below for error details.",
            "",
        ])

    # Full Output (truncated if too long)
    if results.get("stdout"):
        report_lines.extend([
            "FULL OUTPUT (Last 50 lines)",
            "-" * 80,
        ])
        stdout_lines = results["stdout"].split("\n")
        report_lines.extend(stdout_lines[-50:])
        report_lines.append("")

    report_lines.extend([
        "=" * 80,
        f"Report generated at: {Path(__file__).parent.parent}",
        "=" * 80,
    ])

    return "\n".join(report_lines)


def main() -> None:
    """Main entry point."""
    print("Running MCP smoke tests...")
    results = run_tests()

    report = generate_report(results)
    print("\n" + report)

    # Save report to file
    report_file = Path(__file__).parent.parent / "test_report.txt"
    report_file.write_text(report, encoding="utf-8")
    print(f"\nReport saved to: {report_file}")

    # Also save JSON for programmatic access
    json_file = Path(__file__).parent.parent / "test_report.json"
    json_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"JSON results saved to: {json_file}")

    # Exit with appropriate code
    if results.get("failed", 0) > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
