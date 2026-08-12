import json

from zzcode.evaluation.cli import main


def test_validate_dataset_cli_reports_digest(evaluation_dataset_roots, capsys):
    public_root, private_parent, _ = evaluation_dataset_roots

    exit_code = main(
        [
            "validate-dataset",
            "--public-root",
            str(public_root),
            "--private-root",
            str(private_parent),
            "--split",
            "dev",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["status"] == "valid"
    assert result["task_count"] == 1
    assert result["dataset_digest"].startswith("sha256:")
