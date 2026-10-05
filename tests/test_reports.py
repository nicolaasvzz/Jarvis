import re

from investment_bot import check, reports
from investment_bot.reports import Report, archive


def label(page: str) -> dict[str, str]:
    """What Jarvis reads from a report: its title and the two meta tags."""
    return {"title": re.search(r"<title>(.*?)</title>", page).group(1),
            "description": re.search(r'<meta name="description" content="(.*?)">', page).group(1),
            "tone": re.search(r'<meta name="tone" content="(.*?)">', page).group(1)}


def test_a_report_is_kept_under_a_new_dated_name_with_its_label(tmp_path):
    out = Report("check", "3 passed, <1> failed", "bad").stat("Passed", 3, "good")
    out.table("Checks", [{"Check": "Config", "Result": "PASS", "Took": 0.25}])
    out.bullets("Notes", ["a & b"])
    first, second = out.save(tmp_path), out.save(tmp_path)
    assert first != second  # never overwritten
    assert re.fullmatch(r"\d{4}-\d\d-\d\d_\d{6}_check(-2)?\.html", first.name)
    page = first.read_text(encoding="utf-8")
    assert label(page) == {"title": "Test mode", "description": "3 passed, &lt;1&gt; failed",
                           "tone": "bad"}
    assert "a &amp; b" in page and "0.25" in page


def test_archive_relabels_a_copy_and_leaves_the_original(tmp_path):
    source = tmp_path / "report.html"
    source.write_text("<html><head><meta charset='utf-8'><title>Old</title></head>"
                      "<body>chart</body></html>", encoding="utf-8")
    kept = archive(source, "backtest", "+1.00% return", "good", folder=tmp_path / "reports")
    page = kept.read_text(encoding="utf-8")
    assert label(page) == {"title": "Backtest", "description": "+1.00% return", "tone": "good"}
    assert page.count("<title>") == 1 and "chart" in page
    assert "<title>Old</title>" in source.read_text(encoding="utf-8")
    assert archive(tmp_path / "missing.html", "backtest", "x") is None  # never raises


def test_check_without_a_config_fails_that_check_and_still_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lines = []
    results, report = check.run_checks("nope.yaml", quick=True, say=lines.append)
    assert [(r.name, r.status) for r in results] == [("Config", "FAIL")]
    assert "nope.yaml is missing" in results[0].detail
    page = report.html()
    assert label(page)["tone"] == "bad" and "0 passed" in label(page)["description"]
    assert reports.KINDS["check"] == "Test mode"
